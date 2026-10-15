"use client";

import { CaretLeftIcon, CaretRightIcon, LockIcon } from "@phosphor-icons/react";
import { useQuery } from "@tanstack/react-query";
import { useLocale, useTranslations } from "next-intl";
import { type ReactNode, useRef } from "react";
import {
  getFuturesSnapshot,
  getMarketIndexStrip,
  getTaiexVix,
  getTickSnapshot,
} from "@/lib/api/market";
import { queryKeys } from "@/lib/api/query-keys";
import { formatNumber } from "@/lib/format-number";
import { directionColor, directionGlyph } from "@/lib/signed";
import type { MarketIndexStripItem } from "@/types/market/quotes";
import type { MaskedTeaser } from "@/types/market/shared";
import { isMaskedTeaser } from "@/types/market/shared";

const SCROLL_STEP_PX = 240;

// 半導體業 tick-snapshot index code (`sources/finmind/realtime.py`'s documented 3-digit index
// codes) and the near-month 台指期 futures contract code — both Sponsor-tier, rendered through
// their own existing tools/routes (never re-tiered), same masking as every other Sponsor card.
const SEMICONDUCTOR_INDEX_CODE = "036";
const TAIEX_FUTURES_CODE = "TXF";

type SponsorItem =
  | { value: string; changePct: number | null }
  | MaskedTeaser
  | "loading"
  | "unavailable";

/**
 * TW-tab ticker strip — 加權指數/櫃買指數/美元/黃金/原油 (public, `get_market_index_strip`) plus
 * 半導體業/台指期/VIX (Sponsor-tier, existing `get_tick_snapshot`/`get_futures_snapshot`/
 * `get_taiex_vix` tools) in one horizontal scrollable row, mirroring a market-data ticker strip
 * (name + value + change/%change per item). The 3 Sponsor items render a compact locked chip
 * (`LockedStripItem`) when unentitled — masked individually, never the whole strip — sized to fit
 * this row's ~128px chips rather than `SponsorTeaser`'s panel-sized lock+preview+CTA layout,
 * which overflowed this narrow a container.
 */
