import { z } from "zod";
import type { CreateAlertRequest } from "@/lib/api/settings";
import {
  ALERT_TRIGGER_MODES,
  type AlertRule,
  alertConditionSchema,
  MARKETS,
} from "@/types/settings";
import { sameCondition } from "./condition";

/** The `alert_proposal` rich card the agent's `propose_alert` tool emits
 * (backend/src/tools/propose_alert_tools.py). The agent only *proposes*: the card is the confirm
 * gate, and tapping 建立 makes the frontend call the ordinary `POST /alerts` — so the shape here is
 * deliberately the create-request shape, parsed defensively since it arrives as an open record. */
export const alertProposalSchema = z.object({
  symbol: z.string().min(1),
  market: z.enum(MARKETS),
  condition: alertConditionSchema,
  trigger_mode: z.enum(ALERT_TRIGGER_MODES).default("once_per_day"),
  expires_in_days: z.number().int().positive().nullable().default(90),
});
export type AlertProposal = z.infer<typeof alertProposalSchema>;

/** Parse a card's open `data` record; a malformed or unsupported proposal yields null so the card
 * renders nothing rather than offering a button that would fail. Unknown extra keys (the summary
 * line, the governance caveat stamp) are ignored. */
export function parseProposal(data: Record<string, unknown>): AlertProposal | null {
  const parsed = alertProposalSchema.safeParse(data);
  return parsed.success ? parsed.data : null;
}

export function toCreateRequest(proposal: AlertProposal): CreateAlertRequest {
  return {
    symbol: proposal.symbol,
    market: proposal.market,
    condition: proposal.condition,
    trigger_mode: proposal.trigger_mode,
    expires_in_days: proposal.expires_in_days,
  };
}

/** An enabled rule the user already has that is exactly this proposal — used so a card reloaded
 * from thread history (or tapped twice) shows "already set" instead of creating a duplicate. */
export function findExistingRule(rules: AlertRule[], proposal: AlertProposal): AlertRule | null {
  return (
    rules.find(
      (rule) =>
        rule.enabled &&
        rule.symbol === proposal.symbol &&
        rule.market === proposal.market &&
        sameCondition(rule.condition, proposal.condition),
    ) ?? null
  );
}
