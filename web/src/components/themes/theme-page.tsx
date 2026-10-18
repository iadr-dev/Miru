"use client";

import { ArrowLeftIcon, TagIcon } from "@phosphor-icons/react";
import { useTranslations } from "next-intl";
import { type ReactNode, useMemo, useState } from "react";
import { Card } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { FeatureLock } from "@/components/ui/feature-lock";
import { QueryFailure } from "@/components/ui/query-state";
import { SignedChange } from "@/components/ui/signed-change";
import { Skeleton } from "@/components/ui/skeleton";
import { type SymbolQuote, useSymbolQuotes } from "@/hooks/use-symbol-quotes";
import { useThemeMembers, useThemes } from "@/hooks/use-themes";
import { Link } from "@/i18n/navigation";
import { cn } from "@/lib/cn";
import type { Market } from "@/types/settings";
import type { ThemeMember, ThemeSummary } from "@/types/theme";

const CHAIN_COLUMNS = ["upstream", "midstream", "downstream"] as const;
type ChainColumn = (typeof CHAIN_COLUMNS)[number];

/** Categorical tokens only: a chain position is a label, never a price move. */
const COLUMN_COLOR: Record<ChainColumn, string> = {
  upstream: "var(--chart-1)",
  midstream: "var(--chart-3)",
  downstream: "var(--chart-4)",
};

/** Cards shown per column (and in the 相關公司 strip) before "show all", which also bounds how many
 * symbols are sent for quotes. */
const COLLAPSED_COUNT = 12;

type Board = {
  readonly columns: Record<ChainColumn, readonly ThemeMember[]>;
  readonly related: readonly ThemeMember[];
};

function splitBoard(members: readonly ThemeMember[]): Board {
  const columns: Record<ChainColumn, ThemeMember[]> = {
    upstream: [],
    midstream: [],
    downstream: [],
  };
  const related: ThemeMember[] = [];
  for (const member of members) {
    if (member.position === null || member.position === "related") {
      related.push(member);
    } else {
      columns[member.position].push(member);
    }
  }
  return { columns, related };
}

function marketOf(member: ThemeMember): Market {
  return member.market === "US" ? "US" : "TW";
}

/** A concept theme: what it is, the themes next to it, and its members laid out along the chain
 * (上游 → 中游 → 下游) with the latest daily move beside each, plus the companies that belong to
 * the theme without a place on the chain. */
