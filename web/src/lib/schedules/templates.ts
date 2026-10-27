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
  "weekly_watchlist_review",
] as const;
export type ScheduleTemplateId = (typeof SCHEDULE_TEMPLATE_IDS)[number];

export type ScheduleTemplate = {
  id: ScheduleTemplateId;
  skillRef: string;
  trigger: ScheduleTrigger;
  /** The skill input holding the target(s), and whether it takes a list. */
  targetInput: string;
  multiple: boolean;
  /** False for the briefings about the user's whole account (the weekly watchlist review): there
   * are no tickers to enter, and the weekday and time are the user's own choice. */
  takesTargets: boolean;
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
    takesTargets: true,
    extraInputs: {},
    targetMarket: "TW",
  },
  after_close_chip: {
    id: "after_close_chip",
    skillRef: "chip-flow-review",
    trigger: { type: "market_event", spec: "after_close", market: "TW" },
    targetInput: "stock_id",
    multiple: false,
    takesTargets: true,
    extraInputs: {},
    targetMarket: "TW",
  },
  disposition_watch: {
    id: "disposition_watch",
    skillRef: "disposition-watch",
    trigger: { type: "market_event", spec: "after_close", market: "TW" },
    targetInput: "stock_id",
    multiple: false,
    takesTargets: true,
    extraInputs: {},
    targetMarket: "TW",
  },
  weekly_catalyst: {
    id: "weekly_catalyst",
    skillRef: "catalyst-scan",
    trigger: { type: "cron", spec: "30 8 * * 1", market: "TW" },
    targetInput: "stock_id",
    multiple: false,
    takesTargets: true,
    extraInputs: {},
    targetMarket: "TW",
  },
  tw_stock_report: {
    id: "tw_stock_report",
    skillRef: "stock-report-tw",
    trigger: { type: "cron", spec: "30 8 * * 1", market: "TW" },
    targetInput: "stock_id",
    multiple: false,
    takesTargets: true,
    extraInputs: { market: "TW" },
    targetMarket: "TW",
  },
  us_stock_report: {
    id: "us_stock_report",
    skillRef: "stock-report-us",
    trigger: { type: "cron", spec: "0 9 * * 6", market: "US" },
    targetInput: "symbol_id",
    multiple: false,
    takesTargets: true,
    extraInputs: { market: "US" },
    targetMarket: "US",
  },
  tw_etf_report: {
    id: "tw_etf_report",
    skillRef: "etf-report",
    trigger: { type: "cron", spec: "30 15 * * 5", market: "TW" },
    targetInput: "etf_id",
    multiple: false,
    takesTargets: true,
    extraInputs: { market: "TW" },
    targetMarket: "TW",
  },
  us_etf_report: {
    id: "us_etf_report",
    skillRef: "etf-report",
    trigger: { type: "cron", spec: "0 9 * * 6", market: "US" },
    targetInput: "etf_id",
    multiple: false,
    takesTargets: true,
    extraInputs: { market: "US" },
    targetMarket: "US",
  },
  weekly_watchlist_review: {
    id: "weekly_watchlist_review",
    skillRef: "watchlist-review",
    trigger: { type: "cron", spec: "0 9 * * 6", market: "TW" },
    targetInput: "",
    multiple: false,
    takesTargets: false,
    extraInputs: { review: "weekly" },
    targetMarket: "TW",
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
  if (!template.takesTargets) {
    return { skill_ref: template.skillRef, inputs: { ...template.extraInputs } };
  }
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
 * way (e.g. the API with a hand-written cron). The weekly review matches on its skill input alone:
 * the weekday and time in its cron are the user's. */
export function templateOfJob(job: ScheduleJob): ScheduleTemplate | null {
  for (const id of SCHEDULE_TEMPLATE_IDS) {
    const template = SCHEDULE_TEMPLATES[id];
    if (!template.takesTargets) {
      if (
        template.skillRef === job.action.skill_ref &&
        job.trigger.type === "cron" &&
        matchesFixedInputs(template, job)
      ) {
        return template;
      }
      continue;
    }
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

function matchesFixedInputs(template: ScheduleTemplate, job: ScheduleJob): boolean {
  return Object.entries(template.extraInputs).every(
    ([key, value]) => job.action.inputs[key] === value,
  );
}

/** Cron weekdays, Sunday = 0 — the order the weekday picker lists them in (Monday first). */
export const WEEKDAY_ORDER = [1, 2, 3, 4, 5, 6, 0] as const;
export type Weekday = (typeof WEEKDAY_ORDER)[number];

export type WeeklyWhen = { weekday: Weekday; time: string };

/** Saturday morning, after Friday's close in both Taipei and New York. */
export const DEFAULT_WEEKLY_WHEN: WeeklyWhen = { weekday: 6, time: "09:00" };

const WEEKLY_CRON = /^(\d{1,2}) (\d{1,2}) \* \* ([0-7])$/;
const TIME_OF_DAY = /^([01]?\d|2[0-3]):([0-5]\d)$/;

const WEEKDAY_SET: ReadonlySet<number> = new Set(WEEKDAY_ORDER);

function isWeekday(value: number): value is Weekday {
  return WEEKDAY_SET.has(value);
}

/** The weekday and time a weekly cron ("30 20 * * 0") names, or null for any other spec. */
export function parseWeeklyCron(spec: string): WeeklyWhen | null {
  const match = WEEKLY_CRON.exec(spec.trim());
  if (!match) {
    return null;
  }
  const minute = Number(match[1]);
  const hour = Number(match[2]);
  const weekday = Number(match[3]) % 7;
  if (minute > 59 || hour > 23 || !isWeekday(weekday)) {
    return null;
  }
  return { weekday, time: `${String(hour).padStart(2, "0")}:${String(minute).padStart(2, "0")}` };
}

export function isTimeOfDay(value: string): boolean {
  return TIME_OF_DAY.test(value);
}

/** The cron trigger for the user's weekday and "HH:MM" (Taipei time); the backend runs the cron in
 * the trigger's market timezone. */
export function buildWeeklyTrigger(when: WeeklyWhen): ScheduleTrigger {
  const match = TIME_OF_DAY.exec(when.time);
  const hour = Number(match?.[1] ?? 9);
  const minute = Number(match?.[2] ?? 0);
  return { type: "cron", spec: `${minute} ${hour} * * ${when.weekday}`, market: "TW" };
}

/** When a weekly job fires, falling back to the default for a hand-written cron. */
export function weeklyWhenOfTrigger(trigger: ScheduleTrigger): WeeklyWhen {
  return parseWeeklyCron(trigger.spec) ?? DEFAULT_WEEKLY_WHEN;
}
