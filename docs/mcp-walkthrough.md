# MCP Server Walkthrough — QSAR Aromatase Inhibitor Predictor

Two MCP endpoints are available. Use **Standalone** for immediate access with a PAT, or **Native App** for per-consumer installation where each user authenticates with their own Snowflake session.

---

## Getting Started

### What this MCP server does

The QSAR Aromatase MCP server exposes a ranger Random Forest QSAR model trained on 2,751 ChEMBL aromatase (CYP19A1) inhibitors. Once connected to an AI agent, the agent can:

- **Predict** aromatase inhibition activity (pIC50, IC50, activity class, 90% confidence interval) for any molecule given as a SMILES string
- **Validate** SMILES strings before running predictions
- **Explore the dataset** — compound counts, train/test split, class balance, pIC50 range
- **Query model performance** — test R², RMSE, fingerprint type, number of trees and features
- **Rank important features** — top ECFP4 fingerprint bits by Gini importance
- **Check Ro5 compliance** — Lipinski Rule of Five statistics by activity class

### Prerequisites

| Requirement | Details |
|---|---|
| Snowflake account | The provider account running `QSAR_PLUMBER_SVC` for standalone; your own account for Native App |
| PAT or session token | See Step 1 below |
| MCP-capable client | CoCo Desktop, CoCo CLI, Claude Desktop, or any MCP-over-HTTP client |

---

## Option 1 — Standalone MCP

**Endpoint:** `https://<your-ingress-url>/mcp`

> **Note:** SPCS ingress URLs change if the service is altered or recreated. Run `SHOW ENDPOINTS IN SERVICE QSAR_DB.PUBLIC.QSAR_PLUMBER_SVC` to confirm the current URL.

### Step 1: Create a PAT token

In Snowsight: `Profile → Programmatic Access Tokens → + Generate token`

Or in SQL:

```sql
ALTER USER <your_user> ADD PROGRAMMATIC ACCESS TOKEN qsar_demo_token
    ROLE_RESTRICTION = 'QSAR_ROLE'
    DAYS_TO_EXPIRY = 30;
```

### Step 2: Configure your MCP client

**Cortex Code Desktop / CoCo CLI** (`~/.snowflake/cortex/mcp.json`):

```json
{
  "mcpServers": {
    "qsar-aromatase": {
      "transport": "http",
      "url": "https://<your-ingress-url>/mcp",
      "headers": {
        "Authorization": "Snowflake Token=\"<your-PAT>\""
      }
    }
  }
}
```

After editing, reconnect with:

```bash
cortex mcp reconnect qsar-aromatase
# Expected: Connected 'qsar-aromatase' (6 tool(s))
```

**Claude Desktop** (`~/.config/claude/claude_desktop_config.json`) — same JSON structure, restart Claude after saving.

> CoCo Desktop and CoCo CLI share a single config file — one entry registers the server for both clients simultaneously.

### Step 3: Verify the connection

```bash
cortex mcp reconnect qsar-aromatase
```

Expected output: `Connected 'qsar-aromatase' (6 tool(s))`

### Step 4: Verify manually with curl (optional)

```bash
curl -s -X POST \
  -H "Authorization: Snowflake Token=\"<your-PAT>\"" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}' \
  https://<your-ingress-url>/mcp \
  | python3 -c "import sys,json; [print(t['name']) for t in json.load(sys.stdin)['result']['tools']]"
```

Expected:
```
predict_pic50
get_model_info
validate_smiles
get_dataset_stats
get_feature_importance
get_ro5_stats
```

---

## Option 2 — Native App MCP

The Native App packages the same model inside `QSAR_MCP_PKG`. Each consumer installs it in their own Snowflake account and gets their own private endpoint.

> **Note:** `QSAR_MCP_PKG` must exist in the provider account. To distribute externally, publish it via the Snowflake Marketplace or share it as an org listing first.

### Step 1: Install the app

Run in the **consumer's** Snowflake account:

```sql
CREATE APPLICATION qsar_mcp
    FROM APPLICATION PACKAGE QSAR_MCP_PKG
    USING VERSION V1
    DEBUG_MODE = TRUE;
```

