"use client";

import { StarIcon, XIcon } from "@phosphor-icons/react";
import { useTranslations } from "next-intl";
import { type ReactNode, useState } from "react";
import { cn } from "@/lib/cn";

type WatchlistTab = "tw" | "us" | "etf";
const TABS: readonly WatchlistTab[] = ["tw", "us", "etf"];

/**
 * The right watchlist rail — this pass is the toggleable shell + empty-state placeholder only. The
 * full drag-and-drop watchlist (docs/web/watchlist/README.md) is future work; nothing here is wired
 * to `/watchlists` yet, so it shows the empty-group copy per README §9. The `☆` toggle lives in the
 * top bar; this renders when open.
 */
export function WatchlistRail({ onClose }: { onClose: () => void }): ReactNode {
  const t = useTranslations("shell.watchlist");
  const [tab, setTab] = useState<WatchlistTab>("tw");

  return (
    <aside
      aria-label={t("title")}
      className="flex h-full w-[300px] flex-col border-[var(--border)] border-l bg-[var(--surface)]"
    >
      <div className="flex h-14 shrink-0 items-center justify-between border-[var(--border)] border-b px-4">
        <span className="inline-flex items-center gap-2 font-semibold text-[15px] text-[var(--text)]">
          <StarIcon size={18} weight="regular" className="text-[var(--accent)]" aria-hidden />
          {t("title")}
        </span>
        <button
          type="button"
          onClick={onClose}
          aria-label={t("close")}
          title={t("close")}
          className={cn(
            "inline-flex size-11 items-center justify-center rounded-[var(--radius-button)]",
            "text-[var(--text-muted)] transition-colors hover:bg-[var(--surface-2)] hover:text-[var(--text)]",
          )}
        >
          <XIcon size={18} weight="regular" />
        </button>
      </div>

      {/* Market tabs (segmented) */}
      <div className="flex gap-1 border-[var(--border)] border-b p-3" role="tablist">
        {TABS.map((key) => {
          const active = key === tab;
          return (
            <button
              key={key}
              type="button"
              role="tab"
              aria-selected={active}
              onClick={() => setTab(key)}
              className={cn(
                "min-h-11 flex-1 rounded-[var(--radius-pill)] px-3 font-medium text-sm transition-colors",
                active
                  ? "bg-[var(--accent-weak)] text-[var(--text)]"
                  : "text-[var(--text-muted)] hover:text-[var(--text)]",
              )}
            >
              {t(`tabs.${key}`)}
            </button>
          );
        })}
      </div>

      {/* Empty state (cold-start copy per README §9) */}
      <div className="flex flex-1 flex-col items-center justify-center gap-3 px-6 text-center">
        <span className="grid size-12 place-items-center rounded-full bg-[var(--surface-2)]">
          <StarIcon size={22} weight="regular" className="text-[var(--text-muted)]" aria-hidden />
        </span>
        <p className="text-[var(--text-muted)] text-sm leading-relaxed">{t("empty")}</p>
      </div>
    </aside>
  );
}
