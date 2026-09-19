"use client";

import { useQuery } from "@tanstack/react-query";
import { useLocale, useTranslations } from "next-intl";
import { type ReactNode, useMemo, useState } from "react";
import { formatCompactNumber, formatNumber } from "@/app/[locale]/(app)/chat/cards/card-format";
import { CardSkeletonBody } from "@/components/market/card-skeleton";
import { SponsorTeaser } from "@/components/market/sponsor-teaser";
import { ExpandModal } from "@/components/ui/expand-modal";
import { MonthGridCalendar } from "@/components/ui/month-grid-calendar";
import {
  getCompanyProfile,
  getDispositionSecurities,
  getEsgScore,
  getInsiderShareholding,
  getStockCalendarEvents,
  getStockInsiderTransferDeclarations,
  getStockMaterialAnnouncements,
  getSymbolThemes,
} from "@/lib/api/stock-detail";
import { hashToChartToken } from "@/lib/chart-token-hash";
import { cn } from "@/lib/cn";
import { isMaskedTeaser } from "@/types/market";
import type { Market } from "@/types/settings";
import type {
  CalendarEventOut,
  InsiderShareholdingResponse,
  InsiderTransferDeclarationRow,
  MaterialAnnouncement,
} from "@/types/stock-detail";

const ANNOUNCEMENTS_COLLAPSED_ROW_COUNT = 5;
const INSIDER_COLLAPSED_ROW_COUNT = 5;

// `get_calendar_events` composes real feed-sourced dividend_ex_rights (除權息)/
// cash_capital_increase_rights (現增認購)/agm (股東會) rows today — other `EventType` values
// (法說會/index_rebalance/macro_release) remain an honestly-disclosed gap with no feed adapter,
// so only these three render here to avoid implying coverage that doesn't exist.
const CALENDAR_EVENT_TYPES_WITH_FEED_DATA = [
  "dividend_ex_rights",
  "cash_capital_increase_rights",
  "agm",
] as const;

const CALENDAR_EVENT_DOT_COLOR: Record<string, string> = {
  dividend_ex_rights: "var(--chart-1)",
  cash_capital_increase_rights: "var(--chart-3)",
  agm: "var(--chart-4)",
};

function isoDaysFromToday(days: number): string {
  const d = new Date();
  d.setDate(d.getDate() + days);
  return d.toISOString().slice(0, 10);
}

/**
 * 基本資料 tab — Phase-1 reduced scope (see the approved plan): 個股資訊卡/財務快照
 * (`get_company_profile`, split into a stat-row + a quieter identity list), 概念股 chips
 * (`get_symbol_themes` — real, previously unused anywhere on the page), 內部人/董監持股·質押比
 * (`get_insider_shareholding`, capped + `ExpandModal`), 公告/事件 stream
 * (`get_material_announcements`, capped + `ExpandModal`), ESG風險卡 (`get_esg_score`, hidden
 * entirely when null — coverage-gated, never an empty scaffold), 處置/注意 banner
 * (`get_disposition_securities`, Sponsor-tier, no forecast field on this tool so no "最快N日"
 * copy). Dropped for this phase: 集團/轉投資/大股東, 被N支ETF持有, 相似股票/相關動能,
 * 觀察這支股票的人也看這些, 產銷組合 — none of `get_company_relationships`/
 * `get_etfs_holding_stock`/a correlation-coefficient tool/cross-user watchlist analytics/a
 * product-mix capability exist yet (confirmed via grep, not assumed).
 */
export function BasicInfoPanel({
  symbol,
  market,
}: {
  readonly symbol: string;
  readonly market: Market;
}): ReactNode {
  return (
    <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
      <DispositionBanner symbol={symbol} market={market} />
      <ProfileCard symbol={symbol} market={market} />
      <ThemeChipsCard symbol={symbol} market={market} />
      <InsiderShareholdingCard symbol={symbol} market={market} />
      <AnnouncementsCard symbol={symbol} market={market} />
      <EsgCard symbol={symbol} market={market} />
    </div>
  );
}

