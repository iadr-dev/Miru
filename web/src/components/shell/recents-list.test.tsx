// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useNewThreadStore } from "@/components/shell/new-thread-store";
import { RecentsList } from "@/components/shell/recents-list";
import { deleteThread, deleteThreads, listThreads, patchThread } from "@/lib/api/threads";
import { messages, renderWithIntl } from "@/test/render";
import type { Thread } from "@/types/conversation";

const push = vi.fn();
let search = "";

vi.mock("next/navigation", async (importOriginal) => ({
  ...(await importOriginal<typeof import("next/navigation")>()),
  useSearchParams: () => new URLSearchParams(search),
}));
vi.mock("@/i18n/navigation", () => ({ useRouter: () => ({ push }) }));
vi.mock("@/lib/api/threads", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api/threads")>();
  return {
    ...actual,
    listThreads: vi.fn(),
    patchThread: vi.fn(),
    deleteThread: vi.fn(),
    deleteThreads: vi.fn(),
  };
});

const t = messages.chat.threads;
const sel = t.select;

const DAY = 24 * 60 * 60 * 1000;
const BASE = Date.parse("2026-10-20T04:00:00Z");

function thread(id: string, title: string, daysAgo: number, over: Partial<Thread> = {}): Thread {
  const at = new Date(BASE - daysAgo * DAY).toISOString();
  return {
    id,
    user_id: "u1",
    title,
    starred: false,
    archived: false,
    tags: [],
    last_message_at: at,
    version: 2,
    created_at: at,
    updated_at: at,
    turns: [],
    ...over,
  };
}

const LIST: Thread[] = [
  thread("t1", "今天的對話", 0),
  thread("t2", "昨天的對話", 1),
  thread("t3", "上週的對話", 5),
  thread("t4", "上個月的對話", 20),
  thread("t5", "很久以前", 60),
  thread("t6", "星號對話", 3, { starred: true }),
];

function mount(): HTMLElement {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return renderWithIntl(
    <QueryClientProvider client={client}>
      <RecentsList />
    </QueryClientProvider>,
  ).container;
}

async function settle(rounds = 4): Promise<void> {
  for (let i = 0; i < rounds; i++) {
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
  }
}

async function mountList(list: Thread[] = LIST): Promise<HTMLElement> {
  vi.mocked(listThreads).mockResolvedValue(list);
  const c = mount();
  await settle();
  return c;
}

function rowOf(title: string): HTMLElement {
  const li = screen.getByText(title).closest("li");
  if (li === null) throw new Error(`no row for ${title}`);
  return li;
}

/** Open a row's "more" menu (Radix opens it on Enter/Space on the trigger). */
async function openMenu(title: string): Promise<void> {
  const trigger = within(rowOf(title)).getByRole("button", { name: t.menu.label });
  fireEvent.keyDown(trigger, { key: "Enter" });
  await settle(2);
}

beforeEach(() => {
  vi.clearAllMocks();
  search = "";
  useNewThreadStore.getState().reset();
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe(): void {}
      unobserve(): void {}
      disconnect(): void {}
    },
  );
  vi.stubGlobal(
    "matchMedia",
    (query: string) =>
      ({
        matches: true,
        media: query,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
      }) as unknown as MediaQueryList,
  );
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("RecentsList states", () => {
  it("shows a skeleton while loading, with the pending placeholder when a thread is starting", () => {
    vi.mocked(listThreads).mockReturnValue(new Promise(() => undefined));
    const plain = mount();
    expect(plain.innerHTML).toMatchSnapshot();
    expect(screen.queryByRole("status", { name: t.creating })).toBeNull();
    cleanup();
    useNewThreadStore.getState().startPending();
    const pending = mount();
    expect(screen.getByRole("status", { name: t.creating })).toBeTruthy();
    expect(pending.innerHTML).toMatchSnapshot();
  });

  it("shows the error line, with the placeholder when a thread is starting", async () => {
    vi.mocked(listThreads).mockRejectedValue(new Error("x"));
    const c = mount();
    await settle();
    expect(screen.getByText(t.error)).toBeTruthy();
    expect(screen.queryByRole("status", { name: t.creating })).toBeNull();
    expect(c.innerHTML).toMatchSnapshot();
    cleanup();
    useNewThreadStore.getState().startPending();
    mount();
    await settle();
    expect(screen.getByText(t.error)).toBeTruthy();
    expect(screen.getByRole("status", { name: t.creating })).toBeTruthy();
  });

  it("shows the empty line, with the placeholder when a thread is starting", async () => {
    const c = await mountList([]);
    expect(screen.getByText(t.empty)).toBeTruthy();
    expect(screen.queryByRole("status", { name: t.creating })).toBeNull();
    expect(c.innerHTML).toMatchSnapshot();
    cleanup();
    useNewThreadStore.getState().startPending();
    await mountList([]);
    expect(screen.getByText(t.empty)).toBeTruthy();
    expect(screen.getByRole("status", { name: t.creating })).toBeTruthy();
  });
});

