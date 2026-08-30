# Databricks notebook source
# MAGIC %md
# MAGIC # Phase 1 — Synthetic Data Generation & Landing
# MAGIC
# MAGIC **Project:** Payment Settlement & Reconciliation Lakehouse
# MAGIC
# MAGIC Generates two sides of a payment dataset and lands them as **append-only batch files** in a Unity
# MAGIC Catalog volume:
# MAGIC - **internal** — our source-of-truth debit records (the system of record)
# MAGIC - **network** — the bank/network side, derived from internal with deliberate, deterministic
# MAGIC   discrepancies injected across disjoint bands, plus network-only "phantom" rows.
# MAGIC
# MAGIC Every internal row is assigned to exactly one band (DROP / AMOUNT_MANUAL / AMOUNT_AUTO / STATUS /
# MAGIC BOTH / NULL_STATUS / CLEAN) so the reconciliation engine can exercise **all six match outcomes and
# MAGIC both AUTO and MANUAL dispositions**. Built on plain PySpark `spark.range()` so it scales from 100K
# MAGIC to 10M+ rows with zero external dependencies.

# COMMAND ----------

# MAGIC %md
# MAGIC ## Cell 1 — Config (catalog / schema / volume)
# MAGIC
# MAGIC On Free Edition the default catalog is usually `workspace`. If `CREATE CATALOG` fails with a
# MAGIC permissions error, set `CATALOG` to a catalog you can already see in the Catalog browser and re-run.

# COMMAND ----------

# ---- Configuration ---------------------------------------------------------
CATALOG = "workspace"            # Free Edition default; change if you lack CREATE CATALOG rights
SCHEMA = "settlement_recon"
VOLUME = "landing"

# Volume landing paths (Bronze will read from these in Phase 2)
VOLUME_ROOT = f"/Volumes/{CATALOG}/{SCHEMA}/{VOLUME}"
INTERNAL_PATH = f"{VOLUME_ROOT}/internal"
NETWORK_PATH = f"{VOLUME_ROOT}/network"

# Row count is driven by Phase 6's scale lab via the `rows` widget (default 100,000).
dbutils.widgets.text("rows", "100000")
ROWS_INTERNAL = int(dbutils.widgets.get("rows"))

CHANNELS = ["ATM", "POS", "ECOM", "WALLET", "IMPS"]
STATUSES = ["SETTLED", "PENDING", "FAILED", "REVERSED"]

# Create catalog / schema / volume (idempotent)
spark.sql(f"CREATE CATALOG IF NOT EXISTS {CATALOG}")
spark.sql(f"CREATE SCHEMA IF NOT EXISTS {CATALOG}.{SCHEMA}")
spark.sql(f"CREATE VOLUME IF NOT EXISTS {CATALOG}.{SCHEMA}.{VOLUME}")

print("Config ready.")
print("  Internal landing:", INTERNAL_PATH)
print("  Network  landing:", NETWORK_PATH)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Cell 2 — Generator functions & corruption bands
# MAGIC
# MAGIC `build_internal()` creates the source of truth. `assign_injection_band()` deterministically assigns
# MAGIC every internal row to exactly one disjoint corruption band. `build_network()` applies those bands to
# MAGIC derive the network side and appends network-only phantom rows (UNMATCHED_NETWORK). All corruption
# MAGIC rates are widgets so the mix is tunable and reproducible.

# COMMAND ----------

from pyspark.sql import DataFrame
from pyspark.sql import functions as F


# Runtime parameters ----------------------------------------------------------
dbutils.widgets.text("business_date", "2026-06-30")
dbutils.widgets.text("drop", "0.05")
dbutils.widgets.text("amount_manual", "0.02")
dbutils.widgets.text("amount_auto", "0.01")
dbutils.widgets.text("status", "0.02")
dbutils.widgets.text("both", "0.01")
dbutils.widgets.text("null_status", "0.005")
dbutils.widgets.text("phantom", "0.005")

BUSINESS_DATE = dbutils.widgets.get("business_date").strip()

DROP_RATE = float(dbutils.widgets.get("drop"))
AMOUNT_MANUAL_RATE = float(dbutils.widgets.get("amount_manual"))
AMOUNT_AUTO_RATE = float(dbutils.widgets.get("amount_auto"))
STATUS_RATE = float(dbutils.widgets.get("status"))
BOTH_RATE = float(dbutils.widgets.get("both"))
NULL_STATUS_RATE = float(dbutils.widgets.get("null_status"))
PHANTOM_RATE = float(dbutils.widgets.get("phantom"))

