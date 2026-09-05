import csv
import hashlib
import io
import os
import re
from datetime import date, datetime, timezone
from decimal import Decimal

import boto3
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse
from jinja2 import Template
from pydantic import BaseModel, Field, model_validator

# ENV-ONLY config
SETTLEMENT_S3_BUCKET = os.environ.get("SETTLEMENT_S3_BUCKET", "")
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
SETTLEMENT_S3_PREFIX = os.environ.get("SETTLEMENT_S3_PREFIX", "landing")

# boto3 default chain, NEVER hardcode creds
s3_client = boto3.client("s3", region_name=AWS_REGION)

app = FastAPI(title="Ingestion API")


class InternalTxn(BaseModel):
    txn_id: str
    business_date: date
    channel: str
    amount: Decimal = Field(gt=0)
    currency: str = "INR"
    status: str
    account_id: str
    txn_ts: datetime

    @model_validator(mode="after")
    def validate_non_empty(self):
        if not self.txn_id or not self.txn_id.strip():
            raise ValueError("txn_id cannot be empty")
        if not self.account_id or not self.account_id.strip():
            raise ValueError("account_id cannot be empty")
        return self


class NetworkTxn(InternalTxn):
    network_ref: str


def get_secure_filename(filename: str) -> str:
    """Extracts basename and removes any unsafe characters."""
    if not filename:
        raise ValueError("Filename cannot be empty")
    # Extract basename to prevent path traversal
    basename = os.path.basename(filename)
    if not basename:
        raise ValueError("Filename cannot be empty")
    # Reject anything with '..', backslashes or null bytes
    if ".." in basename or "\\" in basename or "\x00" in basename:
        raise ValueError("Invalid filename")
    return basename


def is_valid_feed_name(feed: str) -> bool:
    """Validates the feed name using a regex."""
    return bool(re.match(r"^[a-zA-Z0-9_-]+$", feed))


def upload_txns_to_s3(txns: list[BaseModel], feed: str):
    """
    Groups transactions by business_date, converts to CSV, computes SHA256 content hash,
    and uploads to S3 with the idempotent key.
    """
    if not txns:
        return

    # Group by business_date
    grouped = {}
    for txn in txns:
        bdate_str = txn.business_date.isoformat()
        if bdate_str not in grouped:
            grouped[bdate_str] = []
        grouped[bdate_str].append(txn)

    for bdate_str, group_txns in grouped.items():
        # Sort transactions deterministically
        group_txns.sort(key=lambda t: (t.txn_id, getattr(t, "account_id", "")))

        # Determine batch time as max txn_ts in the group for idempotency
        max_ts = max(t.txn_ts for t in group_txns)
        if max_ts.tzinfo is None:
            max_ts = max_ts.replace(tzinfo=timezone.utc)
        batch_time = max_ts.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

        # Get headers from the first model
        headers = list(group_txns[0].model_dump().keys())

        # Write to in-memory CSV string
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=headers)
        writer.writeheader()
        for t in group_txns:
            writer.writerow(t.model_dump())

        csv_content = output.getvalue()

        # Calculate short sha256 hash (first 12 chars)
        hash_val = hashlib.sha256(csv_content.encode("utf-8")).hexdigest()[:12]

        # Construct S3 path
        s3_key = f"{SETTLEMENT_S3_PREFIX}/{feed}/business_date={bdate_str}/batch={batch_time}/part-{hash_val}.csv"

        # Upload to S3
        s3_client.put_object(
            Bucket=SETTLEMENT_S3_BUCKET,
            Key=s3_key,
            Body=csv_content.encode("utf-8"),
            ContentType="text/csv",
        )


@app.post("/api/v1/ingest/internal")
def ingest_internal(txns: list[InternalTxn]):
    upload_txns_to_s3(txns, "internal")
    return {"status": "success", "count": len(txns)}


@app.post("/api/v1/ingest/network")
def ingest_network(txns: list[NetworkTxn]):
    upload_txns_to_s3(txns, "network")
    return {"status": "success", "count": len(txns)}


@app.post("/api/v1/cases/manual")
def ingest_manual(txn: InternalTxn):
    upload_txns_to_s3([txn], "manual")
    return {"status": "success", "count": 1}


@app.post("/api/v1/ingest/file")
async def ingest_file(
    feed: str = Form(...), business_date: date = Form(...), file: UploadFile = File(...)
):
    if not is_valid_feed_name(feed):
        raise HTTPException(status_code=400, detail="Invalid feed name")

    try:
        secure_name = get_secure_filename(file.filename)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    bdate_str = business_date.isoformat()
    batch_time = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    s3_key = (
        f"{SETTLEMENT_S3_PREFIX}/{feed}/business_date={bdate_str}/batch={batch_time}/{secure_name}"
    )

    def upload_stream():
        # boto3 upload_fileobj will stream the file without loading entirely to memory
        s3_client.upload_fileobj(file.file, SETTLEMENT_S3_BUCKET, s3_key)

    await run_in_threadpool(upload_stream)

    return {"status": "success", "file": secure_name}


MANUAL_HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>Manual Ingestion & Upload</title>
</head>
<body>
    <h1>Manual Case Entry</h1>
    <form action="/api/v1/cases/manual" method="post">
        <!-- simplified form for demonstration -->
        <p>Send a POST request to /api/v1/cases/manual with a JSON payload of an InternalTxn.</p>
    </form>

    <hr>

    <h1>File Upload</h1>
    <form action="/api/v1/ingest/file" method="post" enctype="multipart/form-data">
        <label>Feed Name:</label> <input type="text" name="feed" required><br><br>
        <label>Business Date:</label> <input type="date" name="business_date" required><br><br>
        <label>File:</label> <input type="file" name="file" required><br><br>
        <button type="submit">Upload</button>
    </form>
</body>
</html>
"""


@app.get("/manual", response_class=HTMLResponse)
def get_manual(request: Request):
    template = Template(MANUAL_HTML_TEMPLATE)
    return template.render()


@app.get("/healthz")
def healthz():
    return {"status": "ok"}
