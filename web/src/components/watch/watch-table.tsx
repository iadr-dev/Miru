"use client";

import { BellRingingIcon, SealCheckIcon } from "@phosphor-icons/react";
import { useLocale, useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { distanceToLevel, keyLevel, triggersMet } from "@/components/watch/watch-format";
import { cn } from "@/lib/cn";
import { formatNumber } from "@/lib/format-number";
import type { StrategyWatchItem } from "@/types/strategy-watch";

/** One strategy's list. A row is a button: choosing it shows the stock in the detail panel. The
 * dots are the trigger conditions at the latest intraday check (filled = met). */
export function WatchTable({
  items,
  caption,
  selected,
  onSelect,
}: {
  items: readonly StrategyWatchItem[];
  caption: string;
  selected: string | null;
  onSelect: (symbol: string) => void;
}): ReactNode {
  const t = useTranslations("watch");
  const locale = useLocale();

  if (items.length === 0) {
    return <p className="px-1 py-6 text-[13px] text-[var(--text-muted)]">{t("table.empty")}</p>;
  }

  return (
    <table className="w-full border-collapse text-[13px]">
      <caption className="sr-only">{caption}</caption>
      <thead>
        <tr className="border-[var(--border)] border-b text-[11.5px] text-[var(--text-muted)]">
          <th scope="col" className="py-2 pr-2 text-left font-medium">
            {t("table.name")}
          </th>
          <th scope="col" className="px-2 py-2 text-right font-medium">
            {t("table.price")}
          </th>
          <th scope="col" className="px-2 py-2 text-right font-medium">
            {t("table.level")}
          </th>
          <th scope="col" className="px-2 py-2 text-left font-medium">
            {t("table.conditions")}
          </th>
          <th scope="col" className="py-2 pl-2 text-right font-medium">
            {t("table.days")}
          </th>
        </tr>
      </thead>
      <tbody>
        {items.map((item) => {
          const level = keyLevel(item);
          const distance = distanceToLevel(item);
          const { met, total } = triggersMet(item);
          const active = selected === item.symbol;
          return (
            <tr
              key={`${item.symbol}-${item.strategy}`}
              className={cn(
                "border-[var(--border)] border-b last:border-b-0",
                active && "bg-[var(--surface-2)]",
              )}
            >
              <td className="py-2 pr-2">
                <button
                  type="button"
                  onClick={() => onSelect(item.symbol)}
                  aria-pressed={active}
                  className="flex min-h-11 w-full flex-col items-start text-left focus-visible:outline-2 focus-visible:outline-[var(--accent)]"
                >
                  <span className="font-medium text-[var(--text)]">{item.name ?? item.symbol}</span>
                  <span className="font-num text-[11.5px] text-[var(--text-muted)]">
                    {item.symbol}
                    {item.industry ? ` · ${item.industry}` : ""}
                  </span>
                </button>
              </td>
              <td className="px-2 py-2 text-right font-num tabular-nums">
                {formatNumber(item.price ?? item.close, locale, { decimals: 2 })}
              </td>
              <td className="px-2 py-2 text-right">
                <span className="block font-num tabular-nums">
                  {formatNumber(level.value, locale, { decimals: 2 })}
                </span>
                <span className="block text-[11px] text-[var(--text-muted)]">
                  {t(`levels.${level.key}`)}
                  {distance !== null ? ` ${distance >= 0 ? "+" : ""}${distance.toFixed(1)}%` : ""}
                </span>
              </td>
              <td className="px-2 py-2">
                <ConditionDots met={met} total={total} checked={item.latest_slot !== null} />
                <span className="mt-0.5 flex flex-wrap gap-1 text-[11px]">
                  {item.alerted_at ? (
                    <span className="inline-flex items-center gap-0.5 text-[var(--accent-text)]">
                      <BellRingingIcon size={12} aria-hidden />
                      {t("status.alerted", { time: item.alerted_at })}
                    </span>
                  ) : null}
                  {item.close_confirmed ? (
                    <span className="inline-flex items-center gap-0.5 text-[var(--accent-text)]">
                      <SealCheckIcon size={12} aria-hidden />
                      {t("status.closeConfirmed")}
                    </span>
                  ) : null}
                </span>
              </td>
              <td className="py-2 pl-2 text-right font-num text-[var(--text-muted)] tabular-nums">
                {item.days_on_list}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

export function ConditionDots({
  met,
  total,
  checked,
}: {
  met: number;
  total: number;
  checked: boolean;
}): ReactNode {
  const t = useTranslations("watch.status");
  if (!checked || total === 0) {
    return <span className="text-[11.5px] text-[var(--text-muted)]">{t("notChecked")}</span>;
  }
  return (
    <span
      className="inline-flex items-center gap-1"
      role="img"
      aria-label={t("conditionsMet", { met, total })}
    >
      {Array.from({ length: total }, (_, i) => (
        <span
          // biome-ignore lint/suspicious/noArrayIndexKey: dots have no identity beyond position
          key={i}
          className={cn(
            "size-2.5 rounded-full border",
            i < met
              ? "border-[var(--accent)] bg-[var(--accent)]"
              : "border-[var(--border-strong)] bg-transparent",
          )}
        />
      ))}
    </span>
  );
}
