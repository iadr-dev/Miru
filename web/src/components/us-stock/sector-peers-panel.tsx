"use client";

import { useQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { CardSkeletonBody } from "@/components/market/card-skeleton";
import { SymbolLink } from "@/components/market/symbol-link";
import { getStockPeers } from "@/lib/api/stock-detail";
import type { Market } from "@/types/settings";
import type { IndustryOverviewCompanyRow, SectorOverviewCompanyRow } from "@/types/stock-detail";

const PEER_ROW_LIMIT = 10;

/** 產業同業 tab — one symbol's sector + industry peer companies (`get_stock_peers`, which itself
 * resolves the symbol's own sector/industry classification then fetches both peer lists in one
 * call). Genuinely new: no TW-detail-page precedent exists for this — TW's own 產業分析 tab
 * (`industry-panel.tsx`) is built on curated theme/supply-chain datasets that don't apply
 * outside TW, not the same underlying capability as this panel. */
export function SectorPeersPanel({
  symbol,
  market,
}: {
  readonly symbol: string;
  readonly market: Market;
}): ReactNode {
  const t = useTranslations("usStock.sectorPeers");
  const query = useQuery({
    queryKey: ["stock_peers", symbol, market],
    queryFn: () => getStockPeers(symbol, market),
  });

  if (query.isLoading) {
    return (
      <section className="rounded-[var(--radius-card)] border border-[var(--border)] bg-[var(--surface)] p-5">
        <CardSkeletonBody height={420} />
      </section>
    );
  }
  const data = query.data;
  if (!data) return null;

  if (
    data.unavailable_reason &&
    data.sector_peers.length === 0 &&
    data.industry_peers.length === 0
  ) {
    return (
      <section className="rounded-[var(--radius-card)] border border-[var(--border)] bg-[var(--surface)] p-5">
        <h2 className="mb-1 font-semibold text-[15px] text-[var(--text)]">{t("title")}</h2>
        <p className="mt-3 text-[13px] text-[var(--text-muted)]">{t("unavailable")}</p>
      </section>
    );
  }

  return (
    <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
      <PeerCard
        title={data.sector ? t("sectorTitle", { sector: data.sector }) : t("sectorTitleFallback")}
        rows={data.sector_peers}
        symbol={symbol}
        market={market}
        emptyLabel={t("noSectorPeers")}
      />
      <PeerCard
        title={
          data.industry
            ? t("industryTitle", { industry: data.industry })
            : t("industryTitleFallback")
        }
        rows={data.industry_peers}
        symbol={symbol}
        market={market}
        emptyLabel={t("noIndustryPeers")}
      />
    </div>
  );
}

function PeerCard({
  title,
  rows,
  symbol,
  market,
  emptyLabel,
}: {
  readonly title: string;
  readonly rows: readonly (SectorOverviewCompanyRow | IndustryOverviewCompanyRow)[];
  readonly symbol: string;
  readonly market: Market;
  readonly emptyLabel: string;
}): ReactNode {
  const t = useTranslations("usStock.sectorPeers");
  const companyRows = rows.filter((row) => row.row_kind === "company").slice(0, PEER_ROW_LIMIT);

  return (
    <section className="rounded-[var(--radius-card)] border border-[var(--border)] bg-[var(--surface)] p-5">
      <h2 className="mb-3 font-semibold text-[15px] text-[var(--text)]">{title}</h2>
      {companyRows.length === 0 ? (
        <p className="text-[13px] text-[var(--text-muted)]">{emptyLabel}</p>
      ) : (
        <ul className="flex flex-col gap-2.5">
          {companyRows.map((row) => (
            <li
              key={row.symbol}
              className="flex items-center justify-between gap-3 border-[var(--border)] border-b pb-2 last:border-b-0 last:pb-0"
            >
              <SymbolLink
                symbol={row.symbol}
                market={market}
                className="min-w-0 truncate text-[13px] text-[var(--text)] hover:underline"
              >
                {row.name || row.symbol}
              </SymbolLink>
              {row.symbol === symbol ? (
                <span className="shrink-0 rounded-[var(--radius-pill)] bg-[var(--accent)]/15 px-2 py-0.5 text-[11px] text-[var(--accent)]">
                  {t("self")}
                </span>
              ) : row.rating ? (
                <span className="shrink-0 text-[11px] text-[var(--text-muted)]">{row.rating}</span>
              ) : null}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
