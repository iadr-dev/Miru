// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ScheduleProposalCard } from "@/app/[locale]/(app)/chat/cards/schedule-proposal-card";
import { createSchedule, listSchedules } from "@/lib/api/settings";
import { messages, renderWithIntl } from "@/test/render";
import type { ScheduleJob } from "@/types/settings";

vi.mock("@/lib/api/settings", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api/settings")>();
  return { ...actual, createSchedule: vi.fn(), listSchedules: vi.fn() };
});
vi.mock("@/i18n/navigation", async () => ({
  Link: (await import("@/test/link-stub")).LinkStub,
}));

const t = messages.chat.cards.scheduleProposal;
const templates = messages.settings.alerts.schedules.templates;

const REVIEW = {
  template: "weekly_watchlist_review",
  targets: [],
  market: "TW",
  cadence: "每週六 09:00",
  trigger: { type: "cron", spec: "0 9 * * 6", market: "TW" },
  action: { skill_ref: "watchlist-review", inputs: { review: "weekly" } },
  summary: "每週六 09:00：我的自選股",
  requires_user_confirmation: true,
  next_step: "DONE",
};

async function settle(): Promise<void> {
  for (let i = 0; i < 4; i++) {
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
  }
}

function mount(data: Record<string, unknown>, existing: ScheduleJob[] = []): HTMLElement {
  vi.mocked(listSchedules).mockResolvedValue(existing);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return renderWithIntl(
    <QueryClientProvider client={client}>
      <ScheduleProposalCard data={data} />
    </QueryClientProvider>,
  ).container;
}

beforeEach(() => {
  vi.clearAllMocks();
});
afterEach(() => {
  cleanup();
});

describe("ScheduleProposalCard: weekly watchlist review", () => {
  it("shows the briefing and the proposed weekday and time, with no ticker line", async () => {
    const c = mount(REVIEW);
    await settle();
    expect(screen.getByText(templates.weekly_watchlist_review.label)).toBeTruthy();
    expect(screen.getByText("每週六 09:00")).toBeTruthy();
    expect(c.querySelectorAll("p")).toHaveLength(4); // title, label, cadence, caveat
  });

  it("shows the weekday and time the user asked for, read from the trigger", async () => {
    mount({ ...REVIEW, trigger: { type: "cron", spec: "30 20 * * 0", market: "TW" } });
    await settle();
    expect(screen.getByText("每週日 20:30")).toBeTruthy();
  });

  it("creates nothing until 建立 is tapped, then posts the trigger and action it carries", async () => {
    vi.mocked(createSchedule).mockResolvedValue({} as never);
    mount(REVIEW);
    await settle();
    expect(createSchedule).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: t.create }));
    await settle();
    expect(vi.mocked(createSchedule).mock.calls[0]?.[0]).toEqual({
      trigger: REVIEW.trigger,
      action: REVIEW.action,
      delivery_channels: ["in_app"],
    });
  });

  it("buttons are at least 44px tall", async () => {
    mount(REVIEW);
    await settle();
    for (const name of [t.create, t.dismiss]) {
      expect(screen.getByRole("button", { name }).className).toMatch(/min-h-11|h-11/);
    }
  });

  it("renders nothing for a proposal that lost its template", async () => {
    const c = mount({ ...REVIEW, template: "made_up" });
    await settle();
    expect(c.querySelector("section")).toBeNull();
  });
});
