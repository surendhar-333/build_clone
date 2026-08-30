"""FastAPI service for landing settlement feeds in Amazon S3.

The generated S3 layout is compatible with Databricks Auto Loader and uses
Hive-style ``business_date`` partitions. AWS credentials are resolved through
boto3's default credential provider chain.
"""

from __future__ import annotations

import csv
import hashlib
import io
import logging
import os
import re
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from decimal import Decimal
from functools import lru_cache
from pathlib import PurePath
from typing import Annotated, Any, Iterable, Mapping, Sequence, TypeVar

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import Body, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse
from jinja2 import DictLoader, Environment, select_autoescape
from pydantic import BaseModel, ConfigDict, Field, field_validator

LOGGER = logging.getLogger(__name__)

DEFAULT_AWS_REGION = "us-east-1"
DEFAULT_S3_PREFIX = "landing"
CONTENT_HASH_LENGTH = 12
UPLOAD_CHUNK_SIZE = 8 * 1024 * 1024
FEED_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")

MANUAL_TEMPLATE = """
<!doctype html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Manual Settlement Transaction</title>
    <style>
        :root {
            color-scheme: light;
            font-family: Inter, ui-sans-serif, system-ui, sans-serif;
            background: #f5f7fb;
            color: #172033;
        }
        * { box-sizing: border-box; }
        body { margin: 0; }
        main {
            max-width: 720px;
            margin: 2rem auto;
            padding: 1.5rem;
        }
        form {
            display: grid;
            gap: 1rem;
            padding: 1.5rem;
            background: white;
            border: 1px solid #dce3ec;
            border-radius: 10px;
        }
        label {
            display: grid;
            gap: 0.35rem;
            color: #334155;
        }
        input, select, button {
            width: 100%;
            padding: 0.65rem;
            font: inherit;
            border: 1px solid #cbd5e1;
            border-radius: 6px;
        }
        button {
            border-color: #2563eb;
            background: #2563eb;
            color: white;
            cursor: pointer;
        }
        #result {
            min-height: 1.5rem;
            margin-top: 1rem;
            white-space: pre-wrap;
        }
    </style>
</head>
<body>
<main>
    <h1>Manual settlement transaction</h1>
    <p>Submit an internal transaction for ingestion by Databricks Auto Loader.</p>

    <form id="manual-form">
        <label>
            Transaction ID
            <input name="txn_id" required maxlength="256">
        </label>
        <label>
            Business date
            <input name="business_date" type="date" required>
        </label>
        <label>
            Channel
            <input name="channel" required maxlength="128">
        </label>
        <label>
            Amount
            <input name="amount" type="number" min="0.01" step="0.01" required>
        </label>
        <label>
            Currency
            <input name="currency" value="INR" required maxlength="3">
        </label>
        <label>
            Status
            <input name="status" required maxlength="128">
        </label>
        <label>
            Account ID
            <input name="account_id" required maxlength="256">
        </label>
        <label>
            Transaction timestamp
            <input name="txn_ts" type="datetime-local" required>
        </label>
        <button type="submit">Submit transaction</button>
    </form>

    <div id="result" role="status" aria-live="polite"></div>
</main>
<script>
const form = document.getElementById("manual-form");
const result = document.getElementById("result");

form.addEventListener("submit", async (event) => {
    event.preventDefault();
    result.textContent = "Submitting...";

    const values = new FormData(form);
    const timestamp = new Date(String(values.get("txn_ts")));

    if (Number.isNaN(timestamp.getTime())) {
        result.textContent = "Enter a valid transaction timestamp.";
        return;
    }

    const payload = {
        txn_id: String(values.get("txn_id")),
        business_date: String(values.get("business_date")),
        channel: String(values.get("channel")),
        amount: String(values.get("amount")),
        currency: String(values.get("currency")),
        status: String(values.get("status")),
        account_id: String(values.get("account_id")),
        txn_ts: timestamp.toISOString()
    };

    try {
        const response = await fetch("/api/v1/cases/manual", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify(payload)
        });
        const body = await response.json();

        if (!response.ok) {
            throw new Error(JSON.stringify(body));
        }

        result.textContent = `Submitted transaction ${body.txn_id}`;
        form.reset();
    } catch (error) {
        result.textContent = `Submission failed: ${error.message}`;
    }
});
</script>
</body>
</html>
"""

