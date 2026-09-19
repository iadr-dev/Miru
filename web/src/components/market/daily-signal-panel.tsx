"use client";

import { LockIcon } from "@phosphor-icons/react";
import { useQuery } from "@tanstack/react-query";
import { useLocale, useTranslations } from "next-intl";
import { type ReactNode, useState } from "react";
import { formatCompactNumber } from "@/app/[locale]/(app)/chat/cards/card-format";
import { QueryState } from "@/app/[locale]/(app)/market/market-panel";
import { CardSkeletonBody } from "@/components/market/card-skeleton";
import {
  DAILY_SIGNAL_COLLAPSED_ROW_COUNT,
  DailySignalTable,
  filterDailySignalRows,
} from "@/components/market/daily-signal-table";
import { ExpandModal } from "@/components/ui/expand-modal";
import { getDailySignal } from "@/lib/api/market";
import { cn } from "@/lib/cn";
import type { DailySignalHighlight, HoldingStatus } from "@/types/market";
import type { Market } from "@/types/settings";

const STATUS_FILTERS: readonly HoldingStatus[] = ["新增", "刪除", "加碼", "減碼"];

/**
 * Tab-panel wrapper for the ETF tab's 今日訊號 sub-tab — owns the single `/market/daily-signal`
 * query, the two 焦點股 highlight cards (top inflow/outflow for the day), and the 新增/刪除/加碼/
 * 減碼 filter-chip row above `DailySignalTable`. Sponsor-tier masking follows the same
 * `SponsorTeaser` convention `EtfMoneyFlowPanel` already uses.
 */
export function DailySignalPanel({ market }: { readonly market: Market }): ReactNode {
  const t = useTranslations("market.cards.dailySignal");
  const [statusFilter, setStatusFilter] = useState<HoldingStatus | null>(null);
  const [expanded, setExpanded] = useState(false);

  const query = useQuery({
    queryKey: ["daily_signal", market],
    queryFn: () => getDailySignal(market),
  });

  return (
    <section className="flex w-full flex-col rounded-[var(--radius-card)] border border-[var(--border)] bg-[var(--surface)] p-5">
      <QueryState query={query} skeleton={<CardSkeletonBody height={600} />}>
        {(data) => {
          if (!data.data_available) {
            return (
              <>
                <h2 className="mb-1 font-semibold text-[15px] text-[var(--text)]">{t("title")}</h2>
                <p className="mt-3 text-[13px] text-[var(--text-muted)]">
                  {data.unavailable_reason ?? t("unavailable")}
                </p>
              </>
            );
          }
          const statusCounts: Record<HoldingStatus, number> = {
            新增: data.new_count,
            刪除: data.removed_count,
            加碼: data.increased_count,
            減碼: data.decreased_count,
            no_prior_data: 0,
          };
          // Filtered here (not left to `DailySignalTable` alone) so the inline preview can
          // slice AFTER filtering — slicing `data.rows` first, then letting the table filter
          // internally, would show fewer than `DAILY_SIGNAL_COLLAPSED_ROW_COUNT` rows
          // whenever a status chip is active and its matches aren't all within the first N
          // unfiltered rows.
          const filteredRows = filterDailySignalRows(data.rows, statusFilter);
          return (
            <>
              <h2 className="mb-1 font-semibold text-[15px] text-[var(--text)]">
                {t("title")}
                {data.report_date ? (
                  <span className="ml-2 font-normal text-[12px] text-[var(--text-muted)]">
                    {t("reportDate", { date: data.report_date })} · {t("nextUpdate")}
                  </span>
                ) : null}
              </h2>

              {data.top_inflow || data.top_outflow ? (
                <div className="mt-3 grid grid-cols-1 gap-2.5 sm:grid-cols-2">
                  {data.top_inflow ? (
                    <HighlightCard kind="inflow" highlight={data.top_inflow} />
                  ) : null}
                  {data.top_outflow ? (
                    <HighlightCard kind="outflow" highlight={data.top_outflow} />
                  ) : null}
                </div>
              ) : null}

              <p className="mt-3 text-[12px] text-[var(--text-muted)]">
                {t("totalCount", { count: data.total_stock_count })}
              </p>
              <div className="mt-2 flex flex-wrap gap-1.5">
                <StatusChip
                  active={statusFilter === null}
                  label={t("all")}
                  count={data.total_stock_count}
                  onClick={() => setStatusFilter(null)}
                />
                {STATUS_FILTERS.map((status) => (
                  <StatusChip
                    key={status}
                    active={statusFilter === status}
                    label={status}
                    count={statusCounts[status]}
                    onClick={() => setStatusFilter(status)}
                  />
                ))}
              </div>

              <div className="mt-3">
                <DailySignalTable
                  rows={filteredRows.slice(0, DAILY_SIGNAL_COLLAPSED_ROW_COUNT)}
                  statusFilter={null}
                  market={market}
                />
              </div>

              {filteredRows.length > DAILY_SIGNAL_COLLAPSED_ROW_COUNT ? (
                <button
                  type="button"
                  onClick={() => setExpanded(true)}
                  className={cn(
                    "mt-2.5 flex min-h-11 w-full items-center justify-center gap-1.5 rounded-[var(--radius-input)]",
                    "bg-[var(--surface-2)] text-[12.5px] text-[var(--text-muted)] hover:bg-[var(--surface-3)] hover:text-[var(--text)]",
                  )}
                >
                  <span>{t("expandTo", { count: filteredRows.length })}</span>
                </button>
              ) : null}

              <ExpandModal
                open={expanded}
                title={t("title")}
                closeLabel={t("collapse")}
                onClose={() => setExpanded(false)}
              >
                <DailySignalTable rows={filteredRows} statusFilter={null} market={market} />
              </ExpandModal>
            </>
          );
        }}
      </QueryState>
    </section>
  );
}

