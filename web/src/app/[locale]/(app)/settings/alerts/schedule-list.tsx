"use client";

import { CalendarBlankIcon, PencilSimpleIcon, PlusIcon, TrashIcon } from "@phosphor-icons/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useFormatter, useTranslations } from "next-intl";
import { type FormEvent, type ReactNode, useState } from "react";
import { ConfirmDialog } from "@/components/settings/confirm-dialog";
import { Select, TextInput, ToggleSwitch } from "@/components/settings/form-controls";
import { SettingsError, SettingsLoading } from "@/components/settings/settings-states";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { useScheduleCadence } from "@/hooks/use-schedule-cadence";
import { Link } from "@/i18n/navigation";
import { queryKeys } from "@/lib/api/query-keys";
import {
  createSchedule,
  deleteSchedule,
  listSchedules,
  SettingsApiError,
  updateSchedule,
} from "@/lib/api/settings";
import { getWatchlists } from "@/lib/api/watchlist";
import { parseServerDate } from "@/lib/parse-server-date";
import {
  buildScheduleAction,
  buildWatchlistAction,
  buildWeeklyTrigger,
  checkTargets,
  DEFAULT_WEEKLY_WHEN,
  isTimeOfDay,
  MAX_TARGETS,
  SCHEDULE_TEMPLATE_IDS,
  SCHEDULE_TEMPLATES,
  type ScheduleTemplate,
  type ScheduleTemplateId,
  supportsWatchlist,
  type TargetCheck,
  targetsOfJob,
  templateOfJob,
  WEEKDAY_ORDER,
  type Weekday,
  type WeeklyWhen,
  watchlistOfJob,
  weeklyWhenOfTrigger,
} from "@/lib/schedules/templates";
import { groupNameStyle } from "@/lib/watchlist-color";
import type { ScheduleJob } from "@/types/settings";
import type { WatchlistGroupView } from "@/types/watchlist";

/** Schedules — scheduled briefings (docs/backend/modules/14 §4). Template-first, like alerts: the
 * user picks a briefing (盤前簡報, 收盤籌碼…) and the stock(s); the trigger and skill come from the
 * catalogue, so nobody types a cron or a skill name. Each row shows when it runs next, how the last
 * run went (and why, if it didn't), and links to the one thread every run is appended to. */
