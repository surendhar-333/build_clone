# STAR interview stories (from the real work)

Each is a defensible Situation-Task-Action-Result. All map to committed code.

## 1. The silent NULL-status false-match
- **S:** A reconciliation should flag when internal and network statuses disagree.
- **T:** Discrepancy cases where one side's status was NULL were being reported as MATCHED.
- **A:** Root-caused it to Spark three-valued logic (`a != b` → NULL when either is NULL, falling through
  to the MATCHED else-branch). Made the comparison null-aware and added a regression test in the module
  the notebooks import.
- **R:** The 89 injected null-status rows now classify correctly as MISMATCH_STATUS; a green test pins it
  so it can't regress.

## 2. Tests that didn't guard production
- **S:** There was a unit-tested reconciliation module *and* an inline copy of the same logic in the Gold
  notebook.
- **T:** The two could drift silently — the tests proved nothing about what actually ran.
- **A:** Made the notebook import the shared module and deleted the duplicate; verified by grep that no
  second classifier survived; the null-fix then reached production through the same code path.
- **R:** One source of truth; the CI pytest suite now genuinely guards the pipeline's classification.

## 3. Re-running the pipeline corrupted state
- **S:** Silver was a full overwrite; re-running generation re-ingested files and full-recomputed.
- **T:** Make ingestion/processing idempotent and incremental without losing correctness.
- **A:** Append-only batch landing + Auto Loader checkpoints (exactly-once ingest); Silver rewritten as a
  Delta MERGE (SCD Type 1) keyed on `txn_id` with a `txn_ts` out-of-order guard; enabled Change Data Feed.
- **R:** Re-running twice left Silver counts *exactly* stable (20,000 / 19,094) and Gold identical —
  idempotency proven by re-run, not asserted.

## 4. Exception cases that renumbered themselves
- **S:** Case IDs were positional row numbers.
- **T:** Analysts' notes/dispositions must stay attached to the same real break across runs.
- **A:** Stable `sha2(business_date|txn_id)` case key + a MERGE that preserves `case_id`/`first_seen_ts`
  and analyst-set states (MANUAL_REVIEW/CLOSED), recomputing only system states; disappeared cases go to
  CLOSED_DISAPPEARED rather than being deleted.
- **R:** The case-id set signature is byte-identical across re-runs; a re-run no longer reshuffles cases.

## 5. "Where does a human actually use this?"
- **S:** The pipeline produced tables but no one could *work* the breaks.
- **T:** Show the operational consumer, not just the data.
- **A:** Built a FastAPI ops console: a triage queue (aging/amount sort, channel/type filters), a case
  detail with internal-vs-network comparison, and a disposition write-back that appends an idempotent
  audit row then upserts state inside a transaction.
- **R:** A working analyst workflow; a double-submit writes exactly one audit row (verified), and resolved
  cases drop out of the active queue.

## 6. Making it run on serverless (constraints as a story)
- **S:** Databricks Free Edition serverless has real limits.
- **T:** Keep everything runnable and free.
- **A:** Hit and handled: `.cache()`/`.persist()` unsupported on serverless (removed, relying on
  determinism); `Trigger.AvailableNow` instead of continuous/ProcessingTime; ran the whole pipeline as a
  git-sourced job so the private repo (with the shared `src/` module) is cloned at run time.
- **R:** A 100%-free, reproducible pipeline that a reviewer can run from the repo with no setup.
