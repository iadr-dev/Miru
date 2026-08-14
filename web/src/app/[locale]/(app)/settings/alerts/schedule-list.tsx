"use client";

import { PlusIcon, TrashIcon } from "@phosphor-icons/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { type FormEvent, type ReactNode, useState } from "react";
import { ConfirmDialog } from "@/components/settings/confirm-dialog";
import { Select, TextInput, ToggleSwitch } from "@/components/settings/form-controls";
import { SettingsError, SettingsLoading } from "@/components/settings/settings-states";
import { Button } from "@/components/ui/button";
import { createSchedule, deleteSchedule, listSchedules, updateSchedule } from "@/lib/api/settings";
import {
  MARKETS,
  type Market,
  SCHEDULE_TRIGGER_TYPES,
  type ScheduleJob,
  type ScheduleTrigger,
} from "@/types/settings";

/** Schedules — same shape/pattern as alerts, kept as a simpler second block on the same page
 * (interaction-spec §2.13 leaves the split to implementer judgment). */
export function ScheduleList(): ReactNode {
  const t = useTranslations("settings.alerts.schedules");
  const tc = useTranslations("settings.common");
  const queryClient = useQueryClient();

  const query = useQuery({ queryKey: ["settings", "schedules"], queryFn: listSchedules });
  const [creating, setCreating] = useState(false);
  const [pendingDelete, setPendingDelete] = useState<ScheduleJob | null>(null);

  const toggleMutation = useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) =>
      updateSchedule(id, { enabled }),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["settings", "schedules"] }),
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => deleteSchedule(id),
    onSuccess: () => {
      setPendingDelete(null);
      void queryClient.invalidateQueries({ queryKey: ["settings", "schedules"] });
    },
  });

  if (query.isLoading) {
    return <SettingsLoading rows={2} />;
  }

  if (query.isError) {
    return <SettingsError message={tc("errorLoad")} onRetry={() => void query.refetch()} />;
  }

  const schedules = query.data ?? [];

  return (
    <div>
      {schedules.length === 0 && !creating ? (
        <div className="py-6 text-center">
          <p className="text-[14px] text-[var(--text-muted)]">{t("empty")}</p>
          <Button variant="filled" size="sm" className="mt-3" onClick={() => setCreating(true)}>
            <PlusIcon size={16} weight="regular" aria-hidden />
            {t("createCta")}
          </Button>
        </div>
      ) : (
        <>
          <ul className="divide-y divide-[var(--border)]">
            {schedules.map((job) => (
              <li key={job.id} className="flex items-center justify-between gap-3 py-3">
                <div className="min-w-0">
                  <p className="font-medium text-[14px] text-[var(--text)]">
                    {job.action.skill_ref} · {job.trigger.market}
                  </p>
                  <p className="truncate text-[12px] text-[var(--text-muted)]">
                    {t(`triggerTypeOption.${job.trigger.type}`)} — {job.trigger.spec}
                  </p>
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  <ToggleSwitch
                    checked={job.enabled}
                    onChange={(next) => toggleMutation.mutate({ id: job.id, enabled: next })}
                    label={t("toggleLabel", { skill: job.action.skill_ref })}
                  />
                  <button
                    type="button"
                    onClick={() => setPendingDelete(job)}
                    aria-label={t("deleteLabel", { skill: job.action.skill_ref })}
                    className="inline-flex size-11 items-center justify-center rounded-[var(--radius-button)] text-[var(--text-muted)] hover:bg-[var(--surface-2)] hover:text-[var(--warn)]"
                  >
                    <TrashIcon size={18} weight="regular" />
                  </button>
                </div>
              </li>
            ))}
          </ul>
          {!creating ? (
            <Button variant="outline" size="sm" className="mt-4" onClick={() => setCreating(true)}>
              <PlusIcon size={16} weight="regular" aria-hidden />
              {t("createCta")}
            </Button>
          ) : null}
        </>
      )}

      {creating ? (
        <div className="mt-4">
          <ScheduleForm onDone={() => setCreating(false)} />
        </div>
      ) : null}

      <ConfirmDialog
        open={pendingDelete !== null}
        title={t("deleteConfirmTitle")}
        description={
          pendingDelete
            ? t("deleteConfirmBody", { skill: pendingDelete.action.skill_ref })
            : undefined
        }
        confirmLabel={tc("delete")}
        cancelLabel={tc("cancel")}
        onCancel={() => setPendingDelete(null)}
        onConfirm={() => {
          if (pendingDelete) {
            deleteMutation.mutate(pendingDelete.id);
          }
        }}
      />
    </div>
  );
}

function ScheduleForm({ onDone }: { onDone: () => void }): ReactNode {
  const t = useTranslations("settings.alerts.schedules.form");
  const queryClient = useQueryClient();

  const [triggerType, setTriggerType] = useState<ScheduleTrigger["type"]>("cron");
  const [spec, setSpec] = useState("");
  const [market, setMarket] = useState<Market>("TW");
  const [skillRef, setSkillRef] = useState("");
  const [error, setError] = useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: createSchedule,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["settings", "schedules"] });
      onDone();
    },
  });

  const onSubmit = (e: FormEvent<HTMLFormElement>): void => {
    e.preventDefault();
    if (!spec.trim() || !skillRef.trim()) {
      setError(t("errorRequired"));
      return;
    }
    setError(null);
    mutation.mutate({
      trigger: { type: triggerType, spec: spec.trim(), market },
      action: { skill_ref: skillRef.trim(), inputs: {} },
      delivery_channels: ["in_app"],
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
          <label
            className="mb-1 block text-[13px] text-[var(--text-muted)]"
            htmlFor="schedule-trigger-type"
          >
            {t("triggerType")}
          </label>
          <Select
            id="schedule-trigger-type"
            value={triggerType}
            onChange={(e) => setTriggerType(e.target.value as ScheduleTrigger["type"])}
          >
            {SCHEDULE_TRIGGER_TYPES.map((v) => (
              <option key={v} value={v}>
                {t(`triggerTypeOption.${v}`)}
              </option>
            ))}
          </Select>
        </div>
        <div>
          <label
            className="mb-1 block text-[13px] text-[var(--text-muted)]"
            htmlFor="schedule-market"
          >
            {t("market")}
          </label>
          <Select
            id="schedule-market"
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
            htmlFor="schedule-spec"
          >
            {t("spec")}
          </label>
          <TextInput
            id="schedule-spec"
            value={spec}
            onChange={(e) => setSpec(e.target.value)}
            placeholder={triggerType === "cron" ? "0 9 * * 1-5" : "market_open"}
          />
        </div>
        <div>
          <label
            className="mb-1 block text-[13px] text-[var(--text-muted)]"
            htmlFor="schedule-skill"
          >
            {t("skillRef")}
          </label>
          <TextInput
            id="schedule-skill"
            value={skillRef}
            onChange={(e) => setSkillRef(e.target.value)}
            placeholder="daily_digest"
          />
        </div>
      </fieldset>
      {error ? (
        <p role="alert" className="text-[12px] text-[var(--warn)]">
          {error}
        </p>
      ) : null}
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
