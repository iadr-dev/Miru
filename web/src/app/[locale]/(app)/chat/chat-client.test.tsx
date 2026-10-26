// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, render, screen } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import type { ReactElement, ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ChatClient } from "@/app/[locale]/(app)/chat/chat-client";
import { useTurnStore } from "@/app/[locale]/(app)/chat/turn-store";
import { useNewThreadStore } from "@/components/shell/new-thread-store";
import { fetchMe } from "@/lib/api/account";
import {
  AgentStreamError,
  type AgentStreamFrame,
  streamAgentQuery,
  streamEditTurn,
  streamRegenerateTurn,
} from "@/lib/api/agent-stream";
import { queryKeys } from "@/lib/api/query-keys";
import { cancelTurn, getThread, patchThread } from "@/lib/api/threads";
import { messages } from "@/test/render";

type AnyFn = (...args: never[]) => unknown;
type Captured = {
  composer: Record<string, unknown> | null;
  persisted: Record<string, unknown> | null;
  active: Record<string, unknown> | null;
  empty: Record<string, unknown> | null;
  pet: Record<string, unknown> | null;
  voice: Record<string, unknown> | null;
};

const hoisted = vi.hoisted(() => ({
  search: { value: "" },
  replace: vi.fn(),
  captured: {
    composer: null,
    persisted: null,
    active: null,
    empty: null,
    pet: null,
    voice: null,
  } as Record<string, Record<string, unknown> | null>,
  resumeOpts: [] as unknown[],
}));
const captured = hoisted.captured as Captured;

vi.mock("next/navigation", () => ({
  useSearchParams: (): URLSearchParams => new URLSearchParams(hoisted.search.value),
}));
vi.mock("@/i18n/navigation", () => ({
  useRouter: (): { replace: typeof hoisted.replace } => ({ replace: hoisted.replace }),
}));
vi.mock("@/lib/api/account", () => ({ fetchMe: vi.fn() }));
vi.mock("@/lib/api/agent-stream", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api/agent-stream")>();
  return {
    ...actual,
    streamAgentQuery: vi.fn(),
    streamEditTurn: vi.fn(),
    streamRegenerateTurn: vi.fn(),
    streamThreadResume: vi.fn(),
  };
});
vi.mock("@/lib/api/stream-resume", () => ({
  streamWithResume: (
    open: (signal: AbortSignal) => AsyncGenerator<AgentStreamFrame>,
    _resume: unknown,
    signal: AbortSignal,
    opts: unknown,
  ): AsyncGenerator<AgentStreamFrame> => {
    hoisted.resumeOpts.push(opts);
    return open(signal);
  },
}));
vi.mock("@/lib/api/threads", () => ({
  cancelTurn: vi.fn(),
  getThread: vi.fn(),
  patchThread: vi.fn(),
}));

vi.mock("@/app/[locale]/(app)/chat/composer", () => ({
  Composer: (p: Record<string, unknown>): ReactNode => {
    hoisted.captured.composer = p;
    return <div data-testid="composer" data-streaming={String(p.streaming)} />;
  },
}));
vi.mock("@/app/[locale]/(app)/chat/empty-state", () => ({
  EmptyState: (p: Record<string, unknown>): ReactNode => {
    hoisted.captured.empty = p;
    return <div data-testid="empty" />;
  },
}));
vi.mock("@/app/[locale]/(app)/chat/persisted-thread-view", () => ({
  PersistedThreadView: (p: Record<string, unknown>): ReactNode => {
    hoisted.captured.persisted = p;
    return <div data-testid="persisted" data-streaming={String(p.streaming)} />;
  },
}));
vi.mock("@/app/[locale]/(app)/chat/active-turn-view", () => ({
  ActiveTurnView: (p: Record<string, unknown>): ReactNode => {
    hoisted.captured.active = p;
    return <div data-testid="active">{(p.turn as { userText: string }).userText}</div>;
  },
}));
vi.mock("@/app/[locale]/(app)/chat/pet-companion", () => ({
  PetCompanion: (p: Record<string, unknown>): ReactNode => {
    hoisted.captured.pet = p;
    return <div data-testid="pet">{JSON.stringify([p.thinking, p.notice])}</div>;
  },
}));
vi.mock("@/app/[locale]/(app)/chat/voice-mode-overlay", () => ({
  VoiceModeOverlay: (p: Record<string, unknown>): ReactNode => {
    hoisted.captured.voice = p;
    return <div data-testid="voice" />;
  },
}));

