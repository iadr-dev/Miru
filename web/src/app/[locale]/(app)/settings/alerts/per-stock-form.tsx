"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useFormatter, useTranslations } from "next-intl";
import { type FormEvent, type ReactNode, useEffect, useMemo, useState } from "react";
import { Select, TextInput } from "@/components/settings/form-controls";
import { Button } from "@/components/ui/button";
import {
  buildBatch,
  fillBlankRows,
  type PerStockRow,
  quickFillLevel,
  type RowComparator,
} from "@/lib/alerts/batch";
import { getPriceHistory } from "@/lib/api/market";
import { createAlertsBatch, SettingsApiError } from "@/lib/api/settings";
import { getWatchlists } from "@/lib/api/watchlist";
import { FRESHNESS } from "@/lib/freshness";
import { groupNameStyle } from "@/lib/watchlist-color";
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

/** Latest close per `market:symbol`, read in batches of 20 (the price endpoint's limit). */
async function loadCloses(items: readonly WatchlistItem[]): Promise<Record<string, number>> {
  const out: Record<string, number> = {};
  for (const market of ["TW", "US"] as const) {
    const symbols = items.filter((i) => i.market === market).map((i) => i.symbol);
    for (let i = 0; i < symbols.length; i += PRICE_BATCH) {
      const history = await getPriceHistory(symbols.slice(i, i + PRICE_BATCH), market, 5);
      const latest = new Map<string, { date: string; close: number }>();
      for (const bar of history.rows) {
        const seen = latest.get(bar.symbol);
        if (!seen || bar.trade_date > seen.date) {
          latest.set(bar.symbol, { date: bar.trade_date, close: bar.close });
        }
      }
      for (const [symbol, bar] of latest) {
        out[`${market}:${symbol}`] = bar.close;
      }
    }
  }
  return out;
}

/** 逐檔設定 — a price alert for each stock of a watchlist group, each with its own level. A group
 * cannot share one price (2,475 vs 185), so the sheet lists the members with their latest close for
 * reference; a row left empty is skipped, and quick-fill offsets each stock from its own close.
 * Submitting creates one ordinary alert per filled row, all or nothing. */
export function PerStockForm({
  onDone,
  initialGroupId,
}: {
  onDone: () => void;
  initialGroupId?: string;
}): ReactNode {
  const t = useTranslations("settings.alerts.perStock");
  const tf = useTranslations("settings.alerts.form");
  const format = useFormatter();
  const queryClient = useQueryClient();

  const groups = useQuery({
    queryKey: ["settings", "alert-watchlists"],
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
    queryKey: ["settings", "alert-closes", groupId, members.length],
    queryFn: () => loadCloses(members),
    enabled: members.length > 0,
    staleTime: FRESHNESS.slow,
  });

  // Rebuild the sheet when the group (or its reference closes) change; keep what was typed.
  useEffect(() => {
    setRows((previous) =>
      members.flatMap((item) => {
        const market = asMarket(item.market);
        if (market === null) {
          return [];
        }
        const kept = previous.find((r) => r.symbol === item.symbol && r.market === market);
        return [
          {
            symbol: item.symbol,
            market,
            close: closes.data?.[`${market}:${item.symbol}`] ?? null,
            comparator: kept?.comparator ?? "above",
            level: kept?.level ?? "",
          },
        ];
      }),
    );
  }, [members, closes.data]);

  const mutation = useMutation({
    mutationFn: createAlertsBatch,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["settings", "alerts"] });
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

  let errorText: string | null = null;
  if (mutation.isError) {
    const code = mutation.error instanceof SettingsApiError ? mutation.error.code : "";
    errorText = code === "cap_reached" ? t("errorCap") : t("errorGeneric");
  } else if (submitted && !check.ok) {
    errorText =
      check.reason === "invalid"
        ? t("errorInvalid", { symbols: check.badSymbols.join("、") })
        : t("errorNothing");
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
                <option value="above">{tf("comparatorOption.above")}</option>
                <option value="below">{tf("comparatorOption.below")}</option>
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
                  <tr key={`${row.market}:${row.symbol}`}>
                    <td className="py-2 pr-3 font-medium text-[var(--text)]">{row.symbol}</td>
                    <td className="py-2 pr-3 text-right font-num tabular-nums">
                      {fmtClose(row.close)}
                    </td>
                    <td className="py-2 pr-3">
                      <div className="w-28">
                        <Select
                          aria-label={t("rowComparator", { symbol: row.symbol })}
                          value={row.comparator}
                          onChange={(e) =>
                            update(index, { comparator: e.target.value as RowComparator })
                          }
                        >
                          <option value="above">{tf("comparatorOption.above")}</option>
                          <option value="below">{tf("comparatorOption.below")}</option>
                        </Select>
                      </div>
                    </td>
                    <td className="py-2 pr-3">
                      <div className="w-28">
                        <TextInput
                          inputMode="decimal"
                          aria-label={t("rowLevel", { symbol: row.symbol })}
                          value={row.level}
                          onChange={(e) => update(index, { level: e.target.value })}
                          className="font-num tabular-nums"
                        />
                      </div>
                    </td>
                    <td className="py-2">
                      <div className="flex flex-wrap gap-1 whitespace-nowrap">
                        {QUICK_UP.map((p) => (
                          <button
                            key={`up${p}`}
                            type="button"
                            disabled={row.close === null}
                            onClick={() =>
                              update(index, {
                                comparator: "above",
                                level: quickFillLevel(row.close, "above", p),
                              })
                            }
                            className="rounded-full border border-[var(--border)] px-2 py-0.5 text-[12px] text-[var(--text-muted)] hover:text-[var(--text)] disabled:opacity-40"
                          >
                            +{p}%
                          </button>
                        ))}
                        {QUICK_DOWN.map((p) => (
                          <button
                            key={`down${p}`}
                            type="button"
                            disabled={row.close === null}
                            onClick={() =>
                              update(index, {
                                comparator: "below",
                                level: quickFillLevel(row.close, "below", p),
                              })
                            }
                            className="rounded-full border border-[var(--border)] px-2 py-0.5 text-[12px] text-[var(--text-muted)] hover:text-[var(--text)] disabled:opacity-40"
                          >
                            -{p}%
                          </button>
                        ))}
                      </div>
                    </td>
                  </tr>
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
