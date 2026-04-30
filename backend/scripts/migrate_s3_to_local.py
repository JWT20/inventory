"""Copy every object from the configured S3 bucket to the local upload dir.

Reads S3 credentials and bucket from the standard env vars (S3_ENDPOINT_URL,
S3_BUCKET, S3_ACCESS_KEY_ID, S3_SECRET_ACCESS_KEY, S3_REGION) and writes each
object to UPLOAD_DIR/<key> on the local filesystem.

Idempotent: skips files that already exist with the same size. Safe to run
multiple times — once for the bulk sync while production still writes to S3,
and again as a delta sync during the cutover window.

Usage (inside the backend container so the uploads volume is mounted at
/app/uploads):

    docker compose exec backend python -m scripts.migrate_s3_to_local
    docker compose exec backend python -m scripts.migrate_s3_to_local --delete-source
"""
import argparse
import os
import sys

import boto3
from botocore.config import Config as BotoConfig

from app.config import settings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--delete-source",
        action="store_true",
        help="After successful local write, delete the object from the S3 bucket.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List what would be copied without writing or deleting anything.",
    )
    args = parser.parse_args()

    bucket = settings.s3_bucket
    if not bucket:
        print("S3_BUCKET is not set; nothing to migrate.", file=sys.stderr)
        return 1

    upload_dir = settings.upload_dir
    os.makedirs(upload_dir, exist_ok=True)

    client = boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url,
        aws_access_key_id=settings.s3_access_key_id,
        aws_secret_access_key=settings.s3_secret_access_key,
        region_name=settings.s3_region,
        config=BotoConfig(
            request_checksum_calculation="when_required",
            response_checksum_validation="when_required",
        ),
    )

    print(f"Source: s3://{bucket} via {settings.s3_endpoint_url}")
    print(f"Target: {upload_dir}")
    if args.dry_run:
        print("Mode:   DRY RUN (no writes, no deletes)")
    elif args.delete_source:
        print("Mode:   COPY + DELETE source after successful write")
    else:
        print("Mode:   COPY (source objects retained)")
    print()

    copied = skipped = deleted = failed = 0
    total_bytes = 0

    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            size = obj["Size"]
            target_path = os.path.join(upload_dir, key)

            if os.path.isfile(target_path) and os.path.getsize(target_path) == size:
                skipped += 1
                if args.delete_source and not args.dry_run:
                    client.delete_object(Bucket=bucket, Key=key)
                    deleted += 1
                continue

            if args.dry_run:
                print(f"[would copy] {key} ({size} bytes)")
                copied += 1
                total_bytes += size
                continue

            try:
                os.makedirs(os.path.dirname(target_path), exist_ok=True)
                response = client.get_object(Bucket=bucket, Key=key)
                with open(target_path, "wb") as f:
                    for chunk in response["Body"].iter_chunks(chunk_size=1024 * 1024):
                        f.write(chunk)
                if os.path.getsize(target_path) != size:
                    raise RuntimeError(
                        f"size mismatch after write: expected {size}, got {os.path.getsize(target_path)}"
                    )
                copied += 1
                total_bytes += size
                print(f"[copied] {key} ({size} bytes)")
                if args.delete_source:
                    client.delete_object(Bucket=bucket, Key=key)
                    deleted += 1
            except Exception as e:
                failed += 1
                print(f"[FAILED] {key}: {e}", file=sys.stderr)

    print()
    print(
        f"Done. copied={copied} skipped={skipped} deleted={deleted} failed={failed} "
        f"total_bytes_transferred={total_bytes}"
    )
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