const t = messages.chat.turn;
let seqCounter = 0;

function frame(kind: string, data: Record<string, unknown>, turnId = "turn1"): AgentStreamFrame {
  seqCounter += 1;
  return {
    kind,
    envelope: { seq: seqCounter, ts: "2026-10-09T00:00:00Z", turn_id: turnId, data },
  } as AgentStreamFrame;
}

async function* frames(
  list: AgentStreamFrame[],
  thenThrow?: unknown,
): AsyncGenerator<AgentStreamFrame> {
  for (const f of list) yield f;
  if (thenThrow !== undefined) throw thenThrow;
}

function okTurn(threadId: string, text = "答案"): AgentStreamFrame[] {
  return [
    frame("turn.start", { thread_id: threadId }),
    frame("message", { text }),
    frame("turn.end", { status: "done" }),
  ];
}

function thread(over: Record<string, unknown> = {}): never {
  return {
    id: "t1",
    version: 3,
    turns: [],
    model_override: null,
    personality_override: null,
    ...over,
  } as never;
}

function me(features: string[], byok = false): never {
  return { entitlements: { features, byok_active: byok } } as never;
}

let client: QueryClient;
function ui(): ReactElement {
  return (
    <QueryClientProvider client={client}>
      <NextIntlClientProvider locale="zh-TW" messages={messages} timeZone="Asia/Taipei">
        <ChatClient />
      </NextIntlClientProvider>
    </QueryClientProvider>
  );
}

async function mount(search = ""): Promise<ReturnType<typeof render>> {
  hoisted.search.value = search;
  const view = render(ui());
  await settle();
  return view;
}

async function settle(rounds = 6): Promise<void> {
  for (let i = 0; i < rounds; i++) {
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
  }
}

function composer(): Record<string, unknown> {
  return captured.composer as Record<string, unknown>;
}
function call<T extends AnyFn>(fn: unknown, ...args: Parameters<T>): ReturnType<T> {
  return (fn as (...a: Parameters<T>) => ReturnType<T>)(...args);
}
async function send(text: string, attachments: unknown[] = []): Promise<void> {
  await act(async () => {
    call(composer().onSend, text as never, attachments as never);
  });
  await settle();
}
function lastBody(): Record<string, unknown> {
  return vi.mocked(streamAgentQuery).mock.lastCall?.[0] as unknown as Record<string, unknown>;
}

let turnHistory: ({ mode: string; errorMessage: string | null; errorCode: string | null } | null)[];
let unsubscribe: () => void;

beforeEach(() => {
  vi.clearAllMocks();
  seqCounter = 0;
  hoisted.resumeOpts.length = 0;
  for (const k of Object.keys(captured)) hoisted.captured[k] = null;
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  vi.mocked(fetchMe).mockResolvedValue(me([]));
  vi.mocked(getThread).mockResolvedValue(thread());
  vi.mocked(patchThread).mockResolvedValue(undefined as never);
  vi.mocked(cancelTurn).mockResolvedValue(undefined);
  vi.mocked(streamAgentQuery).mockImplementation((() => frames(okTurn("t_new"))) as never);
  useTurnStore.getState().clear();
  useNewThreadStore.getState().reset();
  turnHistory = [];
  unsubscribe = useTurnStore.subscribe((s) => {
    turnHistory.push(
      s.activeTurn
        ? {
            mode: s.activeTurn.mode,
            errorMessage: s.activeTurn.errorMessage,
            errorCode: s.activeTurn.errorCode,
          }
        : null,
    );
  });
});
afterEach(() => {
  unsubscribe();
  cleanup();
  vi.restoreAllMocks();
});

