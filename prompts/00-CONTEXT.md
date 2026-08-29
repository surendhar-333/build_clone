# CONTEXT — paste this FIRST into any new model session

You are working on a Databricks data-engineering project. Read this whole block
before doing anything. A task file will follow.

## The project

A medallion-architecture **payment settlement & reconciliation lakehouse** on
Databricks Free Edition (serverless), Unity Catalog, Delta Lake. It reconciles an
**internal** settlement feed against a **network/bank** feed and raises exception
cases.

```
Phase 1  01_phase1_data_generation.py       synthetic internal + network CSVs, discrepancies injected
Phase 2  02_phase2_bronze_autoloader.py     Auto Loader → bronze_internal, bronze_network
Phase 3  03_phase3_silver.py                standardise + DQ split + dedupe → silver_*, silver_*_rejects
Phase 4  04_phase4_gold_reconciliation.py   FULL OUTER JOIN → gold_recon_results, gold_exception_cases
Phase 5  05_phase5_reports.py               3 × gold_report_* aggregates
Phase 6  06_phase6_orchestration_scale.py   orchestration driver + scale lab → gold_scale_log
```

All notebooks live in `notebooks/`. Everything is in `workspace.settlement_recon`.
Landing zone is a Unity Catalog volume: `/Volumes/workspace/settlement_recon/landing`.

**There is no Azure and no AWS in this repo.** Do not add cloud-specific paths.

## Hard constraints

- **Databricks source format.** Every notebook's first line is
  `# Databricks notebook source`; cells are separated by `# COMMAND ----------`;
  markdown cells use `# MAGIC %md`. If you break this the notebook stops rendering.
- **Serverless Free Edition.** No `pip install` of new packages. Standard PySpark +
  Delta only. No `spark.conf` settings that require classic clusters.
- **Idempotent.** Every notebook must survive re-running: `CREATE ... IF NOT EXISTS`,
  `mode("overwrite")` with `overwriteSchema`, or `MERGE`.
- **Do not change the table/column contract** unless the task explicitly says to. If
  you change a column, you must update every phase that reads it.
- **Config constants sit at the top of each notebook** (`CATALOG`, `SCHEMA`, `VOLUME`,
  paths) and must stay identical across phases.

## The table contract (do not break)

- `bronze_internal` / `bronze_network` — 8 CSV columns (`txn_id`, `business_date`,
  `channel`, `amount`, `currency`, `status`, `account_id`, `txn_ts`), plus
  `network_ref` on the network side, plus `_rescued_data`, `_ingest_ts`, `_source_file`.
  Types are Auto Loader–inferred.
- `silver_internal` / `silver_network` — same business columns with enforced types:
  `business_date` date, `amount` **decimal(18,2)**, `txn_ts` timestamp; `channel`,
  `status`, `currency` upper+trimmed. Plus `_ingest_ts`, `_source_file` (+`network_ref`).
  `_rescued_data` is deliberately dropped from clean Silver.
- `silver_internal_rejects` / `silver_network_rejects` — all bronze columns +
  `reject_reason` string.
- `gold_recon_results` — `txn_id`, `business_date`, `channel`, `internal_amount`,
  `network_amount`, `amount_diff`, `internal_status`, `network_status`,
  `match_status`, `reason`.
- `gold_exception_cases` — `case_id`, `txn_id`, `business_date`, `channel`,
  `case_type`, `internal_amount`, `network_amount`, `amount_diff`,
  `internal_status`, `network_status`, `disposition`, `reason`, `created_ts`.
- `gold_report_funding_by_channel`, `gold_report_cash_flow`,
  `gold_report_exception_summary`, `gold_scale_log`.

## How the reconciliation works

- **FULL OUTER JOIN on `txn_id` only.** `business_date` and `channel` are coalesced
  after the join, not joined on. `network_ref` exists but is unused as a key.
- `AMOUNT_TOLERANCE = 0.01`, compared with strict `>`.
- `AUTO_RESOLVE_TOLERANCE = 1.00`.
- `amount_diff = internal_amount − network_amount` (so an inflated network side gives
  a **negative** diff).
- Classification order (order is load-bearing): `UNMATCHED_INTERNAL`,
  `UNMATCHED_NETWORK`, `MISMATCH_BOTH`, `MISMATCH_AMOUNT`, `MISMATCH_STATUS`,
  else `MATCHED`.
- `disposition` = `AUTO` only if `case_type == "MISMATCH_AMOUNT"` and
  `abs(amount_diff) <= AUTO_RESOLVE_TOLERANCE`; otherwise `MANUAL`.

## Known defects (do not "helpfully" fix ones outside your task)

The repo has real bugs, documented in `docs/PROJECT_STATE.md`. The big ones:
`MISMATCH_BOTH` and `UNMATCHED_NETWORK` are unreachable; `AUTO` disposition never
fires; `case_id` is positional and therefore unstable across runs; null `status`
is silently classified as `MATCHED`; re-running Phase 1 doubles Bronze.

**Only fix what your task asks for.** If you spot something else, list it at the end
under "Also noticed" — do not change it.

## The most important rule

The repo owner did **not** hand-write this code and is taking ownership of it for job
interviews. Therefore:

- Prefer **readable, explainable PySpark** over clever or compressed code.
- Comment the **why**, not the what.
- Where you make a design decision, state the trade-off in a comment or in a markdown
  cell, so the owner can defend it when questioned.
- If a simpler approach exists that's easier to explain, choose it.

Now wait for the task file.
