"use client";

import { useLocale, useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { formatMacroDay, formatMacroValue } from "@/lib/us-macro-format";
import type { YieldCurveSpread } from "@/types/market/us-macro";

/** One spread's state in neutral words (倒掛中 / 持平 / 正斜率) with how long it has been inverted
 * and since when. The state is text, never a red/green signal; the swatch only matches the line. */
export function YieldSpreadStatus({
  spread,
  color,
}: {
  readonly spread: YieldCurveSpread;
  readonly color: string | null;
}): ReactNode {
  const t = useTranslations("market.cards.usMacro.yieldCurve");
  const locale = useLocale();
  const name = t.has(`spread.${spread.series_id}`) ? t(`spread.${spread.series_id}`) : spread.label;

  return (
    <li className="rounded-[var(--radius-input)] bg-[var(--surface-2)] px-3.5 py-3">
      <div className="flex items-center justify-between gap-3">
        <span className="flex min-w-0 items-center gap-2 text-[13px] text-[var(--text)]">
          {color ? (
            <span
              aria-hidden
              className="h-0.5 w-3 shrink-0 rounded-full"
              style={{ backgroundColor: color }}
            />
          ) : null}
          <span className="truncate">{name}</span>
        </span>
        <span className="shrink-0 font-num text-[13px] text-[var(--text)] tabular-nums">
          {formatMacroValue(spread.latest.value, "percent", locale)}
        </span>
      </div>
      <p className="mt-1 font-semibold text-[13px] text-[var(--text)]">
        {t(`state.${spread.state}`)}
      </p>
      <StatusDetail spread={spread} />
    </li>
  );
}

function StatusDetail({ spread }: { readonly spread: YieldCurveSpread }): ReactNode {
  const t = useTranslations("market.cards.usMacro.yieldCurve");
  const locale = useLocale();

  if (spread.state === "inverted") {
    return (
      <p className="mt-0.5 text-[12px] text-[var(--text-muted)]">
        <span className="font-num tabular-nums">
          {t("daysInverted", { days: spread.days_inverted })}
        </span>
        {spread.inverted_since ? (
          <>
            {" · "}
            {t(spread.since_window_start ? "sinceWindowStart" : "since", {
              date: formatMacroDay(spread.inverted_since, locale),
            })}
          </>
        ) : null}
      </p>
    );
  }
  if (spread.last_inverted_date) {
    return (
      <p className="mt-0.5 text-[12px] text-[var(--text-muted)]">
        {t("lastInverted", { date: formatMacroDay(spread.last_inverted_date, locale) })}
      </p>
    );
  }
  return null;
}