describe("ChatClient rendering states", () => {
  it("shows the empty state for a fresh chat, and a pick sends it", async () => {
    await mount();
    expect(screen.getAllByTestId("empty")).toHaveLength(1);
    expect(screen.queryByTestId("persisted")).toBeNull();
    expect(getThread).not.toHaveBeenCalled();
    await act(async () => {
      call(captured.empty?.onPick, "hello" as never, [] as never);
    });
    await settle();
    expect(lastBody()).toEqual({ message: "hello", web_search: true });
  });

  it("loads the URL thread and shows its history", async () => {
    vi.mocked(getThread).mockResolvedValue(thread({ turns: [{ id: "u1" }] }));
    const view = await mount("thread=t1");
    expect(getThread).toHaveBeenCalledWith("t1", expect.anything());
    expect(screen.queryByTestId("empty")).toBeNull();
    expect(screen.getAllByTestId("persisted")).toHaveLength(1);
    expect(screen.queryByTestId("active")).toBeNull();
    expect(view.container.innerHTML).toMatchSnapshot();
  });

  it("shows neither history nor an error for a thread with no turns", async () => {
    await mount("thread=t1");
    expect(screen.queryByTestId("persisted")).toBeNull();
    expect(screen.queryByText(t.loadError)).toBeNull();
    expect(screen.queryByTestId("empty")).toBeNull();
  });

  it("shows the load error when the thread cannot be read", async () => {
    vi.mocked(getThread).mockRejectedValue(new Error("nope"));
    await mount("thread=t1");
    expect(screen.getByText(t.loadError)).toBeTruthy();
    expect(screen.queryByTestId("persisted")).toBeNull();
  });

  it("follows the URL thread param when it changes", async () => {
    const view = await mount("thread=t1");
    hoisted.search.value = "thread=t2";
    view.rerender(ui());
    await settle();
    expect(getThread).toHaveBeenCalledWith("t2", expect.anything());
    hoisted.search.value = "";
    view.rerender(ui());
    await settle();
    expect(screen.getAllByTestId("empty")).toHaveLength(1);
  });

  it("passes the composer its defaults", async () => {
    const view = await mount();
    const c = composer();
    expect(c.streaming).toBe(false);
    expect(c.modelOverride).toBeNull();
    expect(c.toneOverride).toBeNull();
    expect(c.webSearchEnabled).toBe(true);
    expect(c.deepResearch).toBeNull();
    expect(c.deepResearchLocked).toBe(true);
    expect(c.byokActive).toBe(false);
    expect(view.container.innerHTML).toMatchSnapshot();
  });

  it("does not lock deep research before /me has loaded", async () => {
    vi.mocked(fetchMe).mockReturnValue(new Promise(() => undefined));
    await mount();
    expect(composer().deepResearchLocked).toBe(false);
    expect(composer().deepResearch).toBeNull();
    expect(composer().byokActive).toBeUndefined();
  });

  it("exposes deep research and byok from the account entitlements", async () => {
    vi.mocked(fetchMe).mockResolvedValue(me(["deep_research"], true));
    await mount();
    expect(composer().deepResearchLocked).toBe(false);
    expect(composer().byokActive).toBe(true);
    expect((composer().deepResearch as { enabled: boolean }).enabled).toBe(false);
  });

  it("reads the thread's own model and tone picks, mapping the follow-account tone", async () => {
    vi.mocked(getThread).mockResolvedValue(
      thread({ model_override: "m0", personality_override: "calm" }),
    );
    await mount("thread=t1");
    expect(composer().modelOverride).toBe("m0");
    expect(composer().toneOverride).toBe("calm");
  });
});

