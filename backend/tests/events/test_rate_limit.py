from __future__ import annotations

from events.rate_limit import InMemoryRateLimiter


def test_allows_up_to_max_requests_then_blocks() -> None:
    limiter = InMemoryRateLimiter(max_requests=2, window_seconds=60.0)

    first = limiter.check("caller-1")
    second = limiter.check("caller-1")
    third = limiter.check("caller-1")

    assert first.allowed is True
    assert second.allowed is True
    assert third.allowed is False
    assert third.retry_after_seconds is not None


def test_separate_callers_have_independent_windows() -> None:
    limiter = InMemoryRateLimiter(max_requests=1, window_seconds=60.0)

    assert limiter.check("caller-a").allowed is True
    assert limiter.check("caller-b").allowed is True
    assert limiter.check("caller-a").allowed is False
