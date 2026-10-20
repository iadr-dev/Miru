"use client";

import { CaretDownIcon, CaretUpIcon, StarIcon } from "@phosphor-icons/react/dist/ssr";
import type { CellContext } from "@tanstack/react-table";
import { useTranslations } from "next-intl";
import { createContext, type ReactNode, useContext } from "react";
import { hashToChartToken } from "@/lib/chart-token-hash";
import { cn } from "@/lib/cn";
import type { ScreenerSurvivor } from "@/types/screener";

/** One screener result row once the quote / `survivor.display` enrichment is merged in. */
export type EnrichedRow = ScreenerSurvivor & {
  name: string | null;
  price: number | null;
  changePct: number | null;
  volume: number | null;
  industryCategory: string | null;
  peRatio: number | null;
  marketCap: number | null;
  pctHeldInstitutions: number | null;
  shortPctOfFloat: number | null;
};

/** What the star and matched-conditions cells read from the table that renders them. Cells are
 * module-level components (stable identity, so a row's buttons are not unmounted on every parent
 * render), so this table-level data reaches them through context instead of a closure. */
export type ScreenerCellContextValue = {
  readonly starredSymbols: ReadonlySet<string>;
  readonly toggleStar: (symbol: string) => void;
  readonly conditionLabels: ReadonlyMap<string, string>;
};

export const ScreenerCellContext = createContext<ScreenerCellContextValue>({
  starredSymbols: new Set(),
  toggleStar: () => undefined,
  conditionLabels: new Map(),
});

type Cell = (info: CellContext<EnrichedRow, unknown>) => ReactNode;

const DASH = <span className="text-[var(--text-muted)]">—</span>;

function MutedNumber({
  value,
  format,
}: {
  readonly value: number | null;
  readonly format: (value: number) => string;
}): ReactNode {
  if (value === null) return DASH;
  return <span className="font-num text-[var(--text-muted)] tabular-nums">{format(value)}</span>;
}

export const SectorCell: Cell = (info) => {
  const category = info.row.original.industryCategory;
  if (!category) return null;
  const token = hashToChartToken(category);
  return (
    <span
      className="rounded-[var(--radius-pill)] px-2 py-0.5 text-[11px]"
      style={{
        backgroundColor: `color-mix(in srgb, ${token} 16%, transparent)`,
        color: token,
      }}
    >
      {category}
    </span>
  );
};

export const MarketCapCell: Cell = (info) => (
  <MutedNumber
    value={info.row.original.marketCap}
    format={(value) => `${(value / 1_000_000_000).toFixed(1)}B`}
  />
);

export const PeRatioCell: Cell = (info) => (
  <MutedNumber value={info.row.original.peRatio} format={(value) => `${value.toFixed(1)}x`} />
);

export const PctHeldInstitutionsCell: Cell = (info) => (
  <MutedNumber
    value={info.row.original.pctHeldInstitutions}
    format={(value) => `${value.toFixed(1)}%`}
  />
);

export const ShortPctOfFloatCell: Cell = (info) => (
  <MutedNumber
    value={info.row.original.shortPctOfFloat}
    format={(value) => `${value.toFixed(1)}%`}
  />
);

export const VolumeCell: Cell = (info) => (
  <MutedNumber value={info.row.original.volume} format={(value) => value.toLocaleString()} />
);

export const SymbolCell: Cell = (info) => (
  <div className="flex flex-col">
    <span className="font-medium text-[var(--text)]">{info.getValue<string>()}</span>
    {info.row.original.name ? (
      <span className="text-[11px] text-[var(--text-muted)]">{info.row.original.name}</span>
    ) : null}
  </div>
);

export const PriceCell: Cell = (info) => {
  const price = info.row.original.price;
  if (price === null) return DASH;
  return <span className="font-num text-[var(--text)] tabular-nums">{price.toLocaleString()}</span>;
};

export const ChangePctCell: Cell = (info) => {
  const value = info.row.original.changePct;
  if (value === null) return DASH;
  const up = value >= 0;
  return (
    <span
      role="group"
      className={cn(
        "inline-flex items-center gap-0.5 font-num tabular-nums",
        up ? "text-[var(--up)]" : "text-[var(--down)]",
      )}
      aria-label={`${up ? "上漲" : "下跌"} ${Math.abs(value).toFixed(2)}%`}
    >
      {up ? <CaretUpIcon size={11} weight="fill" /> : <CaretDownIcon size={11} weight="fill" />}
      {Math.abs(value).toFixed(2)}%
    </span>
  );
};

export const StarCell: Cell = (info) => {
  const t = useTranslations("screener.table");
  const { starredSymbols, toggleStar } = useContext(ScreenerCellContext);
  const symbol = info.row.original.symbol;
  const starred = starredSymbols.has(symbol);
  return (
    <button
      type="button"
      aria-pressed={starred}
      aria-label={t("star")}
      onClick={(event) => {
        event.stopPropagation();
        toggleStar(symbol);
      }}
      className="grid size-8 place-items-center rounded-[var(--radius-button)] text-[var(--text-muted)] hover:text-[var(--accent)]"
    >
      <StarIcon
        size={16}
        weight={starred ? "fill" : "regular"}
        className={starred ? "text-[var(--accent)]" : undefined}
      />
    </button>
  );
};

export const SponsorLockedCell: Cell = () => {
  const t = useTranslations("screener.table");
  return (
    <span className="rounded-[var(--radius-pill)] bg-[var(--surface-2)] px-2 py-1 text-[11px] text-[var(--text-muted)] backdrop-blur-sm">
      {t("sponsorLocked")}
    </span>
  );
};

export const MatchedConditionsCell: Cell = (info) => {
  const { conditionLabels } = useContext(ScreenerCellContext);
  return (
    <div className="flex flex-wrap gap-1">
      {info.row.original.matched_conditions.map((condition) => (
        <span
          key={condition}
          className="rounded-[var(--radius-pill)] border border-[var(--accent)] px-2 py-0.5 text-[11px] text-[var(--accent)]"
        >
          {conditionLabels.get(condition) ?? condition}
        </span>
      ))}
    </div>
  );
};
