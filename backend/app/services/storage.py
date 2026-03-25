"""File storage abstraction with Local and S3-compatible backends.

Usage:
    from app.services.storage import storage

    # Save a file
    key = storage.save("scans/abc123.jpg", image_bytes)

    # Get a URL for the browser
    url = storage.url("scans/abc123.jpg")

    # Delete a file
    storage.delete("scans/abc123.jpg")

Backend is selected via the STORAGE_BACKEND setting ("local" or "s3").
S3 backend works with any S3-compatible service (AWS S3, OCI Object Storage,
Cloudflare R2, MinIO, etc.).
"""

import abc
import logging
import os
import time

from app.config import settings

logger = logging.getLogger(__name__)


class StorageBackend(abc.ABC):
    """Abstract file storage interface."""

    @abc.abstractmethod
    def save(self, key: str, data: bytes) -> str:
        """Store bytes at the given key. Returns the key."""

    @abc.abstractmethod
    def delete(self, key: str) -> None:
        """Delete the file at the given key. No error if missing."""

    @abc.abstractmethod
    def exists(self, key: str) -> bool:
        """Check if a file exists at the given key."""

    @abc.abstractmethod
    def url(self, key: str) -> str:
        """Return a URL the browser can use to load the file."""

    @abc.abstractmethod
    def list_keys(self, prefix: str) -> list[tuple[str, float]]:
        """List files under the prefix.

        Returns list of (key, last_modified_timestamp) tuples.
        """


class LocalStorage(StorageBackend):
    """Store files on the local filesystem (development / single-instance)."""

    def __init__(self, base_dir: str) -> None:
        self._base_dir = base_dir

    def _full_path(self, key: str) -> str:
        return os.path.join(self._base_dir, key)

    def save(self, key: str, data: bytes) -> str:
        path = self._full_path(key)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)
        return key

    def delete(self, key: str) -> None:
        path = self._full_path(key)
        if os.path.exists(path):
            os.remove(path)

    def exists(self, key: str) -> bool:
        return os.path.isfile(self._full_path(key))

    def url(self, key: str) -> str:
        return f"/api/files/{key}"

    def list_keys(self, prefix: str) -> list[tuple[str, float]]:
        dir_path = self._full_path(prefix)
        if not os.path.isdir(dir_path):
            return []
        results = []
        for filename in os.listdir(dir_path):
            filepath = os.path.join(dir_path, filename)
            if os.path.isfile(filepath):
                mtime = os.path.getmtime(filepath)
                key = f"{prefix}/{filename}" if not prefix.endswith("/") else f"{prefix}{filename}"
                results.append((key, mtime))
        return results


class S3Storage(StorageBackend):
    """Store files in an S3-compatible object store (OCI, AWS, R2, etc.)."""

    def __init__(
        self,
        bucket: str,
        endpoint_url: str,
        access_key_id: str,
        secret_access_key: str,
        region: str,
        presigned_url_expiry: int = 3600,
    ) -> None:
        import boto3

        self._bucket = bucket
        self._presigned_url_expiry = presigned_url_expiry
        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            region_name=region,
        )
        logger.info(
            "S3 storage initialized: bucket=%s endpoint=%s region=%s",
            bucket, endpoint_url, region,
        )

    def save(self, key: str, data: bytes) -> str:
        content_type = "image/jpeg"
        if key.endswith(".png"):
            content_type = "image/png"
        elif key.endswith(".webp"):
            content_type = "image/webp"

        self._client.put_object(
            Bucket=self._bucket,
            Key=key,
            Body=data,
            ContentType=content_type,
        )
        return key

    def delete(self, key: str) -> None:
        self._client.delete_object(Bucket=self._bucket, Key=key)

    def exists(self, key: str) -> bool:
        try:
            self._client.head_object(Bucket=self._bucket, Key=key)
            return True
        except self._client.exceptions.NoSuchKey:
            return False
        except Exception:
            # head_object raises ClientError with 404 code
            return False

    def url(self, key: str) -> str:
        return self._client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self._bucket, "Key": key},
            ExpiresIn=self._presigned_url_expiry,
        )

    def list_keys(self, prefix: str) -> list[tuple[str, float]]:
        results = []
        paginator = self._client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self._bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                mtime = obj["LastModified"].timestamp()
                results.append((obj["Key"], mtime))
        return results


def _create_storage() -> StorageBackend:
    """Create the storage backend based on settings."""
    backend = settings.storage_backend.lower()

    if backend == "s3":
        if not settings.s3_bucket:
            raise ValueError("S3_BUCKET is required when STORAGE_BACKEND=s3")
        return S3Storage(
            bucket=settings.s3_bucket,
            endpoint_url=settings.s3_endpoint_url,
            access_key_id=settings.s3_access_key_id,
            secret_access_key=settings.s3_secret_access_key,
            region=settings.s3_region,
            presigned_url_expiry=settings.s3_presigned_url_expiry,
        )

    # Default: local filesystem
    return LocalStorage(settings.upload_dir)


storage: StorageBackend = _create_storage()
