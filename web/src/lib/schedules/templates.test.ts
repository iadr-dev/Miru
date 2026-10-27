import { describe, expect, it } from "vitest";
import { scheduleJobSchema } from "@/types/settings";
import { findExistingJob, parseScheduleProposal, toScheduleRequest } from "./proposal";
import {
  buildScheduleAction,
  buildWatchlistAction,
  buildWeeklyTrigger,
  checkTargets,
  DEFAULT_WEEKLY_WHEN,
  isTimeOfDay,
  MAX_TARGETS,
  parseTargets,
  parseWeeklyCron,
  SCHEDULE_TEMPLATE_IDS,
  SCHEDULE_TEMPLATES,
  supportsWatchlist,
  targetsOfJob,
  templateOfJob,
  watchlistOfJob,
  weeklyWhenOfTrigger,
} from "./templates";

const job = (action: { skill_ref: string; inputs: Record<string, unknown> }, trigger: object) =>
  scheduleJobSchema.parse({
    id: "j1",
    user_id: "u",
    trigger,
    action,
    delivery_channels: ["in_app"],
    quiet_hours: null,
    daily_limit: null,
    enabled: true,
    last_run: null,
    next_run: null,
    version: 0,
    created_at: "2026-10-01T00:00:00Z",
    updated_at: "2026-10-01T00:00:00Z",
  });

describe("parseTargets", () => {
  it("splits on commas, spaces and 、, upper-cases and de-duplicates", () => {
    expect(parseTargets(" 2330, 2317 、aapl 2330 ")).toEqual(["2330", "2317", "AAPL"]);
    expect(parseTargets("  ,, ")).toEqual([]);
  });
});

describe("checkTargets", () => {
  const brief = SCHEDULE_TEMPLATES.pre_open_brief;
  const chip = SCHEDULE_TEMPLATES.after_close_chip;

  it("accepts a list only for multi-target templates", () => {
    expect(checkTargets(brief, "2330, 2317")).toEqual({ ok: true, targets: ["2330", "2317"] });
    expect(checkTargets(chip, "2330, 2317")).toEqual({ ok: false, reason: "singleOnly" });
  });

  it("flags empty, over-long and malformed input", () => {
    expect(checkTargets(chip, "")).toEqual({ ok: false, reason: "required" });
    const many = Array.from({ length: MAX_TARGETS + 1 }, (_, i) => `${1000 + i}`).join(",");
    expect(checkTargets(brief, many)).toEqual({ ok: false, reason: "tooMany" });
    expect(checkTargets(chip, "23 30!")).toEqual({ ok: false, reason: "singleOnly" });
    expect(checkTargets(chip, "bad code!")).toEqual({ ok: false, reason: "singleOnly" });
    expect(checkTargets(chip, "2330!")).toEqual({ ok: false, reason: "format" });
  });
});

describe("buildScheduleAction", () => {
  it("uses a list input for multi-target skills and a scalar for the rest", () => {
    expect(buildScheduleAction(SCHEDULE_TEMPLATES.pre_open_brief, ["2330", "2317"])).toEqual({
      skill_ref: "daily-context-brief",
      inputs: { stock_ids: ["2330", "2317"] },
    });
    expect(buildScheduleAction(SCHEDULE_TEMPLATES.tw_etf_report, ["0050"])).toEqual({
      skill_ref: "etf-report",
      inputs: { market: "TW", etf_id: "0050" },
    });
  });

  it("carries the fixed extra inputs (the US snapshot needs its market)", () => {
    expect(buildScheduleAction(SCHEDULE_TEMPLATES.us_stock_report, ["AAPL"]).inputs).toEqual({
      market: "US",
      symbol_id: "AAPL",
    });
  });
});

describe("templateOfJob / targetsOfJob", () => {
  it("round-trips every catalogue template", () => {
    for (const id of SCHEDULE_TEMPLATE_IDS) {
      const template = SCHEDULE_TEMPLATES[id];
      const stored = job(buildScheduleAction(template, ["2330"]), template.trigger);
      expect(templateOfJob(stored)?.id).toBe(id);
      expect(targetsOfJob(stored, template)).toEqual(template.takesTargets ? ["2330"] : []);
    }
  });

  it("returns null for a job that is not from the catalogue", () => {
    const stored = job(
      { skill_ref: "stock-score", inputs: {} },
      { type: "cron", spec: "0 9 * * 1-5", market: "TW" },
    );
    expect(templateOfJob(stored)).toBeNull();
  });
});

describe("schedule proposal card", () => {
  const data = {
    template: "pre_open_brief",
    targets: ["2330"],
    market: "TW",
    cadence: "每個台股交易日開盤前",
    trigger: { type: "market_event", spec: "pre_open", market: "TW" },
    action: { skill_ref: "daily-context-brief", inputs: { stock_ids: ["2330"] } },
    summary: "x",
    requires_user_confirmation: true,
    next_step: "DONE",
    non_trading_caveat: "c",
  };

  it("parses the tool output, ignoring the extra keys", () => {
    const proposal = parseScheduleProposal(data);
    expect(proposal?.template).toBe("pre_open_brief");
    expect(proposal && toScheduleRequest(proposal)).toEqual({
      trigger: data.trigger,
      action: data.action,
      delivery_channels: ["in_app"],
    });
  });

  it("renders nothing for a malformed proposal", () => {
    expect(parseScheduleProposal({ ...data, template: "not_a_template" })).toBeNull();
    expect(parseScheduleProposal({ ...data, targets: [] })).toBeNull();
  });

  it("recognises an already-created identical schedule, whatever the input key order", () => {
    const proposal = parseScheduleProposal(data);
    expect(proposal).not.toBeNull();
    const existing = job(
      { skill_ref: "daily-context-brief", inputs: { stock_ids: ["2330"] } },
      data.trigger,
    );
    if (proposal) {
      expect(findExistingJob([existing], proposal)?.id).toBe("j1");
      const other = job(
        { skill_ref: "daily-context-brief", inputs: { stock_ids: ["2317"] } },
        data.trigger,
      );
      expect(findExistingJob([other], proposal)).toBeNull();
    }
  });
});

