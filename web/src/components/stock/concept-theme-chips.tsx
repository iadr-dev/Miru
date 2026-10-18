"use client";

import { useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { FeatureLock } from "@/components/ui/feature-lock";
import { Link } from "@/i18n/navigation";
import { hashToChartToken } from "@/lib/chart-token-hash";
import type { SymbolTheme } from "@/types/stock/profile";

/** 所屬題材: the concept themes a stock belongs to, as small wrapping chips that open each theme's
 * page. Industry-chain themes are not listed here (they have their own 產業鏈 view). Renders
 * nothing when the stock is in no concept theme, so there is never an empty label. */
export function ConceptThemeChips({
  themes,
  locked = false,
}: {
  readonly themes: readonly SymbolTheme[];
  /** The server withheld some concept themes for the caller's plan (`concept_locked`). */
  readonly locked?: boolean;
}): ReactNode {
  const t = useTranslations("stock.industry.conceptThemes");
  const tLock = useTranslations("featureLock");
  const concept = themes.filter((theme) => theme.kind === "concept");
  if (concept.length === 0 && !locked) {
    return null;
  }
  return (
    <div className="flex flex-wrap items-center gap-x-2 gap-y-1.5">
      <span className="text-[12px] text-[var(--text-muted)]">{t("title")}</span>
      <ul className="flex flex-wrap gap-1.5">
        {concept.map((theme) => (
          <li key={theme.theme_key}>
            <Link
              href={`/themes/${encodeURIComponent(theme.theme_key)}`}
              aria-label={t("aria", { name: theme.name_zh })}
              className="relative inline-flex items-center gap-1.5 rounded-[var(--radius-pill)] border border-[var(--border)] px-2.5 py-1 text-[12px] text-[var(--text-muted)] transition-colors before:absolute before:inset-x-0 before:-inset-y-2 before:content-[''] hover:text-[var(--text)] focus-visible:outline-2 focus-visible:outline-[var(--accent)]"
            >
              <span
                aria-hidden
                className="size-1.5 rounded-full"
                style={{ backgroundColor: hashToChartToken(theme.name_zh) }}
              />
              {theme.name_zh}
            </Link>
          </li>
        ))}
        {locked ? (
          <li>
            <FeatureLock message={tLock("conceptChips.more")} variant="chip" />
          </li>
        ) : null}
      </ul>
    </div>
  );
}
