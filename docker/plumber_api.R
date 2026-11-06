#!/usr/bin/env Rscript
# ─────────────────────────────────────────────────────────────────────────────
# QSAR Aromatase Plumber API
# Serves ranger quantile RF predictions via native R REST endpoints.
# No Python / rpy2 — pure R inference.
#
# Endpoints:
#   GET  /health       — liveness check
#   POST /predict      — batch prediction (Snowflake SPCS format)
#   POST /mcp          — MCP server (JSON-RPC 2.0, Streamable HTTP transport)
#
# MCP tool: predict_pic50(smiles) → pIC50, activity class, 90% CI
# ─────────────────────────────────────────────────────────────────────────────

library(plumber)
library(rcdk)
library(ranger)
library(jsonlite)

# ── Load model artifacts at startup (baked into image) ────────────────────────
cat("Loading model artifacts...\n")
model     <- readRDS("/model/qsar_aromatase_model.rds")
kept_bits <- read.csv("/model/kept_bits.csv")$bit
n_bits    <- length(kept_bits)
# Metrics written by train_rcdk.R for this model; reported by the MCP tools
metrics   <- jsonlite::read_json("/model/training_metrics.json")
cat(sprintf("  ranger model: %d trees, %d features\n", model$num.trees, n_bits))

# ── Pre-warm rcdk JVM (avoids cold-start latency on first request) ────────────
cat("Warming up rcdk JVM...\n")
tryCatch({
  mol <- parse.smiles("C")[[1]]
  get.fingerprint(mol, type = "circular", circular.type = "ECFP4",
                  depth = 2, size = 1024)
  cat("  rcdk JVM ready\n")
}, error = function(e) {
  cat(sprintf("  rcdk warmup warning: %s\n", e$message))
})

cat("Plumber API ready — listening on port 5000\n")

# ── Shared inference helper ───────────────────────────────────────────────────
.run_prediction <- function(smiles) {
  mol <- tryCatch(parse.smiles(smiles)[[1]], error = function(e) NULL)
  if (is.null(mol)) return(list(error = paste("Invalid SMILES:", smiles)))

  fp_obj <- get.fingerprint(mol, type = "circular",
                              circular.type = "ECFP4", depth = 2, size = 1024)
  # Convert S4 fingerprint to integer bit vector via @bits slot (1-based indices)
  fp_vec <- integer(1024)
  fp_vec[fp_obj@bits] <- 1L

  fp_df <- as.data.frame(t(fp_vec))
  colnames(fp_df) <- paste0("bit_", seq_len(1024))
  fp_df <- fp_df[, kept_bits, drop = FALSE]

  pred   <- predict(model, data = fp_df, type = "quantiles",
                    quantiles = c(0.05, 0.5, 0.95))
  preds  <- pred$predictions[1, ]
  ci_lo  <- round(preds[[1]], 3)
  pic50  <- round(preds[[2]], 3)
  ci_hi  <- round(preds[[3]], 3)
  label  <- if (pic50 >= 6.5) "Active" else "Inactive"
  ic50_nm <- round(10^(9 - pic50), 1)

  list(pic50 = pic50, activity = label, ci_lo = ci_lo, ci_hi = ci_hi,
       ic50_nm = ic50_nm)
}

# ─────────────────────────────────────────────────────────────────────────────
# Health check
# ─────────────────────────────────────────────────────────────────────────────

#* Fingerprint diagnostic — bit convention check
#* @get /diag_fp
#* @serializer unboxedJSON
function() {
  smiles <- "CC(=O)Oc1ccccc1C(=O)O"
  mol <- parse.smiles(smiles)[[1]]
  fp_obj <- get.fingerprint(mol, type = "circular", circular.type = "ECFP4",
                             depth = 2, size = 1024)
  bits <- fp_obj@bits
  list(
    class     = class(fp_obj),
    nbit      = fp_obj@nbit,
    n_on      = length(bits),
    min_bit   = if (length(bits) > 0) min(bits) else NA,
    max_bit   = if (length(bits) > 0) max(bits) else NA,
    head_bits = head(bits, 10),
    as_vec_ok = tryCatch({ sum(as.vector(fp_obj)); TRUE }, error = function(e) FALSE)
  )
}

#* @get /health
#* @serializer unboxedJSON
function() {
  list(status = "ok", trees = model$num.trees, features = n_bits)
}

# ─────────────────────────────────────────────────────────────────────────────
# Snowflake SPCS batch prediction endpoint
# ─────────────────────────────────────────────────────────────────────────────

