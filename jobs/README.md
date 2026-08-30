# Databricks Workflows Job (native orchestrator)

`settlement_recon_job.json` defines the production orchestrator: a multi-task Job running the medallion
pipeline (Phase 1 → 5) in dependency order under the Databricks **native scheduler** — it runs even when
your laptop is off.

- `depends_on` enforces the DAG (Bronze never runs before generation, etc.).
- `max_retries` + `min_retry_interval_millis` add resilience.
- `max_concurrent_runs: 1` stops overlapping runs racing on the same tables.
- `git_source` clones this repo at run time, so `src/` imports resolve and the Job always runs the
  latest `main` — no manual "Pull".
- `schedule` is committed **PAUSED**; the `rows` job parameter flows into Phase 1.

## Create / run (Databricks CLI)
First replace `REPLACE_WITH_YOUR_EMAIL` (or drop `email_notifications`).
```bash
databricks jobs create --json-file jobs/settlement_recon_job.json
databricks jobs run-now --job-id <JOB_ID>
```
Or paste the JSON in the Workflows UI → Create Job → JSON editor.

This exact multi-task DAG has been created and run green end-to-end on serverless (see `docs/RUN_LOG.md`).
The local Dagster DAG in `orchestration/dagster/` triggers this same Job via the Jobs REST API.
