"use client";

import { BellIcon, BellRingingIcon } from "@phosphor-icons/react";
import { useSearchParams } from "next/navigation";
import { useLocale, useTranslations } from "next-intl";
import { type ReactNode, useState } from "react";
import { QueryState } from "@/components/ui/query-state";
import { SubscriptionDialog } from "@/components/watch/subscription-dialog";
import { WatchDetail } from "@/components/watch/watch-detail";
import { WatchFeed } from "@/components/watch/watch-feed";
import { initialStrategy } from "@/components/watch/watch-format";
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
 * feed of what met its conditions today. The route page passes the non-trading caveat in. */
export function WatchDashboard({ caveat }: { caveat: ReactNode }): ReactNode {
  const t = useTranslations("watch");
  const query = useStrategyWatch();
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
            <section className="surface-card p-6">
              <p className="font-medium text-[14px] text-[var(--text)]">{t("unavailable")}</p>
              <p className="mt-1 text-[13px] text-[var(--text-muted)]">{t("unavailableHint")}</p>
            </section>
          )
        }
      </QueryState>
      {caveat}
    </div>
  );
}

function WatchBody({ data }: { data: StrategyWatchToday }): ReactNode {
  const t = useTranslations("watch");
  const params = useSearchParams();
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
      <RunStatus data={data} />
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
                "min-h-11 shrink-0 whitespace-nowrap rounded-[var(--radius-pill)] border px-3.5 text-[13px] transition-colors",
                strategy === key
                  ? "border-[var(--accent)] bg-[var(--surface-2)] font-medium text-[var(--text)]"
                  : "border-[var(--border)] text-[var(--text-muted)] hover:text-[var(--text)]",
              )}
            >
              {t(`strategies.${key}.name`)}
              <span className="ml-1.5 font-num text-[var(--text-muted)] tabular-nums">
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
      <p className="text-[12.5px] text-[var(--text-muted)]">{t(`strategies.${strategy}.desc`)}</p>

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_400px]">
        <section className="surface-card overflow-x-auto p-3">
          <WatchTable
            items={items}
            caption={t("table.caption", { strategy: t(`strategies.${strategy}.name`) })}
            selected={selected?.symbol ?? null}
            onSelect={setSymbol}
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

/** 08:00 選股 → 09:00–13:30 盯盤 → 18:00 收盤確認, with the market breadth the list was built under. */
function RunStatus({ data }: { data: StrategyWatchToday }): ReactNode {
  const t = useTranslations("watch");
  const locale = useLocale();
  const isToday = data.run_date === taipeiToday();
  const steps = [
    { key: "screen", done: true, note: data.as_of ? t("asOf", { date: data.as_of }) : null },
    {
      key: "watch",
      done: data.last_slot !== null,
      note: data.last_slot
        ? t("pipeline.lastCheck", { time: data.last_slot })
        : t("pipeline.notStarted"),
    },
    {
      key: "close",
      done: data.events.some((e) => e.kind === "close"),
      note: null,
    },
  ] as const;
  return (
    <section className="surface-card space-y-3 p-4" aria-label={t("pipeline.label")}>
      {isToday || data.run_date === null ? null : (
        <p className="text-[12.5px] text-[var(--warn)]">
          {t("earlierList", { date: data.run_date })}
        </p>
      )}
      <ol className="flex flex-wrap items-center gap-x-2 gap-y-2 text-[12.5px]">
        {steps.map((step, index) => (
          <li key={step.key} className="flex items-center gap-2">
            {index > 0 ? <span aria-hidden className="h-px w-6 bg-[var(--border-strong)]" /> : null}
            <span
              className={cn(
                "inline-flex items-center gap-1.5 rounded-[var(--radius-pill)] border px-2.5 py-1",
                step.done
                  ? "border-[var(--accent)] text-[var(--text)]"
                  : "border-[var(--border)] text-[var(--text-muted)]",
              )}
            >
              <span
                aria-hidden
                className={cn(
                  "size-2 rounded-full",
                  step.done ? "bg-[var(--accent)]" : "bg-[var(--border-strong)]",
                )}
              />
              {t(`pipeline.${step.key}`)}
              {step.note ? <span className="text-[var(--text-muted)]">· {step.note}</span> : null}
            </span>
          </li>
        ))}
      </ol>
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
