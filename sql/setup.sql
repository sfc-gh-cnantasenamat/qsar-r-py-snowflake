-- =============================================================================
-- AromatasePred: Snowflake setup for the R Plumber inference service
--
-- Run steps 1-2 as ACCOUNTADMIN, then switch to QSAR_ROLE for the rest.
-- Replace <your_user> before running.
-- =============================================================================

-- ── 1. Role, warehouse, database ─────────────────────────────────────────────
USE ROLE ACCOUNTADMIN;

CREATE ROLE IF NOT EXISTS QSAR_ROLE;
GRANT ROLE QSAR_ROLE TO USER <your_user>;

CREATE WAREHOUSE IF NOT EXISTS QSAR_WH
    WAREHOUSE_SIZE = XSMALL
    AUTO_SUSPEND = 60
    AUTO_RESUME = TRUE;
GRANT USAGE ON WAREHOUSE QSAR_WH TO ROLE QSAR_ROLE;

CREATE DATABASE IF NOT EXISTS QSAR_DB;
GRANT OWNERSHIP ON DATABASE QSAR_DB TO ROLE QSAR_ROLE COPY CURRENT GRANTS;
GRANT OWNERSHIP ON SCHEMA QSAR_DB.PUBLIC TO ROLE QSAR_ROLE COPY CURRENT GRANTS;

-- ── 2. Compute pool and endpoint privilege ───────────────────────────────────
CREATE COMPUTE POOL IF NOT EXISTS QSAR_CPU_POOL
    MIN_NODES = 1
    MAX_NODES = 1
    INSTANCE_FAMILY = CPU_X64_S
    AUTO_RESUME = TRUE
    AUTO_SUSPEND_SECS = 3600
    COMMENT = 'QSAR aromatase SPCS inference';
GRANT USAGE, MONITOR ON COMPUTE POOL QSAR_CPU_POOL TO ROLE QSAR_ROLE;
GRANT BIND SERVICE ENDPOINT ON ACCOUNT TO ROLE QSAR_ROLE;

-- Separate small pool for the Streamlit container runtime.
-- MAX_NODES = 2 leaves room for a new app instance while an old one shuts down.
CREATE COMPUTE POOL IF NOT EXISTS QSAR_ST_POOL
    MIN_NODES = 1
    MAX_NODES = 2
    INSTANCE_FAMILY = CPU_X64_XS
    AUTO_RESUME = TRUE
    AUTO_SUSPEND_SECS = 600
    COMMENT = 'Streamlit container runtime for the QSAR app';
GRANT USAGE, MONITOR ON COMPUTE POOL QSAR_ST_POOL TO ROLE QSAR_ROLE;

-- ── 3. Stage, image repository, audit table ──────────────────────────────────
USE ROLE QSAR_ROLE;
USE WAREHOUSE QSAR_WH;
USE SCHEMA QSAR_DB.PUBLIC;

CREATE STAGE IF NOT EXISTS QSAR_STAGE
    ENCRYPTION = (TYPE = 'SNOWFLAKE_SSE')
    COMMENT = 'Model metadata and dashboard CSVs for the Streamlit app';

CREATE IMAGE REPOSITORY IF NOT EXISTS QSAR_REPO
    COMMENT = 'R Plumber inference image';

-- repository_url column is the docker push target
SHOW IMAGE REPOSITORIES IN SCHEMA QSAR_DB.PUBLIC;

CREATE TABLE IF NOT EXISTS QSAR_PREDICTIONS_LOG (
    PREDICTION_ID    NUMBER AUTOINCREMENT START 1 INCREMENT 1 NOORDER,
    PREDICTED_AT     TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP(),
    USER_NAME        VARCHAR(100) DEFAULT CURRENT_USER(),
    INPUT_SMILES     VARCHAR(4000) NOT NULL,
    PREDICTED_PIC50  FLOAT NOT NULL,
    ACTIVITY_CLASS   VARCHAR(10) NOT NULL,
    IC50_NM_APPROX   FLOAT,
    PRIMARY KEY (PREDICTION_ID)
)
COMMENT = 'Audit trail of predictions made from the Streamlit app';

-- ── 4. Egress for the Streamlit app ──────────────────────────────────────────
-- PyPI installs rdkit and streamlit-jsme; PubChem serves optional name and
-- structure-image lookups (this sends SMILES and InChIKeys outside Snowflake).
CREATE NETWORK RULE IF NOT EXISTS QSAR_APP_EGRESS
    TYPE = HOST_PORT
    MODE = EGRESS
    VALUE_LIST = ('pypi.org', 'files.pythonhosted.org', 'pubchem.ncbi.nlm.nih.gov');

USE ROLE ACCOUNTADMIN;
CREATE EXTERNAL ACCESS INTEGRATION IF NOT EXISTS QSAR_APP_EAI
    ALLOWED_NETWORK_RULES = (QSAR_DB.PUBLIC.QSAR_APP_EGRESS)
    ENABLED = TRUE;
GRANT USAGE ON INTEGRATION QSAR_APP_EAI TO ROLE QSAR_ROLE;

-- Egress for notebooks/prepare_dashboard_data.ipynb: ChEMBL REST API and PyPI (RDKit).
-- The notebook can run on QSAR_ST_POOL.
CREATE NETWORK RULE IF NOT EXISTS QSAR_DB.PUBLIC.QSAR_NB_EGRESS
    TYPE = HOST_PORT
    MODE = EGRESS
    VALUE_LIST = ('www.ebi.ac.uk', 'pypi.org', 'files.pythonhosted.org');
CREATE EXTERNAL ACCESS INTEGRATION IF NOT EXISTS QSAR_NB_EAI
    ALLOWED_NETWORK_RULES = (QSAR_DB.PUBLIC.QSAR_NB_EGRESS)
    ENABLED = TRUE;
GRANT USAGE ON INTEGRATION QSAR_NB_EAI TO ROLE QSAR_ROLE;
USE ROLE QSAR_ROLE;

-- ── 5. Inference service (run after pushing the image) ───────────────────────
-- CREATE SERVICE QSAR_PLUMBER_SVC
--     IN COMPUTE POOL QSAR_CPU_POOL
--     FROM SPECIFICATION $$
--     <contents of docker/plumber_spec.yaml>
--     $$
--     MIN_INSTANCES = 1
--     MAX_INSTANCES = 1;
--
-- SHOW SERVICE CONTAINERS IN SERVICE QSAR_PLUMBER_SVC;
-- SHOW ENDPOINTS IN SERVICE QSAR_PLUMBER_SVC;
