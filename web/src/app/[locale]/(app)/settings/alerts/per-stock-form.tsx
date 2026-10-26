"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useFormatter, useTranslations } from "next-intl";
import { type FormEvent, type ReactNode, useEffect, useMemo, useState } from "react";
import { Select, TextInput } from "@/components/settings/form-controls";
import { Button } from "@/components/ui/button";
import {
  type BatchCheck,
  buildBatch,
  fillBlankRows,
  type PerStockRow,
  quickFillLevel,
  type RowComparator,
} from "@/lib/alerts/batch";
import { getPriceHistory } from "@/lib/api/market";
import { queryKeys } from "@/lib/api/query-keys";
import { createAlertsBatch, SettingsApiError } from "@/lib/api/settings";
import { getWatchlists } from "@/lib/api/watchlist";
import { FRESHNESS } from "@/lib/freshness";
import { groupNameStyle } from "@/lib/watchlist-color";
import type { PriceHistoryBar } from "@/types/market/quotes";
import type { WatchlistItem } from "@/types/watchlist";

/** The most stocks one batch can hold (mirrors the backend's `_MAX_BATCH`). */
const MAX_ROWS = 30;
const PRICE_BATCH = 20;
const QUICK_UP = [5, 10] as const;
const QUICK_DOWN = [5] as const;
const BULK_PERCENTS = [3, 5, 10] as const;

type Market = "TW" | "US";

function asMarket(value: string): Market | null {
  return value === "TW" || value === "US" ? value : null;
}

/** The newest bar's close per symbol. */
function latestCloseBySymbol(bars: readonly PriceHistoryBar[]): Map<string, number> {
  const latest = new Map<string, { date: string; close: number }>();
  for (const bar of bars) {
    const seen = latest.get(bar.symbol);
    if (!seen || bar.trade_date > seen.date) {
      latest.set(bar.symbol, { date: bar.trade_date, close: bar.close });
    }
  }
  return new Map(Array.from(latest, ([symbol, bar]) => [symbol, bar.close]));
}

/** Latest close per `market:symbol`, read in batches of 20 (the price endpoint's limit). */
async function loadCloses(items: readonly WatchlistItem[]): Promise<Record<string, number>> {
  const out: Record<string, number> = {};
  for (const market of ["TW", "US"] as const) {
    const symbols = items.filter((i) => i.market === market).map((i) => i.symbol);
    for (let i = 0; i < symbols.length; i += PRICE_BATCH) {
      const history = await getPriceHistory(symbols.slice(i, i + PRICE_BATCH), market, 5);
      for (const [symbol, close] of latestCloseBySymbol(history.rows)) {
        out[`${market}:${symbol}`] = close;
      }
    }
  }
  return out;
}

/** The sheet rows for the group's members, keeping whatever was already typed for a stock. */
function buildRows(
  members: readonly WatchlistItem[],
  previous: readonly PerStockRow[],
  closes: Record<string, number> | undefined,
): PerStockRow[] {
  return members.flatMap((item) => {
    const market = asMarket(item.market);
    if (market === null) {
      return [];
    }
    const kept = previous.find((r) => r.symbol === item.symbol && r.market === market);
    return [
      {
        symbol: item.symbol,
        market,
        close: closes?.[`${market}:${item.symbol}`] ?? null,
        comparator: kept?.comparator ?? "above",
        level: kept?.level ?? "",
      },
    ];
  });
}

type ErrorKind = "cap" | "generic" | "invalid" | "nothing";

/** Which error line (if any) the sheet shows: a failed save wins over local validation. */
function resolveErrorKind(
  save: { failed: boolean; error: unknown },
  submitted: boolean,
  check: BatchCheck,
): ErrorKind | null {
  if (save.failed) {
    const code = save.error instanceof SettingsApiError ? save.error.code : "";
    return code === "cap_reached" ? "cap" : "generic";
  }
  if (submitted && !check.ok) {
    return check.reason === "invalid" ? "invalid" : "nothing";
  }
  return null;
}

const QUICK_FILL_CLASS =
  "rounded-full border border-[var(--border)] px-2 py-0.5 text-[12px] text-[var(--text-muted)] hover:text-[var(--text)] disabled:opacity-40";

function ComparatorOptions(): ReactNode {
  const tf = useTranslations("settings.alerts.form");
  return (
    <>
      <option value="above">{tf("comparatorOption.above")}</option>
      <option value="below">{tf("comparatorOption.below")}</option>
    </>
  );
}

