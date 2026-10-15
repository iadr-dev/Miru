"use client";

import { LockIcon } from "@phosphor-icons/react";
import { useQuery } from "@tanstack/react-query";
import { useLocale, useTranslations } from "next-intl";
import { type ReactNode, useMemo, useState } from "react";
import { CardSkeletonBody } from "@/components/market/card-skeleton";
import { SponsorTeaser } from "@/components/market/sponsor-teaser";
import { ExpandModal } from "@/components/ui/expand-modal";
import { QueryFailure } from "@/components/ui/query-state";
import { SignedChange } from "@/components/ui/signed-change";
import { Link } from "@/i18n/navigation";
import { queryKeys } from "@/lib/api/query-keys";
import { getEtfsHoldingStock } from "@/lib/api/stock-detail";
import { cn } from "@/lib/cn";
import { formatNumber } from "@/lib/format-number";
import { isMaskedTeaser } from "@/types/market/shared";
import type { Market } from "@/types/settings";
import type {
  EtfHoldingRankRow,
  EtfPositionEvent,
  EtfPositionEventCounts,
  EtfWeightRankRow,
} from "@/types/stock/ownership";

// FinMind's `TaiwanStockActiveETFHolding.shares` is raw 股 (individual shares), not 張 (board
// lots of 1,000 shares) — see `GetEtfsHoldingStockTool`'s own docstring in `tools/market/`.
// The reference app's 持有張數/建清倉 views both report 張, so every raw share count rendered here
// is converted at the display boundary; `stock_capital_pct`/`weight_pct` stay untouched (already
// ratios, unit-independent).
const SHARES_PER_LOT = 1000;
function toLots(shares: number): number {
  return Math.round(shares / SHARES_PER_LOT);
}

const COLLAPSED_ROW_COUNT = 10;

/**
 * 追蹤 tab — ETF持有 card (see this tab's own scoping note below for why 基金/mutual funds are
 * out of scope entirely, not a coming-soon placeholder).
 *
 * The same underlying capability (`get_etfs_holding_stock`) could back a smaller in-page card
 * (比重/張數+增減, no sub-tabs) inside the 基本資料 tab. This component ships the richer
 * 3-sub-view reference-app layout (持有張數排行/權重排行/建清倉) as its own tab instead,
 * per an explicit later request.
 *
 * Coverage is **active-management ETFs only** (FinMind's `TaiwanStockActiveETFHolding` dataset);
 * passive ETFs (0050/00878/006208/etc.) have no ingested constituent-holdings data source. 基金
 * (mutual funds) have zero capability of any kind (SITCA only discloses fees, not holdings) and
 * are not represented in this UI at all — no toggle, no coming-soon stub — since there's nothing
 * to point a toggle at.
 */