describe("watchlist-scoped briefings", () => {
  it("only the multi-stock Taiwan briefing can follow a watchlist", () => {
    const followable = SCHEDULE_TEMPLATE_IDS.filter((id) =>
      supportsWatchlist(SCHEDULE_TEMPLATES[id]),
    );
    expect(followable).toEqual(["pre_open_brief"]);
  });

  it("stores the group id (not a stock list) and reads it back from the job", () => {
    const template = SCHEDULE_TEMPLATES.pre_open_brief;
    const action = buildWatchlistAction(template, "wl-1");
    expect(action).toEqual({ skill_ref: "daily-context-brief", inputs: { watchlist_id: "wl-1" } });
    const stored = job(action, template.trigger);
    expect(watchlistOfJob(stored)).toBe("wl-1");
    expect(templateOfJob(stored)?.id).toBe("pre_open_brief");
    expect(targetsOfJob(stored, template)).toEqual([]);
  });

  it("a job that lists stocks itself follows no watchlist", () => {
    const template = SCHEDULE_TEMPLATES.pre_open_brief;
    const stored = job(buildScheduleAction(template, ["2330"]), template.trigger);
    expect(watchlistOfJob(stored)).toBeNull();
  });
});

describe("weekly watchlist review", () => {
  const review = SCHEDULE_TEMPLATES.weekly_watchlist_review;

  it("is a cron schedule on the watchlist-review skill, Saturday 09:00 Taipei by default", () => {
    expect(review.skillRef).toBe("watchlist-review");
    expect(review.trigger).toEqual({ type: "cron", spec: "0 9 * * 6", market: "TW" });
    expect(review.takesTargets).toBe(false);
    expect(DEFAULT_WEEKLY_WHEN).toEqual({ weekday: 6, time: "09:00" });
  });

  it("takes no tickers: the action is the skill and its mode only", () => {
    expect(buildScheduleAction(review, [])).toEqual({
      skill_ref: "watchlist-review",
      inputs: { review: "weekly" },
    });
    expect(buildScheduleAction(review, ["2330"]).inputs).toEqual({ review: "weekly" });
    expect(supportsWatchlist(review)).toBe(false);
  });

  it("builds a cron from the weekday and time and reads it back", () => {
    expect(buildWeeklyTrigger({ weekday: 0, time: "20:30" })).toEqual({
      type: "cron",
      spec: "30 20 * * 0",
      market: "TW",
    });
    expect(buildWeeklyTrigger({ weekday: 1, time: "07:05" }).spec).toBe("5 7 * * 1");
    expect(parseWeeklyCron("30 20 * * 0")).toEqual({ weekday: 0, time: "20:30" });
    expect(parseWeeklyCron("0 9 * * 7")).toEqual({ weekday: 0, time: "09:00" });
    for (const when of [DEFAULT_WEEKLY_WHEN, { weekday: 3, time: "00:00" } as const]) {
      expect(parseWeeklyCron(buildWeeklyTrigger(when).spec)).toEqual(when);
    }
  });

  it("falls back to the default for a cron it did not write", () => {
    expect(parseWeeklyCron("0 9 * * 1-5")).toBeNull();
    expect(parseWeeklyCron("61 9 * * 1")).toBeNull();
    expect(parseWeeklyCron("0 25 * * 1")).toBeNull();
    expect(weeklyWhenOfTrigger({ type: "cron", spec: "0 8 * * 1-5", market: "TW" })).toEqual(
      DEFAULT_WEEKLY_WHEN,
    );
  });

  it("validates a time of day", () => {
    expect(["09:00", "9:05", "23:59"].every(isTimeOfDay)).toBe(true);
    expect(["", "24:00", "9am", "09:60"].some(isTimeOfDay)).toBe(false);
  });

  it("is recognised whatever weekday and time the user chose, but only with its mode", () => {
    const action = buildScheduleAction(review, []);
    for (const spec of ["0 9 * * 6", "30 20 * * 0", "0 7 * * 1"]) {
      const stored = job(action, { type: "cron", spec, market: "TW" });
      expect(templateOfJob(stored)?.id).toBe("weekly_watchlist_review");
    }
    const adHoc = job(
      { skill_ref: "watchlist-review", inputs: {} },
      { type: "cron", spec: "0 9 * * 6", market: "TW" },
    );
    expect(templateOfJob(adHoc)).toBeNull();
  });

  it("a proposal for it carries no tickers; one for named stocks must", () => {
    const proposal = {
      template: "weekly_watchlist_review",
      targets: [],
      trigger: review.trigger,
      action: { skill_ref: "watchlist-review", inputs: { review: "weekly" } },
    };
    const parsed = parseScheduleProposal(proposal);
    expect(parsed?.template).toBe("weekly_watchlist_review");
    expect(parsed && toScheduleRequest(parsed).action.inputs).toEqual({ review: "weekly" });
    expect(parseScheduleProposal({ ...proposal, template: "tw_etf_report" })).toBeNull();
    expect(
      findExistingJob(
        [job(proposal.action, { type: "cron", spec: "0 9 * * 6", market: "TW" })],
        parsed as NonNullable<typeof parsed>,
      )?.id,
    ).toBe("j1");
  });
});
