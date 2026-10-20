"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { type FormEvent, type KeyboardEvent, type ReactNode, useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import {
  type BuildResult,
  buildAlertPatch,
  buildCondition,
  type ConditionDraft,
  type ConditionFieldErrors,
  conditionSummary,
  DEFAULT_DRAFT,
  draftFromCondition,
  type ExpiryChoice,
  fieldsFor,
  groupTemplatesFor,
  mergeStaleEdit,
  type StaleMerge,
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
import type { AlertRule, AlertTemplateKind, AlertTriggerMode, Market } from "@/types/settings";
import type { WatchlistGroupView } from "@/types/watchlist";
import {
  type AlertScope,
  NumericFields,
  ScopeField,
  SymbolFields,
  TemplateFields,
  TriggerAndExpiryFields,
  WatchlistField,
} from "./alert-form-fields";
import { PerStockForm } from "./per-stock-form";

/** A group rule starts on a scale-free template; the price template is per stock. */
const GROUP_DEFAULT_KIND: AlertTemplateKind = "daily_change";
const CREATE_EXPIRY_OPTIONS: readonly ExpiryChoice[] = ["30", "90", "365", "never"];
/** Editing can also leave the expiry as it is — a rule's exact date is rarely one of the presets. */
const EDIT_EXPIRY_OPTIONS: readonly ExpiryChoice[] = ["keep", ...CREATE_EXPIRY_OPTIONS];

function expiryOptionsFor(editing: boolean): readonly ExpiryChoice[] {
  return editing ? EDIT_EXPIRY_OPTIONS : CREATE_EXPIRY_OPTIONS;
}

function defaultExpiry(editing: boolean): ExpiryChoice {
  return editing ? "keep" : "90";
}

type SaveAction =
  | { mode: "create"; body: Parameters<typeof createAlert>[0] }
  | { mode: "update"; id: string; body: Parameters<typeof updateAlert>[1] };

async function save(action: SaveAction): Promise<unknown> {
  return action.mode === "create" ? createAlert(action.body) : updateAlert(action.id, action.body);
}

function isStaleVersion(error: unknown): boolean {
  return error instanceof SettingsApiError && error.code === "stale_version";
}

/** Which message explains a failed save. `cap_reached` and `stale_version` are the two the user can
 * act on; everything else is the generic retry message for the current mode. */
function errorMessageKey(error: unknown, editing: boolean): string {
  if (error instanceof SettingsApiError && error.code === "cap_reached") {
    return "errorCap";
  }
  if (isStaleVersion(error)) {
    return "errorStale";
  }
  return editing ? "errorUpdate" : "errorCreate";
}

export type AlertFormPreset = {
  scope?: "symbol" | "watchlist" | "perStock";
  symbol?: string;
  market?: Market;
  groupId?: string | undefined;
};

/** What the form starts with: the rule being edited, else the preset, else the defaults. */
function initialFormValues(
  rule: AlertRule | undefined,
  preset: AlertFormPreset | undefined,
): {
  symbol: string;
  market: Market;
  draft: ConditionDraft;
  triggerMode: AlertTriggerMode;
  scope: AlertScope;
  watchlistId: string;
} {
  if (rule) {
    const market = rule.market === "US" || rule.market === "TW" ? rule.market : preset?.market;
    return {
      symbol: rule.symbol ?? preset?.symbol ?? "",
      market: market ?? "TW",
      draft: draftFromCondition(rule.condition),
      triggerMode: rule.trigger_mode,
      scope: rule.symbol ? "symbol" : "watchlist",
      watchlistId: rule.watchlist_id ?? preset?.groupId ?? "",
    };
  }
  return {
    symbol: preset?.symbol ?? "",
    market: preset?.market ?? "TW",
    draft:
      preset?.scope === "watchlist"
        ? { ...DEFAULT_DRAFT, kind: GROUP_DEFAULT_KIND }
        : DEFAULT_DRAFT,
    triggerMode: "once_per_day",
    scope: preset?.scope ?? "symbol",
    watchlistId: preset?.groupId ?? "",
  };
}

/** The template to fall back to when `kind` is not offered for `offered`, else null (keep it). */
function fallbackKind(
  offered: readonly AlertTemplateKind[],
  kind: AlertTemplateKind,
  fallback: AlertTemplateKind,
): AlertTemplateKind | null {
  return offered.includes(kind) ? null : fallback;
}

/** Changing scope can leave the chosen template unavailable: a group rule cannot use the price
 * template (one level, many prices), and a US symbol has no Taiwan-only ones. */
