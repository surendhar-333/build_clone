"""Submit the settlement pipeline as a git-sourced Databricks run and poll to completion.

Reusable smoke check for CI or local use. Reads credentials from the environment (never hard-coded):
  DATABRICKS_HOST   e.g. https://dbc-xxxx.cloud.databricks.com
  DATABRICKS_TOKEN  a Databricks PAT

Usage:  python scripts/run_smoke.py --rows 20000
Exits non-zero unless the run finishes SUCCESS — suitable as a CI gate.
"""
import argparse
import os
import sys
import time

import requests

REPO = "https://github.com/surendhar-333/build"
PHASES = [
    "01_phase1_data_generation",
    "02_phase2_bronze_autoloader",
    "03_phase3_silver",
    "04_phase4_gold_reconciliation",
    "05_phase5_reports",
]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rows", default="20000")
    args = parser.parse_args()

    host = os.environ["DATABRICKS_HOST"].rstrip("/")
    headers = {"Authorization": f"Bearer {os.environ['DATABRICKS_TOKEN']}"}

    tasks = []
    for i, nb in enumerate(PHASES):
        task = {
            "task_key": f"p{i + 1}",
            "notebook_task": {"notebook_path": f"notebooks/{nb}", "source": "GIT"},
        }
        if i == 0:
            task["notebook_task"]["base_parameters"] = {"rows": args.rows}
        else:
            task["depends_on"] = [{"task_key": f"p{i}"}]
        tasks.append(task)

    body = {
        "run_name": "ci_smoke",
        "git_source": {"git_url": REPO, "git_provider": "gitHub", "git_branch": "main"},
        "tasks": tasks,
    }

    resp = requests.post(
        f"{host}/api/2.1/jobs/runs/submit", headers=headers, json=body, timeout=60
    )
    resp.raise_for_status()
    run_id = resp.json()["run_id"]
    print(f"submitted git-sourced run {run_id}")

    while True:
        get = requests.get(
            f"{host}/api/2.1/jobs/runs/get",
            headers=headers,
            params={"run_id": run_id},
            timeout=60,
        )
        get.raise_for_status()
        state = get.json()["state"]
        life = state["life_cycle_state"]
        print(f"state: {life}")
        if life in ("TERMINATED", "INTERNAL_ERROR", "SKIPPED"):
            result = state.get("result_state")
            print(f"result: {result}")
            sys.exit(0 if result == "SUCCESS" else 1)
        time.sleep(15)


if __name__ == "__main__":
    main()
