"use client";

import { useTranslations } from "next-intl";
import { type ReactNode, useMemo, useState } from "react";
import { CardSkeletonBody } from "@/components/market/card-skeleton";
import { CompareChart } from "@/components/stock/compare/compare-chart";
import { CompareLegend } from "@/components/stock/compare/compare-legend";
import { ComparePicker } from "@/components/stock/compare/compare-picker";
import { ChartWithDataSheet } from "@/components/ui/chart-with-data-sheet";
import { QueryFailure } from "@/components/ui/query-state";
import { useComparePeers } from "@/hooks/use-compare-peers";
import { usePriceHistories } from "@/hooks/use-stock-data";
import { useSymbolQuotes } from "@/hooks/use-symbol-quotes";
import { useUrlParams } from "@/hooks/use-url-params";
import { distinctChartColors } from "@/lib/chart-token-hash";
import { cn } from "@/lib/cn";
import {
  addCompareSymbol,
  COMPARE_RANGE_DAYS,
  COMPARE_RANGES,
  type CompareInput,
  type CompareRange,
  DEFAULT_COMPARE_RANGE,
  isCompareSymbol,
  parseCompareSymbols,
  rebaseToCommonStart,
  removeCompareSymbol,
  serializeCompareSymbols,
} from "@/lib/stock-compare";
import type { Market } from "@/types/settings";
import type { SearchHit } from "@/types/symbol-search";

const MIN_SYMBOLS = 2;

function round2(value: number | null): number | null {
  return value === null ? null : Math.round(value * 100) / 100;
}

function RangeChips({
  value,
  onChange,
}: {
  readonly value: CompareRange;
  readonly onChange: (range: CompareRange) => void;
}): ReactNode {
  const t = useTranslations("stock.technical.compare");
  return (
    <fieldset className="m-0 flex min-w-0 gap-1 border-0 p-0">
      <legend className="sr-only">{t("rangeLabel")}</legend>
      {COMPARE_RANGES.map((range) => (
        <button
          key={range}
          type="button"
          aria-pressed={value === range}
          onClick={() => onChange(range)}
          className={cn(
            "min-h-11 min-w-11 rounded-[var(--radius-pill)] px-3 font-medium text-[13px] transition-colors",
            "focus-visible:outline-2 focus-visible:outline-[var(--accent)]",
            value === range
              ? "bg-[var(--accent-weak)] text-[var(--accent-text)]"
              : "text-[var(--text-muted)] hover:text-[var(--text)]",
          )}
        >
          {t(`range.${range}`)}
        </button>
      ))}
    </fieldset>
  );
}

function joinNames(symbols: readonly string[], labels: ReadonlyMap<string, string>): string {
  return symbols.map((symbol) => labels.get(symbol) ?? symbol).join("、");
}

/** 技術分析 > 比較: up to five TW stocks as % change since one shared base date. The list lives in
 * the URL (`?sub=compare&cmp=2330,2454`) so a view can be shared or deep-linked (the watchlist's
 * "compare" action does); with no `cmp` it starts from this stock plus up to two concept-theme
 * peers. */