function kindAfterScopeChange(
  next: AlertScope,
  market: Market,
  kind: AlertTemplateKind,
): AlertTemplateKind | null {
  if (next === "watchlist") {
    return fallbackKind(groupTemplatesFor("US"), kind, GROUP_DEFAULT_KIND);
  }
  if (next === "symbol") {
    return fallbackKind(templatesFor(market), kind, DEFAULT_DRAFT.kind);
  }
  return null;
}

/** The templates the current scope can offer. A group lives in one market, which decides the
 * templates it can use (a Taiwan group can use the Taiwan-only ones); until a group is chosen only
 * the templates both markets have are offered. */
function offeredTemplatesFor(
  scope: AlertScope,
  market: Market,
  groups: readonly WatchlistGroupView[],
  watchlistId: string,
): readonly AlertTemplateKind[] {
  if (scope !== "watchlist") {
    return templatesFor(market);
  }
  const groupMarket = groups.find((g) => g.group.id === watchlistId)?.group.market;
  return groupTemplatesFor(groupMarket === "TW" ? "TW" : "US");
}

/** Flag a ticker with no price data only when the lookup worked and found nothing. */
function isSymbolUnknown(
  editing: boolean,
  scope: AlertScope,
  check: { checked?: boolean; exists?: boolean } | undefined,
): boolean {
  return !editing && scope === "symbol" && check?.checked === true && check.exists === false;
}

function visibleFieldErrors(built: BuildResult, submitted: boolean): ConditionFieldErrors {
  return built.ok || !submitted ? {} : built.errors;
}

type SubmitInputs = {
  readonly editing: boolean;
  readonly base: AlertRule | undefined;
  readonly built: BuildResult;
  readonly symbolMissing: boolean;
  readonly watchlistMissing: boolean;
  readonly scope: AlertScope;
  readonly watchlistId: string;
  readonly symbol: string;
  readonly market: Market;
  readonly triggerMode: AlertTriggerMode;
  readonly expiry: ExpiryChoice;
};

type SubmitPlan =
  | { readonly kind: "invalid" }
  | { readonly kind: "unchanged" }
  | { readonly kind: "save"; readonly action: SaveAction };

/** What pressing submit should do: nothing (invalid), just close (nothing changed), or save. */
function planSubmit(inputs: SubmitInputs): SubmitPlan {
  const { editing, base, built, scope, watchlistId, symbol, market, triggerMode, expiry } = inputs;
  if ((!editing && (inputs.symbolMissing || inputs.watchlistMissing)) || !built.ok) {
    return { kind: "invalid" };
  }
  if (base) {
    const patch = buildAlertPatch(base, { condition: built.condition, triggerMode, expiry });
    // Nothing changed — don't bump the version or re-arm the rule.
    return patch === null
      ? { kind: "unchanged" }
      : { kind: "save", action: { mode: "update", id: base.id, body: patch } };
  }
  return {
    kind: "save",
    action: {
      mode: "create",
      body: {
        ...(scope === "watchlist"
          ? { watchlist_id: watchlistId }
          : { symbol: symbol.trim(), market }),
        condition: built.condition,
        trigger_mode: triggerMode,
        expires_in_days: expiry === "never" ? null : Number(expiry),
      },
    },
  };
}

/** Reload the rule and fold the other device's change into the form's values. Null when the rule
 * was deleted meanwhile, the draft is invalid, or the reload failed — the plain stale message stays. */
async function loadMergedEdit(
  base: AlertRule,
  draft: ConditionDraft,
  triggerMode: AlertTriggerMode,
): Promise<{ latest: AlertRule; merged: StaleMerge } | null> {
  try {
    const built = buildCondition(draft);
    const latest = (await listAlerts()).find((candidate) => candidate.id === base.id);
    if (!latest || !built.ok) {
      return null;
    }
    const merged = mergeStaleEdit(base, latest, { condition: built.condition, triggerMode });
    return { latest, merged };
  } catch {
    // Network failure — the stale message stays and the user can reopen the editor.
    return null;
  }
}

/** The ticker to warn about, once the user pauses typing and the lookup worked but found no price
 * data for it (a typo like APPLE for AAPL). A hint only; creating the rule is never blocked, since
 * a brand-new listing can lack a bar. Null when there is nothing to warn about. */
function useUnknownSymbol(
  editing: boolean,
  scope: AlertScope,
  symbol: string,
  market: Market,
): string | null {
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
  return isSymbolUnknown(editing, scope, symbolCheck.data) ? settledSymbol : null;
}

type MissingFields = { readonly symbol: boolean; readonly watchlist: boolean };
const NOTHING_MISSING: MissingFields = { symbol: false, watchlist: false };

/** Which required scope field is still empty. */
function missingFields(scope: AlertScope, symbol: string, watchlistId: string): MissingFields {
  return {
    symbol: scope === "symbol" && symbol.trim() === "",
    watchlist: scope === "watchlist" && watchlistId === "",
  };
}

