-- ─────────────────────────────────────────────────────────────────────────────
-- QSAR Aromatase MCP Server — Native App Setup Script
--
-- Deploys an R Plumber SPCS service that exposes:
--   GET  /health     — liveness probe
--   POST /predict    — batch pIC50 prediction (Snowflake SPCS format)
--   POST /mcp        — MCP server (JSON-RPC 2.0, Streamable HTTP transport)
--                      Tool: predict_pic50(smiles)
--
-- Consumer install:
--   1. CREATE APPLICATION qsar_mcp
--          FROM APPLICATION PACKAGE QSAR_MCP_PKG USING VERSION V1;
--   2. In Snowsight: approve CREATE COMPUTE POOL + BIND SERVICE ENDPOINT
--   The app creates a compute pool and deploys the service automatically.
-- ─────────────────────────────────────────────────────────────────────────────

CREATE APPLICATION ROLE IF NOT EXISTS QSAR_APP_USER;
CREATE SCHEMA IF NOT EXISTS app_schema;

-- ── Grant callback ────────────────────────────────────────────────────────────
-- Called by Snowflake after the consumer approves the requested privileges.
-- Creates the compute pool and deploys the Plumber MCP service.
CREATE OR REPLACE PROCEDURE app_schema.grant_callback(privs ARRAY)
    RETURNS STRING
    LANGUAGE JAVASCRIPT
    AS $$
    var hasPool     = PRIVS.some(function(p) { return p.toUpperCase() === 'CREATE COMPUTE POOL'; });
    var hasEndpoint = PRIVS.some(function(p) { return p.toUpperCase() === 'BIND SERVICE ENDPOINT'; });

    if (hasPool) {
        snowflake.execute({ sqlText:
            'CREATE COMPUTE POOL IF NOT EXISTS QSAR_MCP_POOL '
          + 'MIN_NODES = 1 MAX_NODES = 1 '
          + 'INSTANCE_FAMILY = CPU_X64_XS '
          + 'AUTO_RESUME = TRUE '
          + 'AUTO_SUSPEND_SECS = 1800'
        });
    }

    if (hasPool && hasEndpoint) {
        var spec = [
            'spec:',
            '  containers:',
            '    - name: qsar-r-plumber',
            '      image: /qsar_mcp_pkg/app_code/app_image_repo/r-qsar-plumber:latest',
            '      readinessProbe:',
            '        port: 5000',
            '        path: /health',
            '  endpoints:',
            '    - name: inference',
            '      port: 5000',
            '      public: true'
        ].join('\n');

        snowflake.execute({ sqlText:
            'CREATE SERVICE IF NOT EXISTS app_schema.qsar_mcp_svc '
          + 'IN COMPUTE POOL QSAR_MCP_POOL '
          + "FROM SPECIFICATION '" + spec + "' "
          + 'MIN_INSTANCES = 1 '
          + 'MAX_INSTANCES = 1 '
          + "COMMENT = 'QSAR Aromatase Inhibitor MCP Server'"
        });
    }

    return 'OK';
    $$;

GRANT USAGE ON PROCEDURE app_schema.grant_callback(ARRAY)
    TO APPLICATION ROLE QSAR_APP_USER;