function ProfileCard({
  symbol,
  market,
}: {
  readonly symbol: string;
  readonly market: Market;
}): ReactNode {
  const t = useTranslations("stock.basicInfo");
  const locale = useLocale();
  const query = useQuery({
    queryKey: ["company_profile", symbol, market],
    queryFn: () => getCompanyProfile(symbol, market),
  });

  if (query.isLoading) {
    return (
      <section className="rounded-[var(--radius-card)] border border-[var(--border)] bg-[var(--surface)] p-5 xl:col-span-2">
        <CardSkeletonBody height={220} />
      </section>
    );
  }
  if (!query.data) return null;
  const p = query.data;

  // Financial-snapshot stats — the numbers a reader scans first, given equal visual weight as a
  // stat row (bigger figures, top of the card). Distinct from the identity fields below, which
  // are reference facts (who/where), not something that moves day to day. Each stat gets its own
  // `--chart-1..6` hue (categorical, not signed) so the row reads as differentiated data points
  // rather than a flat grey list.
  const stats: [string, string, string][] = [
    [t("marketValue"), formatCompactNumber(p.market_value ?? null, locale), "var(--chart-1)"],
    [t("per"), formatNumber(p.per ?? null, locale, { decimals: 1 }), "var(--chart-2)"],
    [
      t("dividendYield"),
      p.dividend_yield != null ? `${p.dividend_yield.toFixed(2)}%` : "—",
      "var(--chart-3)",
    ],
    [t("eps"), formatNumber(p.eps ?? null, locale, { decimals: 2 }), "var(--chart-4)"],
    [
      t("revenueYoy"),
      p.revenue_yoy != null ? `${p.revenue_yoy.toFixed(2)}%` : "—",
      "var(--chart-5)",
    ],
  ];
  const identity: [string, string][] = [
    [t("chairman"), p.chairman ?? "—"],
    [t("auditor"), p.auditor ?? "—"],
    [t("headquarters"), p.headquarters ?? "—"],
    [t("capital"), formatNumber(p.capital ?? null, locale)],
    [t("spokesperson"), p.spokesperson ?? "—"],
    [t("foundingDate"), p.founding_date ?? "—"],
  ];

  return (
    <section className="rounded-[var(--radius-card)] border border-[var(--border)] bg-[var(--surface)] p-5 xl:col-span-2">
      <h2 className="mb-3 font-semibold text-[15px] text-[var(--text)]">{t("profileTitle")}</h2>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-5">
        {stats.map(([label, value, color]) => (
          <div key={label}>
            <p className="text-[11px] text-[var(--text-muted)]">{label}</p>
            <p className="font-num text-[16px] tabular-nums" style={{ color }}>
              {value}
            </p>
          </div>
        ))}
      </div>

      <h3 className="mt-4 mb-1 text-[11px] text-[var(--text-muted)] uppercase tracking-wide">
        {t("identityTitle")}
      </h3>
      <dl className="grid grid-cols-1 divide-y divide-[var(--border)] sm:grid-cols-2 sm:divide-y-0">
        {identity.map(([label, value]) => (
          <div key={label} className="flex items-center justify-between py-1.5 sm:px-2">
            <dt className="text-[12px] text-[var(--text-muted)]">{label}</dt>
            <dd className="text-[13px] text-[var(--text)]">{value}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

function ThemeChipsCard({
  symbol,
  market,
}: {
  readonly symbol: string;
  readonly market: Market;
}): ReactNode {
  const t = useTranslations("stock.basicInfo");
  const query = useQuery({
    queryKey: ["symbol_themes", symbol, market],
    queryFn: () => getSymbolThemes(symbol, market),
  });

  if (query.isLoading) {
    return (
      <section className="rounded-[var(--radius-card)] border border-[var(--border)] bg-[var(--surface)] p-5 xl:col-span-2">
        <CardSkeletonBody height={60} />
      </section>
    );
  }
  const themes = query.data?.themes ?? [];
  if (themes.length === 0) return null;

  return (
    <section className="rounded-[var(--radius-card)] border border-[var(--border)] bg-[var(--surface)] p-5 xl:col-span-2">
      <h2 className="mb-3 font-semibold text-[15px] text-[var(--text)]">{t("themesTitle")}</h2>
      <div className="flex flex-wrap gap-1.5">
        {themes.map((theme) => {
          const token = hashToChartToken(theme.theme_key);
          return (
            <span
              key={theme.theme_key}
              className="rounded-[var(--radius-pill)] px-2.5 py-1 font-medium text-[12px]"
              style={{
                backgroundColor: `${token}26`,
                color: token,
                border: `1px solid ${token}40`,
              }}
            >
              {theme.name_zh}
            </span>
          );
        })}
      </div>
    </section>
  );
}

type InsiderSubTab = "detail" | "transfer";
const INSIDER_TRANSFER_COLLAPSED_ROW_COUNT = 5;

function InsiderShareholdingCard({
  symbol,
  market,
}: {
  readonly symbol: string;
  readonly market: Market;
}): ReactNode {
  const t = useTranslations("stock.basicInfo");
  const [expanded, setExpanded] = useState(false);
  const [subTab, setSubTab] = useState<InsiderSubTab>("detail");

  const holdingsQuery = useQuery({
    queryKey: ["insider_shareholding", symbol, market],
    queryFn: () => getInsiderShareholding(symbol, market),
  });
  const transferQuery = useQuery({
    queryKey: ["stock_insider_transfer_declarations", symbol, market],
    queryFn: () => getStockInsiderTransferDeclarations(symbol, market),
    enabled: subTab === "transfer",
  });

  if (holdingsQuery.isLoading) {
    return (
      <section className="rounded-[var(--radius-card)] border border-[var(--border)] bg-[var(--surface)] p-5">
        <CardSkeletonBody height={200} />
      </section>
    );
  }
  const holdings = holdingsQuery.data?.holdings ?? [];
  if (holdings.length === 0) return null;
  const transferRows = transferQuery.data?.rows ?? [];

  return (
    <section className="rounded-[var(--radius-card)] border border-[var(--border)] bg-[var(--surface)] p-5">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="font-semibold text-[15px] text-[var(--text)]">{t("insiderTitle")}</h2>
        <div className="flex gap-1 rounded-[var(--radius-pill)] bg-[var(--surface-2)] p-0.5">
          <ViewToggleButton
            active={subTab === "detail"}
            label={t("insiderDetailTab")}
            onClick={() => setSubTab("detail")}
          />
          <ViewToggleButton
            active={subTab === "transfer"}
            label={t("insiderTransferTab")}
            onClick={() => setSubTab("transfer")}
          />
        </div>
      </div>

      {subTab === "detail" ? (
        <>
          <InsiderShareholdingList holdings={holdings.slice(0, INSIDER_COLLAPSED_ROW_COUNT)} />
          {holdings.length > INSIDER_COLLAPSED_ROW_COUNT ? (
            <button
              type="button"
              onClick={() => setExpanded(true)}
              className="mt-2.5 flex min-h-11 w-full items-center justify-center gap-1.5 rounded-[var(--radius-input)] bg-[var(--surface-2)] text-[12.5px] text-[var(--text-muted)] transition-colors hover:bg-[var(--surface-3)] hover:text-[var(--text)]"
            >
              {t("expandTo", { count: holdings.length })}
            </button>
          ) : null}
          <ExpandModal
            open={expanded}
            title={t("insiderTitle")}
            closeLabel={t("collapse")}
            onClose={() => setExpanded(false)}
          >
            <InsiderShareholdingList holdings={holdings} />
          </ExpandModal>
        </>
      ) : transferQuery.isLoading ? (
        <CardSkeletonBody height={160} />
      ) : transferRows.length === 0 ? (
        <p className="text-[13px] text-[var(--text-muted)]">{t("insiderTransferEmpty")}</p>
      ) : (
        <>
          <InsiderTransferList rows={transferRows.slice(0, INSIDER_TRANSFER_COLLAPSED_ROW_COUNT)} />
          {transferRows.length > INSIDER_TRANSFER_COLLAPSED_ROW_COUNT ? (
            <button
              type="button"
              onClick={() => setExpanded(true)}
              className="mt-2.5 flex min-h-11 w-full items-center justify-center gap-1.5 rounded-[var(--radius-input)] bg-[var(--surface-2)] text-[12.5px] text-[var(--text-muted)] transition-colors hover:bg-[var(--surface-3)] hover:text-[var(--text)]"
            >
              {t("expandTo", { count: transferRows.length })}
            </button>
          ) : null}
          <ExpandModal
            open={expanded}
            title={t("insiderTransferTab")}
            closeLabel={t("collapse")}
            onClose={() => setExpanded(false)}
          >
            <InsiderTransferList rows={transferRows} />
          </ExpandModal>
        </>
      )}
    </section>
  );
}

function InsiderTransferList({
  rows,
}: {
  readonly rows: readonly InsiderTransferDeclarationRow[];
}): ReactNode {
  const t = useTranslations("stock.basicInfo");
  return (
    <ul className="divide-y divide-[var(--border)]">
      {rows.map((r) => (
        <li key={`${r.person_name}-${r.transfer_window_start}`} className="py-2.5">
          <div className="flex items-center justify-between gap-2">
            <span className="text-[13px] text-[var(--text)]">
              {r.person_name}
              <span className="ml-1.5 text-[11px] text-[var(--text-muted)]">{r.filer_role}</span>
            </span>
            <span className="font-num text-[13px] text-[var(--text)] tabular-nums">
              {r.transfer_shares.toLocaleString()}
            </span>
          </div>
          <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[11px] text-[var(--text-muted)]">
            <span>{r.transfer_method}</span>
            {r.transferee ? <span>{t("insiderTransferee", { name: r.transferee })}</span> : null}
            <span className="font-num tabular-nums">
              {r.transfer_window_start} – {r.transfer_window_end}
            </span>
          </div>
        </li>
      ))}
    </ul>
  );
}

function InsiderShareholdingList({
  holdings,
}: {
  readonly holdings: InsiderShareholdingResponse["holdings"];
}): ReactNode {
  return (
    <ul className="divide-y divide-[var(--border)]">
      {holdings.map((h) => {
        // `pledged_ratio` arrives as an upstream percentage-string (e.g. "21.46%") — parse only
        // to decide the `--warn` threshold, but render the string as-is (never reformatted/
        // rounded away from the source's own precision).
        const pledgeValue = h.pledged_ratio ? Number.parseFloat(h.pledged_ratio) : null;
        const highPledge = pledgeValue !== null && pledgeValue >= 50;
        return (
          <li
            key={`${h.person_name}-${h.title}`}
            className="flex items-center justify-between py-2 text-[13px]"
          >
            <span className="text-[var(--text)]">
              {h.person_name}
              <span className="ml-1.5 text-[11px] text-[var(--text-muted)]">{h.title}</span>
            </span>
            <span
              className={
                highPledge
                  ? "rounded-[var(--radius-pill)] bg-[var(--warn)]/15 px-2 py-0.5 font-num text-[12px] text-[var(--warn)] tabular-nums"
                  : "font-num text-[12px] text-[var(--text-muted)] tabular-nums"
              }
            >
              {h.pledged_ratio ?? "—"}
            </span>
          </li>
        );
      })}
    </ul>
  );
}

type AnnouncementsView = "list" | "calendar";

function AnnouncementsCard({
  symbol,
  market,
}: {
  readonly symbol: string;
  readonly market: Market;
}): ReactNode {
  const t = useTranslations("stock.basicInfo");
  const [expanded, setExpanded] = useState(false);
  const [view, setView] = useState<AnnouncementsView>("list");
  const [calendarCursor, setCalendarCursor] = useState(() => {
    const now = new Date();
    return { year: now.getFullYear(), month: now.getMonth() + 1 };
  });

  const announcementsQuery = useQuery({
    queryKey: ["stock_material_announcements", symbol, market],
    queryFn: () => getStockMaterialAnnouncements(symbol, market),
  });
  // A wide, fixed window (1y back / 2y forward) rather than "today only" — 除權息/現增/股東會
  // rows are sparse and mostly forward-looking, so a narrow window would show nothing.
  const calendarQuery = useQuery({
    queryKey: ["stock_calendar_events", symbol, market],
    queryFn: () =>
      getStockCalendarEvents(symbol, market, {
        startDate: isoDaysFromToday(-365),
        endDate: isoDaysFromToday(730),
      }),
  });

  if (announcementsQuery.isLoading) {
    return (
      <section className="rounded-[var(--radius-card)] border border-[var(--border)] bg-[var(--surface)] p-5">
        <CardSkeletonBody height={200} />
      </section>
    );
  }
  const announcements = announcementsQuery.data?.announcements ?? [];
  const calendarData = calendarQuery.data;
  const calendarLocked = calendarData ? isMaskedTeaser(calendarData) : false;
  const calendarEvents =
    calendarData && !isMaskedTeaser(calendarData)
      ? calendarData.events.filter((e) =>
          CALENDAR_EVENT_TYPES_WITH_FEED_DATA.includes(
            e.type as (typeof CALENDAR_EVENT_TYPES_WITH_FEED_DATA)[number],
          ),
        )
      : [];

  return (
    <section className="rounded-[var(--radius-card)] border border-[var(--border)] bg-[var(--surface)] p-5">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="font-semibold text-[15px] text-[var(--text)]">{t("announcementsTitle")}</h2>
        <div className="flex gap-1 rounded-[var(--radius-pill)] bg-[var(--surface-2)] p-0.5">
          <ViewToggleButton
            active={view === "list"}
            label={t("viewList")}
            onClick={() => setView("list")}
          />
          <ViewToggleButton
            active={view === "calendar"}
            label={t("viewCalendar")}
            onClick={() => setView("calendar")}
          />
        </div>
      </div>

      {view === "list" ? (
        announcements.length === 0 && calendarEvents.length === 0 ? (
          <p className="text-[13px] text-[var(--text-muted)]">{t("announcementsEmpty")}</p>
        ) : (
          <>
            <AnnouncementsList
              announcements={announcements.slice(0, ANNOUNCEMENTS_COLLAPSED_ROW_COUNT)}
              calendarEvents={calendarEvents.slice(0, ANNOUNCEMENTS_COLLAPSED_ROW_COUNT)}
            />
            {announcements.length + calendarEvents.length > ANNOUNCEMENTS_COLLAPSED_ROW_COUNT ? (
              <button
                type="button"
                onClick={() => setExpanded(true)}
                className="mt-2.5 flex min-h-11 w-full items-center justify-center gap-1.5 rounded-[var(--radius-input)] bg-[var(--surface-2)] text-[12.5px] text-[var(--text-muted)] transition-colors hover:bg-[var(--surface-3)] hover:text-[var(--text)]"
              >
                {t("expandTo", { count: announcements.length + calendarEvents.length })}
              </button>
            ) : null}
            <ExpandModal
              open={expanded}
              title={t("announcementsTitle")}
              closeLabel={t("collapse")}
              onClose={() => setExpanded(false)}
            >
              <AnnouncementsList announcements={announcements} calendarEvents={calendarEvents} />
            </ExpandModal>
          </>
        )
      ) : calendarLocked && calendarData && isMaskedTeaser(calendarData) ? (
        <SponsorTeaser
          headline={t("announcementsTitle")}
          preview={calendarData.preview}
          requiredTier={calendarData.required_tier}
        >
          <div className="h-64 rounded-[var(--radius-input)] bg-[var(--surface-2)]" />
        </SponsorTeaser>
      ) : (
        <AnnouncementsCalendarView
          year={calendarCursor.year}
          month={calendarCursor.month}
          announcements={announcements}
          calendarEvents={calendarEvents}
          onPrevMonth={() =>
            setCalendarCursor(({ year, month }) =>
              month === 1 ? { year: year - 1, month: 12 } : { year, month: month - 1 },
            )
          }
          onNextMonth={() =>
            setCalendarCursor(({ year, month }) =>
              month === 12 ? { year: year + 1, month: 1 } : { year, month: month + 1 },
            )
          }
        />
      )}
    </section>
  );
}

function ViewToggleButton({
  active,
  label,
  onClick,
}: {
  readonly active: boolean;
  readonly label: string;
  readonly onClick: () => void;
}): ReactNode {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={cn(
        "rounded-[var(--radius-pill)] px-2.5 py-1 font-medium text-[12px] transition-colors",
        active
          ? "bg-[var(--accent)] text-[color:#06231F]"
          : "text-[var(--text-muted)] hover:text-[var(--text)]",
      )}
    >
      {label}
    </button>
  );
}

function AnnouncementsList({
  announcements,
  calendarEvents,
}: {
  readonly announcements: readonly MaterialAnnouncement[];
  readonly calendarEvents: readonly CalendarEventOut[];
}): ReactNode {
  type Row =
    | { kind: "announcement"; date: string; item: MaterialAnnouncement }
    | { kind: "event"; date: string; item: CalendarEventOut };
  const rows: Row[] = [
    ...announcements.map((a): Row => ({ kind: "announcement", date: a.announced_date, item: a })),
    ...calendarEvents.map((e): Row => ({ kind: "event", date: e.event_date ?? "", item: e })),
  ].sort((a, b) => b.date.localeCompare(a.date));

  return (
    <ul className="divide-y divide-[var(--border)]">
      {rows.map((row) =>
        row.kind === "announcement" ? (
          <li key={`a-${row.item.announced_date}-${row.item.announced_time}`} className="py-2.5">
            <p className="line-clamp-2 text-[13px] text-[var(--text)]">{row.item.subject}</p>
            <p className="mt-1 font-num text-[11px] text-[var(--text-muted)] tabular-nums">
              {row.item.announced_date} {row.item.announced_time}
            </p>
          </li>
        ) : (
          <li key={row.item.id} className="flex items-center gap-2 py-2.5">
            <span
              className="size-1.5 shrink-0 rounded-full"
              style={{ backgroundColor: CALENDAR_EVENT_DOT_COLOR[row.item.type] }}
              aria-hidden
            />
            <p className="flex-1 text-[13px] text-[var(--text)]">{row.item.title_zh}</p>
            <span className="font-num text-[11px] text-[var(--text-muted)] tabular-nums">
              {row.item.event_date ?? "—"}
            </span>
          </li>
        ),
      )}
    </ul>
  );
}

function AnnouncementsCalendarView({
  year,
  month,
  announcements,
  calendarEvents,
  onPrevMonth,
  onNextMonth,
}: {
  readonly year: number;
  readonly month: number;
  readonly announcements: readonly MaterialAnnouncement[];
  readonly calendarEvents: readonly CalendarEventOut[];
  readonly onPrevMonth: () => void;
  readonly onNextMonth: () => void;
}): ReactNode {
  const t = useTranslations("stock.basicInfo");
  const [selectedDay, setSelectedDay] = useState<string | null>(null);

  const dotsByDay = useMemo(() => {
    const map = new Map<string, string[]>();
    for (const a of announcements) {
      const colors = map.get(a.announced_date) ?? [];
      colors.push("var(--chart-6)");
      map.set(a.announced_date, colors);
    }
    for (const e of calendarEvents) {
      if (!e.event_date) continue;
      const colors = map.get(e.event_date) ?? [];
      colors.push(CALENDAR_EVENT_DOT_COLOR[e.type] ?? "var(--chart-6)");
      map.set(e.event_date, colors);
    }
    const deduped = new Map<string, string[]>();
    for (const [day, colors] of map) deduped.set(day, [...new Set(colors)]);
    return deduped;
  }, [announcements, calendarEvents]);

  const visibleAnnouncements = selectedDay
    ? announcements.filter((a) => a.announced_date === selectedDay)
    : announcements;
  const visibleEvents = selectedDay
    ? calendarEvents.filter((e) => e.event_date === selectedDay)
    : calendarEvents;

  return (
    <div>
      <MonthGridCalendar
        year={year}
        month={month}
        dotsByDay={dotsByDay}
        selectedDay={selectedDay}
        onSelectDay={setSelectedDay}
        onPrevMonth={onPrevMonth}
        onNextMonth={onNextMonth}
        prevMonthLabel={t("prevMonth")}
        nextMonthLabel={t("nextMonth")}
        monthTitle={t("monthTitle", { year, month })}
      />

      <AnnouncementsList announcements={visibleAnnouncements} calendarEvents={visibleEvents} />
    </div>
  );
}

function EsgCard({
  symbol,
  market,
}: {
  readonly symbol: string;
  readonly market: Market;
}): ReactNode {
  const t = useTranslations("stock.basicInfo");
  const query = useQuery({
    queryKey: ["esg_score", symbol, market],
    queryFn: () => getEsgScore(symbol, market),
  });

  // Coverage-gated — hidden entirely when null, never an empty scaffold (per the design spec).
  if (!query.data?.esg) return null;
  const { total_esg_score, esg_risk_percentile } = query.data.esg;

  return (
    <section className="rounded-[var(--radius-card)] border border-[var(--border)] bg-[var(--surface)] p-5">
      <h2 className="mb-3 font-semibold text-[15px] text-[var(--text)]">{t("esgTitle")}</h2>
      <div className="flex items-baseline gap-3">
        <span className="font-num text-[22px] text-[var(--text)] tabular-nums">
          {total_esg_score != null ? total_esg_score.toFixed(1) : "—"}
        </span>
        {esg_risk_percentile != null ? (
          <span className="font-num text-[12px] text-[var(--text-muted)] tabular-nums">
            {t("percentile", { value: esg_risk_percentile.toFixed(0) })}
          </span>
        ) : null}
      </div>
    </section>
  );
}

function DispositionBanner({
  symbol,
  market,
}: {
  readonly symbol: string;
  readonly market: Market;
}): ReactNode {
  const t = useTranslations("stock.basicInfo");
  const query = useQuery({
    queryKey: ["disposition_securities", symbol, market],
    queryFn: () => getDispositionSecurities(symbol, market),
  });

  if (!query.data) return null;
  if (isMaskedTeaser(query.data)) {
    return (
      <div className="xl:col-span-2">
        <SponsorTeaser
          headline={t("dispositionTitle")}
          preview={query.data.preview}
          requiredTier={query.data.required_tier}
        >
          <div className="h-12 rounded-[var(--radius-input)] bg-[var(--surface-2)]" />
        </SponsorTeaser>
      </div>
    );
  }
  const rows = query.data.rows;
  if (rows.length === 0) return null;
  const latest = rows[rows.length - 1];
  if (!latest) return null;

  return (
    <div className="flex items-center gap-2 rounded-[var(--radius-card)] bg-[var(--warn)]/12 px-4 py-3 text-[13px] text-[var(--warn)] xl:col-span-2">
      <span className="font-medium">{t("dispositionFlag")}</span>
      <span>{latest.measure}</span>
      <span className="font-num tabular-nums">{latest.date}</span>
    </div>
  );
}
