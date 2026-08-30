# Databricks notebook source
# MAGIC %md
# MAGIC # Phase 4 — Gold Reconciliation (Centerpiece)
# MAGIC
# MAGIC **Payment Settlement & Reconciliation Lakehouse — Medallion Architecture**
# MAGIC
# MAGIC This notebook performs the core reconciliation of the pipeline. It does a **FULL OUTER JOIN**
# MAGIC between the cleaned internal ledger (`silver_internal`, alias `i`) and the network/scheme
# MAGIC feed (`silver_network`, alias `n`) on `txn_id`, then classifies every transaction into a
# MAGIC `match_status` and materializes:
# MAGIC
# MAGIC - **`gold_recon_results`** — one row per `txn_id` with both sides' amounts/statuses, the
# MAGIC   absolute amount difference, the classification, a `disposition`, and a human-readable `reason`.
# MAGIC - **`gold_exception_cases`** — one case per non-`MATCHED` row, with a deterministic `case_id`,
# MAGIC   a `case_type`, an auto/manual `disposition`, and a `created_ts`.
# MAGIC
# MAGIC ### Classification rules
# MAGIC | match_status | meaning |
# MAGIC |---|---|
# MAGIC | `MATCHED` | both sides present, `abs(amount_diff) <= AMOUNT_TOLERANCE` **and** statuses equal |
# MAGIC | `MISMATCH_AMOUNT` | both present, amounts differ beyond tolerance, statuses equal |
# MAGIC | `MISMATCH_STATUS` | both present, amounts within tolerance, statuses differ (incl. a NULL status) |
# MAGIC | `MISMATCH_BOTH` | both present, amounts differ **and** statuses differ |
# MAGIC | `UNMATCHED_INTERNAL` | present in internal, missing in network |
# MAGIC | `UNMATCHED_NETWORK` | present in network, missing in internal |
# MAGIC
# MAGIC **The classifier itself lives in `src/recon_logic.py`** — the same module the pytest suite
# MAGIC exercises — so tests guard exactly what runs here (no test/prod drift).
# MAGIC
# MAGIC **Consumes:** `workspace.settlement_recon.silver_internal`, `workspace.settlement_recon.silver_network`
# MAGIC **Produces:** `gold_recon_results`, `gold_exception_cases`

# COMMAND ----------

# MAGIC %md
# MAGIC ## Config — shared constants (repeated in every phase notebook)

# COMMAND ----------

# Shared constants — must match every other phase notebook exactly.
CATALOG = "workspace"          # Free Edition default; if you hit a permission error, swap to an existing catalog
SCHEMA  = "settlement_recon"
VOLUME  = "landing"

VOLUME_ROOT   = f"/Volumes/{CATALOG}/{SCHEMA}/{VOLUME}"
INTERNAL_PATH = f"{VOLUME_ROOT}/internal"
NETWORK_PATH  = f"{VOLUME_ROOT}/network"

# Fully-qualified table names (naming contract)
SILVER_INTERNAL = f"{CATALOG}.{SCHEMA}.silver_internal"
SILVER_NETWORK  = f"{CATALOG}.{SCHEMA}.silver_network"
GOLD_RECON      = f"{CATALOG}.{SCHEMA}.gold_recon_results"
GOLD_EXCEPTIONS = f"{CATALOG}.{SCHEMA}.gold_exception_cases"

# Reconciliation tolerances come from the shared, unit-tested module (single source of truth).
from src.recon_logic import AMOUNT_TOLERANCE, AUTO_RESOLVE_TOLERANCE

# Ensure the schema exists (idempotent); harmless if already created by earlier phases.
spark.sql(f"CREATE SCHEMA IF NOT EXISTS {CATALOG}.{SCHEMA}")
spark.sql(f"USE {CATALOG}.{SCHEMA}")