describe("ChatClient send flow", () => {
  it("sends a first message, adopts the new thread and reconciles history", async () => {
    await mount();
    const invalidate = vi.spyOn(client, "invalidateQueries");
    vi.mocked(streamAgentQuery).mockImplementation((() => frames(okTurn("t_new"))) as never);
    await send("你好");
    expect(lastBody()).toEqual({ message: "你好", web_search: true });
    expect(hoisted.resumeOpts).toEqual([{ threadIdAtStart: null }]);
    expect(hoisted.replace).toHaveBeenCalledWith("/chat?thread=t_new");
    expect(useNewThreadStore.getState().phase).toBe("resolved");
    expect(useNewThreadStore.getState().threadId).toBe("t_new");
    const keys = invalidate.mock.calls.map((c) => JSON.stringify(c[0]?.queryKey));
    expect(keys).toContain(JSON.stringify(queryKeys.threads));
    expect(keys).toContain(JSON.stringify(queryKeys.thread("t_new")));
    expect(useTurnStore.getState().activeTurn).toBeNull();
    expect(getThread).toHaveBeenCalledWith("t_new", expect.anything());
    expect(turnHistory.some((h) => h?.mode === "send")).toBe(true);
  });

  it("shows the live turn while streaming and ignores a second send", async () => {
    let release: () => void = () => undefined;
    const gate = new Promise<void>((r) => {
      release = r;
    });
    vi.mocked(streamAgentQuery).mockImplementation((() =>
      (async function* () {
        yield frame("turn.start", { thread_id: "t_new" });
        await gate;
        yield frame("turn.end", { status: "done" });
      })()) as never);
    await mount();
    await send("first");
    expect(screen.getByTestId("composer").dataset.streaming).toBe("true");
    expect(screen.getByTestId("active").textContent).toBe("first");
    expect(screen.queryByTestId("empty")).toBeNull();
    expect(screen.getByTestId("pet").textContent).toBe("[true,null]");
    expect(useNewThreadStore.getState().phase).toBe("resolved");
    await send("second");
    expect(streamAgentQuery).toHaveBeenCalledTimes(1);
    await act(async () => {
      release();
    });
    await settle();
    expect(screen.queryByTestId("active")).toBeNull();
    expect(composer().streaming).toBe(false);
  });

  it("sends the thread id, attachments, model, tone and deep research when set", async () => {
    vi.mocked(fetchMe).mockResolvedValue(me(["deep_research"]));
    await mount("thread=t1");
    await act(async () => {
      call(composer().onModelOverrideChange, "m9" as never);
    });
    await settle();
    await act(async () => {
      call(composer().onToneOverrideChange, "calm" as never);
    });
    await settle();
    await act(async () => {
      call((composer().deepResearch as { onChange: AnyFn }).onChange, true as never);
    });
    await act(async () => {
      call(composer().onWebSearchEnabledChange, false as never);
    });
    vi.mocked(streamAgentQuery).mockImplementation((() => frames(okTurn("t1"))) as never);
    const att = { filename: "a.png", media_type: "image/png", data_base64: "AA" };
    await send("hi", [att]);
    expect(lastBody()).toEqual({
      message: "hi",
      web_search: false,
      deep_research: true,
      thread_id: "t1",
      attachments: [att],
    });
    expect(hoisted.resumeOpts).toEqual([{ threadIdAtStart: "t1" }]);
    expect((composer().deepResearch as { enabled: boolean }).enabled).toBe(false);
    expect(streamAgentQuery).toHaveBeenCalledTimes(1);
  });

  it("holds a model/tone picked before the thread exists, sends it, then clears it", async () => {
    await mount();
    await act(async () => {
      call(composer().onModelOverrideChange, "mx" as never);
      call(composer().onToneOverrideChange, "default" as never);
    });
    expect(patchThread).not.toHaveBeenCalled();
    expect(composer().modelOverride).toBe("mx");
    expect(composer().toneOverride).toBeNull();
    await send("hi");
    expect(lastBody()).toEqual({
      message: "hi",
      web_search: true,
      model: "mx",
      personality: "default",
    });
    expect(composer().modelOverride).toBeNull();
    expect(composer().toneOverride).toBeNull();
    await act(async () => {
      call(composer().onToneOverrideChange, "calm" as never);
    });
    // a thread exists now: the pick is persisted immediately
    expect(patchThread).toHaveBeenCalledWith("t_new", { personality_override: "calm" }, 3);
  });

  it("drops the deep research flag when the plan lacks it", async () => {
    await mount();
    expect(composer().deepResearch).toBeNull();
    await send("hi");
    expect(lastBody()).not.toHaveProperty("deep_research");
  });
});

describe("ChatClient per-thread picks", () => {
  it("PATCHes the model with the current version, then refreshes the thread", async () => {
    await mount("thread=t1");
    const invalidate = vi.spyOn(client, "invalidateQueries");
    await act(async () => {
      call(composer().onModelOverrideChange, "m1" as never);
    });
    await settle();
    expect(patchThread).toHaveBeenCalledWith("t1", { model_override: "m1" }, 3);
    expect(invalidate).toHaveBeenCalledWith({ queryKey: queryKeys.thread("t1") });
    await act(async () => {
      call(composer().onModelOverrideChange, null as never);
    });
    await settle();
    expect(patchThread).toHaveBeenLastCalledWith("t1", { model_override: null }, 3);
  });

  it("swallows a failed model or tone PATCH", async () => {
    vi.mocked(patchThread).mockRejectedValue(new Error("409"));
    await mount("thread=t1");
    await act(async () => {
      call(composer().onModelOverrideChange, "m1" as never);
      call(composer().onToneOverrideChange, "calm" as never);
    });
    await settle();
    expect(patchThread).toHaveBeenCalledTimes(2);
    expect(screen.getAllByTestId("composer")).toHaveLength(1);
  });
});