function StatusChip({
  active,
  label,
  count,
  onClick,
}: {
  readonly active: boolean;
  readonly label: string;
  readonly count: number;
  readonly onClick: () => void;
}): ReactNode {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={cn(
        "rounded-[var(--radius-pill)] px-3 py-1 text-[12px] transition-colors",
        active
          ? "bg-[var(--accent)] font-semibold text-[color:#06231F]"
          : "bg-[var(--surface-2)] text-[var(--text-muted)] hover:text-[var(--text)]",
      )}
    >
      {label} {count}
    </button>
  );
}

/** 焦點股 highlight card — inflow uses `--up`, outflow uses `--down` (this IS a real signed
 * money-flow figure, unlike the status pill's categorical tokens). governance.md §4's row-preview
 * exception locks this entirely for an unentitled caller (`highlight.locked`) rather than
 * counting it against `free_row_count` — it concentrates the whole card's single most valuable
 * signal into one figure. */
function HighlightCard({
  kind,
  highlight,
}: {
  readonly kind: "inflow" | "outflow";
  readonly highlight: DailySignalHighlight;
}): ReactNode {
  const t = useTranslations("market.cards.dailySignal");
  const locale = useLocale();
  const color = kind === "inflow" ? "var(--up)" : "var(--down)";
  const value = highlight.held_market_value_delta;

  if (highlight.locked) {
    return (
      <div
        className="rounded-[var(--radius-input)] border-l-4 bg-[var(--surface-2)] p-3"
        style={{ borderLeftColor: color }}
      >
        <p className="text-[11px] text-[var(--text-muted)]">
          {kind === "inflow" ? t("topInflow") : t("topOutflow")}
        </p>
        <p className="mt-0.5 flex items-center gap-1.5 font-medium text-[14px] text-[var(--text-muted)]">
          <LockIcon size={12} weight="regular" aria-hidden />
          <span aria-hidden>••••</span>
        </p>
        <div className="mt-1.5 flex items-center justify-between text-[12px]">
          <span className="text-[var(--text-muted)]">{t("moneyDirection")}</span>
          <span aria-hidden className="font-num text-[var(--text-muted)] tabular-nums">
            ••••
          </span>
        </div>
        <div className="mt-1 flex items-center justify-between text-[12px]">
          <span className="text-[var(--text-muted)]">{t("consensus")}</span>
          <span aria-hidden className="font-num text-[var(--text-muted)] tabular-nums">
            ••••
          </span>
        </div>
      </div>
    );
  }

  return (
    <div
      className="rounded-[var(--radius-input)] border-l-4 bg-[var(--surface-2)] p-3"
      style={{ borderLeftColor: color }}
    >
      <p className="text-[11px] text-[var(--text-muted)]">
        {kind === "inflow" ? t("topInflow") : t("topOutflow")}
      </p>
      <p className="mt-0.5 truncate font-medium text-[14px] text-[var(--text)]">
        {highlight.component_stock_name}
        <span className="ml-1.5 text-[11px] text-[var(--text-muted)]">
          {highlight.component_stock_id}
        </span>
      </p>
      <div className="mt-1.5 flex items-center justify-between text-[12px]">
        <span className="text-[var(--text-muted)]">{t("moneyDirection")}</span>
        <span className="font-num tabular-nums" style={{ color }}>
          {value !== null ? formatCompactNumber(value, locale) : "—"}
        </span>
      </div>
      <div className="mt-1 flex items-center justify-between text-[12px]">
        <span className="text-[var(--text-muted)]">{t("consensus")}</span>
        <span className="font-num tabular-nums">
          {highlight.buying_etf_count}:{highlight.selling_etf_count}
        </span>
      </div>
    </div>
  );
}
