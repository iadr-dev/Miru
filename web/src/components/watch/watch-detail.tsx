"use client";

import {
  ArrowSquareOutIcon,
  BuildingsIcon,
  CheckCircleIcon,
  CircleIcon,
  CoinsIcon,
  CrownSimpleIcon,
  type Icon,
  MegaphoneIcon,
  UsersThreeIcon,
} from "@phosphor-icons/react";
import { useLocale, useTranslations } from "next-intl";
import type { ReactNode } from "react";
import {
  Area,
  CartesianGrid,
  ComposedChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  assertNeverStatus,
  guardReason,
  keyLevel,
  STRATEGY_COLOR,
  tint,
  type WatchStatus,
  watchStatus,
} from "@/components/watch/watch-format";
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
}: Readonly<{
  item: StrategyWatchItem | null;
  masked: boolean;
}>): ReactNode {
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
            <span
              className="mr-1.5 inline-flex items-center gap-1 rounded-[var(--radius-pill)] px-2 py-0.5 font-medium text-[var(--text)]"
              style={{ backgroundColor: tint(STRATEGY_COLOR[item.strategy], 18) }}
            >
              <span
                aria-hidden
                className="size-2 rounded-full"
                style={{ backgroundColor: STRATEGY_COLOR[item.strategy] }}
              />
              {t(`strategies.${item.strategy}.name`)}
            </span>
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
        <StatusLine status={watchStatus(item)} />
        {triggers.length === 0 ? (
          <p className="text-[12.5px] text-[var(--text-muted)]">{t("detail.notChecked")}</p>
        ) : (
          <ConditionList conditions={triggers} color={STRATEGY_COLOR[item.strategy]} />
        )}
      </section>

      {guards.length > 0 ? (
        <section aria-labelledby="watch-guards">
          <h4 id="watch-guards" className="mb-1.5 font-medium text-[12.5px] text-[var(--text)]">
            {t("detail.guards")}
          </h4>
          <ConditionList conditions={guards} color="var(--chart-6)" />
        </section>
      ) : null}

      <ContextFacts item={item} />
      {masked ? <p className="text-[11.5px] text-[var(--text-muted)]">{t("masked")}</p> : null}
    </div>
  );
}

function StatusLine({ status }: Readonly<{ status: WatchStatus }>): ReactNode {
  const t = useTranslations("watch");
  switch (status.kind) {
    case "notChecked":
      return null;
    case "triggersPending":
      return <StatusText>{t("detail.statusTriggersPending", status)}</StatusText>;
    case "awaitingConfirmation":
      return <StatusText>{t("detail.statusAwaiting")}</StatusText>;
    case "blockedByGuard":
      return (
        <StatusText>
          {t("detail.statusBlocked", { reason: guardReason(t, status.guard) })}
        </StatusText>
      );
    case "alerted":
      return <StatusText>{t("detail.statusAlerted", { time: status.time })}</StatusText>;
    default:
      return assertNeverStatus(status);
  }
}

function StatusText({ children }: Readonly<{ children: ReactNode }>): ReactNode {
  return <p className="mb-1.5 text-[12.5px] text-[var(--text-muted)]">{children}</p>;
}

