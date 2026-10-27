// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ScheduleList } from "@/app/[locale]/(app)/settings/alerts/schedule-list";
import {
  createSchedule,
  deleteSchedule,
  listSchedules,
  SettingsApiError,
  updateSchedule,
} from "@/lib/api/settings";
import { getWatchlists } from "@/lib/api/watchlist";
import { messages, renderWithIntl } from "@/test/render";
import type { ScheduleJob } from "@/types/settings";
import type { WatchlistGroupView } from "@/types/watchlist";

vi.mock("@/lib/api/settings", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api/settings")>();
  return {
    ...actual,
    createSchedule: vi.fn(),
    deleteSchedule: vi.fn(),
    listSchedules: vi.fn(),
    updateSchedule: vi.fn(),
  };
});
vi.mock("@/lib/api/watchlist", () => ({ getWatchlists: vi.fn() }));
vi.mock("@/i18n/navigation", async () => ({
  Link: (await import("@/test/link-stub")).LinkStub,
}));

const t = messages.settings.alerts.schedules;
const f = t.form;
const common = messages.settings.common;

function job(over: Partial<ScheduleJob> = {}): ScheduleJob {
  return {
    id: "s1",
    user_id: "u1",
    trigger: { type: "market_event", spec: "pre_open", market: "TW" },
    action: { skill_ref: "daily-context-brief", inputs: { stock_ids: ["2330", "2317"] } },
    delivery_channels: ["in_app"],
    quiet_hours: null,
    daily_limit: null,
    enabled: true,
    last_run: null,
    next_run: null,
    status: "ok",
    status_reason: null,
    thread_id: null,
    version: 4,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    ...over,
  };
}

function group(id: string, name: string): WatchlistGroupView {
  return {
    group: {
      id,
      user_id: "u1",
      name,
      market: "TW",
      asset_class: "stock",
      sort_order: "a",
      is_default: false,
      color: null,
      version: 1,
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z",
    },
    items: [],
  } as WatchlistGroupView;
}

const GROUPS = [group("g_tw", "台股群"), group("g_two", "第二群")];

function mount(jobs: ScheduleJob[]): HTMLElement {
  vi.mocked(listSchedules).mockResolvedValue(jobs);
  vi.mocked(getWatchlists).mockResolvedValue(GROUPS);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return renderWithIntl(
    <QueryClientProvider client={client}>
      <ScheduleList />
    </QueryClientProvider>,
  ).container;
}

/** innerHTML without the animated opacity/transform an entering block carries mid-test. */
function html(c: HTMLElement): string {
  return c.innerHTML.replaceAll(/ style="opacity: [^"]*"/g, "");
}

async function settle(rounds = 4): Promise<void> {
  for (let i = 0; i < rounds; i++) {
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
  }
}

function pick(label: string, value: string): void {
  fireEvent.change(screen.getByLabelText(label), { target: { value } });
}

function type(label: string, value: string): void {
  fireEvent.change(screen.getByLabelText(label), { target: { value } });
}

function submitForm(name: string): void {
  fireEvent.submit(screen.getByRole("form", { name }));
}

async function openCreate(jobs: ScheduleJob[] = []): Promise<HTMLElement> {
  const c = mount(jobs);
  await settle();
  fireEvent.click(screen.getByRole("button", { name: t.createCta }));
  return c;
}

/** The create form on the multi-stock briefing (the default template is a single-stock report). */
async function openMulti(jobs: ScheduleJob[] = []): Promise<HTMLElement> {
  const c = await openCreate(jobs);
  pick(f.template, "pre_open_brief");
  return c;
}

beforeEach(() => {
  vi.clearAllMocks();
});
afterEach(() => {
  cleanup();
});

