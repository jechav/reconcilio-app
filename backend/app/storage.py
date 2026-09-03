"""MinIO object storage access.

Design choice (AC3 of issue #2): the client uploads directly to MinIO via a
presigned PUT URL rather than proxying file bytes through the API server.
The backend validates the declared filename/content-type/size (AC2) and
creates the `Document` row *before* minting the presigned URL, so nothing
is queued until validation passes; the URL itself expires in
`PRESIGNED_URL_EXPIRY` and is never a long-lived or public link, and the
bucket is never made public. Region is pinned to skip MinIO's
auto-detection HEAD request when signing.
"""

from datetime import timedelta
from functools import lru_cache

from minio import Minio

from app.config import get_settings

PRESIGNED_URL_EXPIRY = timedelta(minutes=15)
_REGION = "us-east-1"


@lru_cache
def get_minio_client() -> Minio:
    """Client used for the API's own server-to-server calls (bucket admin)."""
    settings = get_settings()
    return Minio(
        settings.minio_endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
        secure=settings.minio_secure,
        region=_REGION,
    )


@lru_cache
def get_public_minio_client() -> Minio:
    """Client used only to sign URLs handed to the browser.

    Presigning computes a signature locally and never opens a connection,
    so this client can be pointed at `minio_public_endpoint` (e.g.
    "localhost:9000") even though that host may differ from the one the API
    container itself uses to reach MinIO (e.g. "minio:9000" inside the
    docker-compose network) — the browser, not the API, is what dereferences
    the resulting URL.
    """
    settings = get_settings()
    return Minio(
        settings.minio_public_endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
        secure=settings.minio_secure,
        region=_REGION,
    )


def ensure_bucket() -> None:
    settings = get_settings()
    client = get_minio_client()
    if not client.bucket_exists(settings.minio_bucket):
        client.make_bucket(settings.minio_bucket)


def presigned_put_url(key: str) -> str:
    """A short-lived, non-public URL the client PUTs the file bytes to."""
    settings = get_settings()
    ensure_bucket()
    client = get_public_minio_client()
    return client.presigned_put_object(settings.minio_bucket, key, expires=PRESIGNED_URL_EXPIRY)


def presigned_get_url(key: str) -> str:
    """A short-lived (never long-lived/public) URL for reading one object."""
    settings = get_settings()
    client = get_public_minio_client()
    return client.presigned_get_object(settings.minio_bucket, key, expires=PRESIGNED_URL_EXPIRY)


def get_object_bytes(key: str) -> bytes:
    """Read one object's full contents (the pipeline needs the file bytes
    in-process to hand to Textract/the LLM refinement client)."""
    settings = get_settings()
    client = get_minio_client()
    response = client.get_object(settings.minio_bucket, key)
    try:
        return response.read()
    finally:
        response.close()
        response.release_conn()
