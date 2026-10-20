"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { type FormEvent, type KeyboardEvent, type ReactNode, useEffect, useState } from "react";
import { Select, TextInput } from "@/components/settings/form-controls";
import { Button } from "@/components/ui/button";
import {
  buildAlertPatch,
  buildCondition,
  type ConditionDraft,
  type ConditionFieldError,
  type ConditionFieldKey,
  conditionSummary,
  DEFAULT_DRAFT,
  draftFromCondition,
  type ExpiryChoice,
  fieldsFor,
  groupTemplatesFor,
  mergeStaleEdit,
  templatesFor,
} from "@/lib/alerts/condition";
import { queryKeys } from "@/lib/api/query-keys";
import {
  checkAlertSymbol,
  createAlert,
  listAlerts,
  SettingsApiError,
  updateAlert,
} from "@/lib/api/settings";
import { getWatchlists } from "@/lib/api/watchlist";
import { FRESHNESS } from "@/lib/freshness";
import { groupNameStyle } from "@/lib/watchlist-color";
import {
  ALERT_TRIGGER_MODES,
  type AlertRule,
  type AlertTemplateKind,
  type AlertTriggerMode,
  INSTITUTIONAL_INVESTORS,
  MARKETS,
  type Market,
} from "@/types/settings";
import { PerStockForm } from "./per-stock-form";

/** A group rule starts on a scale-free template; the price template is per stock. */
const GROUP_DEFAULT_KIND: AlertTemplateKind = "daily_change";
const CREATE_EXPIRY_OPTIONS: readonly ExpiryChoice[] = ["30", "90", "365", "never"];
/** Editing can also leave the expiry as it is — a rule's exact date is rarely one of the presets. */
const EDIT_EXPIRY_OPTIONS: readonly ExpiryChoice[] = ["keep", ...CREATE_EXPIRY_OPTIONS];

type SaveAction =
  | { mode: "create"; body: Parameters<typeof createAlert>[0] }
  | { mode: "update"; id: string; body: Parameters<typeof updateAlert>[1] };

async function save(action: SaveAction): Promise<unknown> {
  return action.mode === "create" ? createAlert(action.body) : updateAlert(action.id, action.body);
}

/** Which message explains a failed save. `cap_reached` and `stale_version` are the two the user can
 * act on; everything else is the generic retry message for the current mode. */
function errorMessageKey(error: unknown, editing: boolean): string {
  if (error instanceof SettingsApiError) {
    if (error.code === "cap_reached") {
      return "errorCap";
    }
    if (error.code === "stale_version") {
      return "errorStale";
    }
  }
  return editing ? "errorUpdate" : "errorCreate";
}

/** Matches the backend's `MAX_KEYWORD_LENGTH`. */
const KEYWORD_MAX_LENGTH = 20;

const NUMERIC_FIELD_STEP: Record<ConditionFieldKey, string> = {
  value: "any",
  percent: "any",
  multiple: "any",
  window: "1",
  days: "1",
};

/** Alert-rule create/edit form.
 * Template-first — the user picks what to watch and fills only that template's fields — with a
 * fact-phrased preview line and inline, retryable errors. `Esc` cancels. Pass `rule` to edit: the
 * scope (symbol / market) is fixed, only changed fields are sent, and the save carries the rule's
 * `version` so an edit from a stale screen is refused instead of overwriting a newer one. */
export type AlertFormPreset = {
  scope?: "symbol" | "watchlist" | "perStock";
  symbol?: string;
  market?: Market;
  groupId?: string | undefined;
};