describe("RecentsList list", () => {
  it("pins starred threads, then groups the rest by recency", async () => {
    const c = await mountList();
    const labels = Array.from(c.querySelectorAll("p.uppercase")).map((p) => p.textContent);
    expect(labels).toEqual([
      t.recents,
      t.starred,
      t.groups.today,
      t.groups.yesterday,
      t.groups.previous7Days,
      t.groups.previous30Days,
      t.groups.older,
    ]);
    expect(c.innerHTML).toMatchSnapshot();
  });

  it("omits the starred section when nothing is starred and empty recency buckets", async () => {
    const c = await mountList([thread("t1", "只有今天", 0)]);
    const labels = Array.from(c.querySelectorAll("p.uppercase")).map((p) => p.textContent);
    expect(labels).toEqual([t.recents, t.groups.today]);
  });

  it("falls back to the untitled label for a thread with no title", async () => {
    await mountList([thread("t1", "", 0)]);
    expect(screen.getByText(t.untitled)).toBeTruthy();
  });

  it("marks the thread named by ?thread= as current and opens another on click", async () => {
    search = "thread=t2";
    await mountList();
    const current = screen.getByText("昨天的對話").closest("button");
    expect(current?.getAttribute("aria-current")).toBe("true");
    expect(
      screen.getByText("今天的對話").closest("button")?.getAttribute("aria-current"),
    ).toBeNull();
    fireEvent.click(screen.getByText("今天的對話"));
    expect(push).toHaveBeenCalledWith("/chat?thread=t1");
  });

  it("shows the pending placeholder until the new thread's row is in the list", async () => {
    useNewThreadStore.getState().startPending();
    await mountList();
    expect(screen.getByRole("status", { name: t.creating })).toBeTruthy();
    act(() => useNewThreadStore.getState().resolve("t1"));
    // Resolved and its row exists in the list: the placeholder gives way to the (revealing) row.
    expect(screen.queryByRole("status", { name: t.creating })).toBeNull();
  });

  it("keeps the placeholder while the resolved thread is not in the list yet", async () => {
    useNewThreadStore.getState().startPending();
    act(() => useNewThreadStore.getState().resolve("t99"));
    await mountList();
    expect(screen.getByRole("status", { name: t.creating })).toBeTruthy();
  });

  it("reveals a new thread's title with a typewriter, then returns to a plain row", async () => {
    vi.stubGlobal(
      "matchMedia",
      (query: string) =>
        ({
          matches: false,
          media: query,
          addEventListener: vi.fn(),
          removeEventListener: vi.fn(),
        }) as unknown as MediaQueryList,
    );
    useNewThreadStore.getState().startPending();
    act(() => useNewThreadStore.getState().resolve("t1"));
    vi.mocked(listThreads).mockResolvedValue([thread("t1", "新標題", 0)]);
    const c = mount();
    await settle(2);
    // Mid-reveal: a typed prefix with a blinking cursor.
    expect(c.querySelector("li button span.animate-pulse")).not.toBeNull();
    expect(useNewThreadStore.getState().phase).toBe("resolved");
    await act(async () => {
      await new Promise((r) => setTimeout(r, 250));
    });
    expect(screen.getByText("新標題")).toBeTruthy();
    expect(useNewThreadStore.getState().phase).toBe("idle");
  });

  it("reveals at once when motion is reduced", async () => {
    useNewThreadStore.getState().startPending();
    act(() => useNewThreadStore.getState().resolve("t1"));
    await mountList([thread("t1", "新標題", 0)]);
    expect(screen.getByText("新標題")).toBeTruthy();
    expect(useNewThreadStore.getState().phase).toBe("idle");
  });
});