export function TrackerPanel({
  symbol,
  market,
}: {
  readonly symbol: string;
  readonly market: Market;
}): ReactNode {
  const t = useTranslations("stock.tracker");
  const [subTab, setSubTab] = useState<"holdingRank" | "weightRank" | "positionEvents">(
    "holdingRank",
  );
  const query = useQuery({
    queryKey: queryKeys.symbolRead("etfs_holding_stock", symbol, market),
    queryFn: () => getEtfsHoldingStock(symbol, market),
  });

  if (query.isLoading) {
    return (
      <section className="surface-card p-4 sm:p-5">
        <CardSkeletonBody height={320} />
      </section>
    );
  }
  if (query.isError) {
    return <QueryFailure query={query} />;
  }
  const data = query.data;
  if (!data) return null;

  if (isMaskedTeaser(data)) {
    return (
      <section className="surface-card p-4 sm:p-5">
        <h2 className="mb-3 font-semibold text-[15px] text-[var(--text)]">{t("title")}</h2>
        <SponsorTeaser
          headline={t("sponsorLocked")}
          preview={data.preview}
          requiredTier={data.required_tier}
        >
          <div className="h-64 rounded-[var(--radius-input)] bg-[var(--surface-2)]" />
        </SponsorTeaser>
      </section>
    );
  }

  return (
    <section className="surface-card p-4 sm:p-5">
      <div className="mb-1 flex flex-wrap items-center justify-between gap-2">
        <h2 className="font-semibold text-[15px] text-[var(--text)]">
          {t("title")}
          {data.data_available ? (
            <span className="ml-2 font-normal text-[12px] text-[var(--text-muted)]">
              {t("etfCount", { count: data.etf_count })}
            </span>
          ) : null}
        </h2>
        {data.as_of ? (
          <span className="text-[11px] text-[var(--text-muted)]">
            {t("asOfLabel", { date: data.as_of })}
          </span>
        ) : null}
      </div>

      <div className="mb-3 flex gap-1 rounded-[var(--radius-pill)] bg-[var(--surface-2)] p-0.5">
        {(["holdingRank", "weightRank", "positionEvents"] as const).map((key) => (
          <button
            key={key}
            type="button"
            onClick={() => setSubTab(key)}
            aria-pressed={subTab === key}
            className={cn(
              "rounded-[var(--radius-pill)] px-3 py-1.5 text-[12.5px] transition-colors",
              subTab === key
                ? "bg-[var(--accent-weak)] text-[var(--accent-text)]"
                : "text-[var(--text-muted)] hover:text-[var(--text)]",
            )}
          >
            {t(`subTab.${key}`)}
          </button>
        ))}
      </div>

      {!data.data_available ? (
        <p className="text-[13px] text-[var(--text-muted)]">
          {data.unavailable_reason ?? t("empty")}
        </p>
      ) : (
        <>
          {data.etf_count > data.free_row_count ? <RowPreviewCta t={t} /> : null}
          {subTab === "holdingRank" ? (
            <ExpandableSection
              t={t}
              rowCount={data.holding_rank.length}
              renderRows={(rows) => <HoldingRankTable rows={rows} t={t} />}
              rows={data.holding_rank}
            />
          ) : null}
          {subTab === "weightRank" ? (
            <ExpandableSection
              t={t}
              rowCount={data.weight_rank.length}
              renderRows={(rows) => <WeightRankView rows={rows} t={t} />}
              rows={data.weight_rank}
            />
          ) : null}
          {subTab === "positionEvents" ? (
            <ExpandableSection
              t={t}
              rowCount={data.position_events.length}
              renderRows={(rows) => (
                <PositionEventsTimeline
                  events={rows}
                  allEvents={data.position_events}
                  counts={data.position_event_counts}
                  t={t}
                />
              )}
              rows={data.position_events}
            />
          ) : null}
        </>
      )}
    </section>
  );
}

/** Shared collapsed-preview + `ExpandModal` "view all" affordance for the 3 sub-views' row
 * lists — mirrors `DailySignalPanel`'s own expand-to-modal convention (same
 * `COLLAPSED_ROW_COUNT`/`ExpandModal` pattern), generalized here so all 3 sub-views share one
 * button+modal implementation instead of tripling it. */
function ExpandableSection<Row>({
  rows,
  rowCount,
  renderRows,
  t,
}: {
  readonly rows: readonly Row[];
  readonly rowCount: number;
  readonly renderRows: (rows: readonly Row[]) => ReactNode;
  readonly t: ReturnType<typeof useTranslations>;
}): ReactNode {
  const [expanded, setExpanded] = useState(false);
  return (
    <>
      {renderRows(rows.slice(0, COLLAPSED_ROW_COUNT))}
      {rowCount > COLLAPSED_ROW_COUNT ? (
        <button
          type="button"
          onClick={() => setExpanded(true)}
          className={cn(
            "mt-2.5 flex min-h-11 w-full items-center justify-center gap-1.5 rounded-[var(--radius-input)]",
            "bg-[var(--surface-2)] text-[12.5px] text-[var(--text-muted)] hover:bg-[var(--surface-3)] hover:text-[var(--text)]",
          )}
        >
          {t("expandTo", { count: rowCount })}
        </button>
      ) : null}
      <ExpandModal
        open={expanded}
        title={t("title")}
        closeLabel={t("collapse")}
        onClose={() => setExpanded(false)}
      >
        {renderRows(rows)}
      </ExpandModal>
    </>
  );
}

