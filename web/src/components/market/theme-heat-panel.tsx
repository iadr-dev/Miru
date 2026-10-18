"use client";

import { MagnifyingGlassIcon } from "@phosphor-icons/react";
import { useTranslations } from "next-intl";
import { type ReactNode, useId, useMemo, useState } from "react";
import { Card, CardHeader } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { QueryState } from "@/components/ui/query-state";
import { SignedChange } from "@/components/ui/signed-change";
import { Skeleton } from "@/components/ui/skeleton";
import { useSymbolQuotes } from "@/hooks/use-symbol-quotes";
import { useThemeMembers, useThemes } from "@/hooks/use-themes";
import { Link } from "@/i18n/navigation";
import {
  matchesThemeQuery,
  sampleMemberSymbols,
  THEME_HEAT_SAMPLE,
  themeHeat,
} from "@/lib/theme-heat";
import type { ThemeSummary } from "@/types/theme";

/** 題材: every concept theme as a card with its members' average move. The tab is Taiwan-only,
 * like the theme chips on the screener. */
export function ThemeHeatPanel(): ReactNode {
  const t = useTranslations("themes.tab");
  const query = useThemes("concept");
  const [search, setSearch] = useState("");
  const searchId = useId();

  return (
    <Card as="section" className="@container flex flex-col gap-4">
      <CardHeader
        title={t("title")}
        description={t("hint", { sample: THEME_HEAT_SAMPLE })}
        className="mb-0"
      />
      <div className="relative max-w-sm">
        <label htmlFor={searchId} className="sr-only">
          {t("searchLabel")}
        </label>
        <MagnifyingGlassIcon
          size={14}
          weight="regular"
          aria-hidden
          className="pointer-events-none absolute inset-y-0 left-2.5 my-auto text-[var(--text-muted)]"
        />
        <input
          id={searchId}
          type="search"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          placeholder={t("searchPlaceholder")}
          className="min-h-11 w-full rounded-[var(--radius-pill)] border border-[var(--border)] bg-[var(--surface)] pr-3 pl-8 text-[var(--text)] text-sm focus-visible:outline-2 focus-visible:outline-[var(--accent)]"
        />
      </div>
      <QueryState query={query} skeleton={<GridSkeleton />}>
        {(data) => <ThemeGrid themes={data.themes} search={search} />}
      </QueryState>
      <p className="text-[11.5px] text-[var(--text-muted)] leading-relaxed">{t("quoteNote")}</p>
    </Card>
  );
}

function ThemeGrid({
  themes,
  search,
}: {
  readonly themes: readonly ThemeSummary[];
  readonly search: string;
}): ReactNode {
  const t = useTranslations("themes.tab");
  const concept = useMemo(() => themes.filter((theme) => theme.kind === "concept"), [themes]);
  const visible = concept.filter((theme) => matchesThemeQuery(theme, search));

  if (concept.length === 0) {
    return <EmptyState compact title={t("empty")} />;
  }
  if (visible.length === 0) {
    return <EmptyState compact title={t("noMatch")} />;
  }
  return (
    <ul className="grid grid-cols-1 gap-3 @xl:grid-cols-2 @4xl:grid-cols-3">
      {visible.map((theme) => (
        <li key={theme.key} className="min-w-0">
          <ThemeHeatCard theme={theme} />
        </li>
      ))}
    </ul>
  );
}

function GridSkeleton(): ReactNode {
  return (
    <div className="grid grid-cols-1 gap-3 @xl:grid-cols-2 @4xl:grid-cols-3" aria-hidden>
      {[0, 1, 2, 3, 4, 5].map((n) => (
        <Skeleton key={n} className="h-32 rounded-[var(--radius-card)]" />
      ))}
    </div>
  );
}

function ThemeHeatCard({ theme }: { readonly theme: ThemeSummary }): ReactNode {
  const t = useTranslations("themes.tab");
  const members = useThemeMembers(theme.key);
  const symbols = useMemo(() => sampleMemberSymbols(members.data?.members ?? []), [members.data]);
  const quotes = useSymbolQuotes(symbols, "TW");
  const heat = themeHeat(quotes.quotes.map((quote) => quote.changePct));
  const pending = members.isLoading || quotes.isLoading;

  return (
    <Link
      href={`/themes/${encodeURIComponent(theme.key)}`}
      className="surface-card-interactive flex h-full min-h-32 flex-col gap-2 p-4 focus-visible:outline-2 focus-visible:outline-[var(--accent)]"
    >
      <div className="flex items-start justify-between gap-3">
        <h3 className="min-w-0 break-words font-semibold text-[15px] text-[var(--text)]">
          {theme.name_zh}
        </h3>
        <span className="shrink-0 whitespace-nowrap text-[12px] text-[var(--text-muted)]">
          {t("members", { count: theme.member_count })}
        </span>
      </div>
      <p className="line-clamp-2 min-h-[2lh] text-[13px] text-[var(--text-muted)] leading-relaxed">
        {theme.description}
      </p>
      <div className="mt-auto flex items-baseline justify-between gap-3 pt-1">
        <span className="text-[12px] text-[var(--text-muted)]">{t("avgLabel")}</span>
        {pending ? (
          <Skeleton className="h-5 w-16" />
        ) : (
          <span className="flex items-baseline gap-2">
            <SignedChange value={heat.average} decimals={2} unit="%" className="text-[15px]" />
            <span className="text-[11px] text-[var(--text-muted)]">
              {heat.average === null
                ? t("avgUnavailable")
                : t("avgSample", { count: heat.quoted })}
            </span>
          </span>
        )}
      </div>
    </Link>
  );
}
