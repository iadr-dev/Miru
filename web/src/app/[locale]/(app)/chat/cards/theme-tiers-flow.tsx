"use client";

import { ArrowRightIcon, CheckCircleIcon, LightningIcon } from "@phosphor-icons/react";
import { useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { asArray, asString } from "@/app/[locale]/(app)/chat/cards/card-format";

/** `get_theme_tiers`'s three tier buckets — each row is a SUB-INDUSTRY name (e.g. "IC 設計"),
 * not an individual stock ticker; per-ticker membership within a sub-industry is a separate
 * `get_theme_members` lookup the model still narrates in prose. `source` distinguishes an
 * editorially-verified ("curated") assignment from an automatically-classified ("heuristic")
 * one — never presented identically, per `theme-lookup/SKILL.md`'s own boundary rule. */
const TIER_KEYS = ["upstream", "midstream", "downstream"] as const;
type TierKey = (typeof TIER_KEYS)[number];

type TierRow = { sub_industry: string; source: string };

/** A theme's upstream → midstream → downstream sub-industry breakdown, rendered as a left-to-right
 * flow (matches how a supply chain is conventionally read and how the model already narrates it) —
 * replaces the flat `DataSheet` rendering of the same payload, which listed all three tiers as one
 * undifferentiated table with no left-right structure. */
export function ThemeTiersFlow({ data }: { data: unknown }): ReactNode {
  const t = useTranslations("chat.cards");
  if (!data || typeof data !== "object") {
    return null;
  }
  const record = data as Record<string, unknown>;
  const found = record.found !== false;
  const themeKey = asString(record.theme_key);
  const tiers: Record<TierKey, TierRow[]> = {
    upstream: extractTierRows(record.upstream),
    midstream: extractTierRows(record.midstream),
    downstream: extractTierRows(record.downstream),
  };
  const totalRows = TIER_KEYS.reduce((sum, key) => sum + tiers[key].length, 0);
  if (!found || totalRows === 0) {
    return null;
  }

  return (
    <div className="mt-3 rounded-[var(--radius-card)] border border-[var(--border)] bg-[var(--surface)] p-4">
      {themeKey ? (
        <p className="mb-3 text-[13px] text-[var(--text-muted)]">
          {t("fields.theme_key")}: <span className="text-[var(--text)]">{themeKey}</span>
        </p>
      ) : null}
      <div className="flex flex-col gap-3 md:flex-row md:items-stretch md:gap-2">
        {TIER_KEYS.map((tierKey, index) => (
          <div key={tierKey} className="flex flex-1 items-stretch gap-2">
            <TierColumn tierKey={tierKey} rows={tiers[tierKey]} t={t} />
            {index < TIER_KEYS.length - 1 ? (
              <div
                className="hidden shrink-0 items-center text-[var(--text-muted)] md:flex"
                aria-hidden
              >
                <ArrowRightIcon size={16} weight="regular" />
              </div>
            ) : null}
          </div>
        ))}
      </div>
      <div className="mt-3 flex flex-wrap gap-4 border-[var(--border)] border-t pt-3 text-[12px] text-[var(--text-muted)]">
        <span className="inline-flex items-center gap-1">
          <CheckCircleIcon size={13} weight="regular" aria-hidden />
          {t("themeTierSourceCurated")}
        </span>
        <span className="inline-flex items-center gap-1">
          <LightningIcon size={13} weight="regular" aria-hidden />
          {t("themeTierSourceHeuristic")}
        </span>
      </div>
    </div>
  );
}

function TierColumn({
  tierKey,
  rows,
  t,
}: {
  tierKey: TierKey;
  rows: TierRow[];
  t: ReturnType<typeof useTranslations>;
}): ReactNode {
  return (
    <div className="min-w-0 flex-1 rounded-[var(--radius-input)] border border-[var(--border)] bg-[var(--surface-2)] p-3">
      <p className="mb-2 font-medium text-[13px] text-[var(--text)]">{t(`themeTier.${tierKey}`)}</p>
      {rows.length === 0 ? (
        <p className="text-[12px] text-[var(--text-muted)]">{t("themeTierEmpty")}</p>
      ) : (
        <ul className="flex flex-col gap-1.5">
          {rows.map((row) => (
            <li
              key={row.sub_industry}
              className="flex items-center gap-1.5 text-[13px] text-[var(--text)]"
            >
              {row.source === "curated" ? (
                <CheckCircleIcon
                  size={13}
                  weight="regular"
                  className="shrink-0 text-[var(--accent)]"
                  aria-label={t("themeTierSourceCurated")}
                />
              ) : (
                <LightningIcon
                  size={13}
                  weight="regular"
                  className="shrink-0 text-[var(--warn)]"
                  aria-label={t("themeTierSourceHeuristic")}
                />
              )}
              <span className="truncate">{row.sub_industry}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function extractTierRows(value: unknown): TierRow[] {
  return asArray(value)
    .map((row) => ({
      sub_industry: asString(row.sub_industry) ?? "",
      source: asString(row.source) ?? "heuristic",
    }))
    .filter((row) => row.sub_industry.length > 0);
}
