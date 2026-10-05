"use client";

import { CalendarCheckIcon, CheckCircleIcon } from "@phosphor-icons/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { type ReactNode, useState } from "react";
import { Button } from "@/components/ui/button";
import { Link } from "@/i18n/navigation";
import { createSchedule, listSchedules, SettingsApiError } from "@/lib/api/settings";
import {
  findExistingJob,
  parseScheduleProposal,
  toScheduleRequest,
} from "@/lib/schedules/proposal";

const SCHEDULES_KEY = ["settings", "schedules"] as const;

/**
 * The `schedule_proposal` card: the agent's `propose_schedule` tool (backend/src/tools/
 * propose_schedule_tools.py) only *suggests* a recurring briefing — this card is the confirm gate.
 * Nothing exists until the user taps 建立, which calls the ordinary `POST /schedules` (auth,
 * fair-use caps and trigger/skill validation enforced there). The agent has no write path.
 */
export function ScheduleProposalCard({ data }: { data: Record<string, unknown> }): ReactNode {
  const t = useTranslations("chat.cards.scheduleProposal");
  const tt = useTranslations("settings.alerts.schedules.templates");
  const queryClient = useQueryClient();
  const [dismissed, setDismissed] = useState(false);

  const proposal = parseScheduleProposal(data);

  const jobsQuery = useQuery({
    queryKey: SCHEDULES_KEY,
    queryFn: listSchedules,
    enabled: proposal !== null,
    staleTime: 30_000,
    retry: false,
  });

  const create = useMutation({
    mutationFn: createSchedule,
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: SCHEDULES_KEY }),
  });

  if (!proposal) {
    return null;
  }

  const existing = findExistingJob(jobsQuery.data ?? [], proposal);
  const created = create.isSuccess || existing !== null;

  if (dismissed && !created) {
    return (
      <p className="mt-3 text-[13px] text-[var(--text-muted)]" aria-live="polite">
        {t("dismissed")}
      </p>
    );
  }

  return (
    <section
      aria-label={t("title")}
      className="mt-3 rounded-[var(--radius-card)] border border-[var(--border)] bg-[var(--surface)] p-4"
    >
      <div className="flex items-start gap-2">
        <CalendarCheckIcon
          size={18}
          weight="regular"
          className="mt-0.5 shrink-0 text-[var(--accent)]"
          aria-hidden
        />
        <div className="min-w-0">
          <p className="text-[12px] text-[var(--text-muted)]">{t("title")}</p>
          <p className="font-medium text-[14px] text-[var(--text)]">
            {tt(`${proposal.template}.label`)}
          </p>
          <p className="mt-0.5 text-[14px] text-[var(--text)]">{proposal.targets.join("、")}</p>
          <p className="mt-0.5 text-[12px] text-[var(--text-muted)]">
            {tt(`${proposal.template}.cadence`)}
          </p>
        </div>
      </div>

      {created ? (
        <p
          className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-1 text-[13px] text-[var(--accent-text)]"
          aria-live="polite"
        >
          <span className="inline-flex items-center gap-1.5">
            <CheckCircleIcon size={16} weight="regular" aria-hidden />
            {existing !== null && !create.isSuccess ? t("alreadySet") : t("created")}
          </span>
          <Link href="/settings/alerts" className="underline-offset-2 hover:underline">
            {t("manage")}
          </Link>
        </p>
      ) : (
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <Button
            variant="filled"
            size="sm"
            disabled={create.isPending}
            onClick={() => create.mutate(toScheduleRequest(proposal))}
          >
            {create.isPending ? t("creating") : t("create")}
          </Button>
          <Button variant="ghost" size="sm" onClick={() => setDismissed(true)}>
            {t("dismiss")}
          </Button>
        </div>
      )}

      {create.isError ? (
        <p role="alert" className="mt-2 text-[12px] text-[var(--warn)]">
          {create.error instanceof SettingsApiError && create.error.code === "cap_reached"
            ? t("errorCap")
            : t("errorGeneric")}
        </p>
      ) : null}

      <p className="mt-3 text-[11px] text-[var(--text-muted)] leading-snug">{t("caveat")}</p>
    </section>
  );
}
