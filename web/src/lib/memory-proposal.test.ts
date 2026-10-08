import { describe, expect, it } from "vitest";
import { findExistingMemory, parseMemoryProposal } from "@/lib/memory-proposal";
import type { Memory } from "@/types/settings";

// What the backend's `propose_memory` tool returns (tests/tools/test_propose_memory_tools.py), plus
// the keys the agent loop stamps on every card.
const backendCard = {
  memory_type: "preference",
  text: "我主要關注高股息 ETF",
  requires_user_confirmation: true,
  next_step: "DONE",
  non_trading_caveat: "…",
};

const memory = (text: string): Memory => ({
  id: "m1",
  user_id: "u",
  scope: "user",
  thread_id: null,
  type: "preference",
  key: "k",
  content: { text },
  source_ref: null,
  created_at: "2026-10-08T00:00:00",
  updated_at: "2026-10-08T00:00:00",
  last_used_at: null,
  use_count: 0,
});

describe("parseMemoryProposal", () => {
  it("accepts the backend payload and ignores the extra keys", () => {
    expect(parseMemoryProposal(backendCard)).toEqual({
      memory_type: "preference",
      text: "我主要關注高股息 ETF",
    });
  });

  it("rejects a malformed or unsupported proposal", () => {
    expect(parseMemoryProposal({ memory_type: "watchlist", text: "x" })).toBeNull();
    expect(parseMemoryProposal({ memory_type: "finding", text: "" })).toBeNull();
    expect(parseMemoryProposal({})).toBeNull();
  });
});

describe("findExistingMemory", () => {
  const proposal = { memory_type: "preference", text: "我主要關注高股息 ETF" } as const;

  it("finds a memory with the same text", () => {
    expect(findExistingMemory([memory(" 我主要關注高股息 ETF ")], proposal)?.id).toBe("m1");
  });

  it("returns null when nothing matches", () => {
    expect(findExistingMemory([memory("別的")], proposal)).toBeNull();
  });
});