print(f"Reconciling {SILVER_INTERNAL}  x  {SILVER_NETWORK}")
print(f"AMOUNT_TOLERANCE={AMOUNT_TOLERANCE}  AUTO_RESOLVE_TOLERANCE={AUTO_RESOLVE_TOLERANCE}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. Load Silver inputs
# MAGIC
# MAGIC Both Silver tables are cleaned/deduped/typed by Phase 3. `business_date` and `channel` exist on
# MAGIC both sides; the classifier coalesces them so the result has a value even when one side is missing.

# COMMAND ----------

from pyspark.sql import functions as F
from pyspark.sql.window import Window

silver_i = spark.table(SILVER_INTERNAL)
silver_n = spark.table(SILVER_NETWORK)

print("silver_internal rows:", silver_i.count())
print("silver_network  rows:", silver_n.count())

silver_i.printSchema()
silver_n.printSchema()

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. Full outer join + classification (via the shared module)
# MAGIC
# MAGIC The full-outer-join, the six-way `match_status` classification, `amount_diff`, the NULL-safe
# MAGIC status comparison, the auto/manual `disposition`, and the human-readable `reason` are all
# MAGIC produced by `reconcile()` in `src/recon_logic.py`. This notebook must NOT reimplement them.

# COMMAND ----------

# Single source of truth: Databricks Git folders auto-add the repo root to sys.path, so this import
# resolves when the notebook runs inside the `build` Git folder.
from src.recon_logic import reconcile

recon = reconcile(silver_i, silver_n)
recon.printSchema()

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. Write `gold_recon_results`
# MAGIC
# MAGIC Idempotent full overwrite (reconciliation is recomputed from the current Silver state each run).
# MAGIC (Phase P3 of the roadmap makes this Change-Data-Feed-driven and incremental.)

# COMMAND ----------

(
    recon.write
    .format("delta")
    .mode("overwrite")
    .option("overwriteSchema", "true")
    .saveAsTable(GOLD_RECON)
)

print(f"Wrote {GOLD_RECON}: {spark.table(GOLD_RECON).count()} rows")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4. Build `gold_exception_cases`
# MAGIC
# MAGIC Every non-`MATCHED` row becomes an exception case. `disposition` is carried forward from the
# MAGIC reconciliation result (computed once, in the shared module).
# MAGIC
# MAGIC - `case_id = concat("CASE-", business_date, "-", lpad(row_number, 8, "0"))` — `row_number` is
# MAGIC   assigned **per business_date** (ordered by txn_id).
# MAGIC   *(Known limitation: positional ids are not stable when the case set changes — roadmap P3
# MAGIC   replaces this with a sha2 `case_key` + a MERGE lifecycle.)*
# MAGIC - `case_type = match_status`; `created_ts = current_timestamp()`.

# COMMAND ----------

non_matched = spark.table(GOLD_RECON).filter(F.col("match_status") != "MATCHED")

# Deterministic per-date row numbering (ordered by txn_id) for stable-within-a-run case ids
w = Window.partitionBy("business_date").orderBy("txn_id")

exceptions = (
    non_matched
    .withColumn("row_number", F.row_number().over(w))
    .withColumn(
        "case_id",
        F.concat(
            F.lit("CASE-"),
            F.col("business_date").cast("string"),
            F.lit("-"),
            F.lpad(F.col("row_number").cast("string"), 8, "0"),
        ),
    )
    .withColumn("case_type", F.col("match_status"))
    .withColumn("created_ts", F.current_timestamp())
    .select(
        "case_id",
        "txn_id",
        "business_date",
        "channel",
        "case_type",
        "internal_amount",
        "network_amount",
        "amount_diff",
        "internal_status",
        "network_status",
        "disposition",
        "reason",
        "created_ts",
    )
)

(
    exceptions.write
    .format("delta")
    .mode("overwrite")
    .option("overwriteSchema", "true")
    .saveAsTable(GOLD_EXCEPTIONS)
)

print(f"Wrote {GOLD_EXCEPTIONS}: {spark.table(GOLD_EXCEPTIONS).count()} rows")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 5. Verification
# MAGIC
# MAGIC Counts by `match_status` (reconciliation outcome distribution) and by `disposition`
# MAGIC (auto vs manual workload).

# COMMAND ----------

print("=== gold_recon_results: counts by match_status ===")
(
    spark.table(GOLD_RECON)
    .groupBy("match_status")
    .count()
    .orderBy(F.col("count").desc())
    .show(truncate=False)
)

print("=== gold_exception_cases: counts by case_type ===")
(
    spark.table(GOLD_EXCEPTIONS)
    .groupBy("case_type")
    .count()
    .orderBy(F.col("count").desc())
    .show(truncate=False)
)

print("=== gold_exception_cases: counts by disposition ===")
(
    spark.table(GOLD_EXCEPTIONS)
    .groupBy("disposition")
    .count()
    .orderBy(F.col("count").desc())
    .show(truncate=False)
)

print("=== gold_exception_cases: disposition x case_type ===")
(
    spark.table(GOLD_EXCEPTIONS)
    .groupBy("case_type", "disposition")
    .count()
    .orderBy("case_type", "disposition")
    .show(truncate=False)
)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 6. Sample exception cases (eyeball check)

# COMMAND ----------

(
    spark.table(GOLD_EXCEPTIONS)
    .orderBy("business_date", "case_id")
    .show(20, truncate=False)
)

# COMMAND ----------

# Machine-readable run summary (consumable by Phase 6 orchestration / observability).
import json

_status_counts = {
    r["match_status"]: r["count"]
    for r in spark.table(GOLD_RECON).groupBy("match_status").count().collect()
}
_disp_counts = {
    r["disposition"]: r["count"]
    for r in spark.table(GOLD_EXCEPTIONS).groupBy("disposition").count().collect()
}

dbutils.notebook.exit(
    json.dumps(
        {
            "gold_recon_rows": spark.table(GOLD_RECON).count(),
            "exception_rows": spark.table(GOLD_EXCEPTIONS).count(),
            "match_status_counts": _status_counts,
            "disposition_counts": _disp_counts,
        }
    )
)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Done-criteria & hand-off
# MAGIC
# MAGIC **Done when:**
# MAGIC - `gold_recon_results` exists with one row per `txn_id` and every row carries a `match_status`
# MAGIC   in {`MATCHED`, `MISMATCH_AMOUNT`, `MISMATCH_STATUS`, `MISMATCH_BOTH`, `UNMATCHED_INTERNAL`, `UNMATCHED_NETWORK`}.
# MAGIC - `gold_exception_cases` contains exactly the non-`MATCHED` rows, each with a deterministic
# MAGIC   `case_id`, `case_type`, `disposition` (`AUTO`/`MANUAL`), and `created_ts`.
# MAGIC - The verification cell shows non-empty counts by `match_status` and by `disposition`.
# MAGIC
# MAGIC **Next phase (Phase 5 — Reports) consumes:**
# MAGIC - `gold_recon_results` -> `gold_report_funding_by_channel`, `gold_report_cash_flow`
# MAGIC - `gold_exception_cases` -> `gold_report_exception_summary`
