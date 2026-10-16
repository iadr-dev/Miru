"use client";

import { useTranslations } from "next-intl";
import type { ReactNode } from "react";
import type { StrategyWatchEvent } from "@/types/strategy-watch";

/** 今日條件達成: each stock's first intraday slot that met every condition (on two checks in a
 * row) and its close confirmation, in time order. Choosing one opens it in the detail panel. */
export function WatchFeed({
  events,
  onSelect,
}: {
  events: readonly StrategyWatchEvent[];
  onSelect: (event: StrategyWatchEvent) => void;
}): ReactNode {
  const t = useTranslations("watch");
  return (
    <section aria-labelledby="watch-feed" className="surface-card p-4">
      <h2 id="watch-feed" className="mb-2 font-semibold text-[13.5px] text-[var(--text)]">
        {t("feed.title")}
      </h2>
      {events.length === 0 ? (
        <p className="text-[12.5px] text-[var(--text-muted)]">{t("feed.empty")}</p>
      ) : (
        <ul className="divide-y divide-[var(--border)]">
          {events.map((event) => (
            <li key={`${event.symbol}-${event.strategy}-${event.kind}`}>
              <button
                type="button"
                onClick={() => onSelect(event)}
                className="flex min-h-11 w-full items-center gap-3 py-1.5 text-left text-[12.5px] hover:bg-[var(--surface-2)]"
              >
                <span className="w-12 shrink-0 font-num text-[var(--text-muted)] tabular-nums">
                  {event.kind === "close" ? t("feed.close") : event.slot}
                </span>
                <span className="font-medium text-[var(--text)]">
                  {event.name ?? event.symbol}{" "}
                  <span className="font-normal font-num text-[var(--text-muted)]">
                    {event.symbol}
                  </span>
                </span>
                <span className="ml-auto text-[var(--text-muted)]">
                  {t(`strategies.${event.strategy}.name`)} ·{" "}
                  {event.kind === "close" ? t("status.closeConfirmed") : t("status.allMet")}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
