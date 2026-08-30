# Run log

Actual pipeline executions on Databricks Free Edition (serverless), via git-sourced jobs that clone
`github.com/surendhar-333/build@main` at run time.

| Date | Rows | Result | Notes |
|---|---:|---|---|
| 2026-08-30 | 20,000 | ✅ all phases SUCCESS | P1 end-to-end validation; all 6 outcomes + AUTO/null/phantom |
| 2026-08-30 | 20,000 ×2 | ✅ | P2 idempotency: re-run left Silver counts identical (20,000 / 19,094), CDF on |
| 2026-08-30 | 20,000 ×2 | ✅ | P3 stable identity: `case_id` set signature byte-identical across runs |
| 2026-08-30 | 1,000,000 | ✅ all phases SUCCESS | P5 scale lab; ~3.4 min end-to-end; correctness held (see BENCHMARKS.md) |

Verification is machine-readable: notebooks emit a JSON run summary via `dbutils.notebook.exit()` (row
counts, match-status distribution, disposition/lifecycle counts, a `case_id` set signature), captured
from the job run output.
