# Data Model & Schema Contract

This document outlines the frozen schema contract for the Databricks payment settlement & reconciliation lakehouse.

## Tolerances
- **AMOUNT_TOLERANCE**: `0.01`
- **AUTO_RESOLVE_TOLERANCE**: `1.00`

## Match Status Values
The six possible match status values are:
- `MATCHED`
- `MISMATCH_AMOUNT`
- `MISMATCH_STATUS`
- `MISMATCH_BOTH`
- `UNMATCHED_INTERNAL`
- `UNMATCHED_NETWORK`

## Schemas

### INTERNAL

| Column Name | Data Type | Description |
|---|---|---|
| txn_id | str | |
| business_date | date | |
| channel | str | |
| amount | decimal(18,2) | |
| currency | str | |
| status | str | |
| account_id | str | |
| txn_ts | timestamp | |

### NETWORK

This is the `INTERNAL` schema plus `network_ref:str`.

| Column Name | Data Type | Description |
|---|---|---|
| txn_id | str | |
| business_date | date | |
| channel | str | |
| amount | decimal(18,2) | |
| currency | str | |
| status | str | |
| account_id | str | |
| txn_ts | timestamp | |
| network_ref | str | |

### gold_recon_results

| Column Name | Data Type | Description |
|---|---|---|
| txn_id | str | |
| business_date | date | |
| channel | str | |
| internal_amount | decimal(18,2) | |
| network_amount | decimal(18,2) | |
| amount_diff | decimal(18,2) | |
| internal_status | str | |
| network_status | str | |
| match_status | str | |
| reason | str | |
| disposition | str | |

### gold_exception_cases

| Column Name | Data Type | Description |
|---|---|---|
| case_key | str | |
| case_id | str | |
| txn_id | str | |
| business_date | date | |
| channel | str | |
| case_type | str | |
| internal_amount | decimal(18,2) | |
| network_amount | decimal(18,2) | |
| amount_diff | decimal(18,2) | |
| internal_status | str | |
| network_status | str | |
| disposition | str | |
| reason | str | |
| status | str | |
| first_seen_ts | timestamp | |
| last_updated_ts | timestamp | |
