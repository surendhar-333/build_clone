import pytest
from pyspark.sql import SparkSession
from pyspark.sql.types import StructType, StructField, StringType, DoubleType
from chispa.dataframe_comparer import assert_df_equality
from src.recon_logic import reconcile


@pytest.fixture(scope="session")
def spark():
    return SparkSession.builder.master("local[*]").appName("ReconLogicTests").getOrCreate()


def _schema_d():
    return StructType(
        [
            StructField("txn_id", StringType(), True),
            StructField("business_date", StringType(), True),
            StructField("channel", StringType(), True),
            StructField("amount", DoubleType(), True),
            StructField("status", StringType(), True),
        ]
    )


def test_reconcile_comprehensive(spark):
    schema_d = _schema_d()

    # We will define a comprehensive set of test cases encompassing all outcomes:
    # 1. Exact match (MATCHED)
    # 2. Amount mismatch within AMOUNT_TOLERANCE (MATCHED)
    # 3. Amount mismatch (AUTO) exactly 1.00 (MISMATCH_AMOUNT)
    # 4. Amount mismatch (AUTO) < 1.00 (MISMATCH_AMOUNT)
    # 5. Amount mismatch (MANUAL) > 1.00 (MISMATCH_AMOUNT)
    # 6. Status mismatch only (MISMATCH_STATUS)
    # 7. Status mismatch (NULL on network) (MISMATCH_STATUS)
    # 8. Status mismatch (NULL on both) (MISMATCH_STATUS)
    # 9. Mismatch both (MISMATCH_BOTH)
    # 10. Unmatched internal (UNMATCHED_INTERNAL)
    # 11. Unmatched network (UNMATCHED_NETWORK)

    internal_data = [
        ("1", "2023-01-01", "POS", 100.00, "SETTLED"),  # 1. Exact match
        ("2", "2023-01-01", "POS", 100.005, "SETTLED"),  # 2. Amount diff 0.005 -> MATCHED
        (
            "3",
            "2023-01-01",
            "POS",
            101.00,
            "SETTLED",
        ),  # 3. Amount diff 1.00 -> MISMATCH_AMOUNT (AUTO)
        (
            "4",
            "2023-01-01",
            "POS",
            100.50,
            "SETTLED",
        ),  # 4. Amount diff 0.50 -> MISMATCH_AMOUNT (AUTO)
        (
            "5",
            "2023-01-01",
            "POS",
            101.01,
            "SETTLED",
        ),  # 5. Amount diff 1.01 -> MISMATCH_AMOUNT (MANUAL)
        ("6", "2023-01-01", "POS", 100.00, "SETTLED"),  # 6. Status diff
        ("7", "2023-01-01", "POS", 100.00, "SETTLED"),  # 7. Status diff (NULL network)
        ("8", "2023-01-01", "POS", 100.00, None),  # 8. Status diff (NULL both)
        ("9", "2023-01-01", "POS", 105.00, "SETTLED"),  # 9. MISMATCH_BOTH
        ("10", "2023-01-01", "POS", 100.00, "SETTLED"),  # 10. UNMATCHED_INTERNAL
        # "11" missing
    ]

    network_data = [
        ("1", "2023-01-01", "POS", 100.00, "SETTLED"),
        ("2", "2023-01-01", "POS", 100.00, "SETTLED"),
        ("3", "2023-01-01", "POS", 100.00, "SETTLED"),
        ("4", "2023-01-01", "POS", 100.00, "SETTLED"),
        ("5", "2023-01-01", "POS", 100.00, "SETTLED"),
        ("6", "2023-01-01", "POS", 100.00, "PENDING"),
        ("7", "2023-01-01", "POS", 100.00, None),
        ("8", "2023-01-01", "POS", 100.00, None),
        ("9", "2023-01-01", "POS", 100.00, "PENDING"),
        # "10" missing
        ("11", "2023-01-01", "POS", 100.00, "SETTLED"),
    ]

    internal_df = spark.createDataFrame(internal_data, schema=schema_d)
    network_df = spark.createDataFrame(network_data, schema=schema_d)

    result = reconcile(internal_df, network_df)

    # We only care about verifying match_status, amount_diff, disposition, and reason structurally
    # To use assert_df_equality, we create expected df.

    expected_data = [
        (
            "1",
            "2023-01-01",
            "POS",
            100.00,
            100.00,
            0.0,
            "SETTLED",
            "SETTLED",
            "MATCHED",
            "Amount and status agree within tolerance",
            "MANUAL",
        ),
        (
            "10",
            "2023-01-01",
            "POS",
            100.00,
            None,
            None,
            "SETTLED",
            None,
            "UNMATCHED_INTERNAL",
            "Transaction present in internal ledger but missing from network feed",
            "MANUAL",
        ),
        (
            "11",
            "2023-01-01",
            "POS",
            None,
            100.00,
            None,
            None,
            "SETTLED",
            "UNMATCHED_NETWORK",
            "Transaction present in network feed but missing from internal ledger",
            "MANUAL",
        ),
        (
            "2",
            "2023-01-01",
            "POS",
            100.005,
            100.00,
            0.0,
            "SETTLED",
            "SETTLED",
            "MATCHED",
            "Amount and status agree within tolerance",
            "MANUAL",
        ),
        (
            "3",
            "2023-01-01",
            "POS",
            101.00,
            100.00,
            1.0,
            "SETTLED",
            "SETTLED",
            "MISMATCH_AMOUNT",
            "Amount differs by 1.0 (tolerance 0.01)",
            "AUTO",
        ),
        (
            "4",
            "2023-01-01",
            "POS",
            100.50,
            100.00,
            0.5,
            "SETTLED",
            "SETTLED",
            "MISMATCH_AMOUNT",
            "Amount differs by 0.5 (tolerance 0.01)",
            "AUTO",
        ),
        (
            "5",
            "2023-01-01",
            "POS",
            101.01,
            100.00,
            1.01,
            "SETTLED",
            "SETTLED",
            "MISMATCH_AMOUNT",
            "Amount differs by 1.01 (tolerance 0.01)",
            "MANUAL",
        ),
        (
            "6",
            "2023-01-01",
            "POS",
            100.00,
            100.00,
            0.0,
            "SETTLED",
            "PENDING",
            "MISMATCH_STATUS",
            "Status differs (SETTLED vs PENDING)",
            "MANUAL",
        ),
        (
            "7",
            "2023-01-01",
            "POS",
            100.00,
            100.00,
            0.0,
            "SETTLED",
            None,
            "MISMATCH_STATUS",
            None,
            "MANUAL",
        ),
        (
            "8",
            "2023-01-01",
            "POS",
            100.00,
            100.00,
            0.0,
            None,
            None,
            "MISMATCH_STATUS",
            None,
            "MANUAL",
        ),
        (
            "9",
            "2023-01-01",
            "POS",
            105.00,
            100.00,
            5.0,
            "SETTLED",
            "PENDING",
            "MISMATCH_BOTH",
            "Amount differs by 5.0 and status differs (SETTLED vs PENDING)",
            "MANUAL",
        ),
    ]

    expected_schema = StructType(
        [
            StructField("txn_id", StringType(), True),
            StructField("business_date", StringType(), True),
            StructField("channel", StringType(), True),
            StructField("internal_amount", DoubleType(), True),
            StructField("network_amount", DoubleType(), True),
            StructField("amount_diff", DoubleType(), True),
            StructField("internal_status", StringType(), True),
            StructField("network_status", StringType(), True),
            StructField("match_status", StringType(), True),
            StructField("reason", StringType(), True),
            StructField("disposition", StringType(), True),
        ]
    )

    expected_df = spark.createDataFrame(expected_data, schema=expected_schema)

    # Sort both just in case before assert
    result_sorted = result.orderBy("txn_id")
    expected_sorted = expected_df.orderBy("txn_id")

    assert_df_equality(result_sorted, expected_sorted, ignore_nullable=True)
