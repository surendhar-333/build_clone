# Interview Q&A — the questions a good interviewer will actually ask

Answer honestly; the project is strong enough that precise, non-inflated answers land better than buzzwords.

**"Walk me through the architecture."**
A medallion lakehouse on Databricks. A deterministic generator lands an internal ledger + a bank/network
feed (with injected discrepancies) as append-only CSV batches. Auto Loader streams them into Bronze;
Silver cleans/dedupes and upserts current state via Delta MERGE (SCD Type 1) with Change Data Feed on;
Gold full-outer-joins the two sides, classifies six break types, and maintains exception cases with a
lifecycle. Reports + a FastAPI ops console + a scale lab sit on top.

**"Is it exactly-once?"**
Ingestion is exactly-once at the file level via Auto Loader checkpoints. The Silver write uses
`foreachBatch`-style MERGE, which is *at-least-once* delivery made **effectively-once** by an idempotent
MERGE keyed on `txn_id` (with a `txn_ts` guard so a replayed older batch can't overwrite newer state). I
don't claim end-to-end exactly-once — I claim idempotent, restartable processing, and I proved it by
re-running the pipeline and getting identical Silver counts and an identical case-id signature.

**"Is that CDC? Debezium?"**
It's Delta **Change Data Feed** — change tracking on the Delta tables themselves, used to drive
downstream incrementality. It is *not* source-database CDC (no Debezium/LogMiner reading an OLTP redo
log). I designed that Oracle→Debezium→Event Hubs version too, but it's not built, so I don't claim it.

**"How did you handle the NULL-status bug?"**
Classic Spark three-valued logic: `col != col` returns NULL when either side is NULL, which fell through
to MATCHED — a false clean. I made the comparison null-aware (a null on either present side ⇒
MISMATCH_STATUS) and pinned it with a regression test. It's the same module the notebooks import, so the
test guards production.

**"Why did the case IDs matter?"**
The first version numbered cases by row position, so any change to the case set renumbered everything —
analyst notes would attach to the wrong case. I switched to a stable `sha2(business_date|txn_id)` key and
a MERGE that preserves `case_id`, `first_seen_ts`, and analyst-set states; only system states recompute.
I verified the case-id set signature is byte-identical across re-runs.

**"Is the app deployed / multi-user?"**
No — it's a **local** FastAPI console backed by an offline DuckDB snapshot, built to demonstrate the
operational workflow (triage a break, record a disposition as an idempotent audited action). It's not a
hosted multi-user service; I'd containerize it and put the write-model behind auth for production.

**"How did you measure the scale numbers?"**
Fixed seed, reset between runs, ran the pipeline at increasing row counts on serverless, and read the
per-phase durations from the job run metadata (not a stopwatch). See `docs/BENCHMARKS.md`. On Databricks
serverless, Photon is always on and cluster sizing is hidden, so my tuning story is about data layout
(OPTIMIZE/Z-ORDER, file sizing) and incremental vs full recomputation, not knob-twiddling on a cluster.

**"What would you do differently for production?"**
Real source CDC (Debezium) instead of a synthetic generator; enforced Unity Catalog constraints + data
contracts; a proper orchestrator (Databricks Workflows, with an Airflow/Dagster DAG); the ops console
containerized behind SSO; and observability (a run-log table + alerting) — several of these are on the
roadmap as stretch phases.
