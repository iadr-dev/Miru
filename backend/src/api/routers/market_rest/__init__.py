"""Thin REST over the already-built market / derivatives / macro agent tools.

Each route is a direct projection of one (or a small bundle of) registry tool(s), dispatched
through `tools.base.dispatch_tool` exactly as `market.py`'s `/quote` does — never a tool's own
`run()` — so the sponsor-entitlement gate applies uniformly: a Sponsor-classed tool returns the
masked-teaser shape at HTTP 200 for an unentitled caller, real data for a Premium/Pro one.
`optional_user` supplies the (possibly anonymous) entitlement.

One route the consolidated interface reference lists here is deliberately absent: its named
backing tool (`get_market_context`) was never built — no adapter/capability backs it — so wiring
a route that dispatched a nonexistent tool would 500 by construction. It is omitted rather than
faked. `get_advance_decline`, `get_index_contribution`, and `get_sector_heatmap` (產業別漲跌熱力圖)
— previously in the same absent list (`get_sector_indices` was the placeholder name for the
heatmap concept before its two real ingredients — `STOCK_INFO_JOB`'s industry roster and
`PRICE_EOD_JOB`'s closes — were identified) — are now real, derived-store-backed tools
(`tools/market/`) and have routes below (`/market/advance-decline`,
`/market/index-contribution`, `/market/sector-heatmap`). `get_taiex_vix`
(`/derivatives/taiex-vix`) and `get_international_stock` (`/market/international-index`) are
likewise real, already-built tools that simply lacked routes before this pass — never listed in
this module's own "never built"
absent-list above (that claim would have been stale/wrong). `run_tag_screener`/`get_tag_catalog`
(`/market/tag-screener`, `/market/tag-catalog`) are likewise real, already-built tools that simply
lacked routes before this pass.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from api.rate_guard import guard_reads
from api.routers.market_rest import macro_news, market, stock, symbols

router = APIRouter(tags=["market-data"], dependencies=[Depends(guard_reads)])
for _part in (market, symbols, macro_news, stock):
    router.include_router(_part.router)

__all__ = ["router"]
