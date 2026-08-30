# Résumé — project block (Data Engineer)

> Honesty rule: every line maps to code that runs and was validated on Databricks. **No Azure/AWS/GCP** —
> Databricks is the managed cloud lakehouse platform; frame it as such, never as a hyperscaler.

## Compact project bullet (drop into a résumé)

**Payment Settlement & Reconciliation Lakehouse — Databricks / Delta Lake / PySpark**
*github.com/surendhar-333/build*

- Built an end-to-end **medallion lakehouse** (Bronze → Silver → Gold) that reconciles an internal
  payment ledger against a bank/network feed, classifying **six break types** with tolerance-based
  **auto/manual disposition**; validated end-to-end on serverless.
- **Streaming/incremental ingestion** with Auto Loader (Spark Structured Streaming, `Trigger.AvailableNow`
  micro-batch) + schema-evolution/rescued-data handling; **incremental Silver as SCD Type 1 via Delta
  MERGE** with an out-of-order (`txn_ts`) guard, made re-ingest-safe (idempotent) and **Change-Data-Feed**-enabled.
- Fixed a **Spark three-valued-logic bug** (a NULL status silently classifying as MATCHED) and removed
  test/prod drift by having notebooks **import one unit-tested reconciliation module**; added a **stable
  `sha2` case identity** and an OPEN → AUTO_RESOLVED / MANUAL_REVIEW → CLOSED **case lifecycle** via MERGE
  (fixing an unstable positional case-id).
- Shipped a **FastAPI operations console** for analyst break-case triage with an **idempotent, audited**
  disposition write-back; **GitHub Actions CI** (pytest) on every push; a **reproducible scale lab** with
  performance tuning (`OPTIMIZE` / Z-ORDER) — numbers in `docs/BENCHMARKS.md`.

**Stack:** PySpark · Delta Lake · Databricks (serverless) · Structured Streaming / Auto Loader ·
Change Data Feed · Delta MERGE (SCD1) · Unity Catalog · FastAPI · DuckDB · pytest · GitHub Actions.

## One-line summary
End-to-end Databricks medallion lakehouse for payment settlement reconciliation — streaming ingestion,
incremental CDF-driven processing, a tested reconciliation engine, a stable case lifecycle, an analyst
ops console, CI, and a performance scale lab.
