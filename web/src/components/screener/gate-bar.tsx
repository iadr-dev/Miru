"use client";

import { CheckIcon, ShieldCheckIcon } from "@phosphor-icons/react/dist/ssr";
import { useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { cn } from "@/lib/cn";
import type { ScreenerGateOverrides, ScreenerMarket } from "@/types/screener";

/** Market-cap tier quick-select chips — TW in 億元 (1e8 NT$), US in $B (1e9 USD), matching each
 * market's own conventional unit (`gates.py`'s own `_TW_DEFAULTS`/`_US_DEFAULTS` comments use the
 * same units). Values are the RAW threshold `min_market_cap` expects, not a display-scaled
 * number — clicking a chip writes the real gate value directly. */
const TW_MARKET_CAP_TIERS: readonly { labelZh: string; value: number }[] = [
  { labelZh: "10億+", value: 1_000_000_000 },
  { labelZh: "100億+", value: 10_000_000_000 },
  { labelZh: "1000億+", value: 100_000_000_000 },
];
const US_MARKET_CAP_TIERS: readonly { labelZh: string; value: number }[] = [
  { labelZh: "$100M+", value: 100_000_000 },
  { labelZh: "$2B+", value: 2_000_000_000 },
  { labelZh: "$10B+", value: 10_000_000_000 },
];

/** ETF AUM tier chips (億元 NT$, raw NTD values — mirrors `min_aum`'s own storage scale, same
 * as `aum`'s factor-catalog field). */
const ETF_AUM_TIERS: readonly { labelZh: string; value: number }[] = [
  { labelZh: "50億+", value: 5_000_000_000 },
  { labelZh: "100億+", value: 10_000_000_000 },
  { labelZh: "500億+", value: 50_000_000_000 },
];

/** Liquidity/price/market-cap sliders + exclude-toggle pills, grouped under a labeled "market
 * risk protection" header with a shield icon (README.md §5.1) — active exclude pills carry a
 * checkmark, inputs carry their unit inline ($ prefix, 張/億元 suffix), matching the reference's
 * legible-form-group look rather than a bare row of unlabeled number boxes. Liquidity is a gate,
 * never a scored factor — this bar stays visually separated from the condition builder. */
export function GateBar({
  overrides,
  onChange,
  passedCount,
  market,
}: {
  readonly overrides: ScreenerGateOverrides;
  readonly onChange: (next: ScreenerGateOverrides) => void;
  readonly passedCount: number | undefined;
  readonly market: ScreenerMarket;
}): ReactNode {
  const t = useTranslations("screener.gateBar");
  const marketCapTiers = market === "US" ? US_MARKET_CAP_TIERS : TW_MARKET_CAP_TIERS;
  const marketCapUnit = market === "US" ? "$" : "億元";
  const volumeUnit = market === "US" ? t("volumeUnitShares") : t("volumeUnitLots");
  // 處置/注意/全額交割 are TW regulatory statuses with no US/ETF equivalent — the backend gate
  // only ever applies them to the TW branch (`engine.py`'s `_gated_tw_universe`; ETF explicitly
  // defaults them off, US's `_setup_us` doesn't apply gates at all yet), so showing the pills on
  // those tabs would offer a control that silently does nothing.
  const showRiskProtection = market === "TW";
  // `min_market_cap` defaults to 0.0 and is never applied for ETF (`gates.py`'s `_ETF_DEFAULTS`)
  // — an ETF's size is its AUM, a real condition-builder factor (`factor_catalog.py`'s `aum`
  // field), not a stock-style market cap. Showing this gate + stock-cap tiers on the ETF tab
  // would be a control that silently does nothing, same reasoning as the risk-protection pills.
  const showMarketCapGate = market !== "ETF";

  function togglePill(key: keyof ScreenerGateOverrides): void {
    onChange({ ...overrides, [key]: !overrides[key] });
  }

  return (
    <div className="flex flex-wrap items-center gap-4 border-[var(--border)] border-b bg-[var(--surface)] px-4 py-3">
      <label className="flex items-center gap-1.5 text-[13px] text-[var(--text-muted)]">
        {t("minPrice")}
        <span className="relative">
          <span className="pointer-events-none absolute inset-y-0 left-2 flex items-center text-[var(--text-muted)] text-xs">
            $
          </span>
          <input
            type="number"
            name="min_price"
            min={0}
            value={overrides.min_price ?? 0}
            onChange={(event) => onChange({ ...overrides, min_price: Number(event.target.value) })}
            className="min-h-11 w-24 rounded-[var(--radius-input)] border border-[var(--border)] bg-[var(--surface)] pl-5 pr-2 text-[var(--text)] text-sm tabular-nums focus-visible:outline-2 focus-visible:outline-[var(--accent)]"
          />
        </span>
      </label>

      <label className="flex items-center gap-1.5 text-[13px] text-[var(--text-muted)]">
        {t("minAvgVolume")}
        <span className="relative">
          <input
            type="number"
            name="min_avg_volume"
            min={0}
            value={overrides.min_avg_volume ?? 0}
            onChange={(event) =>
              onChange({ ...overrides, min_avg_volume: Number(event.target.value) })
            }
            className="min-h-11 w-28 rounded-[var(--radius-input)] border border-[var(--border)] bg-[var(--surface)] px-2 text-[var(--text)] text-sm tabular-nums focus-visible:outline-2 focus-visible:outline-[var(--accent)]"
          />
          <span className="pointer-events-none absolute inset-y-0 right-2 flex items-center text-[var(--text-muted)] text-xs">
            {volumeUnit}
          </span>
        </span>
      </label>

      {market === "ETF" ? (
        <label className="flex items-center gap-1.5 text-[13px] text-[var(--text-muted)]">
          {t("minAum")}
          <span className="relative">
            <input
              type="number"
              name="min_aum"
              min={0}
              value={overrides.min_aum ?? 0}
              onChange={(event) => onChange({ ...overrides, min_aum: Number(event.target.value) })}
              className="min-h-11 w-28 rounded-[var(--radius-input)] border border-[var(--border)] bg-[var(--surface)] px-2 text-[var(--text)] text-sm tabular-nums focus-visible:outline-2 focus-visible:outline-[var(--accent)]"
            />
            <span className="pointer-events-none absolute inset-y-0 right-2 flex items-center text-[var(--text-muted)] text-xs">
              億元
            </span>
          </span>
          <div className="flex gap-1">
            {ETF_AUM_TIERS.map((tier) => (
              <button
                key={tier.value}
                type="button"
                aria-pressed={overrides.min_aum === tier.value}
                onClick={() => onChange({ ...overrides, min_aum: tier.value })}
                className={cn(
                  "min-h-9 rounded-[var(--radius-pill)] px-2 font-num text-[11px] tabular-nums transition-colors",
                  overrides.min_aum === tier.value
                    ? "bg-[var(--accent-weak)] text-[var(--accent)]"
                    : "border border-[var(--border)] text-[var(--text-muted)] hover:text-[var(--text)]",
                )}
              >
                {tier.labelZh}
              </button>
            ))}
          </div>
        </label>
      ) : null}

      {market === "ETF" ? (
        <div className="flex items-center gap-2 rounded-[var(--radius-input)] border border-[var(--border)] bg-[var(--surface-2)] px-2.5 py-1.5">
          <ShieldCheckIcon size={14} weight="regular" className="text-[var(--text-muted)]" />
          <span className="text-[12px] text-[var(--text-muted)]">{t("etfProtection")}</span>
          <button
            type="button"
            aria-pressed={overrides.exclude_leveraged_inverse ?? true}
            onClick={() => togglePill("exclude_leveraged_inverse")}
            className={cn(
              "flex min-h-9 items-center gap-1 rounded-[var(--radius-pill)] px-2.5 font-medium text-[12px] transition-colors",
              (overrides.exclude_leveraged_inverse ?? true)
                ? "bg-[var(--accent-weak)] text-[var(--accent-text)]"
                : "border border-[var(--border)] text-[var(--text-muted)] hover:text-[var(--text)]",
            )}
          >
            {(overrides.exclude_leveraged_inverse ?? true) ? (
              <CheckIcon size={12} weight="bold" />
            ) : null}
            {t("excludeLeveragedInverse")}
          </button>
        </div>
      ) : null}

      {showMarketCapGate ? (
        <label className="flex items-center gap-1.5 text-[13px] text-[var(--text-muted)]">
          {t("minMarketCap")}
          <span className="relative">
            <input
              type="number"
              name="min_market_cap"
              min={0}
              value={overrides.min_market_cap ?? 0}
              onChange={(event) =>
                onChange({ ...overrides, min_market_cap: Number(event.target.value) })
              }
              className="min-h-11 w-28 rounded-[var(--radius-input)] border border-[var(--border)] bg-[var(--surface)] px-2 text-[var(--text)] text-sm tabular-nums focus-visible:outline-2 focus-visible:outline-[var(--accent)]"
            />
            <span className="pointer-events-none absolute inset-y-0 right-2 flex items-center text-[var(--text-muted)] text-xs">
              {marketCapUnit}
            </span>
          </span>
          <div className="flex gap-1">
            {marketCapTiers.map((tier) => (
              <button
                key={tier.value}
                type="button"
                aria-pressed={overrides.min_market_cap === tier.value}
                onClick={() => onChange({ ...overrides, min_market_cap: tier.value })}
                className={cn(
                  "min-h-9 rounded-[var(--radius-pill)] px-2 font-num text-[11px] tabular-nums transition-colors",
                  overrides.min_market_cap === tier.value
                    ? "bg-[var(--accent-weak)] text-[var(--accent)]"
                    : "border border-[var(--border)] text-[var(--text-muted)] hover:text-[var(--text)]",
                )}
              >
                {tier.labelZh}
              </button>
            ))}
          </div>
        </label>
      ) : null}

      {showRiskProtection ? (
        <div className="flex items-center gap-2 rounded-[var(--radius-input)] border border-[var(--border)] bg-[var(--surface-2)] px-2.5 py-1.5">
          <ShieldCheckIcon size={14} weight="regular" className="text-[var(--text-muted)]" />
          <span className="text-[12px] text-[var(--text-muted)]">{t("riskProtection")}</span>
          <div className="flex gap-1">
            {(
              [
                ["exclude_disposition", "excludeDisposition"],
                ["exclude_attention", "excludeAttention"],
                ["exclude_full_cash_delivery", "excludeFullCashDelivery"],
              ] as const
            ).map(([key, labelKey]) => {
              const active = overrides[key] ?? true;
              return (
                <button
                  key={key}
                  type="button"
                  aria-pressed={active}
                  onClick={() => togglePill(key)}
                  className={cn(
                    "flex min-h-9 items-center gap-1 rounded-[var(--radius-pill)] px-2.5 font-medium text-[12px] transition-colors",
                    active
                      ? "bg-[var(--accent-weak)] text-[var(--accent-text)]"
                      : "border border-[var(--border)] text-[var(--text-muted)] hover:text-[var(--text)]",
                  )}
                >
                  {active ? <CheckIcon size={12} weight="bold" /> : null}
                  {t(labelKey)}
                </button>
              );
            })}
          </div>
        </div>
      ) : null}

      {passedCount !== undefined ? (
        <span
          aria-live="off"
          className="ml-auto inline-flex items-center gap-1 rounded-[var(--radius-pill)] bg-[var(--surface-2)] px-2.5 py-1 font-num text-[13px] text-[var(--text)] tabular-nums"
        >
          {t("passed", { count: passedCount })}
        </span>
      ) : null}
    </div>
  );
}
