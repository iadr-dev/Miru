"use client";

import { LockIcon } from "@phosphor-icons/react";
import { useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { cn } from "@/lib/cn";
import { formatCompactNumber, formatNumber } from "@/lib/format-number";
import { directionColor, directionGlyph } from "@/lib/signed";
import type { DispositionBoardCardRow } from "@/types/market";

const FLAG_ORDER = ["資", "券", "沖", "警", "處", "注"] as const;
type Flag = (typeof FLAG_ORDER)[number];

// Active-flag tint per type — matches the reference 處置王 app's own per-flag color coding
// rather than one flat `--warn` tint for every active flag. `--chart-1..4` are categorical
// (never signed), so they're the right token family for a flag-type distinction.
const FLAG_COLOR: Record<Flag, string> = {
  資: "var(--chart-1)",
  券: "var(--chart-2)",
  沖: "var(--chart-3)",
  警: "var(--warn)",
  處: "var(--down)",
  注: "var(--chart-4)",
};

// Mirrors `disposition_board_tools.py`'s `_ATTENTION_FLAGS_TO_DISPOSITION`/
// `_ROLLING_WINDOW_TRADING_DAYS` — kept in sync here only to render the localized caveat
// sentence client-side (the backend's own English caveat string is still carried on
// `projection_caveat` but not rendered directly, since this app's caveats are UI copy that
// must localize like everything else, not raw passthrough source text).
const ATTENTION_FLAGS_TO_DISPOSITION = 3;
const ROLLING_WINDOW_TRADING_DAYS = 10;

/**
 * One 處置王-style card: header (name/code + 上市/上櫃), big price + ▲▼ change, MA5/10/20 (derived
 * client-side from `sparkline`, the same `useSymbolQuotes` batch every leaderboard/momentum card
 * already fetches), right-aligned stats (volume/turnover/institutional net/margin ratio), a
 * `--warn`-tinted 「最快N日後進入處置」 projection line, a 分盤 interval badge, per-type-tinted flag
 * chips (資/券/沖/警/處/注), and a footer with 處置期間 dates + localized caveats.
 */
export function DispositionStockCard({
  card,
  sparkline,
}: {
  readonly card: DispositionBoardCardRow;
  readonly sparkline: readonly number[];
}): ReactNode {
  const t = useTranslations("market.cards.dispositionBoard");
  const changeUp = card.change_pct !== null && card.change_pct >= 0;
  const changeColor = card.change_pct !== null ? directionColor(card.change_pct ?? 0) : null;
  const activeFlags = new Set(card.flags);
  const ma5 = movingAverage(sparkline, 5);
  const ma10 = movingAverage(sparkline, 10);
  const ma20 = movingAverage(sparkline, 20);

  if (card.locked) {
    return (
      <div className="surface-card flex flex-col p-4">
        <div className="flex items-center justify-between gap-2">
          <span className="inline-flex items-center gap-1.5 text-[13px] text-[var(--text-muted)]">
            <LockIcon size={13} weight="regular" aria-hidden />
            <span aria-hidden>••••</span>
            <span className="font-num text-[11px] tabular-nums">#{card.rank}</span>
          </span>
          <span
            className={cn(
              "shrink-0 rounded-[var(--radius-pill)] px-2 py-0.5 font-medium text-[11px]",
              card.bucket === "at_risk" && "bg-[var(--surface-2)] text-[var(--warn)]",
              card.bucket === "in_disposition" && "bg-[var(--surface-2)] text-[var(--down)]",
              card.bucket === "exiting_soon" && "bg-[var(--surface-2)] text-[var(--up)]",
            )}
          >
            {t(`bucket.${card.bucket}`)}
          </span>
        </div>
        <dl className="mt-3 grid grid-cols-2 gap-y-1.5 text-[12px]">
          <StatRow label={t("volume")} value="••••" />
          <StatRow label={t("turnover")} value="••••" />
          <StatRow label={t("institutionalNet")} value="••••" />
          <StatRow label={t("marginRatio")} value="••••" />
          <StatRow label={t("dayTradingRatio")} value="••••" />
        </dl>
        <p className="mt-1.5 text-[10.5px] text-[var(--text-muted)] leading-relaxed">
          {t("projectionCaveat", {
            window: ROLLING_WINDOW_TRADING_DAYS,
            flags: ATTENTION_FLAGS_TO_DISPOSITION,
          })}
        </p>
      </div>
    );
  }

  return (
    <div className="surface-card flex flex-col p-4">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="truncate font-medium text-[14px] text-[var(--text)]">
            {card.company_name || card.symbol}
            <span className="ml-1.5 text-[11.5px] text-[var(--text-muted)]">{card.symbol}</span>
          </p>
          <div className="mt-0.5 flex flex-wrap items-center gap-1">
            <span className="inline-block rounded-[var(--radius-pill)] bg-[var(--surface-2)] px-1.5 py-0.5 text-[10.5px] text-[var(--text-muted)]">
              {card.security_type === "twse" ? "上市" : "上櫃"}
            </span>
            {card.matching_interval_minutes !== null ? (
              <span className="inline-block rounded-[var(--radius-pill)] bg-[var(--chart-1)]/15 px-1.5 py-0.5 text-[10.5px] text-[var(--chart-1)]">
                {t("matchingInterval", { minutes: card.matching_interval_minutes })}
              </span>
            ) : null}
          </div>
        </div>
        <span
          className={cn(
            "shrink-0 rounded-[var(--radius-pill)] px-2 py-0.5 font-medium text-[11px]",
            card.bucket === "at_risk" && "bg-[var(--surface-2)] text-[var(--warn)]",
            card.bucket === "in_disposition" && "bg-[var(--surface-2)] text-[var(--down)]",
            card.bucket === "exiting_soon" && "bg-[var(--surface-2)] text-[var(--up)]",
          )}
        >
          {t(`bucket.${card.bucket}`)}
        </span>
      </div>

      <div className="mt-2 flex items-baseline gap-2">
        <span className="font-num text-[20px] text-[var(--text)] tabular-nums">
          {card.close !== null ? formatNumber(card.close, "zh-TW", { decimals: 1 }) : "—"}
        </span>
        {card.change_pct !== null ? (
          <span
            role="group"
            aria-label={`${changeUp ? "上漲" : "下跌"} ${Math.abs(card.change_pct).toFixed(2)}%`}
            className="font-num text-[13px] tabular-nums"
            style={{ color: changeColor ?? undefined }}
          >
            {directionGlyph(card.change_pct ?? 0)}
            {formatNumber(Math.abs(card.change_pct), "zh-TW", { decimals: 2 })}%
          </span>
        ) : null}
      </div>

      {ma5 !== null || ma10 !== null || ma20 !== null ? (
        <div className="mt-1.5 flex gap-3 text-[11px] text-[var(--text-muted)]">
          <span>
            MA5 <span className="font-num tabular-nums">{ma5?.toFixed(1) ?? "—"}</span>
          </span>
          <span>
            MA10 <span className="font-num tabular-nums">{ma10?.toFixed(1) ?? "—"}</span>
          </span>
          <span>
            MA20 <span className="font-num tabular-nums">{ma20?.toFixed(1) ?? "—"}</span>
          </span>
        </div>
      ) : null}

      <dl className="mt-2.5 grid grid-cols-2 gap-y-1 text-[12px]">
        <StatRow
          label={t("volume")}
          value={
            card.volume !== null
              ? `${formatNumber(card.volume / 1000, "zh-TW", { decimals: 0 })}${t("lots")}`
              : "—"
          }
        />
        <StatRow
          label={t("turnover")}
          value={card.turnover !== null ? `${formatCompactNumber(card.turnover, "zh-TW")}元` : "—"}
        />
        <StatRow
          label={t("institutionalNet")}
          value={
            card.institutional_net !== null
              ? formatCompactNumber(card.institutional_net, "zh-TW")
              : "—"
          }
          valueColor={
            card.institutional_net !== null
              ? card.institutional_net >= 0
                ? "var(--up)"
                : "var(--down)"
              : undefined
          }
        />
        <StatRow
          label={t("marginRatio")}
          value={
            card.margin_ratio !== null
              ? `${formatNumber(card.margin_ratio, "zh-TW", { decimals: 1 })}%`
              : "—"
          }
        />
        <StatRow
          label={t("dayTradingRatio")}
          value={
            card.day_trading_ratio !== null
              ? `${formatNumber(card.day_trading_ratio, "zh-TW", { decimals: 1 })}%`
              : "—"
          }
        />
      </dl>

      {card.attention_flags_remaining !== null ? (
        <p className="mt-2.5 rounded-[var(--radius-input)] bg-[color-mix(in_srgb,var(--warn)_14%,transparent)] px-2.5 py-1.5 text-[12px] text-[var(--warn)]">
          {card.estimated_days_until_disposition !== null
            ? t("estimatedDaysUntilDisposition", {
                days: card.estimated_days_until_disposition,
                count: card.attention_flags_remaining,
              })
            : t("attentionFlagsRemaining", { count: card.attention_flags_remaining })}
        </p>
      ) : null}
      {card.days_until_exiting !== null ? (
        <p className="mt-2.5 rounded-[var(--radius-input)] bg-[color-mix(in_srgb,var(--up)_14%,transparent)] px-2.5 py-1.5 text-[12px] text-[var(--up)]">
          {card.days_until_exiting === 0
            ? t("exitingToday")
            : t("daysUntilExiting", { days: card.days_until_exiting })}
        </p>
      ) : null}

      <div className="mt-2.5 flex flex-wrap gap-1">
        {FLAG_ORDER.map((flag) => {
          const active = activeFlags.has(flag);
          const color = FLAG_COLOR[flag];
          return (
            <span
              key={flag}
              className={cn(
                "rounded-[var(--radius-pill)] px-1.5 py-0.5 text-[10.5px]",
                !active && "bg-[var(--surface-2)] text-[var(--text-muted)]/50",
              )}
              style={
                active
                  ? { backgroundColor: `color-mix(in srgb, ${color} 20%, transparent)`, color }
                  : undefined
              }
            >
              {t(`flag.${flag}`)}
            </span>
          );
        })}
      </div>

      {card.disposition_period_start && card.disposition_period_end ? (
        <p className="mt-2.5 text-[11px] text-[var(--text-muted)]">
          {t("dispositionPeriod")}: {card.disposition_period_start} ~ {card.disposition_period_end}
        </p>
      ) : null}

      <p className="mt-1.5 text-[10.5px] text-[var(--text-muted)] leading-relaxed">
        {t("projectionCaveat", {
          window: ROLLING_WINDOW_TRADING_DAYS,
          flags: ATTENTION_FLAGS_TO_DISPOSITION,
        })}
      </p>
      {card.margin_health_status && card.margin_health_status !== "ok" && card.margin_caveat ? (
        <p className="mt-1 text-[10.5px] text-[var(--warn)] leading-relaxed">
          {t("marginDegradedCaveat")}
        </p>
      ) : null}
    </div>
  );
}

function movingAverage(sparkline: readonly number[], window: number): number | null {
  if (sparkline.length < window) return null;
  const recent = sparkline.slice(-window);
  return recent.reduce((sum, value) => sum + value, 0) / window;
}

function StatRow({
  label,
  value,
  valueColor,
}: {
  readonly label: string;
  readonly value: string;
  readonly valueColor?: string;
}): ReactNode {
  return (
    <>
      <dt className="text-[var(--text-muted)]">{label}</dt>
      <dd
        className="text-right font-num tabular-nums"
        style={{ color: valueColor ?? "var(--text)" }}
      >
        {value}
      </dd>
    </>
  );
}
