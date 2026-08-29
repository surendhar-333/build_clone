# TASK-02 — Make the generator exercise every code path

**Branch:** `fix/generator-coverage`
**File to change:** `notebooks/01_phase1_data_generation.py` only.
**Do not touch** Phases 2–6.

## Why

Phase 4 defines six `match_status` values and two `disposition` values. With the
current generator, **three of those outcomes are unreachable**, so a third of the
reconciliation logic has never executed and cannot be demonstrated:

1. `MISMATCH_BOTH` — impossible. The amount-mismatch band is `0.05 ≤ r < 0.07` and
   the status-mismatch band is `0.07 ≤ r < 0.09` on the *same* `r`. Disjoint.
2. `UNMATCHED_NETWORK` — impossible. `build_network()` only filters and mutates
   `internal_df`, so network `txn_id`s are always a strict subset of internal.
   Nothing network-only is ever created.
3. `disposition = "AUTO"` — effectively impossible. The amount injection multiplies
   by exactly `1.10`, so `abs(amount_diff) = 0.10 × amount`. Since `amount ≥ 10.00`
   and `AUTO_RESOLVE_TOLERANCE = 1.00`, `AUTO` would need `amount ≤ 10.00`.

Two further defects to fix in the same pass:

4. The status flip sets the **literal** `"SETTLED"`. Status is drawn uniformly from
   4 values, so ~25% of selected rows already read `SETTLED` and the flip silently
   no-ops. `MISMATCH_STATUS` therefore lands ~1,500 instead of the documented 2,000.
5. `amount`, `status` and `account_id` all derive from the **same** `h = abs(hash(seq))`.
   Because `499000 % 4 == 0`, `(h % 499000) % 4 == h % 4`, which means **`status` is a
   deterministic function of the paise digits of `amount`**. `channel` is `seq % 5`,
   i.e. determined by the last digit of `txn_id`. This makes the dataset useless for
   any analysis and is embarrassing if spotted.

## What to change

### 1. Independent randomness per field
Replace the single `h` with separately salted hashes, e.g.
`F.pmod(F.hash(F.col("seq"), F.lit(11)), F.lit(...))` for amount,
`F.lit(13)` for status, `F.lit(17)` for account_id, `F.lit(19)` for channel.

Use **`F.pmod`, not `F.abs`** — `abs(Integer.MIN_VALUE)` is still negative, which can
produce a negative `amount` (~1 row in 4 billion; it shows up at 10M scale across
repeated runs).

### 2. Re-band the injector so every outcome is reachable
Keep one uniform `r` derived from `txn_id`, but redesign the bands so they are
**explicit, non-overlapping-by-construction where intended, and overlapping where
`MISMATCH_BOTH` is wanted.** Suggested layout — you may adjust the widths, but every
band must be reachable and the rates must be named constants, not magic numbers:

| Band | Effect | Produces |
|---|---|---|
| drop | row removed from network | `UNMATCHED_INTERNAL` |
| amount-only | amount inflated beyond tolerance | `MISMATCH_AMOUNT` |
| **amount-small** | amount changed by a **sub-rupee** delta (e.g. ±0.10 to ±0.90) | `MISMATCH_AMOUNT` with `disposition = AUTO` |
| status-only | status rotated to a *different* value | `MISMATCH_STATUS` |
| **both** | amount inflated **and** status rotated | `MISMATCH_BOTH` |
| clean | untouched | `MATCHED` |

Plus a **network-only injection**: synthesise a small number of records that exist
*only* on the network side (new `txn_id`s that were never in internal) and union them
into the network frame. That is the only way to produce `UNMATCHED_NETWORK`.

### 3. Rotate status instead of overwriting
Status mismatch must move the value to a **different** status, e.g. index into the
`STATUSES` array at `(current_index + 1) % 4`, so the flip never no-ops.

### 4. Seed deliberate bad rows so the DQ path is proven
Right now `silver_*_rejects` is always empty, so Phase 3's reject machinery has never
run. Add a small, **named-constant** fraction of rows that violate Silver's DQ rules:
- `txn_id` null or empty string
- `amount` null, zero, or negative

Put them on **both** sides. Keep the rate low (e.g. 0.1%) so the recon numbers stay
readable.

### 5. Remove the `coalesce(1)` scale ceiling
`.coalesce(1)` forces a single output file, which also makes Auto Loader ingest
single-threaded — in a project whose stated purpose includes a scale lab. Replace with
a row-count-aware choice: keep 1 file for small runs (easy inspection), but let Spark
write multiple files above a threshold. Make the threshold a named constant and
comment the trade-off.

### 6. Avoid recomputing the dataset four times
Phase 1 currently triggers ~4 full recomputations (two `write`s, two `count`s, plus
`show`s) with no caching, and `network_df` re-derives `internal_df` each time. Cache
or otherwise avoid the repeated work, and comment why. This directly pollutes the
timings that `gold_scale_log` is supposed to measure.

## Constraints

- Keep the CSV **column order and names** exactly as they are — Phase 2 infers schema
  from these files and Phase 3 projects explicit column lists.
- Keep the output paths and the `business_date=` folder convention unchanged.
- Keep the `rows` widget (`dbutils.widgets.text("rows", "100000")`) working.
- All rates must be **named constants in the config cell**, with a comment stating
  the expected resulting count at `rows=100000`.
- Do not change `BUSINESS_DATE` handling in this task (multi-date is a later task).

## Acceptance criteria

Run Phases 1→4 at `rows=100000`, then
`SELECT match_status, COUNT(*) FROM workspace.settlement_recon.gold_recon_results GROUP BY 1`.

- [ ] **All six** `match_status` values appear with a non-zero count:
      `MATCHED`, `UNMATCHED_INTERNAL`, `UNMATCHED_NETWORK`, `MISMATCH_AMOUNT`,
      `MISMATCH_STATUS`, `MISMATCH_BOTH`
- [ ] `SELECT disposition, COUNT(*) FROM gold_exception_cases GROUP BY 1` returns
      **both** `AUTO` and `MANUAL` with non-zero counts
- [ ] `silver_internal_rejects` and `silver_network_rejects` are **both non-empty**,
      and `SELECT reject_reason, COUNT(*) ... GROUP BY 1` shows both
      `null_or_empty_txn_id` and `amount_le_zero_or_null`
- [ ] `MISMATCH_STATUS` count is within ~10% of the configured rate (proving the
      no-op flip is fixed)
- [ ] `SELECT COUNT(DISTINCT status) FROM silver_internal GROUP BY <paise of amount>`
      no longer shows a 1:1 relationship between amount and status
- [ ] Phase 1 still accepts the `rows` widget and still runs at `rows=1000`
- [ ] Every rate is a named constant with a comment predicting its row count
- [ ] Notebook still opens correctly in Databricks (source format intact)

## Deliverable

A diff to `notebooks/01_phase1_data_generation.py` plus a short summary of:
- the band layout you chose and why
- the expected count for each of the six `match_status` values at `rows=100000`
- anything you noticed but did not change (under "Also noticed")