function StockRowView({
  row,
  closeLabel,
  onChange,
}: {
  row: PerStockRow;
  closeLabel: string;
  onChange: (patch: Partial<PerStockRow>) => void;
}): ReactNode {
  const t = useTranslations("settings.alerts.perStock");
  const quick = (
    key: string,
    comparator: RowComparator,
    percent: number,
    sign: "+" | "-",
  ): ReactNode => (
    <button
      key={key}
      type="button"
      disabled={row.close === null}
      onClick={() =>
        onChange({ comparator, level: quickFillLevel(row.close, comparator, percent) })
      }
      className={QUICK_FILL_CLASS}
    >
      {sign}
      {percent}%
    </button>
  );
  return (
    <tr>
      <td className="py-2 pr-3 font-medium text-[var(--text)]">{row.symbol}</td>
      <td className="py-2 pr-3 text-right font-num tabular-nums">{closeLabel}</td>
      <td className="py-2 pr-3">
        <div className="w-28">
          <Select
            aria-label={t("rowComparator", { symbol: row.symbol })}
            value={row.comparator}
            onChange={(e) => onChange({ comparator: e.target.value as RowComparator })}
          >
            <ComparatorOptions />
          </Select>
        </div>
      </td>
      <td className="py-2 pr-3">
        <div className="w-28">
          <TextInput
            inputMode="decimal"
            aria-label={t("rowLevel", { symbol: row.symbol })}
            value={row.level}
            onChange={(e) => onChange({ level: e.target.value })}
            className="font-num tabular-nums"
          />
        </div>
      </td>
      <td className="py-2">
        <div className="flex flex-wrap gap-1 whitespace-nowrap">
          {QUICK_UP.map((p) => quick(`up${p}`, "above", p, "+"))}
          {QUICK_DOWN.map((p) => quick(`down${p}`, "below", p, "-"))}
        </div>
      </td>
    </tr>
  );
}

/** 逐檔設定 — a price alert for each stock of a watchlist group, each with its own level. A group
 * cannot share one price (2,475 vs 185), so the sheet lists the members with their latest close for
 * reference; a row left empty is skipped, and quick-fill offsets each stock from its own close.
 * Submitting creates one ordinary alert per filled row, all or nothing. */