export function MarketIndexStrip(): ReactNode {
  const locale = useLocale();
  const t = useTranslations("market.cards.indexStrip");
  const scrollRef = useRef<HTMLDivElement>(null);

  const publicQuery = useQuery({
    queryKey: queryKeys.read("market_index_strip"),
    queryFn: () => getMarketIndexStrip(),
  });
  const sectorQuery = useQuery({
    queryKey: queryKeys.read("tick_snapshot", SEMICONDUCTOR_INDEX_CODE),
    queryFn: () => getTickSnapshot(SEMICONDUCTOR_INDEX_CODE),
  });
  const futuresQuery = useQuery({
    queryKey: queryKeys.read("futures_snapshot", TAIEX_FUTURES_CODE),
    queryFn: () => getFuturesSnapshot(TAIEX_FUTURES_CODE),
  });
  const vixQuery = useQuery({
    queryKey: queryKeys.read("taiex_vix"),
    queryFn: () => getTaiexVix(),
  });

  function scroll(direction: -1 | 1): void {
    scrollRef.current?.scrollBy({ left: direction * SCROLL_STEP_PX, behavior: "smooth" });
  }

  const sectorItem = toSponsorItem(sectorQuery.data, sectorQuery.isPending, (rows) => {
    // Single-row snapshot for one index code — no contract fan-out to pick from.
    const row = rows[0];
    return row
      ? { value: formatNumber(row.close, locale, { decimals: 2 }), changePct: row.change_rate }
      : null;
  });
  const futuresItem = toSponsorItem(futuresQuery.data, futuresQuery.isPending, (rows) => {
    // `data_id=TXF` fans out across every open contract month (TXFJ6/TXFK6/TXFR1/...), not one
    // row — the actively-traded front-month contract is the one with by far the highest
    // `total_volume`, confirmed live (thousands vs. single digits for the other months).
    const row = rows.reduce<(typeof rows)[number] | null>(
      (best, candidate) =>
        best === null || candidate.total_volume > best.total_volume ? candidate : best,
      null,
    );
    return row
      ? { value: formatNumber(row.close, locale, { decimals: 0 }), changePct: row.change_rate }
      : null;
  });
  const vixItem = toSponsorItem(vixQuery.data, vixQuery.isPending, (rows) => {
    // Rows come back in ascending date/time order — the latest reading is the last one, not
    // `rows[0]`. `OptionVixRow` carries no pre-computed change, so diff against the last reading
    // from a strictly earlier trade_date (never against a same-day-earlier tick).
    const latest = rows.at(-1);
    if (!latest) return null;
    const priorDay = [...rows].reverse().find((row) => row.trade_date !== latest.trade_date);
    const changePct =
      priorDay && priorDay.vix !== 0 ? ((latest.vix - priorDay.vix) / priorDay.vix) * 100 : null;
    return { value: formatNumber(latest.vix, locale, { decimals: 2 }), changePct };
  });

  const isInitialLoad =
    publicQuery.isPending && sectorQuery.isPending && futuresQuery.isPending && vixQuery.isPending;

  if (isInitialLoad) {
    return (
      <div className="flex h-16 w-full animate-pulse rounded-[var(--radius-card)] bg-[var(--surface-2)]" />
    );
  }

  return (
    <div className="relative flex items-center gap-1">
      <button
        type="button"
        onClick={() => scroll(-1)}
        aria-label={t("scrollLeft")}
        className="grid size-8 shrink-0 place-items-center rounded-[var(--radius-pill)] bg-[var(--surface-2)] text-[var(--text-muted)] hover:text-[var(--text)]"
      >
        <CaretLeftIcon size={16} weight="regular" aria-hidden />
      </button>

      <div
        ref={scrollRef}
        className="flex flex-1 gap-2 overflow-x-auto scroll-smooth"
        style={{ scrollbarWidth: "none" }}
      >
        {publicQuery.data?.rows.map((item) => (
          <PublicStripItem key={item.key} item={item} />
        ))}
        <SponsorStripItem label={t("semiconductorIndex")} item={sectorItem} />
        <SponsorStripItem label={t("taiexFutures")} item={futuresItem} />
        <SponsorStripItem label={t("vix")} item={vixItem} />
      </div>

      <button
        type="button"
        onClick={() => scroll(1)}
        aria-label={t("scrollRight")}
        className="grid size-8 shrink-0 place-items-center rounded-[var(--radius-pill)] bg-[var(--surface-2)] text-[var(--text-muted)] hover:text-[var(--text)]"
      >
        <CaretRightIcon size={16} weight="regular" aria-hidden />
      </button>
    </div>
  );
}

/** Every Sponsor-backed query here returns `rowsOutputSchema`'s `{rows: [...]}` shape (or the
 * masked-teaser envelope) — `select` picks/derives the display row(s) from the full array (never
 * assume `rows[0]` is "the" reading: `taiex_vix` comes back in ascending time order, and
 * `futures_snapshot`'s `data_id=TXF` fans out across every open contract month), passes the
 * masked-teaser envelope through unchanged, or reports "unavailable" when the entitled caller's
 * own fetch came back healthy but empty (e.g. a Sponsor-tier snapshot dataset with no reading
 * outside live trading hours) — distinct from "still loading", which only applies while the
 * query is genuinely in flight. */
function toSponsorItem<Row>(
  data: { rows: readonly Row[] } | MaskedTeaser | undefined,
  isPending: boolean,
  select: (rows: readonly Row[]) => { value: string; changePct: number | null } | null,
): SponsorItem {
  if (isPending) {
    return "loading";
  }
  if (data === undefined) {
    return "unavailable";
  }
  if (isMaskedTeaser(data)) {
    return data;
  }
  return select(data.rows) ?? "unavailable";
}

// Maps each `MarketIndexStripItemRow.key` (TW strip + US strip, both tools) to its i18n label
// key — the backend row's own `label` field is Chinese-only (`sources`/`tools` have no locale
// concept), so the client is the one seam that can render an English label in the `en` locale.
const ITEM_LABEL_KEY: Record<string, string> = {
  taiex: "taiex",
  otc: "otc",
  usd_twd: "usdTwd",
  jpy_twd: "jpyTwd",
  gold: "gold",
  crude_oil_wti: "crudeOilWti",
  crude_oil_brent: "crudeOilBrent",
  sp500: "sp500",
  dow_jones: "dowJones",
  nasdaq: "nasdaq",
  sox: "sox",
  vix: "vix",
  us10y: "us10y",
  fed_rate: "fedRate",
};