describe("ScheduleList states", () => {
  it("shows the loading skeleton first", () => {
    vi.mocked(listSchedules).mockReturnValue(new Promise(() => undefined));
    const client = new QueryClient();
    const c = renderWithIntl(
      <QueryClientProvider client={client}>
        <ScheduleList />
      </QueryClientProvider>,
    ).container;
    expect(html(c)).toMatchSnapshot();
  });

  it("shows a retryable error when the list fails", async () => {
    vi.mocked(listSchedules).mockRejectedValue(new Error("boom"));
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const c = renderWithIntl(
      <QueryClientProvider client={client}>
        <ScheduleList />
      </QueryClientProvider>,
    ).container;
    await settle();
    expect(html(c)).toMatchSnapshot();
    vi.mocked(listSchedules).mockResolvedValue([]);
    fireEvent.click(screen.getByRole("button", { name: /重試|retry/i }));
    await settle();
    expect(listSchedules).toHaveBeenCalledTimes(2);
  });

  it("is empty with a create call to action", async () => {
    const c = mount([]);
    await settle();
    expect(html(c)).toMatchSnapshot();
  });

  it("opening create from the empty state hides the empty message's alternative", async () => {
    const c = await openCreate();
    expect(html(c)).toMatchSnapshot();
  });
});

describe("ScheduleList rows", () => {
  it("renders every title, cadence, status and problem variant", async () => {
    const c = mount([
      job({ id: "a", next_run: "2026-10-16T01:00:00Z", last_run: "2026-10-15T01:00:00Z" }),
      job({
        id: "b",
        trigger: { type: "market_event", spec: "after_close", market: "TW" },
        action: { skill_ref: "chip-flow-review", inputs: { stock_id: "2330" } },
        last_run: "2026-10-15T08:00:00Z",
        status: "error",
        thread_id: "th 1",
      }),
      job({
        id: "c",
        action: { skill_ref: "daily-context-brief", inputs: { watchlist_id: "g_tw" } },
        enabled: false,
        status: "skipped_quota",
      }),
      job({
        id: "d",
        trigger: { type: "cron", spec: "0 8 * * 1", market: "TW" },
        action: { skill_ref: "custom-skill", inputs: {} },
      }),
      job({
        id: "e",
        trigger: { type: "market_event", spec: "after_close", market: "TW" },
        action: { skill_ref: "chip-flow-review", inputs: {} },
      }),
    ]);
    await settle();
    expect(html(c)).toMatchSnapshot();
    expect(screen.getByText(`${t.lastRunFailed}`)).toBeTruthy();
    expect(screen.getByText(t.lastRunSkippedQuota)).toBeTruthy();
    expect(screen.getByText(t.paused, { exact: false })).toBeTruthy();
    expect(c.querySelector("a[href='/chat?thread=th%201']")).not.toBeNull();
  });

  it("toggling a row updates it with its version, and an error shows the generic alert", async () => {
    vi.mocked(updateSchedule).mockRejectedValue(new Error("x"));
    mount([job()]);
    await settle();
    fireEvent.click(screen.getByRole("switch"));
    await settle();
    expect(updateSchedule).toHaveBeenCalledWith("s1", { enabled: false, expected_version: 4 });
    expect(screen.getByRole("alert").textContent).toBe(common.errorGeneric);
  });

  it("the plus button under the list opens create, and hides while a form is open", async () => {
    const c = mount([job()]);
    await settle();
    const create = screen.getByRole("button", { name: t.createCta });
    fireEvent.click(create);
    expect(screen.queryByRole("button", { name: t.createCta })).toBeNull();
    expect(c.querySelector("form")).not.toBeNull();
  });

  it("edit opens a form for the row and closes any create form", async () => {
    mount([job()]);
    await settle();
    fireEvent.click(screen.getByRole("button", { name: t.createCta }));
    expect(screen.getAllByRole("form")).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: /^編輯 .+ 的排程$/ }));
    const forms = screen.getAllByRole("form");
    expect(forms.map((x) => x.getAttribute("aria-label"))).toEqual([f.editTitle]);
  });

  it("deleting asks first, then deletes and closes the dialog", async () => {
    vi.mocked(deleteSchedule).mockResolvedValue(undefined as never);
    mount([job()]);
    await settle();
    fireEvent.click(screen.getByRole("button", { name: /^刪除 .+ 的排程$/ }));
    const dialog = screen.getByRole("dialog");
    expect(dialog.textContent).toContain("確定要刪除");
    fireEvent.click(screen.getByRole("button", { name: common.delete }));
    await settle();
    expect(deleteSchedule).toHaveBeenCalledWith("s1");
  });

  it("cancelling the delete dialog deletes nothing", async () => {
    mount([job()]);
    await settle();
    fireEvent.click(screen.getByRole("button", { name: /^刪除 .+ 的排程$/ }));
    fireEvent.click(screen.getByText(common.cancel));
    await settle();
    expect(deleteSchedule).not.toHaveBeenCalled();
  });
});

