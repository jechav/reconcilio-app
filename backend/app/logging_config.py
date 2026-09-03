"""Structured JSON logging for the API and Celery worker (issue #7, AC4).

Every log line is a single-line JSON object: timestamp, level, logger name,
message, plus whatever `extra=` fields the call site attached. Log calls in
`app/pipeline.py` and `app/celery_app.py` attach only ids, counts, statuses
and durations -- never a Document's filename, raw bytes, or an extracted
field's value -- so a log line is always safe to ship to a third-party
aggregator without leaking a tenant's document contents.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

#: Attributes every stdlib LogRecord carries -- anything else on the record
#: came from the call site's `extra=` and is included in the JSON output.
_STANDARD_RECORD_ATTRS = frozenset(logging.LogRecord("", 0, "", 0, "", (), None).__dict__.keys())


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _STANDARD_RECORD_ATTRS and key != "message":
                payload[key] = value
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


_configured = False


def configure_logging(level: int = logging.INFO) -> None:
    """Idempotent: safe to call from both the API and worker entrypoints,
    and safe to call more than once (e.g. under a test runner)."""
    global _configured
    if _configured:
        return
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
    _configured = True
