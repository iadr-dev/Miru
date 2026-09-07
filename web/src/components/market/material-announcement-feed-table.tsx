"use client";

import { CaretDownIcon } from "@phosphor-icons/react";
import { useTranslations } from "next-intl";
import { type ReactNode, useState } from "react";
import type { MaterialAnnouncementRow } from "@/types/market";

// Same collapsed-row-count convention `DAILY_SIGNAL_COLLAPSED_ROW_COUNT`/`COLLAPSED_EVENT_COUNT`
// (dividend calendar) use — enough rows visible inline while keeping this card's height
// comparable to its dashboard neighbors; "展開完整{count}筆" opens the full list in
// `ExpandModal`.
export const MATERIAL_ANNOUNCEMENT_FEED_COLLAPSED_ROW_COUNT = 5;

// Category badges use distinct categorical tokens (--chart-1..6), never --up/--down — the
// bucket is a disclosure-type fact (`sources/mops.py::_CATEGORY_BY_CLAUSE`), not a price/%/score
// signal (web/CLAUDE.md §4). Keyed by the backend's own bucket label, same literal strings
// `_CATEGORY_BY_CLAUSE`/`_DEFAULT_CATEGORY` produce. Exported so the chip row
// (`MaterialAnnouncementFeedPanel`) uses the exact same mapping as these row badges.
export const MATERIAL_ANNOUNCEMENT_CATEGORY_COLORS: Record<string, string> = {
  法說會: "var(--chart-1)",
  "資產處分/併購": "var(--chart-2)",
  "庫藏股/減資": "var(--chart-3)",
  "財務/營運重大變動": "var(--chart-4)",
  其他: "var(--chart-6)",
};
const DEFAULT_CATEGORY_COLOR = "var(--chart-6)";

/**
 * 重大訊息 whole-market feed list — one row per disclosure, category chip + subject (2-line
 * truncate) + symbol/company_name + time, expandable to the full `detail` text. Public
 * (`DataClass.PUBLIC`) — no `SponsorTeaser`/masked-teaser branch anywhere, per this feature's
 * own explicit no-blur requirement. Shared verbatim between the card's inline (sliced) preview
 * and the `ExpandModal` full list (`MaterialAnnouncementFeedPanel` slices before passing rows
 * here) — one render path, so the two never drift in columns/formatting, the same convention
 * `DividendCalendarEventList`/`filterDailySignalRows` already use.
 */
export function MaterialAnnouncementFeedTable({
  rows,
}: {
  readonly rows: readonly MaterialAnnouncementRow[];
}): ReactNode {
  const t = useTranslations("market.cards.materialAnnouncementFeed");

  if (rows.length === 0) {
    return <p className="py-3 text-[13px] text-[var(--text-muted)]">{t("empty")}</p>;
  }

  return (
    <ul className="divide-y divide-[var(--border)]">
      {rows.map((row) => (
        <MaterialAnnouncementRowItem key={`${row.symbol}-${row.announced_time}`} row={row} />
      ))}
    </ul>
  );
}

function MaterialAnnouncementRowItem({
  row,
}: {
  readonly row: MaterialAnnouncementRow;
}): ReactNode {
  const [expanded, setExpanded] = useState(false);

  return (
    <li className="py-2.5">
      <button
        type="button"
        onClick={() => setExpanded((prev) => !prev)}
        aria-expanded={expanded}
        className="grid min-h-11 w-full grid-cols-[88px_1fr_14px] items-start gap-2 text-left"
      >
        <span
          className="mt-0.5 w-fit justify-self-start truncate rounded-[var(--radius-pill)] bg-[var(--surface-2)] px-2 py-0.5 font-medium text-[11px]"
          style={{
            color: MATERIAL_ANNOUNCEMENT_CATEGORY_COLORS[row.category] ?? DEFAULT_CATEGORY_COLOR,
          }}
        >
          {row.category}
        </span>
        <span className="min-w-0">
          <span
            className={
              expanded
                ? "text-[13px] text-[var(--text)]"
                : "line-clamp-1 truncate text-[13px] text-[var(--text)]"
            }
          >
            {row.subject}
          </span>
          <span className="mt-1 flex items-center gap-1.5 text-[11px] text-[var(--text-muted)]">
            {row.company_name}
            <span>{row.symbol}</span>
            <span className="font-num tabular-nums">{row.announced_time}</span>
          </span>
        </span>
        <CaretDownIcon
          size={14}
          weight="regular"
          aria-hidden
          className={`mt-1 shrink-0 text-[var(--text-muted)] transition-transform ${expanded ? "rotate-180" : ""}`}
        />
      </button>
      {expanded ? (
        <p className="mt-2 whitespace-pre-line text-[12px] text-[var(--text-muted)]">
          {row.detail}
        </p>
      ) : null}
    </li>
  );
}
