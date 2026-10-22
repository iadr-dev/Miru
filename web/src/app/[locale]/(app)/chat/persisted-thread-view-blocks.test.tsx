// @vitest-environment jsdom
import { act, cleanup, fireEvent, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { PersistedThreadView } from "@/app/[locale]/(app)/chat/persisted-thread-view";
import { submitTurnFeedback } from "@/lib/api/threads";
import { messages, renderWithIntl } from "@/test/render";
import type { Message, Thread, Turn } from "@/types/conversation";

vi.mock("@/lib/api/threads", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api/threads")>();
  return { ...actual, submitTurnFeedback: vi.fn() };
});

const T = messages.chat.turn;
const A = T.actions;
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
  usage?: Turn["usage"],
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
    ...(usage ? { usage } : {}),
  };
}

function thread(turns: Turn[]): Thread {
  return {
    id: "th1",
    user_id: "u1",
    title: "x",
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

const user = (text = "問題一"): Turn => turn("u", "user", [block("u", 0, "text", { text })]);
const answer = (extra: Message[] = [], parent: string | null = "u"): Turn =>
  turn("a", "assistant", [block("a", 9, "text", { text: "答案一" }), ...extra], parent);

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(submitTurnFeedback).mockResolvedValue(undefined as never);
});
afterEach(cleanup);

describe("PersistedThreadView user turns", () => {
  it("offers edit and resend only when an edit handler is given and nothing is streaming", () => {
    renderWithIntl(<PersistedThreadView thread={thread([user()])} />);
    expect(screen.queryByRole("button", { name: A.edit })).toBeNull();
    expect(screen.queryByRole("button", { name: A.resend })).toBeNull();
    cleanup();
    renderWithIntl(<PersistedThreadView thread={thread([user()])} onEdit={vi.fn()} streaming />);
    expect(screen.queryByRole("button", { name: A.edit })).toBeNull();
    cleanup();
    renderWithIntl(<PersistedThreadView thread={thread([user()])} onEdit={vi.fn()} />);
    expect(screen.getByRole("button", { name: A.edit })).toBeTruthy();
    expect(screen.getByRole("button", { name: A.resend })).toBeTruthy();
  });

  it("resend reports the same text for that turn; edit reports the new text", () => {
    const onEdit = vi.fn();
    renderWithIntl(<PersistedThreadView thread={thread([user()])} onEdit={onEdit} />);
    fireEvent.click(screen.getByRole("button", { name: A.resend }));
    expect(onEdit).toHaveBeenLastCalledWith("u", "問題一");
    fireEvent.click(screen.getByRole("button", { name: A.edit }));
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "新的問題" } });
    fireEvent.click(screen.getByRole("button", { name: A.editSave }));
    expect(onEdit).toHaveBeenLastCalledWith("u", "新的問題");
  });

  it("a user turn's text block without a string text renders an empty bubble", () => {
    const { container } = renderWithIntl(
      <PersistedThreadView
        thread={thread([turn("u", "user", [block("u", 0, "text", { text: 5 })])])}
      />,
    );
    expect(container.innerHTML).toMatchSnapshot();
  });
});