DERIVED_RATES = [
    DROP_RATE,
    AMOUNT_MANUAL_RATE,
    AMOUNT_AUTO_RATE,
    STATUS_RATE,
    BOTH_RATE,
    NULL_STATUS_RATE,
]

if not BUSINESS_DATE:
    raise ValueError("business_date must not be empty")

if any(rate < 0.0 or rate >= 1.0 for rate in DERIVED_RATES + [PHANTOM_RATE]):
    raise ValueError("Every corruption rate must be in the range [0.0, 1.0)")

if sum(DERIVED_RATES) >= 1.0:
    raise ValueError("Derived-row corruption rates must sum to less than 1.0")

DROP_END = DROP_RATE
AMOUNT_MANUAL_END = DROP_END + AMOUNT_MANUAL_RATE
AMOUNT_AUTO_END = AMOUNT_MANUAL_END + AMOUNT_AUTO_RATE
STATUS_END = AMOUNT_AUTO_END + STATUS_RATE
BOTH_END = STATUS_END + BOTH_RATE
NULL_STATUS_END = BOTH_END + NULL_STATUS_RATE

print("Generator parameters:")
print("  business_date :", BUSINESS_DATE)
print("  drop          :", DROP_RATE)
print("  amount_manual :", AMOUNT_MANUAL_RATE)
print("  amount_auto   :", AMOUNT_AUTO_RATE)
print("  status        :", STATUS_RATE)
print("  both          :", BOTH_RATE)
print("  null_status   :", NULL_STATUS_RATE)
print("  phantom       :", PHANTOM_RATE)


def build_internal(n_rows: int, business_date: str) -> DataFrame:
    """Create deterministic internal payment records using distributed Spark expressions."""
    base = spark.range(0, n_rows).withColumnRenamed("id", "seq")

    txn_id = F.concat(
        F.lit("TXN"),
        F.lpad(F.col("seq").cast("string"), 12, "0"),
    )

    # Separate salted hashes prevent accidental relationships between fields.
    amount_hash = F.pmod(F.hash(F.col("seq"), F.lit(11)), F.lit(499000))
    status_hash = F.pmod(F.hash(F.col("seq"), F.lit(13)), F.lit(len(STATUSES)))
    account_hash = F.pmod(F.hash(F.col("seq"), F.lit(17)), F.lit(50000))
    channel_hash = F.pmod(F.hash(F.col("seq"), F.lit(19)), F.lit(len(CHANNELS)))

    return base.select(
        txn_id.alias("txn_id"),
        F.lit(business_date).cast("date").alias("business_date"),
        F.element_at(
            F.array(*[F.lit(channel) for channel in CHANNELS]),
            (channel_hash + 1).cast("int"),
        ).alias("channel"),
        F.round(amount_hash / F.lit(100.0) + F.lit(10.0), 2).alias("amount"),
        F.lit("INR").alias("currency"),
        F.element_at(
            F.array(*[F.lit(status) for status in STATUSES]),
            (status_hash + 1).cast("int"),
        ).alias("status"),
        F.concat(
            F.lit("ACCT"),
            F.lpad(account_hash.cast("string"), 8, "0"),
        ).alias("account_id"),
        (
            F.unix_timestamp(F.lit(business_date), "yyyy-MM-dd")
            + F.pmod(F.col("seq"), F.lit(86400))
        ).cast("timestamp").alias("txn_ts"),
    )


def assign_injection_band(internal_df: DataFrame) -> DataFrame:
    """Assign every internal row to exactly one deterministic, disjoint band."""
    r = (
        F.abs(F.hash(F.col("txn_id"), F.lit(7))) % F.lit(100000)
    ) / F.lit(100000.0)

    return (
        internal_df
        .withColumn("_r", r)
        .withColumn(
            "_injection_band",
            F.when(F.col("_r") < F.lit(DROP_END), F.lit("DROP"))
            .when(
                F.col("_r") < F.lit(AMOUNT_MANUAL_END),
                F.lit("AMOUNT_MANUAL"),
            )
            .when(
                F.col("_r") < F.lit(AMOUNT_AUTO_END),
                F.lit("AMOUNT_AUTO"),
            )
            .when(F.col("_r") < F.lit(STATUS_END), F.lit("STATUS"))
            .when(F.col("_r") < F.lit(BOTH_END), F.lit("BOTH"))
            .when(
                F.col("_r") < F.lit(NULL_STATUS_END),
                F.lit("NULL_STATUS"),
            )
            .otherwise(F.lit("CLEAN")),
        )
        .drop("_r")
    )


