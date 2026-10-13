"use client";

import { XIcon } from "@phosphor-icons/react";
import { useLocale, useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { SignedChange } from "@/components/ui/signed-change";
import { formatNumber } from "@/lib/format-number";
import { iconToSvgElement, sectorIconKeyForIndustry } from "@/lib/sector-rotation-icons";
import { useQuadrantLabel } from "./quadrant-legend";
import { colorForQuadrant, quadrantFor } from "./rrg-layout";

export interface SectorDetail {
  readonly industryId: string;
  readonly industryName: string;
  readonly weeklyChangePct: number;
  readonly rsRatio: number;
  readonly rsMomentum: number;
  readonly weight: number;
}

const CARD_CLASS =
  "rounded-[var(--radius-card)] border border-[var(--border)] bg-[var(--surface)]/70 backdrop-blur-md";

/** The selected sector at the week on screen: its quadrant, the two RRG figures, the week's move
 * and its share of turnover. The signed move goes through `SignedChange` (紅漲綠跌 + glyph + an
 * aria-label), and the quadrant is named in words as well as coloured. */
export function SectorDetailCard({
  detail,
  onClear,
}: {
  readonly detail: SectorDetail | null;
  readonly onClear: () => void;
}): ReactNode {
  const t = useTranslations("market.cards.sectorHeatmap.rotationRrg");
  const locale = useLocale();
  const quadrantLabel = useQuadrantLabel();

  if (!detail) {
    return (
      <p className={`${CARD_CLASS} px-3 py-2 text-[12px] text-[var(--text-muted)]`}>
        {t("detailHint")}
      </p>
    );
  }
  const quadrant = quadrantFor(detail.rsRatio, detail.rsMomentum);
  const color = colorForQuadrant(quadrant);
  const num = (value: number): string => formatNumber(value, locale, { decimals: 1 });
  return (
    <section aria-label={detail.industryName} className={`${CARD_CLASS} p-3`}>
      <header className="mb-2 flex items-center gap-2">
        <span style={{ color }} className="shrink-0">
          {iconToSvgElement(sectorIconKeyForIndustry(detail.industryId), 18)}
        </span>
        <h4 className="min-w-0 flex-1 truncate font-semibold text-[13px] text-[var(--text)]">
          {detail.industryName}
        </h4>
        <span className="shrink-0 font-semibold text-[12px]" style={{ color }}>
          {quadrantLabel(quadrant)}
        </span>
        <button
          type="button"
          onClick={onClear}
          aria-label={t("clearSelection")}
          className="grid size-8 shrink-0 place-items-center rounded-full text-[var(--text-muted)] hover:text-[var(--text)]"
        >
          <XIcon size={14} />
        </button>
      </header>
      <dl className="grid grid-cols-2 gap-x-4 gap-y-1.5 text-[12px]">
        <dt className="text-[var(--text-muted)]">{t("ratio")}</dt>
        <dd className="text-right font-num text-[var(--text)] tabular-nums">
          {num(detail.rsRatio)}
        </dd>
        <dt className="text-[var(--text-muted)]">{t("momentum")}</dt>
        <dd className="text-right font-num text-[var(--text)] tabular-nums">
          {num(detail.rsMomentum)}
        </dd>
        <dt className="text-[var(--text-muted)]">{t("weeklyChange")}</dt>
        <dd className="text-right font-num tabular-nums">
          <SignedChange value={detail.weeklyChangePct} decimals={1} unit="%" />
        </dd>
        <dt className="text-[var(--text-muted)]">{t("turnoverShare")}</dt>
        <dd className="text-right font-num text-[var(--text)] tabular-nums">
          {num(detail.weight * 100)}%
        </dd>
      </dl>
    </section>
  );
}
