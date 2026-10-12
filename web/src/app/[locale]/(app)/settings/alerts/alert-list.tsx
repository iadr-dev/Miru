"use client";

import {
  BellSimpleIcon,
  CaretDownIcon,
  CaretRightIcon,
  PencilSimpleIcon,
  PlusIcon,
  TrashIcon,
} from "@phosphor-icons/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useFormatter, useTranslations } from "next-intl";
import { type ReactNode, useEffect, useRef, useState } from "react";
import { ConfirmDialog } from "@/components/settings/confirm-dialog";
import { ToggleSwitch } from "@/components/settings/form-controls";
import { SettingsError, SettingsLoading } from "@/components/settings/settings-states";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { conditionSummary, ruleStatus } from "@/lib/alerts/condition";
import { deleteAlert, listAlerts, updateAlert } from "@/lib/api/settings";
import { getWatchlists } from "@/lib/api/watchlist";
import { cn } from "@/lib/cn";
import { parseServerDate } from "@/lib/parse-server-date";
import type { AlertRule } from "@/types/settings";
import { AlertForm, type AlertFormPreset } from "./alert-form";

/** Alert-rule list: cold-start empty
 * state with CTA, a plain-language condition line, lifecycle status, last-fired/expiry, enable
 * toggle, and delete via a focus-trapped confirm dialog. */
