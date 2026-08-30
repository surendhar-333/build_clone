# Keyword → evidence map

Every résumé/JD keyword you can claim, and the exact deliverable that earns it. If a keyword isn't here,
don't claim it.

| Keyword | Where it's demonstrated |
|---|---|
| Medallion architecture / Lakehouse | notebooks 02→05: Bronze → Silver → Gold on Delta |
| Delta Lake | all Gold/Silver tables; MERGE, CDF, OPTIMIZE/Z-ORDER |
| PySpark / Spark SQL | entire pipeline; `src/recon_logic.py` |
| Spark Structured Streaming | Bronze Auto Loader `readStream` + `Trigger.AvailableNow` |
| Auto Loader (cloudFiles) | notebook 02 (schema evolution, rescued data) |
| Incremental / SCD Type 1 | Silver Delta MERGE with `txn_ts` guard (notebook 03) |
| Change Data Feed (CDF) | enabled on Silver + exception tables |
| Delta MERGE / upsert | Silver SCD1, Gold case lifecycle |
| Data quality | reject/quarantine tables + `dq_metrics` (notebook 03) |
| Idempotency / restartability | append-only landing, checkpoints, MERGE-by-key (proven by re-run) |
| Reconciliation engine | `src/recon_logic.py` — 6 outcomes + auto/manual disposition, unit-tested |
| Unit testing / pytest | `tests/test_recon_logic.py` (six outcomes, null regression, disposition) |
| CI/CD | `.github/workflows/ci.yml` — ruff/black + pytest on every push |
| REST API / web app | `serving/app.py` FastAPI ops console |
| SQL / analytics | reports (funding, cash-flow, exception summary) |
| Performance tuning | scale lab: OPTIMIZE / Z-ORDER, file compaction (`docs/BENCHMARKS.md`) |
| Data modeling / contracts | `docs/DATA_MODEL.md` (frozen schema, lifecycle) |
| Databricks (serverless) | whole project; git-sourced jobs; CLI orchestration |

**Do NOT claim:** Azure / AWS / GCP, Kafka/Event Hubs, Debezium/source-CDC, Airflow (unless the stretch
orchestration phase is built), Terraform/IaC, ML — none are built yet.
