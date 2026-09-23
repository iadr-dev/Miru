"use client";

import { useQuery } from "@tanstack/react-query";
import { useLocale, useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { formatNumber } from "@/app/[locale]/(app)/chat/cards/card-format";
import { CardSkeletonBody } from "@/components/market/card-skeleton";
import { SponsorTeaser } from "@/components/market/sponsor-teaser";
import { getActiveEtfReport } from "@/lib/api/etf-detail";
import { isMaskedTeaser } from "@/types/market";
import type { Market } from "@/types/settings";

const HOLDING_CHART_COLORS = [
  "var(--chart-1)",
  "var(--chart-2)",
  "var(--chart-3)",
  "var(--chart-4)",
] as const;
const TOP_N = 4;

/** 成分股 — genuinely absent for passive TW ETFs today (`sources/etf_pcf.py`'s own docstring:
 * per-constituent holdings are never ingested for passive/leveraged/inverse funds). Scoped to
 * active-management ETFs only, reusing `get_active_etf_report`'s already-live holding rows
 * (current_weight per constituent) — the same honest-scoping precedent `tracker-panel.tsx`
 * already established for the reverse (stock→ETF) relationship. A passive ETF symbol renders a
 * calm coverage-class empty state, never a fabricated donut. */
export function HoldingsPanel({
  symbol,
  market,
}: {
  readonly symbol: string;
  readonly market: Market;
}): ReactNode {
  const t = useTranslations("etf.holdings");
  const locale = useLocale();
  const query = useQuery({
    queryKey: ["active_etf_report", symbol, market],
    queryFn: () => getActiveEtfReport(symbol, market),
  });

  if (query.isLoading) return <CardSkeletonBody height={360} />;
  if (!query.data) return null;

  if (isMaskedTeaser(query.data)) {
    return (
      <section className="rounded-[var(--radius-card)] border border-[var(--border)] bg-[var(--surface)] p-5">
        <SponsorTeaser
          headline={t("title")}
          preview={query.data.preview}
          requiredTier={query.data.required_tier}
        >
          <div className="h-64 rounded-[var(--radius-input)] bg-[var(--surface-2)]" />
        </SponsorTeaser>
      </section>
    );
  }

  const data = query.data;
  const weighted = data.rows
    .filter((row) => row.current_weight != null)
    .sort((a, b) => (b.current_weight as number) - (a.current_weight as number));

  if (data.report_date == null || weighted.length === 0) {
    return (
      <section className="rounded-[var(--radius-card)] border border-[var(--border)] bg-[var(--surface)] p-5">
        <h2 className="mb-1 font-semibold text-[15px] text-[var(--text)]">{t("title")}</h2>
        <p className="mt-3 text-[13px] text-[var(--text-muted)] leading-[1.6]">
          {t("passiveUnavailable")}
        </p>
      </section>
    );
  }

  const top = weighted.slice(0, TOP_N);
  const othersWeight = weighted
    .slice(TOP_N)
    .reduce((sum, row) => sum + (row.current_weight as number), 0);

  return (
    <section className="rounded-[var(--radius-card)] border border-[var(--border)] bg-[var(--surface)] p-5">
      <div className="mb-3 flex items-baseline justify-between gap-2">
        <h2 className="font-semibold text-[15px] text-[var(--text)]">{t("title")}</h2>
        <span className="text-[11px] text-[var(--text-muted)]">
          {t("asOfDate", { date: data.report_date })}
        </span>
      </div>
      <p className="mb-4 text-[11px] text-[var(--text-muted)] leading-[1.5]">
        {t("activeOnlyCaveat")}
      </p>

      <div className="flex flex-col gap-2">
        {top.map((row, index) => (
          <WeightBar
            key={row.component_stock_id}
            label={row.component_stock_name}
            weight={row.current_weight as number}
            color={HOLDING_CHART_COLORS[index % HOLDING_CHART_COLORS.length] ?? "var(--chart-1)"}
            locale={locale}
          />
        ))}
        {othersWeight > 0 ? (
          <WeightBar
            label={t("others")}
            weight={othersWeight}
            color="var(--text-muted)"
            locale={locale}
          />
        ) : null}
      </div>
    </section>
  );
}

function WeightBar({
  label,
  weight,
  color,
  locale,
}: {
  readonly label: string;
  readonly weight: number;
  readonly color: string;
  readonly locale: string;
}): ReactNode {
  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-[12.5px] text-[var(--text)]">{label}</span>
        <span className="shrink-0 font-num text-[12.5px] tabular-nums" style={{ color }}>
          {formatNumber(weight, locale, { decimals: 2 })}%
        </span>
      </div>
      <div className="h-1.5 overflow-hidden rounded-full bg-[var(--surface-2)]">
        <div
          className="h-full rounded-full"
          style={{ width: `${Math.min(weight, 100)}%`, backgroundColor: color }}
        />
      </div>
    </div>
  );
}