/** Alert-rule create/edit form.
 * Template-first — the user picks what to watch and fills only that template's fields — with a
 * fact-phrased preview line and inline, retryable errors. `Esc` cancels. Pass `rule` to edit: the
 * scope (symbol / market) is fixed, only changed fields are sent, and the save carries the rule's
 * `version` so an edit from a stale screen is refused instead of overwriting a newer one. */
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
  const queryClient = useQueryClient();

  const editing = rule !== undefined;
  const initial = initialFormValues(rule, preset);
  // The server copy the edit is diffed against. It moves forward after a 409 is merged.
  const [base, setBase] = useState<AlertRule | undefined>(rule);
  const [notice, setNotice] = useState<"merged" | "conflict" | null>(null);
  const [symbol, setSymbol] = useState(initial.symbol);
  const [market, setMarket] = useState<Market>(initial.market);
  const [draft, setDraft] = useState<ConditionDraft>(initial.draft);
  const [triggerMode, setTriggerMode] = useState<AlertTriggerMode>(initial.triggerMode);
  const [expiry, setExpiry] = useState<ExpiryChoice>(defaultExpiry(editing));
  // A rule follows one symbol, or every stock in one of the user's watchlist groups (the scope is
  // fixed once created). A group can hold both markets, so a group rule offers only the templates
  // that work for both.
  const [scope, setScope] = useState<AlertScope>(initial.scope);
  const [watchlistId, setWatchlistId] = useState(initial.watchlistId);
  const groups = useQuery({
    queryKey: queryKeys.settings("alert-watchlists"),
    queryFn: () => getWatchlists(),
    enabled: !editing,
  });
  const [submitted, setSubmitted] = useState(false);

  const unknownSymbol = useUnknownSymbol(editing, scope, symbol, market);

  const mutation = useMutation({
    mutationFn: save,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.settings("alerts") });
      onDone();
    },
    onError: (error) => {
      // A stale edit means another device saved first: refresh the list, then fold their change
      // into the open form so the user re-saves in one click instead of losing their edit.
      if (isStaleVersion(error)) {
        void queryClient.invalidateQueries({ queryKey: queryKeys.settings("alerts") });
        void mergeLatest();
      }
    },
  });

  const mergeLatest = async (): Promise<void> => {
    if (!base) {
      return;
    }
    const result = await loadMergedEdit(base, draft, triggerMode);
    if (!result) {
      return;
    }
    setBase(result.latest);
    setDraft(draftFromCondition(result.merged.condition));
    setTriggerMode(result.merged.triggerMode);
    setNotice(result.merged.conflict ? "conflict" : "merged");
    mutation.reset();
  };

  const setKind = (kind: AlertTemplateKind | null): void => {
    if (kind !== null) {
      setDraft((prev) => ({ ...prev, kind }));
    }
  };

  // A US symbol has no Taiwan-only data, so switching market also drops a template that market
  // can't serve (the API would refuse it) rather than leaving an unsubmittable selection.
  const changeMarket = (next: Market): void => {
    setMarket(next);
    setKind(fallbackKind(templatesFor(next), draft.kind, DEFAULT_DRAFT.kind));
  };

  const changeScope = (next: AlertScope): void => {
    setScope(next);
    setKind(kindAfterScopeChange(next, market, draft.kind));
  };

  const changeWatchlist = (id: string): void => {
    setWatchlistId(id);
    const picked = (groups.data ?? []).find((g) => g.group.id === id);
    const nextMarket: Market = picked?.group.market ?? "US";
    setKind(fallbackKind(groupTemplatesFor(nextMarket), draft.kind, GROUP_DEFAULT_KIND));
  };

  const update = (patch: Partial<ConditionDraft>): void => {
    setDraft((prev) => ({ ...prev, ...patch }));
  };

  const built = buildCondition(draft);
  const missing = missingFields(scope, symbol, watchlistId);

  const onSubmit = (e: FormEvent<HTMLFormElement>): void => {
    e.preventDefault();
    setSubmitted(true);
    const plan = planSubmit({
      editing,
      base,
      built,
      symbolMissing: missing.symbol,
      watchlistMissing: missing.watchlist,
      scope,
      watchlistId,
      symbol,
      market,
      triggerMode,
      expiry,
    });
    if (plan.kind === "invalid") {
      return;
    }
    setNotice(null);
    if (plan.kind === "unchanged") {
      onDone();
      return;
    }
    mutation.mutate(plan.action);
  };

  const onKeyDown = (e: KeyboardEvent<HTMLFormElement>): void => {
    if (e.key === "Escape") {
      e.stopPropagation();
      onDone();
    }
  };

  const scopeField = editing ? null : <ScopeField scope={scope} onChange={changeScope} />;

  if (scope === "perStock") {
    return (
      <div className="space-y-4">
        <div className="rounded-[var(--radius-input)] border border-[var(--border)] bg-[var(--surface-2)] p-4">
          {scopeField}
        </div>
        <PerStockForm onDone={onDone} initialGroupId={watchlistId || undefined} />
      </div>
    );
  }

  const fields = fieldsFor(draft.kind);
  // Required-field errors only show once the user has tried to submit.
  const shownMissing = submitted ? missing : NOTHING_MISSING;

  return (
    <form
      aria-label={editing ? t("editTitle") : t("title")}
      onSubmit={onSubmit}
      onKeyDown={onKeyDown}
      className="space-y-4 rounded-[var(--radius-input)] border border-[var(--border)] bg-[var(--surface-2)] p-4"
    >
      <fieldset className="grid gap-3 sm:grid-cols-2">
        {scopeField}
        {scope === "watchlist" ? (
          <WatchlistField
            groups={groups.data ?? []}
            watchlistId={watchlistId}
            onChange={changeWatchlist}
            disabled={editing}
            showRequired={shownMissing.watchlist}
            onPerStock={() => setScope("perStock")}
          />
        ) : null}
        {scope === "symbol" ? (
          <SymbolFields
            symbol={symbol}
            setSymbol={setSymbol}
            market={market}
            onMarketChange={changeMarket}
            editing={editing}
            showRequired={shownMissing.symbol}
            unknownSymbol={unknownSymbol}
          />
        ) : null}
        <TemplateFields
          draft={draft}
          offered={offeredTemplatesFor(scope, market, groups.data ?? [], watchlistId)}
          update={update}
        />
        <NumericFields
          kind={draft.kind}
          fields={fields}
          draft={draft}
          errors={visibleFieldErrors(built, submitted)}
          update={update}
        />
        <TriggerAndExpiryFields
          triggerMode={triggerMode}
          setTriggerMode={setTriggerMode}
          expiry={expiry}
          setExpiry={setExpiry}
          expiryOptions={expiryOptionsFor(editing)}
        />
      </fieldset>

      <PreviewLine built={built} symbol={scope === "symbol" ? symbol.trim() : ""} />

      <FormMessages
        notice={notice}
        error={mutation.isError ? mutation.error : null}
        editing={editing}
      />

      <FormActions editing={editing} pending={mutation.isPending} onCancel={onDone} />
    </form>
  );
}