describe("ScheduleForm rendering", () => {
  it("create form starts on the single-stock report template", async () => {
    const c = await openCreate();
    expect((screen.getByLabelText(f.template) as HTMLSelectElement).value).toBe("tw_stock_report");
    expect(html(c)).toMatchSnapshot();
  });

  it("the multi-stock briefing offers a stocks/watchlist scope", async () => {
    const c = await openMulti();
    expect(screen.getByLabelText(f.scope)).toBeTruthy();
    expect(html(c)).toMatchSnapshot();
  });

  it("a single-target template has no scope choice and a single-code label", async () => {
    const c = await openCreate();
    pick(f.template, "after_close_chip");
    expect(screen.queryByLabelText(f.scope)).toBeNull();
    expect(screen.getByLabelText(f.targetsOne)).toBeTruthy();
    expect(html(c)).toMatchSnapshot();
  });

  it("a US template uses the US placeholder and no scope", async () => {
    await openCreate();
    pick(f.template, "us_stock_report");
    expect((screen.getByLabelText(f.targetsOne) as HTMLInputElement).placeholder).toBe("AAPL");
  });

  it("following the watchlist swaps the stock box for a group picker with a note", async () => {
    const c = await openMulti();
    pick(f.scope, "watchlist");
    await settle();
    expect(screen.queryByLabelText(f.targetsOne)).toBeNull();
    expect(html(c)).toMatchSnapshot();
  });

  it("switching to a template that cannot follow falls back to stocks", async () => {
    await openMulti();
    pick(f.scope, "watchlist");
    pick(f.template, "after_close_chip");
    expect(screen.getByLabelText(f.targetsOne)).toBeTruthy();
    expect(screen.queryByLabelText(f.watchlist)).toBeNull();
  });

  it("editing a stock job fills its targets and locks the briefing type", async () => {
    const c = mount([job()]);
    await settle();
    fireEvent.click(screen.getByRole("button", { name: /^編輯 .+ 的排程$/ }));
    expect((screen.getByLabelText(f.template) as HTMLSelectElement).disabled).toBe(true);
    expect(
      (screen.getByLabelText(f.targetsMany.replace("{max}", "10")) as HTMLInputElement).value,
    ).toBe("2330, 2317");
    expect(c.querySelector("form[aria-label='編輯排程']")).not.toBeNull();
    expect(html(c)).toMatchSnapshot();
  });

  it("editing a watchlist job starts on the group picker with the group selected", async () => {
    mount([
      job({ action: { skill_ref: "daily-context-brief", inputs: { watchlist_id: "g_tw" } } }),
    ]);
    await settle();
    fireEvent.click(screen.getByRole("button", { name: /^編輯 .+ 的排程$/ }));
    await settle();
    expect((screen.getByLabelText(f.watchlist) as HTMLSelectElement).value).toBe("g_tw");
  });

  it("editing a job of an unknown template starts from the default briefing with no targets", async () => {
    mount([job({ action: { skill_ref: "custom-skill", inputs: {} } })]);
    await settle();
    fireEvent.click(screen.getByRole("button", { name: /^編輯 .+ 的排程$/ }));
    expect((screen.getByLabelText(f.template) as HTMLSelectElement).value).toBe("tw_stock_report");
    expect((screen.getByLabelText(f.targetsOne) as HTMLInputElement).value).toBe("");
  });
});

