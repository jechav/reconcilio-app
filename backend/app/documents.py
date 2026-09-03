"""Upload validation rules shared by the documents router and its tests."""

MAX_UPLOAD_SIZE_BYTES = 20 * 1024 * 1024  # 20MB

ALLOWED_CONTENT_TYPES = {
    "application/pdf",
    "image/jpeg",
    "image/png",
    "text/csv",
    "application/vnd.intu.qfx",  # OFX/QFX is served under several content-types in the wild
    "application/x-ofx",
    "application/octet-stream",
}

ALLOWED_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png", ".csv", ".ofx"}


def validate_upload(filename: str, content_type: str, size_bytes: int) -> str | None:
    """Return an error message if the upload is invalid, else None.

    Extension is the authoritative check (content-type headers for CSV/OFX
    are inconsistent across clients); content-type is checked only when it
    isn't the generic octet-stream fallback.
    """
    if size_bytes <= 0:
        return "File is empty"
    if size_bytes > MAX_UPLOAD_SIZE_BYTES:
        return f"File exceeds maximum size of {MAX_UPLOAD_SIZE_BYTES // (1024 * 1024)}MB"

    lowered = filename.lower()
    extension = "." + lowered.rsplit(".", 1)[-1] if "." in lowered else ""
    if extension not in ALLOWED_EXTENSIONS:
        allowed = ", ".join(sorted(ALLOWED_EXTENSIONS))
        return f"Unsupported file type '{extension}'. Allowed types: {allowed}"

    if content_type and content_type != "application/octet-stream" and content_type not in ALLOWED_CONTENT_TYPES:
        return f"Unsupported content type '{content_type}'"

    return None
