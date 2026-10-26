"use client";

import { MagnifyingGlassIcon } from "@phosphor-icons/react/dist/ssr";
import { useTranslations } from "next-intl";
import { type ReactNode, useMemo, useState } from "react";
import { ScreenerLoading } from "@/components/screener/screener-loading";
import { ScreenerTable } from "@/components/screener/screener-table";
import { useThemeMembers, useThemes } from "@/hooks/use-themes";
import { Link } from "@/i18n/navigation";
import { hashToChartToken } from "@/lib/chart-token-hash";
import { cn } from "@/lib/cn";
import type { ThemeKind, ThemeMember, ThemeSummary } from "@/types/theme";

const ALL_KIND = "__all__";

type KindFilter = typeof ALL_KIND | ThemeKind;

function matchesKind(theme: ThemeSummary, kindFilter: string): boolean {
  return kindFilter === ALL_KIND || theme.kind === kindFilter;
}

function matchesSearch(theme: ThemeSummary, search: string): boolean {
  if (search.trim().length === 0) return true;
  return (
    theme.name_zh.includes(search) || theme.name_en.toLowerCase().includes(search.toLowerCase())
  );
}

function countSubIndustries(members: readonly ThemeMember[]): [string, number][] {
  const counts = new Map<string, number>();
  for (const member of members) {
    if (!member.sub_industry) continue;
    counts.set(member.sub_industry, (counts.get(member.sub_industry) ?? 0) + 1);
  }
  return Array.from(counts.entries());
}

function KindTabs({
  kindFilter,
  onChange,
}: Readonly<{
  kindFilter: string;
  onChange: (key: KindFilter) => void;
}>): ReactNode {
  const t = useTranslations("screener.themes");
  return (
    <div role="tablist" className="flex gap-1.5">
      {(
        [
          [ALL_KIND, t("allKinds")],
          ["industry", t("kindIndustry")],
          ["concept", t("kindConcept")],
        ] as const
      ).map(([key, label]) => (
        <button
          key={key}
          type="button"
          role="tab"
          aria-selected={kindFilter === key}
          onClick={() => onChange(key)}
          className={cn(
            "min-h-9 rounded-[var(--radius-pill)] px-3 text-[12px] transition-colors",
            kindFilter === key
              ? "bg-[var(--accent-weak)] text-[var(--accent-text)]"
              : "border border-[var(--border)] text-[var(--text-muted)] hover:text-[var(--text)]",
          )}
        >
          {label}
        </button>
      ))}
    </div>
  );
}

function ThemeChip({
  theme,
  active,
  onSelect,
}: Readonly<{
  theme: ThemeSummary;
  active: boolean;
  onSelect: (key: string) => void;
}>): ReactNode {
  const token = hashToChartToken(theme.name_zh);
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={() => onSelect(theme.key)}
      className={cn(
        "flex min-h-11 items-center gap-1.5 rounded-[var(--radius-pill)] border px-3 font-medium text-[13px] transition-colors",
        active
          ? "border-[var(--accent)] bg-[var(--accent-weak)] text-[var(--text)]"
          : "border-[var(--border)] text-[var(--text-muted)] hover:text-[var(--text)]",
      )}
    >
      <span
        className="size-1.5 rounded-full"
        style={{ backgroundColor: token }}
        aria-hidden="true"
      />
      {theme.name_zh}
      <span className="font-num text-[11px] tabular-nums opacity-70">{theme.member_count}</span>
    </button>
  );
}

function SubIndustryButton({
  selected,
  onClick,
  children,
}: Readonly<{
  selected: boolean;
  onClick: () => void;
  children: ReactNode;
}>): ReactNode {
  return (
    <button
      type="button"
      aria-pressed={selected}
      onClick={onClick}
      className={cn(
        "min-h-8 rounded-[var(--radius-pill)] px-2.5 text-[12px] transition-colors",
        selected
          ? "bg-[var(--accent-weak)] text-[var(--accent)]"
          : "border border-[var(--border)] text-[var(--text-muted)] hover:text-[var(--text)]",
      )}
    >
      {children}
    </button>
  );
}

function SubIndustryFilter({
  subIndustries,
  value,
  onChange,
}: Readonly<{
  subIndustries: [string, number][];
  value: string | null;
  onChange: (next: string | null) => void;
}>): ReactNode {
  const t = useTranslations("screener.themes");
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <span className="text-[12px] text-[var(--text-muted)]">{t("subIndustry")}</span>
      <SubIndustryButton selected={value === null} onClick={() => onChange(null)}>
        {t("allSubIndustries")}
      </SubIndustryButton>
      {subIndustries.map(([name, count]) => (
        <SubIndustryButton key={name} selected={value === name} onClick={() => onChange(name)}>
          {name}
          <span className="ml-1 font-num tabular-nums opacity-70">{count}</span>
        </SubIndustryButton>
      ))}
    </div>
  );
}

