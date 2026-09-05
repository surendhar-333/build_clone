"""Tests for the reverse-ETL Gold -> Ops Console DuckDB loader (in-memory DuckDB only).

These exercise the pure DuckDB-side helpers; no Databricks connection or network is used
(``databricks.sql`` is imported lazily inside reverse_etl and never touched here).
"""

from __future__ import annotations

import decimal
import duckdb

from serving import reverse_etl


def _sample_row(case_id: str) -> tuple:
    """A well-formed 16-value Gold exception-case row."""
    return (
        f"KEY-{case_id}",
        case_id,
        f"TXN-{case_id}",
        "2026-06-30",
        "POS",
        "MISMATCH_AMOUNT",
        decimal.Decimal("100.00"),
        decimal.Decimal("99.50"),
        decimal.Decimal("0.50"),
        "SETTLED",
        "SETTLED",
        "AUTO",
        "amount differs",
        "OPEN",
        "2026-06-30 10:00:00",
        "2026-06-30 10:00:00",
    )


def _columns(conn: duckdb.DuckDBPyConnection) -> list[str]:
    rows = conn.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = 'gold_exception_cases' ORDER BY ordinal_position"
    ).fetchall()
    return [row[0] for row in rows]


def test_ensure_schema_creates_the_16_columns() -> None:
    conn = duckdb.connect(":memory:")
    reverse_etl.ensure_schema(conn)
    assert _columns(conn) == reverse_etl.COLUMNS


def test_load_cases_inserts_and_returns_count() -> None:
    conn = duckdb.connect(":memory:")
    reverse_etl.ensure_schema(conn)
    inserted = reverse_etl.load_cases(conn, [_sample_row(f"C{i}") for i in range(5)])
    assert inserted == 5
    assert conn.execute("SELECT COUNT(*) FROM gold_exception_cases").fetchone()[0] == 5


def test_load_cases_replaces_without_duplicates() -> None:
    conn = duckdb.connect(":memory:")
    reverse_etl.ensure_schema(conn)
    reverse_etl.load_cases(conn, [_sample_row(f"C{i}") for i in range(5)])
    reverse_etl.load_cases(conn, [_sample_row(f"C{i}") for i in range(3)])
    total = conn.execute("SELECT COUNT(*) FROM gold_exception_cases").fetchone()[0]
    distinct = conn.execute("SELECT COUNT(DISTINCT case_id) FROM gold_exception_cases").fetchone()[
        0
    ]
    assert total == 3
    assert distinct == 3


def test_load_cases_leaves_ops_case_state_untouched() -> None:
    conn = duckdb.connect(":memory:")
    reverse_etl.ensure_schema(conn)
    conn.execute("CREATE TABLE ops_case_state (case_id VARCHAR PRIMARY KEY, status VARCHAR)")
    conn.execute("INSERT INTO ops_case_state VALUES ('C0', 'CLOSED')")
    reverse_etl.load_cases(conn, [_sample_row("C0")])
    row = conn.execute("SELECT status FROM ops_case_state WHERE case_id = 'C0'").fetchone()
    assert row is not None
    assert row[0] == "CLOSED"
