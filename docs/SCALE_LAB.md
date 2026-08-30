# Scale lab — methodology

## How to reproduce
1. Reset the schema (drop tables + clear landing/checkpoints) for clean numbers.
2. Submit the 01→05 pipeline as a **git-sourced job** at the target `rows` (100k / 1M / …):
   `databricks runs submit` with `git_source` (repo @ main) and `notebook_task.source = GIT`.
3. Read **per-phase wall-clock** from the run metadata (`tasks[].execution_duration`) — not a stopwatch.
4. Read **correctness** from each notebook's JSON `dbutils.notebook.exit()` summary.
5. Run the OPTIMIZE/Z-ORDER measurement notebook (`DESCRIBE DETAIL` before/after + a timed point-lookup).

Fixed seed + reset between sizes ⇒ reproducible. Numbers land in `BENCHMARKS.md` / `RUN_LOG.md`.

## What to measure
- Per-phase duration and how the bottleneck moves with volume (ingest + MERGE dominate at scale).
- That correctness holds at scale (all six outcomes, disposition split, no case-id collisions).
- File count / size and point-lookup latency before vs after OPTIMIZE + Z-ORDER.

## Honest constraints (Databricks Free Edition serverless)
- **Photon is always on and cluster sizing is hidden**, so tuning is about *data layout* (OPTIMIZE /
  Z-ORDER, file sizing) and *incremental vs full* recomputation — not cluster-knob tuning.
- **Daily serverless compute cap** — cap the top size to what completes cleanly; stagger big runs.
- Continuous / `ProcessingTime` streaming triggers are **not allowed** on serverless → `Trigger.AvailableNow`
  micro-batch only.
- At ~1M rows the Gold table is a single ~10 MB file, so OPTIMIZE compaction is a no-op; compaction/Z-ORDER
  wins show up at far larger volumes. Reported truthfully rather than inflated.
