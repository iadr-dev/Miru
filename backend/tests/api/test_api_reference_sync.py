"""`docs/backend/api-reference.md` carries a generated route index. This fails the moment a route is
added, removed or renamed without regenerating it, so the documented surface cannot drift from the
code:

    cd backend/src && uv run python -m api.route_inventory --write
"""

from __future__ import annotations

from api.route_inventory import (
    ROUTES_BEGIN,
    ROUTES_END,
    reference_path,
    render_markdown,
    route_rows,
)


def test_route_index_in_api_reference_matches_the_code() -> None:
    document = reference_path().read_text(encoding="utf-8").replace("\r\n", "\n")
    begin = document.index(ROUTES_BEGIN)
    end = document.index(ROUTES_END) + len(ROUTES_END)

    assert document[begin:end] == render_markdown(route_rows()), (
        "docs/backend/api-reference.md route index is stale; regenerate it with "
        "`cd backend/src && uv run python -m api.route_inventory --write`"
    )


def test_every_route_has_a_section_row() -> None:
    rows = route_rows()
    assert rows, "no routes found"
    assert len({(r.method, r.path) for r in rows}) == len(rows), "duplicate method+path"
