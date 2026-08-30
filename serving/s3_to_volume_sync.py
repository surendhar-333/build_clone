"""Mirror an append-only Amazon S3 landing prefix into a UC managed Volume.

This program is intended to run outside Databricks—for example, on a local
machine or in GitHub Actions. It reads S3 through boto3's default credential
provider chain and writes files through the Databricks Files API.

Idempotency model
-----------------
The S3 landing prefix is assumed to be append-only and its object keys
immutable. An object is considered synchronized when its corresponding path
already exists in the Volume. The ingestion API's content-addressed keys
satisfy this requirement.

S3 deletions are intentionally not propagated. This preserves append-only
landing semantics and avoids deleting data that Auto Loader may have already
discovered.

Example:
    python serving/s3_to_volume_sync.py --once
    python serving/s3_to_volume_sync.py --once --dry-run
    python serving/s3_to_volume_sync.py --loop --interval-seconds 60
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import posixpath
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, BinaryIO, Iterator, Mapping, Sequence

import boto3
from botocore.client import BaseClient
from botocore.exceptions import BotoCoreError, ClientError
from databricks.sdk import WorkspaceClient
from databricks.sdk.errors import DatabricksError, NotFound

DEFAULT_AWS_REGION = "us-east-1"
DEFAULT_S3_PREFIX = "landing"
DEFAULT_LOOP_INTERVAL_SECONDS = 60
DOWNLOAD_CHUNK_SPOOL_BYTES = 64 * 1024 * 1024
MAX_FILES_API_OBJECT_BYTES = 5 * 1024 * 1024 * 1024

LOGGER = logging.getLogger("s3_to_volume_sync")


class JsonFormatter(logging.Formatter):
    """Format log records as one JSON object per line."""

    _standard_attributes = {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "message",
        "module",
        "msecs",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "taskName",
        "thread",
        "threadName",
    }

    def format(self, record: logging.LogRecord) -> str:
        """Serialize a log record without exposing configured credentials."""

        event: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        for name, value in record.__dict__.items():
            if name not in self._standard_attributes and not name.startswith("_"):
                event[name] = value

        if record.exc_info:
            event["exception"] = self.formatException(record.exc_info)

        return json.dumps(event, default=str, separators=(",", ":"))


def configure_logging(verbose: bool) -> None:
    """Configure structured logging on standard output."""

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.DEBUG if verbose else logging.INFO)

    # Avoid verbose HTTP diagnostics that could include authorization metadata.
    logging.getLogger("databricks.sdk").setLevel(logging.WARNING)
    logging.getLogger("botocore").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


@dataclass(frozen=True, slots=True)
class Settings:
    """Environment-derived source and destination configuration."""

    s3_bucket: str
    s3_prefix: str
    aws_region: str
    databricks_host: str
    databricks_token: str
    volume_path: str

    @classmethod
    def from_environment(cls) -> "Settings":
        """Read and validate configuration exclusively from the environment."""

        bucket = required_environment("SETTLEMENT_S3_BUCKET")
        prefix = normalize_s3_prefix(
            os.getenv("SETTLEMENT_S3_PREFIX", DEFAULT_S3_PREFIX)
        )
        region = os.getenv("AWS_REGION", DEFAULT_AWS_REGION).strip()
        host = required_environment("DATABRICKS_HOST").rstrip("/")
        token = required_environment("DATABRICKS_TOKEN")
        volume_path = normalize_volume_path(required_environment("VOLUME_PATH"))

        if not region:
            raise ConfigurationError("AWS_REGION must not be empty")

        return cls(
            s3_bucket=bucket,
            s3_prefix=prefix,
            aws_region=region,
            databricks_host=host,
            databricks_token=token,
            volume_path=volume_path,
        )


@dataclass(frozen=True, slots=True)
class S3Object:
    """Metadata required to synchronize one S3 object."""

    key: str
    relative_key: str
    size: int
    etag: str
    last_modified: datetime | None


@dataclass(slots=True)
class SyncStats:
    """Counters and byte totals for one synchronization cycle."""

    discovered: int = 0
    uploaded: int = 0
    skipped: int = 0
    would_upload: int = 0
    rejected: int = 0
    bytes_uploaded: int = 0
    bytes_would_upload: int = 0


class ConfigurationError(ValueError):
    """Raised when required environment configuration is invalid."""


class UnsafeObjectKey(ValueError):
    """Raised when an S3 key cannot be mapped safely into the Volume."""


def required_environment(name: str) -> str:
    """Return a non-empty required environment variable."""

    value = os.getenv(name, "").strip()
    if not value:
        raise ConfigurationError(f"{name} is required")
    return value


def normalize_s3_prefix(value: str) -> str:
    """Normalize and validate an S3 source prefix."""

    prefix = value.strip().strip("/")
    if not prefix:
        raise ConfigurationError("SETTLEMENT_S3_PREFIX must not be empty")

    parts = prefix.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ConfigurationError("SETTLEMENT_S3_PREFIX contains an unsafe path segment")

    return "/".join(parts)


def normalize_volume_path(value: str) -> str:
    """Validate a Unity Catalog managed Volume path."""

    path = value.strip()
    if not path.startswith("/"):
        path = f"/{path}"

    normalized = posixpath.normpath(path)
    parts = normalized.split("/")

    # ['', 'Volumes', catalog, schema, volume, ...]
    if len(parts) < 5 or parts[1] != "Volumes":
        raise ConfigurationError(
            "VOLUME_PATH must have the form "
            "/Volumes/<catalog>/<schema>/<volume>[/directory]"
        )

    if any(not part or part in {".", ".."} for part in parts[2:5]):
        raise ConfigurationError("VOLUME_PATH contains an invalid UC identifier")

    return normalized.rstrip("/")


def relative_object_key(key: str, prefix: str) -> str:
    """Map an S3 key to its path relative to the configured landing prefix."""

    prefix_with_separator = f"{prefix}/"
    if not key.startswith(prefix_with_separator):
        raise UnsafeObjectKey(
            f"S3 key {key!r} is outside configured prefix {prefix!r}"
        )

    relative = key[len(prefix_with_separator) :]
    if not relative or relative.endswith("/"):
        raise UnsafeObjectKey("S3 directory-marker objects are not synchronized")
    if "\x00" in relative or "\\" in relative:
        raise UnsafeObjectKey("S3 key contains an unsafe character")

    parts = relative.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise UnsafeObjectKey("S3 key contains an unsafe path segment")

    return "/".join(parts)


def destination_path(volume_path: str, relative_key: str) -> str:
    """Build a destination path while preventing traversal outside the Volume."""

    destination = posixpath.normpath(posixpath.join(volume_path, relative_key))
    expected_prefix = f"{volume_path}/"

    if not destination.startswith(expected_prefix):
        raise UnsafeObjectKey("Mapped destination escapes VOLUME_PATH")

    return destination


def strip_etag(value: str | None) -> str:
    """Remove the quotes normally returned around an S3 ETag."""

    return (value or "").strip('"')


def iter_s3_objects(client: BaseClient, settings: Settings) -> Iterator[S3Object]:
    """Yield all ordinary objects below the configured S3 prefix."""

    paginator = client.get_paginator("list_objects_v2")
    source_prefix = f"{settings.s3_prefix}/"

    for page in paginator.paginate(
        Bucket=settings.s3_bucket,
        Prefix=source_prefix,
    ):
        for item in page.get("Contents", []):
            key = str(item["Key"])

            try:
                relative = relative_object_key(key, settings.s3_prefix)
            except UnsafeObjectKey as exc:
                LOGGER.warning(
                    "Ignoring unmappable S3 object",
                    extra={
                        "event": "object_rejected",
                        "s3_key": key,
                        "reason": str(exc),
                    },
                )
                continue

            yield S3Object(
                key=key,
                relative_key=relative,
                size=int(item.get("Size", 0)),
                etag=strip_etag(item.get("ETag")),
                last_modified=item.get("LastModified"),
            )


def volume_file_exists(workspace: WorkspaceClient, path: str) -> bool:
    """Return whether a destination file currently exists."""

    try:
        workspace.files.get_metadata(path)
        return True
    except NotFound:
        return False


def spool_s3_object(
    s3_client: BaseClient,
    settings: Settings,
    source: S3Object,
) -> BinaryIO:
    """Download an S3 object into a bounded-memory temporary file.

    Small files remain in memory. Larger files spill transparently to local
    disk, preventing an unbounded process-memory allocation.
    """

    temporary = tempfile.SpooledTemporaryFile(
        max_size=DOWNLOAD_CHUNK_SPOOL_BYTES,
        mode="w+b",
    )

    try:
        s3_client.download_fileobj(
            Bucket=settings.s3_bucket,
            Key=source.key,
            Fileobj=temporary,
        )
        downloaded_size = temporary.tell()

        if downloaded_size != source.size:
            raise IOError(
                f"Downloaded {downloaded_size} bytes; expected {source.size}"
            )

        temporary.seek(0)
        return temporary
    except Exception:
        temporary.close()
        raise


def destination_appeared_after_error(
    workspace: WorkspaceClient,
    destination: str,
) -> bool:
    """Check whether a competing process completed the destination upload."""

    try:
        return volume_file_exists(workspace, destination)
    except DatabricksError:
        return False


def upload_one(
    s3_client: BaseClient,
    workspace: WorkspaceClient,
    settings: Settings,
    source: S3Object,
    *,
    dry_run: bool,
) -> str:
    """Synchronize one S3 object.

    Returns one of ``uploaded``, ``skipped``, ``would_upload``, or ``rejected``.
    """

    destination = destination_path(settings.volume_path, source.relative_key)

    if volume_file_exists(workspace, destination):
        LOGGER.info(
            "Destination already exists",
            extra={
                "event": "object_skipped",
                "reason": "destination_exists",
                "s3_key": source.key,
                "volume_path": destination,
                "bytes": source.size,
                "etag": source.etag,
            },
        )
        return "skipped"

    if source.size > MAX_FILES_API_OBJECT_BYTES:
        LOGGER.error(
            "Object exceeds the supported Files API upload size",
            extra={
                "event": "object_rejected",
                "reason": "object_too_large",
                "s3_key": source.key,
                "volume_path": destination,
                "bytes": source.size,
                "maximum_bytes": MAX_FILES_API_OBJECT_BYTES,
            },
        )
        return "rejected"

    if dry_run:
        LOGGER.info(
            "Would upload object",
            extra={
                "event": "object_would_upload",
                "s3_key": source.key,
                "volume_path": destination,
                "bytes": source.size,
                "etag": source.etag,
            },
        )
        return "would_upload"

    LOGGER.info(
        "Downloading S3 object",
        extra={
            "event": "object_download_started",
            "s3_key": source.key,
            "bytes": source.size,
            "etag": source.etag,
        },
    )

    content = spool_s3_object(s3_client, settings, source)
    try:
        LOGGER.info(
            "Uploading object to Volume",
            extra={
                "event": "object_upload_started",
                "s3_key": source.key,
                "volume_path": destination,
                "bytes": source.size,
                "etag": source.etag,
            },
        )

        try:
            workspace.files.upload(
                destination,
                content,
                overwrite=False,
            )
        except DatabricksError:
            # A concurrent relay may have uploaded the same immutable key after
            # our initial existence check. Treat that race as successful.
            if destination_appeared_after_error(workspace, destination):
                LOGGER.info(
                    "Destination was created concurrently",
                    extra={
                        "event": "object_skipped",
                        "reason": "concurrent_upload",
                        "s3_key": source.key,
                        "volume_path": destination,
                        "bytes": source.size,
                    },
                )
                return "skipped"
            raise
    finally:
        content.close()

    LOGGER.info(
        "Object synchronized",
        extra={
            "event": "object_uploaded",
            "s3_key": source.key,
            "volume_path": destination,
            "bytes": source.size,
            "etag": source.etag,
        },
    )
    return "uploaded"


def run_sync_cycle(
    s3_client: BaseClient,
    workspace: WorkspaceClient,
    settings: Settings,
    *,
    dry_run: bool,
) -> SyncStats:
    """Run one complete scan and synchronization cycle."""

    stats = SyncStats()
    started_at = time.monotonic()

    LOGGER.info(
        "Synchronization cycle started",
        extra={
            "event": "cycle_started",
            "s3_bucket": settings.s3_bucket,
            "s3_prefix": settings.s3_prefix,
            "volume_path": settings.volume_path,
            "dry_run": dry_run,
        },
    )

    for source in iter_s3_objects(s3_client, settings):
        stats.discovered += 1

        try:
            outcome = upload_one(
                s3_client,
                workspace,
                settings,
                source,
                dry_run=dry_run,
            )
        except (BotoCoreError, ClientError, DatabricksError, OSError) as exc:
            LOGGER.exception(
                "Object synchronization failed",
                extra={
                    "event": "object_failed",
                    "s3_key": source.key,
                    "volume_path": destination_path(
                        settings.volume_path,
                        source.relative_key,
                    ),
                    "bytes": source.size,
                    "error_type": type(exc).__name__,
                },
            )
            raise

        if outcome == "uploaded":
            stats.uploaded += 1
            stats.bytes_uploaded += source.size
        elif outcome == "skipped":
            stats.skipped += 1
        elif outcome == "would_upload":
            stats.would_upload += 1
            stats.bytes_would_upload += source.size
        elif outcome == "rejected":
            stats.rejected += 1

    LOGGER.info(
        "Synchronization cycle completed",
        extra={
            "event": "cycle_completed",
            **asdict(stats),
            "duration_seconds": round(time.monotonic() - started_at, 3),
            "dry_run": dry_run,
        },
    )
    return stats


def create_clients(settings: Settings) -> tuple[BaseClient, WorkspaceClient]:
    """Create AWS and Databricks clients from validated configuration."""

    s3_client = boto3.client("s3", region_name=settings.aws_region)
    workspace = WorkspaceClient(
        host=settings.databricks_host,
        token=settings.databricks_token,
    )
    return s3_client, workspace


def positive_integer(value: str) -> int:
    """Parse a strictly positive command-line integer."""

    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be greater than zero")
    return parsed


def parse_arguments(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse synchronization mode and runtime flags."""

    parser = argparse.ArgumentParser(
        description=(
            "Mirror an append-only S3 landing prefix into a Unity Catalog Volume."
        )
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--once",
        action="store_true",
        help="Run one synchronization cycle and exit.",
    )
    mode.add_argument(
        "--loop",
        action="store_true",
        help="Run synchronization cycles continuously.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List required uploads without downloading or writing files.",
    )
    parser.add_argument(
        "--interval-seconds",
        type=positive_integer,
        default=DEFAULT_LOOP_INTERVAL_SECONDS,
        help=(
            "Delay between loop cycles; only used with --loop "
            f"(default: {DEFAULT_LOOP_INTERVAL_SECONDS})."
        ),
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable debug-level application logging.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command-line synchronization process."""

    arguments = parse_arguments(argv)
    configure_logging(arguments.verbose)

    try:
        settings = Settings.from_environment()
        s3_client, workspace = create_clients(settings)
    except ConfigurationError as exc:
        LOGGER.error(
            "Invalid configuration",
            extra={
                "event": "configuration_error",
                "reason": str(exc),
            },
        )
        return 2

    if arguments.once:
        try:
            stats = run_sync_cycle(
                s3_client,
                workspace,
                settings,
                dry_run=arguments.dry_run,
            )
            return 1 if stats.rejected else 0
        except (BotoCoreError, ClientError, DatabricksError, OSError):
            return 1

    LOGGER.info(
        "Continuous synchronization started",
        extra={
            "event": "loop_started",
            "interval_seconds": arguments.interval_seconds,
            "dry_run": arguments.dry_run,
        },
    )

    try:
        while True:
            try:
                run_sync_cycle(
                    s3_client,
                    workspace,
                    settings,
                    dry_run=arguments.dry_run,
                )
            except (BotoCoreError, ClientError, DatabricksError, OSError):
                LOGGER.error(
                    "Cycle failed; retrying after the configured interval",
                    extra={
                        "event": "cycle_retry_scheduled",
                        "interval_seconds": arguments.interval_seconds,
                    },
                )

            time.sleep(arguments.interval_seconds)
    except KeyboardInterrupt:
        LOGGER.info(
            "Synchronization stopped",
            extra={"event": "loop_stopped", "reason": "keyboard_interrupt"},
        )
        return 0


if __name__ == "__main__":
    raise SystemExit(main())