export function PerStockForm({
  onDone,
  initialGroupId,
}: Readonly<{
  onDone: () => void;
  initialGroupId?: string | undefined;
}>): ReactNode {
  const t = useTranslations("settings.alerts.perStock");
  const tf = useTranslations("settings.alerts.form");
  const format = useFormatter();
  const queryClient = useQueryClient();

  const groups = useQuery({
    queryKey: queryKeys.settings("alert-watchlists"),
    queryFn: () => getWatchlists(),
  });
  const [groupId, setGroupId] = useState(initialGroupId ?? "");
  const [rows, setRows] = useState<PerStockRow[]>([]);
  const [bulkComparator, setBulkComparator] = useState<RowComparator>("above");
  const [bulkPercent, setBulkPercent] = useState<number>(5);
  const [triggerMode, setTriggerMode] = useState<"once" | "once_per_day" | "every_occurrence">(
    "once_per_day",
  );
  const [expiry, setExpiry] = useState<"30" | "90" | "365" | "never">("90");
  const [submitted, setSubmitted] = useState(false);

  const view = (groups.data ?? []).find((g) => g.group.id === groupId);
  const members = useMemo(() => (view?.items ?? []).slice(0, MAX_ROWS), [view]);
  const truncated = (view?.items.length ?? 0) > MAX_ROWS;

  const closes = useQuery({
    queryKey: queryKeys.settings("alert-closes", groupId, members.length),
    queryFn: () => loadCloses(members),
    enabled: members.length > 0,
    staleTime: FRESHNESS.slow,
  });

  // Rebuild the sheet when the group (or its reference closes) change; keep what was typed.
  useEffect(() => {
    setRows((previous) => buildRows(members, previous, closes.data));
  }, [members, closes.data]);

  const mutation = useMutation({
    mutationFn: createAlertsBatch,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.settings("alerts") });
      onDone();
    },
  });

  const update = (index: number, patch: Partial<PerStockRow>): void => {
    setRows((prev) => prev.map((row, i) => (i === index ? { ...row, ...patch } : row)));
  };

  const check = buildBatch(rows);
  const onSubmit = (e: FormEvent<HTMLFormElement>): void => {
    e.preventDefault();
    setSubmitted(true);
    if (!check.ok || groupId === "") {
      return;
    }
    mutation.mutate({
      items: check.items,
      origin_watchlist_id: groupId,
      trigger_mode: triggerMode,
      expires_in_days: expiry === "never" ? null : Number(expiry),
    });
  };

  const fmtClose = (close: number | null): string =>
    close === null ? "—" : format.number(close, { maximumFractionDigits: 2 });
  const filledCount = check.ok
    ? check.items.length
    : rows.filter((r) => r.level.trim() !== "").length;

  const errorKind = resolveErrorKind(
    { failed: mutation.isError, error: mutation.error },
    submitted,
    check,
  );
  let errorText: string | null = null;
  if (errorKind === "cap") errorText = t("errorCap");
  else if (errorKind === "generic") errorText = t("errorGeneric");
  else if (errorKind === "nothing") errorText = t("errorNothing");
  else if (errorKind === "invalid" && !check.ok) {
    errorText = t("errorInvalid", { symbols: check.badSymbols.join("、") });
  }

  return (
    <form
      aria-label={t("title")}
      onSubmit={onSubmit}
      onKeyDown={(e) => {
        if (e.key === "Escape") {
          e.stopPropagation();
          onDone();
        }
      }}
      className="space-y-4 rounded-[var(--radius-input)] border border-[var(--border)] bg-[var(--surface-2)] p-4"
    >
      <div>
        <label className="mb-1 block text-[13px] text-[var(--text-muted)]" htmlFor="perstock-group">
          {t("group")}
        </label>
        <Select id="perstock-group" value={groupId} onChange={(e) => setGroupId(e.target.value)}>
          <option value="">{t("groupPlaceholder")}</option>
          {(groups.data ?? []).map((g) => (
            <option key={g.group.id} value={g.group.id} style={groupNameStyle(g.group)}>
              {g.group.name}
            </option>
          ))}
        </Select>
        <p className="mt-1 text-[12px] text-[var(--text-muted)]">{t("help")}</p>
      </div>

      {groupId !== "" && members.length === 0 ? (
        <p className="text-[13px] text-[var(--text-muted)]">{t("empty")}</p>
      ) : null}

      {rows.length > 0 ? (
        <>
          <div className="flex flex-wrap items-center gap-2 text-[13px]">
            <span className="text-[var(--text-muted)]">{t("bulkLabel")}</span>
            <div className="w-28">
              <Select
                aria-label={t("bulkComparator")}
                value={bulkComparator}
                onChange={(e) => setBulkComparator(e.target.value as RowComparator)}
              >
                <ComparatorOptions />
              </Select>
            </div>
            <span className="text-[var(--text-muted)]">{t("bulkFromClose")}</span>
            <div className="w-24">
              <Select
                aria-label={t("bulkPercent")}
                value={String(bulkPercent)}
                onChange={(e) => setBulkPercent(Number(e.target.value))}
              >
                {BULK_PERCENTS.map((p) => (
                  <option key={p} value={p}>
                    {bulkComparator === "above" ? `+${p}%` : `-${p}%`}
                  </option>
                ))}
              </Select>
            </div>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => setRows((prev) => fillBlankRows(prev, bulkComparator, bulkPercent))}
            >
              {t("bulkApply")}
            </Button>
          </div>

          <div className="overflow-x-auto">
            <table className="w-full text-left text-[13px]">
              <caption className="sr-only">{t("title")}</caption>
              <thead className="text-[var(--text-muted)]">
                <tr>
                  <th scope="col" className="py-1 pr-3 font-normal">
                    {t("colSymbol")}
                  </th>
                  <th scope="col" className="py-1 pr-3 text-right font-normal">
                    {t("colClose")}
                  </th>
                  <th scope="col" className="py-1 pr-3 font-normal">
                    {t("colCondition")}
                  </th>
                  <th scope="col" className="py-1 pr-3 font-normal">
                    {t("colLevel")}
                  </th>
                  <th scope="col" className="py-1 font-normal">
                    {t("colQuick")}
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[var(--border)]">
                {rows.map((row, index) => (
                  <StockRowView
                    key={`${row.market}:${row.symbol}`}
                    row={row}
                    closeLabel={fmtClose(row.close)}
                    onChange={(patch) => update(index, patch)}
                  />
                ))}
              </tbody>
            </table>
          </div>
          <p className="text-[12px] text-[var(--text-muted)]">{t("closeNote")}</p>
          {truncated ? (
            <p className="text-[12px] text-[var(--text-muted)]">
              {t("truncated", { max: MAX_ROWS })}
            </p>
          ) : null}

          <fieldset className="grid gap-3 sm:grid-cols-2">
            <div>
              <label
                className="mb-1 block text-[13px] text-[var(--text-muted)]"
                htmlFor="perstock-mode"
              >
                {tf("triggerMode")}
              </label>
              <Select
                id="perstock-mode"
                value={triggerMode}
                onChange={(e) => setTriggerMode(e.target.value as typeof triggerMode)}
              >
                <option value="once">{tf("triggerModeOption.once")}</option>
                <option value="once_per_day">{tf("triggerModeOption.once_per_day")}</option>
                <option value="every_occurrence">{tf("triggerModeOption.every_occurrence")}</option>
              </Select>
            </div>
            <div>
              <label
                className="mb-1 block text-[13px] text-[var(--text-muted)]"
                htmlFor="perstock-expiry"
              >
                {tf("expiry")}
              </label>
              <Select
                id="perstock-expiry"
                value={expiry}
                onChange={(e) => setExpiry(e.target.value as typeof expiry)}
              >
                {(["30", "90", "365", "never"] as const).map((choice) => (
                  <option key={choice} value={choice}>
                    {tf(`expiryOption.${choice}`)}
                  </option>
                ))}
              </Select>
            </div>
          </fieldset>
        </>
      ) : null}

      {errorText ? (
        <p role="alert" className="text-[13px] text-[var(--warn)]">
          {errorText}
        </p>
      ) : null}
      <div className="flex justify-end gap-2">
        <Button type="button" variant="ghost" size="sm" onClick={onDone}>
          {t("cancel")}
        </Button>
        <Button
          type="submit"
          variant="filled"
          size="sm"
          disabled={mutation.isPending || rows.length === 0}
        >
          {t("submit", { count: filledCount })}
        </Button>
      </div>
    </form>
  );
}
