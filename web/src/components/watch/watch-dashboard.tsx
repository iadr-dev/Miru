"use client";

import { BellIcon, BellRingingIcon } from "@phosphor-icons/react";
import { useSearchParams } from "next/navigation";
import { useTranslations } from "next-intl";
import { type ReactNode, useState } from "react";
import { QueryState } from "@/components/ui/query-state";
import { RunStatus } from "@/components/watch/run-status";
import { SubscriptionDialog } from "@/components/watch/subscription-dialog";
import { WatchDetail } from "@/components/watch/watch-detail";
import { WatchFeed } from "@/components/watch/watch-feed";
import { initialStrategy, STRATEGY_COLOR, tint } from "@/components/watch/watch-format";
import { WatchTable } from "@/components/watch/watch-table";
import { useStrategySubscription, useStrategyWatch } from "@/hooks/use-strategy-watch";
import { cn } from "@/lib/cn";
import {
  STRATEGY_KEYS,
  type StrategyKey,
  type StrategyWatchEvent,
  type StrategyWatchToday,
} from "@/types/strategy-watch";

/** Taipei's calendar date now, as YYYY-MM-DD: the list is dated by the Taiwan trading day. */
function taipeiToday(): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Taipei" }).format(new Date());
}

/** AI 盯盤: today's run, the strategy tabs, the list with the selected stock beside it, and the
 * feed of what met its conditions today. The non-trading statement is the page subtitle (the
 * product owner chose that over a separate caveat chip here; OVERVIEW.md §4 records the exception). */
/** `?date=YYYY-MM-DD` shows that day's list (a past session) instead of today's. */
function requestedDate(value: string | null): string | undefined {
  return value !== null && /^\d{4}-\d{2}-\d{2}$/.test(value) ? value : undefined;
}

export function WatchDashboard(): ReactNode {
  const t = useTranslations("watch");
  const params = useSearchParams();
  const query = useStrategyWatch(requestedDate(params.get("date")));
  return (
    <div className="mx-auto w-full max-w-[1400px] space-y-4 p-4 sm:p-6">
      <header className="space-y-1">
        <h1 className="font-semibold text-[20px] text-[var(--text)]">{t("title")}</h1>
        <p className="max-w-3xl text-[13px] text-[var(--text-muted)] leading-relaxed">
          {t("subtitle")}
        </p>
      </header>
      <QueryState query={query}>
        {(data) =>
          data.data_available ? (
            <WatchBody data={data} />
          ) : (
            <>
              <RunStatus data={data} stale={false} />
              <section className="surface-card p-6">
                <p className="font-medium text-[14px] text-[var(--text)]">{t("unavailable")}</p>
                <p className="mt-1 text-[13px] text-[var(--text-muted)]">{t("unavailableHint")}</p>
              </section>
            </>
          )
        }
      </QueryState>
    </div>
  );
}

function WatchBody({ data }: { data: StrategyWatchToday }): ReactNode {
  const t = useTranslations("watch");
  const params = useSearchParams();
  const asked = requestedDate(params.get("date"));
  // A list for another day is only a warning when today's was wanted and is not ready.
  const isToday = data.run_date === (asked ?? taipeiToday());
  const [strategy, setStrategy] = useState<StrategyKey>(() =>
    initialStrategy(data.counts, STRATEGY_KEYS, params.get("strategy")),
  );
  const [symbol, setSymbol] = useState<string | null>(() => params.get("symbol"));
  const [dialogOpen, setDialogOpen] = useState(false);
  const subscription = useStrategySubscription();

  const items = data.items.filter((item) => item.strategy === strategy);
  const selected =
    items.find((item) => item.symbol === symbol) ?? (symbol === null ? (items[0] ?? null) : null);
  const subscribed = subscription.data?.enabled === true;

  const openEvent = (event: StrategyWatchEvent): void => {
    setStrategy(event.strategy);
    setSymbol(event.symbol);
  };

  return (
    <>
      <RunStatus data={data} stale={!isToday} />
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div
          role="tablist"
          aria-label={t("pipeline.label")}
          className="tab-strip flex gap-1 overflow-x-auto"
        >
          {STRATEGY_KEYS.map((key) => (
            <button
              key={key}
              type="button"
              role="tab"
              aria-selected={strategy === key}
              onClick={() => {
                setStrategy(key);
                setSymbol(null);
              }}
              className={cn(
                "inline-flex min-h-11 shrink-0 items-center gap-2 whitespace-nowrap rounded-[var(--radius-pill)] border px-3.5 text-[13px] transition-colors",
                strategy === key
                  ? "font-medium text-[var(--text)]"
                  : "border-[var(--border)] text-[var(--text-muted)] hover:text-[var(--text)]",
              )}
              style={
                strategy === key
                  ? {
                      borderColor: STRATEGY_COLOR[key],
                      backgroundColor: tint(STRATEGY_COLOR[key], 14),
                    }
                  : undefined
              }
            >
              <span
                aria-hidden
                className="size-2.5 rounded-full"
                style={{ backgroundColor: STRATEGY_COLOR[key] }}
              />
              {t(`strategies.${key}.name`)}
              <span
                className="rounded-[var(--radius-pill)] px-1.5 font-num text-[11.5px] tabular-nums"
                style={{
                  backgroundColor: tint(STRATEGY_COLOR[key], strategy === key ? 28 : 14),
                  color: "var(--text)",
                }}
              >
                {data.counts[key] ?? 0}
              </span>
            </button>
          ))}
        </div>
        <button
          type="button"
          onClick={() => setDialogOpen(true)}
          className={cn(
            "inline-flex min-h-11 items-center gap-1.5 rounded-[var(--radius-button)] border px-3.5 text-[13px]",
            subscribed
              ? "border-[var(--accent)] text-[var(--accent-text)]"
              : "border-[var(--border)] text-[var(--text)] hover:bg-[var(--surface-2)]",
          )}
        >
          {subscribed ? (
            <BellRingingIcon size={16} aria-hidden />
          ) : (
            <BellIcon size={16} aria-hidden />
          )}
          {subscribed ? t("subscribed") : t("subscribe")}
        </button>
      </div>
      <p
        className="border-l-[3px] pl-2.5 text-[12.5px] text-[var(--text-muted)]"
        style={{ borderColor: STRATEGY_COLOR[strategy] }}
      >
        {t(`strategies.${strategy}.desc`)}
      </p>

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_400px]">
        <section className="surface-card overflow-x-auto p-3">
          <WatchTable
            items={items}
            caption={t("table.caption", { strategy: t(`strategies.${strategy}.name`) })}
            selected={selected?.symbol ?? null}
            onSelect={setSymbol}
            color={STRATEGY_COLOR[strategy]}
          />
        </section>
        <aside className="surface-card lg:sticky lg:top-4 lg:self-start" aria-live="off">
          <WatchDetail item={selected} masked={data.masked} />
        </aside>
      </div>

      <WatchFeed events={data.events} onSelect={openEvent} />
      <SubscriptionDialog
        open={dialogOpen}
        onClose={() => setDialogOpen(false)}
        current={subscription.data ?? null}
      />
    </>
  );
}
