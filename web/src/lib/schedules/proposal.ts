import { z } from "zod";
import type { CreateScheduleRequest } from "@/lib/api/settings";
import { type ScheduleJob, scheduleActionSchema, scheduleTriggerSchema } from "@/types/settings";
import { SCHEDULE_TEMPLATE_IDS } from "./templates";

/** The `schedule_proposal` rich card the agent's `propose_schedule` tool emits
 * (backend/src/tools/propose_schedule_tools.py). Like the alert card, the agent only *proposes*:
 * tapping 建立 makes the frontend call the ordinary `POST /schedules` with the resolved trigger and
 * action the card carries, so the shape is the create-request shape, parsed defensively because it
 * arrives as an open record. */
export const scheduleProposalSchema = z.object({
  template: z.enum(SCHEDULE_TEMPLATE_IDS),
  targets: z.array(z.string().min(1)).min(1),
  trigger: scheduleTriggerSchema,
  action: scheduleActionSchema,
});
export type ScheduleProposal = z.infer<typeof scheduleProposalSchema>;

/** A malformed proposal yields null so the card renders nothing rather than offering a button that
 * would fail. Unknown extra keys (summary, caveat stamp, next_step) are ignored. */
export function parseScheduleProposal(data: Record<string, unknown>): ScheduleProposal | null {
  const parsed = scheduleProposalSchema.safeParse(data);
  return parsed.success ? parsed.data : null;
}

export function toScheduleRequest(proposal: ScheduleProposal): CreateScheduleRequest {
  return {
    trigger: proposal.trigger,
    action: proposal.action,
    delivery_channels: ["in_app"],
  };
}

/** Key order must not matter when comparing stored inputs with a proposal's. */
function canonical(value: unknown): string {
  if (Array.isArray(value)) {
    return `[${value.map(canonical).join(",")}]`;
  }
  if (value !== null && typeof value === "object") {
    const entries = Object.entries(value as Record<string, unknown>).sort(([a], [b]) =>
      a.localeCompare(b),
    );
    const members = entries.map(([key, item]) => `${key}:${canonical(item)}`);
    return `{${members.join(",")}}`;
  }
  return JSON.stringify(value);
}

/** The enabled schedule that is already exactly this proposal, if any — covers a card re-rendered
 * from thread history and a double tap, so there is nothing left to confirm. */
export function findExistingJob(
  jobs: readonly ScheduleJob[],
  proposal: ScheduleProposal,
): ScheduleJob | null {
  return (
    jobs.find(
      (job) =>
        job.enabled &&
        job.action.skill_ref === proposal.action.skill_ref &&
        canonical(job.action.inputs) === canonical(proposal.action.inputs) &&
        job.trigger.type === proposal.trigger.type &&
        job.trigger.spec === proposal.trigger.spec &&
        job.trigger.market === proposal.trigger.market,
    ) ?? null
  );
}