export function ThemePage({ themeKey }: { readonly themeKey: string }): ReactNode {
  const t = useTranslations("themes.page");
  const tLock = useTranslations("featureLock");
  const membersQuery = useThemeMembers(themeKey);
  const themesQuery = useThemes("concept");
  const [expanded, setExpanded] = useState(false);

  const members = membersQuery.data?.members;
  const board = useMemo(() => splitBoard(members ?? []), [members]);
  const limit = expanded ? Number.POSITIVE_INFINITY : COLLAPSED_COUNT;
  const visible = useMemo(() => {
    const lists = [...CHAIN_COLUMNS.map((column) => board.columns[column]), board.related];
    return lists.flatMap((list) => list.slice(0, limit));
  }, [board, limit]);
  const codesIn = (market: Market): string[] => [
    ...new Set(visible.filter((member) => marketOf(member) === market).map((m) => m.symbol)),
  ];
  const twQuotes = useSymbolQuotes(codesIn("TW"), "TW");
  const usQuotes = useSymbolQuotes(codesIn("US"), "US");

  if (membersQuery.isLoading) {
    return <ThemePageSkeleton />;
  }
  if (membersQuery.isError) {
    return <QueryFailure query={membersQuery} />;
  }
  const data = membersQuery.data;
  if (!data || !data.found) {
    return (
      <Card as="section">
        <EmptyState
          icon={<TagIcon size={20} weight="regular" />}
          title={t("notFoundTitle")}
          description={t("notFoundBody")}
          action={<BackLink />}
        />
      </Card>
    );
  }

  const summaries = themesQuery.data?.themes ?? [];
  const summary = summaries.find((theme) => theme.key === themeKey);
  const description = data.description || summary?.description || "";
  const relatedKeys =
    data.related_keys.length > 0 ? data.related_keys : (summary?.related_keys ?? []);
  const hasChain = CHAIN_COLUMNS.some((column) => board.columns[column].length > 0);
  const quoteFor = (member: ThemeMember): SymbolQuote | undefined =>
    (marketOf(member) === "TW" ? twQuotes : usQuotes).quotes.find((q) => q.code === member.symbol);
  const quotesPending = twQuotes.isLoading || usQuotes.isLoading;
  const hiddenCount = data.members.length - visible.length;
  const canToggle = expanded || hiddenCount > 0;

  return (
    <div className="@container flex flex-col gap-4">
      <BackLink />
      <header className="flex flex-col gap-2">
        <h1 className="break-words font-semibold text-[22px] text-[var(--text)] leading-tight">
          {data.name_zh}
          {data.name_en ? (
            <span className="ml-2 font-normal text-[14px] text-[var(--text-muted)]">
              {data.name_en}
            </span>
          ) : null}
        </h1>
        {description ? (
          <p className="max-w-prose text-[14px] text-[var(--text-muted)] leading-relaxed">
            {description}
          </p>
        ) : null}
        <p className="font-num text-[12px] text-[var(--text-muted)] tabular-nums">
          {t("memberCount", { count: data.member_count })}
        </p>
        <RelatedThemes keys={relatedKeys} summaries={summaries} />
      </header>

      {data.members.length === 0 ? (
        <Card as="section">
          <EmptyState compact title={t("emptyTitle")} description={t("emptyBody")} />
        </Card>
      ) : (
        <>
          {hasChain ? (
            <section aria-label={t("boardAria")} className="flex flex-col gap-2">
              <h2 className="font-semibold text-[15px] text-[var(--text)]">{t("boardTitle")}</h2>
              <div className="grid grid-cols-1 gap-3 @2xl:grid-cols-3">
                {CHAIN_COLUMNS.map((column) => (
                  <BoardColumn
                    key={column}
                    column={column}
                    members={board.columns[column].slice(0, limit)}
                    quoteFor={quoteFor}
                    pending={quotesPending}
                  />
                ))}
              </div>
            </section>
          ) : null}
          {board.related.length > 0 ? (
            <section aria-label={t("relatedCompanies")} className="flex flex-col gap-2">
              <h2 className="font-semibold text-[15px] text-[var(--text)]">
                {t("relatedCompanies")}
              </h2>
              <ul className="grid grid-cols-1 gap-2 @md:grid-cols-2 @3xl:grid-cols-3">
                {board.related.slice(0, limit).map((member) => (
                  <li key={`${member.market}-${member.symbol}`} className="min-w-0">
                    <MemberCard member={member} quote={quoteFor(member)} pending={quotesPending} />
                  </li>
                ))}
              </ul>
            </section>
          ) : null}
          {data.locked && data.hidden_count > 0 ? (
            <FeatureLock
              message={tLock("themePage.more", { count: data.hidden_count })}
              benefit={tLock("themePage.benefit")}
            />
          ) : null}
          {canToggle ? (
            <button
              type="button"
              aria-expanded={expanded}
              onClick={() => setExpanded((current) => !current)}
              className="min-h-11 w-fit font-medium text-[13px] text-[var(--accent-text)] hover:underline focus-visible:outline-2 focus-visible:outline-[var(--accent)]"
            >
              {expanded ? t("showFewer") : t("showAll", { count: hiddenCount })}
            </button>
          ) : null}
        </>
      )}
      <p className="text-[11.5px] text-[var(--text-muted)] leading-relaxed">{t("quoteNote")}</p>
    </div>
  );
}

function BackLink(): ReactNode {
  const t = useTranslations("themes.page");
  return (
    <Link
      href="/market"
      className="inline-flex min-h-11 w-fit items-center gap-1.5 text-[13px] text-[var(--accent-text)] hover:underline focus-visible:outline-2 focus-visible:outline-[var(--accent)]"
    >
      <ArrowLeftIcon size={14} weight="regular" aria-hidden />
      {t("backToThemes")}
    </Link>
  );
}