export function AlertList({ preset }: { preset?: AlertFormPreset | undefined }): ReactNode {
  const t = useTranslations("settings.alerts");
  const ts = useTranslations("settings.alerts.summary");
  const tc = useTranslations("settings.common");
  const format = useFormatter();
  const queryClient = useQueryClient();

  const query = useQuery({ queryKey: ["settings", "alerts"], queryFn: listAlerts });
  // The watchlist groups, to name a group rule ("半導體 · 7 檔") and show where a per-stock rule
  // came from. Not required to render the list — an unknown group just falls back to a generic label.
  const groups = useQuery({
    queryKey: ["settings", "alert-watchlists"],
    queryFn: () => getWatchlists(),
  });
  const [creating, setCreating] = useState(preset !== undefined);
  const formRef = useRef<HTMLDivElement>(null);
  // Opened from the watchlist (a preset): bring the form into view instead of leaving it below a long list.
  useEffect(() => {
    if (preset !== undefined) {
      formRef.current?.scrollIntoView({ block: "nearest" });
    }
  }, [preset]);
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(new Set());
  const [editing, setEditing] = useState<AlertRule | null>(null);
  const [pendingDelete, setPendingDelete] = useState<AlertRule | null>(null);

  const toggleMutation = useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) => updateAlert(id, { enabled }),
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
  const now = new Date();
  const shortDate = (iso: string): string =>
    format.dateTime(parseServerDate(iso), { dateStyle: "medium" });

  const groupOf = (id: string | null) => (groups.data ?? []).find((g) => g.group.id === id);

  const scopeLabel = (alert: AlertRule): string => {
    if (alert.symbol && alert.market) {
      return `${alert.symbol} · ${tc(`marketOption.${alert.market}`)}`;
    }
    const view = groupOf(alert.watchlist_id);
    return view
      ? t("groupScope", { name: view.group.name, count: view.items.length })
      : t("watchlistScope");
  };

  /** The group's members with whether each currently meets the rule (the engine records this per
   * symbol after every evaluation; a member it has not evaluated yet shows as pending). */
  const memberStates = (alert: AlertRule): { symbol: string; met: boolean | null }[] => {
    const view = groupOf(alert.watchlist_id);
    const met = (alert.state.met ?? {}) as Record<string, unknown>;
    return (view?.items ?? []).map((item) => ({
      symbol: item.symbol,
      met: typeof met[item.symbol] === "boolean" ? (met[item.symbol] as boolean) : null,
    }));
  };

  const toggleExpanded = (id: string): void => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  };

  const metaLine = (alert: AlertRule): string => {
    const fired = alert.last_triggered
      ? t("lastFired", { date: shortDate(alert.last_triggered) })
      : t("neverFired");
    const expires = alert.expires_at
      ? t("expiresOn", { date: shortDate(alert.expires_at) })
      : t("neverExpires");
    return `${fired} · ${expires}`;
  };

  return (
    <div>
      {alerts.length === 0 && !creating ? (
        <EmptyState
          compact
          icon={<BellSimpleIcon size={20} weight="regular" />}
          title={t("empty")}
          action={
            <Button variant="filled" size="sm" onClick={() => setCreating(true)}>
              <PlusIcon size={16} weight="regular" aria-hidden />
              {t("createCta")}
            </Button>
          }
        />
      ) : (
        <>
          <ul className="divide-y divide-[var(--border)]">
            {alerts.map((alert) => {
              const summary = conditionSummary(alert.condition);
              const status = ruleStatus(alert, now);
              return (
                <li key={alert.id} className="flex items-center justify-between gap-3 py-3">
                  <div className="min-w-0">
                    <p className="flex items-center gap-2 font-medium text-[14px] text-[var(--text)]">
                      {alert.watchlist_id ? (
                        <button
                          type="button"
                          onClick={() => toggleExpanded(alert.id)}
                          aria-expanded={expanded.has(alert.id)}
                          aria-label={t("toggleMembers", { name: scopeLabel(alert) })}
                          className="inline-flex size-6 items-center justify-center rounded text-[var(--text-muted)] hover:text-[var(--text)]"
                        >
                          {expanded.has(alert.id) ? (
                            <CaretDownIcon size={14} weight="regular" />
                          ) : (
                            <CaretRightIcon size={14} weight="regular" />
                          )}
                        </button>
                      ) : null}
                      <span>{scopeLabel(alert)}</span>
                      {alert.origin_watchlist_id ? (
                        <span className="rounded-full border border-[var(--border)] px-2 py-0.5 font-normal text-[11px] text-[var(--text-muted)]">
                          {t("fromGroup", {
                            name:
                              groupOf(alert.origin_watchlist_id)?.group.name ?? t("deletedGroup"),
                          })}
                        </span>
                      ) : null}
                      <span
                        className={cn(
                          "rounded-full border px-2 py-0.5 font-normal text-[11px]",
                          status === "active"
                            ? "border-[var(--accent)] text-[var(--accent-text)]"
                            : "border-[var(--border)] text-[var(--text-muted)]",
                        )}
                      >
                        {t(`status.${status}`)}
                      </span>
                    </p>
                    <p className="truncate text-[13px] text-[var(--text)]">
                      {ts(summary.key, summary.values)}
                    </p>
                    <p className="truncate text-[12px] text-[var(--text-muted)]">
                      {metaLine(alert)}
                    </p>
                    {alert.watchlist_id && expanded.has(alert.id) ? (
                      <ul className="mt-2 flex flex-wrap gap-1.5">
                        {memberStates(alert).map((member) => (
                          <li
                            key={member.symbol}
                            className={cn(
                              "rounded-full border px-2 py-0.5 text-[12px]",
                              member.met === true
                                ? "border-[var(--accent)] text-[var(--accent-text)]"
                                : "border-[var(--border)] text-[var(--text-muted)]",
                            )}
                          >
                            {member.symbol} ·{" "}
                            {member.met === null
                              ? t("memberPending")
                              : member.met
                                ? t("memberMet")
                                : t("memberNotMet")}
                          </li>
                        ))}
                      </ul>
                    ) : null}
                  </div>
                  <div className="flex shrink-0 items-center gap-2">
                    <ToggleSwitch
                      checked={alert.enabled}
                      onChange={(next) => toggleMutation.mutate({ id: alert.id, enabled: next })}
                      label={t("toggleLabel", { symbol: scopeLabel(alert) })}
                    />
                    <button
                      type="button"
                      onClick={() => {
                        setCreating(false);
                        setEditing(alert);
                      }}
                      aria-label={t("editLabel", { symbol: scopeLabel(alert) })}
                      className="inline-flex size-11 items-center justify-center rounded-[var(--radius-button)] text-[var(--text-muted)] hover:bg-[var(--surface-2)] hover:text-[var(--text)]"
                    >
                      <PencilSimpleIcon size={18} weight="regular" />
                    </button>
                    <button
                      type="button"
                      onClick={() => setPendingDelete(alert)}
                      aria-label={t("deleteLabel", { symbol: scopeLabel(alert) })}
                      className="inline-flex size-11 items-center justify-center rounded-[var(--radius-button)] text-[var(--text-muted)] hover:bg-[var(--surface-2)] hover:text-[var(--warn)]"
                    >
                      <TrashIcon size={18} weight="regular" />
                    </button>
                  </div>
                </li>
              );
            })}
          </ul>
          {!creating ? (
            <Button variant="outline" size="sm" className="mt-4" onClick={() => setCreating(true)}>
              <PlusIcon size={16} weight="regular" aria-hidden />
              {t("createCta")}
            </Button>
          ) : null}
        </>
      )}

      {toggleMutation.isError ? (
        <p role="alert" className="mt-3 text-[13px] text-[var(--warn)]">
          {tc("errorGeneric")}
        </p>
      ) : null}

      {creating ? (
        <div ref={formRef} className="mt-4">
          <AlertForm onDone={() => setCreating(false)} preset={preset} />
        </div>
      ) : null}
      {editing ? (
        <div className="mt-4">
          {/* Keyed by id+version. After a 409 the form merges the latest server copy itself and
              keeps the user's edits, so the list refetch must not remount it. */}
          <AlertForm
            key={`${editing.id}:${editing.version}`}
            rule={editing}
            onDone={() => setEditing(null)}
          />
        </div>
      ) : null}

      <ConfirmDialog
        open={pendingDelete !== null}
        title={t("deleteConfirmTitle")}
        description={
          pendingDelete ? t("deleteConfirmBody", { symbol: scopeLabel(pendingDelete) }) : undefined
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
