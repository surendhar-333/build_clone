<div align="center">

# 💳 Payment Settlement & Reconciliation Lakehouse

### An end-to-end Databricks lakehouse for payment settlement reconciliation — streaming ingestion, incremental CDF-driven processing, a unit-tested reconciliation engine, a stable case lifecycle, an analyst ops console, CI, and a measured scale lab.

[![CI](https://github.com/surendhar-333/build/actions/workflows/ci.yml/badge.svg)](https://github.com/surendhar-333/build/actions/workflows/ci.yml)
[![PySpark](https://img.shields.io/badge/PySpark-Data%20Engineering-E25A1C?logo=apachespark&logoColor=white)](https://spark.apache.org/)
[![Databricks](https://img.shields.io/badge/Databricks-Serverless-FF3621?logo=databricks&logoColor=white)](https://www.databricks.com/)
[![Delta Lake](https://img.shields.io/badge/Delta%20Lake-MERGE%20%2B%20CDF-00ADD8?logo=delta&logoColor=white)](https://delta.io/)
[![FastAPI](https://img.shields.io/badge/FastAPI-Ops%20Console-009688?logo=fastapi&logoColor=white)](serving/app.py)

</div>

---

## ✨ What this is

A production-shaped **payment settlement & reconciliation lakehouse** that reconciles an internal payment
ledger against a bank/network feed, classifies six break types, and gives analysts a console to work the
breaks. It runs **100% free** on Databricks Free Edition (serverless) plus local tooling — every claim
below is backed by a real run whose numbers are recorded in [`docs/BENCHMARKS.md`](docs/BENCHMARKS.md).

- 🏗️ **Medallion** Bronze → Silver → Gold on Delta Lake
- ⚡ **Streaming ingestion** — Auto Loader (Structured Streaming, `Trigger.AvailableNow`) with schema
  evolution + rescued-data handling
- 🔁 **Incremental Silver** — SCD Type 1 via **Delta MERGE** with an out-of-order (`txn_ts`) guard;
  **Change Data Feed** enabled; **idempotent** (re-runs leave state identical)
- 🔍 **Reconciliation engine** — full-outer-join, six outcomes, tolerance-based auto/manual disposition,
  in **one unit-tested module the notebooks import** (no test/prod drift)
- 🗂️ **Stable exception cases** — `sha2` case identity + an OPEN → AUTO_RESOLVED / MANUAL_REVIEW → CLOSED
  lifecycle via MERGE (analyst decisions are never overwritten)
- 🖥️ **Ops Console** — a FastAPI app for analyst break triage with an **idempotent, audited** write-back
- 🧪 **CI** — GitHub Actions runs pytest on every push · ⏱️ **Scale lab** with measured numbers

## 📈 Results by the numbers (measured, 1,000,000 rows)

| | Value |
|---|---|
| End-to-end pipeline (5 phases) | **~3.4 min** on serverless (generate 32s · Bronze 57s · Silver 52s · Gold 33s · reports 28s) |
| Reconciled rows | 1,005,000 (all six outcomes present) |
| Exception cases | 119,605 — **119,605 distinct case_ids (zero collisions)** |
| Idempotency | re-running leaves Silver counts + `case_id` signature **byte-identical** |
| OPTIMIZE/Z-ORDER | point-lookup 1.10s → 0.69s (single ~10 MB file — honest note in BENCHMARKS) |

## 🗺️ Architecture

~~~mermaid
flowchart LR
    A["Internal ledger (source of truth)"] --> C["Landing (append-only batches)"]
    B["Network feed (6 injected break types)"] --> C
    C --> D["Bronze — Auto Loader (AvailableNow) + checkpoints"]
    D --> E["Silver — MERGE SCD1 (txn_ts guard) + CDF + dq_metrics"]
    E --> F["Gold — full outer join, 6-way classify (shared tested module)"]
    F --> G["Exception cases — sha2 id + lifecycle (MERGE)"]
    E --> H["Reports (funding / cash-flow / exceptions)"]
    G --> I["FastAPI Ops Console (analyst triage + audited disposition)"]
    F --> J["Scale lab — timings + OPTIMIZE/ZORDER"]

    classDef s fill:#eef6ff,stroke:#2563eb,color:#172554
    classDef b fill:#fff7ed,stroke:#c2410c,color:#431407
    classDef si fill:#f8fafc,stroke:#64748b,color:#0f172a
    classDef g fill:#fefce8,stroke:#ca8a04,color:#422006
    classDef o fill:#f0fdf4,stroke:#16a34a,color:#052e16
    class A,B,C s
    class D b
    class E si
    class F,G g
    class H,I,J o
~~~

## 🚀 Pipeline

| Phase | Notebook | What it does |
|---|---|---|
| 1 | [Data generation](notebooks/01_phase1_data_generation.py) | Deterministic internal + network data across disjoint break bands (drop / amount / status / both / null-status / phantom); append-only batch landing |
| 2 | [Bronze](notebooks/02_phase2_bronze_autoloader.py) | Auto Loader streaming ingest (AvailableNow), schema evolution, rescued data, checkpoints |
| 3 | [Silver](notebooks/03_phase3_silver.py) | Standardize/quality-split, **MERGE SCD1** with `txn_ts` guard, **enable CDF**, `dq_metrics` |
| 4 | [Gold](notebooks/04_phase4_gold_reconciliation.py) | Reconcile via the shared tested module; exception cases with **stable id + lifecycle** |
| 5 | [Reports](notebooks/05_phase5_reports.py) | Funding-by-channel, daily cash-flow, exception summary |
| 6 | [Orchestration & scale](notebooks/06_phase6_orchestration_scale.py) | Multi-task Job wiring + scale-lab timing/OPTIMIZE |

Reconciliation core: [`src/recon_logic.py`](src/recon_logic.py) · tests: [`tests/test_recon_logic.py`](tests/test_recon_logic.py) ·
frozen schema: [`docs/DATA_MODEL.md`](docs/DATA_MODEL.md).

## 🖥️ Ops Console (the operational consumer)

A local **FastAPI** app ([`serving/`](serving/)) where a payments-ops analyst triages break cases and
records dispositions. Reads exceptions from an **offline DuckDB** snapshot (no Databricks/network/auth
needed to demo); write-back appends an **idempotency-keyed audit row** then upserts case state inside a
transaction. `approve`/`resolve` → CLOSED, `escalate` → MANUAL_REVIEW; the pipeline never overwrites
analyst decisions.

~~~bash
cd serving && pip install -r requirements.txt && uvicorn app:app --reload
# http://127.0.0.1:8000  (seeds ~60 sample cases on first run)
~~~

## 🧪 Tests & CI

`src/recon_logic.py` is Databricks-independent and unit-tested (six outcomes, the NULL-status regression,
disposition boundaries). GitHub Actions runs `pytest` on every push (badge above).

~~~bash
PYTHONPATH=. pytest -q      # (PowerShell: $env:PYTHONPATH="."; pytest -q)
~~~

## 🔬 Reproduce the scale lab
See [`docs/SCALE_LAB.md`](docs/SCALE_LAB.md) — run the 01→05 DAG as a git-sourced job at a chosen `rows`,
read per-phase timings from run metadata, and run the OPTIMIZE/Z-ORDER measurement.

## 🎯 Honest scope
- Runs on **Databricks (a managed cloud lakehouse platform)** — **not** Azure/AWS/GCP core services.
- Change tracking is **Delta Change Data Feed**, not source-database CDC (no Debezium/redo-log).
- Ingest is exactly-once (Auto Loader checkpoints); the MERGE makes processing **effectively-once /
  idempotent** — not end-to-end exactly-once.
- The Ops Console is a **local** app demonstrating the workflow, not a hosted multi-user service.

## 🛣️ Roadmap (stretch)
Unity Catalog enforced constraints + data contracts · real orchestration (Databricks Workflows + a local
Dagster DAG) · observability (run-log + alerting) · optional DLT/Lakeflow showcase.

---
<div align="center">
<b>An executable, tested, measured data-engineering project — not a slide deck.</b>
</div>
