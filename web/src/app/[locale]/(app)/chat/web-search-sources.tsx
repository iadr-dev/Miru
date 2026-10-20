"use client";

import { GlobeIcon } from "@phosphor-icons/react";
import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
import { useTranslations } from "next-intl";
import { type ReactNode, useState } from "react";
import { cn } from "@/lib/cn";
import { sourceLogoUrl } from "@/lib/source-logo";
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
export function WebSearchSources({ sources }: Readonly<{ sources: WebSearchSource[] }>): ReactNode {
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

function SourceMenuItem({ source }: Readonly<{ source: WebSearchSource }>): ReactNode {
  return (
    <DropdownMenu.Item asChild>
      <a
        href={source.url}
        target="_blank"
        rel="noopener noreferrer"
        aria-label={source.title}
        className={cn(
          "flex min-h-11 items-center gap-2 rounded-[var(--radius-button)] px-2.5 py-1.5",
          "outline-none transition-colors data-[highlighted]:bg-[var(--surface-2)]",
        )}
      >
        <SourceLogo url={source.url} />
        <span className="flex min-w-0 flex-col justify-center gap-0.5">
          <span className="truncate text-[13px] text-[var(--accent-text)] underline underline-offset-2">
            {source.title}
          </span>
          <span className="truncate text-[11px] text-[var(--text-muted)]">{source.url}</span>
        </span>
      </a>
    </DropdownMenu.Item>
  );
}

/** The source site's logo — a pre-downloaded local file for known finance/news domains, a live
 * favicon-service fetch for anything else (`sourceLogoUrl`'s own docstring). Falls back to the
 * generic globe glyph on a load failure (unreachable favicon service, unparseable URL, 404) —
 * never a broken-image icon. */
function SourceLogo({ url }: Readonly<{ url: string }>): ReactNode {
  const logoUrl = sourceLogoUrl(url);
  const [failed, setFailed] = useState(false);

  if (!logoUrl || failed) {
    return (
      <span className="flex size-5 shrink-0 items-center justify-center text-[var(--text-muted)]">
        <GlobeIcon size={14} weight="regular" aria-hidden />
      </span>
    );
  }

  return (
    // A favicon can come from an arbitrary domain `web_search` returns — `next/image` requires
    // a static `remotePatterns` allowlist, which a dynamic per-source domain can't satisfy, so a
    // plain `img` is the correct tool here (not a lint-worthy shortcut).
    // biome-ignore lint/performance/noImgElement: dynamic per-source domain, remotePatterns can't allowlist it
    <img
      src={logoUrl}
      alt=""
      width={20}
      height={20}
      className="size-5 shrink-0 rounded-[4px] object-contain"
      onError={() => setFailed(true)}
    />
  );
}
