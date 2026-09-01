import pytest

from app.documents import MAX_UPLOAD_SIZE_BYTES, validate_upload


@pytest.mark.parametrize(
    "filename,content_type",
    [
        ("invoice.pdf", "application/pdf"),
        ("receipt.jpg", "image/jpeg"),
        ("receipt.jpeg", "image/jpeg"),
        ("receipt.png", "image/png"),
        ("statement.csv", "text/csv"),
        ("statement.ofx", "application/x-ofx"),
    ],
)
def test_validate_upload_accepts_supported_types(filename, content_type):
    assert validate_upload(filename, content_type, 1024) is None


def test_validate_upload_rejects_unsupported_extension():
    error = validate_upload("archive.zip", "application/zip", 1024)
    assert error is not None
    assert "Unsupported file type" in error


def test_validate_upload_rejects_oversized_file():
    error = validate_upload("invoice.pdf", "application/pdf", MAX_UPLOAD_SIZE_BYTES + 1)
    assert error is not None
    assert "exceeds maximum size" in error


def test_validate_upload_rejects_empty_file():
    error = validate_upload("invoice.pdf", "application/pdf", 0)
    assert error is not None
    assert "empty" in error