def build_network(internal_df: DataFrame) -> DataFrame:
    """Build derived network rows and append deterministic network-only phantoms."""
    banded = assign_injection_band(internal_df)

    # Rotating through the configured values guarantees a different valid status.
    status_rotation = F.create_map(
        F.lit("SETTLED"), F.lit("PENDING"),
        F.lit("PENDING"), F.lit("FAILED"),
        F.lit("FAILED"), F.lit("REVERSED"),
        F.lit("REVERSED"), F.lit("SETTLED"),
    )

    # The greatest() safeguard ensures the manual difference is strictly above
    # Rs 1.00 even for the smallest generated amount.
    manual_amount = F.round(
        F.greatest(
            F.col("amount") * F.lit(1.10),
            F.col("amount") + F.lit(1.01),
        ),
        2,
    )

    derived = (
        banded
        .filter(F.col("_injection_band") != "DROP")
        .withColumn(
            "amount",
            F.when(
                F.col("_injection_band").isin("AMOUNT_MANUAL", "BOTH"),
                manual_amount,
            )
            .when(
                F.col("_injection_band") == "AMOUNT_AUTO",
                F.round(F.col("amount") + F.lit(0.50), 2),
            )
            .otherwise(F.col("amount")),
        )
        .withColumn(
            "status",
            F.when(
                F.col("_injection_band").isin("STATUS", "BOTH"),
                F.element_at(status_rotation, F.col("status")),
            )
            .when(
                F.col("_injection_band") == "NULL_STATUS",
                F.lit(None).cast("string"),
            )
            .otherwise(F.col("status")),
        )
        .withColumn(
            "network_ref",
            F.concat(
                F.lit("NET"),
                F.substring(F.col("txn_id"), 4, 12),
            ),
        )
    )

    phantom_count = int(ROWS_INTERNAL * PHANTOM_RATE)
    phantom_base = spark.range(0, phantom_count).withColumnRenamed("id", "seq")

    phantom_amount_hash = F.pmod(
        F.hash(F.col("seq"), F.lit(31)),
        F.lit(499000),
    )
    phantom_channel_hash = F.pmod(
        F.hash(F.col("seq"), F.lit(37)),
        F.lit(len(CHANNELS)),
    )
    phantom_status_hash = F.pmod(
        F.hash(F.col("seq"), F.lit(41)),
        F.lit(len(STATUSES)),
    )
    phantom_account_hash = F.pmod(
        F.hash(F.col("seq"), F.lit(43)),
        F.lit(50000),
    )

    phantom_txn_id = F.concat(
        F.lit("TXNP"),
        F.lpad(F.col("seq").cast("string"), 12, "0"),
    )

    phantoms = phantom_base.select(
        phantom_txn_id.alias("txn_id"),
        F.lit(BUSINESS_DATE).cast("date").alias("business_date"),
        F.element_at(
            F.array(*[F.lit(channel) for channel in CHANNELS]),
            (phantom_channel_hash + 1).cast("int"),
        ).alias("channel"),
        F.round(
            phantom_amount_hash / F.lit(100.0) + F.lit(10.0),
            2,
        ).alias("amount"),
        F.lit("INR").alias("currency"),
        F.element_at(
            F.array(*[F.lit(status) for status in STATUSES]),
            (phantom_status_hash + 1).cast("int"),
        ).alias("status"),
        F.concat(
            F.lit("ACCTP"),
            F.lpad(phantom_account_hash.cast("string"), 8, "0"),
        ).alias("account_id"),
        (
            F.unix_timestamp(F.lit(BUSINESS_DATE), "yyyy-MM-dd")
            + F.pmod(F.col("seq"), F.lit(86400))
        ).cast("timestamp").alias("txn_ts"),
        F.concat(
            F.lit("NET"),
            F.substring(phantom_txn_id, 4, 12),
        ).alias("network_ref"),
        F.lit("PHANTOM").alias("_injection_band"),
    )

    return derived.select(
        "txn_id",
        "business_date",
        "channel",
        "amount",
        "currency",
        "status",
        "account_id",
        "txn_ts",
        "network_ref",
        "_injection_band",
    ).unionByName(phantoms)