function DeltaCell({
  value,
  locale,
}: {
  readonly value: number | null | undefined;
  readonly locale: string;
}): ReactNode {
  return <SignedChange value={value} locale={locale} />;
}

/** One upsell CTA above the row list when any row this card returned is locked — the row-level
 * counterpart to `SponsorTeaser`'s own CTA, shown once per card rather than once per locked row
 * (mirrors `LockedCategoryChip`'s single-CTA convention in `chip-analysis-panel.tsx`). */
function RowPreviewCta({ t }: { readonly t: ReturnType<typeof useTranslations> }): ReactNode {
  const teaserT = useTranslations("market.teaser");
  return (
    <div className="mb-3 flex items-center gap-3 rounded-[var(--radius-input)] border border-[var(--border)] bg-[var(--surface-2)] p-2.5">
      <span
        aria-hidden
        className="grid size-8 shrink-0 place-items-center rounded-[var(--radius-pill)] bg-[var(--surface-3)]"
      >
        <LockIcon size={14} weight="regular" className="text-[var(--text-muted)]" />
      </span>
      <p className="min-w-0 flex-1 text-[12px] text-[var(--text-muted)]">{t("sponsorLocked")}</p>
      <Link
        href="/settings/billing"
        className="shrink-0 whitespace-nowrap text-[12px] text-[var(--accent-text)] hover:underline"
      >
        {teaserT("cta", { tier: teaserT("tier.premium") })}
      </Link>
    </div>
  );
}

/** governance.md §4 top-N-preview exception's row-level lock: the row's own value is never
 * sent for a `locked` row (nulled server-side in `tools/market/`), so this renders a fixed
 * placeholder + lock icon rather than reading any field — there is no real number to blur here,
 * unlike `SponsorTeaser`'s whole-card CSS blur over real content. */
function LockedValueCell(): ReactNode {
  return (
    <span className="inline-flex items-center justify-end gap-1 text-[var(--text-muted)]">
      <LockIcon size={11} weight="regular" aria-hidden />
      <span aria-hidden className="tabular-nums">
        ••••
      </span>
    </span>
  );
}

/** The identity cell for a locked row — `etf_symbol`/`etf_name` are nulled server-side too
 * (not just the value), so this shows `rank` (the row's real, non-identifying 1-based position)
 * behind the same lock icon instead of reading either field. */
function LockedIdentityCell({ rank }: { readonly rank: number }): ReactNode {
  return (
    <span className="inline-flex items-center gap-1.5 text-[var(--text-muted)]">
      <LockIcon size={11} weight="regular" aria-hidden />
      <span aria-hidden>••••</span>
      <span className="font-num text-[11px] tabular-nums">#{rank}</span>
    </span>
  );
}

/** The masked-row's 4-column `<td>` set (identity + 3 locked values) — shared between
 * `HoldingRankTable`/`WeightRankView` since both tables have the same "identity + 3 numeric
 * columns" shape, just with different real-data columns when unlocked. */
function LockedRowCells({ rank }: { readonly rank: number }): ReactNode {
  return (
    <>
      <td className="px-2 py-1.5 text-[var(--text)]">
        <LockedIdentityCell rank={rank} />
      </td>
      <td className="px-2 py-1.5 text-right font-num tabular-nums">
        <LockedValueCell />
      </td>
      <td className="px-2 py-1.5 text-right font-num tabular-nums">
        <LockedValueCell />
      </td>
      <td className="px-2 py-1.5 text-right font-num tabular-nums">
        <LockedValueCell />
      </td>
    </>
  );
}