/** The fact-phrased summary of the rule as it stands, or a placeholder while it is incomplete. */
function PreviewLine({
  built,
  symbol,
}: {
  readonly built: BuildResult;
  readonly symbol: string;
}): ReactNode {
  const t = useTranslations("settings.alerts.form");
  const ts = useTranslations("settings.alerts.summary");
  const preview = built.ok ? conditionSummary(built.condition) : null;
  return (
    <p className="text-[13px] text-[var(--text-muted)]" aria-live="polite">
      <span className="font-medium text-[var(--text)]">{t("preview")}：</span>
      {preview ? (
        <>
          {symbol ? `${symbol} · ` : ""}
          {ts(preview.key, preview.values)}
        </>
      ) : (
        t("previewPending")
      )}
    </p>
  );
}

/** The merge notice after a stale save, and the inline error for a failed one. */
function FormMessages({
  notice,
  error,
  editing,
}: {
  readonly notice: "merged" | "conflict" | null;
  readonly error: unknown;
  readonly editing: boolean;
}): ReactNode {
  const t = useTranslations("settings.alerts.form");
  return (
    <>
      {notice !== null ? (
        <p role="status" className="text-[13px] text-[var(--text-muted)]">
          {t(notice === "conflict" ? "mergedConflict" : "merged")}
        </p>
      ) : null}

      {error !== null ? (
        <p role="alert" className="text-[13px] text-[var(--warn)]">
          {t(errorMessageKey(error, editing))}
        </p>
      ) : null}
    </>
  );
}

function FormActions({
  editing,
  pending,
  onCancel,
}: {
  readonly editing: boolean;
  readonly pending: boolean;
  readonly onCancel: () => void;
}): ReactNode {
  const t = useTranslations("settings.alerts.form");
  return (
    <div className="flex justify-end gap-2">
      <Button type="button" variant="ghost" size="sm" onClick={onCancel}>
        {t("cancel")}
      </Button>
      <Button type="submit" variant="filled" size="sm" disabled={pending}>
        {editing ? t("save") : t("submit")}
      </Button>
    </div>
  );
}