/** Exported so `us-market-index-strip.tsx` (an entirely-public strip, no Sponsor items) reuses
 * this same 名稱+值+漲跌% chip rendering rather than duplicating it. */
export function PublicStripItem({ item }: { readonly item: MarketIndexStripItem }): ReactNode {
  const locale = useLocale();
  const t = useTranslations("market.cards.indexStrip");
  const labelKey = ITEM_LABEL_KEY[item.key];
  const label = labelKey ? t(labelKey) : item.label;
  return (
    <div className="surface-card flex min-w-[128px] shrink-0 flex-col gap-0.5 px-3 py-2">
      <span className="truncate text-[12px] text-[var(--text-muted)]">{label}</span>
      <StripItemValue
        value={formatNumber(item.value, locale, { decimals: 2 })}
        changePct={item.change_pct}
      />
    </div>
  );
}

function SponsorStripItem({
  label,
  item,
}: {
  readonly label: string;
  readonly item: SponsorItem;
}): ReactNode {
  if (item === "loading") {
    return (
      <div className="min-w-[128px] shrink-0 animate-pulse rounded-[var(--radius-card)] border border-[var(--border)] bg-[var(--surface-2)] px-3 py-2" />
    );
  }
  if (item === "unavailable") {
    return (
      <div className="surface-card flex min-w-[128px] shrink-0 flex-col gap-0.5 px-3 py-2">
        <span className="truncate text-[12px] text-[var(--text-muted)]">{label}</span>
        <span className="font-num text-[15px] text-[var(--text-muted)] tabular-nums">—</span>
      </div>
    );
  }
  if (isMaskedTeaser(item)) {
    return <LockedStripItem label={label} />;
  }
  return (
    <div className="surface-card flex min-w-[128px] shrink-0 flex-col gap-0.5 px-3 py-2">
      <span className="truncate text-[12px] text-[var(--text-muted)]">{label}</span>
      <StripItemValue value={item.value} changePct={item.changePct} />
    </div>
  );
}

/** Compact locked chip for one Sponsor-tier ticker item — same footprint as `PublicStripItem`/
 * the real `SponsorStripItem` body (label + one value line), just with a lock icon + "••••" in
 * place of the real value, never `SponsorTeaser`'s panel-sized lock+preview+CTA layout. */
function LockedStripItem({ label }: { readonly label: string }): ReactNode {
  return (
    <div className="surface-card flex min-w-[128px] shrink-0 flex-col gap-0.5 px-3 py-2">
      <span className="truncate text-[12px] text-[var(--text-muted)]">{label}</span>
      <span className="inline-flex items-center gap-1 text-[var(--text-muted)]">
        <LockIcon size={11} weight="regular" aria-hidden />
        <span aria-hidden className="font-display text-[17px] tabular-nums">
          ••••
        </span>
      </span>
    </div>
  );
}

function StripItemValue({
  value,
  changePct,
}: {
  readonly value: string;
  readonly changePct: number | null;
}): ReactNode {
  const tCommon = useTranslations("common");
  const locale = useLocale();
  const up = changePct !== null && changePct >= 0;
  const color = changePct === null ? "var(--text-muted)" : directionColor(changePct ?? 0);
  return (
    <span
      role="group"
      aria-label={
        changePct !== null
          ? `${value} ${up ? tCommon("direction.up") : tCommon("direction.down")} ${Math.abs(changePct).toFixed(2)}%`
          : value
      }
      className="flex items-baseline gap-1.5"
    >
      <span aria-hidden className="font-display text-[17px] text-[var(--text)] tabular-nums">
        {value}
      </span>
      {changePct !== null ? (
        <span aria-hidden className="font-num text-[11px] tabular-nums" style={{ color }}>
          {directionGlyph(changePct ?? 0)}
          {formatNumber(Math.abs(changePct), locale, { decimals: 2 })}%
        </span>
      ) : null}
    </span>
  );
}
