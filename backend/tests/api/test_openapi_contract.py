"""The public API contract is committed as the full OpenAPI spec (`web/openapi.json`), so changing
it is a visible, reviewed edit — and it is the spec the web app generates its response types from
(`pnpm gen:api`), so the frontend's compile-time checks are only as current as this file.

Any difference between the running app's spec and the committed one fails this test. The message
separates BREAKING changes (a removed route, a newly required input, a dropped status code) from
additions and other edits. To accept an intended change:

    UPDATE_OPENAPI_CONTRACT=1 uv run pytest tests/api/test_openapi_contract.py

then run `pnpm gen:api` in `web/` and commit both files."""

from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi.testclient import TestClient

WEB_SPEC = Path(__file__).resolve().parents[3] / "web" / "openapi.json"
_METHODS = {"get", "post", "put", "patch", "delete"}


def _operations(spec: dict[str, object]) -> dict[str, dict[str, object]]:
    """Each operation reduced to what a caller can break on: required inputs and status codes."""
    contract: dict[str, dict[str, object]] = {}
    paths = spec["paths"]
    assert isinstance(paths, dict)
    for path, operations in sorted(paths.items()):
        for method, operation in sorted(operations.items()):
            if method not in _METHODS:
                continue
            required = sorted(
                f"{p['in']}:{p['name']}"
                for p in operation.get("parameters", [])
                if p.get("required")
            )
            body = operation.get("requestBody") or {}
            contract[f"{method.upper()} {path}"] = {
                "required_params": required,
                "body_required": bool(body.get("required")),
                "responses": sorted(operation.get("responses", {})),
            }
    return contract


def _breaking(old: dict[str, dict[str, object]], new: dict[str, dict[str, object]]) -> list[str]:
    problems: list[str] = []
    for key, before in old.items():
        after = new.get(key)
        if after is None:
            problems.append(f"removed: {key}")
            continue
        added = set(after["required_params"]) - set(before["required_params"])  # type: ignore[arg-type]
        if added:
            problems.append(f"newly required on {key}: {sorted(added)}")
        if after["body_required"] and not before["body_required"]:
            problems.append(f"request body now required on {key}")
        dropped = set(before["responses"]) - set(after["responses"])  # type: ignore[arg-type]
        if dropped:
            problems.append(f"response codes dropped on {key}: {sorted(dropped)}")
    return problems


def test_the_committed_spec_matches_the_app(client: TestClient) -> None:
    spec = client.app.openapi()  # type: ignore[attr-defined]
    current = json.dumps(spec, indent=1, sort_keys=True, ensure_ascii=False) + "\n"
    if os.environ.get("UPDATE_OPENAPI_CONTRACT"):
        WEB_SPEC.write_text(current, encoding="utf-8", newline="\n")
        return
    committed_text = WEB_SPEC.read_text(encoding="utf-8")
    if committed_text == current:
        return
    old = _operations(json.loads(committed_text))
    new = _operations(spec)
    raise AssertionError(
        "web/openapi.json no longer matches the API. If intended, re-run with "
        "UPDATE_OPENAPI_CONTRACT=1, then `pnpm gen:api` in web/, and commit both.\n"
        f"BREAKING: {_breaking(old, new) or 'none'}\n"
        f"added routes: {sorted(set(new) - set(old))}\n"
        f"changed operations: {sorted(k for k in new if k in old and new[k] != old[k])}\n"
        "(other differences are in schemas/descriptions — `git diff web/openapi.json` after the "
        "update shows them)"
    )
