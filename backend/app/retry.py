"""Retry-with-backoff for the pipeline's network boundaries.

Textract and the LLM refiners (issue #3/#4) call out to AWS and
OpenRouter/LiteLLM -- both fail transiently in production (throttling,
connection resets, 5xx) and those failures should not surface as a permanent
Document failure on the first blip. `with_backoff` is a small, injectable
retry loop (no external dependency) used at each of those call sites: it
retries only exceptions the caller identifies as transient, with exponential
backoff, and re-raises unchanged once attempts are exhausted so the normal
Celery dead-letter path (see app/celery_app.py) still takes over.

`sleep` is injectable so tests exercise real retry/backoff logic without
actually sleeping.
"""

from __future__ import annotations

import logging
import time
from typing import Callable, TypeVar

logger = logging.getLogger("reconcilio.retry")

T = TypeVar("T")

DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_BASE_DELAY = 0.1


def with_backoff(
    fn: Callable[[], T],
    *,
    op_name: str,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    base_delay: float = DEFAULT_BASE_DELAY,
    retry_on: tuple[type[BaseException], ...] = (Exception,),
    should_retry: Callable[[BaseException], bool] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    """Call `fn()`, retrying transient failures with exponential backoff.

    An exception is retried only if it is an instance of `retry_on` AND (no
    `should_retry` predicate is given, or the predicate returns True for it)
    -- anything else propagates on the first attempt. Once `max_attempts` is
    reached the last exception propagates unchanged.
    """

    attempt = 0
    while True:
        attempt += 1
        try:
            return fn()
        except retry_on as exc:
            transient = should_retry is None or should_retry(exc)
            if not transient or attempt >= max_attempts:
                logger.warning(
                    "retry.gave_up" if attempt >= max_attempts and transient else "retry.non_transient",
                    extra={"op": op_name, "attempt": attempt, "error_type": type(exc).__name__},
                )
                raise
            delay = base_delay * (2 ** (attempt - 1))
            logger.info(
                "retry.attempt",
                extra={
                    "op": op_name,
                    "attempt": attempt,
                    "delay_seconds": delay,
                    "error_type": type(exc).__name__,
                },
            )
            sleep(delay)
