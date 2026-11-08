-- =============================================================================
-- AromatasePred: shared SQL function layer
--
-- Run after the R service QSAR_PLUMBER_SVC is READY. The four service functions
-- call the R Plumber container, so fingerprints, predictions, similarity, and
-- explanations all use the same rcdk ECFP4 code the model was trained with.
-- RO5() uses RDKit and matches the descriptors in dashboard_ro5.csv.
-- =============================================================================

USE ROLE QSAR_ROLE;
USE WAREHOUSE QSAR_WH;
USE SCHEMA QSAR_DB.PUBLIC;

-- ── 1. Service functions over the R service ─────────────────────────────────
-- Each returns an OBJECT; invalid SMILES return {"error": "..."}.

-- {"bits": [...], "n_bits_on": n}: 1-based ECFP4 bits, same as training
CREATE OR REPLACE FUNCTION ECFP4(smiles VARCHAR)
    RETURNS VARIANT
    STRICT
    SERVICE = QSAR_PLUMBER_SVC
    ENDPOINT = api
    MAX_BATCH_ROWS = 50
    AS '/sf/fingerprint';

-- {"pic50", "activity", "q05", "q95", "ic50_nm"}
CREATE OR REPLACE FUNCTION PREDICT_PIC50(smiles VARCHAR)
    RETURNS VARIANT
    STRICT
    SERVICE = QSAR_PLUMBER_SVC
    ENDPOINT = api
    MAX_BATCH_ROWS = 50
    AS '/sf/predict';

-- {"max_tanimoto", "neighbours": [{molecule_chembl_id, tanimoto, pchembl_value, activity_class, canonical_smiles}]}
CREATE OR REPLACE FUNCTION SIMILAR_COMPOUNDS(smiles VARCHAR, k INTEGER)
    RETURNS VARIANT
    STRICT
    SERVICE = QSAR_PLUMBER_SVC
    ENDPOINT = api
    MAX_BATCH_ROWS = 10
    AS '/sf/similar';

-- {"pic50", "activity", "n_present", "top_features": [{bit, importance_rank, importance, fragments, radius}]}
CREATE OR REPLACE FUNCTION EXPLAIN_PREDICTION(smiles VARCHAR, n INTEGER)
    RETURNS VARIANT
    STRICT
    SERVICE = QSAR_PLUMBER_SVC
    ENDPOINT = api
    MAX_BATCH_ROWS = 10
    AS '/sf/explain';

-- ── 2. Rule of Five descriptors (RDKit) ──────────────────────────────────────
-- Pinned to the RDKit version notebook 01 uses, installed from PyPI. Later
-- releases count hydrogen-bond acceptors differently for about 1,000 compounds.
CREATE OR REPLACE FUNCTION RO5(smiles VARCHAR)
    RETURNS VARIANT
    LANGUAGE PYTHON
    RUNTIME_VERSION = '3.11'
    ARTIFACT_REPOSITORY = snowflake.snowpark.pypi_shared_repository
    PACKAGES = ('rdkit==2025.09.3')
    HANDLER = 'ro5'
AS $$
from rdkit import Chem, RDLogger
from rdkit.Chem import Crippen, Descriptors, rdMolDescriptors

RDLogger.DisableLog("rdApp.*")


def ro5(smiles):
    mol = Chem.MolFromSmiles(smiles) if smiles else None
    if mol is None:
        return {"error": "Invalid SMILES"}
    mw = round(Descriptors.MolWt(mol), 2)
    logp = round(Crippen.MolLogP(mol), 3)
    hbd = rdMolDescriptors.CalcNumHBD(mol)
    hba = rdMolDescriptors.CalcNumHBA(mol)
    violations = (mw > 500) + (logp > 5) + (hbd > 5) + (hba > 10)
    return {"mw": mw, "logp": logp, "hbd": hbd, "hba": hba,
            "tpsa": round(Descriptors.TPSA(mol), 2),
            "ro5_violations": int(violations), "ro5_pass": violations <= 1}
$$;

-- ── 3. Checks ────────────────────────────────────────────────────────────────
-- Letrozole: pIC50 7.89 (bundled model), top feature bit_319 = triazole
SELECT PREDICT_PIC50('N#Cc1ccc(cc1)C(n1cncn1)c1ccc(C#N)cc1') AS prediction,
       RO5('N#Cc1ccc(cc1)C(n1cncn1)c1ccc(C#N)cc1')           AS ro5,
       EXPLAIN_PREDICTION('N#Cc1ccc(cc1)C(n1cncn1)c1ccc(C#N)cc1', 3):top_features AS top_features;
