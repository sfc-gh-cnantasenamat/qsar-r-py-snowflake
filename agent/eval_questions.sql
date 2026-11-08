-- Optional evaluation fixture. Run in your own guide schema after reviewing
-- the bundled-model expected values. This script replaces AGENT_EVAL_QUESTIONS.
USE ROLE QSAR_ROLE;
USE WAREHOUSE QSAR_WH;
USE SCHEMA QSAR_DB.PUBLIC;
CREATE OR REPLACE TABLE AGENT_EVAL_QUESTIONS (
  INPUT_QUERY VARCHAR,
  GROUND_TRUTH VARIANT
);
INSERT INTO AGENT_EVAL_QUESTIONS (INPUT_QUERY, GROUND_TRUTH)
SELECT column1, PARSE_JSON(TO_JSON(OBJECT_CONSTRUCT('ground_truth_output', column2, 'ground_truth_invocations', PARSE_JSON(column3))))
FROM VALUES
 ('How many unique aromatase compounds are measured?', '2,751 measured compounds, not 3,960 activity records.', '[{"tool_name":"Analyst"}]'),
 ('How many measured compounds are active?', '1,061 active measured compounds.', '[{"tool_name":"Analyst"}]'),
 ('How many measured compounds are inactive?', '1,690 inactive measured compounds.', '[{"tool_name":"Analyst"}]'),
 ('How many compounds pass Rule of Five?', '2,652 of 2,751 compounds pass Rule of Five (at most one violation).', '[{"tool_name":"Analyst"}]'),
 ('What is the activity breakdown for aromatase compounds?', '1,061 active and 1,690 inactive, for 2,751 total.', '[{"tool_name":"Analyst"}]'),
 ('What is the measured versus predicted error for the whole dataset?', 'All 2,751 measured compounds include training and held-out rows; RMSE across both is about 0.469 pIC50 units, not the held-out test RMSE.', '[{"tool_name":"Analyst"}]'),
 ('What assay conditions were used to test exemestane CHEMBL1200374?', 'Report assay IDs from ChEMBL such as CHEMBL3414091 (human placental microsomes, radiolabeled androstenedione, 15 minutes) and CHEMBL3414093 (MCF7-expressed, 1 hour). Do not invent conditions.', '[{"tool_name":"Search"}]'),
 ('Which publication describes structure-guided aromatase inhibitor design?', 'Novel aromatase inhibitors by structure-guided design; Journal of Medicinal Chemistry, 2012, linked document CHEMBL2176958.', '[{"tool_name":"Search"}]'),
 ('Predict pIC50 of letrozole SMILES N#Cc1ccc(cc1)C(n1cncn1)c1ccc(C#N)cc1', 'Bundled R model predicts pIC50 7.89, active; 5th and 95th quantiles 5.79 to 9.22, which are not calibrated confidence intervals.', '[{"tool_name":"Predict"}]'),
 ('Predict the pIC50 for aspirin CC(=O)Oc1ccccc1C(=O)O', 'Predict pIC50 about 5.21, inactive, using the R model. Do not call this a measured result.', '[{"tool_name":"Predict"}]'),
 ('Explain the fingerprint features in letrozole N#Cc1ccc(cc1)C(n1cncn1)c1ccc(C#N)cc1', 'Explain top present model bit 319 with triazole [N]C=[N] and bits 835 or 909 with nitrile. Regression impurity importance is global and not a local contribution.', '[{"tool_name":"Explain"}]'),
 ('Triage letrozole N#Cc1ccc(cc1)C(n1cncn1)c1ccc(C#N)cc1 and aspirin CC(=O)Oc1ccccc1C(=O)O, one SMILES per line', 'Letrozole predicted active 7.89 ranks above aspirin predicted inactive 5.21; aspirin has low similarity around 0.347. Similarity refers to all measured compounds, not training-only.', '[{"tool_name":"Triage"}]'),
 ('Triage not_a_smiles as a molecule', 'Return an invalid SMILES error, not a potency prediction.', '[{"tool_name":"Triage"}]'),
 ('How should I describe the model q05 and q95 values?', 'They are uncalibrated model quantiles, not a calibrated confidence interval. This is a teaching model.', '[]'),
 ('What does the nearest-neighbour Tanimoto score compare against?', 'It compares against all 2,751 measured compounds including held-out rows. It is a similarity heuristic, not a validated applicability-domain measure.', '[]');