function RelatedThemes({
  keys,
  summaries,
}: {
  readonly keys: readonly string[];
  readonly summaries: readonly ThemeSummary[];
}): ReactNode {
  const t = useTranslations("themes.page");
  if (keys.length === 0) {
    return null;
  }
  return (
    <div className="flex flex-wrap items-center gap-x-2 gap-y-1.5">
      <span className="text-[12px] text-[var(--text-muted)]">{t("relatedTitle")}</span>
      <ul className="flex flex-wrap gap-1.5">
        {keys.map((key) => (
          <li key={key}>
            <Link
              href={`/themes/${encodeURIComponent(key)}`}
              className="inline-flex min-h-11 items-center rounded-[var(--radius-pill)] border border-[var(--border)] px-3 text-[13px] text-[var(--text-muted)] transition-colors hover:text-[var(--text)] focus-visible:outline-2 focus-visible:outline-[var(--accent)]"
            >
              {summaries.find((theme) => theme.key === key)?.name_zh ?? key}
            </Link>
          </li>
        ))}
      </ul>
    </div>
  );
}

function BoardColumn({
  column,
  members,
  quoteFor,
  pending,
}: {
  readonly column: ChainColumn;
  readonly members: readonly ThemeMember[];
  readonly quoteFor: (member: ThemeMember) => SymbolQuote | undefined;
  readonly pending: boolean;
}): ReactNode {
  const t = useTranslations("themes.page");
  return (
    <section
      aria-label={t(`position.${column}` as const)}
      className="surface-card flex min-w-0 flex-col gap-2 border-t-2 p-3"
      style={{ borderTopColor: COLUMN_COLOR[column] }}
    >
      <h3 className="font-semibold text-[13px] text-[var(--text)]">
        {t(`position.${column}` as const)}
      </h3>
      {members.length === 0 ? (
        <p className="py-2 text-[12px] text-[var(--text-muted)]">{t("emptyColumn")}</p>
      ) : (
        <ul className="flex flex-col gap-2">
          {members.map((member) => (
            <li key={`${member.market}-${member.symbol}`} className="min-w-0">
              <MemberCard member={member} quote={quoteFor(member)} pending={pending} />
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function MemberCard({
  member,
  quote,
  pending,
}: {
  readonly member: ThemeMember;
  readonly quote: SymbolQuote | undefined;
  readonly pending: boolean;
}): ReactNode {
  const t = useTranslations("themes.page");
  const name = quote?.name ?? member.symbol;
  return (
    <Link
      href={`/stock/${encodeURIComponent(member.symbol)}?market=${marketOf(member)}`}
      className={cn(
        "flex min-h-11 items-center justify-between gap-3 rounded-[var(--radius-input)] border border-[var(--border)]",
        "bg-[var(--surface-2)] px-3 py-2 transition-colors hover:border-[var(--border-strong)]",
        "focus-visible:outline-2 focus-visible:outline-[var(--accent)]",
      )}
    >
      <span className="min-w-0">
        <span className="block truncate font-medium text-[13px] text-[var(--text)]">{name}</span>
        <span className="block font-num text-[11px] text-[var(--text-muted)] tabular-nums">
          {member.symbol}
        </span>
      </span>
      {pending ? (
        <Skeleton className="h-4 w-14 shrink-0" />
      ) : (
        <SignedChange
          value={quote?.changePct}
          decimals={2}
          unit="%"
          className="shrink-0 text-[13px]"
        />
      )}
    </Link>
  );
}

function ThemePageSkeleton(): ReactNode {
  return (
    <div className="flex flex-col gap-4" aria-hidden>
      <Skeleton className="h-7 w-48" />
      <Skeleton className="h-4 w-full max-w-prose" />
      <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
        {[0, 1, 2].map((n) => (
          <Skeleton key={n} className="h-56 rounded-[var(--radius-card)]" />
        ))}
      </div>
    </div>
  );
}
