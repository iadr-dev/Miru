import { CardsIcon } from "@phosphor-icons/react/dist/ssr";
import { useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { AlertProposalCard } from "@/app/[locale]/(app)/chat/cards/alert-proposal-card";
import { AskUserCard } from "@/app/[locale]/(app)/chat/cards/ask-user-card";
import { BarChart } from "@/app/[locale]/(app)/chat/cards/bar-chart";
import { PLUMBING_KEYS } from "@/app/[locale]/(app)/chat/cards/card-format";
import { shapeForCardType } from "@/app/[locale]/(app)/chat/cards/card-registry";
import { CompareTable } from "@/app/[locale]/(app)/chat/cards/compare-table";
import { DataSheet } from "@/app/[locale]/(app)/chat/cards/data-sheet";
import { DivergingBarChart } from "@/app/[locale]/(app)/chat/cards/diverging-bar-chart";
import { EventTimeline } from "@/app/[locale]/(app)/chat/cards/event-timeline";
import { anyRowsPresent, hasSnapshotEntity } from "@/app/[locale]/(app)/chat/cards/extract-rows";
import { cardTypeLabel, fieldLabel } from "@/app/[locale]/(app)/chat/cards/field-labels";
import { FlowDistribution } from "@/app/[locale]/(app)/chat/cards/flow-distribution";
import {
  chartHintPayloads,
  chartHintUsable,
  GenericChart,
} from "@/app/[locale]/(app)/chat/cards/generic-chart";
import { GroupedBarChart } from "@/app/[locale]/(app)/chat/cards/grouped-bar-chart";
import { InstitutionalFlowTimeseries } from "@/app/[locale]/(app)/chat/cards/institutional-flow-timeseries";
import { LeaderboardTable } from "@/app/[locale]/(app)/chat/cards/leaderboard-table";
import { MemoryProposalCard } from "@/app/[locale]/(app)/chat/cards/memory-proposal-card";
import { ScheduleProposalCard } from "@/app/[locale]/(app)/chat/cards/schedule-proposal-card";
import { SnapshotCard } from "@/app/[locale]/(app)/chat/cards/snapshot-card";
import { ThemeTiersFlow } from "@/app/[locale]/(app)/chat/cards/theme-tiers-flow";
import { TimeseriesChart } from "@/app/[locale]/(app)/chat/cards/timeseries-chart";
import type { RichCard } from "@/types/conversation";

/**
 * Inline `rich_card` renderer. The card `data` shape is card-type-specific
 * and NOT invented here — the backend (`agent/loop.py::RichCard`) emits an already-governance-scanned
 * open object. If `data.chart_hint` validates against `chartHintSchema`, chart selection is
 * decoupled from `card_type` entirely: `GenericChart` picks a mark/encoding from the hint alone,
 * independent of which tool produced the data (see `generic-chart.tsx`'s own docstring) — this is
 * checked FIRST, before the legacy path, but is purely additive: no existing `card_type` needs a
 * `chart_hint` to keep rendering exactly as it does today. Absent a valid hint, `card_type` is
 * looked up in `card-registry.ts`'s shape map; a mapped type renders through one of 10 dedicated
 * components (candlestick+line chart/snapshot/data-sheet/leaderboard/compare/distribution bar/
 * timeline/diverging-net bar/grouped bar/bar) — but only once a pure, hook-free check confirms the
 * payload actually has the rows/entity that shape needs (`extract-rows.ts`); a mapped type whose
 * real payload doesn't resolve, or a type absent from the map altogether, falls through to the
 * flat key/value list below — honest, never a crash, never fabricated structure.
 *
 * Governance: renders external/model-derived values as inert text only (no HTML injection); numbers
 * use `--font-num` + `tabular-nums`; `--accent` is never used to color a value.
 */
export function RichCardView({
  card,
  onAskUserPick,
}: {
  card: RichCard;
  /** Only consulted for `card_type === "ask_user"` — every other card type ignores it. Optional
   * because most render sites (persisted history read-only contexts, etc.) never show an
   * `ask_user` card in the first place; when omitted, an `ask_user` card still renders its
   * question/options, just with no-op buttons. */
  onAskUserPick?: (prompt: string) => void;
}): ReactNode {
  if (card.card_type === "alert_proposal") {
    return <AlertProposalCard data={card.data} />;
  }
  if (card.card_type === "memory_proposal") {
    return <MemoryProposalCard data={card.data} />;
  }
  if (card.card_type === "schedule_proposal") {
    return <ScheduleProposalCard data={card.data} />;
  }
  if (card.card_type === "ask_user") {
    return <AskUserCard data={card.data} onPick={onAskUserPick ?? (() => {})} />;
  }
  const multi = chartHintPayloads(card.data);
  if (multi.length > 0) {
    return (
      <div className="space-y-3">
        {multi.map((payload, index) => (
          // biome-ignore lint/suspicious/noArrayIndexKey: a fixed, never-reordered list of this card's charts.
          <GenericChart key={index} data={payload} />
        ))}
      </div>
    );
  }
  if (chartHintUsable(card.data)) {
    return <GenericChart data={card.data} />;
  }
  const shape = shapeForCardType(card.card_type);
  const usable = shape === "snapshot" ? hasSnapshotEntity(card.data) : anyRowsPresent(card.data);
  if (shape && usable) {
    return renderShaped(shape, card);
  }
  return <FlatCard card={card} />;
}

function renderShaped(
  shape: NonNullable<ReturnType<typeof shapeForCardType>>,
  card: RichCard,
): ReactNode {
  switch (shape) {
    case "timeseries":
      return <TimeseriesChart data={card.data} />;
    case "snapshot":
      return <SnapshotCard card_type={card.card_type} data={card.data} />;
    case "data_sheet":
      return <DataSheet data={card.data} />;
    case "leaderboard":
      return <LeaderboardTable data={card.data} />;
    case "comparison":
      return <CompareTable data={card.data} />;
    case "distribution":
      return <FlowDistribution data={card.data} />;
    case "event_list":
      return <EventTimeline data={card.data} />;
    case "diverging_bar":
      return <DivergingBarChart data={card.data} />;
    case "grouped_bar":
      return <GroupedBarChart data={card.data} />;
    case "bar":
      return <BarChart data={card.data} />;
    case "institutional_flow_timeseries":
      return <InstitutionalFlowTimeseries data={card.data} />;
    case "theme_tiers_flow":
      return <ThemeTiersFlow data={card.data} />;
    default:
      return null;
  }
}

function FlatCard({ card }: { card: RichCard }): ReactNode {
  const t = useTranslations("chat.cards");
  const scalars = Object.entries(card.data).filter(
    ([key, value]) =>
      !PLUMBING_KEYS.has(key) &&
      (typeof value === "string" || typeof value === "number" || typeof value === "boolean"),
  );
  // Nothing but source/health plumbing came back (a genuinely empty tool result) — the caveat
  // text already covers the "no data" case in prose; an empty card with just a card_type label
  // is noise, not information, so render nothing rather than a content-free box.
  if (scalars.length === 0) {
    return null;
  }
  const typeLabel = cardTypeLabel(t, card.card_type);

  return (
    <figure
      className="mt-3 rounded-[var(--radius-card)] border border-[var(--border)] bg-[var(--surface)] p-5"
      aria-label={typeLabel}
    >
      <figcaption className="mb-3 inline-flex items-center gap-2 font-medium text-[13px] text-[var(--text-muted)]">
        <CardsIcon size={16} weight="regular" aria-hidden />
        {typeLabel}
      </figcaption>
      <dl className="divide-y divide-[var(--border)]">
        {scalars.map(([key, value]) => (
          <div key={key} className="flex items-center justify-between gap-4 py-2">
            <dt className="truncate text-[var(--text-muted)] text-sm">{fieldLabel(t, key)}</dt>
            <dd
              className={
                typeof value === "number"
                  ? "font-num text-[var(--text)] text-sm tabular-nums"
                  : "text-[var(--text)] text-sm"
              }
            >
              {String(value)}
            </dd>
          </div>
        ))}
      </dl>
    </figure>
  );
}
