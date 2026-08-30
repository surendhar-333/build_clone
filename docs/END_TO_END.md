# End-to-end workflow

How the whole system fits together — data flow, the exception lifecycle, orchestration, the developer
loop, and the analyst's day. Every box below maps to committed, validated code.

## 1. Runtime data flow (medallion → serving)

```mermaid
flowchart TD
  IL["Internal ledger<br/>source of truth"] --> LZ["Landing<br/>append-only batches"]
  NF["Network feed<br/>6 injected break types"] --> LZ
  LZ --> BR["Bronze · Auto Loader<br/>AvailableNow, checkpoints"]
  BR --> SV["Silver · MERGE SCD1 + CDF<br/>idempotent, txn_ts guard, dq_metrics"]
  SV --> GD["Gold · reconciliation<br/>6 outcomes, tolerance auto/manual"]
  GD --> EX["Exception cases<br/>stable id + lifecycle"]
  GD --> RP["Reports<br/>funding · cashflow · exceptions"]
  EX --> OC["Ops Console · FastAPI<br/>triage + audited disposition"]
  EX --> DB["Dashboard · Streamlit"]
  RP --> DB
```

1. **Generate & land** (nb01) — a deterministic internal ledger + a network feed with six disjoint
   break bands (drop, amount-manual, amount-auto, status, both, null-status) plus network-only phantoms,
   written as append-only `business_date=/batch=` CSV batches.
2. **Bronze** (nb02) — Auto Loader (`Trigger.AvailableNow`) streams the CSVs into raw Delta with
   checkpoints, schema evolution and rescued-data capture. Exactly-once ingest.
3. **Silver** (nb03) — standardize + quality-split (reject null/empty txn_id, amount ≤ 0), then
   **MERGE (SCD Type 1)** by `txn_id` with a `txn_ts` out-of-order guard; **Change Data Feed** on;
   `dq_metrics` per run. Re-running is idempotent.
4. **Gold** (nb04) — full-outer-join via the unit-tested `src/recon_logic.reconcile`, classify six
   outcomes with tolerance-based AUTO/MANUAL, then upsert exception cases with a stable identity + lifecycle.
5. **Reports** (nb05) — funding-by-channel, daily cash-flow, exception summary.
6. **Serve** — a local FastAPI **Ops Console** (analyst triage + disposition) and a Streamlit **dashboard**,
   both over an offline DuckDB snapshot of Gold.

## 2. Exception case lifecycle

`case_key = sha2(business_date | txn_id)` is stable; `case_id` and `first_seen_ts` never change. The
system only recomputes OPEN/AUTO_RESOLVED; analyst-set states are preserved. (Rules unit-tested in
`src/case_lifecycle.py` / `tests/test_case_lifecycle.py`; the notebook MERGE mirrors them.)

```mermaid
stateDiagram-v2
  [*] --> OPEN: new · MANUAL
  [*] --> AUTO_RESOLVED: new · AUTO
  OPEN --> AUTO_RESOLVED: disposition AUTO
  AUTO_RESOLVED --> OPEN: disposition MANUAL
  OPEN --> MANUAL_REVIEW: analyst escalate
  MANUAL_REVIEW --> CLOSED: analyst resolve / approve
  OPEN --> CLOSED_DISAPPEARED: absent from latest recon
  MANUAL_REVIEW --> CLOSED_DISAPPEARED: absent from latest recon
  CLOSED --> [*]
  CLOSED_DISAPPEARED --> [*]
```

## 3. Orchestration

```mermaid
flowchart LR
  subgraph WF["Databricks Workflows — native scheduler (runs when laptop is off)"]
    direction LR
    P1["01 generate"] --> P2["02 bronze"] --> P3["03 silver"] --> P4["04 gold"] --> P5["05 reports"] --> G8["08 governance"] --> O9["09 observability"]
  end
  DG["Local Dagster DAG"] -. run-now via Jobs REST API .-> P1
```

Databricks Workflows owns production scheduling (`jobs/settlement_recon_job.json`, also as an Asset
Bundle in `databricks.yml`); the local **Dagster** DAG (`orchestration/dagster/`) is a standalone control
plane that triggers the same Job via the Jobs REST API.

## 4. Developer / CI loop

```mermaid
flowchart LR
  DEV["Local edit<br/>(Copilot generates, review, write)"] --> PUSH["git push main"]
  PUSH --> CI["GitHub Actions<br/>ruff/black + pytest"]
  PUSH --> RUN["git-sourced Databricks run<br/>clones repo at run time — no manual Pull"]
  RUN --> VERIFY["notebook.exit JSON<br/>counts · rates · case_id signature"]
```

The pipeline runs as a **git-sourced job**, so a push is the deploy: Databricks clones `main` at run time
and `src/` imports resolve. CI runs the unit tests (recon logic, lifecycle rules, serving) on every push.

## 5. Analyst workflow (how it's used)

```mermaid
flowchart LR
  Q["Open queue<br/>(aging / amount sorted)"] --> C["Open a break case<br/>internal vs network"]
  C --> A{"Disposition"}
  A -->|approve / resolve| CL["CLOSED<br/>leaves queue"]
  A -->|escalate| MR["MANUAL_REVIEW<br/>stays for follow-up"]
  A --> AU["audit row appended<br/>(idempotency-keyed) then state upsert"]
```

## 6. Build phases (all validated on serverless)

| Phase | Delivers | Proof |
|---|---|---|
| P1 | correct engine, all 6 outcomes, single tested source of truth | run shows every outcome |
| P2 | incremental MERGE Silver + CDF + dq_metrics | re-run → identical Silver counts |
| P3 | stable `case_key` + lifecycle | `case_id` signature identical across runs |
| P4 | FastAPI Ops Console | idempotent disposition, tested |
| P5 | 1M-row scale lab + portfolio docs | ~3.4 min, correctness held |
| P6 | UC governance + data dictionary | bad insert rejected; 48-row dictionary |
| P7 | Workflows Job + Dagster DAG | Job created + ran green |
| P8 | CI + observability + IaC | run_log written; CI green |

## 7. Run it end-to-end
```bash
# Pipeline (git-sourced job): submit tasks 01→05 (+08,09) with notebook_task.source=GIT — see jobs/README.md
# Local app + dashboard:
cd serving && pip install -r requirements.txt && uvicorn app:app --reload          # http://127.0.0.1:8000
pip install -r serving/requirements-dashboard.txt && streamlit run serving/dashboard_streamlit.py
# Tests:
PYTHONPATH=. pytest -q
```
