"""The public API contract is snapshotted, so changing it is a visible, reviewed edit.

For every operation the snapshot records the required parameters, whether a request body is
required, and the response status codes. A route removed, a parameter newly required, a status code
dropped or any addition makes this fail with the diff; run

    UPDATE_OPENAPI_CONTRACT=1 uv run pytest tests/api/test_openapi_contract.py

to accept it, and the changed snapshot lands in the PR for review. Breaking changes (removals,
newly required inputs) are called out separately in the failure message so they are not missed."""

from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi.testclient import TestClient

SNAPSHOT = Path(__file__).with_name("openapi_contract.json")
_METHODS = {"get", "post", "put", "patch", "delete"}


def _contract(spec: dict[str, object]) -> dict[str, dict[str, object]]:
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


def test_the_api_contract_matches_the_committed_snapshot(client: TestClient) -> None:
    current = _contract(client.app.openapi())  # type: ignore[attr-defined]
    if os.environ.get("UPDATE_OPENAPI_CONTRACT"):
        SNAPSHOT.write_text(json.dumps(current, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        return
    committed = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    if current == committed:
        return
    added = sorted(set(current) - set(committed))
    changed = sorted(k for k in current if k in committed and current[k] != committed[k])
    breaking = _breaking(committed, current)
    raise AssertionError(
        "The API contract changed. If intended, re-run with UPDATE_OPENAPI_CONTRACT=1 and commit "
        f"tests/api/openapi_contract.json.\nBREAKING: {breaking or 'none'}\nadded: {added}\n"
        f"changed: {changed}"
    )
