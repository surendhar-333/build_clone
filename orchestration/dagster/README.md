# Local Dagster orchestrator

A standalone orchestrator surface (the Airflow/Dagster keyword) that **triggers the Databricks Workflows
Job** via the Jobs REST API and tracks it to completion. Databricks Workflows remains the *production*
scheduler; Dagster here is a local control plane that authors a DAG and delegates execution to Databricks.

Dagster is chosen over Airflow because it is pip-installable and runs natively on Windows/PowerShell
(no WSL/Docker required).

## Run
```bash
pip install -r requirements.txt
# PowerShell:
$env:DATABRICKS_HOST = "https://dbc-xxxx.cloud.databricks.com"
$env:DATABRICKS_TOKEN = "dapi..."          # a PAT — never commit it
$env:SETTLEMENT_JOB_ID = "<job_id from jobs/settlement_recon_job.json>"
dagster dev -f settlement_pipeline.py
# open http://127.0.0.1:3000 -> Launchpad -> launch settlement_recon_dagster_job
```

`daily_settlement_schedule` shows a cron schedule (enable it in the Dagster UI). The op fails loudly if
the Databricks run finishes anything other than SUCCESS, so a failed pipeline surfaces in Dagster too.
