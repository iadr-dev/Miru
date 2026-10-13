"""The non-negotiable governance hook: `pre_tool` (no full-market scan) and `pre_output`
(no trading language, caveats present).

This is a *separate* concern from the `sponsor` data-class entitlement gate (`tools.base.
dispatch_tool`) — that gate substitutes a masked teaser for unentitled paid-data calls;
this module never looks at tiers or billing at all. Both checks run on every relevant call,
independently, and neither can stand in for the other.

`check_tool_call` (the `pre_tool` full-market-scan veto) lives here because it is `agent`-runtime
-specific — nothing below `agent` in the layering graph proposes a tool call to veto. Every
other name below (`check_output`, `scan_structured_payload`, `HookVerdict`, etc. — the `pre_output`
non-trading/caveat scan) is re-exported unchanged from `governance.textscan`, a leaf package
(deliberately outside `tests/arch/test_layering.py`'s `LAYER_ORDER`, matching `llm/base.py`'s own
precedent) that lower layers (`analytics`, for its LLM-generated stock-summary output) can import
directly without creating a forbidden upward edge into `agent`. Every existing caller of this
module keeps working exactly as before — this split changes where the pure scan logic lives, not
its behavior or its public names.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final, cast

from governance.textscan import (
    NON_TRADING_CAVEAT,
    REDACTION_NOTICE_EN,
    REDACTION_NOTICE_ZH,
    HookVerdict,
    check_output,
    scan_sentence,
    scan_structured_payload,
    split_sentences,
    split_sentences_streaming,
)

# ---- pre_tool: no full-market scan ----

_FULL_MARKET_SCAN_KEYS: Final[tuple[str, ...]] = (
    "scan_all",
    "full_market",
    "whole_market",
    "market_wide",
    "all_symbols",
)
_FULL_MARKET_SCAN_VALUES: Final[frozenset[str]] = frozenset({"*", "all", "all_symbols", "market"})
_MAX_SYMBOLS_PER_CALL: Final[int] = 20


def check_tool_call(tool_name: str, arguments: Mapping[str, object]) -> HookVerdict:
    """Reject tool arguments that request the whole market.

    Every immediate/deferred tool built so far already requires a single `(symbol, market)`
    pair, so this should rarely trigger in practice — it exists as the enforced seam itself,
    not as a substitute for narrow tool schemas.
    """
    for key, value in arguments.items():
        lowered_key = key.lower()
        if any(marker in lowered_key for marker in _FULL_MARKET_SCAN_KEYS):
            return HookVerdict(
                allowed=False,
                reason=f"{tool_name}: argument {key!r} requests a full-market scan",
            )
        if isinstance(value, str) and value.strip().lower() in _FULL_MARKET_SCAN_VALUES:
            return HookVerdict(
                allowed=False,
                reason=f"{tool_name}: {key!r}={value!r} requests a full-market scan",
            )
        if isinstance(value, list) and len(cast("list[object]", value)) > _MAX_SYMBOLS_PER_CALL:
            return HookVerdict(
                allowed=False,
                reason=f"{tool_name}: {key!r} names too many symbols for one bounded call",
            )
    return HookVerdict(allowed=True)


__all__ = [
    "NON_TRADING_CAVEAT",
    "REDACTION_NOTICE_EN",
    "REDACTION_NOTICE_ZH",
    "HookVerdict",
    "check_output",
    "check_tool_call",
    "scan_sentence",
    "scan_structured_payload",
    "split_sentences",
    "split_sentences_streaming",
]
