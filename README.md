# AromatasePred: R-Powered Drug Discovery on Snowflake

Companion repository for the Snowflake developer guide **Build an R and Python Powered Drug Discovery App on Snowflake**.

A QSAR (quantitative structure-activity relationship) model, trained in R, predicts how strongly a molecule inhibits human aromatase (CYP19A1, ChEMBL1978), a breast cancer drug target. R does the cheminformatics and modelling; Python curates the data and serves the interface:

| Layer | Technology | Location |
|---|---|---|
| Data preparation | Python Workspace notebooks: ChEMBL API, pandas, RDKit | `notebooks/` |
| Training | R: `rcdk` ECFP4 fingerprints, `caret`, `ranger` quantile random forest | `docker/train_rcdk.R` |
| Serving | R Plumber container on Snowpark Container Services (SPCS) | `docker/Dockerfile.plumber`, `docker/plumber_api.R` |
| App | Streamlit in Snowflake | `streamlit/` |
| AI assistants | MCP endpoint; optional in-platform Cortex Agent | `/mcp` in `plumber_api.R`, `agent/` |

## Repository layout

```
data/        Snapshot of the cleaned ChEMBL1978 IC50 dataset (2,751 compounds) used for the bundled model
docker/      R training script, Plumber API, Dockerfile, SPCS spec, bundled model artifacts
streamlit/   Multipage app; optional Ask an Agent page appears when an agent exists
notebooks/   01: raw ChEMBL, assays, publications, and EDA; 02: SQL scoring and agent tables
agent/       Optional agent spec and evaluation cases
native_app/  Optional Native App package for per-consumer MCP deployment
scripts/     Endpoint smoke test and dashboard data generator
sql/         Setup, R-backed SQL functions, and optional semantic view, search and triage
docs/        MCP walkthrough
```

## Quick start

The developer guide walks through each step in detail.

1. **Set up Snowflake.** Run `sql/setup.sql` in Snowsight (replace `<your_user>`). It creates the role, warehouse, database, compute pools, stage, image repository, audit table, and external access integrations. `QSAR_ROLE` owns these objects and is granted to `SYSADMIN`, so Snowflake CLI connections that use `SYSADMIN` or `ACCOUNTADMIN` can use them too.
2. **Prepare the training data.** Upload both notebooks to a stage and run `01_prepare_training_data.ipynb`. It fetches raw ChEMBL records, cleans them into `CHEMBL1978_COMPOUNDS`, explores them, and writes `chembl1978_cleaned.csv`, `dashboard_eda.csv`, and `dashboard_ro5.csv` to `@QSAR_STAGE`:
   ```sql
   USE ROLE QSAR_ROLE;
   USE WAREHOUSE QSAR_WH;
   USE SCHEMA QSAR_DB.PUBLIC;
   CREATE STAGE IF NOT EXISTS NB_PROJECT_STAGE;
   ```
   From the repository root in a terminal (not a SQL worksheet):
   ```bash
   snow sql -q "PUT file://notebooks/*.ipynb @QSAR_DB.PUBLIC.NB_PROJECT_STAGE AUTO_COMPRESS=FALSE OVERWRITE=TRUE"
   ```
   Back in the worksheet:
   ```sql
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
    cp output/top_features_rcdk.csv output/training_metrics.json output/train_fingerprints.csv docker/
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
 6. **Create the shared SQL functions** by running `sql/functions.sql` after the service is READY. They expose R-backed `ECFP4`, `PREDICT_PIC50`, `SIMILAR_COMPOUNDS`, and `EXPLAIN_PREDICTION`, plus RDKit `RO5`. The setup grants `SNOWFLAKE.PYPI_REPOSITORY_USER` to pin RO5 to RDKit 2025.09.3.

    **Score the compounds** for the dashboard with `02_score_compounds.ipynb`, and upload the model metadata the app reads:
   ```sql
   USE ROLE QSAR_ROLE;
   USE WAREHOUSE QSAR_WH;
   EXECUTE NOTEBOOK PROJECT QSAR_DB.PUBLIC.QSAR_NOTEBOOKS
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
 7. **Deploy the Streamlit app.** In Snowflake, the app reads the `dns_name` from `SHOW SERVICES` automatically. For local use, configure `spcs.predict_url` in Streamlit secrets. Then:
   ```bash
   cd streamlit
   snow streamlit deploy --replace
   ```
 8. Optionally, **connect an AI assistant** to the MCP endpoint at `https://<ingress_url>/mcp`. See `docs/mcp-walkthrough.md`.

