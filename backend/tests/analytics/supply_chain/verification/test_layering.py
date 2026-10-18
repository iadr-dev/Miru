"""The research pipeline reaches the network only through the injected SearchProvider, whose one
real implementation goes through `dataaccess.web_search`. Nothing here may import `sources` or a
network client."""

from __future__ import annotations

import ast
from pathlib import Path

PACKAGE = (
    Path(__file__).resolve().parents[4] / "src" / "analytics" / "supply_chain" / "verification"
)
FORBIDDEN_ROOTS = {"sources", "httpx", "requests", "aiohttp", "socket", "urllib3", "websockets"}


def _imports(path: Path) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module.split(".")[0])
    return found


def test_verification_package_has_no_network_or_sources_imports() -> None:
    files = sorted(PACKAGE.glob("*.py"))
    assert len(files) >= 10
    offenders = {
        f.name: _imports(f) & FORBIDDEN_ROOTS for f in files if _imports(f) & FORBIDDEN_ROOTS
    }
    assert offenders == {}


def test_the_only_network_path_is_the_dataaccess_gateway() -> None:
    importers = [f.name for f in PACKAGE.glob("*.py") if "dataaccess" in _imports(f)]
    assert importers == ["search.py"]


def test_urllib_request_is_not_used() -> None:
    for f in PACKAGE.glob("*.py"):
        assert "urllib.request" not in f.read_text(encoding="utf-8")
