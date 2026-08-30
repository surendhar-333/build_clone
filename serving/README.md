# Payment Exception Ops Console (local, offline)

A lightweight **FastAPI** operations console — the real-world consumer of the reconciliation
pipeline. A payments-ops analyst triages exception **break cases** (from `gold_exception_cases`) and
records dispositions, written back as an **immutable, idempotent audit trail** plus a current-state
table. Runs **fully offline against a local DuckDB file** — no Databricks, no network, no auth — so
it is safe to demo anywhere. (An optional "online" mode reading Databricks SQL can be added later;
the offline DuckDB backend is the default demo path.)

## What it does
- **Queue** (`/`) — active cases (`OPEN` / `MANUAL_REVIEW`), sortable by aging or absolute amount
  difference, filterable by channel and case type, with a KPI header.
- **Case detail** (`/cases/{case_id}`) — internal-vs-network comparison, reason, full action history,
  and a disposition form.
- **Disposition** (`POST /cases/{case_id}/disposition`) — writes one `ops_case_actions` audit row
  (deduplicated by an idempotency key) **then** upserts `ops_case_state`, inside a transaction.
  `approve`/`resolve` → `CLOSED`; `escalate` → `MANUAL_REVIEW`.
- JSON: `/cases`, `/kpis`, `/healthz`.

Effective status = `COALESCE(ops_case_state.status, gold_exception_cases.status)`, so analyst actions
override the pipeline-computed status without the pipeline ever overwriting analyst decisions.

## Run it
```bash
cd serving
pip install -r requirements.txt
uvicorn app:app --reload
# open http://127.0.0.1:8000
```
On first start it seeds ~60 realistic sample cases into `./ops_console.duckdb` (override the path with
the `DUCKDB_PATH` env var). Delete that file to reseed.

## Design notes
- **Idempotent, ordered writes**: audit row first (deduped on `idempotency_key`), then the state
  upsert — a double-submit or retry never double-writes.
- **Injection-safe** (parameterized queries) and **XSS-safe** (autoescaped Jinja templates).
- App-owned tables (`ops_case_actions`, `ops_case_state`) are never written by the pipeline.
