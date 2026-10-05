import type { AlertCondition, AlertRule, Market } from "@/types/settings";

/** One-click alert presets for the stock/ETF page bell. Each is a plain, data-observable fact about
 * the symbol — never a call to act — and only templates that really exist for the asset are
 * offered: 注意股 and 三大法人 data are Taiwan-only, and an ETF is not an exchange attention-stock
 * candidate. (ETF 折溢價 / holdings-change templates arrive with their ingest publishers.) */
export function presetsFor(market: Market, isEtf: boolean): AlertCondition[] {
  const presets: AlertCondition[] = [
    { kind: "daily_change", direction: "up", percent: 5 },
    { kind: "daily_change", direction: "down", percent: 5 },
    { kind: "volume_surge", multiple: 2, window: 20 },
  ];
  if (market === "TW" && !isEtf) {
    presets.unshift(
      { kind: "attention_entry" },
      { kind: "disposition_entry" },
      { kind: "month_revenue_published" },
      { kind: "institutional_streak", investor: "foreign", direction: "buy", days: 3 },
      { kind: "margin_balance_change", side: "margin", direction: "up", percent: 10 },
    );
  }
  if (market === "TW" && isEtf) {
    // 折溢價 exists for Taiwan ETFs only (a stock has no NAV row), so it is offered just here.
    presets.unshift(
      { kind: "etf_premium_discount", direction: "premium", percent: 1 },
      { kind: "etf_premium_discount", direction: "discount", percent: 1 },
    );
  }
  return presets;
}

/** The symbol's own rules (any status), newest first as the API returns them. Watchlist-scoped
 * rules are not listed here — they belong to the group, not this symbol. */
export function rulesForSymbol(rules: AlertRule[], symbol: string, market: Market): AlertRule[] {
  return rules.filter((rule) => rule.symbol === symbol && rule.market === market);
}

export function activeRuleCount(rules: AlertRule[]): number {
  return rules.filter((rule) => rule.enabled).length;
}
