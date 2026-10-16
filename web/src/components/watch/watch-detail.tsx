"use client";

import { ArrowSquareOutIcon, CheckCircleIcon, CircleIcon } from "@phosphor-icons/react";
import { useLocale, useTranslations } from "next-intl";
import type { ReactNode } from "react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { keyLevel } from "@/components/watch/watch-format";
import { usePriceHistory } from "@/hooks/use-stock-data";
import { Link } from "@/i18n/navigation";
import { cn } from "@/lib/cn";
import { formatNumber } from "@/lib/format-number";
import type { StrategyCondition, StrategyWatchItem } from "@/types/strategy-watch";

const CHART_DAYS = 60;

/** The selected stock: its last 60 closes with the trigger level drawn, every condition at the
 * latest check with what was observed against what was required, and the context facts. */
export function WatchDetail({
  item,
  masked,
}: {
  item: StrategyWatchItem | null;
  masked: boolean;
}): ReactNode {
  const t = useTranslations("watch");
  if (item === null) {
    return <p className="p-4 text-[13px] text-[var(--text-muted)]">{t("detail.select")}</p>;
  }
  const triggers = item.conditions.filter((c) => c.kind === "trigger");
  const guards = item.conditions.filter((c) => c.kind === "guard");
  return (
    <div className="space-y-4 p-4">
      <header className="flex items-start justify-between gap-3">
        <div>
          <h3 className="font-semibold text-[15px] text-[var(--text)]">
            {item.name ?? item.symbol}{" "}
            <span className="font-normal font-num text-[13px] text-[var(--text-muted)]">
              {item.symbol}
            </span>
          </h3>
          <p className="text-[12px] text-[var(--text-muted)]">
            {t(`strategies.${item.strategy}.name`)} ·{" "}
            {t("detail.daysOnList", { days: item.days_on_list })}
          </p>
        </div>
        <Link
          href={`/stock/${encodeURIComponent(item.symbol)}`}
          className="inline-flex min-h-11 items-center gap-1 text-[12.5px] text-[var(--accent-text)] hover:underline"
        >
          {t("detail.openStock")}
          <ArrowSquareOutIcon size={14} aria-hidden />
        </Link>
      </header>

      <LevelChart item={item} />

      <section aria-labelledby="watch-triggers">
        <h4 id="watch-triggers" className="mb-1.5 font-medium text-[12.5px] text-[var(--text)]">
          {t("detail.conditions")}
          {item.latest_slot ? (
            <span className="ml-1.5 font-normal text-[var(--text-muted)]">{item.latest_slot}</span>
          ) : null}
        </h4>
        {triggers.length === 0 ? (
          <p className="text-[12.5px] text-[var(--text-muted)]">{t("detail.notChecked")}</p>
        ) : (
          <ConditionList conditions={triggers} />
        )}
      </section>

      {guards.length > 0 ? (
        <section aria-labelledby="watch-guards">
          <h4 id="watch-guards" className="mb-1.5 font-medium text-[12.5px] text-[var(--text)]">
            {t("detail.guards")}
          </h4>
          <ConditionList conditions={guards} />
        </section>
      ) : null}

      <ContextFacts item={item} />
      {masked ? <p className="text-[11.5px] text-[var(--text-muted)]">{t("masked")}</p> : null}
    </div>
  );
}

function ConditionList({ conditions }: { conditions: readonly StrategyCondition[] }): ReactNode {
  const t = useTranslations("watch");
  const locale = useLocale();
  return (
    <ul className="space-y-1">
      {conditions.map((c) => (
        <li key={c.key} className="flex items-center justify-between gap-2 text-[12.5px]">
          <span className="flex items-center gap-1.5">
            {c.met ? (
              <CheckCircleIcon
                size={15}
                weight="fill"
                className="text-[var(--accent)]"
                aria-hidden
              />
            ) : (
              <CircleIcon size={15} className="text-[var(--text-muted)]" aria-hidden />
            )}
            <span className={cn(c.met ? "text-[var(--text)]" : "text-[var(--text-muted)]")}>
              {t(`conditions.${c.key}`)}
            </span>
            <span className="sr-only">{c.met ? "✓" : "✗"}</span>
          </span>
          {c.observed !== null || c.required !== null ? (
            <span className="font-num text-[11.5px] text-[var(--text-muted)] tabular-nums">
              {formatNumber(c.observed, locale, { decimals: 2 })}
              {" / "}
              {formatNumber(c.required, locale, { decimals: 2 })}
            </span>
          ) : null}
        </li>
      ))}
    </ul>
  );
}

