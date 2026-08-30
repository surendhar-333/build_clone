# Databricks notebook source
# MAGIC %md
# MAGIC # Unity Catalog Governance
# MAGIC
# MAGIC Applies documentation, enforced quality constraints, informational keys,
# MAGIC and a generated data dictionary to the curated Silver and Gold layers.
# MAGIC Bronze tables and landing files are intentionally excluded because they
# MAGIC must retain dirty source records for audit and remediation.

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. Configuration

# COMMAND ----------

CATALOG = "workspace"
SCHEMA = "settlement_recon"

SILVER_INTERNAL = f"{CATALOG}.{SCHEMA}.silver_internal"
SILVER_NETWORK = f"{CATALOG}.{SCHEMA}.silver_network"
GOLD_RECON = f"{CATALOG}.{SCHEMA}.gold_recon_results"
GOLD_EXCEPTIONS = f"{CATALOG}.{SCHEMA}.gold_exception_cases"
DATA_DICTIONARY = f"{CATALOG}.{SCHEMA}.data_dictionary"

spark.sql(f"USE CATALOG {CATALOG}")
spark.sql(f"USE SCHEMA {SCHEMA}")

GOVERNED_TABLES = [
    SILVER_INTERNAL,
    SILVER_NETWORK,
    GOLD_RECON,
    GOLD_EXCEPTIONS,
]

print("Governance target:", f"{CATALOG}.{SCHEMA}")
for table_name in GOVERNED_TABLES:
    print("  ", table_name)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. Table and column documentation

# COMMAND ----------


def apply_ddl(description: str, statement: str) -> bool:
    """Apply one idempotent governance statement without stopping the notebook."""
    try:
        spark.sql(statement)
        print(f"applied: {description}")
        return True
    except Exception as error:
        print(f"skip (exists/unsupported): {description}: {error}")
        return False


table_comments = {
    SILVER_INTERNAL: (
        "Curated internal payment-ledger transactions after standardization, "
        "quality validation, and txn_id deduplication."
    ),
    SILVER_NETWORK: (
        "Curated payment-network transactions after standardization, quality "
        "validation, and txn_id deduplication."
    ),
    GOLD_RECON: (
        "Transaction-level reconciliation results comparing the internal ledger "
        "with the payment-network feed."
    ),
    GOLD_EXCEPTIONS: (
        "Persistent payment exception cases with stable business identity, "
        "disposition, and lifecycle status."
    ),
}

for table_name, table_comment in table_comments.items():
    escaped_comment = table_comment.replace("'", "''")
    apply_ddl(
        f"comment table {table_name}",
        f"COMMENT ON TABLE {table_name} IS '{escaped_comment}'",
    )

column_comments = {
    SILVER_INTERNAL: {
        "txn_id": "Stable transaction identifier and Silver business key.",
        "business_date": "Settlement business date represented by the transaction.",
        "amount": "Internal transaction amount stored as decimal currency.",
        "status": "Normalized internal payment status; may be null when unavailable.",
    },
    SILVER_NETWORK: {
        "txn_id": "Stable transaction identifier and Silver business key.",
        "business_date": "Settlement business date represented by the network record.",
        "amount": "Network-reported transaction amount stored as decimal currency.",
        "status": (
            "Normalized network payment status; null is retained when the network "
            "did not supply a status."
        ),
    },
    GOLD_RECON: {
        "txn_id": "Transaction identifier used to reconcile the two Silver feeds.",
        "business_date": "Coalesced settlement business date from the reconciled feeds.",
        "match_status": (
            "Reconciliation classification: matched, mismatched, or missing on one side."
        ),
        "amount_diff": "Signed difference calculated as internal amount minus network amount.",
        "disposition": "System disposition indicating AUTO or MANUAL handling.",
    },
    GOLD_EXCEPTIONS: {
        "case_key": "SHA-256 business identity derived from business date and transaction ID.",
        "case_id": "Readable stable exception identifier derived from case_key.",
        "business_date": "Settlement business date associated with the exception.",
        "amount_diff": "Signed internal-minus-network amount difference for the exception.",
        "disposition": "System recommendation indicating AUTO or MANUAL handling.",
        "status": (
            "Exception lifecycle status: OPEN, AUTO_RESOLVED, MANUAL_REVIEW, "
            "CLOSED, or CLOSED_DISAPPEARED."
        ),
    },
}

for table_name, comments_by_column in column_comments.items():
    for column_name, column_comment in comments_by_column.items():
        escaped_comment = column_comment.replace("'", "''")
        apply_ddl(
            f"comment {table_name}.{column_name}",
            f"""
            ALTER TABLE {table_name}
            ALTER COLUMN {column_name}
            COMMENT '{escaped_comment}'
            """,
        )

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. Enforced constraints and informational primary keys
# MAGIC
# MAGIC Each statement is isolated so an existing constraint, incompatible legacy
# MAGIC data, or a runtime limitation does not prevent the remaining governance
# MAGIC controls from being attempted.

