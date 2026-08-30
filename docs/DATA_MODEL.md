# Data Model — Settlement Reconciliation Lakehouse

Frozen after roadmap phase **P3**. Downstream surfaces (the Ops Console app, dashboards, reports)
build against this contract. Table/column comments and *enforced* NOT NULL / CHECK constraints are
added in P6 (governance); until then the shapes below are the contract.

## Medallion overview

```
landing (CSV, append-only batches)  ->  Bronze (raw + audit)  ->  Silver (clean, typed, deduped, CDF)  ->  Gold (recon + exception cases)  ->  reports / Ops Console / dashboard
```

## Silver (current-state, Change-Data-Feed enabled)

`silver_internal`, `silver_network` — one row per `txn_id`, upserted via Delta **MERGE** (SCD Type 1)
with an out-of-order guard (`WHEN MATCHED AND s.txn_ts > t.txn_ts`). `delta.enableChangeDataFeed = true`.

| column | type | notes |
|---|---|---|
| txn_id | string | business key |
| business_date | date | |
| channel | string | ATM/POS/ECOM/WALLET/IMPS |
| amount | decimal(18,2) | |
| currency | string | INR |
| status | string | SETTLED/PENDING/FAILED/REVERSED, **nullable** (a null is a real DQ signal, not a reject) |
| account_id | string | |
| txn_ts | timestamp | latest-wins key for the MERGE guard |
| _ingest_ts | timestamp | audit |
| _source_file | string | audit |
| network_ref | string | **network side only** |

`silver_internal_rejects` / `silver_network_rejects` — standardized columns + `reject_reason`
(rejected when `txn_id` null/empty or `amount <= 0`; a null **status** is NOT a reject).

`dq_metrics` — one row per side per run: `run_ts, side, rows_in, rows_clean, rows_rejected, null_status_count`.

## Gold — `gold_recon_results`

Full-outer-join of the two Silver tables on `txn_id`, classified by the shared, unit-tested
`src/recon_logic.reconcile()`. One row per `txn_id` (recomputed each run).

| column | type | notes |
|---|---|---|
| txn_id | string | |
| business_date | date | |
| channel | string | |
| internal_amount / network_amount | decimal | one may be null when a side is missing |
| amount_diff | double | `internal - network`, null when a side is missing |
| internal_status / network_status | string | |
| match_status | string | see classes below |
| reason | string | human-readable explanation |
| disposition | string | AUTO / MANUAL |

**`match_status` classes:** `MATCHED`, `MISMATCH_AMOUNT`, `MISMATCH_STATUS`, `MISMATCH_BOTH`,
`UNMATCHED_INTERNAL`, `UNMATCHED_NETWORK`.
**Tolerances:** amounts equal within `AMOUNT_TOLERANCE = 0.01`; `disposition = AUTO` when
`MISMATCH_AMOUNT` and `abs(amount_diff) <= AUTO_RESOLVE_TOLERANCE = 1.00`, else `MANUAL`.
**Null-status rule:** a null status on either present side classifies as `MISMATCH_STATUS` (never `MATCHED`).

## Gold — `gold_exception_cases` (stable identity + lifecycle, CDF enabled)

One case per non-`MATCHED` reconciliation row, upserted via Delta **MERGE** on `case_key`.

- **`case_key = sha2(concat_ws('|', business_date, txn_id), 256)`** — deterministic, stable across runs.
- **`case_id = 'CASE-' + business_date + '-' + upper(substr(case_key, 1, 12))`** — human-readable, never rewritten.
- `first_seen_ts` set once; `last_updated_ts` bumped on each touch.

| column | type | notes |
|---|---|---|
| case_key | string | sha2 identity (MERGE key) |
| case_id | string | stable human id |
| txn_id, business_date, channel | | |
| case_type | string | = match_status |
| internal_amount, network_amount, amount_diff | | |
| internal_status, network_status, reason, disposition | | carried from recon |
| status | string | **lifecycle** (below) |
| first_seen_ts, last_updated_ts | timestamp | |

**Lifecycle (`status`):**
```
OPEN ─(disposition=AUTO)─▶ AUTO_RESOLVED
OPEN ─(analyst escalate)─▶ MANUAL_REVIEW ─(analyst resolve/approve)─▶ CLOSED
(case absent from latest recon) ─▶ CLOSED_DISAPPEARED   (row kept, never deleted)
```
System recomputes **only** `OPEN`/`AUTO_RESOLVED`; analyst-set `MANUAL_REVIEW`/`CLOSED` are preserved
across re-runs. Validated: `case_id` set signature is byte-identical across repeated pipeline runs.

## Serving (app-owned, local — never written by the pipeline)

- `ops_case_actions` — append-only analyst audit trail (idempotency-keyed).
- `ops_case_state` — current analyst status per `case_id` (upsert). Effective status =
  `COALESCE(ops_case_state.status, gold_exception_cases.status)`.

## Reports (`gold_report_*`)

`funding_by_channel` (business_date × channel), `cash_flow` (business_date), `exception_summary`
(case_type × disposition) — all `overwrite`, idempotent, sourced from Silver internal + Gold exceptions.

## Enforcement (P6, not yet applied)

NOT NULL + CHECK constraints will be **enforced** on Silver/Gold only (not Bronze/landing, which must
accept intentionally-dirty synthetic rows). PK/FK will be **informational only** (no `RELY`).