### Step 2: Grant required privileges

```sql
GRANT CREATE COMPUTE POOL ON ACCOUNT TO APPLICATION qsar_mcp;
GRANT BIND SERVICE ENDPOINT ON ACCOUNT TO APPLICATION qsar_mcp;
```

### Step 3: Deploy the service

```sql
CALL qsar_mcp.app_schema.grant_callback(
    ARRAY_CONSTRUCT('CREATE COMPUTE POOL', 'BIND SERVICE ENDPOINT')
);
```

### Step 4: Get the endpoint URL

The compute pool and container take ~40 seconds to start. Then:

```sql
SHOW ENDPOINTS IN SERVICE qsar_mcp.app_schema.qsar_mcp_svc;
-- ingress_url column → https://<id>-<account>.snowflakecomputing.app
```

### Step 5: Configure your MCP client

Same format as the standalone option, substituting the URL from Step 4 and your own session token.

---

## Available MCP Tools

### `predict_pic50`

Predicts aromatase inhibitor activity from a SMILES string.

| Parameter | Type   | Required | Description                    |
|-----------|--------|----------|--------------------------------|
| `smiles`  | string | yes      | SMILES string of the molecule  |

**Output:**
```
SMILES: <input>
pIC50:  <value> (Active / Inactive)
90% CI: [<lower>, <upper>]
IC50 ~  <value> nM

Model: ranger RF, 500 trees, quantile regression
Target: human aromatase (CYP19A1) | ChEMBL1978
Threshold: pIC50 >= 6.5 = Active
```

- **Active** = predicted pIC50 ≥ 6.5 (IC50 ≤ ~316 nM)
- **Inactive** = predicted pIC50 < 6.5

---

### `validate_smiles`

Checks whether a SMILES string is chemically valid using the CDK toolkit.

| Parameter | Type   | Required | Description              |
|-----------|--------|----------|--------------------------|
| `smiles`  | string | yes      | SMILES string to validate |

**Output:** `{ valid: true/false, smiles: "...", reason: "..." }`

Use before `predict_pic50` when SMILES origin is uncertain.

---

### `get_model_info`

Returns metadata about the QSAR model — no arguments required.

**Output fields:** model type, number of trees, fingerprint type, dataset name, n_train, n_test, n_features, test R², test RMSE, activity threshold.

---

### `get_dataset_stats`

Returns statistics about the ChEMBL1978 training dataset — no arguments required.

**Output fields:**

| Field | Value |
|---|---|
| n_total | 2,751 |
| n_active | 1,061 (38.6%) |
| n_inactive | 1,690 (61.4%) |
| n_train | 2,203 |
| n_test | 548 |
| fingerprint | CDK ECFP4 radius-2 1024-bit |
| n_features_used | 178 |
| activity_label | Active if pIC50 ≥ 6.5 (IC50 ≤ 316 nM) |

---

### `get_feature_importance`

Returns the top N most important ECFP4 bits by Gini importance.

| Parameter | Type    | Required | Description                        |
|-----------|---------|----------|------------------------------------|
| `n`       | integer | no       | Number of features to return (default 10, max 20) |

**Output:** ranked list of `{ rank, bit, importance }` objects. Importance = mean decrease in node impurity (Gini criterion).

---

### `get_ro5_stats`

Returns Lipinski Rule of Five compliance statistics — no arguments required.

**Output:** per-rule compliance percentages for Active and Inactive compounds, plus overall median MW, LogP, HBD, HBA.

| Rule | Active % | Inactive % |
|---|---|---|
| MW ≤ 500 Da | 98.0 | 93.4 |
| LogP ≤ 5 | 81.3 | 78.9 |
| HBD ≤ 5 | 100.0 | 99.5 |
| HBA ≤ 10 | 99.5 | 98.9 |

---

## Example Questions

Once connected, ask the agent natural-language questions. The agent will call the appropriate tool(s) automatically.

### Single compound prediction