print("Generator functions defined: build_internal(), assign_injection_band(), build_network()")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Cell 3 — Generate one business day and append a unique landing batch
# MAGIC
# MAGIC Removes `coalesce(1)` and writes **append-only** into a unique `batch=<timestamp>` subfolder so
# MAGIC repeated and daily runs accumulate instead of overwriting. Prints per-band counts and the expected
# MAGIC reconciliation coverage for verification.

# COMMAND ----------

from datetime import datetime, timezone


batch_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")

internal_batch_path = (
    f"{INTERNAL_PATH}/business_date={BUSINESS_DATE}/batch={batch_id}/"
)
network_batch_path = (
    f"{NETWORK_PATH}/business_date={BUSINESS_DATE}/batch={batch_id}/"
)

# NOTE: serverless compute does not support .cache()/.persist(). The generator is
# fully deterministic, so recomputation across actions yields identical data.
internal_df = build_internal(ROWS_INTERNAL, BUSINESS_DATE)
internal_count = internal_df.count()

source_band_rows = (
    assign_injection_band(internal_df)
    .groupBy("_injection_band")
    .count()
    .collect()
)
source_band_counts = {
    row["_injection_band"]: row["count"]
    for row in source_band_rows
}

network_tagged_df = build_network(internal_df)

network_band_rows = (
    network_tagged_df
    .groupBy("_injection_band")
    .count()
    .collect()
)
network_band_counts = {
    row["_injection_band"]: row["count"]
    for row in network_band_rows
}

network_count = sum(network_band_counts.values())

# Remove the verification-only band column before landing so the existing CSV
# and Bronze/Silver column contracts remain unchanged.
network_df = network_tagged_df.drop("_injection_band")

(
    internal_df.write
    .mode("append")
    .option("header", "true")
    .csv(internal_batch_path)
)

(
    network_df.write
    .mode("append")
    .option("header", "true")
    .csv(network_batch_path)
)

print("Landing batch complete.")
print("  batch_id      :", batch_id)
print("  internal path :", internal_batch_path)
print("  network path  :", network_batch_path)
print(f"  internal rows : {internal_count:,}")
print(f"  network rows  : {network_count:,}")

print("\n=== Deterministic injection-band counts ===")
for band in [
    "DROP",
    "AMOUNT_MANUAL",
    "AMOUNT_AUTO",
    "STATUS",
    "BOTH",
    "NULL_STATUS",
    "CLEAN",
]:
    print(f"{band:<15} {source_band_counts.get(band, 0):>12,}")

print(f"{'PHANTOM':<15} {network_band_counts.get('PHANTOM', 0):>12,}")

print("\n=== Expected reconciliation coverage ===")
print(
    "UNMATCHED_INTERNAL :",
    f"{source_band_counts.get('DROP', 0):,}",
)
print(
    "MISMATCH_AMOUNT / MANUAL :",
    f"{source_band_counts.get('AMOUNT_MANUAL', 0):,}",
)
print(
    "MISMATCH_AMOUNT / AUTO   :",
    f"{source_band_counts.get('AMOUNT_AUTO', 0):,}",
)
print(
    "MISMATCH_STATUS :",
    f"{source_band_counts.get('STATUS', 0) + source_band_counts.get('NULL_STATUS', 0):,}",
)
print(
    "MISMATCH_BOTH   :",
    f"{source_band_counts.get('BOTH', 0):,}",
)
print(
    "MATCHED         :",
    f"{source_band_counts.get('CLEAN', 0):,}",
)
print(
    "UNMATCHED_NETWORK:",
    f"{network_band_counts.get('PHANTOM', 0):,}",
)

print("\n=== INTERNAL SAMPLE ===")
internal_df.show(5, truncate=False)

print("=== NETWORK SAMPLE ===")
network_df.show(5, truncate=False)

# Return a machine-readable run summary (also consumable by Phase 6 orchestration).
import json

dbutils.notebook.exit(
    json.dumps(
        {
            "batch_id": batch_id,
            "rows_internal": internal_count,
            "rows_network": network_count,
            "source_bands": source_band_counts,
            "network_bands": network_band_counts,
        }
    )
)

# COMMAND ----------

# MAGIC %md
# MAGIC ✅ **Phase 1 done when:** the per-band counts print non-zero rows for all seven bands (incl.
# MAGIC AMOUNT_AUTO, BOTH, NULL_STATUS and PHANTOM), so Gold can later show every match_status and both
# MAGIC dispositions. Next: **Phase 2 — Bronze with Auto Loader** reads the batch subfolders under
# MAGIC internal/ and network/ into `bronze_internal` / `bronze_network` with checkpointing.
