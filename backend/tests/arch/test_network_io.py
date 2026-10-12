"""Network I/O stays in a short, reviewed list of places.

`sources/` is where market-data acquisition lives (callers name a capability, never a source).
Everything else that opens a connection is infrastructure for the app's own lifecycle: the event
transport and rate limiter (Redis), the LLM and voice vendors, billing, OAuth and notification
delivery. Each of those is listed below with its reason. A new import of a network client anywhere
else fails this test, so adding one is a deliberate decision recorded here (and in the ADR), not
something that slips in. Stale entries fail too, so the list cannot rot.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[2] / "src"

# Top-level packages that open connections. `redis` and the vendor SDKs count: they are clients.
NETWORK_LIBRARIES = frozenset(
    {
        "httpx",
        "requests",
        "aiohttp",
        "urllib3",
        "socket",
        "websockets",
        "redis",
        "asyncpg",
        "anthropic",
        "openai",
        "google",
    }
)

# file (relative to src/) -> why it may use a network client.
ALLOWED: dict[str, str] = {
    # App lifecycle transport on Redis: events, rate limits, quota counters, cross-worker control.
    "api/routers/probes.py": "readiness probe pings Redis",
    "dataaccess/composition.py": "builds the Redis-backed source cache wrapper",
    "events/data_updates.py": "Redis pub/sub for data.updated",
    "events/factory.py": "selects the Redis event bus",
    "events/live_quote.py": "Redis pub/sub for live quotes",
    "events/live_quote_factory.py": "selects the Redis live-quote transport",
    "events/pubsub.py": "shared Redis pub/sub close helper",
    "events/rate_limit.py": "Redis rate limiter",
    "events/redis_bus.py": "Redis event bus",
    "events/turn_control.py": "cross-worker cancel/steer over Redis",
    "usage/finmind_pool.py": "Redis counters for the FinMind quota pool",
    # The shared pooled HTTP client every outbound caller leases from.
    "netclient/shared.py": "pooled httpx client",
    "netclient/retry.py": "bounded retry transport for safe reads",
    # LLM and voice vendors (the model providers are not market-data sources).
    "llm/anthropic_client.py": "Anthropic SDK",
    "llm/gemini_client.py": "Google Gemini SDK",
    "llm/openai_client.py": "OpenAI SDK",
    "llm/openai_compat.py": "OpenAI-compatible HTTP endpoints (Ollama, etc.)",
    "voice/providers.py": "OpenAI speech APIs",
    # Billing, sign-in and notification delivery: account lifecycle, not market data.
    "accounts/oauth_providers.py": "OAuth token exchange with Google/LINE/Telegram",
    "payments/client.py": "Paddle API",
    "payments/pricing.py": "Paddle price reads",
    "scheduler/delivery/transport.py": "outbound notification delivery (push/email/LINE/Telegram)",
}


def _network_imports() -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for path in sorted(SRC_ROOT.rglob("*.py")):
        rel = path.relative_to(SRC_ROOT)
        if rel.parts[0] == "sources":
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                names = [node.module]
            else:
                continue
            hits = {n.split(".", 1)[0] for n in names} & NETWORK_LIBRARIES
            if hits:
                found.setdefault(rel.as_posix(), set()).update(hits)
    return found


def test_network_clients_are_only_used_in_listed_modules() -> None:
    unlisted = {
        file: sorted(libs) for file, libs in _network_imports().items() if file not in ALLOWED
    }
    assert not unlisted, (
        "These modules import a network client but are not in ALLOWED (add an entry with a "
        f"reason, or move the I/O into sources/): {unlisted}"
    )


def test_the_allow_list_has_no_stale_entries() -> None:
    importing = set(_network_imports())
    stale = sorted(set(ALLOWED) - importing)
    assert not stale, f"ALLOWED lists modules that no longer import a network client: {stale}"
