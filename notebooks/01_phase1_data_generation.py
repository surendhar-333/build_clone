# Databricks notebook source
# MAGIC %md
# MAGIC # Phase 1: Data Generation

# COMMAND ----------
import json
from datetime import datetime, timezone

from pyspark.sql import functions as F
from pyspark.sql.types import DecimalType

# COMMAND ----------
# MAGIC %sql
# MAGIC CREATE CATALOG IF NOT EXISTS workspace;
# MAGIC CREATE SCHEMA IF NOT EXISTS workspace.settlement_recon;
# MAGIC CREATE VOLUME IF NOT EXISTS workspace.settlement_recon.landing;

# COMMAND ----------
# Configure Widgets
dbutils.widgets.text("business_date", "2024-01-01", "Business Date (YYYY-MM-DD)")
dbutils.widgets.text("rows", "100000", "Number of base rows")
dbutils.widgets.text("drop_rate", "0.01", "Drop Rate")
dbutils.widgets.text("amount_auto_rate", "0.01", "Amount Auto Rate")
dbutils.widgets.text("amount_manual_rate", "0.01", "Amount Manual Rate")
dbutils.widgets.text("status_rate", "0.01", "Status Rate")
dbutils.widgets.text("both_rate", "0.01", "Both Mismatch Rate")
dbutils.widgets.text("null_status_rate", "0.005", "Null Status Rate")
dbutils.widgets.text("phantom_rate", "0.01", "Phantom Rows Rate")

business_date = dbutils.widgets.get("business_date")
num_rows = int(dbutils.widgets.get("rows"))
drop_rate = float(dbutils.widgets.get("drop_rate"))
amount_auto_rate = float(dbutils.widgets.get("amount_auto_rate"))
amount_manual_rate = float(dbutils.widgets.get("amount_manual_rate"))
status_rate = float(dbutils.widgets.get("status_rate"))
both_rate = float(dbutils.widgets.get("both_rate"))
null_status_rate = float(dbutils.widgets.get("null_status_rate"))
phantom_rate = float(dbutils.widgets.get("phantom_rate"))

# COMMAND ----------
# Calculate band thresholds
total_rate = (
    drop_rate + amount_auto_rate + amount_manual_rate + status_rate + both_rate + null_status_rate
)

if total_rate > 1.0:
    raise ValueError(f"Total corruption rate {total_rate} exceeds 1.0")

# Modulo base for assigning disjoint bands
mod_base = 100000

drop_threshold = int(drop_rate * mod_base)
amount_auto_threshold = drop_threshold + int(amount_auto_rate * mod_base)
amount_manual_threshold = amount_auto_threshold + int(amount_manual_rate * mod_base)
status_threshold = amount_manual_threshold + int(status_rate * mod_base)
both_threshold = status_threshold + int(both_rate * mod_base)
null_status_threshold = both_threshold + int(null_status_rate * mod_base)

# Ensure disjoint thresholds:
# 0 <= x < drop_threshold : drop
# drop_threshold <= x < amount_auto_threshold : amount auto
# ...

# COMMAND ----------
# Generate base internal dataframe
base_df = spark.range(0, num_rows).withColumn("mod_id", F.col("id") % mod_base)

internal_df = (
    base_df.withColumn("txn_id", F.concat(F.lit("TXN-"), F.col("id").cast("string")))
    .withColumn("business_date", F.to_date(F.lit(business_date)))
    .withColumn(
        "channel",
        F.when(F.col("id") % 3 == 0, F.lit("WEB"))
        .when(F.col("id") % 3 == 1, F.lit("MOBILE"))
        .otherwise(F.lit("POS")),
    )
    .withColumn(
        "amount",
        (F.lit(10.0) + (F.col("id") % 1000).cast("double") * 0.53).cast(DecimalType(18, 2)),
    )
    .withColumn("currency", F.lit("INR"))
    .withColumn(
        "status",
        F.when(F.col("id") % 5 == 0, F.lit("PENDING"))
        .when(F.col("id") % 5 == 1, F.lit("FAILED"))
        .otherwise(F.lit("SETTLED")),
    )
    .withColumn("account_id", F.concat(F.lit("ACC-"), (F.col("id") % 100).cast("string")))
    .withColumn(
        "txn_ts",
        F.to_timestamp(
            F.concat(
                F.lit(business_date),
                F.lit(" "),
                F.lpad((F.col("id") % 24).cast("string"), 2, "0"),
                F.lit(":15:00"),
            )
        ),
    )
)

