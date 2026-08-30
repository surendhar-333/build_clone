"""Local Dagster orchestrator for the settlement pipeline.

This is the *standalone orchestrator* surface (the Airflow/Dagster resume keyword). It does NOT
re-implement the pipeline — it delegates to the Databricks **Workflows** Job (created from
`jobs/settlement_recon_job.json`) via the Jobs REST API and polls it to completion.

Division of responsibility (say this in an interview):
- **Databricks Workflows** owns *production scheduling* — it runs even when this laptop is off.
- **Dagster** here is a local control-plane demo: author a DAG, trigger the remote Job, track it.

Configure via environment variables (never commit a token):
  DATABRICKS_HOST     e.g. https://dbc-xxxx.cloud.databricks.com
  DATABRICKS_TOKEN    a Databricks personal access token
  SETTLEMENT_JOB_ID   the job_id created from jobs/settlement_recon_job.json

Run:
  pip install -r requirements.txt
  dagster dev -f settlement_pipeline.py      # then open http://127.0.0.1:3000
"""
import os
import time

import requests
from dagster import (
    Definitions,
    Field,
    OpExecutionContext,
    RunRequest,
    job,
    op,
    schedule,
)


def _databricks():
    host = os.environ["DATABRICKS_HOST"].rstrip("/")
    token = os.environ["DATABRICKS_TOKEN"]
    job_id = int(os.environ["SETTLEMENT_JOB_ID"])
    return host, {"Authorization": f"Bearer {token}"}, job_id


@op(config_schema={"rows": Field(str, default_value="20000")})
def trigger_settlement_pipeline(context: OpExecutionContext) -> int:
    """Kick off the Databricks Workflows Job with the requested row count."""
    host, headers, job_id = _databricks()
    rows = context.op_config["rows"]
    resp = requests.post(
        f"{host}/api/2.1/jobs/run-now",
        headers=headers,
        json={"job_id": job_id, "job_parameters": {"rows": rows}},
        timeout=30,
    )
    resp.raise_for_status()
    run_id = resp.json()["run_id"]
    context.log.info(f"Triggered Databricks job {job_id} at rows={rows} -> run {run_id}")
    return run_id


@op
def wait_for_completion(context: OpExecutionContext, run_id: int) -> str:
    """Poll the Databricks run until it reaches a terminal state; fail the op if it wasn't SUCCESS."""
    host, headers, _ = _databricks()
    while True:
        resp = requests.get(
            f"{host}/api/2.1/jobs/runs/get",
            headers=headers,
            params={"run_id": run_id},
            timeout=30,
        )
        resp.raise_for_status()
        state = resp.json()["state"]
        life = state["life_cycle_state"]
        context.log.info(f"Databricks run {run_id}: {life}")
        if life in ("TERMINATED", "INTERNAL_ERROR", "SKIPPED"):
            result = state.get("result_state")
            if result != "SUCCESS":
                raise Exception(f"Databricks run {run_id} finished {life}/{result}")
            context.log.info(f"Databricks run {run_id} SUCCESS")
            return result
        time.sleep(15)


@job
def settlement_recon_dagster_job():
    wait_for_completion(trigger_settlement_pipeline())


@schedule(
    cron_schedule="0 3 * * *",
    job=settlement_recon_dagster_job,
    execution_timezone="Asia/Kolkata",
)
def daily_settlement_schedule(_context):
    return RunRequest(run_key=None)


defs = Definitions(
    jobs=[settlement_recon_dagster_job],
    schedules=[daily_settlement_schedule],
)
