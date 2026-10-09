import { beforeEach, describe, expect, it } from "vitest";
import { useTurnStore } from "@/app/[locale]/(app)/chat/turn-store";
import type { AgentStreamFrame } from "@/lib/api/agent-stream";

function frame(kind: string, seq: number, data: Record<string, unknown>): AgentStreamFrame {
  return { kind, envelope: { seq, ts: "2026-10-09T00:00:00Z", data } } as AgentStreamFrame;
}

describe("lane labels", () => {
  beforeEach(() => {
    useTurnStore.getState().clear();
    useTurnStore.getState().begin("q", null);
  });

  it("keeps a lane's label after it finishes (a done frame carries none)", () => {
    const { applyFrame } = useTurnStore.getState();
    applyFrame(
      frame("agent.spawned", 1, {
        agent_id: "deep_research:1",
        parent_agent_id: "root",
        role: "subagent",
        skill: "deep_research",
        activity: "搜尋：台積電 最新新聞",
      }),
    );
    applyFrame(frame("agent.done", 2, { agent_id: "deep_research:1", status: "ok" }));

    const lane = useTurnStore.getState().activeTurn?.lanes[0];
    expect(lane?.status).toBe("done");
    expect(lane?.activity).toBe("搜尋：台積電 最新新聞");
    expect(lane?.skill).toBe("deep_research");
  });
});