describe("PersistedThreadView block types", () => {
  it("shows image and file attachment chips, and skips a malformed one", () => {
    const { container } = renderWithIntl(
      <PersistedThreadView
        thread={thread([
          turn("u", "user", [
            block("u", 0, "attachment", {
              filename: "k.png",
              media_type: "image/png",
              is_image: true,
              size_bytes: 10,
            }),
            block("u", 1, "attachment", {
              filename: "r.pdf",
              media_type: "application/pdf",
              is_image: false,
              size_bytes: 10,
            }),
            block("u", 2, "attachment", { filename: 3 }),
            block("u", 3, "text", { text: "看這個" }),
          ]),
        ])}
      />,
    );
    expect(screen.getByText("k.png")).toBeTruthy();
    expect(screen.getByText("r.pdf")).toBeTruthy();
    expect(container.innerHTML).toMatchSnapshot();
  });

  it("renders a valid rich card inline and skips a malformed one", () => {
    const { container } = renderWithIntl(
      <PersistedThreadView
        thread={thread([
          user(),
          answer([
            block("a", 1, "rich_card", { card_type: "mystery_card", data: { 欄位: "數值" } }),
            block("a", 2, "rich_card", { data: {} }),
          ]),
        ])}
      />,
    );
    expect(container.innerHTML).toMatchSnapshot();
  });

  it("an ask_user card hands the picked option up, but not while streaming", () => {
    const card = block("a", 1, "rich_card", {
      card_type: "ask_user",
      data: { question: "要看哪一個？", options: ["籌碼", "財報"] },
    });
    const onAskUserPick = vi.fn();
    renderWithIntl(
      <PersistedThreadView
        thread={thread([user(), answer([card])])}
        onAskUserPick={onAskUserPick}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "籌碼" }));
    expect(onAskUserPick).toHaveBeenCalledWith("籌碼");
    cleanup();
    const again = vi.fn();
    renderWithIntl(
      <PersistedThreadView
        thread={thread([user(), answer([card])])}
        onAskUserPick={again}
        streaming
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "財報" }));
    expect(again).not.toHaveBeenCalled();
  });

  it("shows the web search sources pill, and nothing for empty or malformed sources", () => {
    renderWithIntl(
      <PersistedThreadView
        thread={thread([
          user(),
          answer([
            block("a", 1, "web_search_sources", {
              sources: [{ url: "https://example.com/a", title: "甲" }],
            }),
          ]),
        ])}
      />,
    );
    expect(screen.getByRole("button", { name: "1 個來源" })).toBeTruthy();
    cleanup();
    renderWithIntl(
      <PersistedThreadView
        thread={thread([
          user(),
          answer([
            block("a", 1, "web_search_sources", { sources: [] }),
            block("a", 2, "web_search_sources", { sources: "bad" }),
          ]),
        ])}
      />,
    );
    expect(screen.queryByRole("button", { name: /個來源/ })).toBeNull();
  });

  it("ignores block types without a persisted renderer", () => {
    const { container } = renderWithIntl(
      <PersistedThreadView
        thread={thread([
          user(),
          answer([
            block("a", 1, "caveat", { text: "x" }),
            block("a", 2, "steering", { text: "y" }),
            block("a", 3, "branch_summary", {}),
            block("a", 4, "compaction", {}),
          ]),
        ])}
      />,
    );
    expect(container.innerHTML).toMatchSnapshot();
  });
});

describe("PersistedThreadView process disclosure", () => {
  const lanes = [
    block("a", 0, "tool_use", {
      agent_id: "a1",
      activity: "查詢法人",
      call: { id: "c1", name: "flow", arguments: {} },
    }),
    block("a", 1, "tool_result", {
      agent_id: "a1",
      result: { tool_call_id: "c1", content: "外資買超" },
    }),
    block("a", 2, "thinking", { agent_id: "a2", text: "先看基本面" }),
    block("a", 3, "thinking", { agent_id: "a2", text: "" }),
    block("a", 4, "tool_result", {
      agent_id: "a2",
      result: { tool_call_id: "missing", content: "沒有對應呼叫" },
    }),
    block("a", 5, "tool_use", { nonsense: true }),
    block("a", 6, "tool_result", { nonsense: true }),
    block("a", 7, "thinking", { nonsense: true }),
  ];

  it("groups blocks by lane, drops empty thinking and malformed blocks", () => {
    const { container } = renderWithIntl(
      <PersistedThreadView thread={thread([user(), answer(lanes)])} />,
    );
    fireEvent.click(screen.getByRole("button", { name: T.showProcess }));
    expect(container.querySelectorAll("ol[aria-live='off'] > li")).toHaveLength(2);
    expect(container.innerHTML).toMatchSnapshot();
    fireEvent.click(screen.getByRole("button", { name: T.hideProcess }));
    expect(container.querySelector("ol")).toBeNull();
  });

  it("has no disclosure when a turn has no process blocks, nor on user turns", () => {
    renderWithIntl(<PersistedThreadView thread={thread([user(), answer()])} />);
    expect(screen.queryByRole("button", { name: T.showProcess })).toBeNull();
  });

  it("has no disclosure when every process block is malformed or empty", () => {
    renderWithIntl(
      <PersistedThreadView
        thread={thread([user(), answer([block("a", 1, "thinking", { text: "" })])])}
      />,
    );
    expect(screen.queryByRole("button", { name: T.showProcess })).toBeNull();
  });
});

