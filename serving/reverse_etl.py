"""Reverse-ETL Gold settlement exception cases into the Ops Console DuckDB."""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any, Protocol

COLUMNS = [
    "case_key",
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
    "status",
    "first_seen_ts",
    "last_updated_ts",
]

DEFAULT_GOLD_TABLE = "workspace.settlement_recon.gold_exception_cases"
DEFAULT_DUCKDB_PATH = "serving/ops_demo.duckdb"

_GOLD_TABLE_PATTERN = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_$]*"
    r"(?:\.[A-Za-z_][A-Za-z0-9_$]*){0,2}$"
)

LOGGER = logging.getLogger(__name__)


class DuckDBConnection(Protocol):
    """Minimal DuckDB connection interface used by this module."""

    def execute(
        self,
        query: str,
        parameters: Sequence[Any] | None = None,
    ) -> Any:
        """Execute a SQL statement."""

    def executemany(
        self,
        query: str,
        parameters: Iterable[Sequence[Any]],
    ) -> Any:
        """Execute a parameterized SQL statement for multiple rows."""


class DatabricksCursor(Protocol):
    """Minimal Databricks SQL cursor interface used by this module."""

    def execute(self, query: str) -> Any:
        """Execute a SQL statement."""

    def fetchall(self) -> list[Sequence[Any]]:
        """Return all query rows."""

    def close(self) -> None:
        """Close the cursor."""


class DatabricksConnection(Protocol):
    """Minimal Databricks SQL connection interface used by this module."""

    def cursor(self) -> DatabricksCursor:
        """Create a cursor."""

    def close(self) -> None:
        """Close the connection."""


def configure_logging() -> None:
    """Configure logging so each emitted message is structured JSON."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")


def log_event(event: str, **fields: Any) -> None:
    """Log one structured JSON event."""
    LOGGER.info(json.dumps({"event": event, **fields}, default=str, sort_keys=True))


def get_gold_table() -> str:
    """Return and validate the configured Databricks Gold table name."""
    table = os.getenv("GOLD_TABLE", DEFAULT_GOLD_TABLE).strip()
    if not _GOLD_TABLE_PATTERN.fullmatch(table):
        raise ValueError(
            "GOLD_TABLE must be an unquoted one-, two-, or three-part SQL identifier"
        )
    return table


def required_env(name: str) -> str:
    """Return a required environment variable or raise a clear error."""
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Required environment variable {name} is not set")
    return value


def connect_databricks() -> DatabricksConnection:
    """Connect to Databricks SQL using environment-only configuration.

    The Databricks SQL connector is imported inside this helper so importing
    this module does not require ``databricks-sql-connector``.
    """
    from databricks import sql  # Imported lazily by design.

    return sql.connect(
        server_hostname=required_env("DATABRICKS_HOST"),
        http_path=required_env("DATABRICKS_HTTP_PATH"),
        access_token=required_env("DATABRICKS_TOKEN"),
    )


def ensure_schema(duck_conn: DuckDBConnection) -> None:
    """Create the Gold exception read-model table and unique index."""
    duck_conn.execute(
        """
        CREATE TABLE IF NOT EXISTS gold_exception_cases (
            case_key VARCHAR,
            case_id VARCHAR,
            txn_id VARCHAR,
            business_date DATE,
            channel VARCHAR,
            case_type VARCHAR,
            internal_amount DOUBLE,
            network_amount DOUBLE,
            amount_diff DOUBLE,
            internal_status VARCHAR,
            network_status VARCHAR,
            disposition VARCHAR,
            reason VARCHAR,
            status VARCHAR,
            first_seen_ts TIMESTAMP,
            last_updated_ts TIMESTAMP
        )
        """
    )
    duck_conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_gold_exception_case_id
        ON gold_exception_cases(case_id)
        """
    )


def load_cases(
    duck_conn: DuckDBConnection,
    rows: Iterable[Sequence[Any]],
) -> int:
    """Atomically replace the DuckDB Gold exception cases with ``rows``.

    This function changes only ``gold_exception_cases``. In particular, it
    does not delete from or update ``ops_case_state`` or ``ops_case_actions``.
    """
    materialized_rows = [tuple(row) for row in rows]

    for row_number, row in enumerate(materialized_rows, start=1):
        if len(row) != len(COLUMNS):
            raise ValueError(
                f"Row {row_number} has {len(row)} values; "
                f"expected {len(COLUMNS)}"
            )

    duck_conn.execute("BEGIN TRANSACTION")
    try:
        duck_conn.execute("DELETE FROM gold_exception_cases")

        if materialized_rows:
            column_sql = ", ".join(COLUMNS)
            placeholders = ", ".join("?" for _ in COLUMNS)
            duck_conn.executemany(
                f"""
                INSERT INTO gold_exception_cases ({column_sql})
                VALUES ({placeholders})
                """,
                materialized_rows,
            )

        duck_conn.execute("COMMIT")
    except Exception:
        duck_conn.execute("ROLLBACK")
        raise

    return len(materialized_rows)


def fetch_cases(databricks_conn: DatabricksConnection) -> list[tuple[Any, ...]]:
    """Fetch Gold exception cases from the configured Databricks Delta table."""
    gold_table = get_gold_table()
    column_sql = ", ".join(COLUMNS)
    cursor = databricks_conn.cursor()

    try:
        cursor.execute(f"SELECT {column_sql} FROM {gold_table}")
        return [tuple(row) for row in cursor.fetchall()]
    finally:
        cursor.close()


def build_parser() -> argparse.ArgumentParser:
    """Build the reverse-ETL command-line argument parser."""
    parser = argparse.ArgumentParser(
        description=(
            "Copy Databricks Gold settlement exception cases into the "
            "Ops Console DuckDB."
        )
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--once",
        dest="dry_run",
        action="store_false",
        help="Fetch and replace DuckDB data once (default).",
    )
    mode.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        help="Fetch and count Databricks rows without writing to DuckDB.",
    )
    parser.set_defaults(dry_run=False)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run one reverse-ETL synchronization using environment configuration."""
    args = build_parser().parse_args(argv)
    configure_logging()

    gold_table = get_gold_table()
    duckdb_path = os.getenv("DUCKDB_PATH", DEFAULT_DUCKDB_PATH)

    databricks_conn = connect_databricks()
    try:
        rows = fetch_cases(databricks_conn)
    finally:
        databricks_conn.close()

    if not args.dry_run:
        import duckdb

        resolved_path = Path(duckdb_path).expanduser()
        resolved_path.parent.mkdir(parents=True, exist_ok=True)

        duck_conn = duckdb.connect(str(resolved_path))
        try:
            ensure_schema(duck_conn)
            load_cases(duck_conn, rows)
        finally:
            duck_conn.close()

    summary = {
        "rows": len(rows),
        "gold_table": gold_table,
        "duckdb_path": duckdb_path,
    }
    LOGGER.info(json.dumps(summary, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())