describe("RecentsList row menu", () => {
  it("stars a thread with its version and refreshes the list", async () => {
    vi.mocked(patchThread).mockResolvedValue(undefined as never);
    await mountList();
    await openMenu("今天的對話");
    fireEvent.click(screen.getByRole("menuitem", { name: t.menu.star }));
    await settle();
    expect(patchThread).toHaveBeenCalledWith("t1", { starred: true }, 2);
    expect(listThreads).toHaveBeenCalledTimes(2);
  });

  it("offers to remove the star from a starred thread", async () => {
    vi.mocked(patchThread).mockResolvedValue(undefined as never);
    await mountList();
    await openMenu("星號對話");
    fireEvent.click(screen.getByRole("menuitem", { name: t.menu.unstar }));
    await settle();
    expect(patchThread).toHaveBeenCalledWith("t6", { starred: false }, 2);
  });

  it("renames on blur with a trimmed new title", async () => {
    vi.mocked(patchThread).mockResolvedValue(undefined as never);
    await mountList();
    await openMenu("今天的對話");
    fireEvent.click(screen.getByRole("menuitem", { name: t.menu.rename }));
    const input = screen.getByPlaceholderText(t.rename.placeholder);
    expect((input as HTMLInputElement).value).toBe("今天的對話");
    fireEvent.change(input, { target: { value: "  改名了  " } });
    fireEvent.blur(input);
    await settle();
    expect(patchThread).toHaveBeenCalledWith("t1", { title: "改名了" }, 2);
    expect(screen.queryByPlaceholderText(t.rename.placeholder)).toBeNull();
  });

  it("Enter commits, and an unchanged or empty title just closes the field", async () => {
    vi.mocked(patchThread).mockResolvedValue(undefined as never);
    await mountList();
    await openMenu("今天的對話");
    fireEvent.click(screen.getByRole("menuitem", { name: t.menu.rename }));
    let input = screen.getByPlaceholderText(t.rename.placeholder);
    fireEvent.blur(input);
    expect(patchThread).not.toHaveBeenCalled();
    expect(screen.queryByPlaceholderText(t.rename.placeholder)).toBeNull();
    await openMenu("今天的對話");
    fireEvent.click(screen.getByRole("menuitem", { name: t.menu.rename }));
    input = screen.getByPlaceholderText(t.rename.placeholder);
    fireEvent.change(input, { target: { value: "   " } });
    fireEvent.blur(input);
    expect(patchThread).not.toHaveBeenCalled();
  });

  it("Escape cancels the rename; Enter blurs the field", async () => {
    await mountList();
    await openMenu("今天的對話");
    fireEvent.click(screen.getByRole("menuitem", { name: t.menu.rename }));
    const input = screen.getByPlaceholderText(t.rename.placeholder);
    fireEvent.change(input, { target: { value: "別存" } });
    fireEvent.keyDown(input, { key: "Escape" });
    expect(screen.queryByPlaceholderText(t.rename.placeholder)).toBeNull();
    expect(patchThread).not.toHaveBeenCalled();
    await openMenu("今天的對話");
    fireEvent.click(screen.getByRole("menuitem", { name: t.menu.rename }));
    const again = screen.getByPlaceholderText(t.rename.placeholder);
    again.focus();
    fireEvent.keyDown(again, { key: "Enter" });
    expect(document.activeElement).not.toBe(again);
  });

  it("a failed rename closes the field", async () => {
    vi.mocked(patchThread).mockRejectedValue(new Error("x"));
    await mountList();
    await openMenu("今天的對話");
    fireEvent.click(screen.getByRole("menuitem", { name: t.menu.rename }));
    const input = screen.getByPlaceholderText(t.rename.placeholder);
    fireEvent.change(input, { target: { value: "新名" } });
    fireEvent.blur(input);
    await settle();
    expect(screen.queryByPlaceholderText(t.rename.placeholder)).toBeNull();
  });

  it("deletes after confirming, and cancelling deletes nothing", async () => {
    vi.mocked(deleteThread).mockResolvedValue(undefined);
    await mountList();
    await openMenu("今天的對話");
    fireEvent.click(screen.getByRole("menuitem", { name: t.menu.delete }));
    await settle(2);
    const dialog = screen.getByRole("alertdialog");
    expect(dialog.textContent).toContain("「今天的對話」將永久刪除");
    fireEvent.click(within(dialog).getByRole("button", { name: t.delete.cancel }));
    await settle(2);
    expect(deleteThread).not.toHaveBeenCalled();
    await openMenu("今天的對話");
    fireEvent.click(screen.getByRole("menuitem", { name: t.menu.delete }));
    await settle(2);
    fireEvent.click(
      within(screen.getByRole("alertdialog")).getByRole("button", { name: t.delete.confirm }),
    );
    await settle();
    expect(deleteThread).toHaveBeenCalledWith("t1");
    expect(listThreads).toHaveBeenCalledTimes(2);
  });

  it("the delete dialog names an untitled thread by the untitled label", async () => {
    await mountList([thread("t1", "", 0)]);
    await openMenu(t.untitled);
    fireEvent.click(screen.getByRole("menuitem", { name: t.menu.delete }));
    await settle(2);
    expect(screen.getByRole("alertdialog").textContent).toContain(`「${t.untitled}」將永久刪除`);
  });
});