export function StockComparePanel({
  symbol,
  market,
}: {
  readonly symbol: string;
  readonly market: Market;
}): ReactNode {
  const t = useTranslations("stock.technical.compare");
  const { searchParams, setParams } = useUrlParams();
  const [range, setRange] = useState<CompareRange>(DEFAULT_COMPARE_RANGE);
  const [hidden, setHidden] = useState<ReadonlySet<string>>(new Set());

  const cmpParam = searchParams.get("cmp");
  const urlSymbols = useMemo(() => parseCompareSymbols(cmpParam), [cmpParam]);
  const peers = useComparePeers(symbol, market, urlSymbols === null);
  const symbols = useMemo(
    () => urlSymbols ?? [symbol, ...peers].filter(isCompareSymbol),
    [urlSymbols, symbol, peers],
  );

  const commit = (next: readonly string[]): void => {
    setParams({ sub: "compare", cmp: serializeCompareSymbols(next) });
  };

  const { quotes } = useSymbolQuotes(symbols, market);
  const names = useMemo(
    () => new Map(quotes.flatMap((q) => (q.name === null ? [] : [[q.code, q.name] as const]))),
    [quotes],
  );
  const labels = useMemo(
    () => new Map(symbols.map((s) => [s, names.get(s) ?? ""] as const)),
    [symbols, names],
  );
  const colors = useMemo(() => distinctChartColors(symbols), [symbols]);
  const visible = useMemo(() => new Set(symbols.filter((s) => !hidden.has(s))), [symbols, hidden]);

  const history = usePriceHistories(symbols, market, COMPARE_RANGE_DAYS[range]);
  const result = useMemo(() => {
    const inputs: CompareInput[] = symbols.map((s, index) => ({
      symbol: s,
      bars: (history.histories[index]?.rows ?? [])
        .filter((row) => row.symbol === s)
        .map((row) => ({ date: row.trade_date, close: row.close })),
    }));
    return rebaseToCommonStart(inputs);
  }, [symbols, history.histories]);
  const asOf = useMemo(
    () =>
      history.histories
        .map((h) => h?.end_date ?? "")
        .reduce((latest, date) => (date > latest ? date : latest), ""),
    [history.histories],
  );

  const toggle = (s: string): void => {
    setHidden((prev) => {
      const next = new Set(prev);
      if (next.has(s)) {
        next.delete(s);
      } else {
        next.add(s);
      }
      return next;
    });
  };

  const onAdd = (hit: SearchHit): void => commit(addCompareSymbol(symbols, hit.symbol));

  const sheetRows = useMemo(() => {
    const columnFor = (s: string): string => {
      const name = labels.get(s);
      return name ? `${s} ${name} (%)` : `${s} (%)`;
    };
    return [...result.rows].reverse().map((row) => {
      const out: Record<string, unknown> = { trade_date: row.date };
      for (const s of symbols) {
        if (!result.missing.includes(s)) {
          out[columnFor(s)] = round2(row.pct[s] ?? null);
        }
      }
      return out;
    });
  }, [result, symbols, labels]);

  const needMore = symbols.length < MIN_SYMBOLS;
  const drawable = result.rows.length >= 2;
  const baseDate = result.baseDate ?? "";

  let body: ReactNode;
  if (needMore) {
    body = <p className="mt-3 text-[13px] text-[var(--text-muted)]">{t("needMore")}</p>;
  } else if (history.isError) {
    body = (
      <div className="mt-3">
        <QueryFailure query={{ error: history.error, refetch: history.retry }} />
      </div>
    );
  } else if (history.isLoading) {
    body = (
      <section className="surface-card mt-3 p-4 sm:p-5">
        <CardSkeletonBody height={420} />
      </section>
    );
  } else if (drawable) {
    body = (
      <ChartWithDataSheet rows={sheetRows}>
        <CompareChart
          rows={result.rows}
          symbols={symbols}
          visible={visible}
          colors={colors}
          labels={labels}
          range={range}
          ariaLabel={t("chartLabel", { date: baseDate })}
        />
        <CompareLegend
          series={result.series}
          visible={visible}
          colors={colors}
          labels={labels}
          onToggle={toggle}
        />
        <div className="mt-3 flex flex-col gap-1 text-[12px] text-[var(--text-muted)]">
          <p>{t("baseNote", { date: baseDate })}</p>
          {result.limitedBy.length > 0 ? (
            <p>
              {t("limitedNote", { names: joinNames(result.limitedBy, labels), date: baseDate })}
            </p>
          ) : null}
          {result.missing.length > 0 ? (
            <p>{t("missingNote", { names: joinNames(result.missing, labels) })}</p>
          ) : null}
        </div>
      </ChartWithDataSheet>
    );
  } else {
    body = <p className="mt-3 text-[13px] text-[var(--text-muted)]">{t("tooFew")}</p>;
  }

  return (
    <div className="@container flex flex-col">
      <section className="surface-card p-4 sm:p-5">
        <div className="mb-2 flex flex-wrap items-center justify-between gap-3">
          <h2 className="font-semibold text-[15px] text-[var(--text)]">{t("title")}</h2>
          <RangeChips value={range} onChange={setRange} />
        </div>
        <p className="mb-3 text-[13px] text-[var(--text-muted)]">{t("hint")}</p>
        <ComparePicker
          symbols={symbols}
          names={names}
          colors={colors}
          onAdd={onAdd}
          onRemove={(s) => commit(removeCompareSymbol(symbols, s))}
        />
      </section>

      {body}

      <div>
        {asOf === "" ? null : (
          <p className="mt-2 text-[12px] text-[var(--text-muted)]">{t("asOf", { date: asOf })}</p>
        )}
      </div>
    </div>
  );
}
