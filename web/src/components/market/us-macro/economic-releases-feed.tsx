"use client";

import { useLocale, useTranslations } from "next-intl";
import { type ReactNode, useMemo, useState } from "react";
import { CardSkeletonBody } from "@/components/market/card-skeleton";
import { ChipGroup } from "@/components/market/us-macro/chip-group";
import { ReleaseEntry } from "@/components/market/us-macro/release-entry";
import { QueryState } from "@/components/ui/query-state";
import { useUsEconomicReleases } from "@/hooks/use-us-macro";
import { currentWeekBounds, formatMacroWeekdayDate } from "@/lib/us-macro-format";
import type { ReleaseEntry as ReleaseEntryData } from "@/types/market/us-macro";

type FeedFilter = "all" | "week";

type DayGroup = { readonly date: string; readonly releases: readonly ReleaseEntryData[] };

function groupByDate(releases: readonly ReleaseEntryData[]): DayGroup[] {
  const groups = new Map<string, ReleaseEntryData[]>();
  for (const release of releases) {
    const group = groups.get(release.release_date);
    if (group) {
      group.push(release);
    } else {
      groups.set(release.release_date, [release]);
    }
  }
  return [...groups.entries()].map(([date, items]) => ({ date, releases: items }));
}

/** Upcoming days first (soonest at the top), then the recent past (newest first), so what is
 * coming next is never buried under what already came out. */
function orderedDayGroups(releases: readonly ReleaseEntryData[]): DayGroup[] {
  const upcoming = groupByDate(releases.filter((release) => release.status === "upcoming")).sort(
    (a, b) => a.date.localeCompare(b.date),
  );
  const released = groupByDate(releases.filter((release) => release.status === "released")).sort(
    (a, b) => b.date.localeCompare(a.date),
  );
  return [...upcoming, ...released];
}

/** 經濟數據發布: the data releases around today grouped by date, each with its status and the
 * latest vs previous value of the series it covers. Complements the calendar above it (which
 * carries the consensus); this list has no forecasts. */
export function EconomicReleasesFeed(): ReactNode {
  const t = useTranslations("market.cards.usMacro.releases");
  const locale = useLocale();
  const [filter, setFilter] = useState<FeedFilter>("all");
  const query = useUsEconomicReleases();
  const week = useMemo(() => currentWeekBounds(new Date()), []);

  return (
    <section className="surface-card flex w-full flex-col gap-3 p-4 sm:p-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-0">
          <h2 className="font-semibold text-[15px] text-[var(--text)]">{t("title")}</h2>
          <p className="mt-0.5 text-[12.5px] text-[var(--text-muted)]">{t("description")}</p>
        </div>
        <ChipGroup
          ariaLabel={t("filterLabel")}
          value={filter}
          onChange={setFilter}
          options={[
            { value: "all", label: t("filterAll") },
            { value: "week", label: t("filterWeek") },
          ]}
        />
      </div>
      <QueryState query={query} skeleton={<CardSkeletonBody height={260} />}>
        {(data) => {
          const visible =
            filter === "week"
              ? data.releases.filter(
                  (release) =>
                    release.release_date >= week.start && release.release_date <= week.end,
                )
              : data.releases;
          if (!data.data_available || visible.length === 0) {
            return (
              <p className="py-6 text-center text-[13px] text-[var(--text-muted)]">
                {!data.data_available || filter === "all" ? t("empty") : t("emptyWeek")}
              </p>
            );
          }
          return (
            <div className="flex flex-col gap-4">
              {orderedDayGroups(visible).map((group) => (
                <div key={group.date} className="flex flex-col gap-2">
                  <h3 className="font-num text-[12.5px] text-[var(--text-muted)] tabular-nums">
                    {formatMacroWeekdayDate(group.date, locale)}
                  </h3>
                  <ul className="flex flex-col gap-2">
                    {group.releases.map((release) => (
                      <ReleaseEntry key={release.release_id} release={release} />
                    ))}
                  </ul>
                </div>
              ))}
              <p className="text-[11.5px] text-[var(--text-muted)]">{data.currentness_caveat}</p>
            </div>
          );
        }}
      </QueryState>
    </section>
  );
}
