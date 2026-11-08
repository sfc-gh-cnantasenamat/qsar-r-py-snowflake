#!/usr/bin/env Rscript
# ─────────────────────────────────────────────────────────────────────────────
# Retrain ranger QSAR model using PPM rcdk fingerprints.
# Ensures training and serving use the SAME fingerprint algorithm (CDK ECFP4).
# ─────────────────────────────────────────────────────────────────────────────

options(repos = c(PPM = "https://packagemanager.posit.co/cran/__linux__/jammy/latest"))
install.packages("caret", quiet = TRUE)

library(rcdk)
library(ranger)
library(caret)
set.seed(42)

# ── Load data ──────────────────────────────────────────────────────────────────
cat("Loading data...\n")
df <- read.csv("/data/chembl1978_cleaned.csv", stringsAsFactors = FALSE)
cat(sprintf("  %d compounds\n", nrow(df)))

# ── Parse SMILES and compute ECFP4 fingerprints using same rcdk as serving ────
cat("Computing ECFP4 fingerprints (PPM rcdk, same as Plumber serving)...\n")
parse_safe <- function(smi) tryCatch(parse.smiles(smi)[[1]], error = function(e) NULL)
mols  <- lapply(df$canonical_smiles, parse_safe)
valid <- vapply(mols, Negate(is.null), logical(1))
df    <- df[valid, ]
mols  <- mols[valid]
cat(sprintf("  Valid SMILES: %d\n", nrow(df)))

fps <- lapply(mols, function(mol) {
  fp <- get.fingerprint(mol, type = "circular", circular.type = "ECFP4",
                        depth = 2, size = 1024)
  v  <- integer(1024)
  v[fp@bits] <- 1L
  v
})
fp_matrix           <- do.call(rbind, fps)
colnames(fp_matrix) <- paste0("bit_", seq_len(1024))
cat(sprintf("  Avg ON bits per molecule: %.1f\n", mean(rowSums(fp_matrix))))

# ── Remove zero-variance bits ──────────────────────────────────────────────────
cat("Removing zero-variance bits...\n")
nzv_idx <- nearZeroVar(fp_matrix, saveMetrics = FALSE)
if (length(nzv_idx) > 0) fp_matrix <- fp_matrix[, -nzv_idx]
cat(sprintf("  Kept %d / 1024 bits\n", ncol(fp_matrix)))

# ── Train/test split ───────────────────────────────────────────────────────────
train_idx <- createDataPartition(df$pchembl_value, p = 0.8, list = FALSE)
train_df  <- as.data.frame(fp_matrix[ train_idx, ])
train_df$target <- df$pchembl_value[ train_idx]
test_x    <- fp_matrix[-train_idx, ]
test_y    <- df$pchembl_value[-train_idx]
cat(sprintf("Train: %d  |  Test: %d\n", nrow(train_df), length(test_y)))

# ── Train ranger RF ────────────────────────────────────────────────────────────
cat("Training ranger RF (500 trees, quantreg=TRUE)...\n")
model <- ranger(
  target ~ .,
  data       = train_df,
  num.trees  = 500,
  importance = "impurity",
  quantreg   = TRUE,
  seed       = 42
)
cat(sprintf("  OOB R2: %.4f\n", model$r.squared))

# ── Evaluate ───────────────────────────────────────────────────────────────────
test_pred <- predict(model, data = as.data.frame(test_x), type = "response")
r2   <- cor(test_pred$predictions, test_y)^2
rmse <- sqrt(mean((test_pred$predictions - test_y)^2))
cat(sprintf("  Test R2: %.4f  |  RMSE: %.4f\n", r2, rmse))

# ── Save artifacts ─────────────────────────────────────────────────────────────
saveRDS(model, "/output/qsar_aromatase_model_rcdk.rds")
cat(sprintf("  Saved model: %d KB\n",
            as.integer(file.info("/output/qsar_aromatase_model_rcdk.rds")$size / 1024)))

kept <- colnames(fp_matrix)
write.csv(data.frame(bit = kept), "/output/kept_bits_rcdk.csv", row.names = FALSE)

imp    <- model$variable.importance
imp_df <- data.frame(bit = names(imp), importance = imp)
imp_df <- imp_df[order(-imp_df$importance), ]
write.csv(head(imp_df, 20), "/output/top_features_rcdk.csv", row.names = FALSE)

# Full ECFP4 bits per measured compound, including held-out rows
write.csv(data.frame(
  molecule_chembl_id = df$molecule_chembl_id,
  canonical_smiles   = df$canonical_smiles,
  pchembl_value      = df$pchembl_value,
  activity_class     = df$activity_class,
  ecfp4_bits         = vapply(fps, function(v) paste(which(v == 1L), collapse = ";"), character(1))
), "/output/train_fingerprints.csv", row.names = FALSE)

metrics <- list(
  oob_r2      = round(model$r.squared, 4),
  test_r2     = round(r2, 4),
  test_rmse   = round(rmse, 4),
  n_compounds = nrow(df),
  n_active    = sum(df$activity_class),
  n_train     = nrow(train_df),
  n_test      = length(test_y),
  n_bits      = ncol(fp_matrix),
  n_trees     = model$num.trees,
  fingerprint = sprintf("CDK ECFP4 (rcdk %s)", packageVersion("rcdk")),
  model       = "ranger quantile RF"
)
jsonlite::write_json(metrics, "/output/training_metrics.json", auto_unbox = TRUE, pretty = TRUE)

cat("Done.\n")
