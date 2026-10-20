"use client";

import { useLocale, useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { PipelineTimeline } from "@/components/watch/pipeline-timeline";
import type { StrategyWatchToday } from "@/types/strategy-watch";

export type PipelineStepKey = "screen" | "watch" | "prices" | "close" | "flows";

export type PipelineStep = {
  key: PipelineStepKey;
  done: boolean;
  /** What the step's note says: a date the data runs through, the last check time, or nothing. */
  note:
    | { kind: "asOf"; date: string }
    | { kind: "lastCheck"; time: string }
    | { kind: "notStarted" }
    | { kind: "dataThrough"; date: string }
    | { kind: "noData" }
    | null;
};

/** The earliest of the dates that exist (ISO dates sort as strings). */
function oldest(...dates: (string | null)[]): string | null {
  return dates.filter((d): d is string => d !== null).sort((a, b) => a.localeCompare(b))[0] ?? null;
}

function refreshNote(date: string | null): PipelineStep["note"] {
  return date === null ? { kind: "noData" } : { kind: "dataThrough", date };
}

/** The day's cycle in time order: the 08:00 screen, the intraday checks, the post-close refreshes
 * the next morning's screen depends on (股價・融資 17:40, 三大法人 20:10) and the 18:00 close
 * check. A refresh is done when its data has landed for the list's session; otherwise its note
 * says where the data stops, which is how a failing source shows up instead of going unnoticed. */
export function pipelineSteps(data: StrategyWatchToday): PipelineStep[] {
  const session = data.run_date;
  const dates = data.data_dates;
  const prices = oldest(dates.price_eod, dates.margin_short);
  const landed = (...required: (string | null)[]): boolean =>
    session !== null && required.every((date) => date !== null && date >= session);
  return [
    {
      key: "screen",
      done: session !== null,
      note: data.as_of ? { kind: "asOf", date: data.as_of } : null,
    },
    {
      key: "watch",
      done: data.last_slot !== null,
      note: data.last_slot ? { kind: "lastCheck", time: data.last_slot } : { kind: "notStarted" },
    },
    {
      key: "prices",
      done: landed(dates.price_eod, dates.margin_short),
      note: refreshNote(prices),
    },
    { key: "close", done: data.events.some((e) => e.kind === "close"), note: null },
    {
      key: "flows",
      done: landed(dates.institutional_flow),
      note: refreshNote(dates.institutional_flow),
    },
  ];
}

/** Each step of the cycle has its own colour (categorical palette, never the price red/green). */
const STEP_COLOR: Record<PipelineStepKey, string> = {
  screen: "var(--chart-1)",
  watch: "var(--chart-7)",
  prices: "var(--chart-3)",
  close: "var(--chart-2)",
  flows: "var(--chart-4)",
};

export function RunStatus({
  data,
  stale,
}: {
  data: StrategyWatchToday;
  /** Today's list was wanted and an older one is shown. */
  stale: boolean;
}): ReactNode {
  const t = useTranslations("watch");
  const locale = useLocale();
  return (
    <section className="surface-card space-y-3 p-4" aria-label={t("pipeline.label")}>
      {stale && data.run_date !== null ? (
        <p className="text-[12.5px] text-[var(--warn)]">
          {t("earlierList", { date: data.run_date })}
        </p>
      ) : null}
      <PipelineTimeline data={data} steps={pipelineSteps(data)} colors={STEP_COLOR} />
      {data.regime !== null ? (
        <p className="text-[12.5px] text-[var(--text-muted)]">
          {t("regime.label")}：{t(`regime.${data.regime}`)} ·{" "}
          {t("regime.breadth", {
            pct:
              data.breadth_pct === null
                ? "—"
                : new Intl.NumberFormat(locale, { maximumFractionDigits: 0 }).format(
                    data.breadth_pct,
                  ),
          })}
        </p>
      ) : null}
    </section>
  );
}
