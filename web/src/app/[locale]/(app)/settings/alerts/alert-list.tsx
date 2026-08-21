"use client";

import { PlusIcon, TrashIcon } from "@phosphor-icons/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { type ReactNode, useState } from "react";
import { ConfirmDialog } from "@/components/settings/confirm-dialog";
import { ToggleSwitch } from "@/components/settings/form-controls";
import { SettingsError, SettingsLoading } from "@/components/settings/settings-states";
import { Button } from "@/components/ui/button";
import { deleteAlert, listAlerts, updateAlert } from "@/lib/api/settings";
import type { AlertRule } from "@/types/settings";
import { AlertForm } from "./alert-form";

/** Alert-rule list (interaction-spec §2.13): cold-start empty state with CTA, enable/disable
 * toggle, delete via focus-trapped confirm dialog. */
export function AlertList(): ReactNode {
  const t = useTranslations("settings.alerts");
  const tc = useTranslations("settings.common");
  const queryClient = useQueryClient();

  const query = useQuery({ queryKey: ["settings", "alerts"], queryFn: listAlerts });
  const [creating, setCreating] = useState(false);
  const [pendingDelete, setPendingDelete] = useState<AlertRule | null>(null);

  const toggleMutation = useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) => updateAlert(id, enabled),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["settings", "alerts"] }),
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => deleteAlert(id),
    onSuccess: () => {
      setPendingDelete(null);
      void queryClient.invalidateQueries({ queryKey: ["settings", "alerts"] });
    },
  });

  if (query.isLoading) {
    return <SettingsLoading rows={2} />;
  }

  if (query.isError) {
    return <SettingsError message={tc("errorLoad")} onRetry={() => void query.refetch()} />;
  }

  const alerts = query.data ?? [];

  return (
    <div>
      {alerts.length === 0 && !creating ? (
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
            {alerts.map((alert) => (
              <li key={alert.id} className="flex items-center justify-between gap-3 py-3">
                <div className="min-w-0">
                  <p className="font-medium text-[14px] text-[var(--text)]">
                    {alert.symbol} · {tc(`marketOption.${alert.market}`)}
                  </p>
                  <p className="truncate text-[12px] text-[var(--text-muted)]">
                    {alert.condition.metric}{" "}
                    {t(`form.comparatorOption.${alert.condition.comparator}`)}{" "}
                    {alert.condition.threshold}
                  </p>
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  <ToggleSwitch
                    checked={alert.enabled}
                    onChange={(next) => toggleMutation.mutate({ id: alert.id, enabled: next })}
                    label={t("toggleLabel", { symbol: alert.symbol })}
                  />
                  <button
                    type="button"
                    onClick={() => setPendingDelete(alert)}
                    aria-label={t("deleteLabel", { symbol: alert.symbol })}
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
          <AlertForm onDone={() => setCreating(false)} />
        </div>
      ) : null}

      <ConfirmDialog
        open={pendingDelete !== null}
        title={t("deleteConfirmTitle")}
        description={
          pendingDelete ? t("deleteConfirmBody", { symbol: pendingDelete.symbol }) : undefined
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