function ContextFacts({ item }: { item: StrategyWatchItem }): ReactNode {
  const t = useTranslations("watch.context");
  const heading = useTranslations("watch.detail")("context");
  const facts: string[] = [];
  if (item.industry && item.sector_strength_pct !== null) {
    facts.push(t("sector", { industry: item.industry, pct: Math.round(item.sector_strength_pct) }));
  }
  for (const [key, streak] of [
    ["foreign", item.foreign_streak],
    ["trust", item.trust_streak],
  ] as const) {
    if (Math.abs(streak) >= 2) {
      facts.push(t(key, { days: Math.abs(streak), side: t(streak > 0 ? "buy" : "sell") }));
    }
  }
  if (item.large_holder_pct !== null && item.large_holder_change_4w !== null) {
    const change = item.large_holder_change_4w;
    facts.push(
      t("largeHolder", {
        pct: item.large_holder_pct.toFixed(1),
        change: `${change >= 0 ? "+" : ""}${change.toFixed(2)}`,
      }),
    );
  }
  if (item.margin_change_20d_pct !== null) {
    const margin = item.margin_change_20d_pct;
    const text = t("margin", { pct: `${margin >= 0 ? "+" : ""}${margin.toFixed(1)}` });
    facts.push(margin > 20 ? `${text}（${t("marginSurge")}）` : text);
  }
  if (item.headline) facts.push(t("headline", { text: item.headline }));
  if (facts.length === 0) return null;
  return (
    <section aria-labelledby="watch-context">
      <h4 id="watch-context" className="mb-1.5 font-medium text-[12.5px] text-[var(--text)]">
        {heading}
      </h4>
      <ul className="space-y-1 text-[12.5px] text-[var(--text-muted)]">
        {facts.map((fact) => (
          <li key={fact}>{fact}</li>
        ))}
      </ul>
    </section>
  );
}

function LevelChart({ item }: { item: StrategyWatchItem }): ReactNode {
  const t = useTranslations("watch");
  const locale = useLocale();
  const history = usePriceHistory(item.symbol, "TW", CHART_DAYS);
  const level = keyLevel(item);
  const floor = item.range_low_20d ?? item.flag_low;
  const rows = (history.data?.rows ?? []).map((r) => ({
    date: r.trade_date.slice(5),
    close: r.close,
  }));
  if (rows.length === 0) {
    return (
      <div
        className="h-40 animate-pulse rounded-[var(--radius-input)] bg-[var(--surface-2)]"
        aria-hidden
      />
    );
  }
  return (
    <figure>
      <figcaption className="mb-1 text-[11.5px] text-[var(--text-muted)]">
        {t("detail.chart")}
      </figcaption>
      <div className="h-44">
        <ResponsiveContainer>
          <LineChart data={rows} margin={{ top: 6, right: 8, bottom: 0, left: 0 }}>
            <CartesianGrid vertical={false} stroke="var(--grid)" />
            <XAxis
              dataKey="date"
              tick={{ fill: "var(--text-muted)", fontSize: 10 }}
              stroke="var(--border)"
              minTickGap={24}
            />
            <YAxis
              domain={["auto", "auto"]}
              tick={{ fill: "var(--text-muted)", fontSize: 10 }}
              stroke="var(--border)"
              width={44}
              tickFormatter={(v: number) => formatNumber(v, locale, { decimals: 1 })}
            />
            <Tooltip
              contentStyle={{ background: "var(--surface)", border: "1px solid var(--border)" }}
              labelStyle={{ color: "var(--text)" }}
              itemStyle={{ color: "var(--text)" }}
            />
            <Line
              type="monotone"
              dataKey="close"
              stroke="var(--chart-1)"
              strokeWidth={1.6}
              dot={false}
              isAnimationActive={false}
            />
            {level.value !== null ? (
              <ReferenceLine
                y={level.value}
                stroke="var(--accent)"
                strokeDasharray="4 3"
                label={{
                  value: t(`levels.${level.key}`),
                  fill: "var(--accent-text)",
                  fontSize: 10,
                  position: "insideTopRight",
                }}
              />
            ) : null}
            {floor !== null && level.key !== "lower_band" && level.key !== "low_20d" ? (
              <ReferenceLine y={floor} stroke="var(--border-strong)" strokeDasharray="2 3" />
            ) : null}
          </LineChart>
        </ResponsiveContainer>
      </div>
    </figure>
  );
}
