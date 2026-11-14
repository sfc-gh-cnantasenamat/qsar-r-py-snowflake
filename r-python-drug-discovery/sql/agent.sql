-- Optional Aromatase analyst. Run after notebooks 01 and 02 and functions.sql.
USE ROLE QSAR_ROLE;
USE WAREHOUSE QSAR_WH;
USE SCHEMA QSAR_DB.PUBLIC;

CREATE OR REPLACE SEMANTIC VIEW AROMATASE_SV
  TABLES (
    compounds AS CHEMBL1978_COMPOUNDS PRIMARY KEY (MOLECULE_CHEMBL_ID),
    predictions AS CHEMBL1978_PREDICTIONS PRIMARY KEY (MOLECULE_CHEMBL_ID),
    descriptors AS CHEMBL1978_DESCRIPTORS PRIMARY KEY (MOLECULE_CHEMBL_ID),
    fingerprints AS CHEMBL1978_FINGERPRINTS PRIMARY KEY (MOLECULE_CHEMBL_ID),
    assays AS CHEMBL1978_ASSAYS PRIMARY KEY (ASSAY_CHEMBL_ID),
    documents AS CHEMBL1978_DOCUMENTS PRIMARY KEY (DOCUMENT_CHEMBL_ID),
    prediction_log AS QSAR_PREDICTIONS_LOG PRIMARY KEY (PREDICTION_ID)
  )
  RELATIONSHIPS (
    predictions(MOLECULE_CHEMBL_ID) REFERENCES compounds(MOLECULE_CHEMBL_ID),
    descriptors(MOLECULE_CHEMBL_ID) REFERENCES compounds(MOLECULE_CHEMBL_ID),
    fingerprints(MOLECULE_CHEMBL_ID) REFERENCES compounds(MOLECULE_CHEMBL_ID),
    compounds(ASSAY_CHEMBL_ID) REFERENCES assays(ASSAY_CHEMBL_ID),
    assays(DOCUMENT_CHEMBL_ID) REFERENCES documents(DOCUMENT_CHEMBL_ID)
  )
  FACTS (
    compounds.measured_pic50 AS compounds.PCHEMBL_VALUE,
    compounds.active_flag AS compounds.ACTIVITY_CLASS,
    predictions.predicted_pic50 AS predictions.PRED_PIC50,
    predictions.prediction_error AS predictions.ERROR,
    descriptors.molecular_weight AS descriptors.MW,
    descriptors.logp AS descriptors.LOGP,
    descriptors.hba AS descriptors.HBA,
    descriptors.hbd AS descriptors.HBD,
    descriptors.tpsa AS descriptors.TPSA,
    descriptors.ro5_violations AS descriptors.RO5_VIOLATIONS,
    fingerprints.bit_count AS ARRAY_SIZE(fingerprints.ECFP4_BITS),
    prediction_log.predicted_pic50 AS prediction_log.PREDICTED_PIC50
  )
  DIMENSIONS (
    compounds.molecule_id AS compounds.MOLECULE_CHEMBL_ID,
    compounds.smiles AS compounds.CANONICAL_SMILES,
    compounds.activity_label AS CASE WHEN compounds.ACTIVITY_CLASS = 1 THEN 'Active' ELSE 'Inactive' END,
    assays.assay_id AS assays.ASSAY_CHEMBL_ID,
    assays.assay_type AS assays.ASSAY_TYPE,
    assays.assay_description AS assays.DESCRIPTION,
    documents.document_id AS documents.DOCUMENT_CHEMBL_ID,
    documents.title AS documents.TITLE,
    documents.journal AS documents.JOURNAL,
    documents.publication_year AS TRY_TO_NUMBER(documents.YEAR),
    descriptors.ro5_pass AS descriptors.RO5_PASS,
    prediction_log.predicted_at AS prediction_log.PREDICTED_AT,
    prediction_log.service_user AS prediction_log.USER_NAME
  )
  METRICS (
    compounds.compound_count AS COUNT(*),
    compounds.active_count AS SUM(compounds.active_flag),
    compounds.mean_measured_pic50 AS AVG(compounds.measured_pic50),
    predictions.mean_predicted_pic50 AS AVG(predictions.predicted_pic50),
    predictions.rmse AS SQRT(AVG(POWER(predictions.prediction_error, 2))),
    descriptors.ro5_pass_count AS COUNT_IF(descriptors.ro5_pass),
    fingerprints.mean_bits_on AS AVG(fingerprints.bit_count),
    prediction_log.request_count AS COUNT(*)
  )
  COMMENT = 'Measured human aromatase bioactivity and model scores; compound grain, one selected assay per compound. Training and test rows are mixed.'
  AI_VERIFIED_QUERIES (
    compound_totals AS (
      QUESTION 'How many compounds and actives are in the aromatase dataset?'
      ONBOARDING_QUESTION TRUE
      SQL 'SELECT * FROM SEMANTIC_VIEW(QSAR_DB.PUBLIC.AROMATASE_SV METRICS compounds.compound_count, compounds.active_count)'
    ),
    activity_breakdown AS (
      QUESTION 'How many active and inactive aromatase compounds are there?'
      SQL 'SELECT * FROM SEMANTIC_VIEW(QSAR_DB.PUBLIC.AROMATASE_SV DIMENSIONS compounds.activity_label METRICS compounds.compound_count)'
    ),
    ro5_total AS (
      QUESTION 'How many compounds pass the Rule of Five?'
      SQL 'SELECT * FROM SEMANTIC_VIEW(QSAR_DB.PUBLIC.AROMATASE_SV METRICS descriptors.ro5_pass_count)'
    )
  );

