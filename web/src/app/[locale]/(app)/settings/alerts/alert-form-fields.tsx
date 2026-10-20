"use client";

import { useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { Select, TextInput } from "@/components/settings/form-controls";
import type {
  ConditionDraft,
  ConditionFieldError,
  ConditionFieldErrors,
  ConditionFieldKey,
  ExpiryChoice,
} from "@/lib/alerts/condition";
import { groupNameStyle } from "@/lib/watchlist-color";
import {
  ALERT_TRIGGER_MODES,
  type AlertTemplateKind,
  type AlertTriggerMode,
  INSTITUTIONAL_INVESTORS,
  MARKETS,
  type Market,
} from "@/types/settings";
import type { WatchlistGroupView } from "@/types/watchlist";

export type AlertScope = "symbol" | "watchlist" | "perStock";
type UpdateDraft = (patch: Partial<ConditionDraft>) => void;

/** Matches the backend's `MAX_KEYWORD_LENGTH`. */
const KEYWORD_MAX_LENGTH = 20;

const NUMERIC_FIELD_STEP: Record<ConditionFieldKey, string> = {
  value: "any",
  percent: "any",
  multiple: "any",
  window: "1",
  days: "1",
};

export function Field({
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

export function ScopeField({
  scope,
  onChange,
}: {
  readonly scope: AlertScope;
  readonly onChange: (next: AlertScope) => void;
}): ReactNode {
  const t = useTranslations("settings.alerts.form");
  return (
    <Field label={t("scope")} htmlFor="alert-scope" className="sm:col-span-2">
      <Select
        id="alert-scope"
        value={scope}
        onChange={(e) => onChange(e.target.value as AlertScope)}
      >
        <option value="symbol">{t("scopeSymbol")}</option>
        <option value="watchlist">{t("scopeWatchlist")}</option>
        <option value="perStock">{t("scopePerStock")}</option>
      </Select>
    </Field>
  );
}

export function WatchlistField({
  groups,
  watchlistId,
  onChange,
  disabled,
  showRequired,
  onPerStock,
}: {
  readonly groups: readonly WatchlistGroupView[];
  readonly watchlistId: string;
  readonly onChange: (id: string) => void;
  readonly disabled: boolean;
  readonly showRequired: boolean;
  readonly onPerStock: () => void;
}): ReactNode {
  const t = useTranslations("settings.alerts.form");
  return (
    <Field label={t("watchlist")} htmlFor="alert-watchlist" className="sm:col-span-2">
      <Select
        id="alert-watchlist"
        value={watchlistId}
        onChange={(e) => onChange(e.target.value)}
        disabled={disabled}
      >
        <option value="">{t("watchlistPlaceholder")}</option>
        {groups.map((view) => (
          <option key={view.group.id} value={view.group.id} style={groupNameStyle(view.group)}>
            {view.group.name}
          </option>
        ))}
      </Select>
      <FieldError message={showRequired ? t("errorWatchlistRequired") : undefined} />
      <p className="mt-1 text-[12px] text-[var(--text-muted)]">
        {t("groupHint")}{" "}
        <button type="button" className="text-[var(--accent-text)] underline" onClick={onPerStock}>
          {t("groupHintAction")}
        </button>
      </p>
    </Field>
  );
}

export function SymbolFields({
  symbol,
  setSymbol,
  market,
  onMarketChange,
  editing,
  showRequired,
  unknownSymbol,
}: {
  readonly symbol: string;
  readonly setSymbol: (symbol: string) => void;
  readonly market: Market;
  readonly onMarketChange: (market: Market) => void;
  readonly editing: boolean;
  readonly showRequired: boolean;
  /** The ticker to warn about, when the price-data check found nothing for it. */
  readonly unknownSymbol: string | null;
}): ReactNode {
  const t = useTranslations("settings.alerts.form");
  const tc = useTranslations("settings.common");
  return (
    <>
      <Field label={t("symbol")} htmlFor="alert-symbol">
        <TextInput
          id="alert-symbol"
          value={symbol}
          onChange={(e) => setSymbol(e.target.value)}
          placeholder="2330"
          disabled={editing}
        />
        <FieldError message={showRequired ? t("errorSymbolRequired") : undefined} />
        {unknownSymbol !== null ? (
          <p role="status" className="text-[12px] text-[var(--warn)]">
            {t("warnSymbolUnknown", { symbol: unknownSymbol })}
          </p>
        ) : null}
      </Field>
      <Field label={t("market")} htmlFor="alert-market">
        <Select
          id="alert-market"
          value={market}
          onChange={(e) => onMarketChange(e.target.value as Market)}
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
  );
}

export function TemplateFields({
  draft,
  offered,
  update,
}: {
  readonly draft: ConditionDraft;
  readonly offered: readonly AlertTemplateKind[];
  readonly update: UpdateDraft;
}): ReactNode {
  const t = useTranslations("settings.alerts.form");
  return (
    <>
      <Field label={t("template")} htmlFor="alert-template" className="sm:col-span-2">
        <Select
          id="alert-template"
          value={draft.kind}
          onChange={(e) => update({ kind: e.target.value as AlertTemplateKind })}
        >
          {offered.map((kind) => (
            <option key={kind} value={kind}>
              {t(`templateOption.${kind}`)}
            </option>
          ))}
        </Select>
      </Field>
      <KindExtraFields draft={draft} update={update} />
    </>
  );
}

type KindProps = { readonly draft: ConditionDraft; readonly update: UpdateDraft };

/** Each template's own choice fields (above/below, up/down, side, keyword, …). */
const KIND_EXTRA_FIELDS: Partial<Record<AlertTemplateKind, (props: KindProps) => ReactNode>> = {
  close_price: ComparatorField,
  daily_change: MoveDirectionField,
  margin_balance_change: MarginFields,
  material_announcement: KeywordField,
  etf_premium_discount: NavSideField,
  institutional_streak: InstitutionalFields,
};

function KindExtraFields(props: KindProps): ReactNode {
  const Extra = KIND_EXTRA_FIELDS[props.draft.kind];
  return Extra ? <Extra {...props} /> : null;
}

function ComparatorField({ draft, update }: KindProps): ReactNode {
  const t = useTranslations("settings.alerts.form");
  return (
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
  );
}

function MoveDirectionField({ draft, update }: KindProps): ReactNode {
  const t = useTranslations("settings.alerts.form");
  return (
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
  );
}

function MarginFields({ draft, update }: KindProps): ReactNode {
  const t = useTranslations("settings.alerts.form");
  return (
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
  );
}

function KeywordField({ draft, update }: KindProps): ReactNode {
  const t = useTranslations("settings.alerts.form");
  return (
    <Field label={t("keyword")} htmlFor="alert-keyword" className="sm:col-span-2">
      <TextInput
        id="alert-keyword"
        maxLength={KEYWORD_MAX_LENGTH}
        placeholder={t("keywordPlaceholder")}
        value={draft.keyword}
        onChange={(e) => update({ keyword: e.target.value })}
      />
    </Field>
  );
}

function NavSideField({ draft, update }: KindProps): ReactNode {
  const t = useTranslations("settings.alerts.form");
  return (
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
  );
}

function InstitutionalFields({ draft, update }: KindProps): ReactNode {
  const t = useTranslations("settings.alerts.form");
  return (
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
  );
}

/** Which message explains a numeric field's error, if it has one. */
function fieldMessageKey(
  code: ConditionFieldError | undefined,
): "errorRequired" | "errorRange" | null {
  if (code === "required") {
    return "errorRequired";
  }
  return code === "range" ? "errorRange" : null;
}

/** The numeric inputs the template uses, followed by its explanatory notes. */
export function NumericFields({
  kind,
  fields,
  draft,
  errors,
  update,
}: {
  readonly kind: AlertTemplateKind;
  readonly fields: readonly ConditionFieldKey[];
  readonly draft: ConditionDraft;
  readonly errors: ConditionFieldErrors;
  readonly update: UpdateDraft;
}): ReactNode {
  const t = useTranslations("settings.alerts.form");
  return (
    <>
      {fields.map((key) => {
        const messageKey = fieldMessageKey(errors[key]);
        return (
          <Field key={key} label={t(key)} htmlFor={`alert-${key}`}>
            <TextInput
              id={`alert-${key}`}
              type="number"
              inputMode="decimal"
              step={NUMERIC_FIELD_STEP[key]}
              value={draft[key]}
              onChange={(e) => update({ [key]: e.target.value })}
              aria-invalid={errors[key] ? true : undefined}
            />
            <FieldError message={messageKey ? t(messageKey) : undefined} />
          </Field>
        );
      })}
      {kind === "etf_premium_discount" ? (
        <p className="text-[13px] text-[var(--text-muted)] sm:col-span-2">{t("etfNote")}</p>
      ) : null}
      {fields.length === 0 ? (
        <p className="text-[13px] text-[var(--text-muted)] sm:col-span-2">{t(`hint.${kind}`)}</p>
      ) : null}
    </>
  );
}

export function TriggerAndExpiryFields({
  triggerMode,
  setTriggerMode,
  expiry,
  setExpiry,
  expiryOptions,
}: {
  readonly triggerMode: AlertTriggerMode;
  readonly setTriggerMode: (mode: AlertTriggerMode) => void;
  readonly expiry: ExpiryChoice;
  readonly setExpiry: (expiry: ExpiryChoice) => void;
  readonly expiryOptions: readonly ExpiryChoice[];
}): ReactNode {
  const t = useTranslations("settings.alerts.form");
  return (
    <>
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
    </>
  );
}