templates = Environment(
    loader=DictLoader({"manual.html": MANUAL_TEMPLATE}),
    autoescape=select_autoescape(default=True),
)

app = FastAPI(
    title="Settlement Feed Ingestion API",
    description=(
        "Lands internal and network settlement feeds in S3 for "
        "Databricks Auto Loader."
    ),
    version="1.0.0",
)


@dataclass(frozen=True, slots=True)
class Settings:
    """Environment-derived application configuration."""

    bucket: str
    region: str
    prefix: str


class InternalTxn(BaseModel):
    """Transaction produced by an internal payment system."""

    model_config = ConfigDict(str_strip_whitespace=True)

    txn_id: str
    business_date: date
    channel: str
    amount: Decimal = Field(gt=0)
    currency: str = "INR"
    status: str
    account_id: str
    txn_ts: datetime

    @field_validator("txn_id", "account_id")
    @classmethod
    def validate_required_identifier(cls, value: str) -> str:
        """Reject blank transaction and account identifiers."""

        if not value:
            raise ValueError("value must not be empty")
        return value


class NetworkTxn(BaseModel):
    """Transaction received from an external payment network."""

    model_config = ConfigDict(str_strip_whitespace=True)

    txn_id: str
    business_date: date
    channel: str
    amount: Decimal = Field(gt=0)
    currency: str = "INR"
    status: str
    account_id: str
    txn_ts: datetime
    network_ref: str

    @field_validator("txn_id", "account_id")
    @classmethod
    def validate_required_identifier(cls, value: str) -> str:
        """Reject blank transaction and account identifiers."""

        if not value:
            raise ValueError("value must not be empty")
        return value


class IngestResponse(BaseModel):
    """Summary returned after one or more transaction batches are landed."""

    batches: int
    rows: int
    keys: list[str]


class ManualIngestResponse(BaseModel):
    """Response returned for a manually submitted transaction."""

    txn_id: str


class HealthResponse(BaseModel):
    """Service health and non-secret destination information."""

    ok: bool
    bucket: str
    prefix: str


Txn = TypeVar("Txn", InternalTxn, NetworkTxn)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Read and validate configuration from environment variables."""

    bucket = os.getenv("SETTLEMENT_S3_BUCKET", "").strip()
    region = os.getenv("AWS_REGION", DEFAULT_AWS_REGION).strip()
    raw_prefix = os.getenv("SETTLEMENT_S3_PREFIX", DEFAULT_S3_PREFIX).strip()

    if not bucket:
        raise RuntimeError("SETTLEMENT_S3_BUCKET is required")
    if not region:
        raise RuntimeError("AWS_REGION must not be empty")

    prefix_parts = [part for part in raw_prefix.strip("/").split("/") if part]
    if not prefix_parts or any(part in {".", ".."} for part in prefix_parts):
        raise RuntimeError("SETTLEMENT_S3_PREFIX is invalid")

    return Settings(
        bucket=bucket,
        region=region,
        prefix="/".join(prefix_parts),
    )


@lru_cache(maxsize=8)
def get_s3_client(region: str) -> Any:
    """Create an S3 client using boto3's default credential chain."""

    return boto3.client("s3", region_name=region)


def settings_or_503() -> Settings:
    """Return application settings or an HTTP configuration error."""

    try:
        return get_settings()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def utc_datetime(value: datetime) -> datetime:
    """Return a timezone-aware UTC datetime.

    Naive timestamps are treated as UTC so CSV output and S3 keys remain
    deterministic across hosts.
    """

    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def key_timestamp(value: datetime) -> str:
    """Format a UTC timestamp for use in an S3 key."""

    normalized = utc_datetime(value)
    return normalized.strftime("%Y-%m-%dT%H-%M-%S.%fZ")


def csv_timestamp(value: datetime) -> str:
    """Format a transaction timestamp as ISO 8601 UTC."""

    return utc_datetime(value).isoformat(timespec="microseconds").replace(
        "+00:00",
        "Z",
    )


