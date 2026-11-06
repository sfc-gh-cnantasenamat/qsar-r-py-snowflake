# AromatasePred: R-Powered Drug Discovery on Snowflake

Companion repository for the Snowflake developer guide **Build an R-Powered Drug Discovery App with Snowpark Container Services**.

A QSAR (quantitative structure-activity relationship) model, trained in R, predicts how strongly a molecule inhibits human aromatase (CYP19A1, ChEMBL1978), a breast cancer drug target. R does the science, Python serves the interface:

| Layer | Technology | Location |
|---|---|---|
| Training | R: `rcdk` ECFP4 fingerprints, `caret`, `ranger` quantile random forest | `docker/train_rcdk.R` |
| Serving | R Plumber container on Snowpark Container Services (SPCS) | `docker/Dockerfile.plumber`, `docker/plumber_api.R` |
| App | Streamlit in Snowflake | `streamlit/` |
| AI assistants | MCP (JSON-RPC 2.0) endpoint on the same container | `/mcp` in `plumber_api.R`, `docs/mcp-walkthrough.md` |

## Repository layout

```
data/        Cleaned ChEMBL1978 IC50 dataset (2,751 compounds) used for R training
docker/      R training script, Plumber API, Dockerfile, SPCS spec, model artifacts
streamlit/   Multipage Streamlit app (Dashboard, Single and Batch Prediction, About)
notebooks/   Workspace notebook: raw ChEMBL records to dashboard data on the stage
native_app/  Optional Native App package for per-consumer MCP deployment
scripts/     Endpoint smoke test and dashboard data generator
sql/         Snowflake setup (role, warehouse, database, pool, repo, stage, log table)
docs/        MCP walkthrough
```

## Quick start

The developer guide walks through each step in detail.

1. Run `sql/setup.sql` in Snowsight (replace `<your_user>`). It creates the role, warehouse, database, compute pools, stage, image repository, audit table, and external access integrations.
2. Build and push the image:
   ```bash
   cd docker
   snow spcs image-registry login
   docker build --platform linux/amd64 -f Dockerfile.plumber -t <repository_url>/r-qsar-plumber:v1 .
   docker push <repository_url>/r-qsar-plumber:v1
   cd ..
   ```
3. Create the service with `docker/plumber_spec.yaml` (see the commented block at the end of `sql/setup.sql`).
4. Smoke-test the endpoint:
   ```bash
   export QSAR_ENDPOINT="<ingress_url from SHOW ENDPOINTS>"
   export SNOWFLAKE_PAT="<programmatic access token>"
   python scripts/test_endpoint.py
   ```
5. Prepare the dashboard data by running `notebooks/prepare_dashboard_data.ipynb` in Snowflake. It fetches raw ChEMBL records, cleans them, scores them with the service, and writes the dashboard CSVs to `@QSAR_STAGE`:
   ```sql
   USE SCHEMA QSAR_DB.PUBLIC;
   CREATE STAGE IF NOT EXISTS NB_PROJECT_STAGE;
   -- snow sql -q "PUT file://notebooks/prepare_dashboard_data.ipynb @QSAR_DB.PUBLIC.NB_PROJECT_STAGE AUTO_COMPRESS=FALSE OVERWRITE=TRUE"
   CREATE OR REPLACE NOTEBOOK PROJECT DASHBOARD_DATA_PROJ FROM '@NB_PROJECT_STAGE';
   EXECUTE NOTEBOOK PROJECT DASHBOARD_DATA_PROJ
       MAIN_FILE = 'prepare_dashboard_data.ipynb'
       COMPUTE_POOL = 'QSAR_ST_POOL'
       QUERY_WAREHOUSE = 'QSAR_WH'
       RUNTIME = 'V2-CPU-PY3.12'
       EXTERNAL_ACCESS_INTEGRATIONS = (QSAR_NB_EAI);
   ```
   Then upload the model metadata that the About and Dashboard pages read:
   ```bash
   cp docker/top_features_rcdk.csv /tmp/top_features.csv
   snow sql -q "PUT file:///tmp/top_features.csv @QSAR_DB.PUBLIC.QSAR_STAGE AUTO_COMPRESS=FALSE OVERWRITE=TRUE"
   snow sql -q "PUT file://docker/training_metrics.json @QSAR_DB.PUBLIC.QSAR_STAGE AUTO_COMPRESS=FALSE OVERWRITE=TRUE"
   ```
6. Deploy the Streamlit app. In `streamlit/_shared.py`, replace `<hash>` in the service URL with the `dns_name` from `SHOW SERVICES`, then:
   ```bash
   cd streamlit
   snow streamlit deploy --replace
   ```
7. Optionally, connect an AI assistant to the MCP endpoint at `https://<ingress_url>/mcp`. See `docs/mcp-walkthrough.md`.

To prepare the dashboard data from a laptop instead of step 5, run `scripts/generate_dashboard_data.py`, which reads `data/chembl1978_cleaned.csv` and calls the public endpoint with a PAT.

## Clean up

Drop the objects in reverse order: the Streamlit app and service, then the compute pools, integrations, database, warehouse, and role. The guide's Clean Up section lists the exact commands.

## Retrain the model

To retrain on fresh ChEMBL data, download `chembl1978_cleaned.csv` from `@QSAR_STAGE` (written by the notebook) into `data/` first.

```bash
docker build --platform linux/amd64 -f docker/Dockerfile.plumber -t r-qsar-plumber:local docker
mkdir -p output
docker run --rm --platform linux/amd64 \
  -v "$PWD/data:/data" -v "$PWD/output:/output" -v "$PWD/docker:/work" \
  r-qsar-plumber:local Rscript /work/train_rcdk.R
cp output/qsar_aromatase_model_rcdk.rds docker/qsar_aromatase_model.rds
cp output/kept_bits_rcdk.csv docker/kept_bits.csv
```

## Model limitations

This is a teaching example, not a validated drug discovery model.

- **Feature filtering before the split.** `nearZeroVar()` runs on all compounds before the 80/20 split, so the test set influences which bits are kept.
- **Reported R2 is squared correlation.** `test_r2` (0.6855) is `cor(pred, y)^2`, not `1 - SSE/SST`. RMSE (0.771) is computed conventionally.
- **Quantiles are not calibrated intervals.** The 5th to 95th percentile range from the quantile forest is labelled "90% CI" in API output but has not been calibrated.
- **No applicability domain check.** Ethanol receives pIC50 5.11 instead of being flagged as out of domain.
- **Scaffold gaps.** Exemestane, a potent steroidal inhibitor, is predicted 6.21 (Inactive), suggesting weak coverage of steroid scaffolds.
- **Audit identity.** In Streamlit in Snowflake, `QSAR_PREDICTIONS_LOG.USER_NAME` records the app's service user, not the viewer.
- **External lookups.** `streamlit/_shared.py` sends SMILES (structure image fallback) and InChIKeys (name lookup) to PubChem. Disable these before entering proprietary structures. In Streamlit in Snowflake they also require an external access integration.

## Data

Bioactivity data comes from [ChEMBL](https://www.ebi.ac.uk/chembl/), licensed under CC BY-SA 3.0.
