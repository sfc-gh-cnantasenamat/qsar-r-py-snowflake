# QSAR Aromatase Inhibitor MCP Server

A Snowflake Native App that exposes a **Model Context Protocol (MCP) server** for predicting small-molecule potency against human aromatase (CYP19A1).

## What it does

Provides an MCP tool `predict_pic50(smiles)` that returns:

- Predicted **pIC50** (−log₁₀ IC₅₀ [M])
- **Activity class** (Active if pIC50 ≥ 6.5)
- **90% confidence interval** from ranger quantile regression
- Approximate **IC50 in nM**

## Model

- **Algorithm**: ranger Random Forest, 500 trees, `quantreg=TRUE`
- **Features**: CDK ECFP4 fingerprints (rcdk 3.8.2), 178 informative bits
- **Training data**: ChEMBL1978 — 2751 human aromatase IC50 measurements
- **Performance**: reported by the `get_model_info` tool from the `training_metrics.json` baked into the image
- **Language**: Pure R (rcdk + ranger + Plumber) — no Python/rpy2

## Installation

```sql
-- 1. Create a compute pool (if you don't have one)
CREATE COMPUTE POOL QSAR_CPU_POOL
    MIN_NODES = 1 MAX_NODES = 1
    INSTANCE_FAMILY = CPU_X64_S
    AUTO_RESUME = TRUE;

-- 2. Install the app
CREATE APPLICATION qsar_mcp
    FROM APPLICATION PACKAGE QSAR_MCP_PKG
    USING VERSION V1
    WITH COMPUTE_POOL = QSAR_CPU_POOL;

-- 3. Get your MCP endpoint (~2 min to start)
SHOW ENDPOINTS IN APPLICATION qsar_mcp;
```

## Using the MCP server

Configure any MCP-compatible client (Claude Desktop, CoCo Desktop, etc.):

```json
{
  "mcpServers": {
    "qsar-aromatase": {
      "url": "https://<your-ingress-url>/mcp",
      "transport": "http",
      "headers": {
        "Authorization": "Snowflake Token=\"<your-session-token>\""
      }
    }
  }
}
```

### Available tool: `predict_pic50`

**Input**: `smiles` (string) — canonical SMILES notation  
**Output**: pIC50, activity class, 90% CI, approximate IC50 in nM

**Example agent interaction**:
> *"Is Anastrozole likely to inhibit aromatase?"*  
> Agent calls `predict_pic50("Cc1cc(Cc2cnc(C(C)(C)C#N)cc2)cnc1C(C)(C)C#N")`  
> Returns pIC50 and activity classification with confidence interval

## Semantic layer (Apache Ossie)

This app implements `predict_pic50` as defined in `compound_profiling.ossie.yaml` —  
a vendor-neutral semantic specification for the QSAR compound profiling model.
