# AromatasePred: R-Powered Drug Discovery on Snowflake

Companion repository for the Snowflake developer guide **Build an R and Python Powered Drug Discovery App on Snowflake**.

A QSAR (quantitative structure-activity relationship) model, trained in R, predicts how strongly a molecule inhibits human aromatase (CYP19A1, ChEMBL1978), a breast cancer drug target. R does the cheminformatics and modelling; Python curates the data and serves the interface:

| Layer | Technology | Location |
|---|---|---|
| Data preparation | Python Workspace notebooks: ChEMBL API, pandas, RDKit | `notebooks/` |
| Training | R: `rcdk` ECFP4 fingerprints, `caret`, `ranger` quantile random forest | `docker/train_rcdk.R` |
| Serving | R Plumber container on Snowpark Container Services (SPCS) | `docker/Dockerfile.plumber`, `docker/plumber_api.R` |
| App | Streamlit in Snowflake | `streamlit/` |
| AI assistants | MCP (JSON-RPC 2.0) endpoint on the same container | `/mcp` in `plumber_api.R`, `docs/mcp-walkthrough.md` |

## Repository layout

```
data/        Snapshot of the cleaned ChEMBL1978 IC50 dataset (2,751 compounds) used for the bundled model
docker/      R training script, Plumber API, Dockerfile, SPCS spec, bundled model artifacts
streamlit/   Multipage Streamlit app (Dashboard, Single and Batch Prediction, About)
notebooks/   01: raw ChEMBL records to training data and EDA; 02: score compounds for the dashboard
native_app/  Optional Native App package for per-consumer MCP deployment
scripts/     Endpoint smoke test and dashboard data generator
sql/         Snowflake setup (role, warehouse, database, pool, repo, stage, log table)
docs/        MCP walkthrough
```

## Quick start

The developer guide walks through each step in detail.

1. **Set up Snowflake.** Run `sql/setup.sql` in Snowsight (replace `<your_user>`). It creates the role, warehouse, database, compute pools, stage, image repository, audit table, and external access integrations.
2. **Prepare the training data.** Upload both notebooks to a stage and run `01_prepare_training_data.ipynb`. It fetches raw ChEMBL records, cleans them into `CHEMBL1978_COMPOUNDS`, explores them, and writes `chembl1978_cleaned.csv`, `dashboard_eda.csv`, and `dashboard_ro5.csv` to `@QSAR_STAGE`:
   ```sql
   USE SCHEMA QSAR_DB.PUBLIC;
   CREATE STAGE IF NOT EXISTS NB_PROJECT_STAGE;
   -- snow sql -q "PUT file://notebooks/*.ipynb @QSAR_DB.PUBLIC.NB_PROJECT_STAGE AUTO_COMPRESS=FALSE OVERWRITE=TRUE"
   CREATE OR REPLACE NOTEBOOK PROJECT QSAR_NOTEBOOKS FROM '@NB_PROJECT_STAGE';
   EXECUTE NOTEBOOK PROJECT QSAR_NOTEBOOKS
       MAIN_FILE = '01_prepare_training_data.ipynb'
       COMPUTE_POOL = 'QSAR_ST_POOL'
       QUERY_WAREHOUSE = 'QSAR_WH'
       RUNTIME = 'V2-CPU-PY3.12'
       EXTERNAL_ACCESS_INTEGRATIONS = (QSAR_NB_EAI);
   ```
3. **Train the R model** on that data, then copy the artifacts into `docker/`:
   ```bash
   snow sql -q "GET @QSAR_DB.PUBLIC.QSAR_STAGE/chembl1978_cleaned.csv file://data/"
   docker build --platform linux/amd64 -f docker/Dockerfile.plumber -t r-qsar-plumber:local docker
   mkdir -p output
   docker run --rm --platform linux/amd64 \
     -v "$PWD/data:/data" -v "$PWD/output:/output" -v "$PWD/docker:/work" \
     r-qsar-plumber:local Rscript /work/train_rcdk.R
   cp output/qsar_aromatase_model_rcdk.rds docker/qsar_aromatase_model.rds
   cp output/kept_bits_rcdk.csv docker/kept_bits.csv
   cp output/top_features_rcdk.csv output/training_metrics.json docker/
   ```
