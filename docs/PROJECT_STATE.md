# Project state — Payment Settlement & Reconciliation Lakehouse

Honest assessment as of 2026-08-22. Read this before touching anything.

## What this is

A medallion pipeline on Databricks that reconciles an **internal** settlement feed
against a **network/bank** feed and raises exception cases.

```
Phase 1  generate synthetic internal + network CSVs (with injected discrepancies)
Phase 2  Auto Loader  → bronze_internal, bronze_network
Phase 3  standardise/DQ/dedupe → silver_*, silver_*_rejects
Phase 4  FULL OUTER JOIN on txn_id → gold_recon_results, gold_exception_cases
Phase 5  aggregate → 3 gold_report_* tables
Phase 6  orchestration + scale lab → gold_scale_log
```

Storage is a **Unity Catalog volume** — `/Volumes/workspace/settlement_recon/landing`.
**There is no Azure anywhere in this repo.** The Azure/Debezium/Event Hubs design is
the separate `settlement-platform` repo, which is documentation only with no code.

## Ground truth

**Phases 01→05 are a real, working, end-to-end pipeline** producing 11 populated
Delta tables. That is the defensible core.

**But nothing here has ever been run.** No committed output, no row counts, no
filled-in scale numbers. `AGENT_CONTEXT.md` still says the next action is yours.
Treat everything as "should run", not "has run".

**Phase 6 barely does anything.** `run_pipeline(...)` and `timed_run(...)` are both
commented out on purpose. The multi-task Job spec is a JSON block in a markdown cell
with a placeholder git URL — never deployed. The only real work Phase 6 performs is
`OPTIMIZE ... ZORDER BY (txn_id)`.

**Zero tests, zero CI.** `jules/tasks/TASK-01-recon-tests.md` specifies
`src/recon_logic.py` and `tests/test_recon_logic.py`. Neither exists.

---

## The table contract

Everything in `workspace.settlement_recon`.

| Table | Created by | Key columns |
|---|---|---|
| `bronze_internal` / `bronze_network` | Phase 2 | 8 CSV cols (+`network_ref` on network) + `_rescued_data`, `_ingest_ts`, `_source_file`. Types are **Auto Loader–inferred**, not declared. |
| `silver_internal` / `silver_network` | Phase 3 | `txn_id`, `business_date` date, `channel`, `amount` **decimal(18,2)**, `currency`, `status`, `account_id`, `txn_ts`, `_ingest_ts`, `_source_file` (+`network_ref`) |
| `silver_*_rejects` | Phase 3 | all bronze cols + `reject_reason` |
| `gold_recon_results` | Phase 4 | `txn_id`, `business_date`, `channel`, `internal_amount`, `network_amount`, `amount_diff` double, `internal_status`, `network_status`, `match_status`, `reason` |
| `gold_exception_cases` | Phase 4 | `case_id`, `txn_id`, `business_date`, `channel`, `case_type`, amounts, `amount_diff`, statuses, `disposition`, `reason`, `created_ts` |
| `gold_report_funding_by_channel` / `_cash_flow` / `_exception_summary` | Phase 5 | aggregates |
| `gold_scale_log` | Phase 6 | `run_rows`, `phase`, `seconds`, `ts` |

### The recon logic (Phase 4) — know this cold

- **Join:** `FULL OUTER` on **`txn_id` only**. `business_date` and `channel` are
  coalesced afterwards, not joined on. `network_ref` is never used as a key.
- **Tolerances:** `AMOUNT_TOLERANCE = 0.01` (strictly `>`, so a 1-paisa diff counts
  as matched), `AUTO_RESOLVE_TOLERANCE = 1.00`.
- **Classification order matters** — unmatched branches fire first:
  `UNMATCHED_INTERNAL` → `UNMATCHED_NETWORK` → `MISMATCH_BOTH` →
  `MISMATCH_AMOUNT` → `MISMATCH_STATUS` → `MATCHED`
- **`amount_diff` = internal − network.** The injector inflates the *network* side,
  so real mismatches come out **negative**.
- **`case_id`** = `CASE-{business_date}-{row_number:08d}`, where `row_number()` is
  ordered by `txn_id` within `business_date`.
- **`disposition`** = `AUTO` only when `case_type == MISMATCH_AMOUNT` **and**
  `abs(amount_diff) <= 1.00`. Everything else is `MANUAL`.

---

## Defects an interviewer will find

Ranked by how badly they'd hurt you. These are not hypothetical — they're in the code.

### P0 — makes a third of the logic provably dead

1. **`MISMATCH_BOTH` can never happen.** Phase 1 puts the amount-mismatch band at
   `0.05 ≤ r < 0.07` and the status-mismatch band at `0.07 ≤ r < 0.09` on the *same*
   `r`. Disjoint — no row is ever both. Phase 4 has a whole branch and reason string
   that never execute.
2. **`UNMATCHED_NETWORK` can never happen.** `build_network()` only filters and
   mutates `internal_df`, so network `txn_id`s are a strict subset of internal.
   No network-only record is ever created.
3. **`AUTO` disposition is effectively dead.** The injector inflates by exactly
   **10%**, so `abs(amount_diff) = 0.10 × amount`. With `amount ≥ 10.00` and
   `AUTO_RESOLVE_TOLERANCE = 1.00`, `AUTO` needs `amount ≤ 10.00` — essentially
   never. Expect **~0 AUTO out of ~8,500 cases**, i.e. the "tolerance-based
   auto-disposition" story has no evidence behind it.
4. **`case_id` is not stable across runs** — and a comment in Phase 4 claims it is.
   `row_number()` is positional: resolve one case and every later case renumbers.
   `CASE-2026-06-30-00000042` points at a different transaction next run. For an
   exception-management table this is the most serious defect in the repo.