describe("PersistedThreadView assistant actions", () => {
  it("renders usage chips on an assistant turn", () => {
    const { container } = renderWithIntl(
      <PersistedThreadView
        thread={thread([
          user(),
          turn("a", "assistant", [block("a", 0, "text", { text: "答案一" })], "u", {
            input_tokens: 1200,
            output_tokens: 80,
            cost_micros: 0,
            estimated_cost_micros: 0,
            models: [],
            memories_used: [],
            personality: null,
          }),
        ])}
      />,
    );
    expect(container.innerHTML).toMatchSnapshot();
  });

  it("retry regenerates with the question from the parent user turn", async () => {
    let finish: () => void = () => undefined;
    const onRegenerate = vi.fn(
      () =>
        new Promise<unknown>((resolve) => {
          finish = () => resolve(undefined);
        }),
    );
    renderWithIntl(
      <PersistedThreadView thread={thread([user(), answer()])} onRegenerate={onRegenerate} />,
    );
    const retry = screen.getByRole("button", { name: A.retry }) as HTMLButtonElement;
    fireEvent.click(retry);
    expect(onRegenerate).toHaveBeenCalledWith("a", "問題一");
    expect(retry.disabled).toBe(true);
    await act(async () => {
      finish();
      await Promise.resolve();
    });
    expect(retry.disabled).toBe(false);
  });

  it("retry falls back to the answer text when the parent turn is missing", () => {
    const onRegenerate = vi.fn(() => Promise.resolve(undefined));
    renderWithIntl(
      <PersistedThreadView thread={thread([answer([], null)])} onRegenerate={onRegenerate} />,
    );
    fireEvent.click(screen.getByRole("button", { name: A.retry }));
    expect(onRegenerate).toHaveBeenCalledWith("a", "答案一");
  });

  it("retry falls back to the answer text when the parent has no text", () => {
    const onRegenerate = vi.fn(() => Promise.resolve(undefined));
    renderWithIntl(
      <PersistedThreadView
        thread={thread([turn("u", "user", []), answer()])}
        onRegenerate={onRegenerate}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: A.retry }));
    expect(onRegenerate).toHaveBeenCalledWith("a", "答案一");
  });

  it("hides retry without a handler and while another turn streams", () => {
    renderWithIntl(<PersistedThreadView thread={thread([user(), answer()])} />);
    expect(screen.queryByRole("button", { name: A.retry })).toBeNull();
    cleanup();
    renderWithIntl(
      <PersistedThreadView thread={thread([user(), answer()])} onRegenerate={vi.fn()} streaming />,
    );
    expect(screen.queryByRole("button", { name: A.retry })).toBeNull();
  });

  it("feedback votes are submitted and shown as pressed", () => {
    renderWithIntl(<PersistedThreadView thread={thread([user(), answer()])} />);
    const up = screen.getByRole("button", { name: A.thumbsUp });
    const down = screen.getByRole("button", { name: A.thumbsDown });
    expect(up.getAttribute("aria-pressed")).toBe("false");
    fireEvent.click(up);
    expect(submitTurnFeedback).toHaveBeenLastCalledWith("th1", "a", "up");
    expect(up.getAttribute("aria-pressed")).toBe("true");
    fireEvent.click(down);
    expect(submitTurnFeedback).toHaveBeenLastCalledWith("th1", "a", "down");
    expect(up.getAttribute("aria-pressed")).toBe("false");
    expect(down.getAttribute("aria-pressed")).toBe("true");
  });

  it("a failed feedback submit reverts the pressed state", async () => {
    vi.mocked(submitTurnFeedback).mockRejectedValue(new Error("x"));
    renderWithIntl(<PersistedThreadView thread={thread([user(), answer()])} />);
    const up = screen.getByRole("button", { name: A.thumbsUp });
    await act(async () => {
      fireEvent.click(up);
      await Promise.resolve();
    });
    expect(up.getAttribute("aria-pressed")).toBe("false");
  });

  it("an assistant turn with no text block copies an empty answer", () => {
    const { container } = renderWithIntl(
      <PersistedThreadView thread={thread([user(), turn("a", "assistant", [], "u")])} />,
    );
    expect(container.innerHTML).toMatchSnapshot();
  });
});