export function ScheduleList(): ReactNode {
  const t = useTranslations("settings.alerts.schedules");
  const tt = useTranslations("settings.alerts.schedules.templates");
  const tc = useTranslations("settings.common");
  const cadenceText = useScheduleCadence();
  const format = useFormatter();
  const queryClient = useQueryClient();

  const query = useQuery({ queryKey: queryKeys.settings("schedules"), queryFn: listSchedules });
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<ScheduleJob | null>(null);
  const [pendingDelete, setPendingDelete] = useState<ScheduleJob | null>(null);

  const toggleMutation = useMutation({
    mutationFn: ({ job, enabled }: { job: ScheduleJob; enabled: boolean }) =>
      updateSchedule(job.id, { enabled, expected_version: job.version }),
    onSuccess: () =>
      void queryClient.invalidateQueries({ queryKey: queryKeys.settings("schedules") }),
    onError: () =>
      void queryClient.invalidateQueries({ queryKey: queryKeys.settings("schedules") }),
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => deleteSchedule(id),
    onSuccess: () => {
      setPendingDelete(null);
      void queryClient.invalidateQueries({ queryKey: queryKeys.settings("schedules") });
    },
  });

  if (query.isLoading) {
    return <SettingsLoading rows={2} />;
  }

  if (query.isError) {
    return <SettingsError message={tc("errorLoad")} onRetry={() => void query.refetch()} />;
  }

  const schedules = query.data ?? [];
  const when = (iso: string): string =>
    format.dateTime(parseServerDate(iso), { dateStyle: "medium", timeStyle: "short" });

  const titleOf = (job: ScheduleJob): string => {
    const template = templateOfJob(job);
    if (!template) {
      return job.action.skill_ref;
    }
    const label = tt(`${template.id}.label`);
    if (watchlistOfJob(job) !== null) {
      return `${label} · ${t("form.scopeWatchlist")}`;
    }
    const targets = targetsOfJob(job, template);
    return targets.length > 0 ? `${label} · ${targets.join("、")}` : label;
  };

  const cadenceOf = (job: ScheduleJob): string => {
    const template = templateOfJob(job);
    return template
      ? cadenceText(template, job.trigger)
      : `${job.trigger.type} — ${job.trigger.spec}`;
  };

  const statusLine = (job: ScheduleJob): string => {
    if (!job.enabled) {
      return t("paused");
    }
    const parts: string[] = [];
    if (job.next_run) {
      parts.push(t("nextRun", { when: when(job.next_run) }));
    }
    if (job.last_run) {
      parts.push(t("lastRun", { when: when(job.last_run) }));
    }
    return parts.join(" · ") || t("neverRun");
  };

  const problemOf = (job: ScheduleJob): string | null => {
    if (job.status === "error") {
      return t("lastRunFailed");
    }
    if (job.status === "skipped_quota") {
      return t("lastRunSkippedQuota");
    }
    return null;
  };

  return (
    <div>
      {schedules.length === 0 && !creating ? (
        <EmptyState
          compact
          icon={<CalendarBlankIcon size={20} weight="regular" />}
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
            {schedules.map((job) => {
              const problem = problemOf(job);
              return (
                <li key={job.id} className="flex items-center justify-between gap-3 py-3">
                  <div className="min-w-0">
                    <p className="font-medium text-[14px] text-[var(--text)]">{titleOf(job)}</p>
                    <p className="truncate text-[12px] text-[var(--text-muted)]">
                      {cadenceOf(job)} · {statusLine(job)}
                    </p>
                    {problem ? <p className="text-[12px] text-[var(--warn)]">{problem}</p> : null}
                    {job.thread_id ? (
                      <Link
                        href={`/chat?thread=${encodeURIComponent(job.thread_id)}`}
                        className="text-[12px] text-[var(--accent-text)] underline-offset-2 hover:underline"
                      >
                        {t("viewThread")}
                      </Link>
                    ) : null}
                  </div>
                  <div className="flex shrink-0 items-center gap-2">
                    <ToggleSwitch
                      checked={job.enabled}
                      onChange={(next) => toggleMutation.mutate({ job, enabled: next })}
                      label={t("toggleLabel", { skill: titleOf(job) })}
                    />
                    <button
                      type="button"
                      onClick={() => {
                        setCreating(false);
                        setEditing(job);
                      }}
                      aria-label={t("editLabel", { skill: titleOf(job) })}
                      className="inline-flex size-11 items-center justify-center rounded-[var(--radius-button)] text-[var(--text-muted)] hover:bg-[var(--surface-2)] hover:text-[var(--text)]"
                    >
                      <PencilSimpleIcon size={18} weight="regular" />
                    </button>
                    <button
                      type="button"
                      onClick={() => setPendingDelete(job)}
                      aria-label={t("deleteLabel", { skill: titleOf(job) })}
                      className="inline-flex size-11 items-center justify-center rounded-[var(--radius-button)] text-[var(--text-muted)] hover:bg-[var(--surface-2)] hover:text-[var(--warn)]"
                    >
                      <TrashIcon size={18} weight="regular" />
                    </button>
                  </div>
                </li>
              );
            })}
          </ul>
          {!creating && !editing ? (
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
        <div className="mt-4">
          <ScheduleForm onDone={() => setCreating(false)} />
        </div>
      ) : null}
      {editing ? (
        <div className="mt-4">
          <ScheduleForm key={editing.id} job={editing} onDone={() => setEditing(null)} />
        </div>
      ) : null}

      <ConfirmDialog
        open={pendingDelete !== null}
        title={t("deleteConfirmTitle")}
        description={
          pendingDelete ? t("deleteConfirmBody", { skill: titleOf(pendingDelete) }) : undefined
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

/** Which message explains a failed save: the two the user can act on, else the generic one. */
function errorKeyFor(error: unknown): "errorCap" | "errorStale" | "errorGeneric" {
  if (error instanceof SettingsApiError) {
    if (error.code === "cap_reached") {
      return "errorCap";
    }
    if (error.code === "stale_version") {
      return "errorStale";
    }
  }
  return "errorGeneric";
}

/** The form's starting values: the job's own when editing, the first template and blanks when not. */
function initialFormState(job: ScheduleJob | undefined): {
  templateId: ScheduleTemplateId;
  targetsText: string;
  followList: boolean;
  watchlistId: string;
  when: WeeklyWhen;
} {
  const jobTemplate = job ? templateOfJob(job) : null;
  const watchlist = job ? watchlistOfJob(job) : null;
  return {
    templateId: jobTemplate?.id ?? SCHEDULE_TEMPLATE_IDS[0],
    targetsText: job && jobTemplate ? targetsOfJob(job, jobTemplate).join(", ") : "",
    followList: watchlist !== null,
    watchlistId: watchlist ?? "",
    when: job ? weeklyWhenOfTrigger(job.trigger) : DEFAULT_WEEKLY_WHEN,
  };
}

/** Create the schedule, or update `job` (with its version) when editing. */
function saveSchedule({
  job,
  template,
  following,
  watchlistId,
  targets,
  when,
}: Readonly<{
  job: ScheduleJob | undefined;
  template: ScheduleTemplate;
  following: boolean;
  watchlistId: string;
  targets: string[];
  when: WeeklyWhen;
}>): Promise<ScheduleJob> {
  const action = following
    ? buildWatchlistAction(template, watchlistId)
    : buildScheduleAction(template, targets);
  // The weekly review runs when the user says; every other template has its catalogue trigger.
  const trigger = template.takesTargets ? template.trigger : buildWeeklyTrigger(when);
  if (job) {
    return updateSchedule(job.id, {
      action,
      ...(template.takesTargets ? {} : { trigger }),
      expected_version: job.version,
    });
  }
  return createSchedule({ trigger, action, delivery_channels: ["in_app"] });
}

function isStaleVersion(error: unknown): boolean {
  return error instanceof SettingsApiError && error.code === "stale_version";
}

/** What to save on submit — the typed targets, `[]` for a followed group — or null while the
 * form is not valid yet. */
function targetsToSave(
  following: boolean,
  watchlistId: string,
  check: TargetCheck,
): string[] | null {
  if (following) return watchlistId === "" ? null : [];
  return check.ok ? check.targets : null;
}

/** Create or edit a schedule. Editing keeps the briefing type (changing it is delete + re-create,
 * like an alert's symbol) and lets the user change the stock(s). A 409 means another device saved
 * first: the list is refreshed and the user is told to reopen — the edit is a single field. */
function ScheduleForm({
  onDone,
  job,
}: Readonly<{ onDone: () => void; job?: ScheduleJob }>): ReactNode {
  const t = useTranslations("settings.alerts.schedules.form");
  const queryClient = useQueryClient();

  const editing = job !== undefined;
  const initial = initialFormState(job);
  const [templateId, setTemplateId] = useState<ScheduleTemplateId>(initial.templateId);
  const template: ScheduleTemplate = SCHEDULE_TEMPLATES[templateId];
  const [targetsText, setTargetsText] = useState(initial.targetsText);
  // "my watchlist" instead of typed stocks (pre-open briefing only): the group is expanded at run
  // time, so the briefing tracks the list as it changes.
  const [followList, setFollowList] = useState(initial.followList);
  const [watchlistId, setWatchlistId] = useState(initial.watchlistId);
  const [weekday, setWeekday] = useState<Weekday>(initial.when.weekday);
  const [time, setTime] = useState(initial.when.time);
  const canFollow = supportsWatchlist(template);
  const following = canFollow && followList;
  const groups = useQuery({
    queryKey: queryKeys.settings("schedule-watchlists"),
    queryFn: () => getWatchlists("TW"),
    enabled: canFollow,
  });
  const [submitted, setSubmitted] = useState(false);

  const mutation = useMutation({
    mutationFn: (targets: string[]) =>
      saveSchedule({ job, template, following, watchlistId, targets, when: { weekday, time } }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.settings("schedules") });
      onDone();
    },
    onError: (error) => {
      if (isStaleVersion(error)) {
        void queryClient.invalidateQueries({ queryKey: queryKeys.settings("schedules") });
      }
    },
  });

  const check = checkTargets(template, targetsText);
  const takesTargets = template.takesTargets;
  const targetError = submitted && takesTargets && !following && !check.ok ? check.reason : null;
  const watchlistError = submitted && following && watchlistId === "";
  const timeError = submitted && !takesTargets && !isTimeOfDay(time);

  const onSubmit = (e: FormEvent<HTMLFormElement>): void => {
    e.preventDefault();
    setSubmitted(true);
    const targets = takesTargets ? targetsToSave(following, watchlistId, check) : [];
    if (targets !== null && (takesTargets || isTimeOfDay(time))) mutation.mutate(targets);
  };

  return (
    <form
      aria-label={editing ? t("editTitle") : t("title")}
      onSubmit={onSubmit}
      onKeyDown={(e) => {
        if (e.key === "Escape") {
          e.stopPropagation();
          onDone();
        }
      }}
      className="space-y-4 rounded-[var(--radius-input)] border border-[var(--border)] bg-[var(--surface-2)] p-4"
    >
      <fieldset className="grid gap-3 sm:grid-cols-2">
        <TemplateField templateId={templateId} disabled={editing} onChange={setTemplateId} />
        {takesTargets ? null : <TemplateDescription templateId={templateId} />}
        {canFollow ? <ScopeField followList={followList} onChange={setFollowList} /> : null}
        {takesTargets ? null : (
          <WhenFields
            weekday={weekday}
            time={time}
            onWeekday={setWeekday}
            onTime={setTime}
            showTimeError={timeError}
          />
        )}
        {following && takesTargets ? (
          <WatchlistField
            watchlistId={watchlistId}
            onChange={setWatchlistId}
            groups={groups.data ?? []}
            showError={watchlistError}
          />
        ) : null}
        {!following && takesTargets ? (
          <TargetsField
            template={template}
            value={targetsText}
            onChange={setTargetsText}
            error={targetError}
          />
        ) : null}
      </fieldset>
      <p className="text-[12px] text-[var(--text-muted)]">{t("note")}</p>
      {mutation.isError ? (
        <p role="alert" className="text-[13px] text-[var(--warn)]">
          {t(errorKeyFor(mutation.error))}
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

function TemplateField({
  templateId,
  disabled,
  onChange,
}: Readonly<{
  templateId: ScheduleTemplateId;
  disabled: boolean;
  onChange: (id: ScheduleTemplateId) => void;
}>): ReactNode {
  const t = useTranslations("settings.alerts.schedules.form");
  const tt = useTranslations("settings.alerts.schedules.templates");
  const cadenceText = useScheduleCadence();
  return (
    <div>
      <label
        className="mb-1 block text-[13px] text-[var(--text-muted)]"
        htmlFor="schedule-template"
      >
        {t("template")}
      </label>
      <Select
        id="schedule-template"
        value={templateId}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value as ScheduleTemplateId)}
      >
        {SCHEDULE_TEMPLATE_IDS.map((id) => (
          <option key={id} value={id}>
            {tt(`${id}.label`)}
          </option>
        ))}
      </Select>
      <p className="mt-1 text-[12px] text-[var(--text-muted)]">
        {cadenceText(SCHEDULE_TEMPLATES[templateId])}
      </p>
    </div>
  );
}

/** What an account-wide briefing covers - it has no stocks to type, so the form says what it reads. */
function TemplateDescription({
  templateId,
}: Readonly<{ templateId: ScheduleTemplateId }>): ReactNode {
  const tt = useTranslations("settings.alerts.schedules.templates");
  return (
    <p className="text-[13px] text-[var(--text-muted)] leading-relaxed sm:col-span-2">
      {tt(`${templateId}.description`)}
    </p>
  );
}

/** Weekday and time (Taipei) for the weekly review. Two cells on a wide form, stacked at 390px. */
function WhenFields({
  weekday,
  time,
  onWeekday,
  onTime,
  showTimeError,
}: Readonly<{
  weekday: Weekday;
  time: string;
  onWeekday: (day: Weekday) => void;
  onTime: (time: string) => void;
  showTimeError: boolean;
}>): ReactNode {
  const t = useTranslations("settings.alerts.schedules.form");
  const weekdays = useTranslations("settings.alerts.schedules.weekdays");
  return (
    <>
      <div>
        <label
          className="mb-1 block text-[13px] text-[var(--text-muted)]"
          htmlFor="schedule-weekday"
        >
          {t("weekday")}
        </label>
        <Select
          id="schedule-weekday"
          value={String(weekday)}
          onChange={(e) => {
            const next = WEEKDAY_ORDER.find((day) => String(day) === e.target.value);
            if (next !== undefined) onWeekday(next);
          }}
        >
          {WEEKDAY_ORDER.map((day) => (
            <option key={day} value={String(day)}>
              {weekdays(String(day))}
            </option>
          ))}
        </Select>
      </div>
      <div>
        <label className="mb-1 block text-[13px] text-[var(--text-muted)]" htmlFor="schedule-time">
          {t("time")}
        </label>
        <TextInput
          id="schedule-time"
          type="time"
          value={time}
          onChange={(e) => onTime(e.target.value)}
          aria-invalid={showTimeError}
          className="min-w-0"
        />
        {showTimeError ? (
          <p role="alert" className="mt-1 text-[12px] text-[var(--warn)]">
            {t("errorTimeRequired")}
          </p>
        ) : null}
      </div>
    </>
  );
}

function ScopeField({
  followList,
  onChange,
}: Readonly<{ followList: boolean; onChange: (follow: boolean) => void }>): ReactNode {
  const t = useTranslations("settings.alerts.schedules.form");
  return (
    <div className="sm:col-span-2">
      <label className="mb-1 block text-[13px] text-[var(--text-muted)]" htmlFor="schedule-scope">
        {t("scope")}
      </label>
      <Select
        id="schedule-scope"
        value={followList ? "watchlist" : "stocks"}
        onChange={(e) => onChange(e.target.value === "watchlist")}
      >
        <option value="stocks">{t("scopeStocks")}</option>
        <option value="watchlist">{t("scopeWatchlist")}</option>
      </Select>
    </div>
  );
}

function WatchlistField({
  watchlistId,
  onChange,
  groups,
  showError,
}: Readonly<{
  watchlistId: string;
  onChange: (id: string) => void;
  groups: readonly WatchlistGroupView[];
  showError: boolean;
}>): ReactNode {
  const t = useTranslations("settings.alerts.schedules.form");
  return (
    <div className="sm:col-span-2">
      <label
        className="mb-1 block text-[13px] text-[var(--text-muted)]"
        htmlFor="schedule-watchlist"
      >
        {t("watchlist")}
      </label>
      <Select
        id="schedule-watchlist"
        value={watchlistId}
        onChange={(e) => onChange(e.target.value)}
      >
        <option value="">{t("watchlistPlaceholder")}</option>
        {groups.map((view) => (
          <option key={view.group.id} value={view.group.id} style={groupNameStyle(view.group)}>
            {view.group.name}
          </option>
        ))}
      </Select>
      <p className="mt-1 text-[12px] text-[var(--text-muted)]">{t("watchlistNote")}</p>
      {showError ? (
        <p role="alert" className="mt-1 text-[12px] text-[var(--warn)]">
          {t("errorWatchlistRequired")}
        </p>
      ) : null}
    </div>
  );
}

function TargetsField({
  template,
  value,
  onChange,
  error,
}: Readonly<{
  template: ScheduleTemplate;
  value: string;
  onChange: (value: string) => void;
  error: Extract<TargetCheck, { ok: false }>["reason"] | null;
}>): ReactNode {
  const t = useTranslations("settings.alerts.schedules.form");
  return (
    <div>
      <label className="mb-1 block text-[13px] text-[var(--text-muted)]" htmlFor="schedule-targets">
        {template.multiple ? t("targetsMany", { max: MAX_TARGETS }) : t("targetsOne")}
      </label>
      <TextInput
        id="schedule-targets"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={template.targetMarket === "US" ? "AAPL" : "2330"}
        aria-invalid={error !== null}
      />
      {error ? (
        <p role="alert" className="mt-1 text-[12px] text-[var(--warn)]">
          {t(`error.${error}`, { max: MAX_TARGETS })}
        </p>
      ) : null}
    </div>
  );
}