> *"What is the predicted aromatase inhibition activity of anastrozole? SMILES: `Cc1cc(Cc2cnc(C(C)(C)C#N)cc2)cnc1C(C)(C)C#N`"*

> *"Predict the IC50 of letrozole against CYP19A1. SMILES: `N#Cc1ccc(Cc2ccc(C#N)cc2)cc1`"*

> *"Is this compound an active aromatase inhibitor? `c1ccc2c(c1)CC(=O)O2`"*

### Validation before prediction

> *"Check if this SMILES is valid before predicting its activity: `CC(=O)Oc1ccccc1C(=O)O`"*

> *"Validate and then predict the pIC50 for `invalid_smiles_string`"*

### Dataset exploration

> *"How many compounds are in the training dataset, and what fraction are active inhibitors?"*

> *"What is the train/test split for the QSAR model?"*

> *"What pIC50 range does the training data cover?"*

### Model performance

> *"What is the test R² and RMSE of the aromatase QSAR model?"*

> *"How many ECFP4 features does the Random Forest use?"*

> *"Summarise the model: type, training data, performance metrics."*

### Feature importance

> *"Which ECFP4 fingerprint bits are most predictive of aromatase inhibition?"*

> *"Show me the top 10 features ranked by Gini importance."*

> *"Which structural substructures drive the model's predictions most?"*

### Drug-likeness (Ro5)

> *"What percentage of active aromatase inhibitors satisfy Lipinski's Rule of Five?"*

> *"Compare Ro5 compliance between active and inactive compounds in the dataset."*

> *"What are the median MW and LogP values of the ChEMBL1978 dataset?"*

### Multi-step workflows

> *"Validate this SMILES, predict its aromatase activity, and tell me whether it is drug-like according to Ro5: `COc1ccc2c(c1)CCC(=O)O2`"*

> *"Compare the predicted pIC50 of estradiol (`OC1CC2CCC3c4ccc(O)cc4CCC3C2(C)C1`) against the model's average test performance."*

> *"What are the most important fingerprint bits, and how does the model perform on the test set?"*

---

## Comparison

| | Standalone | Native App |
|---|---|---|
| Snowflake account needed | Provider only | Each consumer |
| Authentication | PAT for the provider account | Consumer's own session token |
| Endpoint URL | Fixed (shared) | Unique per installation |
| Setup for consumer | Zero | ~2 min after `CREATE APPLICATION` |
| Best for | Demos, team use | Large-scale distribution |

---

## Maintenance

### Checking the service is running

```bash
curl -s -H "Authorization: Snowflake Token=\"$SNOWFLAKE_PAT\"" https://<your-ingress-url>/health
```

Expected: `{"status":"ok","trees":500,"features":178}`

### Checking or refreshing the ingress URL

```python
import snowflake.connector
conn = snowflake.connector.connect(connection_name='default')
cs = conn.cursor()
cs.execute("SHOW ENDPOINTS IN SERVICE QSAR_DB.PUBLIC.QSAR_PLUMBER_SVC")
print(cs.fetchall())
```

If the URL has changed, update `~/.snowflake/cortex/mcp.json` and run `cortex mcp reconnect qsar-aromatase`.

### Renewing the PAT

PATs expire. Generate a new one in Snowsight (`Profile → Programmatic Access Tokens`), update the `Authorization` header in your MCP client config, and reconnect.

### Rebuilding the Docker image

The SPCS service runs from `Dockerfile.plumber` (not the default `Dockerfile`). Always specify the correct file:

```bash
cd docker/

# Build for AMD64 (SPCS requires linux/amd64)
docker buildx build --platform linux/amd64 \
  -f Dockerfile.plumber \
  -t <org>-<account>.registry.snowflakecomputing.com/qsar_db/public/qsar_repo/r-qsar-plumber:<tag> \
  --load .

# Push
docker push <org>-<account>.registry.snowflakecomputing.com/qsar_db/public/qsar_repo/r-qsar-plumber:<tag>

# Update service
ALTER SERVICE QSAR_DB.PUBLIC.QSAR_PLUMBER_SVC FROM SPECIFICATION $$ ... $$;
```