function HoldingRankTable({
  rows,
  t,
}: {
  readonly rows: readonly EtfHoldingRankRow[];
  readonly t: ReturnType<typeof useTranslations>;
}): ReactNode {
  const locale = useLocale();
  if (rows.length === 0)
    return <p className="text-[13px] text-[var(--text-muted)]">{t("empty")}</p>;
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-[13px]">
        <thead>
          <tr className="border-[var(--border)] border-b text-[11px] text-[var(--text-muted)]">
            <th className="px-2 py-1.5 text-left font-medium">{t("holdingRank.etf")}</th>
            <th className="px-2 py-1.5 text-right font-medium">
              {t("holdingRank.shares")} ({t("lotUnit")})
            </th>
            <th className="px-2 py-1.5 text-right font-medium">{t("holdingRank.sharesDelta")}</th>
            <th className="px-2 py-1.5 text-right font-medium">
              {t("holdingRank.stockCapitalPct")}
            </th>
          </tr>
        </thead>
        <tbody className="divide-y divide-[var(--border)]">
          {rows.map((row) => (
            <tr key={row.locked ? `locked-${row.rank}` : row.etf_symbol}>
              {row.locked || row.etf_symbol === null || row.shares === null ? (
                <LockedRowCells rank={row.rank} />
              ) : (
                <>
                  <td className="px-2 py-1.5 text-[var(--text)]">
                    <span className="font-num tabular-nums">{row.etf_symbol}</span>{" "}
                    <span className="text-[12px] text-[var(--text-muted)]">{row.etf_name}</span>
                  </td>
                  <td className="px-2 py-1.5 text-right font-num text-[var(--text)] tabular-nums">
                    {formatNumber(toLots(row.shares), locale)}
                  </td>
                  <td className="px-2 py-1.5 text-right font-num tabular-nums">
                    <DeltaCell
                      value={
                        row.shares_delta === null || row.shares_delta === undefined
                          ? null
                          : toLots(row.shares_delta)
                      }
                      locale={locale}
                    />
                  </td>
                  <td className="px-2 py-1.5 text-right font-num text-[var(--text)] tabular-nums">
                    {row.stock_capital_pct === null || row.stock_capital_pct === undefined
                      ? "—"
                      : `${row.stock_capital_pct.toFixed(2)}%`}
                  </td>
                </>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const WEIGHT_BUCKETS = [
  { key: "0_2_5", min: 0, max: 2.5 },
  { key: "2_5_5", min: 2.5, max: 5 },
  { key: "5_7_5", min: 5, max: 7.5 },
  { key: "7_5_10", min: 7.5, max: 10 },
  { key: "10_plus", min: 10, max: Number.POSITIVE_INFINITY },
] as const;

function weightDistribution(rows: readonly EtfWeightRankRow[]): number[] {
  const counts = new Array(WEIGHT_BUCKETS.length).fill(0);
  for (const row of rows) {
    const weightPct = row.weight_pct;
    if (weightPct === null) continue; // locked row — no real value to bucket
    const idx = WEIGHT_BUCKETS.findIndex((b) => weightPct >= b.min && weightPct < b.max);
    if (idx >= 0) counts[idx] += 1;
  }
  return counts;
}

function WeightRankView({
  rows,
  t,
}: {
  readonly rows: readonly EtfWeightRankRow[];
  readonly t: ReturnType<typeof useTranslations>;
}): ReactNode {
  const locale = useLocale();
  const distribution = useMemo(() => weightDistribution(rows), [rows]);
  const maxCount = Math.max(1, ...distribution);

  if (rows.length === 0)
    return <p className="text-[13px] text-[var(--text-muted)]">{t("empty")}</p>;

  return (
    <div className="flex flex-col gap-4">
      <div>
        <p className="mb-2 text-[12px] text-[var(--text-muted)]">
          {t("weightRank.distributionTitle")}
        </p>
        <div className="flex flex-col gap-1.5">
          {WEIGHT_BUCKETS.map((bucket, i) => {
            const count = distribution[i] ?? 0;
            return (
              <div key={bucket.key} className="flex items-center gap-2 text-[11px]">
                <span className="w-16 shrink-0 text-[var(--text-muted)]">
                  {t(`weightRank.bucket.${bucket.key}`)}
                </span>
                <div className="h-3 flex-1 rounded-full bg-[var(--surface-2)]">
                  <div
                    className="h-3 rounded-full"
                    style={{
                      width: `${(count / maxCount) * 100}%`,
                      backgroundColor: "var(--chart-1)",
                    }}
                  />
                </div>
                <span className="w-6 shrink-0 text-right font-num text-[var(--text)] tabular-nums">
                  {count}
                </span>
              </div>
            );
          })}
        </div>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-[13px]">
          <thead>
            <tr className="border-[var(--border)] border-b text-[11px] text-[var(--text-muted)]">
              <th className="px-2 py-1.5 text-left font-medium">{t("weightRank.etf")}</th>
              <th className="px-2 py-1.5 text-right font-medium">{t("weightRank.weightPct")}</th>
              <th className="px-2 py-1.5 text-right font-medium">
                {t("weightRank.weightPctDelta")}
              </th>
              <th className="px-2 py-1.5 text-right font-medium">{t("weightRank.quarterRange")}</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-[var(--border)]">
            {rows.map((row) => (
              <tr key={row.locked ? `locked-${row.rank}` : row.etf_symbol}>
                {row.locked || row.etf_symbol === null || row.weight_pct === null ? (
                  <LockedRowCells rank={row.rank} />
                ) : (
                  <>
                    <td className="px-2 py-1.5 text-[var(--text)]">
                      <span className="font-num tabular-nums">{row.etf_symbol}</span>{" "}
                      <span className="text-[12px] text-[var(--text-muted)]">{row.etf_name}</span>
                    </td>
                    <td className="px-2 py-1.5 text-right font-num text-[var(--text)] tabular-nums">
                      {row.weight_pct.toFixed(2)}%
                    </td>
                    <td className="px-2 py-1.5 text-right font-num tabular-nums">
                      <DeltaCell value={row.weight_pct_delta} locale={locale} />
                    </td>
                    <td className="px-2 py-1.5 text-right font-num text-[11px] text-[var(--text-muted)] tabular-nums">
                      {row.quarter_min_weight_pct !== null &&
                      row.quarter_min_weight_pct !== undefined
                        ? `${row.quarter_min_weight_pct.toFixed(1)}–${(row.quarter_max_weight_pct ?? row.quarter_min_weight_pct).toFixed(1)}`
                        : "—"}
                    </td>
                  </>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

const POSITION_EVENT_STYLE: Record<
  EtfPositionEvent["event"],
  { readonly color: string; readonly labelKey: "new" | "removed" | "increased" | "decreased" }
> = {
  新增: { color: "var(--warn)", labelKey: "new" },
  刪除: { color: "var(--text-muted)", labelKey: "removed" },
  加碼: { color: "var(--up)", labelKey: "increased" },
  減碼: { color: "var(--down)", labelKey: "decreased" },
  no_prior_data: { color: "var(--text-muted)", labelKey: "removed" },
};

function EventRow({
  event,
  locale,
  t,
}: {
  readonly event: EtfPositionEvent;
  readonly locale: string;
  readonly t: ReturnType<typeof useTranslations>;
}): ReactNode {
  const style = POSITION_EVENT_STYLE[event.event];
  const isMasked = event.locked || event.etf_symbol === null;
  return (
    <li className="flex items-center gap-3 py-2.5">
      <span
        className="shrink-0 rounded-[var(--radius-pill)] px-2 py-0.5 font-medium text-[11px]"
        style={{
          backgroundColor: `color-mix(in srgb, ${style.color} 16%, transparent)`,
          color: style.color,
        }}
      >
        {t(`positionEvents.${style.labelKey}`)}
      </span>
      <span className="flex-1 text-[13px] text-[var(--text)]">
        {isMasked ? (
          <span className="inline-flex items-center gap-1.5 text-[var(--text-muted)]">
            <LockIcon size={11} weight="regular" aria-hidden />
            <span aria-hidden>••••</span>
          </span>
        ) : (
          <>
            <span className="font-num tabular-nums">{event.etf_symbol}</span>{" "}
            <span className="text-[12px] text-[var(--text-muted)]">{event.etf_name}</span>
          </>
        )}
      </span>
      <span className="font-num text-[12px] text-[var(--text-muted)] tabular-nums">
        {isMasked || event.shares_delta === null ? (
          <LockedValueCell />
        ) : (
          `${formatNumber(toLots(Math.abs(event.shares_delta)), locale)} ${t("lotUnit")}`
        )}
      </span>
      <span className="font-num text-[11px] text-[var(--text-muted)] tabular-nums">
        {event.event_date}
      </span>
    </li>
  );
}

/** Each count card is also a filter trigger — clicking one opens a modal scoped to just that
 * status's events (Screenshot_8's reference behavior), rather than only displaying a static
 * count. `status` is the real `HoldingStatus` value (`event.event`), not the card's own i18n
 * key, so filtering `events` by `e.event === status` needs no separate lookup table. */
function PositionEventCountCards({
  counts,
  events,
  t,
}: {
  readonly counts: EtfPositionEventCounts;
  readonly events: readonly EtfPositionEvent[];
  readonly t: ReturnType<typeof useTranslations>;
}): ReactNode {
  const locale = useLocale();
  const [activeStatus, setActiveStatus] = useState<EtfPositionEvent["event"] | null>(null);
  const cards = [
    {
      key: "countNew" as const,
      status: "新增" as const,
      count: counts.new_count,
      color: "var(--warn)",
    },
    {
      key: "countRemoved" as const,
      status: "刪除" as const,
      count: counts.removed_count,
      color: "var(--text-muted)",
    },
    {
      key: "increased" as const,
      status: "加碼" as const,
      count: counts.increased_count,
      color: "var(--up)",
    },
    {
      key: "decreased" as const,
      status: "減碼" as const,
      count: counts.decreased_count,
      color: "var(--down)",
    },
  ];
  const activeCard = cards.find((card) => card.status === activeStatus);
  const filteredEvents = activeStatus ? events.filter((event) => event.event === activeStatus) : [];

  return (
    <>
      <div className="mb-4 grid grid-cols-2 gap-2 sm:grid-cols-4">
        {cards.map((card) => (
          <button
            key={card.key}
            type="button"
            onClick={() => card.count > 0 && setActiveStatus(card.status)}
            disabled={card.count === 0}
            className="rounded-[var(--radius-input)] border p-2.5 text-left backdrop-blur-md transition-opacity disabled:cursor-default disabled:opacity-60"
            style={{
              borderColor: `color-mix(in srgb, ${card.color} 35%, transparent)`,
              backgroundColor: `color-mix(in srgb, ${card.color} 14%, transparent)`,
            }}
          >
            <p className="text-[11px] text-[var(--text-muted)]">
              {t(`positionEvents.${card.key}`)}
            </p>
            <p className="font-num text-[16px] tabular-nums" style={{ color: card.color }}>
              {card.count}
              <span className="ml-1 text-[11px] text-[var(--text-muted)]">
                {t("positionEvents.countUnit")}
              </span>
            </p>
          </button>
        ))}
      </div>
      <ExpandModal
        open={activeStatus !== null}
        title={activeCard ? t(`positionEvents.${activeCard.key}`) : ""}
        closeLabel={t("collapse")}
        onClose={() => setActiveStatus(null)}
      >
        {filteredEvents.length === 0 ? (
          <p className="text-[13px] text-[var(--text-muted)]">{t("positionEvents.empty")}</p>
        ) : (
          <ul className="divide-y divide-[var(--border)]">
            {filteredEvents.map((event, i) => (
              <EventRow
                key={`${event.event_date}-${event.etf_symbol ?? i}`}
                event={event}
                locale={locale}
                t={t}
              />
            ))}
          </ul>
        )}
      </ExpandModal>
    </>
  );
}

function PositionEventsTimeline({
  events,
  allEvents,
  counts,
  t,
}: {
  readonly events: readonly EtfPositionEvent[];
  readonly allEvents: readonly EtfPositionEvent[];
  readonly counts: EtfPositionEventCounts;
  readonly t: ReturnType<typeof useTranslations>;
}): ReactNode {
  const locale = useLocale();
  return (
    <div>
      <PositionEventCountCards counts={counts} events={allEvents} t={t} />
      {events.length === 0 ? (
        <p className="text-[13px] text-[var(--text-muted)]">{t("positionEvents.empty")}</p>
      ) : (
        <ul className="divide-y divide-[var(--border)]">
          {events.map((event, i) => (
            <EventRow
              key={`${event.event_date}-${event.etf_symbol ?? i}`}
              event={event}
              locale={locale}
              t={t}
            />
          ))}
        </ul>
      )}
    </div>
  );
}
