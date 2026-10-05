import { describe, expect, it } from "vitest";
import { scheduleJobSchema } from "@/types/settings";
import { findExistingJob, parseScheduleProposal, toScheduleRequest } from "./proposal";
import {
  buildScheduleAction,
  buildWatchlistAction,
  checkTargets,
  MAX_TARGETS,
  parseTargets,
  SCHEDULE_TEMPLATE_IDS,
  SCHEDULE_TEMPLATES,
  supportsWatchlist,
  targetsOfJob,
  templateOfJob,
  watchlistOfJob,
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
      expect(targetsOfJob(stored, template)).toEqual(["2330"]);
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
