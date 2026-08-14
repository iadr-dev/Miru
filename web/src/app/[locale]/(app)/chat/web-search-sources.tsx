"use client";

import { GlobeIcon } from "@phosphor-icons/react";
import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
import { useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { cn } from "@/lib/cn";
import type { WebSearchSource } from "@/types/sse";

/**
 * Grok-style "N sources" pill, shown on an assistant answer that used native web search
 * (`web_search.result` SSE frames accumulated in `turn.webSearchSources`). Expands into a list of
 * the actual sources consulted — same `DropdownMenu` pattern as `table-actions.tsx`'s
 * download-format menu (a list-in-a-popover fits better here than `Collapsible`, since this is a
 * small fixed set of links, not an inline-growing section of the page).
 *
 * Supplementary metadata alongside the answer, not itself the "final answer" — the pill and its
 * list stay `aria-live="off"`; only the answer container carries `aria-live="polite"`
 * (`ActiveTurnView`/`PersistedThreadView` own that region, this component never adds a second
 * one). External links reuse `answer-markdown.tsx`'s external-link convention (`target="_blank"
 * rel="noopener noreferrer"`, accent underline) rather than inventing new link styling.
 */
export function WebSearchSources({ sources }: { sources: WebSearchSource[] }): ReactNode {
  const t = useTranslations("chat.turn.webSearch");

  if (sources.length === 0) {
    return null;
  }

  return (
    <div className="mt-2" aria-live="off">
      <DropdownMenu.Root>
        <DropdownMenu.Trigger asChild>
          <button
            type="button"
            aria-label={t("sourcesCount", { count: sources.length })}
            className={cn(
              "inline-flex min-h-7 items-center gap-1.5 rounded-[var(--radius-pill)] border",
              "border-[var(--border)] bg-[var(--surface-2)] px-2.5 text-[12px] text-[var(--text-muted)]",
              "transition-colors hover:text-[var(--text)]",
              "!outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]",
              "data-[state=open]:bg-[var(--surface)] data-[state=open]:text-[var(--text)]",
            )}
          >
            <GlobeIcon size={13} weight="regular" aria-hidden />
            {t("sourcesCount", { count: sources.length })}
          </button>
        </DropdownMenu.Trigger>
        <DropdownMenu.Portal>
          <DropdownMenu.Content
            side="bottom"
            align="start"
            sideOffset={6}
            className={cn(
              "z-50 w-72 max-w-[90vw] rounded-[var(--radius-card)] border border-[var(--border)]",
              "bg-[var(--surface)] p-1.5 shadow-2xl",
            )}
          >
            {sources.map((source, index) => {
              // Sources are append-only and never reordered/deduped (see the store docstring) —
              // the same URL can legitimately appear twice (different lanes), so the index is
              // the only stable, unique key.
              // biome-ignore lint/suspicious/noArrayIndexKey: see comment above
              return <SourceMenuItem key={index} source={source} />;
            })}
          </DropdownMenu.Content>
        </DropdownMenu.Portal>
      </DropdownMenu.Root>
    </div>
  );
}

function SourceMenuItem({ source }: { source: WebSearchSource }): ReactNode {
  return (
    <DropdownMenu.Item asChild>
      <a
        href={source.url}
        target="_blank"
        rel="noopener noreferrer"
        aria-label={source.title}
        className={cn(
          "flex min-h-11 flex-col justify-center gap-0.5 rounded-[var(--radius-button)] px-2.5 py-1.5",
          "outline-none transition-colors data-[highlighted]:bg-[var(--surface-2)]",
        )}
      >
        <span className="truncate text-[13px] text-[var(--accent-text)] underline underline-offset-2">
          {source.title}
        </span>
        <span className="truncate text-[11px] text-[var(--text-muted)]">{source.url}</span>
      </a>
    </DropdownMenu.Item>
  );
}