describe("ChatClient errors and cancellation", () => {
  it.each([
    ["chat_limit_reached", t.chatLimitReached],
    ["rate_limited", t.rateLimited],
    ["byok_key_required", t.byokKeyRequired],
    ["feature_not_entitled", t.deepResearchNotEntitled],
    ["something_else", "boom"],
  ])("a refusal with code %s fails the turn with its message", async (code, text) => {
    vi.mocked(streamAgentQuery).mockImplementation((() =>
      frames([], new AgentStreamError("boom", 400, code))) as never);
    await mount();
    useNewThreadStore.getState().startPending();
    await send("hi");
    expect(turnHistory.some((h) => h?.errorMessage === text && h.errorCode === code)).toBe(true);
    expect(useNewThreadStore.getState().phase).toBe("idle");
    expect(hoisted.replace).not.toHaveBeenCalled();
  });

  it("falls back to the generic message for a non-Error throw, and uses Error.message", async () => {
    vi.mocked(streamAgentQuery).mockImplementation((() => frames([], "weird")) as never);
    await mount();
    await send("hi");
    expect(turnHistory.some((h) => h?.errorMessage === t.error && h.errorCode === null)).toBe(true);
    vi.mocked(streamAgentQuery).mockImplementation((() =>
      frames([], new Error("net down"))) as never);
    await send("again");
    expect(turnHistory.some((h) => h?.errorMessage === "net down")).toBe(true);
  });

  it("treats an AbortError as an intentional stop, not a failure", async () => {
    vi.mocked(streamAgentQuery).mockImplementation((() =>
      frames([], new DOMException("stop", "AbortError"))) as never);
    await mount();
    await send("hi");
    expect(turnHistory.some((h) => h?.errorMessage)).toBe(false);
  });

  it("adopts the original thread on a duplicate request without failing", async () => {
    vi.mocked(streamAgentQuery).mockImplementation((() =>
      frames([], new AgentStreamError("dup", 409, "duplicate_request", "t_dup"))) as never);
    await mount();
    await send("hi");
    expect(turnHistory.some((h) => h?.errorMessage)).toBe(false);
    expect(hoisted.replace).toHaveBeenCalledWith("/chat?thread=t_dup");
  });

  it("a duplicate request with no thread id keeps the current thread", async () => {
    vi.mocked(streamAgentQuery).mockImplementation((() =>
      frames([], new AgentStreamError("dup", 409, "duplicate_request"))) as never);
    await mount("thread=t1");
    await send("hi");
    expect(hoisted.replace).not.toHaveBeenCalled();
  });

  it("stop cancels the running turn and aborts the stream", async () => {
    let aborted = false;
    vi.mocked(streamAgentQuery).mockImplementation(((_body: unknown, signal: AbortSignal) =>
      (async function* () {
        yield frame("turn.start", { thread_id: "t1" }, "turnA");
        await new Promise<void>((_, reject) => {
          signal.addEventListener("abort", () => {
            aborted = true;
            reject(new DOMException("stop", "AbortError"));
          });
        });
      })()) as never);
    await mount();
    await send("hi");
    await act(async () => {
      call(composer().onStop);
    });
    await settle();
    expect(cancelTurn).toHaveBeenCalledWith("t1", "turnA");
    expect(aborted).toBe(true);
    expect(composer().streaming).toBe(false);
  });

  it("stop with no turn running does nothing harmful", async () => {
    await mount();
    await act(async () => {
      call(composer().onStop);
    });
    expect(cancelTurn).not.toHaveBeenCalled();
  });
});

