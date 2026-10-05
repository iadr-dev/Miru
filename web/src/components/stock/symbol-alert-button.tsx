"use client";

import { BellIcon, BellRingingIcon, CheckIcon, PlusIcon } from "@phosphor-icons/react";
import * as Popover from "@radix-ui/react-popover";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { type FormEvent, type ReactNode, useState } from "react";
import { Select, TextInput } from "@/components/settings/form-controls";
import { Link } from "@/i18n/navigation";
import {
  buildCondition,
  conditionSummary,
  DEFAULT_DRAFT,
  sameCondition,
} from "@/lib/alerts/condition";
import { activeRuleCount, presetsFor, rulesForSymbol } from "@/lib/alerts/presets";
import { createAlert, listAlerts, SettingsApiError } from "@/lib/api/settings";
import { cn } from "@/lib/cn";
import type { AlertCondition, AlertMarket, Market } from "@/types/settings";

const ALERTS_KEY = ["settings", "alerts"] as const;
const DEFAULT_LIFETIME_DAYS = 90;

function isAlertMarket(market: Market): market is AlertMarket {
  return market === "TW" || market === "US";
}

/** 🔔 quick-create on the stock/ETF header: a lit bell with a count
 * when this symbol already has active rules, one-click fact presets that exist for this asset, and
 * a price-level form. Everything created here is the same rule the Settings panel manages —
 * nothing is created without a click. Markets other than TW/US have no alert support and render
 * nothing (no dead control). */
export function SymbolAlertButton({
  symbol,
  market,
  isEtf,
}: {
  readonly symbol: string;
  readonly market: Market;
  readonly isEtf: boolean;
}): ReactNode {
  if (!isAlertMarket(market)) {
    return null;
  }
  return <SymbolAlertPopover symbol={symbol} market={market} isEtf={isEtf} />;
}

