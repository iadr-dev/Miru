from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from schemas.enums import Market


class Symbol(BaseModel):
    """A symbol is (exchange_symbol, market), not a bare string."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str  # canonical, market-scoped (e.g. "2330", "AAPL")
    market: Market
    yahoo: str | None = None  # provider-specific forms resolved by adapters (2330.TW, 7203.T)
    finmind: str | None = None
