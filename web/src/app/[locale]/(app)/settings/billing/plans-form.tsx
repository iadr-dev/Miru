"use client";

import { useTranslations } from "next-intl";
import { type ReactNode, useState } from "react";
import { Checkbox, Select } from "@/components/settings/form-controls";
import { SettingsRow } from "@/components/settings/settings-section";
import { Button } from "@/components/ui/button";
import { formatMinorAmount } from "@/lib/billing-format";
import { BILLING_INTERVALS, PAYABLE_TIERS, type PlanInfo } from "@/types/settings";

export type PayableTier = (typeof PAYABLE_TIERS)[number];
export type BillingInterval = (typeof BILLING_INTERVALS)[number];

type PlansFormProps = {
  tier: PayableTier;
  setTier: (tier: PayableTier) => void;
  interval: BillingInterval;
  setInterval: (interval: BillingInterval) => void;
  byok: boolean;
  setByok: (byok: boolean) => void;
  actionError: string | null;
  pending: boolean;
  onSubmit: () => void;
  submitLabel: string;
  /** Why submitting is not possible right now (shown instead of silently disabling). */
  blockedNote?: string | null;
  /** When set, the first click asks this question and a second click submits. */
  confirmText?: string;
  plan: PlanInfo | undefined;
  locale: string;
};

/** The tier / interval / BYOK picker with the selected plan's price. Used for the first checkout
 * and, with `confirmText`, for changing an existing subscription's plan. */
export function PlansForm({
  tier,
  setTier,
  interval,
  setInterval,
  byok,
  setByok,
  actionError,
  pending,
  onSubmit,
  submitLabel,
  blockedNote,
  confirmText,
  plan,
  locale,
}: Readonly<PlansFormProps>): ReactNode {
  const t = useTranslations("settings.billing");
  const [confirming, setConfirming] = useState(false);
  const askFirst = confirmText != null && !blockedNote;
  const priceText = plan?.price
    ? `${formatMinorAmount(plan.price.amount, plan.price.currency_code, locale)} / ${t(`perInterval.${interval}`)}`
    : t("priceUnavailable");
  return (
    <div className="space-y-4 py-1">
      <SettingsRow label={t("tier")}>
        <Select
          aria-label={t("tier")}
          value={tier}
          onChange={(e) => {
            setConfirming(false);
            setTier(e.target.value as PayableTier);
          }}
          className="w-40"
        >
          {PAYABLE_TIERS.map((v) => (
            <option key={v} value={v}>
              {t(`tierOption.${v}`)}
            </option>
          ))}
        </Select>
      </SettingsRow>
      <SettingsRow label={t("interval")}>
        <Select
          aria-label={t("interval")}
          value={interval}
          onChange={(e) => {
            setConfirming(false);
            setInterval(e.target.value as BillingInterval);
          }}
          className="w-40"
        >
          {BILLING_INTERVALS.map((v) => (
            <option key={v} value={v}>
              {t(`intervalOption.${v}`)}
            </option>
          ))}
        </Select>
      </SettingsRow>
      <SettingsRow label={t("byok")}>
        <Checkbox
          checked={byok}
          onChange={(next) => {
            setConfirming(false);
            setByok(next);
          }}
          label={t("byokCheckbox")}
        />
      </SettingsRow>
      <SettingsRow label={t("price")} hint={plan?.price ? t("taxNote") : undefined}>
        <span className="font-medium text-[14px] text-[var(--text)] tabular-nums">{priceText}</span>
      </SettingsRow>
      {blockedNote ? <p className="text-[13px] text-[var(--text-muted)]">{blockedNote}</p> : null}
      {actionError ? (
        <p role="alert" className="text-[13px] text-[var(--warn)]">
          {actionError}
        </p>
      ) : null}
      {askFirst && confirming ? (
        <div className="space-y-3 rounded-[var(--radius-input)] bg-[var(--surface-2)] p-3">
          <p className="text-[13px] text-[var(--text)]">{confirmText}</p>
          <div className="flex gap-2">
            <Button variant="filled" size="sm" disabled={pending} onClick={onSubmit}>
              {t("confirmChange")}
            </Button>
            <Button
              variant="outline"
              size="sm"
              disabled={pending}
              onClick={() => setConfirming(false)}
            >
              {t("keepPlan")}
            </Button>
          </div>
        </div>
      ) : (
        <Button
          variant="filled"
          size="sm"
          disabled={pending || Boolean(blockedNote)}
          onClick={() => (askFirst ? setConfirming(true) : onSubmit())}
        >
          {submitLabel}
        </Button>
      )}
    </div>
  );
}
