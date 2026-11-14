#!/usr/bin/env python3
"""
Generate dashboard data files from ChEMBL1978 compounds and upload to Snowflake stage.

Produces:
  dashboard_eda.csv         pIC50 distribution + activity label per compound
  dashboard_ro5.csv         Lipinski Ro5 properties per compound
  dashboard_predictions.csv actual vs predicted pIC50 (batch inference via Plumber)

Usage:
  cd /path/to/build-r-python-drug-discovery-apps-and-agents
  python scripts/generate_dashboard_data.py
"""

import os, sys, json, time, tempfile
import requests
import pandas as pd
import snowflake.connector
import toml, pathlib

# ── Config ────────────────────────────────────────────────────────────────────
PROJECT_ROOT   = pathlib.Path(__file__).parent.parent
CLEANED_CSV    = PROJECT_ROOT / "data" / "chembl1978_cleaned.csv"
# Public ingress URL from: SHOW ENDPOINTS IN SERVICE QSAR_DB.PUBLIC.QSAR_PLUMBER_SVC
_ENDPOINT      = os.environ["QSAR_ENDPOINT"].rstrip("/")
if not _ENDPOINT.startswith("http"):
    _ENDPOINT = "https://" + _ENDPOINT
PREDICT_URL    = _ENDPOINT + "/predict"
BATCH_SIZE     = 50   # compounds per /predict call
STAGE          = os.environ.get("QSAR_STAGE", "@QSAR_DB.PUBLIC.QSAR_STAGE")

# ── Snowflake auth ─────────────────────────────────────────────────────────────
CONNECTION     = os.environ.get("SNOWFLAKE_CONNECTION", "default")

def get_conn():
    # Uses a named connection from ~/.snowflake/connections.toml
    return snowflake.connector.connect(connection_name=CONNECTION)

# ── Load source data ───────────────────────────────────────────────────────────
print(f"Loading {CLEANED_CSV} ...")
df = pd.read_csv(CLEANED_CSV)
df = df.rename(columns={
    "pchembl_value":    "pchembl_value",
    "activity_class":   "activity_class",
    "canonical_smiles": "canonical_smiles",
})
df["activity_label"] = df["activity_class"].map({1: "Active", 0: "Inactive"})
print(f"  {len(df)} compounds loaded")

# ══════════════════════════════════════════════════════════════════════════════
# 1. EDA data
# ══════════════════════════════════════════════════════════════════════════════
print("\n[1/3] Generating EDA data ...")
eda_df = df[["pchembl_value", "activity_label"]].copy()
eda_df.columns = ["pchembl_value", "activity_label"]
print(f"  {len(eda_df)} rows")

# ══════════════════════════════════════════════════════════════════════════════
# 2. Ro5 properties (rdkit)
# ══════════════════════════════════════════════════════════════════════════════
print("\n[2/3] Computing Ro5 properties with rdkit ...")
try:
    from rdkit import Chem
    from rdkit.Chem import Descriptors, rdMolDescriptors, Crippen
except ImportError:
    print("  ERROR: rdkit not installed. Run: pip install rdkit-pypi")
    sys.exit(1)

rows = []
n_invalid = 0
for _, row in df.iterrows():
    mol = Chem.MolFromSmiles(row["canonical_smiles"])
    if mol is None:
        n_invalid += 1
        continue
    rows.append({
        "pchembl_value":  row["pchembl_value"],
        "mw":             round(Descriptors.MolWt(mol), 2),
        "logp":           round(Crippen.MolLogP(mol), 3),
        "hbd":            rdMolDescriptors.CalcNumHBD(mol),
        "hba":            rdMolDescriptors.CalcNumHBA(mol),
        "tpsa":           round(Descriptors.TPSA(mol), 2),
        "activity_label": row["activity_label"],
    })

ro5_df = pd.DataFrame(rows)
print(f"  {len(ro5_df)} valid molecules  ({n_invalid} skipped)")

# ══════════════════════════════════════════════════════════════════════════════
# 3. Actual vs predicted (batch inference through deployed Plumber service)
# ══════════════════════════════════════════════════════════════════════════════
print(f"\n[3/3] Running {len(df)} SMILES through Plumber /predict (batch={BATCH_SIZE}) ...")

# Public ingress requires a programmatic access token (PAT)
PAT = os.environ["SNOWFLAKE_PAT"]
headers = {
    "Content-Type":  "application/json",
    "Authorization": f"Snowflake Token=\"{PAT}\"",
}

smiles_list  = df["canonical_smiles"].tolist()
actual_list  = df["pchembl_value"].tolist()
pred_list    = []
failed_idx   = set()

for start in range(0, len(smiles_list), BATCH_SIZE):
    batch = smiles_list[start : start + BATCH_SIZE]
    payload = {"data": [[i, s] for i, s in enumerate(batch)]}
    for attempt in range(3):
        try:
            r = requests.post(PREDICT_URL, headers=headers, json=payload, timeout=120)
            r.raise_for_status()
            data = r.json().get("data", [])
            # data rows: [idx, pic50, activity, ci_lo, ci_hi]
            if (len(data) != len(batch) or
                    sorted(row[0] for row in data) != list(range(len(batch))) or
                    any(len(row) < 5 or not isinstance(row[1], (int, float)) for row in data)):
                raise ValueError("Incomplete or malformed prediction batch")
            data_sorted = sorted(data, key=lambda x: x[0])
            pred_list.extend([row[1] for row in data_sorted])
            break
        except Exception as e:
            if attempt == 2:
                print(f"  WARN: batch {start}-{start+len(batch)} failed: {e}")
                for i in range(len(batch)):
                    failed_idx.add(start + i)
                pred_list.extend([None] * len(batch))
            else:
                time.sleep(2)

    pct = min(100, round(100 * (start + len(batch)) / len(smiles_list)))
    print(f"  {pct:3d}%  ({start + len(batch)}/{len(smiles_list)})", end="\r")

print()

# Drop rows where prediction failed
pred_df = pd.DataFrame({
    "actual_pic50": actual_list,
    "pred_pic50":   pred_list,
    "activity_label": df["activity_label"].tolist(),
})
pred_df = pred_df.dropna(subset=["pred_pic50"]).reset_index(drop=True)
pred_df["pred_pic50"] = pred_df["pred_pic50"].round(3)
print(f"  {len(pred_df)} predictions  ({len(df) - len(pred_df)} failed)")

# ══════════════════════════════════════════════════════════════════════════════
# 4. Save to /tmp and upload to stage
# ══════════════════════════════════════════════════════════════════════════════
tmpdir = tempfile.mkdtemp()

files = {
    "dashboard_eda.csv":         eda_df,
    "dashboard_ro5.csv":         ro5_df,
    "dashboard_predictions.csv": pred_df,
}

print(f"\nSaving to {tmpdir} ...")
for fname, frame in files.items():
    fpath = os.path.join(tmpdir, fname)
    frame.to_csv(fpath, index=False)
    print(f"  {fname}  ({len(frame)} rows, {os.path.getsize(fpath)//1024} KB)")

print(f"\nUploading to {STAGE} ...")
conn = get_conn()
cur  = conn.cursor()
for fname in files:
    fpath = os.path.join(tmpdir, fname)
    cur.execute(
        f"PUT file://{fpath} {STAGE} AUTO_COMPRESS=FALSE OVERWRITE=TRUE"
    )
    print(f"  Uploaded: {fname}")
conn.close()

print(f"\nDone — confirm with: LIST {STAGE}")
