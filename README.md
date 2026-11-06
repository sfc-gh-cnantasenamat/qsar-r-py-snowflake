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
data/        Cleaned ChEMBL1978 IC50 dataset (2,751 compounds) and fetch script
docker/      R training script, Plumber API, Dockerfile, SPCS spec, model artifacts
streamlit/   Multipage Streamlit app (Dashboard, Single and Batch Prediction, About)
notebooks/   Workspace notebook: raw ChEMBL records to dashboard data on the stage
native_app/  Optional Native App package for per-consumer MCP deployment
scripts/     Endpoint smoke test and dashboard data generator
sql/         Snowflake setup (role, warehouse, database, pool, repo, stage, log table)
docs/        MCP walkthrough
```

## Quick start

1. Run `sql/setup.sql` in Snowsight (replace `<your_user>`).
2. Build and push the image:
   ```bash
   cd docker
   snow spcs image-registry login
   docker build --platform linux/amd64 -f Dockerfile.plumber -t <repository_url>/r-qsar-plumber:v1 .
   docker push <repository_url>/r-qsar-plumber:v1
   ```
3. Create the service with `docker/plumber_spec.yaml` (see the commented block at the end of `sql/setup.sql`).
4. Smoke-test the endpoint:
   ```bash
   export QSAR_ENDPOINT="<ingress_url from SHOW ENDPOINTS>"
   export SNOWFLAKE_PAT="<programmatic access token>"
   python scripts/test_endpoint.py
   ```

Full instructions are in the developer guide.

## Retrain the model

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
