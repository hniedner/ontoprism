"""Real behavioral tests for retry_with_backoff (no mocks — real counters/functions)."""

import pytest

from ontolib.common.error_handling import retry_with_backoff


class _BoomError(Exception):
    pass


@pytest.mark.unit
async def test_async_retries_then_succeeds() -> None:
    calls = {"n": 0}

    @retry_with_backoff(
        max_attempts=3, base_delay=0.0, retryable_exceptions=(_BoomError,)
    )
    async def flaky() -> str:
        calls["n"] += 1
        if calls["n"] < 3:
            raise _BoomError
        return "ok"

    assert await flaky() == "ok"
    assert calls["n"] == 3


@pytest.mark.unit
async def test_async_reraises_after_exhaustion() -> None:
    calls = {"n": 0}

    @retry_with_backoff(
        max_attempts=2, base_delay=0.0, retryable_exceptions=(_BoomError,)
    )
    async def always_fails() -> None:
        calls["n"] += 1
        raise _BoomError

    with pytest.raises(_BoomError):
        await always_fails()
    assert calls["n"] == 2


@pytest.mark.unit
def test_sync_retries_then_succeeds() -> None:
    calls = {"n": 0}

    @retry_with_backoff(
        max_attempts=3, base_delay=0.0, retryable_exceptions=(_BoomError,)
    )
    def flaky() -> int:
        calls["n"] += 1
        if calls["n"] < 2:
            raise _BoomError
        return 42

    assert flaky() == 42
    assert calls["n"] == 2


@pytest.mark.unit
def test_non_retryable_exception_propagates_immediately() -> None:
    calls = {"n": 0}

    @retry_with_backoff(
        max_attempts=3, base_delay=0.0, retryable_exceptions=(_BoomError,)
    )
    def raises_other() -> None:
        calls["n"] += 1
        raise ValueError("different")

    with pytest.raises(ValueError, match="different"):
        raises_other()
    assert calls["n"] == 1


@pytest.mark.unit
def test_invalid_max_attempts_rejected() -> None:
    with pytest.raises(ValueError, match="max_attempts"):
        retry_with_backoff(max_attempts=0)