# COMMAND ----------

constraint_statements = [
    # Silver internal: required business key/date/amount and positive money.
    (
        "silver_internal txn_id NOT NULL",
        f"ALTER TABLE {SILVER_INTERNAL} ALTER COLUMN txn_id SET NOT NULL",
    ),
    (
        "silver_internal business_date NOT NULL",
        f"ALTER TABLE {SILVER_INTERNAL} ALTER COLUMN business_date SET NOT NULL",
    ),
    (
        "silver_internal amount NOT NULL",
        f"ALTER TABLE {SILVER_INTERNAL} ALTER COLUMN amount SET NOT NULL",
    ),
    (
        "silver_internal positive amount",
        f"""
        ALTER TABLE {SILVER_INTERNAL}
        ADD CONSTRAINT chk_amount_pos CHECK (amount > 0)
        """,
    ),
    # Silver network: status intentionally remains nullable.
    (
        "silver_network txn_id NOT NULL",
        f"ALTER TABLE {SILVER_NETWORK} ALTER COLUMN txn_id SET NOT NULL",
    ),
    (
        "silver_network business_date NOT NULL",
        f"ALTER TABLE {SILVER_NETWORK} ALTER COLUMN business_date SET NOT NULL",
    ),
    (
        "silver_network amount NOT NULL",
        f"ALTER TABLE {SILVER_NETWORK} ALTER COLUMN amount SET NOT NULL",
    ),
    (
        "silver_network positive amount",
        f"""
        ALTER TABLE {SILVER_NETWORK}
        ADD CONSTRAINT chk_amount_pos CHECK (amount > 0)
        """,
    ),
    # Gold reconciliation outcome and disposition domains.
    (
        "gold_recon_results txn_id NOT NULL",
        f"ALTER TABLE {GOLD_RECON} ALTER COLUMN txn_id SET NOT NULL",
    ),
    (
        "gold_recon_results match_status NOT NULL",
        f"ALTER TABLE {GOLD_RECON} ALTER COLUMN match_status SET NOT NULL",
    ),
    (
        "gold_recon_results match_status domain",
        f"""
        ALTER TABLE {GOLD_RECON}
        ADD CONSTRAINT chk_match_status CHECK (
            match_status IN (
                'MATCHED',
                'MISMATCH_AMOUNT',
                'MISMATCH_STATUS',
                'MISMATCH_BOTH',
                'UNMATCHED_INTERNAL',
                'UNMATCHED_NETWORK'
            )
        )
        """,
    ),
    (
        "gold_recon_results disposition domain",
        f"""
        ALTER TABLE {GOLD_RECON}
        ADD CONSTRAINT chk_disp CHECK (
            disposition IN ('AUTO', 'MANUAL')
        )
        """,
    ),
    # Gold exception identity and lifecycle domains.
    (
        "gold_exception_cases case_key NOT NULL",
        f"ALTER TABLE {GOLD_EXCEPTIONS} ALTER COLUMN case_key SET NOT NULL",
    ),
    (
        "gold_exception_cases case_id NOT NULL",
        f"ALTER TABLE {GOLD_EXCEPTIONS} ALTER COLUMN case_id SET NOT NULL",
    ),
    (
        "gold_exception_cases lifecycle status domain",
        f"""
        ALTER TABLE {GOLD_EXCEPTIONS}
        ADD CONSTRAINT chk_case_status CHECK (
            status IN (
                'OPEN',
                'AUTO_RESOLVED',
                'MANUAL_REVIEW',
                'CLOSED',
                'CLOSED_DISAPPEARED'
            )
        )
        """,
    ),
    (
        "gold_exception_cases case_type domain",
        f"""
        ALTER TABLE {GOLD_EXCEPTIONS}
        ADD CONSTRAINT chk_case_type CHECK (
            case_type IN (
                'MISMATCH_AMOUNT',
                'MISMATCH_STATUS',
                'MISMATCH_BOTH',
                'UNMATCHED_INTERNAL',
                'UNMATCHED_NETWORK'
            )
        )
        """,
    ),
]

for description, statement in constraint_statements:
    apply_ddl(description, statement)

# Informational keys document uniqueness expectations for optimizers and users.
# They are deliberately NOT ENFORCED and do not use RELY.
informational_primary_keys = [
    (
        "silver_internal informational primary key",
        f"""
        ALTER TABLE {SILVER_INTERNAL}
        ADD CONSTRAINT pk_silver_internal
        PRIMARY KEY (txn_id) NOT ENFORCED
        """,
    ),
    (
        "silver_network informational primary key",
        f"""
        ALTER TABLE {SILVER_NETWORK}
        ADD CONSTRAINT pk_silver_network
        PRIMARY KEY (txn_id) NOT ENFORCED
        """,
    ),
    (
        "gold_exception_cases informational primary key",
        f"""
        ALTER TABLE {GOLD_EXCEPTIONS}
        ADD CONSTRAINT pk_gold_exception_cases
        PRIMARY KEY (case_key) NOT ENFORCED
        """,
    ),
]

