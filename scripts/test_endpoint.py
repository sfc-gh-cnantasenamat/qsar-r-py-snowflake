"""
Smoke-test the AromatasePred R Plumber service through its public endpoint.

Usage:
    export QSAR_ENDPOINT="https://<your-ingress-url>"
    export SNOWFLAKE_PAT="<programmatic access token>"
    python scripts/test_endpoint.py

Get QSAR_ENDPOINT from: SHOW ENDPOINTS IN SERVICE QSAR_DB.PUBLIC.QSAR_PLUMBER_SVC
"""

import os

import requests

URL = os.environ["QSAR_ENDPOINT"].rstrip("/")
if not URL.startswith("http"):
    URL = "https://" + URL

# Public ingress expects the PAT in this exact form; "Bearer <pat>" redirects to sign-in.
HEADERS = {
    "Authorization": f'Snowflake Token="{os.environ["SNOWFLAKE_PAT"]}"',
    "Content-Type": "application/json",
}

COMPOUNDS = {
    "letrozole":   "N#Cc1ccc(cc1)C(n1cncn1)c1ccc(C#N)cc1",
    "anastrozole": "CC(C)(C#N)c1cc(Cn2cncn2)cc(c1)C(C)(C)C#N",
    "exemestane":  "C=C1CC2=CC(=O)C=CC2(C)C2CCC3(C)C(=O)CCC3C12",
    "ethanol":     "CCO",
    "invalid":     "not_a_smiles",
}

# ── Health ────────────────────────────────────────────────────────────────────
r = requests.get(f"{URL}/health", headers=HEADERS, timeout=60, allow_redirects=False)
print("health:", r.status_code, r.text)
r.raise_for_status()

# ── Batch prediction (Snowflake service-function row format) ─────────────────
names = list(COMPOUNDS)
payload = {"data": [[i, COMPOUNDS[n]] for i, n in enumerate(names)]}
r = requests.post(f"{URL}/predict", headers=HEADERS, json=payload, timeout=120)
r.raise_for_status()
print(f"\n{'compound':<12} {'pIC50':>6}  {'class':<10} {'q05':>5} {'q95':>5}")
for idx, pic50, label, lo, hi in r.json()["data"]:
    fmt = lambda v: f"{v:.2f}" if isinstance(v, (int, float)) else "-"
    print(f"{names[idx]:<12} {fmt(pic50):>6}  {str(label)[:30]:<10} {fmt(lo):>5} {fmt(hi):>5}")

# ── MCP (JSON-RPC 2.0) ────────────────────────────────────────────────────────
r = requests.post(f"{URL}/mcp", headers=HEADERS, timeout=120,
                  json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
r.raise_for_status()
print("\nMCP tools:", [t["name"] for t in r.json()["result"]["tools"]])

r = requests.post(f"{URL}/mcp", headers=HEADERS, timeout=120,
                  json={"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                        "params": {"name": "predict_pic50",
                                   "arguments": {"smiles": COMPOUNDS["letrozole"]}}})
r.raise_for_status()
print("\nMCP predict_pic50:\n" + r.json()["result"]["content"][0]["text"])