### P1 — correctness

5. **Null status is silently reported as MATCHED.** `i.status != n.status` yields
   NULL when either side is null, no `when` branch fires, and it falls through to
   `.otherwise("MATCHED")`. Silver only DQ-checks `txn_id` and `amount`, so nothing
   upstream prevents it. Needs `eqNullSafe`.
6. **Re-running Phase 1 doubles Bronze.** Phase 1 overwrites the CSV directory but
   the new file has a new UUID name; Auto Loader tracks *filenames*, so it re-ingests.
   `bronze_internal` goes 100k → 200k. Phase 2's own docs claim it's idempotent —
   true only if Phase 1 isn't re-run, which is exactly what Phase 6 does.
7. **~25% of status flips no-op.** The flip sets the literal `"SETTLED"`, and status
   is uniform over 4 values, so a quarter already read `SETTLED`. `MISMATCH_STATUS`
   lands ≈1,500, not the documented 2,000.
8. **Reject tables are always empty.** Every generated `txn_id` is non-null and every
   `amount ≥ 10.00`, so the DQ machinery is written but never proven to work.
9. **Money becomes `double`** in Bronze (inferred), in `amount_diff`, and in every
   Phase 5 aggregate — despite Silver correctly using `decimal(18,2)`.
10. **`total_amount_diff` is 0.0 for unmatched cases.** `amount_diff` is NULL for
    `UNMATCHED_*`, so the financial exposure of ~5,000 missing transactions reports
    as **zero**. Should use `internal_amount`.

### P2 — design / scale

11. **`coalesce(1)` in Phase 1 is a hard scale ceiling** — one task, one file, and
    therefore single-threaded Auto Loader ingest too. In a notebook whose purpose is
    a *scale lab*.
12. **`Window.partitionBy("business_date")` on single-date data = one partition.**
    At 10M rows ~900k exception rows funnel into a single task for `row_number()`.
    Guaranteed skew/spill, plausible OOM.
13. **`gold_exception_cases` is fully overwritten each run** — no MERGE, no case
    state, `created_ts` re-stamped every time. Any analyst work would be destroyed.
14. **Hidden functional dependency:** `amount`, `status` and `account_id` all derive
    from the same `hash(seq)`, and `499000 % 4 == 0`, so **status is determined by the
    paise of amount**. `channel` is just `seq % 5`. Any analytics on this is
    meaningless. Fix with distinct hash salts.
15. **Everything is one business date** (`2026-06-30`, hardcoded). No late-arriving
    scenario exists, despite docs claiming Silver "handles late files".
16. **Config copy-pasted into six notebooks**, four of them dead code
    (`VOLUME_ROOT` is unused in Phases 3–6).
17. **Phase 6 docs are wrong** — they claim Phase 1 hardcodes its row count and has
    no widget. It does have a `rows` widget. Anyone reading Phase 6 concludes the
    scale lab isn't wired.
18. **`abs(hash(...))` can be negative** (`abs(Integer.MIN_VALUE)`), producing a
    negative amount roughly 1 row in 4 billion. Use `pmod`.

---

## Moving the landing zone to S3 later

Good news: the blast radius is tiny. **Only Phases 1 and 2 touch storage at all** —
Phases 3–6 are pure table-to-table. There are **zero `dbutils.fs` calls** in the repo.

1. Repoint `VOLUME_ROOT` in `01` and `02` from `/Volumes/...` to `s3://bucket/prefix`.
   `INTERNAL_PATH`, `NETWORK_PATH`, `SCHEMAS_ROOT`, `CHECKPOINTS_ROOT` all derive
   from it — no other edits.
2. Delete `CREATE VOLUME IF NOT EXISTS` in Phase 1.
3. Register a UC **Storage Credential** (IAM role) + **External Location**, and grant
   `READ FILES, WRITE FILES`. **None of this auth plumbing exists in the repo** — it's
   net-new config, not an edit. On serverless this is the only viable route.
4. **Mandatory Bronze rebuild.** A new `checkpointLocation` is a new stream identity,
   so Auto Loader re-ingests everything. Drop `bronze_*` and delete the old
   `_schemas/*` and `_checkpoints/*` first.
5. Keep `cloudFiles.partitionColumns=""` — the `business_date=` folder convention is
   unchanged and would otherwise collide with the real CSV column.

**This cannot be done on Databricks Free Edition.** Free Edition is serverless-only
(no instance profiles) with restricted outbound internet, and you don't control the
account-level IAM trust. Options are the 14-day Free Trial deployed into your own AWS
account, or a separate AWS-native pipeline (S3 + Glue/EMR Serverless + Athena).

---

## Fix order

Do them in this order. Rationale: kill the dead code paths first, because those are
what an interviewer trips over in the first five minutes.

| # | Task | Why first |
|---|---|---|
| 1 | Fix the generator: all 6 `match_status` values reachable, `AUTO` fires, seeded bad rows | A third of your logic is currently unreachable |
| 2 | Stable `case_id` (hash, not `row_number`) + `MERGE` for cases | Case identity is the substance of the system |
| 3 | Null-safe status comparison | Silent false-MATCHED is a correctness bug |
| 4 | Consolidate config into one place; fix Phase 6's stale docs | Removes the "why is this pasted 6 times" question |
| 5 | Unit tests for the recon logic (TASK-01, already specced) | Biggest single differentiator at your level |
| 6 | Actually run the scale lab; fill in the war story | The project's stated centerpiece is three ellipses |
| 7 | AWS-native S3 pipeline (separate track) | Earns the S3 line on your CV honestly |