describe("RecentsList select mode", () => {
  async function enter(): Promise<HTMLElement> {
    const c = await mountList();
    fireEvent.click(screen.getByRole("button", { name: sel.enter }));
    return c;
  }

  it("swaps row menus for checkboxes, adds a select-all row and a cancel toggle", async () => {
    const c = await enter();
    expect(screen.getByRole("button", { name: sel.exit })).toBeTruthy();
    expect(
      screen.getByRole("button", { name: sel.selectAll.replace("{count}", "6") }),
    ).toBeTruthy();
    expect(screen.queryByRole("button", { name: t.menu.label })).toBeNull();
    expect(c.innerHTML).toMatchSnapshot();
  });

  it("row clicks toggle selection instead of navigating, and show the bulk bar", async () => {
    await enter();
    const first = screen.getByText("今天的對話").closest("button") as HTMLElement;
    expect(first.getAttribute("aria-pressed")).toBe("false");
    expect(first.getAttribute("aria-current")).toBeNull();
    fireEvent.click(first);
    expect(push).not.toHaveBeenCalled();
    expect(first.getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByText(sel.selectedCount.replace("{count}", "1"))).toBeTruthy();
    fireEvent.click(first);
    expect(first.getAttribute("aria-pressed")).toBe("false");
    expect(screen.queryByText(/已選取/)).toBeNull();
  });

  it("select all selects every thread, and again clears them", async () => {
    await enter();
    const all = screen.getByRole("button", { name: sel.selectAll.replace("{count}", "6") });
    fireEvent.click(all);
    expect(screen.getByText(sel.selectedCount.replace("{count}", "6"))).toBeTruthy();
    fireEvent.click(all);
    expect(screen.queryByText(/已選取/)).toBeNull();
  });

  it("selecting every row by hand makes select-all read as checked", async () => {
    const c = await enter();
    for (const thread of LIST) {
      fireEvent.click(screen.getByText(thread.title));
    }
    const all = screen.getByRole("button", { name: sel.selectAll.replace("{count}", "6") });
    expect(all.querySelector("svg")).not.toBeNull();
    expect(c.innerHTML).toMatchSnapshot();
  });

  it("cancel leaves select mode and forgets the selection", async () => {
    await enter();
    fireEvent.click(screen.getByText("今天的對話"));
    fireEvent.click(screen.getByRole("button", { name: sel.exit }));
    expect(screen.getByRole("button", { name: sel.enter })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: sel.enter }));
    expect(screen.queryByText(/已選取/)).toBeNull();
  });

  it("bulk delete confirms, deletes the selection, exits and refreshes", async () => {
    vi.mocked(deleteThreads).mockResolvedValue({ failedIds: [] });
    await enter();
    fireEvent.click(screen.getByText("今天的對話"));
    fireEvent.click(screen.getByText("昨天的對話"));
    fireEvent.click(screen.getByRole("button", { name: sel.delete }));
    await settle(2);
    const dialog = screen.getByRole("alertdialog");
    expect(dialog.textContent).toContain("刪除 2 則對話？");
    fireEvent.click(within(dialog).getByRole("button", { name: sel.confirm }));
    await settle();
    expect(deleteThreads).toHaveBeenCalledWith(["t1", "t2"]);
    expect(screen.getByRole("button", { name: sel.enter })).toBeTruthy();
    expect(screen.queryByRole("status", { name: "" })).toBeNull();
    expect(listThreads).toHaveBeenCalledTimes(2);
  });

  it("a partial failure still exits and shows a count toast that dismisses itself", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    vi.mocked(deleteThreads).mockResolvedValue({ failedIds: ["t2"] });
    vi.mocked(listThreads).mockResolvedValue(LIST);
    mount();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10);
    });
    fireEvent.click(screen.getByRole("button", { name: sel.enter }));
    fireEvent.click(screen.getByText("今天的對話"));
    fireEvent.click(screen.getByText("昨天的對話"));
    fireEvent.click(screen.getByRole("button", { name: sel.delete }));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10);
    });
    fireEvent.click(
      within(screen.getByRole("alertdialog")).getByRole("button", { name: sel.confirm }),
    );
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10);
    });
    expect(screen.getByRole("button", { name: sel.enter })).toBeTruthy();
    expect(screen.getByRole("status").textContent).toBe(sel.partialFailed.replace("{count}", "1"));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(4100);
    });
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("cancelling the bulk confirm keeps select mode and deletes nothing", async () => {
    await enter();
    fireEvent.click(screen.getByText("今天的對話"));
    fireEvent.click(screen.getByRole("button", { name: sel.delete }));
    await settle(2);
    fireEvent.click(
      within(screen.getByRole("alertdialog")).getByRole("button", { name: sel.cancel }),
    );
    await settle(2);
    expect(deleteThreads).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: sel.exit })).toBeTruthy();
  });
});
