"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { type FormEvent, type ReactNode, useState } from "react";
import { Checkbox, Select, TextInput } from "@/components/settings/form-controls";
import { Button } from "@/components/ui/button";
import { createAlert } from "@/lib/api/settings";
import {
  ALERT_COMPARATORS,
  ALERT_CONDITION_TYPES,
  type AlertComparator,
  type AlertConditionType,
  DELIVERY_CHANNEL_KINDS,
  type DeliveryChannelKind,
  MARKETS,
  type Market,
} from "@/types/settings";

type FieldErrors = Partial<Record<"symbol" | "metric" | "threshold", string>>;

/** Alert-rule create form (interaction-spec §2.13): 標的/條件/通道/頻率 fields with
 * threshold-direction validation (non-empty, coherent with comparator). */
export function AlertForm({ onDone }: { onDone: () => void }): ReactNode {
  const t = useTranslations("settings.alerts.form");
  const queryClient = useQueryClient();

  const [symbol, setSymbol] = useState("");
  const [market, setMarket] = useState<Market>("TW");
  const [conditionType, setConditionType] = useState<AlertConditionType>("price");
  const [metric, setMetric] = useState("close");
  const [comparator, setComparator] = useState<AlertComparator>("above");
  const [threshold, setThreshold] = useState("");
  const [window, setWindowValue] = useState("1");
  const [channels, setChannels] = useState<DeliveryChannelKind[]>(["in_app"]);
  const [errors, setErrors] = useState<FieldErrors>({});

  const mutation = useMutation({
    mutationFn: createAlert,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["settings", "alerts"] });
      onDone();
    },
  });

  const toggleChannel = (channel: DeliveryChannelKind, checked: boolean): void => {
    setChannels((prev) => (checked ? [...prev, channel] : prev.filter((c) => c !== channel)));
  };

  const validate = (): FieldErrors => {
    const next: FieldErrors = {};
    if (!symbol.trim()) {
      next.symbol = t("errorSymbolRequired");
    }
    if (!metric.trim()) {
      next.metric = t("errorMetricRequired");
    }
    const thresholdValue = Number(threshold);
    if (threshold.trim() === "" || Number.isNaN(thresholdValue)) {
      next.threshold = t("errorThresholdRequired");
    }
    return next;
  };

  const onSubmit = (e: FormEvent<HTMLFormElement>): void => {
    e.preventDefault();
    const nextErrors = validate();
    setErrors(nextErrors);
    if (Object.keys(nextErrors).length > 0) {
      return;
    }
    mutation.mutate({
      symbol: symbol.trim(),
      market,
      condition: {
        type: conditionType,
        metric: metric.trim(),
        comparator,
        threshold: Math.trunc(Number(threshold)),
        window: Math.trunc(Number(window)) || 1,
      },
      channels,
    });
  };

  return (
    <form
      aria-label={t("title")}
      onSubmit={onSubmit}
      className="space-y-4 rounded-[var(--radius-input)] border border-[var(--border)] bg-[var(--surface-2)] p-4"
    >
      <fieldset className="grid gap-3 sm:grid-cols-2">
        <div>
          <label className="mb-1 block text-[13px] text-[var(--text-muted)]" htmlFor="alert-symbol">
            {t("symbol")}
          </label>
          <TextInput
            id="alert-symbol"
            value={symbol}
            onChange={(e) => setSymbol(e.target.value)}
            placeholder="2330"
          />
          {errors.symbol ? (
            <p role="alert" className="mt-1 text-[12px] text-[var(--warn)]">
              {errors.symbol}
            </p>
          ) : null}
        </div>
        <div>
          <label className="mb-1 block text-[13px] text-[var(--text-muted)]" htmlFor="alert-market">
            {t("market")}
          </label>
          <Select
            id="alert-market"
            value={market}
            onChange={(e) => setMarket(e.target.value as Market)}
          >
            {MARKETS.map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
          </Select>
        </div>
        <div>
          <label
            className="mb-1 block text-[13px] text-[var(--text-muted)]"
            htmlFor="alert-condition-type"
          >
            {t("conditionType")}
          </label>
          <Select
            id="alert-condition-type"
            value={conditionType}
            onChange={(e) => setConditionType(e.target.value as AlertConditionType)}
          >
            {ALERT_CONDITION_TYPES.map((v) => (
              <option key={v} value={v}>
                {t(`conditionTypeOption.${v}`)}
              </option>
            ))}
          </Select>
        </div>
        <div>
          <label className="mb-1 block text-[13px] text-[var(--text-muted)]" htmlFor="alert-metric">
            {t("metric")}
          </label>
          <TextInput
            id="alert-metric"
            value={metric}
            onChange={(e) => setMetric(e.target.value)}
            placeholder="close"
          />
          {errors.metric ? (
            <p role="alert" className="mt-1 text-[12px] text-[var(--warn)]">
              {errors.metric}
            </p>
          ) : null}
        </div>
        <div>
          <label
            className="mb-1 block text-[13px] text-[var(--text-muted)]"
            htmlFor="alert-comparator"
          >
            {t("comparator")}
          </label>
          <Select
            id="alert-comparator"
            value={comparator}
            onChange={(e) => setComparator(e.target.value as AlertComparator)}
          >
            {ALERT_COMPARATORS.map((v) => (
              <option key={v} value={v}>
                {t(`comparatorOption.${v}`)}
              </option>
            ))}
          </Select>
        </div>
        <div>
          <label
            className="mb-1 block text-[13px] text-[var(--text-muted)]"
            htmlFor="alert-threshold"
          >
            {t("threshold")}
          </label>
          <TextInput
            id="alert-threshold"
            type="number"
            value={threshold}
            onChange={(e) => setThreshold(e.target.value)}
          />
          {errors.threshold ? (
            <p role="alert" className="mt-1 text-[12px] text-[var(--warn)]">
              {errors.threshold}
            </p>
          ) : null}
        </div>
        <div>
          <label className="mb-1 block text-[13px] text-[var(--text-muted)]" htmlFor="alert-window">
            {t("window")}
          </label>
          <TextInput
            id="alert-window"
            type="number"
            min={1}
            value={window}
            onChange={(e) => setWindowValue(e.target.value)}
          />
        </div>
      </fieldset>

      <fieldset>
        <legend className="mb-1 text-[13px] text-[var(--text-muted)]">{t("channels")}</legend>
        <div className="flex flex-wrap gap-3">
          {DELIVERY_CHANNEL_KINDS.map((channel) => (
            <Checkbox
              key={channel}
              checked={channels.includes(channel)}
              onChange={(checked) => toggleChannel(channel, checked)}
              label={t(`channelOption.${channel}`)}
            />
          ))}
        </div>
      </fieldset>

      <div className="flex justify-end gap-2">
        <Button type="button" variant="ghost" size="sm" onClick={onDone}>
          {t("cancel")}
        </Button>
        <Button type="submit" variant="filled" size="sm" disabled={mutation.isPending}>
          {t("submit")}
        </Button>
      </div>
    </form>
  );
}
