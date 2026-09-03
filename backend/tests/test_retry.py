"""Unit tests for the generic retry-with-backoff helper (issue #7, AC1)."""

import pytest

from app.retry import with_backoff


class Transient(RuntimeError):
    pass


class Permanent(RuntimeError):
    pass


def test_retries_until_success_and_records_sleeps():
    calls = {"n": 0}
    sleeps: list[float] = []

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise Transient("boom")
        return "ok"

    result = with_backoff(
        flaky,
        op_name="test.flaky",
        max_attempts=5,
        base_delay=1.0,
        retry_on=(Transient,),
        sleep=sleeps.append,
    )

    assert result == "ok"
    assert calls["n"] == 3
    # Exponential backoff: 1.0, 2.0 (two waits before the third, successful, call)
    assert sleeps == [1.0, 2.0]


def test_exhausts_attempts_and_raises_last_error():
    calls = {"n": 0}

    def always_fails():
        calls["n"] += 1
        raise Transient(f"attempt {calls['n']}")

    with pytest.raises(Transient, match="attempt 3"):
        with_backoff(
            always_fails,
            op_name="test.always_fails",
            max_attempts=3,
            base_delay=0.0,
            retry_on=(Transient,),
            sleep=lambda _seconds: None,
        )

    assert calls["n"] == 3


def test_non_transient_error_is_not_retried():
    calls = {"n": 0}

    def fails_permanently():
        calls["n"] += 1
        raise Permanent("nope")

    with pytest.raises(Permanent):
        with_backoff(
            fails_permanently,
            op_name="test.permanent",
            max_attempts=5,
            base_delay=0.0,
            retry_on=(Transient, Permanent),
            should_retry=lambda exc: isinstance(exc, Transient),
            sleep=lambda _seconds: None,
        )

    assert calls["n"] == 1


def test_unmatched_exception_type_propagates_immediately():
    def raises_value_error():
        raise ValueError("not retryable at all")

    with pytest.raises(ValueError):
        with_backoff(
            raises_value_error,
            op_name="test.unmatched",
            retry_on=(Transient,),
            sleep=lambda _seconds: None,
        )
