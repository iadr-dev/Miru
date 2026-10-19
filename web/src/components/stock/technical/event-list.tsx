"use client";

import { CoinsIcon, CurrencyCircleDollarIcon, UsersThreeIcon } from "@phosphor-icons/react";
import type { useTranslations } from "next-intl";
import type { ComponentType, ReactNode } from "react";
import { Skeleton } from "@/components/ui/skeleton";
import { CHART_EVENT_STYLE, type ChartEvent, type ChartEventKind } from "@/lib/chart-events";

const KIND_ICON: Record<
  ChartEventKind,
  ComponentType<{ size?: number; "aria-hidden"?: boolean }>
> = {
  exDividend: CurrencyCircleDollarIcon,
  exRights: CurrencyCircleDollarIcon,
  exDividendRights: CurrencyCircleDollarIcon,
  agm: UsersThreeIcon,
  capitalIncrease: CoinsIcon,
};

type EventsT = ReturnType<typeof useTranslations>;

function amountLine(event: ChartEvent, t: EventsT): string | null {
  const parts: string[] = [];
  if (event.cash !== undefined) parts.push(t("events.cash", { value: event.cash }));
  if (event.stock !== undefined) parts.push(t("events.stock", { value: event.stock }));
  return parts.length > 0 ? parts.join(" · ") : null;
}

/** The list under the candlestick chart: the accessible equivalent of the event markers (a screen
 * reader never sees the canvas) and the only place with the full details, amounts included. Newest
 * first; only events on or after `rangeStart` are shown, upcoming ones flagged in words. */
export function EventList({
  events,
  rangeStart,
  isLoading,
  isError,
  degraded,
  onRetry,
  t,
}: {
  readonly events: readonly ChartEvent[];
  readonly rangeStart: string;
  readonly isLoading: boolean;
  readonly isError: boolean;
  readonly degraded: boolean;
  readonly onRetry: () => void;
  readonly t: EventsT;
}): ReactNode {
  const visible = events.filter((e) => e.date >= rangeStart);
  let body: ReactNode;
  if (isLoading) {
    body = (
      <div className="flex flex-col gap-2" role="status" aria-busy="true">
        <Skeleton className="h-9 w-full" />
        <Skeleton className="h-9 w-full" />
        <Skeleton className="h-9 w-2/3" />
      </div>
    );
  } else if (isError) {
    body = (
      <div
        role="alert"
        className="rounded-[var(--radius-input)] border border-[var(--warn)]/40 bg-[var(--surface-2)] p-3"
      >
        <p className="text-[13px] text-[var(--text)]">{t("events.error")}</p>
        <button
          type="button"
          onClick={onRetry}
          className="mt-1 inline-flex min-h-[44px] items-center font-medium text-[13px] text-[var(--accent-text)] hover:underline"
        >
          {t("events.retry")}
        </button>
      </div>
    );
  } else if (visible.length === 0) {
    body = <p className="text-[13px] text-[var(--text-muted)]">{t("events.empty")}</p>;
  } else {
    body = (
      <ul aria-label={t("events.listLabel")} className="divide-y divide-[var(--border)]">
        {visible.map((event) => {
          const Icon = KIND_ICON[event.kind];
          const amounts = amountLine(event, t);
          return (
            <li
              key={`${event.date}|${event.kind}`}
              className="flex flex-wrap items-center gap-x-3 gap-y-1 py-2"
            >
              <span style={{ color: CHART_EVENT_STYLE[event.kind].token }}>
                <Icon size={18} aria-hidden />
              </span>
              <time dateTime={event.date} className="font-num text-[13px] tabular-nums">
                {event.date}
              </time>
              <span className="font-medium text-[13px] text-[var(--text)]">
                {t(`events.kind.${event.kind}`)}
              </span>
              {event.upcoming ? (
                <span className="rounded-[var(--radius-pill)] bg-[var(--accent-weak)] px-2 py-0.5 text-[11px] text-[var(--accent-text)]">
                  {t("events.upcoming")}
                </span>
              ) : null}
              {amounts ? (
                <span className="font-num text-[12px] text-[var(--text-muted)] tabular-nums">
                  {amounts}
                </span>
              ) : null}
            </li>
          );
        })}
      </ul>
    );
  }

  return (
    <div className="mt-4">
      <h3 className="mb-2 font-semibold text-[13px] text-[var(--text)]">{t("events.title")}</h3>
      {body}
      {degraded && !isLoading ? (
        <p className="mt-2 text-[11px] text-[var(--text-muted)]">{t("events.degraded")}</p>
      ) : null}
    </div>
  );
}
