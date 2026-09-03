"""Regression test: a presigned URL must be signed for the host the
*browser* can reach, not the docker-compose-internal host the API uses to
talk to MinIO itself. See app/storage.py's get_public_minio_client."""

from urllib.parse import urlparse

from app import storage
from app.config import Settings


def _settings(**overrides):
    return Settings(
        minio_endpoint="minio:9000",
        minio_public_endpoint="localhost:9000",
        **overrides,
    )


def test_presigned_put_url_uses_public_endpoint_not_internal_one(monkeypatch):
    monkeypatch.setattr(storage, "get_settings", _settings)
    monkeypatch.setattr(storage, "ensure_bucket", lambda: None)
    storage.get_public_minio_client.cache_clear()

    try:
        url = storage.presigned_put_url("org/some-key.pdf")
    finally:
        storage.get_public_minio_client.cache_clear()

    assert urlparse(url).hostname == "localhost"
    assert urlparse(url).port == 9000


def test_presigned_get_url_uses_public_endpoint_not_internal_one(monkeypatch):
    monkeypatch.setattr(storage, "get_settings", _settings)
    storage.get_public_minio_client.cache_clear()

    try:
        url = storage.presigned_get_url("org/some-key.pdf")
    finally:
        storage.get_public_minio_client.cache_clear()

    assert urlparse(url).hostname == "localhost"
    assert urlparse(url).port == 9000
