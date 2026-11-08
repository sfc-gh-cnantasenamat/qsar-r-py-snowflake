"""About page — model description, dataset, fingerprints, deployment."""

import streamlit as st

from _shared import load_metrics

st.title(":material/info: About")
st.subheader("About this model")
st.markdown(
    "This app predicts the **pIC50** of small molecules against human "
    "**aromatase (CYP19A1)**, a key enzyme in estrogen biosynthesis relevant "
    "to breast cancer research. Data is curated from ChEMBL in a Snowflake "
    "notebook; fingerprint computation, model training, and inference are "
    "implemented in **R** and served live from a Snowpark Container Services (SPCS) pod."
)

st.subheader("Dataset")
st.markdown("""
- Source: **ChEMBL1978** — curated IC₅₀ bioactivity records for CYP19A1
- 2,751 compounds after filtering exact binding-assay IC₅₀ values and
  retaining the highest pIC50 per ChEMBL molecule ID
- pIC50 = −log₁₀(IC₅₀ [M]) used as the continuous regression target
- Train / test split: 80 / 20 partitioned on continuous pIC50 values using a fixed seed
""")

st.subheader("Fingerprints")
st.markdown("""
- **Library:** `rcdk` 3.8.2 (R wrapper around the Chemistry Development Kit)
  installed from Posit Package Manager (PPM) binary builds to avoid QEMU
  compilation failures in the SPCS Docker image
- **Type:** ECFP4 — circular Morgan fingerprints, radius 2, 1024 bits
- **Bit extraction:** the `@bits` slot of the S4 `fingerprint` object gives
  1-based set-bit indices; these are mapped to a 1024-element integer vector
- **Feature selection:** 178 informative bits retained after near-zero-variance
  (NZV) filtering using `caret::nearZeroVar()`; the selected column names are
  stored in `kept_bits.csv` and loaded at container start-up
""")

_m = load_metrics()
performance = (f"Test R² = {_m['test_r2']:.3f}, RMSE = {_m['test_rmse']:.3f} log units on the held-out set"
               if _m else "see `training_metrics.json`")

st.subheader("Model")
st.markdown(f"""
- **Package:** `ranger` — a fast C++ implementation of Random Forests for R
- **Trees:** 500 (`num.trees = 500`)
- **Regression mode:** quantile regression (`quantreg = TRUE`) — enables
  predicting arbitrary quantiles rather than just the mean
- **Inference:** `predict(model, data, type = "quantiles", quantiles = c(0.05, 0.5, 0.95))`
  returns the 5th, 50th (median pIC50), and 95th percentiles in one pass,
  giving the prediction and uncalibrated 5th to 95th model quantiles simultaneously
- **Performance:** {performance}
- **Serving:** the trained model (`.rds`) and `kept_bits.csv` are baked into
  the Docker image at build time; a `plumber` REST API exposes `/predict` and
  `/mcp` endpoints; the JVM (required by rcdk / CDK) is pre-warmed at container
  start to eliminate cold-start latency on the first request
""")

st.subheader("Activity threshold")
st.markdown("""
- pIC50 ≥ 6.5 → **Active** (IC₅₀ ≤ 316 nM)
- pIC50 < 6.5 → **Inactive**
- The 6.5 cut-off is a widely used convention in QSAR literature for CYP19A1
""")

st.subheader("Deployment")
st.markdown("""
Every component of this project runs entirely inside Snowflake:

| Component | Snowflake feature |
|---|---|
| Model artifacts & CSV files | **Internal Stage** (`@QSAR_DB.PUBLIC.QSAR_STAGE`) |
| R Plumber inference container | **Snowpark Container Services (SPCS)** — `QSAR_PLUMBER_SVC` on `QSAR_CPU_POOL` |
| Container image registry | **SPCS Image Repository** (`QSAR_DB.PUBLIC.QSAR_REPO`) |
| This web app | **Streamlit in Snowflake** — container runtime (`SYSTEM$ST_CONTAINER_RUNTIME_PY3_11`) |
| App ↔ inference networking | **Internal DNS** — intra-pool call to `qsar-plumber-svc.*.svc.spcs.internal` (no public ingress needed) |
| External egress (optional) | **External Access Integration** (`QSAR_EAI`) with a Network Rule scoped to the SPCS ingress domain |
| MCP tool server | **SPCS public ingress** — `/mcp` endpoint exposes `predict_pic50` as an MCP tool for AI agents and CoCo Desktop |
| Native App packaging | **Snowflake Native App** (`QSAR_MCP`) — bundles the same container image for one-click consumer deployment |
| Prediction audit log | **Snowflake Table** (`QSAR_DB.PUBLIC.QSAR_PREDICTIONS_LOG`) — every prediction is written via `INSERT` through the active Snowpark session |

The inference service is intentionally **kept separate from Snowflake's Python ML stack** — it uses pure R (`ranger` + `rcdk`) to avoid any rpy2 bridge, ensuring fingerprint and model parity between training and serving.
""")