for description, statement in informational_primary_keys:
    apply_ddl(description, statement)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4. Verify the reconciliation status CHECK constraint
# MAGIC
# MAGIC A schema-aware test row is constructed so the attempted insert satisfies
# MAGIC unrelated column types and specifically exercises the `match_status` domain.
# MAGIC If enforcement is unavailable, the test row is removed immediately.

# COMMAND ----------

from pyspark.sql.types import (
    BinaryType,
    BooleanType,
    ByteType,
    DateType,
    DecimalType,
    DoubleType,
    FloatType,
    IntegerType,
    LongType,
    ShortType,
    StringType,
    TimestampType,
)

TEST_TXN_ID = "__GOVERNANCE_CHECK_INVALID_MATCH_STATUS__"


def sql_literal_for_field(field) -> str:
    """Return a valid SQL expression for one Gold reconciliation test column."""
    name = field.name
    data_type = field.dataType

    overrides = {
        "txn_id": f"'{TEST_TXN_ID}'",
        "business_date": "DATE '2099-12-31'",
        "channel": "'TEST'",
        "internal_amount": "CAST(100.00 AS DECIMAL(18,2))",
        "network_amount": "CAST(100.00 AS DECIMAL(18,2))",
        "amount_diff": "CAST(0.00 AS DOUBLE)",
        "internal_status": "'SETTLED'",
        "network_status": "'SETTLED'",
        "match_status": "'BOGUS'",
        "reason": "'Governance CHECK verification row'",
        "disposition": "'MANUAL'",
    }

    if name in overrides:
        return overrides[name]

    if isinstance(data_type, StringType):
        return f"'TEST_{name.upper()}'"
    if isinstance(data_type, DateType):
        return "DATE '2099-12-31'"
    if isinstance(data_type, TimestampType):
        return "TIMESTAMP '2099-12-31 00:00:00'"
    if isinstance(data_type, BooleanType):
        return "FALSE"
    if isinstance(
        data_type,
        (ByteType, ShortType, IntegerType, LongType, FloatType, DoubleType),
    ):
        return "CAST(0 AS DOUBLE)"
    if isinstance(data_type, DecimalType):
        return f"CAST(0 AS DECIMAL({data_type.precision},{data_type.scale}))"
    if isinstance(data_type, BinaryType):
        return "CAST('TEST' AS BINARY)"

    # Nullable complex or unexpected columns are not part of the current Gold
    # contract. NULL keeps this verification resilient to additive evolution.
    return f"CAST(NULL AS {data_type.simpleString()})"


constraints_ok = False

try:
    gold_recon_schema = spark.table(GOLD_RECON).schema
    insert_columns = ", ".join(f"`{field.name}`" for field in gold_recon_schema.fields)
    insert_values = ", ".join(
        sql_literal_for_field(field) for field in gold_recon_schema.fields
    )

    spark.sql(
        f"""
        INSERT INTO {GOLD_RECON} ({insert_columns})
        SELECT {insert_values}
        """
    )
except Exception as error:
    constraints_ok = True
    print("CHECK enforced: bad insert rejected")
    print("  rejection:", error)
else:
    constraints_ok = False
    spark.sql(
        f"""
        DELETE FROM {GOLD_RECON}
        WHERE txn_id = '{TEST_TXN_ID}'
        """
    )
    print(
        "WARNING: invalid match_status was accepted; the test row was deleted. "
        "The CHECK constraint is not enforced or was not created."
    )

# COMMAND ----------

# MAGIC %md
# MAGIC ## 5. Publish the Unity Catalog data dictionary

# COMMAND ----------

dictionary_query = f"""
    SELECT
        table_name,
        column_name,
        data_type,
        comment
    FROM {CATALOG}.information_schema.columns
    WHERE table_schema = '{SCHEMA}'
      AND table_name IN (
          'silver_internal',
          'silver_network',
          'gold_recon_results',
          'gold_exception_cases'
      )
    ORDER BY table_name, ordinal_position
"""

dictionary_source = spark.sql(dictionary_query)
dictionary_rows = dictionary_source.collect()
dictionary_count = len(dictionary_rows)

# Materialize the small metadata result before overwriting the destination table.
# This avoids a source/target conflict if this governance notebook is rerun.
dictionary_df = spark.createDataFrame(
    dictionary_rows,
    schema=dictionary_source.schema,
)

display(dictionary_df)

(
    dictionary_df.write.format("delta")
    .mode("overwrite")
    .option("overwriteSchema", "true")
    .saveAsTable(DATA_DICTIONARY)
)

print(f"Wrote {dictionary_count} rows to {DATA_DICTIONARY}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Completion result

# COMMAND ----------

import json

dbutils.notebook.exit(
    json.dumps(
        {
            "constraints_ok": constraints_ok,
            "dictionary_rows": dictionary_count,
        }
    )
)
