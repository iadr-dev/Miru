"""Opt-in live News verification. Never run in the default suite.

Run manually:
    uv run pytest -m network tests/sources/test_news_network.py
"""

from __future__ import annotations

import pytest

from config.settings import get_settings
from schemas.enums import TLSPolicy
from schemas.source import HealthStatus
from sources.news import NewsAdapter

pytestmark = pytest.mark.network


async def test_live_fetch_news_taiwan_semiconductor() -> None:
    adapter = NewsAdapter(settings=get_settings())

    result = await adapter.fetch_news(query="TSMC", tls_policy=TLSPolicy.STRICT)

    assert result.health.status in {HealthStatus.OK, HealthStatus.STALE}