#* @post /predict
#* @serializer unboxedJSON
function(req) {
  body <- tryCatch(jsonlite::fromJSON(rawToChar(req$bodyRaw), simplifyVector = FALSE),
                   error = function(e) req$body)
  rows <- body$data

  results <- lapply(rows, function(row) {
    row_idx <- row[[1]]
    smiles  <- as.character(row[[2]])

    p <- .run_prediction(smiles)
    if (!is.null(p$error)) return(list(row_idx, NA, p$error, NA, NA))
    list(row_idx, p$pic50, p$activity, p$ci_lo, p$ci_hi)
  })

  list(data = results)
}

# ─────────────────────────────────────────────────────────────────────────────
# MCP server — JSON-RPC 2.0, Streamable HTTP transport
# Spec: https://spec.modelcontextprotocol.io/specification/2025-03-26/
#
# Handles: initialize, notifications/initialized, tools/list, tools/call
# ─────────────────────────────────────────────────────────────────────────────

.MCP_TOOLS_LIST <- list(
  list(
    name = "predict_pic50",
    description = paste(
      "Predict pIC50 (-log10 IC50 [M]) of a small molecule against human aromatase",
      "(CYP19A1) using a ranger Random Forest QSAR model trained on ChEMBL1978",
      "(2751 compounds, ECFP4 fingerprints, 500 trees, quantile regression).",
      "Returns predicted pIC50, activity classification (Active if pIC50 >= 6.5),",
      "90% confidence interval, and approximate IC50 in nM.",
      "Target: CYP19A1 (aromatase) — key enzyme in estrogen biosynthesis,",
      "relevant to breast cancer and other hormone-driven conditions."
    ),
    inputSchema = list(
      type = "object",
      properties = list(
        smiles = list(
          type = "string",
          description = paste(
            "Canonical SMILES notation of the molecule to evaluate.",
            "Example: 'CC(=O)Oc1ccccc1C(=O)O' (aspirin)."
          )
        )
      ),
      required = list("smiles")
    )
  ),
  list(
    name = "get_model_info",
    description = paste(
      "Returns metadata about the QSAR aromatase Random Forest model:",
      "dataset, fingerprint type, number of trees, train/test set sizes,",
      "test R² and RMSE, activity threshold, and target protein.",
      "Call this to answer questions about model performance or capabilities."
    ),
    inputSchema = list(
      type = "object",
      properties = setNames(list(), character(0))
    )
  ),
  list(
    name = "validate_smiles",
    description = paste(
      "Checks whether a SMILES string is chemically valid using the CDK toolkit.",
      "Returns valid (true/false), the input SMILES, atom count (if valid),",
      "and a human-readable reason.",
      "Use this to pre-screen inputs before calling predict_pic50,",
      "especially when the SMILES origin is uncertain."
    ),
    inputSchema = list(
      type = "object",
      properties = list(
        smiles = list(
          type = "string",
          description = "SMILES string to validate. Example: 'c1ccccc1' (benzene)."
        )
      ),
      required = list("smiles")
    )
  ),
  list(
    name = "get_dataset_stats",
    description = paste(
      "Returns statistics about the ChEMBL1978 aromatase inhibitor dataset used",
      "to train the QSAR model: total compounds, active/inactive counts and",
      "percentages, train/test split sizes, fingerprint type, and activity label",
      "definition. Use this to understand the training data before interpreting",
      "model predictions."
    ),
    inputSchema = list(
      type = "object",
      properties = setNames(list(), character(0))
    )
  ),
  list(
    name = "get_feature_importance",
    description = paste(
      "Returns the top N most important ECFP4 fingerprint bits (features) ranked",
      "by Gini importance from the Random Forest model.",
      "Useful for understanding which structural substructures drive aromatase",
      "inhibition predictions. Default n=10, maximum n=20."
    ),
    inputSchema = list(
      type = "object",
      properties = list(
        n = list(
          type = "integer",
          description = "Number of top features to return. Default 10, max 20."
        )
      )
    )
  ),
  list(
    name = "get_ro5_stats",
    description = paste(
      "Returns Lipinski Rule of Five (Ro5) compliance statistics for the",
      "ChEMBL1978 aromatase dataset, broken down by activity class",
      "(Active vs Inactive). Reports percentage of compounds satisfying each",
      "rule: MW <= 500 Da, LogP <= 5, HBD <= 5, HBA <= 10.",
      "Also returns median physicochemical property values for the full dataset."
    ),
    inputSchema = list(
      type = "object",
      properties = setNames(list(), character(0))
    )
  )
)