describe("ChatClient regenerate, edit and voice", () => {
  beforeEach(() => {
    vi.mocked(getThread).mockResolvedValue(thread({ turns: [{ id: "u1" }] }));
  });

  it("regenerate re-runs the user text through the regenerate stream", async () => {
    vi.mocked(streamRegenerateTurn).mockImplementation((() => frames(okTurn("t1"))) as never);
    await mount("thread=t1");
    await act(async () => {
      call(captured.persisted?.onRegenerate, "a1" as never, "原問題" as never);
    });
    await settle();
    expect(streamRegenerateTurn).toHaveBeenCalledWith(
      "t1",
      "a1",
      { web_search: true },
      expect.anything(),
    );
    expect(turnHistory.some((h) => h?.mode === "regenerate")).toBe(true);
    expect(useNewThreadStore.getState().phase).toBe("idle");
  });

  it("edit resubmits the new text as a send with no attachments", async () => {
    vi.mocked(streamEditTurn).mockImplementation((() => frames(okTurn("t1"))) as never);
    await mount("thread=t1");
    await act(async () => {
      call(composer().onWebSearchEnabledChange, false as never);
    });
    await act(async () => {
      call(captured.persisted?.onEdit, "u1" as never, "新文字" as never);
    });
    await settle();
    expect(streamEditTurn).toHaveBeenCalledWith(
      "t1",
      "u1",
      { message: "新文字", web_search: false },
      expect.anything(),
    );
    expect(turnHistory.some((h) => h?.mode === "send")).toBe(true);
  });

  it("ask-user picks from history and the live turn send like a typed message", async () => {
    await mount("thread=t1");
    await act(async () => {
      call(captured.persisted?.onAskUserPick, "選項A" as never, [] as never);
    });
    await settle();
    expect(lastBody()).toMatchObject({ message: "選項A", thread_id: "t1" });
  });

  it("voice mode opens, runs a turn and returns the spoken answer text", async () => {
    vi.mocked(streamAgentQuery).mockImplementation((() =>
      frames(okTurn("t1", "口說答案"))) as never);
    await mount("thread=t1");
    expect(screen.queryByTestId("voice")).toBeNull();
    await act(async () => {
      call(composer().onOpenVoiceMode);
    });
    expect(screen.getAllByTestId("voice")).toHaveLength(1);
    let answer: unknown;
    await act(async () => {
      answer = await call(captured.voice?.runTurn, "你好" as never);
    });
    expect(answer).toBe("口說答案");
    expect(lastBody()).toMatchObject({ message: "你好", thread_id: "t1" });
    await act(async () => {
      call(captured.voice?.onClose);
    });
    expect(screen.queryByTestId("voice")).toBeNull();
  });

  it("voice mode returns null for a failed turn, and without starting one while streaming", async () => {
    vi.mocked(streamAgentQuery).mockImplementation((() =>
      frames([frame("turn.start", { thread_id: "t1" })], new Error("x"))) as never);
    await mount("thread=t1");
    await act(async () => {
      call(composer().onOpenVoiceMode);
    });
    let answer: unknown = "unset";
    await act(async () => {
      answer = await call(captured.voice?.runTurn, "hi" as never);
    });
    expect(answer).toBeNull();

    let release: () => void = () => undefined;
    const gate = new Promise<void>((r) => {
      release = r;
    });
    vi.mocked(streamAgentQuery).mockClear();
    vi.mocked(streamAgentQuery).mockImplementation((() =>
      (async function* () {
        yield frame("turn.start", { thread_id: "t1" });
        await gate;
      })()) as never);
    await send("busy");
    let blocked: unknown = "unset";
    await act(async () => {
      blocked = await call(captured.voice?.runTurn, "more" as never);
    });
    expect(blocked).toBeNull();
    expect(streamAgentQuery).toHaveBeenCalledTimes(1);
    await act(async () => {
      release();
    });
    await settle();
  });
});

describe("ChatClient pet notice", () => {
  it("announces a level-up, refreshes pet queries, then lets the note fade", async () => {
    const realSetTimeout = window.setTimeout.bind(window);
    let fade: (() => void) | null = null;
    vi.spyOn(window, "setTimeout").mockImplementation(((fn: () => void, ms?: number) => {
      if (ms === 8000) {
        fade = fn;
        return 0;
      }
      return realSetTimeout(fn, ms);
    }) as never);
    vi.mocked(streamAgentQuery).mockImplementation((() =>
      frames([
        frame("turn.start", { thread_id: "t_new" }),
        frame("pet.leveled", { pet_id: "cat", level: 4 }),
        frame("pet.pull_earned", { count: 2 }),
        frame("message", { text: "ok" }),
        frame("turn.end", { status: "done" }),
      ])) as never);
    await mount();
    const invalidate = vi.spyOn(client, "invalidateQueries");
    await send("hi");
    const keys = invalidate.mock.calls.map((c) => JSON.stringify(c[0]?.queryKey));
    expect(keys).toContain(JSON.stringify(queryKeys.settings("pet-active")));
    expect(keys).toContain(JSON.stringify(queryKeys.settings("pet-progress")));
    expect(screen.getByTestId("pet").textContent).toBe(
      JSON.stringify([false, { leveledTo: 4, pullsEarned: 2 }]),
    );
    expect(fade).not.toBeNull();
    await act(async () => {
      (fade as unknown as () => void)();
    });
    expect(screen.getByTestId("pet").textContent).toBe("[false,null]");
  });

  it("shows no note when the turn earned nothing", async () => {
    const setTimeoutSpy = vi.spyOn(window, "setTimeout");
    await mount();
    await send("hi");
    expect(setTimeoutSpy.mock.calls.some((c) => c[1] === 8000)).toBe(false);
    expect(screen.getByTestId("pet").textContent).toBe("[false,null]");
  });
});