describe("ScheduleForm validation and submit", () => {
  const many = f.targetsMany.replace("{max}", "10");

  it("shows nothing before a submit, then the matching error after", async () => {
    await openMulti();
    expect(screen.queryByRole("alert")).toBeNull();
    submitForm(f.title);
    expect(screen.getByRole("alert").textContent).toBe(f.error.required);
    expect(screen.getByLabelText(many).getAttribute("aria-invalid")).toBe("true");
    expect(createSchedule).not.toHaveBeenCalled();
  });

  it("rejects too many, a bad format and (single templates) more than one code", async () => {
    await openMulti();
    type(many, "1,2,3,4,5,6,7,8,9,10,11");
    submitForm(f.title);
    expect(screen.getByRole("alert").textContent).toBe(f.error.tooMany.replace("{max}", "10"));
    type(many, "bad!code");
    expect(screen.getByRole("alert").textContent).toBe(f.error.format);
    pick(f.template, "after_close_chip");
    type(f.targetsOne, "2330 2317");
    expect(screen.getByRole("alert").textContent).toBe(f.error.singleOnly);
    expect(createSchedule).not.toHaveBeenCalled();
  });

  it("creates a schedule from the typed stocks and closes", async () => {
    vi.mocked(createSchedule).mockResolvedValue(job() as never);
    await openMulti();
    type(many, "2330、2317");
    submitForm(f.title);
    await settle();
    expect(createSchedule).toHaveBeenCalledWith({
      trigger: { type: "market_event", spec: "pre_open", market: "TW" },
      action: { skill_ref: "daily-context-brief", inputs: { stock_ids: ["2330", "2317"] } },
      delivery_channels: ["in_app"],
    });
    expect(screen.queryByRole("form")).toBeNull();
  });

  it("creates a single-target schedule with a scalar input", async () => {
    vi.mocked(createSchedule).mockResolvedValue(job() as never);
    await openMulti();
    pick(f.template, "after_close_chip");
    type(f.targetsOne, "2330");
    submitForm(f.title);
    await settle();
    expect(createSchedule).toHaveBeenCalledWith(
      expect.objectContaining({
        action: { skill_ref: "chip-flow-review", inputs: { stock_id: "2330" } },
      }),
    );
  });

  it("following a watchlist asks for a group, then saves the group", async () => {
    vi.mocked(createSchedule).mockResolvedValue(job() as never);
    await openMulti();
    pick(f.scope, "watchlist");
    await settle();
    submitForm(f.title);
    expect(screen.getByRole("alert").textContent).toBe(f.errorWatchlistRequired);
    expect(createSchedule).not.toHaveBeenCalled();
    pick(f.watchlist, "g_two");
    expect(screen.queryByRole("alert")).toBeNull();
    submitForm(f.title);
    await settle();
    expect(createSchedule).toHaveBeenCalledWith(
      expect.objectContaining({
        action: { skill_ref: "daily-context-brief", inputs: { watchlist_id: "g_two" } },
      }),
    );
  });

  it("editing saves with the expected version", async () => {
    vi.mocked(updateSchedule).mockResolvedValue(job() as never);
    mount([job()]);
    await settle();
    fireEvent.click(screen.getByRole("button", { name: /^編輯 .+ 的排程$/ }));
    type(many, "2454");
    submitForm(f.editTitle);
    await settle();
    expect(updateSchedule).toHaveBeenCalledWith("s1", {
      action: { skill_ref: "daily-context-brief", inputs: { stock_ids: ["2454"] } },
      expected_version: 4,
    });
    expect(screen.queryByRole("form")).toBeNull();
  });

  it.each([
    ["cap_reached", f.errorCap],
    ["stale_version", f.errorStale],
    ["other", f.errorGeneric],
  ])("a %s failure explains itself and keeps the form", async (code, message) => {
    vi.mocked(createSchedule).mockRejectedValue(new SettingsApiError(code, "x", 409, false));
    await openMulti();
    type(many, "2330");
    submitForm(f.title);
    await settle();
    expect(screen.getByRole("alert").textContent).toBe(message);
    expect(screen.getByRole("form")).toBeTruthy();
    // A stale version also refreshes the list.
    expect(vi.mocked(listSchedules).mock.calls).toHaveLength(code === "stale_version" ? 2 : 1);
  });

  it("a non-API failure shows the generic message", async () => {
    vi.mocked(createSchedule).mockRejectedValue(new Error("network"));
    await openMulti();
    type(many, "2330");
    submitForm(f.title);
    await settle();
    expect(screen.getByRole("alert").textContent).toBe(f.errorGeneric);
  });

  it("Escape and Cancel close the form", async () => {
    await openMulti();
    fireEvent.keyDown(screen.getByRole("form"), { key: "Escape" });
    expect(screen.queryByRole("form")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: t.createCta }));
    fireEvent.click(screen.getByRole("button", { name: f.cancel }));
    expect(screen.queryByRole("form")).toBeNull();
  });

  it("other keys do not close the form", async () => {
    await openMulti();
    fireEvent.keyDown(screen.getByRole("form"), { key: "a" });
    expect(screen.getByRole("form")).toBeTruthy();
  });
});

