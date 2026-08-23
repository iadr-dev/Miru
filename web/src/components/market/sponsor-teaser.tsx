"use client";

import { LockIcon } from "@phosphor-icons/react";
import { useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { Link } from "@/i18n/navigation";
import { cn } from "@/lib/cn";

/**
 * 馬賽克 (mosaic) teaser for a Sponsor-tier tool result the caller isn't entitled to — the
 * `{locked, required_tier, preview}` envelope is a normal HTTP 200, never an error
 * (web/CLAUDE.md §4/§8), so this renders inline and never blocks the rest of the page.
 *
 * The frame/headline/labels stay SHARP — only `children` (the value layer, e.g. a chart/table
 * shaped like the real data) sits under `backdrop-blur` + a diagonal texture overlay, with a
 * centered lock chip + one calm CTA on top. Falls back to a solid `--surface` scrim under
 * `prefers-reduced-transparency` via the shared `.glass` utility (`app/globals.css`).
 */
export function SponsorTeaser({
  headline,
  preview,
  requiredTier,
  children,
}: {
  headline: string;
  preview: string;
  requiredTier: "premium" | "pro";
  children: ReactNode;
}): ReactNode {
  const t = useTranslations("market.teaser");

  return (
    <div className="relative overflow-hidden rounded-[var(--radius-card)]">
      <p className="mb-2 font-medium text-[14px] text-[var(--text)]">{headline}</p>
      <div aria-hidden className="pointer-events-none blur-[6px]">
        {children}
      </div>
      <div
        className={cn(
          "glass absolute inset-0 top-7 flex flex-col items-center justify-center gap-3 rounded-[var(--radius-card)]",
          "border border-[var(--border)] bg-[var(--surface)]/70 px-6 py-8 text-center",
        )}
        style={{
          backgroundImage:
            "repeating-linear-gradient(135deg, color-mix(in srgb, var(--border) 40%, transparent) 0px, color-mix(in srgb, var(--border) 40%, transparent) 1px, transparent 1px, transparent 12px)",
        }}
      >
        <span className="grid size-11 place-items-center rounded-[var(--radius-pill)] bg-[var(--surface-2)]">
          <LockIcon size={20} weight="regular" aria-hidden className="text-[var(--text-muted)]" />
        </span>
        <p className="max-w-xs text-[13px] text-[var(--text-muted)] leading-relaxed">{preview}</p>
        <Link
          href="/settings/billing"
          aria-label={t("cta", { tier: t(`tier.${requiredTier}`) })}
          className={cn(
            "inline-flex min-h-11 items-center justify-center gap-2 rounded-[var(--radius-button)] px-5",
            "bg-[var(--accent)] font-medium text-[15px] text-[var(--bg)]",
            "transition-colors duration-150 hover:opacity-90 active:scale-[0.98]",
          )}
        >
          {t("cta", { tier: t(`tier.${requiredTier}`) })}
        </Link>
      </div>
    </div>
  );
}
