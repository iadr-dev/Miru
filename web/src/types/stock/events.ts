/**
 * Events — `/stock/disposition-securities`, `/stock/calendar-events`,
 * `/stock/calendar-events-feed`.
 */

import { z } from "zod";
import { healthStatusSchema, maskedTeaserSchema } from "@/types/market/shared";

// ---------------------------------------------------------------------------
// 5. GET /stock/disposition-securities — get_disposition_securities (Sponsor-tier)
// ---------------------------------------------------------------------------

export const dispositionSecuritiesRowSchema = z.object({
  symbol: z.string(),
  market: z.string(),
  date: z.string(),
  stock_name: z.string(),
  disposition_cnt: z.number(),
  condition: z.string(),
  measure: z.string(),
});

export type DispositionSecuritiesRow = z.infer<typeof dispositionSecuritiesRowSchema>;

export const dispositionSecuritiesDataSchema = z.object({
  source_id: z.string(),
  currentness_caveat: z.string(),
  health_status: healthStatusSchema,
  rows: z.array(dispositionSecuritiesRowSchema).default([]),
});

export type DispositionSecuritiesData = z.infer<typeof dispositionSecuritiesDataSchema>;

export const dispositionSecuritiesResponseSchema = z.union([
  dispositionSecuritiesDataSchema,
  maskedTeaserSchema,
]);

export type DispositionSecuritiesResponse = z.infer<typeof dispositionSecuritiesResponseSchema>;

// ---------------------------------------------------------------------------
// 15. GET /stock/calendar-events — get_calendar_events (Sponsor-tier)
// ---------------------------------------------------------------------------

export const calendarEventOutSchema = z.object({
  id: z.string(),
  stock_id: z.string().nullable().optional(),
  type: z.string(),
  title_zh: z.string(),
  title_en: z.string(),
  event_date: z.string().nullable().optional(),
  window_start: z.string().nullable().optional(),
  window_end: z.string().nullable().optional(),
  state: z.string(),
  source: z.string(),
});

export type CalendarEventOut = z.infer<typeof calendarEventOutSchema>;

export const stockCalendarEventsDataSchema = z.object({
  source_id: z.string(),
  currentness_caveat: z.string(),
  health_status: healthStatusSchema,
  events: z.array(calendarEventOutSchema).default([]),
});

export type StockCalendarEventsData = z.infer<typeof stockCalendarEventsDataSchema>;

// `get_calendar_events` is public (CB events are only added for Premium/Pro), never a teaser.
export const stockCalendarEventsResponseSchema = stockCalendarEventsDataSchema;

export type StockCalendarEventsResponse = z.infer<typeof stockCalendarEventsResponseSchema>;

// ---------------------------------------------------------------------------
// 23. GET /stock/calendar-events-feed — get_calendar_events_feed (yfinance.Calendars,
// market-wide, non-TW). Backs the US 總覽 tab's upcoming-events calendar card.
// ---------------------------------------------------------------------------

export const earningsCalendarRowSchema = z.object({
  market: z.string(),
  symbol: z.string(),
  company: z.string(),
  event_start: z.string(),
  timing: z.string().nullable().optional(),
  eps_estimate: z.number().nullable().optional(),
  reported_eps: z.number().nullable().optional(),
  surprise_pct: z.number().nullable().optional(),
});

export type EarningsCalendarRow = z.infer<typeof earningsCalendarRowSchema>;

export const ipoCalendarRowSchema = z.object({
  market: z.string(),
  symbol: z.string(),
  company: z.string(),
  exchange: z.string().nullable().optional(),
  filing_date: z.string().nullable().optional(),
  offer_date: z.string().nullable().optional(),
  price_from: z.number().nullable().optional(),
  price_to: z.number().nullable().optional(),
  price: z.number().nullable().optional(),
  currency: z.string().nullable().optional(),
  shares: z.number().nullable().optional(),
  action: z.string().nullable().optional(),
});

export type IpoCalendarRow = z.infer<typeof ipoCalendarRowSchema>;

export const economicCalendarRowSchema = z.object({
  market: z.string(),
  event: z.string(),
  region: z.string().nullable().optional(),
  event_time: z.string(),
  for_period: z.string().nullable().optional(),
  actual: z.number().nullable().optional(),
  expected: z.number().nullable().optional(),
  last: z.number().nullable().optional(),
  revised: z.number().nullable().optional(),
});

export type EconomicCalendarRow = z.infer<typeof economicCalendarRowSchema>;

export const calendarEventsFeedResponseSchema = z.object({
  currentness_caveat: z.string(),
  earnings: z.array(earningsCalendarRowSchema).default([]),
  ipo: z.array(ipoCalendarRowSchema).default([]),
  economic: z.array(economicCalendarRowSchema).default([]),
});

export type CalendarEventsFeedResponse = z.infer<typeof calendarEventsFeedResponseSchema>;
