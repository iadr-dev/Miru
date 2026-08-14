/**
 * Conversation domain contracts: `Thread`, `Turn`, `Message`, and the message block-content
 * payloads — the persisted shapes a re-opened thread renders (distinct from the live SSE event
 * vocabulary in `./sse.ts`).
 *
 * Mirrors `backend/src/conversation/models.py` (`Thread`/`Turn`/`Message`, `MessageBlockType`,
 * `TextBlock`/`ThinkingBlock`/`ToolUseBlock`/`ToolResultBlock`/`CaveatBlock`/`RichCardBlock`/
 * `SteeringBlock`/`BranchSummaryBlock`/`CompactionBlock`/`WebSearchSourcesBlock`) and the
 * `RichCard` shape in `backend/src/agent/loop.py`. The list/get/CRUD envelopes mirror
 * `backend/src/api/routers/threads.py` + `threads_actions.py`. Parse-don't-trust at the seam.
 */
import { z } from "zod";
import { webSearchSourceSchema } from "@/types/sse";

export const TURN_ROLES = ["user", "assistant"] as const;
export const TURN_STATUSES = ["running", "done", "cancelled", "error"] as const;
export const FRAMEWORKS = ["none", "single", "subagent", "team"] as const;

export const MESSAGE_BLOCK_TYPES = [
  "text",
  "thinking",
  "tool_use",
  "tool_result",
  "caveat",
  "rich_card",
  "steering",
  "branch_summary",
  "compaction",
  "web_search_sources",
] as const;
export type MessageBlockType = (typeof MESSAGE_BLOCK_TYPES)[number];

/** A governed UI card (`agent/loop.py::RichCard`, persisted as `RichCardBlock`). `data` is an open,
 * already-governance-scanned object — its inner shape is card-type-specific and NOT invented here;
 * a card renderer narrows it defensively at render time. */
export const richCardSchema = z.object({
  card_type: z.string(),
  data: z.record(z.string(), z.unknown()).default({}),
});
export type RichCard = z.infer<typeof richCardSchema>;

/** A persisted `web_search_sources` block (`conversation.models.WebSearchSourcesBlock`) — the
 * durable counterpart of the live `web_search.result` SSE frame's `sources` list (same
 * `webSearchSourceSchema` shape, reused rather than re-declared, per this module's own
 * "interoperate, don't duplicate" convention). Without this block, `web-search-sources.tsx`'s
 * "N sources" pill has nothing to render once a turn leaves the live overlay for the persisted
 * thread view. */
export const webSearchSourcesBlockSchema = z.object({
  sources: z.array(webSearchSourceSchema).default([]),
});
export type WebSearchSourcesBlockContent = z.infer<typeof webSearchSourcesBlockSchema>;

/** Only the block fields the chat thread renders. `content` is kept as an open record keyed off the
 * block `type`; renderers read the fields relevant to each type (parse-don't-trust per field). */
export const messageSchema = z.object({
  id: z.string(),
  turn_id: z.string(),
  user_id: z.string(),
  parent_id: z.string().nullable(),
  seq: z.number().int().nonnegative(),
  type: z.enum(MESSAGE_BLOCK_TYPES),
  content: z.record(z.string(), z.unknown()).default({}),
  created_at: z.string(),
});
export type Message = z.infer<typeof messageSchema>;

export const turnSchema = z.object({
  id: z.string(),
  thread_id: z.string(),
  user_id: z.string(),
  role: z.enum(TURN_ROLES),
  status: z.enum(TURN_STATUSES),
  parent_turn_id: z.string().nullable().optional(),
  regenerate_of: z.string().nullable().optional(),
  framework: z.enum(FRAMEWORKS).nullable().optional(),
  idempotency_key: z.string().nullable().optional(),
  created_at: z.string(),
  messages: z.array(messageSchema).default([]),
});
export type Turn = z.infer<typeof turnSchema>;

export const threadSchema = z.object({
  id: z.string(),
  user_id: z.string(),
  title: z.string(),
  starred: z.boolean().default(false),
  archived: z.boolean().default(false),
  tags: z.array(z.string()).default([]),
  model_override: z.string().nullable().optional(),
  personality_override: z.string().nullable().optional(),
  active_leaf_message_id: z.string().nullable().optional(),
  draft_content: z.string().nullable().optional(),
  last_message_at: z.string(),
  version: z.number().int().default(0),
  created_at: z.string(),
  updated_at: z.string(),
  turns: z.array(turnSchema).default([]),
});
export type Thread = z.infer<typeof threadSchema>;

/** `GET /threads` → `{threads: Thread[]}` (`threads.py::list_threads`). */
export const threadListResponseSchema = z.object({
  threads: z.array(threadSchema).default([]),
});
export type ThreadListResponse = z.infer<typeof threadListResponseSchema>;