function ConditionList({
  conditions,
  color,
}: Readonly<{
  conditions: readonly StrategyCondition[];
  color: string;
}>): ReactNode {
  const t = useTranslations("watch");
  const locale = useLocale();
  return (
    <ul className="space-y-1">
      {conditions.map((c) => (
        <li key={c.key} className="flex items-center justify-between gap-2 text-[12.5px]">
          <span className="flex items-center gap-1.5">
            {c.met ? (
              <CheckCircleIcon size={15} weight="fill" style={{ color }} aria-hidden />
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

type Fact = { text: string; icon: Icon; color: string };

type ContextTranslator = ReturnType<typeof useTranslations>;

function signed(value: number, decimals: number): string {
  return `${value >= 0 ? "+" : ""}${value.toFixed(decimals)}`;
}

function sectorFact(item: StrategyWatchItem, t: ContextTranslator): Fact | null {
  if (!item.industry || item.sector_strength_pct === null) return null;
  return {
    text: t("sector", { industry: item.industry, pct: Math.round(item.sector_strength_pct) }),
    icon: BuildingsIcon,
    color: "var(--chart-7)",
  };
}

/** 法人 consecutive buy/sell days — only a streak of two or more is worth a line. */
function streakFacts(item: StrategyWatchItem, t: ContextTranslator): Fact[] {
  const facts: Fact[] = [];
  for (const [key, streak] of [
    ["foreign", item.foreign_streak],
    ["trust", item.trust_streak],
  ] as const) {
    if (Math.abs(streak) >= 2) {
      facts.push({
        text: t(key, { days: Math.abs(streak), side: t(streak > 0 ? "buy" : "sell") }),
        icon: UsersThreeIcon,
        color: "var(--chart-1)",
      });
    }
  }
  return facts;
}

function largeHolderFact(item: StrategyWatchItem, t: ContextTranslator): Fact | null {
  if (item.large_holder_pct === null || item.large_holder_change_4w === null) return null;
  return {
    text: t("largeHolder", {
      pct: item.large_holder_pct.toFixed(1),
      change: signed(item.large_holder_change_4w, 2),
    }),
    icon: CrownSimpleIcon,
    color: "var(--chart-3)",
  };
}

function marginFact(item: StrategyWatchItem, t: ContextTranslator): Fact | null {
  if (item.margin_change_20d_pct === null) return null;
  const margin = item.margin_change_20d_pct;
  const text = t("margin", { pct: signed(margin, 1) });
  const surge = margin > 20;
  return {
    text: surge ? `${text}（${t("marginSurge")}）` : text,
    icon: CoinsIcon,
    color: surge ? "var(--chart-8)" : "var(--chart-6)",
  };
}

function headlineFact(item: StrategyWatchItem, t: ContextTranslator): Fact | null {
  if (!item.headline) return null;
  return {
    text: t("headline", { text: item.headline }),
    icon: MegaphoneIcon,
    color: "var(--chart-2)",
  };
}

/** A fact's icon and colour say what kind of signal it is at a glance: sector (sky), 法人 (blue),
 * 大戶 (gold), 融資 (orange when it surges, else muted), 重訊 (purple). */
function ContextFacts({ item }: Readonly<{ item: StrategyWatchItem }>): ReactNode {
  const t = useTranslations("watch.context");
  const heading = useTranslations("watch.detail")("context");
  const facts = [
    sectorFact(item, t),
    ...streakFacts(item, t),
    largeHolderFact(item, t),
    marginFact(item, t),
    headlineFact(item, t),
  ].filter((fact): fact is Fact => fact !== null);
  if (facts.length === 0) return null;
  return (
    <section aria-labelledby="watch-context">
      <h4 id="watch-context" className="mb-1.5 font-medium text-[12.5px] text-[var(--text)]">
        {heading}
      </h4>
      <ul className="space-y-1.5 text-[12.5px] text-[var(--text-muted)]">
        {facts.map(({ text, icon: FactIcon, color }) => (
          <li key={text} className="flex items-start gap-2">
            <span
              aria-hidden
              className="mt-px inline-flex size-5 shrink-0 items-center justify-center rounded-full"
              style={{ backgroundColor: tint(color, 18), color }}
            >
              <FactIcon size={12} weight="bold" />
            </span>
            <span>{text}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}

function LevelChart({ item }: Readonly<{ item: StrategyWatchItem }>): ReactNode {
  const t = useTranslations("watch");
  const locale = useLocale();
  const history = usePriceHistory(item.symbol, "TW", CHART_DAYS);
  const level = keyLevel(item);
  const color = STRATEGY_COLOR[item.strategy];
  const floor = item.range_low_20d ?? item.flag_low;
  const rows = (history.data?.rows ?? []).map((r) => ({
    date: r.trade_date.slice(5),
    close: r.close,
  }));
  // The axis always takes in the trigger level and the floor: a breakout level sits above every
  // close by definition, and an auto-scaled axis would leave exactly that line off the chart.
  const span = [...rows.map((r) => r.close), level.value, floor].filter(
    (v): v is number => v !== null,
  );
  const pad = (Math.max(...span) - Math.min(...span)) * 0.05 || 1;
  const domain: [number, number] = [
    Math.floor(Math.min(...span) - pad),
    Math.ceil(Math.max(...span) + pad),
  ];
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
          <ComposedChart data={rows} margin={{ top: 6, right: 8, bottom: 0, left: 0 }}>
            <defs>
              <linearGradient id={`watch-fill-${item.strategy}`} x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={color} stopOpacity={0.32} />
                <stop offset="100%" stopColor={color} stopOpacity={0.02} />
              </linearGradient>
            </defs>
            <CartesianGrid vertical={false} stroke="var(--grid)" />
            <XAxis
              dataKey="date"
              tick={{ fill: "var(--text-muted)", fontSize: 10 }}
              stroke="var(--border)"
              minTickGap={24}
            />
            <YAxis
              domain={domain}
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
            <Area
              type="monotone"
              dataKey="close"
              stroke={color}
              strokeWidth={1.8}
              fill={`url(#watch-fill-${item.strategy})`}
              baseValue="dataMin"
              dot={false}
              isAnimationActive={false}
            />
            {level.value !== null ? (
              <ReferenceLine
                y={level.value}
                stroke={color}
                strokeWidth={1.6}
                strokeDasharray="5 3"
                label={{
                  value: t(`levels.${level.key}`),
                  fill: color,
                  fontSize: 10,
                  position: "insideTopRight",
                }}
              />
            ) : null}
            {floor !== null && level.key !== "lower_band" && level.key !== "low_20d" ? (
              <ReferenceLine y={floor} stroke="var(--border-strong)" strokeDasharray="2 3" />
            ) : null}
          </ComposedChart>
        </ResponsiveContainer>
      </div>
    </figure>
  );
}