def csv_value(value: Any) -> str:
    """Serialize a supported model value for CSV output."""

    if value is None:
        return ""
    if isinstance(value, datetime):
        return csv_timestamp(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return format(value, "f")
    return str(value)


def canonical_sort_key(transaction: InternalTxn | NetworkTxn) -> tuple[str, ...]:
    """Return a deterministic ordering key for transaction CSV rows."""

    values = transaction.model_dump(mode="python")
    return tuple(csv_value(values[name]) for name in type(transaction).model_fields)


def build_csv(
    transactions: Sequence[InternalTxn] | Sequence[NetworkTxn],
    fieldnames: Sequence[str],
    extra_columns: Mapping[str, str] | None = None,
) -> bytes:
    """Build deterministic UTF-8 CSV content in memory."""

    output = io.StringIO(newline="")
    all_fieldnames = [*fieldnames, *(extra_columns or {}).keys()]
    writer = csv.DictWriter(
        output,
        fieldnames=all_fieldnames,
        extrasaction="raise",
        lineterminator="\n",
    )
    writer.writeheader()

    for transaction in sorted(transactions, key=canonical_sort_key):
        row = {
            name: csv_value(value)
            for name, value in transaction.model_dump(mode="python").items()
        }
        if extra_columns:
            row.update(extra_columns)
        writer.writerow(row)

    return output.getvalue().encode("utf-8")


def content_digest(content: bytes) -> str:
    """Return the full SHA-256 hexadecimal digest for content."""

    return hashlib.sha256(content).hexdigest()


def deterministic_part_uuid(digest: str) -> uuid.UUID:
    """Create a stable UUID for a content digest."""

    return uuid.uuid5(uuid.NAMESPACE_URL, f"sha256:{digest}")


def transaction_s3_key(
    settings: Settings,
    feed: str,
    business_date: date,
    batch_timestamp: datetime,
    digest: str,
) -> str:
    """Build an idempotent Auto Loader transaction object key."""

    short_hash = digest[:CONTENT_HASH_LENGTH]
    batch = f"{key_timestamp(batch_timestamp)}-{short_hash}"
    part_id = deterministic_part_uuid(digest)

    return (
        f"{settings.prefix}/{feed}/"
        f"business_date={business_date.isoformat()}/"
        f"batch={batch}/part-{part_id}.csv"
    )


def put_csv(settings: Settings, key: str, content: bytes, digest: str) -> None:
    """Upload CSV content to S3."""

    client = get_s3_client(settings.region)

    try:
        client.put_object(
            Bucket=settings.bucket,
            Key=key,
            Body=content,
            ContentType="text/csv; charset=utf-8",
            Metadata={"sha256": digest},
        )
    except (BotoCoreError, ClientError) as exc:
        LOGGER.exception("Failed to upload CSV to s3://%s/%s", settings.bucket, key)
        raise HTTPException(
            status_code=502,
            detail="Failed to write the settlement batch to S3",
        ) from exc


def group_by_business_date(
    transactions: Iterable[Txn],
) -> dict[date, list[Txn]]:
    """Group transactions by their business date."""

    groups: dict[date, list[Txn]] = defaultdict(list)
    for transaction in transactions:
        groups[transaction.business_date].append(transaction)
    return dict(groups)


def ingest_transactions(
    feed: str,
    transactions: Sequence[InternalTxn] | Sequence[NetworkTxn],
    fieldnames: Sequence[str],
) -> IngestResponse:
    """Serialize, partition, and upload transaction batches."""

    settings = settings_or_503()
    keys: list[str] = []

    for business_date, group in sorted(group_by_business_date(transactions).items()):
        content = build_csv(group, fieldnames)
        digest = content_digest(content)
        batch_timestamp = max(utc_datetime(item.txn_ts) for item in group)
        key = transaction_s3_key(
            settings=settings,
            feed=feed,
            business_date=business_date,
            batch_timestamp=batch_timestamp,
            digest=digest,
        )
        put_csv(settings, key, content, digest)
        keys.append(key)

    return IngestResponse(
        batches=len(keys),
        rows=len(transactions),
        keys=keys,
    )


def safe_filename(filename: str | None) -> str:
    """Return a safe basename for an uploaded file."""

    if not filename:
        raise HTTPException(status_code=422, detail="Uploaded file needs a filename")

    normalized = filename.replace("\\", "/")
    basename = PurePath(normalized).name.strip()

    if not basename or basename in {".", ".."} or "\x00" in basename:
        raise HTTPException(status_code=422, detail="Invalid uploaded filename")

    return basename


def validate_feed(feed: str) -> str:
    """Validate a feed name before including it in an S3 key."""

    normalized = feed.strip()
    if not FEED_PATTERN.fullmatch(normalized):
        raise HTTPException(
            status_code=422,
            detail=(
                "feed must be 1-64 characters and contain only letters, "
                "numbers, underscores, or hyphens"
            ),
        )
    return normalized


async def hash_upload(upload: UploadFile) -> tuple[str, int]:
    """Hash an uploaded file incrementally and rewind it for S3 streaming."""

    digest = hashlib.sha256()
    size = 0

    while chunk := await upload.read(UPLOAD_CHUNK_SIZE):
        digest.update(chunk)
        size += len(chunk)

    await upload.seek(0)
    return digest.hexdigest(), size


@app.post(
    "/api/v1/ingest/internal",
    response_model=IngestResponse,
    status_code=201,
)
def ingest_internal(
    transactions: Annotated[
        list[InternalTxn],
        Body(min_length=1),
    ],
) -> IngestResponse:
    """Land internal transactions as business-date-partitioned CSV files."""

    return ingest_transactions(
        feed="internal",
        transactions=transactions,
        fieldnames=tuple(InternalTxn.model_fields),
    )


@app.post(
    "/api/v1/ingest/network",
    response_model=IngestResponse,
    status_code=201,
)
def ingest_network(
    transactions: Annotated[
        list[NetworkTxn],
        Body(min_length=1),
    ],
) -> IngestResponse:
    """Land network transactions as business-date-partitioned CSV files."""

    return ingest_transactions(
        feed="network",
        transactions=transactions,
        fieldnames=tuple(NetworkTxn.model_fields),
    )


@app.post(
    "/api/v1/cases/manual",
    response_model=ManualIngestResponse,
    status_code=201,
)
def ingest_manual(transaction: InternalTxn) -> ManualIngestResponse:
    """Land one analyst-entered transaction using the internal feed schema."""

    settings = settings_or_503()
    content = build_csv(
        [transaction],
        tuple(InternalTxn.model_fields),
    )
    digest = content_digest(content)
    key = transaction_s3_key(
        settings=settings,
        feed="internal",
        business_date=transaction.business_date,
        batch_timestamp=transaction.txn_ts,
        digest=digest,
    )
    put_csv(settings, key, content, digest)

    return ManualIngestResponse(txn_id=transaction.txn_id)


@app.post("/api/v1/ingest/file", status_code=201)
async def ingest_file(
    feed: Annotated[str, Form(...)],
    business_date: Annotated[date, Form(...)],
    file: Annotated[UploadFile, File(...)],
) -> dict[str, str | int]:
    """Stream an uploaded feed file to a deterministic S3 object key."""

    settings = settings_or_503()
    normalized_feed = validate_feed(feed)
    filename = safe_filename(file.filename)
    digest, size = await hash_upload(file)

    if size == 0:
        raise HTTPException(status_code=422, detail="Uploaded file must not be empty")

    short_hash = digest[:CONTENT_HASH_LENGTH]
    partition_time = datetime.combine(
        business_date,
        time.min,
        tzinfo=timezone.utc,
    )
    batch = f"{key_timestamp(partition_time)}-{short_hash}"
    key = (
        f"{settings.prefix}/{normalized_feed}/"
        f"business_date={business_date.isoformat()}/"
        f"batch={batch}/{filename}"
    )

    client = get_s3_client(settings.region)

    try:
        await run_in_threadpool(
            client.upload_fileobj,
            file.file,
            settings.bucket,
            key,
            {
                "ContentType": file.content_type or "application/octet-stream",
                "Metadata": {"sha256": digest},
            },
        )
    except (BotoCoreError, ClientError, OSError) as exc:
        LOGGER.exception(
            "Failed to stream upload to s3://%s/%s",
            settings.bucket,
            key,
        )
        raise HTTPException(
            status_code=502,
            detail="Failed to write the uploaded feed to S3",
        ) from exc

    return {"key": key, "bytes": size}


@app.get("/manual", response_class=HTMLResponse)
def manual_form(request: Request) -> HTMLResponse:
    """Render the manual internal-transaction submission form."""

    html = templates.get_template("manual.html").render(request=request)
    return HTMLResponse(content=html)


@app.get("/healthz", response_model=HealthResponse)
def healthz() -> HealthResponse:
    """Return service health and configured S3 destination details."""

    settings = settings_or_503()
    return HealthResponse(
        ok=True,
        bucket=settings.bucket,
        prefix=settings.prefix,
    )