"use client";

import { useTranslations } from "next-intl";
import { type FormEvent, type ReactNode, useState } from "react";
import { ExpandModal } from "@/components/ui/expand-modal";
import { useSaveStrategySubscription } from "@/hooks/use-strategy-watch";
import { useWatchlists } from "@/hooks/use-watchlists";
import { StrategyWatchApiError } from "@/lib/api/strategy-watch";
import { STRATEGY_KEYS, type StrategyKey, type StrategySubscription } from "@/types/strategy-watch";

const DAILY_LIMITS = [3, 5, 10, 20] as const;

/** The user's one 策略盯盤 subscription. Channels and quiet hours are not chosen here: they follow
 * the alert settings, so there is one place to manage where notifications go. */
export function SubscriptionDialog({
  open,
  onClose,
  current,
}: Readonly<{
  open: boolean;
  onClose: () => void;
  current: StrategySubscription | null;
}>): ReactNode {
  const t = useTranslations("watch");
  return (
    <ExpandModal
      open={open}
      title={t("form.title")}
      closeLabel={t("detail.close")}
      onClose={onClose}
    >
      {open ? (
        <SubscriptionForm key={current?.version ?? -1} current={current} onDone={onClose} />
      ) : null}
    </ExpandModal>
  );
}

function SubscriptionForm({
  current,
  onDone,
}: Readonly<{
  current: StrategySubscription | null;
  onDone: () => void;
}>): ReactNode {
  const t = useTranslations("watch");
  const groups = useWatchlists("TW");
  const save = useSaveStrategySubscription();
  const [strategies, setStrategies] = useState<StrategyKey[]>(
    current?.strategies ?? [...STRATEGY_KEYS],
  );
  const [groupId, setGroupId] = useState<string | null>(current?.watchlist_id ?? null);
  const [morningList, setMorningList] = useState(current?.morning_list ?? true);
  const [dailyLimit, setDailyLimit] = useState<number | null>(current?.daily_limit ?? 10);
  const [enabled, setEnabled] = useState(current?.enabled ?? true);
  const groupOptions = groups.data ?? [];

  const toggle = (key: StrategyKey): void => {
    setStrategies((now) =>
      now.includes(key)
        ? now.filter((k) => k !== key)
        : STRATEGY_KEYS.filter((k) => k === key || now.includes(k)),
    );
  };

  const submit = (event: FormEvent): void => {
    event.preventDefault();
    if (strategies.length === 0) return;
    save.mutate(
      {
        strategies,
        watchlist_id: groupId,
        morning_list: morningList,
        daily_limit: dailyLimit,
        enabled,
        ...(current ? { expected_version: current.version } : {}),
      },
      { onSuccess: onDone },
    );
  };

  const error =
    save.error instanceof StrategyWatchApiError && save.error.status === 409
      ? t("form.conflict")
      : save.error
        ? t("form.failed")
        : null;

  return (
    <form onSubmit={submit} className="space-y-5 text-[13px]">
      <fieldset>
        <legend className="mb-2 font-medium text-[var(--text)]">{t("form.strategies")}</legend>
        <div className="space-y-2">
          {STRATEGY_KEYS.map((key) => (
            <label key={key} className="flex min-h-11 cursor-pointer items-start gap-2.5">
              <input
                type="checkbox"
                checked={strategies.includes(key)}
                onChange={() => toggle(key)}
                className="mt-1 size-4 accent-[var(--accent)]"
              />
              <span>
                <span className="block text-[var(--text)]">{t(`strategies.${key}.name`)}</span>
                <span className="block text-[12px] text-[var(--text-muted)]">
                  {t(`strategies.${key}.desc`)}
                </span>
              </span>
            </label>
          ))}
        </div>
        {strategies.length === 0 ? (
          <p className="mt-1 text-[12px] text-[var(--down)]" role="alert">
            {t("form.needOne")}
          </p>
        ) : null}
      </fieldset>

      <fieldset>
        <legend className="mb-2 font-medium text-[var(--text)]">{t("form.scope")}</legend>
        <label className="flex min-h-11 items-center gap-2.5">
          <input
            type="radio"
            name="watch-scope"
            checked={groupId === null}
            onChange={() => setGroupId(null)}
            className="size-4 accent-[var(--accent)]"
          />
          {t("form.scopeAll")}
        </label>
        <label className="flex min-h-11 items-center gap-2.5">
          <input
            type="radio"
            name="watch-scope"
            checked={groupId !== null}
            disabled={groupOptions.length === 0}
            onChange={() => setGroupId(groupOptions[0]?.group.id ?? null)}
            className="size-4 accent-[var(--accent)]"
          />
          {t("form.scopeGroup")}
        </label>
        {groupId !== null ? (
          <label className="mt-1 block pl-6">
            <span className="sr-only">{t("form.group")}</span>
            <select
              value={groupId}
              onChange={(e) => setGroupId(e.target.value)}
              className="min-h-11 w-full rounded-[var(--radius-input)] border border-[var(--border)] bg-[var(--surface)] px-2"
            >
              {groupOptions.map((group) => (
                <option key={group.group.id} value={group.group.id}>
                  {group.group.name}
                </option>
              ))}
            </select>
          </label>
        ) : groupOptions.length === 0 ? (
          <p className="pl-6 text-[12px] text-[var(--text-muted)]">{t("form.noGroups")}</p>
        ) : null}
      </fieldset>

      <label className="flex min-h-11 items-center gap-2.5">
        <input
          type="checkbox"
          checked={morningList}
          onChange={(e) => setMorningList(e.target.checked)}
          className="size-4 accent-[var(--accent)]"
        />
        {t("form.morningList")}
      </label>

      <label className="block">
        <span className="mb-1 block font-medium text-[var(--text)]">{t("form.dailyLimit")}</span>
        <select
          value={dailyLimit ?? ""}
          onChange={(e) => setDailyLimit(e.target.value === "" ? null : Number(e.target.value))}
          className="min-h-11 w-full rounded-[var(--radius-input)] border border-[var(--border)] bg-[var(--surface)] px-2"
        >
          {DAILY_LIMITS.map((limit) => (
            <option key={limit} value={limit}>
              {limit}
            </option>
          ))}
          <option value="">{t("form.noLimit")}</option>
        </select>
      </label>

      <label className="flex min-h-11 items-center gap-2.5">
        <input
          type="checkbox"
          checked={enabled}
          onChange={(e) => setEnabled(e.target.checked)}
          className="size-4 accent-[var(--accent)]"
        />
        {t("form.enabled")}
      </label>

      <p className="text-[12px] text-[var(--text-muted)]">{t("form.channels")}</p>
      {error ? (
        <p className="text-[12.5px] text-[var(--down)]" role="alert">
          {error}
        </p>
      ) : null}

      <div className="flex justify-end gap-2">
        <button
          type="button"
          onClick={onDone}
          className="min-h-11 rounded-[var(--radius-input)] px-4 text-[var(--text-muted)] hover:bg-[var(--surface-2)]"
        >
          {t("form.cancel")}
        </button>
        <button
          type="submit"
          disabled={save.isPending || strategies.length === 0}
          className="min-h-11 rounded-[var(--radius-input)] bg-[var(--accent)] px-4 font-medium text-[var(--on-accent)] disabled:opacity-50"
        >
          {save.isPending ? t("form.saving") : t("form.save")}
        </button>
      </div>
    </form>
  );
}
