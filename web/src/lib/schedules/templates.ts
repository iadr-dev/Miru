import type { Market, ScheduleAction, ScheduleJob, ScheduleTrigger } from "@/types/settings";

/** The scheduled-briefing catalogue — mirrors backend/src/schemas/schedule_templates.py (the chat
 * agent's `propose_schedule` draws from the same list). Each template is a governed skill, when it
 * runs, and which skill input carries the user's target(s). Skills disagree on that input's name
 * and arity, which is why the mapping lives here and not in the form. */
export const SCHEDULE_TEMPLATE_IDS = [
  "tw_stock_report",
  "us_stock_report",
  "tw_etf_report",
  "us_etf_report",
  "pre_open_brief",
  "after_close_chip",
  "disposition_watch",
  "weekly_catalyst",
] as const;
export type ScheduleTemplateId = (typeof SCHEDULE_TEMPLATE_IDS)[number];

export type ScheduleTemplate = {
  id: ScheduleTemplateId;
  skillRef: string;
  trigger: ScheduleTrigger;
  /** The skill input holding the target(s), and whether it takes a list. */
  targetInput: string;
  multiple: boolean;
  extraInputs: Record<string, unknown>;
  /** The market the targets belong to (drives the placeholder and the US ticker hint). */
  targetMarket: Market;
};

export const MAX_TARGETS = 10;

export const SCHEDULE_TEMPLATES: Record<ScheduleTemplateId, ScheduleTemplate> = {
  pre_open_brief: {
    id: "pre_open_brief",
    skillRef: "daily-context-brief",
    trigger: { type: "market_event", spec: "pre_open", market: "TW" },
    targetInput: "stock_ids",
    multiple: true,
    extraInputs: {},
    targetMarket: "TW",
  },
  after_close_chip: {
    id: "after_close_chip",
    skillRef: "chip-flow-review",
    trigger: { type: "market_event", spec: "after_close", market: "TW" },
    targetInput: "stock_id",
    multiple: false,
    extraInputs: {},
    targetMarket: "TW",
  },
  disposition_watch: {
    id: "disposition_watch",
    skillRef: "disposition-watch",
    trigger: { type: "market_event", spec: "after_close", market: "TW" },
    targetInput: "stock_id",
    multiple: false,
    extraInputs: {},
    targetMarket: "TW",
  },
  weekly_catalyst: {
    id: "weekly_catalyst",
    skillRef: "catalyst-scan",
    trigger: { type: "cron", spec: "30 8 * * 1", market: "TW" },
    targetInput: "stock_id",
    multiple: false,
    extraInputs: {},
    targetMarket: "TW",
  },
  tw_stock_report: {
    id: "tw_stock_report",
    skillRef: "stock-report-tw",
    trigger: { type: "cron", spec: "30 8 * * 1", market: "TW" },
    targetInput: "stock_id",
    multiple: false,
    extraInputs: { market: "TW" },
    targetMarket: "TW",
  },
  us_stock_report: {
    id: "us_stock_report",
    skillRef: "stock-report-us",
    trigger: { type: "cron", spec: "0 9 * * 6", market: "US" },
    targetInput: "symbol_id",
    multiple: false,
    extraInputs: { market: "US" },
    targetMarket: "US",
  },
  tw_etf_report: {
    id: "tw_etf_report",
    skillRef: "etf-report",
    trigger: { type: "cron", spec: "30 15 * * 5", market: "TW" },
    targetInput: "etf_id",
    multiple: false,
    extraInputs: { market: "TW" },
    targetMarket: "TW",
  },
  us_etf_report: {
    id: "us_etf_report",
    skillRef: "etf-report",
    trigger: { type: "cron", spec: "0 9 * * 6", market: "US" },
    targetInput: "etf_id",
    multiple: false,
    extraInputs: { market: "US" },
    targetMarket: "US",
  },
};

const TICKER = /^[A-Z0-9][A-Z0-9.-]{0,15}$/;

/** "2330, 2317" / "2330 2317" / "2330、2317" → ["2330", "2317"] (upper-cased, de-duplicated). */
export function parseTargets(raw: string): string[] {
  const seen = new Set<string>();
  for (const part of raw.split(/[\s,、，]+/)) {
    const code = part.trim().toUpperCase();
    if (code) {
      seen.add(code);
    }
  }
  return [...seen];
}

export type TargetCheck =
  | { ok: true; targets: string[] }
  | { ok: false; reason: "required" | "tooMany" | "singleOnly" | "format" };

export function checkTargets(template: ScheduleTemplate, raw: string): TargetCheck {
  const targets = parseTargets(raw);
  if (targets.length === 0) {
    return { ok: false, reason: "required" };
  }
  if (!template.multiple && targets.length > 1) {
    return { ok: false, reason: "singleOnly" };
  }
  if (targets.length > MAX_TARGETS) {
    return { ok: false, reason: "tooMany" };
  }
  if (targets.some((code) => !TICKER.test(code))) {
    return { ok: false, reason: "format" };
  }
  return { ok: true, targets };
}

export function buildScheduleAction(template: ScheduleTemplate, targets: string[]): ScheduleAction {
  const [first] = targets;
  return {
    skill_ref: template.skillRef,
    inputs: {
      ...template.extraInputs,
      [template.targetInput]: template.multiple ? targets : first,
    },
  };
}

/** Only the multi-stock Taiwan briefing can follow a watchlist group: it expands the group to its
 * current members on every run, so adding a stock to the group changes the next briefing. */
export const WATCHLIST_INPUT = "watchlist_id";

export function supportsWatchlist(template: ScheduleTemplate): boolean {
  return template.multiple && template.targetMarket === "TW";
}

export function buildWatchlistAction(
  template: ScheduleTemplate,
  watchlistId: string,
): ScheduleAction {
  return {
    skill_ref: template.skillRef,
    inputs: { ...template.extraInputs, [WATCHLIST_INPUT]: watchlistId },
  };
}

/** The watchlist group a job follows, or null if it lists stocks itself. */
export function watchlistOfJob(job: ScheduleJob): string | null {
  const value = job.action.inputs[WATCHLIST_INPUT];
  return typeof value === "string" && value !== "" ? value : null;
}

/** Which catalogue template a stored job is (skill + trigger), or null for a job created some other
 * way (e.g. the API with a hand-written cron). */
export function templateOfJob(job: ScheduleJob): ScheduleTemplate | null {
  for (const id of SCHEDULE_TEMPLATE_IDS) {
    const template = SCHEDULE_TEMPLATES[id];
    if (
      template.skillRef === job.action.skill_ref &&
      template.trigger.type === job.trigger.type &&
      template.trigger.spec === job.trigger.spec &&
      template.trigger.market === job.trigger.market
    ) {
      return template;
    }
  }
  return null;
}

/** The target codes a job was created for, read back from whichever input the skill uses. */
export function targetsOfJob(job: ScheduleJob, template: ScheduleTemplate): string[] {
  const value = job.action.inputs[template.targetInput];
  if (Array.isArray(value)) {
    return value.filter((item): item is string => typeof item === "string");
  }
  return typeof value === "string" ? [value] : [];
}
