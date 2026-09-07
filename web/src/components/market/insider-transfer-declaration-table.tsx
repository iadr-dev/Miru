"use client";

import { useLocale, useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { formatNumber } from "@/app/[locale]/(app)/chat/cards/card-format";
import type { InsiderTransferDeclarationRow } from "@/types/market";

// Same collapsed-row-count convention every other market card in this dashboard uses.
export const INSIDER_TRANSFER_DECLARATION_COLLAPSED_ROW_COUNT = 10;

/**
 * 內部人股權轉讓事前申報 whole-market feed list — one row per declaration: filer/person,
 * transfer method + shares, transferee (when it's a 贈與/non-market transfer), transfer window,
 * and current vs. projected holdings. Public (`DataClass.PUBLIC`) — no `SponsorTeaser`/
 * masked-teaser branch, same no-blur requirement as `MaterialAnnouncementFeedTable`. Shared
 * verbatim between the card's inline (sliced) preview and the `ExpandModal` full list.
 */
export function InsiderTransferDeclarationTable({
  rows,
}: {
  readonly rows: readonly InsiderTransferDeclarationRow[];
}): ReactNode {
  const t = useTranslations("market.cards.insiderTransferDeclaration");
  const locale = useLocale();

  if (rows.length === 0) {
    return <p className="py-3 text-[13px] text-[var(--text-muted)]">{t("empty")}</p>;
  }

  return (
    <ul className="divide-y divide-[var(--border)]">
      {rows.map((row, index) => (
        <li
          // `symbol`+`person_name`+`transfer_window_start` isn't guaranteed unique — the same
          // person can file more than one declaration in the same window (e.g. distinct
          // transfer_method/transferee rows, confirmed live: 7610's 廖佩芬 filed two 贈與 rows for
          // the same 2026-09-03 window). `rows` is a stable per-fetch snapshot, never reordered
          // in place, so `index` is a safe tiebreaker here.
          // biome-ignore lint/suspicious/noArrayIndexKey: no field combination is guaranteed unique upstream
          key={`${row.symbol}-${row.person_name}-${row.transfer_window_start}-${index}`}
          className="py-2.5"
        >
          <div className="flex items-center justify-between gap-2">
            <span className="truncate text-[13px] text-[var(--text)]">
              {row.company_name}
              <span className="ml-1.5 text-[11px] text-[var(--text-muted)]">{row.symbol}</span>
            </span>
            <span className="text-right font-num text-[13px] text-[var(--text)] tabular-nums">
              {formatNumber(row.transfer_shares, locale, { decimals: 0 })} {t("shares")}
            </span>
          </div>
          <p className="mt-0.5 text-[12px] text-[var(--text-muted)]">
            {row.person_name} · {row.filer_role} · {row.transfer_method}
            {row.transferee ? ` → ${row.transferee}` : ""}
          </p>
          <div className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 text-[11px] text-[var(--text-muted)]">
            <span>
              {t("transferWindow")} {row.transfer_window_start} ~ {row.transfer_window_end}
            </span>
            <span>
              {t("currentShares")}{" "}
              {formatNumber(row.current_shares_own + row.current_shares_trust, locale, {
                decimals: 0,
              })}
            </span>
            <span>
              {t("projectedShares")}{" "}
              {formatNumber(
                row.projected_shares_own_after + row.projected_shares_trust_after,
                locale,
                { decimals: 0 },
              )}
            </span>
          </div>
        </li>
      ))}
    </ul>
  );
}