describe("weekly watchlist review", () => {
  const review = t.templates.weekly_watchlist_review;
  const days = t.weekdays;

  function reviewJob(spec = "0 9 * * 6", over: Partial<ScheduleJob> = {}): ScheduleJob {
    return job({
      id: "r1",
      trigger: { type: "cron", spec, market: "TW" },
      action: { skill_ref: "watchlist-review", inputs: { review: "weekly" } },
      ...over,
    });
  }

  async function openReview(jobs: ScheduleJob[] = []): Promise<HTMLElement> {
    const c = await openCreate(jobs);
    pick(f.template, "weekly_watchlist_review");
    return c;
  }

  it("is listed in the briefing select with its label", async () => {
    await openCreate();
    const select = screen.getByLabelText(f.template) as HTMLSelectElement;
    const option = [...select.options].find((o) => o.value === "weekly_watchlist_review");
    expect(option?.textContent).toBe(review.label);
  });

  it("has no ticker box or scope choice: a description, a weekday and a time instead", async () => {
    const c = await openReview();
    expect(screen.queryByLabelText(f.targetsOne)).toBeNull();
    expect(screen.queryByLabelText(f.scope)).toBeNull();
    expect(screen.getByText(review.description)).toBeTruthy();
    expect((screen.getByLabelText(f.weekday) as HTMLSelectElement).value).toBe("6");
    expect((screen.getByLabelText(f.time) as HTMLInputElement).value).toBe("09:00");
    expect(screen.getByText("每週六 09:00")).toBeTruthy();
    expect(html(c)).toMatchSnapshot();
  });

  it("lists the seven weekdays, Monday first", async () => {
    await openReview();
    const select = screen.getByLabelText(f.weekday) as HTMLSelectElement;
    expect([...select.options].map((o) => o.textContent)).toEqual([
      days["1"],
      days["2"],
      days["3"],
      days["4"],
      days["5"],
      days["6"],
      days["0"],
    ]);
  });

  it("stacks at 390px and has 44px tap targets, with the description across both columns", async () => {
    const c = await openReview();
    const fieldset = c.querySelector("fieldset");
    expect(fieldset?.className).toContain("grid");
    expect(fieldset?.className).toContain("sm:grid-cols-2");
    expect(fieldset?.className).not.toMatch(/(^| )grid-cols-2/);
    for (const label of [f.template, f.weekday, f.time]) {
      expect(screen.getByLabelText(label).className).toContain("min-h-11");
    }
    expect(screen.getByText(review.description).className).toContain("sm:col-span-2");
    expect(c.querySelector("form")?.className).not.toContain("overflow");
    // Nothing forces a width: no fixed pixel widths on the controls.
    expect(c.innerHTML).not.toMatch(/ w-\[\d+px\]/);
  });

  it("creates the default schedule: Saturday 09:00, no tickers, in-app delivery", async () => {
    vi.mocked(createSchedule).mockResolvedValue(reviewJob() as never);
    await openReview();
    submitForm(f.title);
    await settle();
    expect(createSchedule).toHaveBeenCalledWith({
      trigger: { type: "cron", spec: "0 9 * * 6", market: "TW" },
      action: { skill_ref: "watchlist-review", inputs: { review: "weekly" } },
      delivery_channels: ["in_app"],
    });
    expect(screen.queryByRole("form")).toBeNull();
  });

  it("creates it on the weekday and time the user picked", async () => {
    vi.mocked(createSchedule).mockResolvedValue(reviewJob() as never);
    await openReview();
    pick(f.weekday, "0");
    type(f.time, "20:30");
    submitForm(f.title);
    await settle();
    expect(createSchedule).toHaveBeenCalledWith(
      expect.objectContaining({ trigger: { type: "cron", spec: "30 20 * * 0", market: "TW" } }),
    );
  });

  it("asks for a time when it is cleared, and creates nothing", async () => {
    await openReview();
    type(f.time, "");
    submitForm(f.title);
    expect(screen.getByRole("alert").textContent).toBe(f.errorTimeRequired);
    expect(createSchedule).not.toHaveBeenCalled();
    type(f.time, "08:15");
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("shows in the list as the label with the user's own weekday and time", async () => {
    const c = mount([reviewJob("30 20 * * 0", { next_run: "2026-10-25T12:30:00Z" })]);
    await settle();
    const row = c.querySelector("li");
    expect(row?.textContent).toContain(review.label);
    expect(row?.textContent).toContain("每週日 20:30");
    expect(row?.textContent).not.toContain("、");
    for (const control of screen.getAllByRole("button")) {
      expect(control.className).not.toContain("size-8");
    }
  });

  it("editing fills the weekday and time from the cron and locks the briefing type", async () => {
    mount([reviewJob("15 7 * * 1")]);
    await settle();
    fireEvent.click(screen.getByRole("button", { name: /^編輯 .+ 的排程$/ }));
    expect((screen.getByLabelText(f.template) as HTMLSelectElement).disabled).toBe(true);
    expect((screen.getByLabelText(f.template) as HTMLSelectElement).value).toBe(
      "weekly_watchlist_review",
    );
    expect((screen.getByLabelText(f.weekday) as HTMLSelectElement).value).toBe("1");
    expect((screen.getByLabelText(f.time) as HTMLInputElement).value).toBe("07:15");
    expect(screen.queryByLabelText(f.targetsOne)).toBeNull();
  });

  it("saving an edit sends the new trigger with the expected version", async () => {
    vi.mocked(updateSchedule).mockResolvedValue(reviewJob() as never);
    mount([reviewJob("0 9 * * 6", { version: 7 })]);
    await settle();
    fireEvent.click(screen.getByRole("button", { name: /^編輯 .+ 的排程$/ }));
    pick(f.weekday, "5");
    type(f.time, "18:45");
    submitForm(f.editTitle);
    await settle();
    expect(updateSchedule).toHaveBeenCalledWith("r1", {
      action: { skill_ref: "watchlist-review", inputs: { review: "weekly" } },
      trigger: { type: "cron", spec: "45 18 * * 5", market: "TW" },
      expected_version: 7,
    });
  });

  it("a hand-written cron opens on the default weekday and time", async () => {
    mount([reviewJob("0 9 * * 1-5")]);
    await settle();
    fireEvent.click(screen.getByRole("button", { name: /^編輯 .+ 的排程$/ }));
    expect((screen.getByLabelText(f.weekday) as HTMLSelectElement).value).toBe("6");
    expect((screen.getByLabelText(f.time) as HTMLInputElement).value).toBe("09:00");
  });

  it("switching away from it brings the ticker box back", async () => {
    await openReview();
    pick(f.template, "after_close_chip");
    expect(screen.getByLabelText(f.targetsOne)).toBeTruthy();
    expect(screen.queryByLabelText(f.weekday)).toBeNull();
  });
});
