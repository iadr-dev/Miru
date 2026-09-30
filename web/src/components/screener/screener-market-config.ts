/**
 * Config-driven per-market screener setup (docs/web/screener/README.md §2) — one config object
 * per tab controlling its result-table columns. `factors`/`presets`/`signals` are NOT duplicated
 * here — those come live from `GET /analytics/screener/factors`/`presets` (backend `MarketProfile`
 * is the actual source of truth); this file only owns what's genuinely frontend-side: column
 * layout and per-market display labels for the results table.
 */
import type { ScreenerMarket } from "@/types/screener";

export interface ScreenerColumn {
  key: string;
  labelZh: string;
  labelEn: string;
  /** Signed cells (漲跌%/折溢價%) get --up/--down + ▲▼ + aria-label; never color-alone. */
  signed?: boolean;
  sponsor?: boolean;
}

export interface ScreenerMarketConfig {
  market: ScreenerMarket;
  columns: ScreenerColumn[];
}

const TW_COLUMNS: ScreenerColumn[] = [
  { key: "close", labelZh: "收盤", labelEn: "Close" },
  { key: "change_pct", labelZh: "漲跌%", labelEn: "Change%", signed: true },
  { key: "volume", labelZh: "成交量", labelEn: "Volume" },
  { key: "momentum_percentile", labelZh: "動能", labelEn: "Momentum" },
  {
    key: "chip_broker_net_buy",
    labelZh: "外資/投信連買",
    labelEn: "Foreign/trust streak",
    sponsor: true,
  },
];

const US_COLUMNS: ScreenerColumn[] = [
  { key: "price", labelZh: "股價", labelEn: "Price" },
  { key: "change_pct", labelZh: "漲跌%", labelEn: "Change%", signed: true },
  { key: "market_cap", labelZh: "市值", labelEn: "Market cap" },
  { key: "pe_ratio", labelZh: "本益比", labelEn: "PE" },
  { key: "pct_held_institutions", labelZh: "機構持股比例", labelEn: "Institutional %" },
  { key: "short_pct_of_float", labelZh: "放空比例", labelEn: "Short % of float" },
];

const ETF_COLUMNS: ScreenerColumn[] = [
  { key: "close", labelZh: "收盤", labelEn: "Close" },
  { key: "change_pct", labelZh: "漲跌%", labelEn: "Change%", signed: true },
  { key: "premium_discount_pct", labelZh: "折溢價%", labelEn: "Premium/discount%", signed: true },
  { key: "aum", labelZh: "規模", labelEn: "AUM" },
];

export const SCREENER_MARKET_CONFIGS: Record<ScreenerMarket, ScreenerMarketConfig> = {
  TW: { market: "TW", columns: TW_COLUMNS },
  US: { market: "US", columns: US_COLUMNS },
  ETF: { market: "ETF", columns: ETF_COLUMNS },
};