4. **Build and push the serving image**, which bakes in the trained model:
   ```bash
   cd docker
   snow spcs image-registry login
   docker build --platform linux/amd64 -f Dockerfile.plumber -t <repository_url>/r-qsar-plumber:v1 .
   docker push <repository_url>/r-qsar-plumber:v1
   cd ..
   ```
5. **Create the service** with `docker/plumber_spec.yaml` (see the commented block at the end of `sql/setup.sql`), then smoke-test it:
   ```bash
   export QSAR_ENDPOINT="<ingress_url from SHOW ENDPOINTS>"
   export SNOWFLAKE_PAT="<programmatic access token>"
   python scripts/test_endpoint.py
   ```
6. **Score the compounds** for the dashboard with `02_score_compounds.ipynb`, and upload the model metadata the app reads:
   ```sql
   EXECUTE NOTEBOOK PROJECT QSAR_NOTEBOOKS
       MAIN_FILE = '02_score_compounds.ipynb'
       COMPUTE_POOL = 'QSAR_ST_POOL'
       QUERY_WAREHOUSE = 'QSAR_WH'
       RUNTIME = 'V2-CPU-PY3.12'
       EXTERNAL_ACCESS_INTEGRATIONS = (QSAR_NB_EAI);
   ```
   ```bash
   cp docker/top_features_rcdk.csv /tmp/top_features.csv
   snow sql -q "PUT file:///tmp/top_features.csv @QSAR_DB.PUBLIC.QSAR_STAGE AUTO_COMPRESS=FALSE OVERWRITE=TRUE"
   snow sql -q "PUT file://docker/training_metrics.json @QSAR_DB.PUBLIC.QSAR_STAGE AUTO_COMPRESS=FALSE OVERWRITE=TRUE"
   ```
7. **Deploy the Streamlit app.** In `streamlit/_shared.py`, replace `<hash>` in the service URL with the `dns_name` from `SHOW SERVICES`, then:
   ```bash
   cd streamlit
   snow streamlit deploy --replace
   ```
8. Optionally, **connect an AI assistant** to the MCP endpoint at `https://<ingress_url>/mcp`. See `docs/mcp-walkthrough.md`.

To skip steps 2 and 3, build the image from the bundled model in `docker/`, which was trained on the snapshot in `data/`. In that case, generate the dashboard data from a laptop with `scripts/generate_dashboard_data.py`, which reads `data/chembl1978_cleaned.csv` and calls the public endpoint with a PAT.

## Clean up

Drop the objects in reverse order: the Streamlit app and service, then the compute pools, integrations, database, warehouse, and role. The guide's Clean Up section lists the exact commands.

## Model limitations

This is a teaching example, not a validated drug discovery model.

- **Feature filtering before the split.** `nearZeroVar()` runs on all compounds before the 80/20 split, so the test set influences which bits are kept.
- **Reported R2 is squared correlation.** `test_r2` is `cor(pred, y)^2`, not `1 - SSE/SST`. RMSE is computed conventionally.
- **One split, order-dependent.** The 80/20 split is drawn by row position with a fixed seed, so the same compounds in a different row order give different metrics. Training on notebook 01's output gives test R2 0.6537 and RMSE 0.7949; the bundled model, trained on the `data/` snapshot, gives 0.6855 and 0.7708. Cross-validation would give a more stable estimate.
- **Quantiles are not calibrated intervals.** The 5th to 95th percentile range from the quantile forest is labelled "90% CI" in API output but has not been calibrated.
- **No applicability domain check.** Ethanol receives pIC50 5.11 instead of being flagged as out of domain.
- **Scaffold gaps.** Exemestane, a potent steroidal inhibitor, is predicted 6.21 (Inactive), suggesting weak coverage of steroid scaffolds.
- **Audit identity.** In Streamlit in Snowflake, `QSAR_PREDICTIONS_LOG.USER_NAME` records the app's service user, not the viewer.
- **External lookups.** `streamlit/_shared.py` sends SMILES (structure image fallback) and InChIKeys (name lookup) to PubChem. Disable these before entering proprietary structures. In Streamlit in Snowflake they also require an external access integration.

## Data

Bioactivity data comes from [ChEMBL](https://www.ebi.ac.uk/chembl/), licensed under CC BY-SA 3.0.