export function AlertForm({
  onDone,
  rule,
  preset,
}: Readonly<{
  onDone: () => void;
  rule?: AlertRule;
  preset?: AlertFormPreset | undefined;
}>): ReactNode {
  const t = useTranslations("settings.alerts.form");
  const ts = useTranslations("settings.alerts.summary");
  const tc = useTranslations("settings.common");
  const queryClient = useQueryClient();

  const editing = rule !== undefined;
  // The server copy the edit is diffed against. It moves forward after a 409 is merged.
  const [base, setBase] = useState<AlertRule | undefined>(rule);
  const [notice, setNotice] = useState<"merged" | "conflict" | null>(null);
  const [symbol, setSymbol] = useState(rule?.symbol ?? preset?.symbol ?? "");
  const [market, setMarket] = useState<Market>(
    rule?.market === "US" || rule?.market === "TW" ? rule.market : (preset?.market ?? "TW"),
  );
  const presetDraft: ConditionDraft =
    preset?.scope === "watchlist" ? { ...DEFAULT_DRAFT, kind: GROUP_DEFAULT_KIND } : DEFAULT_DRAFT;
  const [draft, setDraft] = useState<ConditionDraft>(
    rule ? draftFromCondition(rule.condition) : presetDraft,
  );
  const [triggerMode, setTriggerMode] = useState<AlertTriggerMode>(
    rule?.trigger_mode ?? "once_per_day",
  );
  const [expiry, setExpiry] = useState<ExpiryChoice>(editing ? "keep" : "90");
  // A rule follows one symbol, or every stock in one of the user's watchlist groups (the scope is
  // fixed once created). A group can hold both markets, so a group rule offers only the templates
  // that work for both.
  const ruleScope = rule?.symbol ? "symbol" : "watchlist";
  const [scope, setScope] = useState<"symbol" | "watchlist" | "perStock">(
    rule ? ruleScope : (preset?.scope ?? "symbol"),
  );
  const [watchlistId, setWatchlistId] = useState(rule?.watchlist_id ?? preset?.groupId ?? "");
  const groups = useQuery({
    queryKey: queryKeys.settings("alert-watchlists"),
    queryFn: () => getWatchlists(),
    enabled: !editing,
  });
  const [submitted, setSubmitted] = useState(false);
  const expiryOptions = editing ? EDIT_EXPIRY_OPTIONS : CREATE_EXPIRY_OPTIONS;

  // Flag a ticker with no price data (a typo like APPLE for AAPL) once the user pauses typing —
  // a hint only; creating the rule is never blocked, since a brand-new listing can lack a bar.
  const [settledSymbol, setSettledSymbol] = useState("");
  useEffect(() => {
    const timer = window.setTimeout(() => setSettledSymbol(symbol.trim().toUpperCase()), 500);
    return () => window.clearTimeout(timer);
  }, [symbol]);
  const symbolCheck = useQuery({
    queryKey: queryKeys.settings("alert-symbol-check", market, settledSymbol),
    queryFn: () => checkAlertSymbol(settledSymbol, market),
    enabled: !editing && scope === "symbol" && settledSymbol !== "",
    staleTime: FRESHNESS.reference,
  });
  const symbolUnknown =
    !editing &&
    scope === "symbol" &&
    symbolCheck.data?.checked === true &&
    symbolCheck.data.exists === false;

  const mutation = useMutation({
    mutationFn: save,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.settings("alerts") });
      onDone();
    },
    onError: (error) => {
      // A stale edit means another device saved first: refresh the list, then fold their change
      // into the open form so the user re-saves in one click instead of losing their edit.
      if (error instanceof SettingsApiError && error.code === "stale_version") {
        void queryClient.invalidateQueries({ queryKey: queryKeys.settings("alerts") });
        void mergeLatest();
      }
    },
  });

  const mergeLatest = async (): Promise<void> => {
    if (!base) {
      return;
    }
    try {
      const built = buildCondition(draft);
      const latest = (await listAlerts()).find((candidate) => candidate.id === base.id);
      if (!latest || !built.ok) {
        return; // deleted meanwhile, or the draft is invalid — keep the plain stale message
      }
      const merged = mergeStaleEdit(base, latest, { condition: built.condition, triggerMode });
      setBase(latest);
      setDraft(draftFromCondition(merged.condition));
      setTriggerMode(merged.triggerMode);
      setNotice(merged.conflict ? "conflict" : "merged");
      mutation.reset();
    } catch {
      // Network failure — the stale message stays and the user can reopen the editor.
    }
  };

  // A US symbol has no Taiwan-only data, so switching market also drops a template that market
  // can't serve (the API would refuse it) rather than leaving an unsubmittable selection.
  const changeMarket = (next: Market): void => {
    setMarket(next);
    if (!templatesFor(next).includes(draft.kind)) {
      setDraft((prev) => ({ ...prev, kind: DEFAULT_DRAFT.kind }));
    }
  };

  const update = (patch: Partial<ConditionDraft>): void => {
    setDraft((prev) => ({ ...prev, ...patch }));
  };

  const built = buildCondition(draft);
  const symbolMissing = scope === "symbol" && symbol.trim() === "";
  const watchlistMissing = scope === "watchlist" && watchlistId === "";
  // A group lives in one market, which decides the templates it can use (a Taiwan group can use the
  // Taiwan-only ones); until a group is chosen only the templates both markets have are offered.
  const groupMarket = (groups.data ?? []).find((g) => g.group.id === watchlistId)?.group.market;
  const groupTemplateMarket: Market = groupMarket === "TW" ? "TW" : "US";
  const templateMarket: Market = scope === "watchlist" ? groupTemplateMarket : market;
  const offeredTemplates =
    scope === "watchlist" ? groupTemplatesFor(templateMarket) : templatesFor(templateMarket);
  const fields = fieldsFor(draft.kind);
  const fieldErrors = built.ok || !submitted ? {} : built.errors;

  const onSubmit = (e: FormEvent<HTMLFormElement>): void => {
    e.preventDefault();
    setSubmitted(true);
    if ((!editing && (symbolMissing || watchlistMissing)) || !built.ok) {
      return;
    }
    setNotice(null);
    if (base) {
      const patch = buildAlertPatch(base, { condition: built.condition, triggerMode, expiry });
      if (patch === null) {
        onDone(); // nothing changed — don't bump the version or re-arm the rule
        return;
      }
      mutation.mutate({ mode: "update", id: base.id, body: patch });
      return;
    }
    mutation.mutate({
      mode: "create",
      body: {
        ...(scope === "watchlist"
          ? { watchlist_id: watchlistId }
          : { symbol: symbol.trim(), market }),
        condition: built.condition,
        trigger_mode: triggerMode,
        expires_in_days: expiry === "never" ? null : Number(expiry),
      },
    });
  };

  const scopeSelect = editing ? null : (
    <Field label={t("scope")} htmlFor="alert-scope" className="sm:col-span-2">
      <Select
        id="alert-scope"
        value={scope}
        onChange={(e) => {
          const next = e.target.value as "symbol" | "watchlist" | "perStock";
          setScope(next);
          // A group rule cannot use the price template (one level, many prices).
          if (next === "watchlist" && !groupTemplatesFor("US").includes(draft.kind)) {
            setDraft((prev) => ({ ...prev, kind: GROUP_DEFAULT_KIND }));
          }
          if (next === "symbol" && !templatesFor(market).includes(draft.kind)) {
            setDraft((prev) => ({ ...prev, kind: DEFAULT_DRAFT.kind }));
          }
        }}
      >
        <option value="symbol">{t("scopeSymbol")}</option>
        <option value="watchlist">{t("scopeWatchlist")}</option>
        <option value="perStock">{t("scopePerStock")}</option>
      </Select>
    </Field>
  );

  const onKeyDown = (e: KeyboardEvent<HTMLFormElement>): void => {
    if (e.key === "Escape") {
      e.stopPropagation();
      onDone();
    }
  };

  const preview = built.ok ? conditionSummary(built.condition) : null;

  const fieldMessage = (code: ConditionFieldError | undefined): string | undefined => {
    if (code === "required") {
      return t("errorRequired");
    }
    if (code === "range") {
      return t("errorRange");
    }
    return undefined;
  };

  if (scope === "perStock") {
    return (
      <div className="space-y-4">
        <div className="rounded-[var(--radius-input)] border border-[var(--border)] bg-[var(--surface-2)] p-4">
          {scopeSelect}
        </div>
        <PerStockForm onDone={onDone} initialGroupId={watchlistId || undefined} />
      </div>
    );
  }

  return (
    <form
      aria-label={editing ? t("editTitle") : t("title")}
      onSubmit={onSubmit}
      onKeyDown={onKeyDown}
      className="space-y-4 rounded-[var(--radius-input)] border border-[var(--border)] bg-[var(--surface-2)] p-4"
    >
      <fieldset className="grid gap-3 sm:grid-cols-2">
        {scopeSelect}
        {scope === "watchlist" ? (
          <Field label={t("watchlist")} htmlFor="alert-watchlist" className="sm:col-span-2">
            <Select
              id="alert-watchlist"
              value={watchlistId}
              onChange={(e) => {
                setWatchlistId(e.target.value);
                const picked = (groups.data ?? []).find((g) => g.group.id === e.target.value);
                const nextMarket: Market = picked?.group.market ?? "US";
                if (!groupTemplatesFor(nextMarket).includes(draft.kind)) {
                  setDraft((prev) => ({ ...prev, kind: GROUP_DEFAULT_KIND }));
                }
              }}
              disabled={editing}
            >
              <option value="">{t("watchlistPlaceholder")}</option>
              {(groups.data ?? []).map((view) => (
                <option
                  key={view.group.id}
                  value={view.group.id}
                  style={groupNameStyle(view.group)}
                >
                  {view.group.name}
                </option>
              ))}
            </Select>
            <FieldError
              message={submitted && watchlistMissing ? t("errorWatchlistRequired") : undefined}
            />
            <p className="mt-1 text-[12px] text-[var(--text-muted)]">
              {t("groupHint")}{" "}
              <button
                type="button"
                className="text-[var(--accent-text)] underline"
                onClick={() => setScope("perStock")}
              >
                {t("groupHintAction")}
              </button>
            </p>
          </Field>
        ) : null}
        {scope === "symbol" ? (
          <>
            <Field label={t("symbol")} htmlFor="alert-symbol">
              <TextInput
                id="alert-symbol"
                value={symbol}
                onChange={(e) => setSymbol(e.target.value)}
                placeholder="2330"
                disabled={editing}
              />
              <FieldError
                message={submitted && symbolMissing ? t("errorSymbolRequired") : undefined}
              />
              {symbolUnknown ? (
                <p role="status" className="text-[12px] text-[var(--warn)]">
                  {t("warnSymbolUnknown", { symbol: settledSymbol })}
                </p>
              ) : null}
            </Field>
            <Field label={t("market")} htmlFor="alert-market">
              <Select
                id="alert-market"
                value={market}
                onChange={(e) => changeMarket(e.target.value as Market)}
                disabled={editing}
              >
                {MARKETS.map((m) => (
                  <option key={m} value={m}>
                    {tc(`marketOption.${m}`)}
                  </option>
                ))}
              </Select>
            </Field>
          </>
        ) : null}
        <Field label={t("template")} htmlFor="alert-template" className="sm:col-span-2">
          <Select
            id="alert-template"
            value={draft.kind}
            onChange={(e) => update({ kind: e.target.value as AlertTemplateKind })}
          >
            {offeredTemplates.map((kind) => (
              <option key={kind} value={kind}>
                {t(`templateOption.${kind}`)}
              </option>
            ))}
          </Select>
        </Field>

        {draft.kind === "close_price" ? (
          <Field label={t("comparator")} htmlFor="alert-comparator">
            <Select
              id="alert-comparator"
              value={draft.comparator}
              onChange={(e) => update({ comparator: e.target.value as "above" | "below" })}
            >
              {(["above", "below"] as const).map((v) => (
                <option key={v} value={v}>
                  {t(`comparatorOption.${v}`)}
                </option>
              ))}
            </Select>
          </Field>
        ) : null}
        {draft.kind === "daily_change" ? (
          <Field label={t("moveDirection")} htmlFor="alert-move">
            <Select
              id="alert-move"
              value={draft.moveDirection}
              onChange={(e) => update({ moveDirection: e.target.value as "up" | "down" })}
            >
              {(["up", "down"] as const).map((v) => (
                <option key={v} value={v}>
                  {t(`moveDirectionOption.${v}`)}
                </option>
              ))}
            </Select>
          </Field>
        ) : null}
        {draft.kind === "margin_balance_change" ? (
          <>
            <Field label={t("marginSide")} htmlFor="alert-margin-side">
              <Select
                id="alert-margin-side"
                value={draft.marginSide}
                onChange={(e) => update({ marginSide: e.target.value as "margin" | "short" })}
              >
                {(["margin", "short"] as const).map((v) => (
                  <option key={v} value={v}>
                    {t(`marginSideOption.${v}`)}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label={t("marginDirection")} htmlFor="alert-margin-direction">
              <Select
                id="alert-margin-direction"
                value={draft.moveDirection}
                onChange={(e) => update({ moveDirection: e.target.value as "up" | "down" })}
              >
                {(["up", "down"] as const).map((v) => (
                  <option key={v} value={v}>
                    {t(`marginDirectionOption.${v}`)}
                  </option>
                ))}
              </Select>
            </Field>
          </>
        ) : null}
        {draft.kind === "material_announcement" ? (
          <Field label={t("keyword")} htmlFor="alert-keyword" className="sm:col-span-2">
            <TextInput
              id="alert-keyword"
              maxLength={KEYWORD_MAX_LENGTH}
              placeholder={t("keywordPlaceholder")}
              value={draft.keyword}
              onChange={(e) => update({ keyword: e.target.value })}
            />
          </Field>
        ) : null}
        {draft.kind === "etf_premium_discount" ? (
          <Field label={t("navSide")} htmlFor="alert-nav-side">
            <Select
              id="alert-nav-side"
              value={draft.navSide}
              onChange={(e) => update({ navSide: e.target.value as "premium" | "discount" })}
            >
              {(["premium", "discount"] as const).map((v) => (
                <option key={v} value={v}>
                  {t(`navSideOption.${v}`)}
                </option>
              ))}
            </Select>
          </Field>
        ) : null}
        {draft.kind === "institutional_streak" ? (
          <>
            <Field label={t("investor")} htmlFor="alert-investor">
              <Select
                id="alert-investor"
                value={draft.investor}
                onChange={(e) => update({ investor: e.target.value as ConditionDraft["investor"] })}
              >
                {INSTITUTIONAL_INVESTORS.map((v) => (
                  <option key={v} value={v}>
                    {t(`investorOption.${v}`)}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label={t("flowDirection")} htmlFor="alert-flow">
              <Select
                id="alert-flow"
                value={draft.flowDirection}
                onChange={(e) => update({ flowDirection: e.target.value as "buy" | "sell" })}
              >
                {(["buy", "sell"] as const).map((v) => (
                  <option key={v} value={v}>
                    {t(`flowDirectionOption.${v}`)}
                  </option>
                ))}
              </Select>
            </Field>
          </>
        ) : null}

        {fields.map((key) => (
          <Field key={key} label={t(key)} htmlFor={`alert-${key}`}>
            <TextInput
              id={`alert-${key}`}
              type="number"
              inputMode="decimal"
              step={NUMERIC_FIELD_STEP[key]}
              value={draft[key]}
              onChange={(e) => update({ [key]: e.target.value })}
              aria-invalid={fieldErrors[key] ? true : undefined}
            />
            <FieldError message={fieldMessage(fieldErrors[key])} />
          </Field>
        ))}
        {draft.kind === "etf_premium_discount" ? (
          <p className="text-[13px] text-[var(--text-muted)] sm:col-span-2">{t("etfNote")}</p>
        ) : null}
        {fields.length === 0 ? (
          <p className="text-[13px] text-[var(--text-muted)] sm:col-span-2">
            {t(`hint.${draft.kind}`)}
          </p>
        ) : null}

        <Field label={t("triggerMode")} htmlFor="alert-trigger-mode" className="sm:col-span-2">
          <Select
            id="alert-trigger-mode"
            value={triggerMode}
            onChange={(e) => setTriggerMode(e.target.value as AlertTriggerMode)}
          >
            {ALERT_TRIGGER_MODES.map((mode) => (
              <option key={mode} value={mode}>
                {t(`triggerModeOption.${mode}`)}
              </option>
            ))}
          </Select>
        </Field>
        <Field label={t("expiry")} htmlFor="alert-expiry">
          <Select
            id="alert-expiry"
            value={expiry}
            onChange={(e) => setExpiry(e.target.value as ExpiryChoice)}
          >
            {expiryOptions.map((option) => (
              <option key={option} value={option}>
                {t(`expiryOption.${option}`)}
              </option>
            ))}
          </Select>
        </Field>
      </fieldset>

      <p className="text-[13px] text-[var(--text-muted)]" aria-live="polite">
        <span className="font-medium text-[var(--text)]">{t("preview")}：</span>
        {preview ? (
          <>
            {scope === "symbol" && symbol.trim() ? `${symbol.trim()} · ` : ""}
            {ts(preview.key, preview.values)}
          </>
        ) : (
          t("previewPending")
        )}
      </p>

      {notice !== null ? (
        <p role="status" className="text-[13px] text-[var(--text-muted)]">
          {t(notice === "conflict" ? "mergedConflict" : "merged")}
        </p>
      ) : null}

      {mutation.isError ? (
        <p role="alert" className="text-[13px] text-[var(--warn)]">
          {t(errorMessageKey(mutation.error, editing))}
        </p>
      ) : null}

      <div className="flex justify-end gap-2">
        <Button type="button" variant="ghost" size="sm" onClick={onDone}>
          {t("cancel")}
        </Button>
        <Button type="submit" variant="filled" size="sm" disabled={mutation.isPending}>
          {editing ? t("save") : t("submit")}
        </Button>
      </div>
    </form>
  );
}

function Field({
  label,
  htmlFor,
  className,
  children,
}: Readonly<{
  label: string;
  htmlFor: string;
  className?: string;
  children: ReactNode;
}>): ReactNode {
  return (
    <div className={className}>
      <label className="mb-1 block text-[13px] text-[var(--text-muted)]" htmlFor={htmlFor}>
        {label}
      </label>
      {children}
    </div>
  );
}

function FieldError({ message }: { message: string | undefined }): ReactNode {
  return message ? (
    <p role="alert" className="mt-1 text-[12px] text-[var(--warn)]">
      {message}
    </p>
  ) : null;
}
