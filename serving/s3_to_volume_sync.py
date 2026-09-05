import os
import json
import time
import argparse
import logging
import tempfile
from typing import Any

import boto3
from databricks.sdk import WorkspaceClient
from databricks.sdk.errors import NotFound, ResourceDoesNotExist, AlreadyExists, ResourceAlreadyExists

class UnsafeObjectKey(Exception): pass
class ConfigurationError(Exception): pass

def _check_common(path: str, exc_cls):
    if ".." in path:
        raise exc_cls("Path contains '..'")
    if "\\" in path:
        raise exc_cls("Path contains backslash")
    if "\0" in path:
        raise exc_cls("Path contains null byte")
    if "//" in path:
        raise exc_cls("Path contains empty segment")
    if path == "":
        raise exc_cls("Path is empty")

def normalize_s3_prefix(prefix: str) -> str:
    _check_common(prefix, ConfigurationError)
    if prefix.startswith("/"):
        raise ConfigurationError("Prefix cannot start with /")
    if prefix.endswith("/"):
        raise ConfigurationError("Prefix cannot end with / directory marker")
    return prefix

def normalize_volume_path(path: str) -> str:
    _check_common(path, ConfigurationError)
    if not path.startswith("/Volumes/"):
        raise ConfigurationError("Path must start with /Volumes/")
    if path.endswith("/"):
        raise ConfigurationError("Path cannot end with /")

    parts = path.strip("/").split("/")
    if len(parts) < 4:
        raise ConfigurationError("Path must have at least catalog, schema, and volume")

    return path

def relative_object_key(key: str, prefix: str) -> str:
    _check_common(key, UnsafeObjectKey)
    if key.endswith("/"):
        raise UnsafeObjectKey("Directory marker rejected")
    if key.startswith("/"):
        raise UnsafeObjectKey("Object key cannot start with /")

    if key == prefix:
        pass # will be caught by empty rel_key check
    elif not key.startswith(prefix + "/"):
        raise UnsafeObjectKey("Key is out of prefix")

    rel_key = key[len(prefix):]
    if rel_key.startswith("/"):
        rel_key = rel_key[1:]

    if not rel_key:
        raise UnsafeObjectKey("Relative key is empty")

    return rel_key

def destination_path(volume_path: str, rel_key: str) -> str:
    vol = normalize_volume_path(volume_path)
    _check_common(rel_key, UnsafeObjectKey)
    if rel_key.endswith("/"):
        raise UnsafeObjectKey("Directory marker rejected")
    if rel_key.startswith("/"):
        raise UnsafeObjectKey("Relative key cannot start with /")

    return f"{vol}/{rel_key}"

class JsonFormatter(logging.Formatter):
    def format(self, record):
        log_record = {
            "time": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "message": record.getMessage(),
            "logger": record.name
        }
        if record.exc_info:
            log_record["exc_info"] = self.formatException(record.exc_info)

        token = os.environ.get("DATABRICKS_TOKEN")
        res = json.dumps(log_record)
        if token and token in res:
            res = res.replace(token, "***")
        return res

def setup_logging():
    root_logger = logging.getLogger()
    if not root_logger.handlers:
        root_logger.setLevel(logging.INFO)
        handler = logging.StreamHandler()
        handler.setFormatter(JsonFormatter())
        root_logger.addHandler(handler)
    return logging.getLogger("s3_to_volume_sync")

def sync_once(dry_run: bool = False):
    logger = setup_logging()

    bucket = os.environ.get("SETTLEMENT_S3_BUCKET")
    prefix_env = os.environ.get("SETTLEMENT_S3_PREFIX")
    region = os.environ.get("AWS_REGION")
    vol_path_env = os.environ.get("VOLUME_PATH")

    if not all([bucket, prefix_env, region, vol_path_env]):
        raise ConfigurationError("Missing required environment variables")

    prefix = normalize_s3_prefix(prefix_env)
    vol_path = normalize_volume_path(vol_path_env)

    host = os.environ.get("DATABRICKS_HOST")
    token = os.environ.get("DATABRICKS_TOKEN")
    if not host or not token:
        raise ConfigurationError("Missing DATABRICKS_HOST or DATABRICKS_TOKEN")

    s3 = boto3.client("s3", region_name=region)
    w = WorkspaceClient(host=host, token=token)

    logger.info(f"Starting sync from s3://{bucket}/{prefix} to {vol_path}")

    paginator = s3.get_paginator('list_objects_v2')
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix + "/"):
        for obj in page.get('Contents', []):
            key = obj['Key']
            size = obj['Size']

            try:
                rel_key = relative_object_key(key, prefix)
                dest = destination_path(vol_path, rel_key)
            except UnsafeObjectKey as e:
                logger.warning(f"Skipping unsafe key {key}: {e}")
                continue

            if size > 5 * 1024 * 1024 * 1024:
                logger.warning(f"Object {key} too large ({size} bytes), skipping")
                continue

            # check existence
            try:
                w.files.get_metadata(dest)
                logger.info(f"Object {key} already exists in Volume")
                continue
            except (NotFound, ResourceDoesNotExist):
                pass
            except Exception as e:
                # also catch fallback 404 string matching just in case
                if "404" in str(e):
                    pass
                else:
                    raise

            if dry_run:
                logger.info(f"Dry run: would sync object {key} to {dest}")
                continue

            logger.info(f"Syncing object {key} to {dest}")

            with tempfile.SpooledTemporaryFile(max_size=10*1024*1024) as f:
                s3.download_fileobj(bucket, key, f)
                f.seek(0)

                try:
                    w.files.upload(dest, f, overwrite=False)
                    logger.info(f"Successfully synced {key}")
                except (AlreadyExists, ResourceAlreadyExists):
                    logger.info(f"Concurrent create race for {key}, treated as success")
                except Exception as e:
                    if "Exists" in str(e):
                        logger.info(f"Concurrent create race for {key}, treated as success")
                    else:
                        raise

def main():
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--once", action="store_true")
    group.add_argument("--loop", action="store_true")
    parser.add_argument("--interval-seconds", type=int, default=60)
    parser.add_argument("--dry-run", action="store_true")

    args = parser.parse_args()
    logger = setup_logging()

    if args.once:
        sync_once(args.dry_run)
    elif args.loop:
        while True:
            try:
                sync_once(args.dry_run)
            except Exception as e:
                logger.error(f"Error during sync loop: {e}", exc_info=True)
            time.sleep(args.interval_seconds)

if __name__ == "__main__":
    main()
