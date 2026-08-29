# TASK-03 — Make exception cases have real identity and history

**Branch:** `fix/case-identity`
**File to change:** `notebooks/04_phase4_gold_reconciliation.py` only.
**Prerequisite:** TASK-02 merged (you need all six `match_status` values firing).

## Why

This is the most serious design defect in the repo.

**1. `case_id` is positional, not identity-based.** It is built as:

```python
w = Window.partitionBy("business_date").orderBy("txn_id")
case_id = concat(lit("CASE-"), business_date, lit("-"), lpad(row_number().over(w), 8, "0"))
```

`row_number()` is assigned by position. If a single exception is resolved or a new one
appears, **every later case renumbers**. `CASE-2026-06-30-00000042` refers to a
different transaction after the next run. A comment in the notebook currently claims
the IDs are "deterministic and stable across reruns" — that claim is false.

For an exception-management table, stable case identity *is* the product.

**2. There is no case history.** `gold_exception_cases` is written with
`mode("overwrite")` every run, and `created_ts = current_timestamp()` is re-stamped
each time. So:
- any analyst work (assigned owner, resolution note, manual override) is destroyed
- case age is unrecoverable — you cannot answer "how long has this been open?"
- a case that was resolved upstream silently vanishes with no record it ever existed

**3. The window is a scale bomb.** `Window.partitionBy("business_date")` on a
single-date dataset means **one Spark partition**. At `rows=10000000` that funnels
~900k rows through a single task for `row_number()` — guaranteed skew and spill, and a
plausible OOM. Removing `row_number()` removes this too.

## What to change

### 1. Stable, content-derived `case_id`
Derive the ID from the **business identity of the case**, not its position. The
natural key is `(business_date, txn_id, case_type)`. Use a hash, e.g.
`F.sha2(F.concat_ws("||", business_date, txn_id, case_type), 256)`, and keep a readable
prefix so it still looks like a case reference — for example
`CASE-{business_date}-{first 12 hex chars}`.

Requirements:
- Same transaction + same date + same case type ⇒ **same `case_id`**, every run,
  regardless of what else changed.
- Different `case_type` for the same txn ⇒ different `case_id` (a transaction whose
  problem changes from `MISMATCH_AMOUNT` to `MISMATCH_BOTH` is arguably a new case —
  state that decision in a comment either way).
- Remove the `Window` / `row_number()` entirely.
- Note the collision trade-off in a comment (truncated hash ⇒ birthday bound).

### 2. Switch to `MERGE` with case lifecycle columns
Replace the blanket `overwrite` with a `MERGE INTO` on `case_id`. Extend the schema:

| Column | Behaviour |
|---|---|
| `case_id` | merge key |
| `first_seen_ts` | set on insert, **never updated** |
| `last_seen_ts` | set to current run timestamp on insert and on match |
| `case_status` | `OPEN` on insert; `OPEN` while still detected; `RESOLVED` when no longer detected |
| `resolved_ts` | set when transitioning to `RESOLVED`, else null |
| `disposition` | as today (`AUTO` / `MANUAL`) — recompute on match |

Keep all existing columns. Semantics:

- **New exception** → insert, `case_status = OPEN`, `first_seen_ts = last_seen_ts = now`
- **Still-present exception** → update `last_seen_ts`, amounts, statuses, `reason`,
  `disposition`; **do not touch** `first_seen_ts`
- **Previously-open case no longer detected** → set `case_status = RESOLVED`,
  `resolved_ts = now`; do not delete the row

The third branch needs care: a plain `MERGE` on the incoming set won't see rows that
disappeared. Handle it explicitly (a second statement scoped to the same
`business_date` is fine) and **comment why two statements are needed** — this is
exactly the kind of thing you'll be asked to justify.

Because the table must now be created before it can be merged into, add an idempotent
`CREATE TABLE IF NOT EXISTS` with the full explicit schema instead of relying on
`saveAsTable` to infer it.

### 3. Null-safe status comparison (small, include it here)
Currently:

```python
status_mismatch = present_i & present_n & (F.col("i.status") != F.col("n.status"))
```

If either `status` is NULL, the comparison is NULL, no `when` branch fires, and the row
falls through to `.otherwise("MATCHED")` — **a record with a missing status is
silently reported as reconciled.** Silver only DQ-checks `txn_id` and `amount`, so
nothing upstream prevents this. Use `eqNullSafe`:

```python
status_mismatch = present_i & present_n & ~F.col("i.status").eqNullSafe(F.col("n.status"))
```

Add a markdown note stating the decision: is "one side has no status" a mismatch or a
data-quality reject? Pick one and justify it.

## Constraints

- `gold_recon_results` keeps its current schema — this task only changes
  `gold_exception_cases` (plus the null-safe fix in the classification chain).
- Phase 5 reads `gold_exception_cases` for `gold_report_exception_summary` and
  references `case_type`, `disposition`, `amount_diff`. Do not break those columns.
- Must remain idempotent: running Phase 4 twice with unchanged inputs must leave
  `first_seen_ts` untouched and produce no duplicate `case_id`s.
- No new packages.

## Acceptance criteria

- [ ] Run Phase 4 twice without changing inputs. `first_seen_ts` is **identical** in
      both runs; `last_seen_ts` advances; row count unchanged; no duplicate `case_id`.
- [ ] `SELECT COUNT(*) - COUNT(DISTINCT case_id) FROM gold_exception_cases` = 0
- [ ] Re-run Phase 1 with a different `rows` value so some exceptions disappear, then
      Phase 2→4. Cases that vanished are `case_status = 'RESOLVED'` with a non-null
      `resolved_ts`, **not** deleted.
- [ ] The `case_id` for a given `(business_date, txn_id, case_type)` is byte-identical
      across runs — demonstrate with a query on one specific `txn_id`.
- [ ] No `Window` / `row_number()` remains in the notebook.
- [ ] A row where one side's `status` is NULL classifies as a mismatch (or reject) —
      **not** `MATCHED`. Prove it with a seeded example.
- [ ] Phase 5 still runs unchanged.
- [ ] Notebook still opens in Databricks (source format intact).

## Deliverable

Diff to `notebooks/04_phase4_gold_reconciliation.py`, plus:
- the exact `case_id` formula and the collision trade-off
- why the resolved-case transition needs its own statement
- the decision you made about NULL status, and why
- anything else noticed but not changed (under "Also noticed")