/** 主題 Themes sub-tab (README.md §6): every theme as a chip (`get_themes`), grouped by
 * 產業鏈/概念題材 kind since the two have very different provenance (`ThemeSource` — editorial
 * industry-chain vs. AI-researched concept, confirmed real per `theme_tools.py`'s own docstring)
 * and shouldn't read as interchangeable. Selecting a theme loads its members as a pre-filtered,
 * read-only screen via `get_theme_members` — "theme members loaded as a screen", never a scored/
 * ranked factor (a theme is a tag). No gates/conditions apply to this view — it is a plain
 * membership lookup, not a `run_screener` pass.
 *
 * Members render through the SAME `ScreenerTable` the TW screen uses, so 股價/漲跌%/成交量 are
 * real (`useSymbolQuotes`, not fabricated) — the reference this was redesigned against also
 * showed a 量化評分/籌碼集中度/技術指標 column set that has no backing data anywhere in this
 * codebase's theme store (`ThemeMember` is pure classification: symbol + sub_industry +
 * confidence + source, no price/score fields at all) — those columns are deliberately not
 * reproduced rather than shown with fabricated numbers. A sub-industry filter row IS real (every
 * member's own `sub_industry` field) and is reproduced here. */
export function ThemeCards(): ReactNode {
  const t = useTranslations("screener.themes");
  const themesQuery = useThemes();
  const [kindFilter, setKindFilter] = useState<string>(ALL_KIND);
  const [activeKey, setActiveKey] = useState<string | null>(null);
  const [subIndustryFilter, setSubIndustryFilter] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const membersQuery = useThemeMembers(activeKey);

  const themes = themesQuery.data?.themes ?? [];
  const filteredThemes = useMemo(
    () => themes.filter((theme) => matchesKind(theme, kindFilter) && matchesSearch(theme, search)),
    [themes, kindFilter, search],
  );

  const subIndustries = useMemo(
    () => countSubIndustries(membersQuery.data?.members ?? []),
    [membersQuery.data?.members],
  );

  const visibleMembers = useMemo(() => {
    const members = membersQuery.data?.members ?? [];
    if (!subIndustryFilter) return members;
    return members.filter((member) => member.sub_industry === subIndustryFilter);
  }, [membersQuery.data?.members, subIndustryFilter]);

  function selectTheme(key: string): void {
    setActiveKey((current) => (current === key ? null : key));
    setSubIndustryFilter(null);
  }

  if (themesQuery.isLoading) return <ScreenerLoading />;
  if (themesQuery.isError || !themesQuery.data) {
    return <p className="p-6 text-[13px] text-[var(--text-muted)]">{t("error")}</p>;
  }

  return (
    <div className="flex flex-col gap-3 p-4">
      <div className="flex items-center justify-between gap-3">
        <KindTabs kindFilter={kindFilter} onChange={setKindFilter} />
        <div className="relative">
          <MagnifyingGlassIcon
            size={14}
            weight="regular"
            className="pointer-events-none absolute inset-y-0 left-2.5 my-auto text-[var(--text-muted)]"
          />
          <input
            type="search"
            name="theme_search"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder={t("search")}
            aria-label={t("search")}
            className="min-h-11 w-48 rounded-[var(--radius-pill)] border border-[var(--border)] bg-[var(--surface)] pr-3 pl-8 text-[var(--text)] text-sm focus-visible:outline-2 focus-visible:outline-[var(--accent)]"
          />
        </div>
      </div>

      <div className="flex flex-wrap gap-2">
        {filteredThemes.map((theme) => (
          <ThemeChip
            key={theme.key}
            theme={theme}
            active={theme.key === activeKey}
            onSelect={selectTheme}
          />
        ))}
        {filteredThemes.length === 0 ? (
          <p className="p-2 text-[13px] text-[var(--text-muted)]">{t("noThemesMatch")}</p>
        ) : null}
      </div>

      {activeKey && membersQuery.isLoading ? <ScreenerLoading /> : null}
      {activeKey && membersQuery.data?.found ? (
        <div className="flex flex-col gap-2">
          {membersQuery.data.kind === "concept" ? (
            <Link
              href={`/themes/${encodeURIComponent(activeKey)}`}
              className="min-h-11 w-fit self-start py-3 font-medium text-[13px] text-[var(--accent-text)] hover:underline focus-visible:outline-2 focus-visible:outline-[var(--accent)]"
            >
              {t("viewThemePage")}
            </Link>
          ) : null}
          {subIndustries.length > 0 ? (
            <SubIndustryFilter
              subIndustries={subIndustries}
              value={subIndustryFilter}
              onChange={setSubIndustryFilter}
            />
          ) : null}
          <ScreenerTable
            survivors={visibleMembers.map((member) => ({
              symbol: member.symbol,
              matched_conditions: member.sub_industry ? [member.sub_industry] : [],
              display: {},
            }))}
            columns={[]}
            market="TW"
          />
        </div>
      ) : null}
      {activeKey && membersQuery.data && !membersQuery.data.found ? (
        <p className="p-2 text-[13px] text-[var(--text-muted)]">{t("error")}</p>
      ) : null}
    </div>
  );
}