internal_out_df = internal_df.drop("id", "mod_id")

# COMMAND ----------
# Generate network dataframe from base
# Apply drop condition
network_base_df = internal_df.filter(~(F.col("mod_id") < drop_threshold))

# Add network_ref
network_df = network_base_df.withColumn(
    "network_ref", F.concat(F.lit("NET-"), F.col("id").cast("string"))
)

# Apply corruptions
# amount_auto_rate: diff > 0.01 and <= 1.00 (e.g., +0.50)
# amount_manual_rate: diff > 1.00 (e.g., +5.00)
network_df = network_df.withColumn(
    "amount",
    F.when(
        (F.col("mod_id") >= drop_threshold) & (F.col("mod_id") < amount_auto_threshold),
        F.col("amount") + F.lit(0.50),
    )
    .when(
        (F.col("mod_id") >= amount_auto_threshold) & (F.col("mod_id") < amount_manual_threshold),
        F.col("amount") + F.lit(5.00),
    )
    .when(
        (F.col("mod_id") >= status_threshold) & (F.col("mod_id") < both_threshold),
        F.col("amount") + F.lit(5.00),
    )
    .otherwise(F.col("amount")),
)

# Status corruptions
# both: status mismatch + amount mismatch
# status: just status mismatch
# Change status to something different deterministically
network_df = network_df.withColumn(
    "status",
    F.when(
        (F.col("mod_id") >= amount_manual_threshold) & (F.col("mod_id") < status_threshold),
        F.when(F.col("status") == "SETTLED", F.lit("PENDING")).otherwise(F.lit("SETTLED")),
    )
    .when(
        (F.col("mod_id") >= status_threshold) & (F.col("mod_id") < both_threshold),
        F.when(F.col("status") == "SETTLED", F.lit("FAILED")).otherwise(F.lit("SETTLED")),
    )
    .when(
        (F.col("mod_id") >= both_threshold) & (F.col("mod_id") < null_status_threshold),
        F.lit(None).cast("string"),
    )
    .otherwise(F.col("status")),
)

# Generate phantom network rows
num_phantom = int(num_rows * phantom_rate)
phantom_df = None
if num_phantom > 0:
    phantom_base = spark.range(0, num_phantom)
    phantom_df = (
        phantom_base.withColumn("txn_id", F.concat(F.lit("TXNP-"), F.col("id").cast("string")))
        .withColumn("business_date", F.to_date(F.lit(business_date)))
        .withColumn("channel", F.lit("WEB"))
        .withColumn(
            "amount",
            (F.lit(25.0) + (F.col("id") % 100).cast("double") * 0.1).cast(DecimalType(18, 2)),
        )
        .withColumn("currency", F.lit("INR"))
        .withColumn("status", F.lit("SETTLED"))
        .withColumn("account_id", F.concat(F.lit("ACC-"), (F.col("id") % 50).cast("string")))
        .withColumn(
            "txn_ts",
            F.to_timestamp(F.concat(F.lit(business_date), F.lit(" 12:00:00"))),
        )
        .withColumn("network_ref", F.concat(F.lit("NETP-"), F.col("id").cast("string")))
    )
    phantom_df = phantom_df.drop("id")

network_out_df = network_df.drop("id", "mod_id")

if phantom_df is not None:
    network_out_df = network_out_df.unionByName(phantom_df)


# COMMAND ----------
# Write Output Paths
batch_ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

internal_path = f"/Volumes/workspace/settlement_recon/landing/internal/business_date={business_date}/batch={batch_ts}"
network_path = f"/Volumes/workspace/settlement_recon/landing/network/business_date={business_date}/batch={batch_ts}"

# Write internal data
(internal_out_df.write.format("csv").option("header", "true").mode("append").save(internal_path))

# Write network data
(network_out_df.write.format("csv").option("header", "true").mode("append").save(network_path))

# COMMAND ----------
# Output final result
internal_count = internal_out_df.count()
network_count = network_out_df.count()

result = {
    "status": "SUCCESS",
    "business_date": business_date,
    "internal_count": internal_count,
    "network_count": network_count,
    "internal_path": internal_path,
    "network_path": network_path,
}

dbutils.notebook.exit(json.dumps(result))
