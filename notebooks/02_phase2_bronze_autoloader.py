# Databricks notebook source
# MAGIC %md
# MAGIC # Phase 2: Bronze Auto Loader

# COMMAND ----------
import json
from pyspark.sql import functions as F

CATALOG = "workspace"
SCHEMA = "settlement_recon"
VOLUME = "landing"

VOLUME_PATH = f"/Volumes/{CATALOG}/{SCHEMA}/{VOLUME}"

def ingest_bronze(source_path, table_name, label):
    schema_loc = f"{VOLUME_PATH}/_schemas/{label}"
    checkpoint_loc = f"{VOLUME_PATH}/_checkpoints/{label}"

    # Read stream using Auto Loader
    df = (
        spark.readStream.format("cloudFiles")
        .option("cloudFiles.format", "csv")
        .option("cloudFiles.schemaLocation", schema_loc)
        .option("cloudFiles.inferColumnTypes", "true")
        .option("cloudFiles.schemaEvolutionMode", "addNewColumns")
        .option("rescuedDataColumn", "_rescued_data")
        .option("header", "true")
        .load(source_path)
    )

    # Add audit columns
    df = df.withColumn("_ingest_ts", F.current_timestamp()) \
           .withColumn("_source_file", F.col("_metadata.file_path"))

    # Write stream to Delta table
    (
        df.writeStream
        .option("mergeSchema", "true")
        .option("checkpointLocation", checkpoint_loc)
        .trigger(availableNow=True)
        .toTable(table_name)
        .awaitTermination()
    )

# COMMAND ----------

# Paths for internal and network data
internal_source = f"{VOLUME_PATH}/internal/"
network_source = f"{VOLUME_PATH}/network/"

# Table names
internal_table = f"{CATALOG}.{SCHEMA}.bronze_internal"
network_table = f"{CATALOG}.{SCHEMA}.bronze_network"

# COMMAND ----------

# Ingest internal data
ingest_bronze(
    source_path=internal_source,
    table_name=internal_table,
    label="internal"
)

# Ingest network data
ingest_bronze(
    source_path=network_source,
    table_name=network_table,
    label="network"
)

# COMMAND ----------

result = {
    "status": "SUCCESS",
    "tables": [internal_table, network_table]
}

dbutils.notebook.exit(json.dumps(result))
