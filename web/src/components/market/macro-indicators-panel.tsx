"use client";

import { useQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { CardSkeletonBody } from "@/components/market/card-skeleton";
import { QueryState } from "@/components/ui/query-state";
import { getMacroIndicators } from "@/lib/api/market";
import { queryKeys } from "@/lib/api/query-keys";
import { cn } from "@/lib/cn";
import type { MacroIndicatorsData } from "@/types/market/macro";

const FED_COUNTRY_CODE = "FED";
// `symbol` on both rows is FinMind's own free-text `name` field (e.g. "WTI"/"Brent",
// "United States 10-Year"/"United States 2-Year") — not a fixed enum, so this only picks a
// specific instrument when its name happens to contain these substrings; otherwise falls back to
// the most recent row of whichever instrument the feed returned, per `MacroIndicatorsGrid`'s own
// fallback below (never fabricated — always a real returned row, just not guaranteed to be THE
// benchmark tenor/crude grade if the feed's own naming varies).
const WTI_NAME_HINT = "WTI";
const TEN_YEAR_NAME_HINT = "10-Year";

/** 總經 sub-tab's macro-indicators card — the first-ever frontend consumer of
 * `get_macro_indicators` (client fn + Zod schema already existed, unused until now). Shows the
 * fields genuinely relevant to a US macro tab (US Treasury yield curve, Fed funds rate via
 * `central_bank_rate` filtered to the US/`FED` row, gold, crude oil) — `exchange_rate` is
 * TW-scoped (TWD vs. foreign currencies) and skipped here as irrelevant. Every field shown is
 * public data, so every tier sees the card (only the unshown Fear & Greed Index is Sponsor). */
export function MacroIndicatorsPanel(): ReactNode {
  const t = useTranslations("market.cards.macro");
  const query = useQuery({
    queryKey: queryKeys.read("macro_indicators"),
    queryFn: () => getMacroIndicators(),
  });

  return (
    <section className="surface-card flex w-full flex-col p-4 sm:p-5">
      <QueryState query={query} skeleton={<CardSkeletonBody height={280} />}>
        {(data) => (
          <>
            <h2 className="mb-3 font-semibold text-[15px] text-[var(--text)]">{t("title")}</h2>
            <MacroIndicatorsGrid data={data} t={t} />
          </>
        )}
      </QueryState>
    </section>
  );
}

function MacroIndicatorsGrid({
  data,
  t,
}: {
  readonly data: MacroIndicatorsData;
  readonly t: ReturnType<typeof useTranslations>;
}): ReactNode {
  const fedRate = data.central_bank_rate.find((row) => row.country === FED_COUNTRY_CODE);
  const tenYearRows = data.government_bond_yield.filter((row) =>
    row.symbol.includes(TEN_YEAR_NAME_HINT),
  );
  const latestYield = tenYearRows.at(-1) ?? data.government_bond_yield.at(-1);
  const latestGold = data.gold_price.at(-1);
  const wtiRows = data.crude_oil_prices.filter((row) => row.symbol.includes(WTI_NAME_HINT));
  const latestOil = wtiRows.at(-1) ?? data.crude_oil_prices.at(-1);

  return (
    <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
      <MacroStat
        label={t("centralBankRate")}
        value={fedRate ? `${fedRate.interest_rate.toFixed(2)}%` : "—"}
        tint="var(--accent)"
      />
      <MacroStat
        label={t("governmentBondYield")}
        value={latestYield ? `${latestYield.value.toFixed(2)}%` : "—"}
        tint="var(--chart-2)"
      />
      <MacroStat
        label={t("goldPrice")}
        value={latestGold ? latestGold.price.toLocaleString("en-US") : "—"}
        tint="var(--warn)"
      />
      <MacroStat
        label={t("crudeOilPrices")}
        value={latestOil ? latestOil.price.toLocaleString("en-US") : "—"}
        tint="var(--chart-4)"
      />
    </div>
  );
}

/** Frosted-glass stat tile — same `glass` + `color-mix(...)` tint convention
 * `overview-panel.tsx`'s own AUM/YTD-return tiles established (falls back to solid `--surface`
 * under `prefers-reduced-transparency` via the `.glass` class, web/OVERVIEW.md §7). Each of the 4
 * macro stats gets a distinct categorical tint (`--accent`/`--chart-2`/`--warn`/`--chart-4`) —
 * these are context/theme tints, not signed price data, so `--chart-*`/`--warn` here don't
 * collide with the 紅漲綠跌 `--up`/`--down` reservation (web/OVERVIEW.md §4). */
function MacroStat({
  label,
  value,
  tint,
}: {
  readonly label: string;
  readonly value: string;
  readonly tint: string;
}): ReactNode {
  return (
    <div
      className={cn(
        "glass rounded-[var(--radius-input)] p-3",
        "backdrop-blur-[16px] backdrop-saturate-150",
      )}
      style={{ backgroundColor: `color-mix(in srgb, ${tint} 10%, var(--surface) 90%)` }}
    >
      <p className="text-[11px] text-[var(--text-muted)]">{label}</p>
      <p className="mt-1 font-num text-[18px] text-[var(--text)] tabular-nums">{value}</p>
    </div>
  );
}