function SymbolAlertPopover({
  symbol,
  market,
  isEtf,
}: {
  readonly symbol: string;
  readonly market: AlertMarket;
  readonly isEtf: boolean;
}): ReactNode {
  const t = useTranslations("stock.alertButton");
  const ts = useTranslations("settings.alerts.summary");
  const tc = useTranslations("settings.common");
  const queryClient = useQueryClient();

  const [comparator, setComparator] = useState<"above" | "below">("above");
  const [priceInput, setPriceInput] = useState("");
  const [priceError, setPriceError] = useState<"required" | "range" | null>(null);
  const [created, setCreated] = useState<AlertCondition | null>(null);

  const alertsQuery = useQuery({
    queryKey: ALERTS_KEY,
    queryFn: listAlerts,
    staleTime: 30_000,
    retry: false,
  });
  const mine = rulesForSymbol(alertsQuery.data ?? [], symbol, market);
  const activeCount = activeRuleCount(mine);

  const create = useMutation({
    mutationFn: (condition: AlertCondition) =>
      createAlert({ symbol, market, condition, expires_in_days: DEFAULT_LIFETIME_DAYS }),
    onSuccess: (_rule, condition) => {
      setCreated(condition);
      void queryClient.invalidateQueries({ queryKey: ALERTS_KEY });
    },
  });

  const describe = (condition: AlertCondition): string => {
    const summary = conditionSummary(condition);
    return ts(summary.key, summary.values);
  };

  const addPreset = (condition: AlertCondition): void => {
    setCreated(null);
    create.mutate(condition);
  };

  const onPriceSubmit = (e: FormEvent<HTMLFormElement>): void => {
    e.preventDefault();
    const built = buildCondition({
      ...DEFAULT_DRAFT,
      kind: "close_price",
      comparator,
      value: priceInput,
    });
    if (!built.ok) {
      setPriceError(built.errors.value ?? "range");
      return;
    }
    setPriceError(null);
    setCreated(null);
    create.mutate(built.condition, { onSuccess: () => setPriceInput("") });
  };

  const label = activeCount > 0 ? t("labelActive", { count: activeCount }) : t("label");
  const Icon = activeCount > 0 ? BellRingingIcon : BellIcon;

  return (
    <Popover.Root>
      <Popover.Trigger asChild>
        <button
          type="button"
          aria-label={label}
          title={label}
          className={cn(
            "relative inline-flex size-11 items-center justify-center rounded-[var(--radius-button)]",
            "transition-colors hover:bg-[var(--surface-2)]",
            "!outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]",
            activeCount > 0 ? "text-[var(--accent)]" : "text-[var(--text-muted)]",
          )}
        >
          <Icon size={18} weight={activeCount > 0 ? "fill" : "regular"} aria-hidden />
          {activeCount > 0 ? (
            <span
              aria-hidden
              className="absolute top-1.5 right-1.5 inline-flex min-w-4 items-center justify-center rounded-full bg-[var(--accent)] px-1 font-num text-[10px] text-[var(--bg)] leading-4"
            >
              {activeCount}
            </span>
          ) : null}
        </button>
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Content
          align="end"
          sideOffset={8}
          className="z-50 w-80 rounded-[var(--radius-card)] border border-[var(--border)] bg-[var(--surface)] p-4 shadow-lg"
        >
          <p className="font-medium text-[14px] text-[var(--text)]">{t("title", { symbol })}</p>

          {mine.length > 0 ? (
            <section className="mt-3" aria-label={t("existing")}>
              <p className="text-[12px] text-[var(--text-muted)]">{t("existing")}</p>
              <ul className="mt-1 space-y-1">
                {mine.slice(0, 5).map((rule) => (
                  <li
                    key={rule.id}
                    className={cn(
                      "text-[13px]",
                      rule.enabled ? "text-[var(--text)]" : "text-[var(--text-muted)]",
                    )}
                  >
                    {describe(rule.condition)}
                  </li>
                ))}
              </ul>
            </section>
          ) : null}

          <section className="mt-3" aria-label={t("quickAdd")}>
            <p className="text-[12px] text-[var(--text-muted)]">{t("quickAdd")}</p>
            <ul className="mt-1 flex flex-wrap gap-2">
              {presetsFor(market, isEtf).map((preset) => {
                const exists = mine.some((r) => r.enabled && sameCondition(r.condition, preset));
                return (
                  <li key={describe(preset)}>
                    <button
                      type="button"
                      disabled={exists || create.isPending}
                      onClick={() => addPreset(preset)}
                      title={exists ? t("alreadySet") : undefined}
                      className={cn(
                        "inline-flex min-h-11 items-center gap-1.5 rounded-[var(--radius-pill)] border px-3 text-[12px]",
                        "border-[var(--border)] text-[var(--text)] transition-colors hover:bg-[var(--surface-2)]",
                        "!outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] disabled:opacity-60",
                      )}
                    >
                      {exists ? (
                        <CheckIcon size={14} weight="regular" aria-hidden />
                      ) : (
                        <PlusIcon size={14} weight="regular" aria-hidden />
                      )}
                      {describe(preset)}
                    </button>
                  </li>
                );
              })}
            </ul>
          </section>

          <form onSubmit={onPriceSubmit} className="mt-3" aria-label={t("priceTitle")}>
            <p className="text-[12px] text-[var(--text-muted)]">{t("priceTitle")}</p>
            <div className="mt-1 flex items-start gap-2">
              {/* Width comes from a wrapper, not a class on `Select`: this codebase's `cn` does not
                  merge conflicting Tailwind classes, so a `w-24` there loses to the built-in
                  `w-full` and the select swallowed the whole row. */}
              <div className="w-24 shrink-0">
                <Select
                  aria-label={t("comparator")}
                  value={comparator}
                  onChange={(e) => setComparator(e.target.value as "above" | "below")}
                >
                  <option value="above">{t("above")}</option>
                  <option value="below">{t("below")}</option>
                </Select>
              </div>
              <div className="min-w-0 flex-1">
                <TextInput
                  type="number"
                  inputMode="decimal"
                  step="any"
                  aria-label={t("priceValue")}
                  value={priceInput}
                  onChange={(e) => setPriceInput(e.target.value)}
                  aria-invalid={priceError ? true : undefined}
                />
              </div>
              <button
                type="submit"
                disabled={create.isPending}
                className="inline-flex min-h-11 shrink-0 items-center whitespace-nowrap rounded-[var(--radius-button)] bg-[var(--accent)] px-3 text-[13px] text-[var(--bg)] disabled:opacity-60"
              >
                {t("add")}
              </button>
            </div>
            {priceError ? (
              <p role="alert" className="mt-1 text-[12px] text-[var(--warn)]">
                {priceError === "required" ? t("errorRequired") : t("errorRange")}
              </p>
            ) : null}
          </form>

          <div aria-live="polite" className="mt-3 min-h-5 text-[12px]">
            {created ? (
              <p className="text-[var(--accent-text)]">
                {t("created", { summary: describe(created) })}
              </p>
            ) : null}
            {create.isError ? (
              <p role="alert" className="text-[var(--warn)]">
                {create.error instanceof SettingsApiError && create.error.code === "cap_reached"
                  ? t("errorCap")
                  : tc("errorGeneric")}
              </p>
            ) : null}
          </div>

          <div className="mt-2 flex items-center justify-between gap-3 border-[var(--border)] border-t pt-3">
            <p className="text-[11px] text-[var(--text-muted)] leading-snug">{t("caveat")}</p>
            <Link
              href="/settings/alerts"
              className="inline-flex min-h-11 shrink-0 items-center text-[12px] text-[var(--accent-text)] hover:underline"
            >
              {t("manage")}
            </Link>
          </div>
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}
