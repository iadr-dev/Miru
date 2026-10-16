"""策略盯盤 vocabulary shared by the scheduler, the API and the web types: the strategy keys, their
display names, and the fact phrasing of each condition the intraday check records.

Every phrase states what the data shows against a fixed level ("價格 872 高於布林上軌 868"); none
says what to do. The notification adds `NON_ADVICE_FOOTER` and passes the output scan like every
other alert (modules/14 §F, modules/20 §5c).
"""

from __future__ import annotations

from typing import Final, Literal

StrategyKey = Literal["volatility_squeeze", "oversold_bounce", "ad_divergence"]
STRATEGY_KEYS: Final[tuple[StrategyKey, ...]] = (
    "volatility_squeeze",
    "oversold_bounce",
    "ad_divergence",
)

STRATEGY_LABEL: Final[dict[str, str]] = {
    "volatility_squeeze": "波動率擠壓",
    "oversold_bounce": "超跌反彈",
    "ad_divergence": "量價背離",
}

# `{o}` is the observed value, `{r}` the level it was compared with.
CONDITION_PHRASE: Final[dict[str, str]] = {
    "price_above_upper_band": "價格 {o} 高於布林上軌 {r}",
    "volume_ratio_at_least": "量比 {o}（門檻 {r}）",
    "momentum_5d_positive": "價格 {o} 高於 5 日前收盤 {r}",
    "red_candle": "價格 {o} 高於開盤 {r}（紅K）",
    "volume_ratio_at_most": "量比 {o}（量縮，門檻 {r}）",
    "kd_oversold": "前一日 K 值 {o} 低於 {r}",
    "new_20d_low": "價格 {o} 低於 20 日低點 {r}",
    "rsi_higher_low": "RSI {o} 高於前低時的 {r}",
    "ad_higher_low": "A/D 線高於其 20 日低點",
}

# The same facts without the intraday numbers, for a reader whose plan does not include the
# intraday (Sponsor) data: the condition is named, the value is masked, never invented.
CONDITION_NAME: Final[dict[str, str]] = {
    "price_above_upper_band": "價格高於布林上軌",
    "volume_ratio_at_least": "量比達門檻",
    "momentum_5d_positive": "價格高於 5 日前收盤",
    "red_candle": "紅K",
    "volume_ratio_at_most": "量縮",
    "kd_oversold": "KD 低檔",
    "new_20d_low": "價格低於 20 日低點",
    "rsi_higher_low": "RSI 未創新低",
    "ad_higher_low": "A/D 線未創新低",
}


def phrase(key: str, observed: float | None, required: float | None) -> str:
    template = CONDITION_PHRASE.get(key)
    if template is None:
        return CONDITION_NAME.get(key, key)
    return template.format(o=_number(observed), r=_number(required))


def _number(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:,.2f}".rstrip("0").rstrip(".")


__all__ = [
    "CONDITION_NAME",
    "CONDITION_PHRASE",
    "STRATEGY_KEYS",
    "STRATEGY_LABEL",
    "StrategyKey",
    "phrase",
]