-- One search row per measured molecule and assay. Repeated raw measurements
-- for the same molecule, assay and document are deduplicated by SELECT DISTINCT.
CREATE OR REPLACE CORTEX SEARCH SERVICE ASSAY_SEARCH
  ON SEARCH_TEXT
  ATTRIBUTES MOLECULE_CHEMBL_ID, ASSAY_CHEMBL_ID, DOCUMENT_CHEMBL_ID
  WAREHOUSE = QSAR_WH
  TARGET_LAG = '1 day'
  AS
  SELECT DISTINCT
    r.MOLECULE_CHEMBL_ID,
    r.ASSAY_CHEMBL_ID,
    a.DOCUMENT_CHEMBL_ID,
    CONCAT_WS(' | ', 'Assay ID: ' || r.ASSAY_CHEMBL_ID,
              'Publication ID: ' || COALESCE(a.DOCUMENT_CHEMBL_ID, 'unknown'),
              COALESCE(a.DESCRIPTION, ''), COALESCE(d.TITLE, ''),
              COALESCE(d.ABSTRACT, ''), COALESCE(d.JOURNAL, '')) AS SEARCH_TEXT
  FROM CHEMBL1978_RAW_ACTIVITIES r
  JOIN CHEMBL1978_ASSAYS a ON r.ASSAY_CHEMBL_ID = a.ASSAY_CHEMBL_ID
  LEFT JOIN CHEMBL1978_DOCUMENTS d ON a.DOCUMENT_CHEMBL_ID = d.DOCUMENT_CHEMBL_ID;

-- One SMILES per line; the generic agent tool passes one string argument.
-- Novelty compares against all measured compounds (training and held-out rows).
-- It is a similarity heuristic, not a validated applicability domain.
CREATE OR REPLACE PROCEDURE TRIAGE_COMPOUNDS(smiles_list VARCHAR)
  RETURNS VARIANT
  LANGUAGE SQL
  EXECUTE AS OWNER
AS $$
DECLARE
  results ARRAY DEFAULT ARRAY_CONSTRUCT();
  input_rows ARRAY;
  entry VARCHAR;
  predicted VARIANT;
  nearest VARIANT;
  properties VARIANT;
  similarity FLOAT;
  ranking FLOAT;
  reason VARCHAR;
BEGIN
  input_rows := SPLIT(:smiles_list, '\n');
  IF (ARRAY_SIZE(:input_rows) > 20) THEN
    RETURN OBJECT_CONSTRUCT('error', 'At most 20 SMILES per request');
  END IF;
  FOR idx IN 0 TO ARRAY_SIZE(:input_rows) - 1 DO
    entry := TRIM(input_rows[idx]::VARCHAR);
    IF (entry <> '') THEN
      SELECT PREDICT_PIC50(:entry), SIMILAR_COMPOUNDS(:entry, 1), RO5(:entry)
        INTO :predicted, :nearest, :properties;
      IF (predicted:error IS NOT NULL OR properties:error IS NOT NULL) THEN
        results := ARRAY_APPEND(:results,
          OBJECT_CONSTRUCT('smiles', :entry, 'error', 'Invalid SMILES'));
      ELSE
        similarity := nearest:max_tanimoto::FLOAT;
        ranking := predicted:pic50::FLOAT - IFF(similarity < 0.4, 1.0, IFF(similarity < 0.6, 0.5, 0.0))
                   - 0.25 * properties:ro5_violations::INT;
        reason := IFF(similarity < 0.4, 'Low measured-set similarity (<0.4); uncertain extrapolation. ',
                     IFF(similarity < 0.6, 'Moderate measured-set similarity (0.4-0.6). ',
                         'High measured-set similarity (>=0.6). ')) ||
                  IFF(properties:ro5_pass::BOOLEAN, 'Passes Rule of Five.',
                      'Has multiple Rule-of-Five violations.');
        results := ARRAY_APPEND(:results, OBJECT_CONSTRUCT(
          'smiles', :entry, 'predicted_pic50', predicted:pic50,
          'predicted_class', predicted:activity,
          'q05', predicted:q05, 'q95', predicted:q95,
          'max_tanimoto', :similarity,
          'nearest_measured', nearest:neighbours[0],
          'ro5', :properties, 'triage_score', ROUND(:ranking, 3),
          'reason', :reason));
      END IF;
    END IF;
  END FOR;
  SELECT COALESCE(ARRAY_AGG(value) WITHIN GROUP (ORDER BY value:triage_score::FLOAT DESC NULLS LAST), ARRAY_CONSTRUCT())
    INTO :results FROM TABLE(FLATTEN(INPUT => :results));
  RETURN OBJECT_CONSTRUCT('ranked_compounds', :results,
    'note', 'Similarity uses all 2751 measured compounds including held-out rows. Prioritization heuristic, not a clinical decision or calibrated applicability domain. Ranges are uncalibrated model quantiles.');
END;
$$;

-- Create the agent from agent/aromatase_agent.yaml after the SQL above with
-- scripts/deploy_agent.py. The script also lists it in Snowflake Intelligence.
