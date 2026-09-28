"""Default 自選股 (watchlist) starter presets — seeded once per user, per `(market, asset_class)`
combo that has zero non-deleted groups (`memory.watchlists.WatchlistStore.seed_default_presets`).

Plain Python literals, not YAML (unlike `config/concept_themes.yaml`): these 16 groups are fixed
UI content decided once with the product owner, not researched/edited at runtime by an operator
pipeline — a data file would just add an extra parse step for content that never changes without
a code review anyway. Every symbol below was verified real (a real, currently-listed company/ETF)
against this app's own `/market/symbol-info` and `/market/etf-list` endpoints, or via live lookup,
before being added here — never guessed.
"""

from __future__ import annotations

from dataclasses import dataclass

from schemas.enums import Market

_STOCK = "stock"
_ETF = "etf"


@dataclass(frozen=True)
class WatchlistPreset:
    name: str
    market: Market
    asset_class: str
    symbols: tuple[str, ...]


# Always-seeded empty catch-all group, one per combo below — gives every user a themeless
# default target to star into even if they delete every theme preset (seeding an empty-symbols
# preset is already safe: the seed loop's `for symbol in preset.symbols` simply doesn't execute).
_MY_WATCHLIST_TW_STOCK = WatchlistPreset(
    name="我的自選股",
    market=Market.TW,
    asset_class=_STOCK,
    symbols=(),
)
_MY_WATCHLIST_US_STOCK = WatchlistPreset(
    name="我的自選股",
    market=Market.US,
    asset_class=_STOCK,
    symbols=(),
)
_MY_WATCHLIST_TW_ETF = WatchlistPreset(
    name="我的自選股",
    market=Market.TW,
    asset_class=_ETF,
    symbols=(),
)

# TW stocks — 5 concept-theme baskets.
_TW_SPACEX_SUPPLY_CHAIN = WatchlistPreset(
    name="SpaceX 供應鏈",
    market=Market.TW,
    asset_class=_STOCK,
    symbols=("3665", "3491", "2455", "4979", "2059", "6141", "3081", "6213"),
)
_TW_DEFENSE_DRONE = WatchlistPreset(
    name="軍工&無人機",
    market=Market.TW,
    asset_class=_STOCK,
    symbols=("2634", "1503", "2371", "4551", "1522", "4526"),
)
_TW_MEMORY = WatchlistPreset(
    name="記憶體",
    market=Market.TW,
    asset_class=_STOCK,
    symbols=("2408", "8299", "5347", "4919", "3532", "8054", "6533"),
)
_TW_FINANCIAL_AIRLINE = WatchlistPreset(
    name="金融&航空",
    market=Market.TW,
    asset_class=_STOCK,
    symbols=("2882", "2881", "2891", "2886", "2884", "2885", "2610", "2618"),
)
_TW_SEMICONDUCTOR = WatchlistPreset(
    name="半導體",
    market=Market.TW,
    asset_class=_STOCK,
    symbols=("2330", "2454", "3711", "2303", "3661", "3529", "3034", "3413"),
)

# US stocks — 5 concept-theme baskets (verified via live lookup this session; CEG/VST
# deliberately appear in both AI-Infra and Nuclear — real overlap, not a bug).
_US_AI_INFRASTRUCTURE = WatchlistPreset(
    name="AI Infrastructure & Data Centers",
    market=Market.US,
    asset_class=_STOCK,
    symbols=("NVDA", "AVGO", "MU", "VRT", "SMCI", "ANET", "ETN", "VST", "CEG", "ORCL"),
)
_US_NUCLEAR_SMR = WatchlistPreset(
    name="Nuclear Power & SMRs",
    market=Market.US,
    asset_class=_STOCK,
    symbols=("CEG", "VST", "NRG", "OKLO", "SMR", "BWXT", "CCJ", "LEU", "GEV"),
)
_US_QUANTUM_COMPUTING = WatchlistPreset(
    name="Quantum Computing",
    market=Market.US,
    asset_class=_STOCK,
    symbols=("IONQ", "RGTI", "QBTS", "IBM", "GOOGL", "MSFT", "HON"),
)
_US_DEFENSE_DRONE = WatchlistPreset(
    name="Defense & Drones",
    market=Market.US,
    asset_class=_STOCK,
    symbols=("LMT", "RTX", "NOC", "GD", "KTOS", "AVAV", "RCAT", "PLTR"),
)
_US_STABLECOIN_CRYPTO = WatchlistPreset(
    name="Stablecoins & Crypto Infrastructure",
    market=Market.US,
    asset_class=_STOCK,
    symbols=("COIN", "CRCL", "MSTR", "MA", "V", "PYPL", "XYZ", "HOOD"),
)

# TW ETFs — 3 baskets, top-by-real-AUM verified against /market/etf-list?market=TW this session.
_ETF_ACTIVE = WatchlistPreset(
    name="Active ETF",
    market=Market.TW,
    asset_class=_ETF,
    symbols=("00981A", "00403A", "00991A", "00982A", "00992A", "00406A", "00400A"),
)
_ETF_LEVERAGED = WatchlistPreset(
    name="Leveraged ETF",
    market=Market.TW,
    asset_class=_ETF,
    symbols=("00631L", "00685L", "00675L", "00632R", "00663L", "00670L", "00637L"),
)
_ETF_POPULAR = WatchlistPreset(
    name="Popular ETF",
    market=Market.TW,
    asset_class=_ETF,
    symbols=("0050", "0056", "00878", "00919", "006208", "0052", "00881"),
)

DEFAULT_WATCHLIST_PRESETS: tuple[WatchlistPreset, ...] = (
    _MY_WATCHLIST_TW_STOCK,
    _MY_WATCHLIST_US_STOCK,
    _MY_WATCHLIST_TW_ETF,
    _TW_SPACEX_SUPPLY_CHAIN,
    _TW_DEFENSE_DRONE,
    _TW_MEMORY,
    _TW_FINANCIAL_AIRLINE,
    _TW_SEMICONDUCTOR,
    _US_AI_INFRASTRUCTURE,
    _US_NUCLEAR_SMR,
    _US_QUANTUM_COMPUTING,
    _US_DEFENSE_DRONE,
    _US_STABLECOIN_CRYPTO,
    _ETF_ACTIVE,
    _ETF_LEVERAGED,
    _ETF_POPULAR,
)


def presets_for(market: Market, asset_class: str) -> tuple[WatchlistPreset, ...]:
    """Every preset scoped to `(market, asset_class)` — the seeding call's own filter, so a
    caller seeding one tab never touches the other two."""
    return tuple(
        preset
        for preset in DEFAULT_WATCHLIST_PRESETS
        if preset.market is market and preset.asset_class == asset_class
    )


__all__ = ["DEFAULT_WATCHLIST_PRESETS", "WatchlistPreset", "presets_for"]
