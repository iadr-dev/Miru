"""`Any` stays a deliberate, explained exception at untyped third-party edges.

ruff's ANN401 forbids an explicit `Any` in a signature. Where a library is genuinely untyped
(yfinance, pandas, PyJWT keys, the Anthropic stream-event union) the exception is written down
with a reason on the same line. A bare `# noqa: ANN401` fails here, so the reason cannot be
skipped; the layout of a named alias (`type YfObject = Any  # noqa: ANN401 - ...`) keeps each
justification in one place."""

from __future__ import annotations

import re
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[2] / "src"
_NOQA = re.compile(r"#\s*noqa:\s*[^#\n]*ANN401(?P<rest>[^\n]*)")


def test_every_any_exception_carries_a_reason() -> None:
    bare: list[str] = []
    for path in sorted(SRC_ROOT.rglob("*.py")):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            match = _NOQA.search(line)
            if match and not re.match(r"\s*-\s*\S", match.group("rest")):
                bare.append(f"{path.relative_to(SRC_ROOT).as_posix()}:{number}")
    assert not bare, f"`noqa: ANN401` without a ` - reason`: {bare}"