#* MCP server endpoint (JSON-RPC 2.0)
#* @post /mcp
#* @serializer unboxedJSON
function(req) {
  body <- tryCatch(
    jsonlite::fromJSON(rawToChar(req$bodyRaw), simplifyVector = FALSE),
    error = function(e) NULL
  )

  if (is.null(body)) {
    return(list(jsonrpc = "2.0", id = NULL,
                error = list(code = -32700, message = "Parse error")))
  }

  id     <- body[["id"]]
  method <- body[["method"]]

  # ── initialize ──────────────────────────────────────────────────────────────
  if (method == "initialize") {
    return(list(
      jsonrpc = "2.0",
      id      = id,
      result  = list(
        protocolVersion = "2025-03-26",
        serverInfo      = list(name = "qsar-aromatase", version = "1.0.0"),
        capabilities    = list(tools = list(listChanged = FALSE))
      )
    ))
  }

  # ── notifications/initialized ────────────────────────────────────────────────
  if (method == "notifications/initialized") {
    return(list(jsonrpc = "2.0", id = id, result = list()))
  }

  # ── tools/list ──────────────────────────────────────────────────────────────
  if (method == "tools/list") {
    return(list(
      jsonrpc = "2.0",
      id      = id,
      result  = list(tools = .MCP_TOOLS_LIST)
    ))
  }

  # ── tools/call ──────────────────────────────────────────────────────────────
  if (method == "tools/call") {
    tool_name <- body$params$name
    args      <- body$params$arguments

    # ── predict_pic50 ──────────────────────────────────────────────────────────
    if (tool_name == "predict_pic50") {
      smiles <- args$smiles
      if (is.null(smiles) || nchar(trimws(smiles)) == 0) {
        return(list(
          jsonrpc = "2.0", id = id,
          result = list(
            isError = TRUE,
            content = list(list(type = "text",
                                text = "Error: 'smiles' argument is required."))
          )
        ))
      }

      p <- .run_prediction(trimws(smiles))

      if (!is.null(p$error)) {
        return(list(
          jsonrpc = "2.0", id = id,
          result = list(
            isError = TRUE,
            content = list(list(type = "text", text = p$error))
          )
        ))
      }

      text <- sprintf(
        paste0(
          "SMILES: %s\n",
          "pIC50:  %.2f  (%s)\n",
          "90%% CI: [%.2f, %.2f]\n",
          "IC50 ~  %.1f nM\n",
          "\n",
          "Model: ranger RF, 500 trees, quantile regression\n",
          "Target: human aromatase (CYP19A1) | ChEMBL1978\n",
          "Threshold: pIC50 >= 6.5 = Active"
        ),
        smiles, p$pic50, p$activity, p$ci_lo, p$ci_hi, p$ic50_nm
      )

      return(list(
        jsonrpc = "2.0",
        id      = id,
        result  = list(
          content = list(list(type = "text", text = text))
        )
      ))
    }

    # ── get_model_info ─────────────────────────────────────────────────────────
    if (tool_name == "get_model_info") {
      info <- list(
        model            = "ranger Random Forest (quantile regression)",
        trees            = model$num.trees,
        target           = "human aromatase (CYP19A1)",
        dataset          = "ChEMBL1978",
        n_train          = metrics$n_train,
        n_test           = metrics$n_test,
        n_features       = n_bits,
        fingerprint      = "CDK ECFP4 radius-2 1024-bit",
        test_r2          = metrics$test_r2,
        test_rmse_log    = metrics$test_rmse,
        active_threshold = 6.5,
        threshold_unit   = "pIC50 (-log10 IC50 [M])"
      )
      return(list(
        jsonrpc = "2.0", id = id,
        result = list(
          content = list(list(
            type = "text",
            text = jsonlite::toJSON(info, auto_unbox = TRUE, pretty = TRUE)
          ))
        )
      ))
    }

    # ── validate_smiles ────────────────────────────────────────────────────────
    if (tool_name == "validate_smiles") {
      smiles <- args$smiles
      if (is.null(smiles) || nchar(trimws(smiles)) == 0) {
        return(list(
          jsonrpc = "2.0", id = id,
          result = list(
            content = list(list(type = "text", text = jsonlite::toJSON(list(
              valid = FALSE, smiles = "", reason = "Empty or missing SMILES string."
            ), auto_unbox = TRUE)))
          )
        ))
      }
      smiles <- trimws(smiles)
      mol <- tryCatch(parse.smiles(smiles)[[1]], error = function(e) NULL)
      valid <- !is.null(mol)
      reason <- if (valid) {
        n_atoms <- tryCatch(get.atom.count(mol), error = function(e) NA_integer_)
        if (!is.na(n_atoms)) sprintf("Valid molecule with %d heavy atoms.", n_atoms)
        else "Valid molecule."
      } else {
        "Could not parse SMILES — invalid structure or unsupported notation."
      }
      return(list(
        jsonrpc = "2.0", id = id,
        result = list(
          content = list(list(type = "text", text = jsonlite::toJSON(list(
            valid = valid, smiles = smiles, reason = reason
          ), auto_unbox = TRUE)))
        )
      ))
    }

    # ── get_dataset_stats ──────────────────────────────────────────────────────
    if (tool_name == "get_dataset_stats") {
      stats <- list(
        dataset          = "ChEMBL1978",
        target           = "human aromatase (CYP19A1)",
        n_total          = metrics$n_compounds,
        n_active         = metrics$n_active,
        n_inactive       = metrics$n_compounds - metrics$n_active,
        pct_active       = round(100 * metrics$n_active / metrics$n_compounds, 1),
        pct_inactive     = round(100 * (1 - metrics$n_active / metrics$n_compounds), 1),
        n_train          = metrics$n_train,
        n_test           = metrics$n_test,
        fingerprint      = "CDK ECFP4 radius-2 1024-bit",
        n_features_used  = n_bits,
        activity_label   = "Active if pIC50 >= 6.5 (IC50 <= 316 nM)",
        pic50_range      = "4.0 – 11.0 log units"
      )
      return(list(
        jsonrpc = "2.0", id = id,
        result = list(
          content = list(list(
            type = "text",
            text = jsonlite::toJSON(stats, auto_unbox = TRUE, pretty = TRUE)
          ))
        )
      ))
    }

    # ── get_feature_importance ─────────────────────────────────────────────────
    if (tool_name == "get_feature_importance") {
      n_req <- as.integer(if (!is.null(args$n)) args$n else 10L)
      n_req <- max(1L, min(n_req, 20L))

      if (is.null(model$variable.importance) ||
          length(model$variable.importance) == 0) {
        return(list(
          jsonrpc = "2.0", id = id,
          result = list(
            isError = TRUE,
            content = list(list(type = "text",
              text = "Feature importance not available: model was trained without importance='impurity'."))
          )
        ))
      }

      imp     <- sort(model$variable.importance, decreasing = TRUE)
      top_imp <- head(imp, n_req)
      rows    <- lapply(seq_along(top_imp), function(i) {
        list(rank = i, bit = names(top_imp)[i],
             importance = round(top_imp[[i]], 6))
      })
      result <- list(
        n_returned    = length(rows),
        total_features = n_bits,
        importance_type = "Gini (mean decrease in node impurity)",
        features = rows
      )
      return(list(
        jsonrpc = "2.0", id = id,
        result = list(
          content = list(list(
            type = "text",
            text = jsonlite::toJSON(result, auto_unbox = TRUE, pretty = TRUE)
          ))
        )
      ))
    }

    # ── get_ro5_stats ──────────────────────────────────────────────────────────
    if (tool_name == "get_ro5_stats") {
      ro5 <- list(
        description = "Lipinski Rule of Five compliance for ChEMBL1978 (n=2751)",
        rules = list(
          list(rule = "MW <= 500 Da",  active_pct = 98.0, inactive_pct = 93.4),
          list(rule = "LogP <= 5",     active_pct = 81.3, inactive_pct = 78.9),
          list(rule = "HBD <= 5",      active_pct = 100.0, inactive_pct = 99.5),
          list(rule = "HBA <= 10",     active_pct = 99.5,  inactive_pct = 98.9)
        ),
        median_properties_all = list(
          mw   = 322.4,
          logp = 3.9,
          hbd  = 0.0,
          hba  = 3.0
        ),
        note = "Percentages computed over active (n=1061) and inactive (n=1690) subsets."
      )
      return(list(
        jsonrpc = "2.0", id = id,
        result = list(
          content = list(list(
            type = "text",
            text = jsonlite::toJSON(ro5, auto_unbox = TRUE, pretty = TRUE)
          ))
        )
      ))
    }

    # ── unknown tool ───────────────────────────────────────────────────────────
    return(list(
      jsonrpc = "2.0", id = id,
      error = list(code = -32602, message = paste("Unknown tool:", tool_name))
    ))
  }

  # ── unknown method ───────────────────────────────────────────────────────────
  list(
    jsonrpc = "2.0", id = id,
    error = list(code = -32601, message = paste("Method not found:", method))
  )
}
