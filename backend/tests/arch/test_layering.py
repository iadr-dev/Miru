"""Architecture layering gate — enforces the one-way dependency rule.

Walks the static import graph of `*` and fails on any edge that violates the one-way
dependency rule, or on any `sources` import from outside `dataaccess`/`ingest`. This is the
governance-adjacent test that keeps "callers name a capability, never a source" true as the
codebase grows — run it on every change, not just once at scaffold time.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[2] / "src"

# One direction only: interfaces -> product -> interaction -> runtime -> tools -> context
# -> dataaccess -> sources.
LAYER_ORDER: list[list[str]] = [
    ["api", "mcp"],  # interfaces
    ["accounts", "payments", "scheduler", "voice"],  # product
    [
        "orchestrator",
        "conversation",
        "memory",
        "events",
        "skills",
        "feedback",
        "usage",
        "personality",
        "pets",
        "research",
    ],  # interaction
    ["agent"],  # runtime
    ["tools"],
    ["context", "calendar", "analytics", "ingest", "llm", "attachments"],
    ["dataaccess"],
    ["sources"],
    # Foundation: persistence and shared contracts, importable from every layer above.
    ["db"],
    ["schemas", "config", "governance"],
]

LAYER_INDEX: dict[str, int] = {module: i for i, layer in enumerate(LAYER_ORDER) for module in layer}


def _iter_python_files() -> list[Path]:
    return sorted(SRC_ROOT.rglob("*.py"))


def _module_top_package(dotted: str) -> str | None:
    return dotted.split(".", 1)[0]


def _imports_in_file(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.append(node.module)
    return found


def test_no_upward_layer_imports() -> None:
    violations: list[str] = []
    for path in _iter_python_files():
        rel = path.relative_to(SRC_ROOT)
        owner = rel.parts[0]
        if owner not in LAYER_INDEX:
            continue
        owner_layer = LAYER_INDEX[owner]
        for imported in _imports_in_file(path):
            target = _module_top_package(imported)
            if target is None or target == owner or target not in LAYER_INDEX:
                continue
            target_layer = LAYER_INDEX[target]
            if target_layer < owner_layer:
                violations.append(
                    f"{rel} (layer {owner_layer}:{owner}) imports "
                    f"{imported} (layer {target_layer}:{target}) — upward edge"
                )
    assert not violations, "Layering violations:\n" + "\n".join(violations)


def test_sources_only_imported_from_dataaccess_or_ingest() -> None:
    allowed_importers = {"sources", "dataaccess", "ingest"}
    violations: list[str] = []
    for path in _iter_python_files():
        rel = path.relative_to(SRC_ROOT)
        owner = rel.parts[0]
        if owner in allowed_importers:
            continue
        for imported in _imports_in_file(path):
            target = _module_top_package(imported)
            if target == "sources":
                violations.append(f"{rel} imports {imported} — sources/ is I/O-only")
    assert not violations, "sources/ layering violations:\n" + "\n".join(violations)