### Optional: Aromatase analyst

This section documents an isolated `QSAR_AG_*` prototype, not a feature of the current live `QSAR_DB` service or app. Deploying these optional objects to `QSAR_DB` and updating the live app require a separate rollout decision.

The local bundled-model shortcut produces dashboard CSVs, not Snowflake tables. Before following this optional section, run notebook 01, `sql/functions.sql`, and notebook 02 even if you served the bundled model. They create the compound, assay, publication, prediction, fingerprint, and descriptor tables that the agent requires.

Notebook 01 also fetches 313 assay descriptions and 198 publication records. Notebook 02 writes per-molecule predictions, full CDK fingerprints, and Rule of Five descriptors. After the core flow, run `sql/agent.sql` to create the `AROMATASE_SV` semantic view, `ASSAY_SEARCH` Cortex Search service and `TRIAGE_COMPOUNDS` procedure. The agent DDL is generated from `agent/aromatase_agent.yaml`:

```bash
python scripts/deploy_agent.py --connection <your_connection>
```

This grants `QSAR_ROLE` invocation access and adds `AROMATASE_AGENT` to Snowflake Intelligence. The Streamlit **Ask an Agent** page appears when the app's Snowflake session can see the agent. It uses Analyst for measured data, Search for assay/publication text, and the R-backed functions for prediction, similarity and explanation. A triage call accepts at most 20 SMILES, one per line. Similarity searches all measured compounds, including held-out rows, so it is not a validated applicability-domain estimate. Explanations use global regression impurity importance for bits present in a SMILES, not causal or local attributions.

The 15-question evaluation fixtures and YAML are in `agent/eval_questions.sql` and `agent/eval_config.yaml`. Their expected predictions refer to the **bundled model**, so update the ground truth after retraining on notebook output. Run the SQL, stage the YAML, then start and inspect a Cortex Agent evaluation with `EXECUTE_AI_EVALUATION`. For repeat runs on the same dataset, remove the `dataset` block from the staged YAML and choose a new run name. In the isolated prototype, all 15 cases completed with mean answer correctness 0.956, logical consistency 0.956, and tool selection 0.967; those scores are not predictive-model accuracy. These optional components incur Cortex and Cortex Search costs. The guide contains the commands for running and checking the evaluation.

To skip steps 2 and 3, build the image from the bundled model in `docker/`, which was trained on the snapshot in `data/`. In that case, install `requests pandas toml snowflake-connector-python 'rdkit==2025.09.3'` in your Python 3.11 environment and generate dashboard data from a laptop with `scripts/generate_dashboard_data.py`, which reads `data/chembl1978_cleaned.csv` and calls the public endpoint with a PAT. To add the optional agent later, run both notebooks as described above.

## Clean up

Drop the objects in reverse order: the Streamlit app and service, then the compute pools, integrations, database, warehouse, and role. The guide's Clean Up section lists the exact commands.

## Use your own names or existing objects

The guide and `sql/setup.sql` create dedicated objects with fixed names. To rename one, or to reuse a warehouse, compute pool, or integration you already have, skip its `CREATE` statement in `sql/setup.sql`, grant the privilege listed below to `QSAR_ROLE`, and replace the name everywhere it appears:

