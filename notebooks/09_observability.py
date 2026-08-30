# Databricks notebook source
# MAGIC %md
# MAGIC # Phase — Observability (pipeline health run log)
# MAGIC
# MAGIC Appends one health row per run to `ops_run_log` and shows a monitoring view. A portable,
# MAGIC self-contained alternative to Databricks system tables (which may not be exposed on Free Edition).
# MAGIC Pair a SQL Alert on `exception_rate` in Databricks SQL with this table for free alerting.

# COMMAND ----------

import json
from datetime import datetime, timezone

from pyspark.sql import functions as F
from pyspark.sql.types import (
    DoubleType,
    LongType,
    StructField,
    StructType,
    TimestampType,
)

CATALOG = "workspace"
SCHEMA = "settlement_recon"


def fq(name: str) -> str:
    return f"{CATALOG}.{SCHEMA}.{name}"


spark.sql(f"USE CATALOG {CATALOG}")
spark.sql(f"USE SCHEMA {SCHEMA}")

RUN_LOG = fq("ops_run_log")

spark.sql(
    f"""
    CREATE TABLE IF NOT EXISTS {RUN_LOG} (
        run_ts           TIMESTAMP,
        gold_recon_rows  BIGINT,
        exception_rows   BIGINT,
        matched          BIGINT,
        match_rate       DOUBLE,
        exception_rate   DOUBLE,
        auto_count       BIGINT,
        manual_count     BIGINT,
        open_cases       BIGINT,
        silver_internal  BIGINT,
        silver_network   BIGINT
    )
    USING DELTA
    """
)

# COMMAND ----------


def safe_count(name: str) -> int:
    try:
        return spark.table(fq(name)).count()
    except Exception:  # noqa: BLE001
        return 0


gold_recon_rows = safe_count("gold_recon_results")
matched = (
    spark.table(fq("gold_recon_results")).filter("match_status = 'MATCHED'").count()
    if gold_recon_rows
    else 0
)
exception_rows = safe_count("gold_exception_cases")

disp = {}
open_cases = 0
if exception_rows:
    exc = spark.table(fq("gold_exception_cases"))
    disp = {
        r["disposition"]: r["c"]
        for r in exc.groupBy("disposition").agg(F.count("*").alias("c")).collect()
    }
    open_cases = exc.filter("status = 'OPEN'").count()

match_rate = round(matched / gold_recon_rows, 4) if gold_recon_rows else 0.0
exception_rate = round(exception_rows / gold_recon_rows, 4) if gold_recon_rows else 0.0

row = (
    datetime.now(timezone.utc).replace(tzinfo=None),
    int(gold_recon_rows),
    int(exception_rows),
    int(matched),
    float(match_rate),
    float(exception_rate),
    int(disp.get("AUTO", 0)),
    int(disp.get("MANUAL", 0)),
    int(open_cases),
    int(safe_count("silver_internal")),
    int(safe_count("silver_network")),
)

schema = StructType(
    [
        StructField("run_ts", TimestampType(), False),
        StructField("gold_recon_rows", LongType(), False),
        StructField("exception_rows", LongType(), False),
        StructField("matched", LongType(), False),
        StructField("match_rate", DoubleType(), False),
        StructField("exception_rate", DoubleType(), False),
        StructField("auto_count", LongType(), False),
        StructField("manual_count", LongType(), False),
        StructField("open_cases", LongType(), False),
        StructField("silver_internal", LongType(), False),
        StructField("silver_network", LongType(), False),
    ]
)

spark.createDataFrame([row], schema=schema).write.mode("append").saveAsTable(RUN_LOG)
print(f"Appended one health row to {RUN_LOG}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Monitoring view — recent runs (trend match-rate / exception-rate over time)

# COMMAND ----------

spark.sql(f"SELECT * FROM {RUN_LOG} ORDER BY run_ts DESC LIMIT 20").show(truncate=False)

# COMMAND ----------

dbutils.notebook.exit(
    json.dumps(
        {
            "run_log_rows": spark.table(RUN_LOG).count(),
            "match_rate": match_rate,
            "exception_rate": exception_rate,
            "open_cases": int(open_cases),
        }
    )
)
