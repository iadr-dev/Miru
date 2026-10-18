"""`docs/backend/api-reference.md` carries a generated route index. This fails the moment a route is
added, removed or renamed without regenerating it, so the documented surface cannot drift from the
code. To refresh it after a route change:

    cd backend && UPDATE_API_REFERENCE=1 uv run pytest tests/api/test_api_reference_sync.py
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from api.route_inventory import (
    ROUTES_BEGIN,
    ROUTES_END,
    reference_path,
    render_markdown,
    route_rows,
    splice,
)


def test_route_index_in_api_reference_matches_the_code() -> None:
    path = reference_path()
    document = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    if os.environ.get("UPDATE_API_REFERENCE") == "1":
        path.write_text(splice(document, render_markdown(route_rows())), encoding="utf-8")
        document = path.read_text(encoding="utf-8")
    begin = document.index(ROUTES_BEGIN)
    end = document.index(ROUTES_END) + len(ROUTES_END)

    assert document[begin:end] == render_markdown(route_rows()), (
        "docs/backend/api-reference.md route index is stale; regenerate it with "
        "`cd backend && UPDATE_API_REFERENCE=1 uv run pytest tests/api/test_api_reference_sync.py`"
    )


def test_every_route_has_a_section_row() -> None:
    rows = route_rows()
    assert rows, "no routes found"
    assert len({(r.method, r.path) for r in rows}) == len(rows), "duplicate method+path"


# Routes the committed OpenAPI spec has but the index deliberately does not list: the MCP sign-in
# protocol endpoints (mounted at the host root, see mcp-claude-connector.md) and the connector's
# /healthz. Anything else missing is a router someone forgot to add to `ROUTER_SECTIONS`, as
# `scheduler.strategy_watch` once was.
_NOT_INDEXED = {
    ("GET", "/.well-known/oauth-authorization-server"),
    ("GET", "/api/v1/oauth/consent/{request_id}"),
    ("POST", "/api/v1/oauth/consent/{request_id}"),
    ("GET", "/healthz"),
    ("GET", "/oauth/authorize"),
    ("POST", "/oauth/register"),
    ("POST", "/oauth/revoke"),
    ("POST", "/oauth/token"),
}
_SPEC = Path(__file__).resolve().parents[3] / "web" / "openapi.json"
_PATH_CONVERTER = re.compile(r"\{(\w+):\w+\}")


def _bare(path: str) -> str:
    """`/me/devices/{token:path}` and `/me/devices/{token}` are the same route."""
    return _PATH_CONVERTER.sub(r"{\1}", path)


def test_every_route_of_the_app_is_in_the_index() -> None:
    spec = json.loads(_SPEC.read_text(encoding="utf-8"))
    operations = {
        (method.upper(), path)
        for path, item in spec["paths"].items()
        for method in item
        if method in {"get", "post", "put", "patch", "delete"}
    }
    indexed = {(r.method, _bare(r.path)) for r in route_rows()}
    missing = sorted(operations - indexed - _NOT_INDEXED)
    assert not missing, f"routes missing from api/route_inventory.py ROUTER_SECTIONS: {missing}"
    stale = sorted(_NOT_INDEXED - operations)
    assert not stale, f"_NOT_INDEXED lists routes the app no longer has: {stale}"


def test_the_strategy_watch_routes_are_indexed() -> None:
    paths = {(r.method, r.path) for r in route_rows() if "strategy-watch" in r.path}
    assert paths == {
        ("GET", "/api/v1/strategy-watch/subscription"),
        ("PUT", "/api/v1/strategy-watch/subscription"),
        ("DELETE", "/api/v1/strategy-watch/subscription"),
        ("GET", "/api/v1/strategy-watch/today"),
    }