| Object | Default name | Privilege for `QSAR_ROLE` | Where the name appears |
|---|---|---|---|
| Role | `QSAR_ROLE` | n/a | `sql/setup.sql`, every `USE ROLE` in the guide |
| Warehouse | `QSAR_WH` | `USAGE` | `sql/setup.sql`, `streamlit/snowflake.yml` (`query_warehouse`), `QUERY_WAREHOUSE` in the notebook `EXECUTE` statements |
| Database and schema | `QSAR_DB.PUBLIC` | `OWNERSHIP` (or `USAGE` plus create privileges) | `sql/setup.sql`, `DATABASE` in both notebooks, `_DB_SCHEMA` in `streamlit/_shared.py`, `streamlit/snowflake.yml`, `QSAR_STAGE` for `scripts/generate_dashboard_data.py`, the image path in `docker/plumber_spec.yaml` |
| Service compute pool | `QSAR_CPU_POOL` | `USAGE`, `MONITOR` | `sql/setup.sql`, `CREATE SERVICE` |
| App and notebook compute pool | `QSAR_ST_POOL` | `USAGE`, `MONITOR` | `sql/setup.sql`, `streamlit/snowflake.yml` (`compute_pool`), `COMPUTE_POOL` in the notebook `EXECUTE` statements |
| App integration | `QSAR_APP_EAI` | `USAGE` | `sql/setup.sql`, `streamlit/snowflake.yml` (`external_access_integrations`) |
| Notebook integration | `QSAR_NB_EAI` | `USAGE` | `sql/setup.sql`, `EXTERNAL_ACCESS_INTEGRATIONS` in the notebook `EXECUTE` statements |
| Service | `QSAR_PLUMBER_SVC` | owned by `QSAR_ROLE` | `CREATE SERVICE`, `sql/functions.sql`, dynamic DNS lookup in `streamlit/_shared.py` |

An existing integration works if its network rules allow the same hosts: `pypi.org`, `files.pythonhosted.org`, and `pubchem.ncbi.nlm.nih.gov` for the app, and `www.ebi.ac.uk`, `pypi.org`, and `files.pythonhosted.org` for the notebooks. A shared compute pool works if it has a free node for the app or notebook run; the guide's `QSAR_ST_POOL` allows two nodes so a restarted app can start while the old one shuts down.

After you change names, run the grants check from the guide's Setup section with your names to confirm `QSAR_ROLE` has every privilege it needs.

## Model limitations

This is a teaching example, not a validated drug discovery model.

- **Feature filtering before the split.** `nearZeroVar()` runs on all compounds before the 80/20 split, so the test set influences which bits are kept.
- **Reported R2 is squared correlation.** `test_r2` is `cor(pred, y)^2`, not `1 - SSE/SST`. RMSE is computed conventionally.
- **One split, order-dependent.** The 80/20 split is drawn by row position with a fixed seed, so the same compounds in a different row order give different metrics. Training on notebook 01's output gives test R2 0.6537 and RMSE 0.7949; the bundled model, trained on the `data/` snapshot, gives 0.6855 and 0.7708. Cross-validation would give a more stable estimate.
- **Quantiles are not calibrated intervals.** The 5th to 95th percentile range from the quantile forest is labelled "90% CI" in API output but has not been calibrated.
- **No applicability domain check.** Ethanol receives pIC50 5.11 instead of being flagged as out of domain.
- **Held-out misses on known drugs.** Exemestane (measured pIC50 7.82) is in the held-out test set when you train on notebook 01's output and is predicted 6.21 (Inactive). The bundled model, whose training set includes exemestane, predicts 7.00. Steroids make up 682 of 2,751 compounds but are only 31% active, against 39% overall.
- **Audit identity.** In Streamlit in Snowflake, `QSAR_PREDICTIONS_LOG.USER_NAME` records the app's service user, not the viewer.
 - **External lookups.** PubChem name/image lookup is off by default and requires opting in for each molecule on Single Prediction; enabling it sends a SMILES-derived InChIKey, and possibly the SMILES for an image, outside Snowflake. In Streamlit in Snowflake it requires an external access integration. Keep it off for proprietary structures.

## Data

Bioactivity data comes from [ChEMBL](https://www.ebi.ac.uk/chembl/), licensed under CC BY-SA 3.0.
