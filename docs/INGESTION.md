# Real-source ingestion (S3 → Unity Catalog Volume → medallion)

This project's **default** source is the synthetic generator (`notebooks/01_...`). This document
adds a **real ingestion path**: transactions arrive over an HTTP API (or as file drops), land in an
**Amazon S3** bucket, and are relayed into the Unity Catalog **Volume** that Bronze already reads.

```
 file drop ─┐
 API POST ──┼──► serving/ingest_api.py ──► s3://<bucket>/landing/{internal,network}/business_date=…/batch=…/part-*.csv
 /manual ───┘                                        │
                                                      │  serving/s3_to_volume_sync.py  (runs OUTSIDE Databricks)
                                                      ▼
                    /Volumes/workspace/settlement_recon/landing/{internal,network}/…    ← Bronze reads this, unchanged
                                                      ▼
                         Bronze (Auto Loader) → Silver (MERGE) → Gold (reconcile) → exception cases
```

## Why the relay exists (honest note)
Databricks **Free Edition** serverless **cannot read an external S3 bucket** — external-location reads
fail with `403 … serverless network policy` because the egress allowlist is account-console-only, which
Free Edition doesn't expose. So S3 stays the real landing zone and a small **external relay**
(`serving/s3_to_volume_sync.py`) mirrors new objects into a managed UC Volume that Free Edition *can*
read. On a paid workspace you'd instead point a UC external location straight at the bucket and delete
the relay.

## Schema contract (must match the generator, enforced by the API)
Silver's `standardize()` references these columns literally, so the CSVs the API lands carry exactly:

| Feed | Columns |
|---|---|
| internal | `txn_id, business_date, channel, amount, currency, status, account_id, txn_ts` |
| network | the above **+ `network_ref`** |

## One-time setup (you run these — secrets never go in chat or the repo)

**Bucket:** `db-source-suripk`  ·  **Region:** `ap-south-1`
**Volume:** `/Volumes/workspace/settlement_recon/landing` (created by `notebooks/01`; already exists)

```bash
# AWS credentials for boto3 (local dev) — your keys stay on your machine
aws configure   # enter your Access Key / Secret / region ap-south-1

# A fresh Databricks personal access token (the CLI token in prior runs has expired)
#   Databricks workspace → Settings → Developer → Access tokens → Generate
```

```bash
export SETTLEMENT_S3_BUCKET=db-source-suripk
export AWS_REGION=ap-south-1
export SETTLEMENT_S3_PREFIX=landing
export DATABRICKS_HOST=https://dbc-fadc3588-499e.cloud.databricks.com
export DATABRICKS_TOKEN=****                                   # your fresh PAT
export VOLUME_PATH=/Volumes/workspace/settlement_recon/landing
```
(PowerShell: `$env:SETTLEMENT_S3_BUCKET="db-source-suripk"`, etc.)

## Run it end-to-end

**1 — Start the ingestion API and land some data in S3**
```bash
pip install -r serving/requirements-ingest.txt
uvicorn serving.ingest_api:app --reload      # http://127.0.0.1:8000  ·  form at /manual
```
```bash
# a matched internal+network pair (same txn_id) + one internal-only break
curl -X POST localhost:8000/api/v1/ingest/internal -H 'content-type: application/json' -d '[
 {"txn_id":"TXN1001","business_date":"2026-06-30","channel":"POS","amount":"120.50","currency":"INR","status":"SETTLED","account_id":"AC900001","txn_ts":"2026-06-30T10:15:00Z"},
 {"txn_id":"TXN1002","business_date":"2026-06-30","channel":"ATM","amount":"80.00","currency":"INR","status":"SETTLED","account_id":"AC900002","txn_ts":"2026-06-30T10:16:00Z"}]'

curl -X POST localhost:8000/api/v1/ingest/network -H 'content-type: application/json' -d '[
 {"txn_id":"TXN1001","business_date":"2026-06-30","channel":"POS","amount":"120.50","currency":"INR","status":"SETTLED","account_id":"AC900001","txn_ts":"2026-06-30T10:15:02Z","network_ref":"NET1001"}]'
# TXN1002 is absent from network → becomes an UNMATCHED_INTERNAL exception case.
```

**2 — Relay S3 → the UC Volume**
```bash
pip install -r serving/requirements-sync.txt
python serving/s3_to_volume_sync.py --once --dry-run    # preview what would upload
python serving/s3_to_volume_sync.py --once              # do it
```

**3 — Run the pipeline** (Bronze → Silver → Gold) as usual — a git-sourced Databricks job or the
notebooks in the workspace. Bronze reads the Volume it always has; nothing to change.

**4 — Serve it** — the FastAPI Ops Console + Streamlit dashboard over the Gold snapshot (`serving/`).

## Automating the relay (optional, no laptop needed)
`.github/workflows/s3-sync.yml` runs the relay on a schedule via **AWS OIDC** (no stored AWS keys):
1. Create an IAM role trusting GitHub's OIDC provider with a read-only policy on the bucket.
2. Repo **Settings → Secrets and variables → Actions**:
   - Variables: `SETTLEMENT_S3_BUCKET`, `SETTLEMENT_S3_PREFIX=landing`, `VOLUME_PATH`
   - Secrets: `AWS_ROLE_ARN`, `DATABRICKS_HOST`, `DATABRICKS_TOKEN`
The workflow **skips** until `SETTLEMENT_S3_BUCKET` is set, so it never shows red while unconfigured.
