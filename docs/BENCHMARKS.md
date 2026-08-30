# Benchmarks

All numbers are **measured** on Databricks Free Edition (serverless), read from job-run metadata — not
estimated. Reproduce with the git-sourced job at the given `rows` (see `docs/SCALE_LAB.md`).

## End-to-end pipeline, per phase (wall-clock, seconds)

| Phase | 20,000 rows | 1,000,000 rows |
|---|---:|---:|
| P1 generate | ~4 | 32 |
| P2 Bronze (Auto Loader) | ~9 | 57 |
| P3 Silver (MERGE SCD1 + CDF) | ~8 | 52 |
| P4 Gold (reconciliation) | ~6 | 33 |
| P5 reports | ~5 | 28 |
| **Total (exec)** | **~32** | **~202 (3.4 min)** |

*(20k figures are approximate from earlier runs; 1M figures are exact from run metadata.)*
Scaling from 20k→1M is 50× the data for roughly 6× the wall-clock — sub-linear, i.e. mostly fixed
serverless/query overhead at 20k, real work at 1M. Bronze + Silver dominate at scale (ingest + MERGE).

## Correctness holds at scale (1,000,000 rows)

| match_status | count | share |
|---|---:|---:|
| MATCHED | 885,395 | 88.1% |
| UNMATCHED_INTERNAL | 49,694 | 4.9% |
| MISMATCH_AMOUNT | 30,031 | 3.0% |
| MISMATCH_STATUS | 24,828 | 2.5% |
| MISMATCH_BOTH | 10,052 | 1.0% |
| UNMATCHED_NETWORK | 5,000 | 0.5% |

- `gold_recon_rows` = 1,005,000 (1,000,000 internal + 5,000 network-only phantoms).
- exception cases = **119,605**, distinct case_ids = **119,605** → zero id collisions at 1M.
- disposition: AUTO 10,003 / MANUAL 109,602; lifecycle OPEN 109,602 / AUTO_RESOLVED 10,003.
- Proportions match the 20k run (~5% dropped, ~1% auto, 0.5% phantom) — the generator scales cleanly.

## OPTIMIZE + Z-ORDER (gold_recon_results @ 1M)

| metric | before | after |
|---|---:|---:|
| files | 1 | 1 |
| size | 10.24 MB | 10.24 MB |
| point-lookup (`WHERE txn_id = …`) | 1.103 s | 0.685 s |
| OPTIMIZE duration | — | 1.92 s |

**Honest reading:** at 1M rows the Gold output is a single ~10 MB Delta file, so `OPTIMIZE` had nothing to
compact. The Z-ORDER point-lookup improvement (~38%) is real but small and single-file. File compaction /
Z-ORDER pays off at much larger volumes or with many small append files; at this scale the win is the
**incremental MERGE** (only changed keys) versus full recompute, not file layout. Measuring this — and
reporting it truthfully rather than claiming a big OPTIMIZE win — is the point of the lab.
