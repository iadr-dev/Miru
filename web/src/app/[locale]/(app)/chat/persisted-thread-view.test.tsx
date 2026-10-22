// @vitest-environment jsdom
import { cleanup, fireEvent, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { PersistedThreadView } from "@/app/[locale]/(app)/chat/persisted-thread-view";
import { messages, renderWithIntl } from "@/test/render";
import type { Message, Thread, Turn } from "@/types/conversation";

afterEach(cleanup);

const AT = "2026-10-12T01:00:00Z";

function block(
  turnId: string,
  seq: number,
  type: Message["type"],
  content: Record<string, unknown>,
): Message {
  return {
    id: `${turnId}-${seq}`,
    turn_id: turnId,
    user_id: "u1",
    parent_id: null,
    seq,
    type,
    content,
    created_at: AT,
  };
}

function turn(
  id: string,
  role: Turn["role"],
  blocks: Message[],
  parent: string | null = null,
): Turn {
  return {
    id,
    thread_id: "th1",
    user_id: "u1",
    role,
    status: "done",
    parent_turn_id: parent,
    created_at: AT,
    messages: blocks,
  };
}

function thread(turns: Turn[]): Thread {
  return {
    id: "th1",
    user_id: "u1",
    title: "台積電",
    starred: false,
    archived: false,
    tags: [],
    last_message_at: AT,
    version: 0,
    created_at: AT,
    updated_at: AT,
    turns,
  };
}

const QUESTION = "台積電 2330 最近的籌碼面如何？";
const ANSWER = "外資近五日淨買超，投信持平。";

const conversation = thread([
  turn("t1", "user", [block("t1", 0, "text", { text: QUESTION })]),
  turn(
    "t2",
    "assistant",
    [
      block("t2", 0, "tool_use", {
        agent_id: "a1",
        activity: "查詢法人買賣超",
        call: { id: "c1", name: "institutional_flow", arguments: {} },
      }),
      block("t2", 1, "tool_result", {
        agent_id: "a1",
        result: { tool_call_id: "c1", content: "外資 +12,345 張" },
      }),
      block("t2", 2, "text", { text: ANSWER }),
    ],
    "t1",
  ),
]);

describe("PersistedThreadView", () => {
  it("renders the question as the user's bubble and the answer as the assistant's", () => {
    renderWithIntl(<PersistedThreadView thread={conversation} />);
    expect(screen.getByText(QUESTION)).toBeTruthy();
    expect(screen.getByText(ANSWER)).toBeTruthy();
  });

  it("shows no caveat chip under an assistant answer", () => {
    renderWithIntl(<PersistedThreadView thread={conversation} />);
    expect(screen.queryByText(messages.disclaimer.text)).toBeNull();
  });

  it("keeps the tool process collapsed until asked, then shows it", () => {
    renderWithIntl(<PersistedThreadView thread={conversation} />);
    expect(screen.queryByText("外資 +12,345 張")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: messages.chat.turn.showProcess }));
    expect(screen.getByRole("button", { name: messages.chat.turn.hideProcess })).toBeTruthy();
  });

  it("renders nothing for an empty thread", () => {
    const { container } = renderWithIntl(<PersistedThreadView thread={thread([])} />);
    expect(container.textContent).toBe("");
  });
});
