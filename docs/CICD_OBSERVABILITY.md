# CI/CD, IaC, Governance & Observability

## CI — always on, no secrets
`.github/workflows/ci.yml` runs ruff + black (informational) and **pytest (the gate)** on every push/PR.
pytest exercises `src/recon_logic.py` — the exact module the notebooks import — on a Java-equipped runner,
so a green check means the reconciliation logic that runs in production is correct.

## Manual Databricks smoke — gated
`.github/workflows/databricks-smoke.yml` (`workflow_dispatch` only) runs the real pipeline as a
git-sourced run via `scripts/run_smoke.py`. Bind the **`databricks` GitHub Environment** to a required
reviewer and add secrets `DATABRICKS_HOST` + `DATABRICKS_TOKEN` there. Never triggers on push; the token
is never echoed.

## IaC — Databricks Asset Bundle
`databricks.yml` + `resources/settlement_job.yml` define the Workflows Job as code. Validate/deploy from a
local machine with the modern CLI (v0.205+):
```bash
databricks bundle validate
databricks bundle deploy -t dev
```
Deploy from local (in-workspace deploy is blocked by egress on Free Edition). The hand-written
`jobs/settlement_recon_job.json` is the equivalent Jobs-API spec if you prefer `databricks jobs create`.

## Governance — `notebooks/08_governance.py`
Comments + **enforced NOT NULL / CHECK** on Silver + Gold only (Bronze/landing stay permissive), plus
**informational (NOT ENFORCED) primary keys**. A verification cell inserts a bad `match_status` and
asserts it is rejected. Publishes a `data_dictionary` table from `information_schema`.

## Observability — `notebooks/09_observability.py`
Appends a health row (row counts, `match_rate`, `exception_rate`, AUTO/MANUAL, open cases) to
`ops_run_log` and shows a trend view — a portable alternative to system tables. `dq_metrics` (written by
Silver) tracks per-run data quality. **Free alerting:** a Databricks SQL Alert on
`ops_run_log.exception_rate` breaching a threshold, plus the Job's `email_notifications.on_failure`.
