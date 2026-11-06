"""
Fetch ChEMBL1978 (human aromatase / CYP19A1) IC50 bioactivity data
and load into QSAR_DB.PUBLIC.CHEMBL1978_COMPOUNDS.

Usage:
    python fetch_chembl1978.py

Requirements:
    pip install chembl-webresource-client snowflake-connector-python pandas
"""

import math
import os
import pandas as pd
from chembl_webresource_client.new_client import new_client
import snowflake.connector

# ─── ChEMBL fetch ────────────────────────────────────────────────────────────

TARGET_CHEMBL_ID = "CHEMBL1978"  # human aromatase CYP19A1

activity = new_client.activity
print(f"Fetching IC50 activities for {TARGET_CHEMBL_ID} ...")

records = activity.filter(
    target_chembl_id=TARGET_CHEMBL_ID,
    standard_type="IC50",
    standard_relation="=",
    assay_type="B",               # binding assay
).only(
    "molecule_chembl_id",
    "canonical_smiles",
    "standard_value",             # IC50 value
    "standard_units",
    "pchembl_value",
)

rows = list(records)
print(f"  Raw records retrieved: {len(rows)}")

# ─── Clean ───────────────────────────────────────────────────────────────────

df = pd.DataFrame(rows)

# Keep only nM-unit records with valid SMILES and pChEMBL
df = df[df["standard_units"] == "nM"].copy()
df = df.dropna(subset=["canonical_smiles", "pchembl_value"])
df = df[df["canonical_smiles"].str.len() > 0]
df = df[df["canonical_smiles"] != "None"]

# Convert types
df["standard_value"] = pd.to_numeric(df["standard_value"], errors="coerce")
df["pchembl_value"]  = pd.to_numeric(df["pchembl_value"],  errors="coerce")
df = df.dropna(subset=["pchembl_value"])

# Deduplicate: keep highest-quality (highest pChEMBL) per molecule
df = (
    df.sort_values("pchembl_value", ascending=False)
      .drop_duplicates(subset="molecule_chembl_id", keep="first")
      .reset_index(drop=True)
)

# Activity label: active if pIC50 >= 6.5 (IC50 <= 316 nM)
df["activity_class"] = (df["pchembl_value"] >= 6.5).astype(int)

print(f"  After cleaning: {len(df)} compounds")
print(f"  Active (pIC50 >= 6.5): {df['activity_class'].sum()}")
print(f"  Inactive:              {(df['activity_class'] == 0).sum()}")
print(f"  pIC50 range: {df['pchembl_value'].min():.2f} – {df['pchembl_value'].max():.2f}")

# ─── Save CSV (for R training script) ────────────────────────────────────────

csv_path = "chembl1978_cleaned.csv"
df[["molecule_chembl_id", "canonical_smiles", "standard_value",
    "pchembl_value", "activity_class"]].to_csv(csv_path, index=False)
print(f"\nSaved to {csv_path}")

# ─── Load into Snowflake ─────────────────────────────────────────────────────

print("\nConnecting to Snowflake ...")
conn = snowflake.connector.connect(
    connection_name=os.environ.get("SNOWFLAKE_CONNECTION", "default")
)
cur = conn.cursor()

cur.execute("USE ROLE QSAR_ROLE")
cur.execute("USE DATABASE QSAR_DB")
cur.execute("USE SCHEMA PUBLIC")
cur.execute("USE WAREHOUSE QSAR_WH")

# Truncate and reload for idempotency
cur.execute("TRUNCATE TABLE CHEMBL1978_COMPOUNDS")

insert_sql = """
INSERT INTO CHEMBL1978_COMPOUNDS
    (MOLECULE_CHEMBL_ID, CANONICAL_SMILES, IC50_NM, PCHEMBL_VALUE, ACTIVITY_CLASS)
VALUES (%s, %s, %s, %s, %s)
"""

rows_to_load = [
    (
        row["molecule_chembl_id"],
        row["canonical_smiles"],
        float(row["standard_value"]) if pd.notna(row["standard_value"]) else None,
        float(row["pchembl_value"]),
        int(row["activity_class"]),
    )
    for _, row in df.iterrows()
]

cur.executemany(insert_sql, rows_to_load)
conn.commit()
print(f"Loaded {len(rows_to_load)} rows into QSAR_DB.PUBLIC.CHEMBL1978_COMPOUNDS")

cur.close()
conn.close()
