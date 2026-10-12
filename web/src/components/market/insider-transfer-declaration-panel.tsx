"use client";

import { useQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { type ReactNode, useState } from "react";
import { CardSkeletonBody } from "@/components/market/card-skeleton";
import {
  INSIDER_TRANSFER_DECLARATION_COLLAPSED_ROW_COUNT,
  InsiderTransferDeclarationTable,
} from "@/components/market/insider-transfer-declaration-table";
import { ExpandModal } from "@/components/ui/expand-modal";
import { QueryState } from "@/components/ui/query-state";
import { getInsiderTransferDeclarations } from "@/lib/api/market";
import { cn } from "@/lib/cn";
import type { Market } from "@/types/settings";

/**
 * 內部人股權轉讓事前申報 — the TW tab's 籌碼 sub-tab card for the whole-market MOPS insider
 * advance-transfer-plan declaration feed (`t187ap12_L` only). Public (`DataClass.PUBLIC`) — no
 * `SponsorTeaser`/masked-teaser branch, same no-blur requirement as
 * `MaterialAnnouncementFeedPanel`.
 *
 * **History grows day by day** — see `MaterialAnnouncementFeedPanel`'s own docstring for why;
 * same forward-only-no-backfill reasoning applies here.
 */
const INSIDER_TRANSFER_DECLARATION_DATE_TAB_COUNT = 7;

export function InsiderTransferDeclarationPanel({
  market,
}: {
  readonly market: Market;
}): ReactNode {
  const t = useTranslations("market.cards.insiderTransferDeclaration");
  const [tradeDate, setTradeDate] = useState<string | undefined>(undefined);
  const [expanded, setExpanded] = useState(false);

  const query = useQuery({
    queryKey: ["insider_transfer_declarations", market, tradeDate],
    queryFn: () => getInsiderTransferDeclarations(market, tradeDate),
  });

  return (
    <section className="surface-card flex w-full flex-col p-4 sm:p-5">
      <h2 className="mb-3 font-semibold text-[15px] text-[var(--text)]">{t("title")}</h2>
      <QueryState query={query} skeleton={<CardSkeletonBody height={560} />}>
        {(data) => {
          if (!data.data_available) {
            return (
              <p className="text-[13px] text-[var(--text-muted)]">
                {data.unavailable_reason ?? t("unavailable")}
              </p>
            );
          }
          return (
            <>
              {data.available_dates.length > 0 ? (
                <div className="flex flex-wrap gap-1.5">
                  {[...data.available_dates]
                    .sort()
                    .reverse()
                    .slice(0, INSIDER_TRANSFER_DECLARATION_DATE_TAB_COUNT)
                    .map((date) => (
                      <button
                        key={date}
                        type="button"
                        onClick={() => setTradeDate(date)}
                        aria-pressed={(tradeDate ?? data.report_date) === date}
                        className={cn(
                          "rounded-[var(--radius-pill)] px-2.5 py-1 font-num text-[11px] tabular-nums transition-colors",
                          (tradeDate ?? data.report_date) === date
                            ? "bg-[var(--accent-weak)] font-semibold text-[var(--accent-text)]"
                            : "bg-[var(--surface-2)] text-[var(--text-muted)] hover:text-[var(--text)]",
                        )}
                      >
                        {date}
                      </button>
                    ))}
                </div>
              ) : null}

              <div className="mt-3">
                <InsiderTransferDeclarationTable
                  rows={data.rows.slice(0, INSIDER_TRANSFER_DECLARATION_COLLAPSED_ROW_COUNT)}
                />
              </div>

              {data.rows.length > INSIDER_TRANSFER_DECLARATION_COLLAPSED_ROW_COUNT ? (
                <button
                  type="button"
                  onClick={() => setExpanded(true)}
                  className={cn(
                    "mt-2.5 flex min-h-11 w-full items-center justify-center gap-1.5 rounded-[var(--radius-input)]",
                    "bg-[var(--surface-2)] text-[12.5px] text-[var(--text-muted)] hover:bg-[var(--surface-3)] hover:text-[var(--text)]",
                  )}
                >
                  <span>{t("expandTo", { count: data.rows.length })}</span>
                </button>
              ) : null}

              <ExpandModal
                open={expanded}
                title={t("title")}
                closeLabel={t("collapse")}
                onClose={() => setExpanded(false)}
              >
                <InsiderTransferDeclarationTable rows={data.rows} />
              </ExpandModal>
            </>
          );
        }}
      </QueryState>
    </section>
  );
}
