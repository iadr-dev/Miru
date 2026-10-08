import { z } from "zod";
import { memoryText } from "@/lib/memory";
import { type Memory, WRITABLE_MEMORY_TYPES } from "@/types/settings";

/** The `memory_proposal` rich card the agent's `propose_memory` tool emits
 * (backend/src/tools/propose_memory_tools.py). The agent only *proposes*: tapping 儲存 makes the
 * frontend call the ordinary `POST /memory`. Parsed defensively since it arrives as an open record. */
export const memoryProposalSchema = z.object({
  memory_type: z.enum(WRITABLE_MEMORY_TYPES),
  text: z.string().min(1).max(300),
});
export type MemoryProposal = z.infer<typeof memoryProposalSchema>;

/** A malformed proposal yields null so the card renders nothing rather than a button that fails. */
export function parseMemoryProposal(data: Record<string, unknown>): MemoryProposal | null {
  const parsed = memoryProposalSchema.safeParse(data);
  return parsed.success ? parsed.data : null;
}

/** A memory the user already has with exactly this text - so a card re-rendered from thread history,
 * or tapped twice, shows "already remembered" instead of saving a duplicate. */
export function findExistingMemory(memories: Memory[], proposal: MemoryProposal): Memory | null {
  const wanted = proposal.text.trim();
  return memories.find((m) => memoryText(m).trim() === wanted) ?? null;
}
