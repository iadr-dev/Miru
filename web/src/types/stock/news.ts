/**
 * News and filings — `/stock/finmind-news`, `/stock/ptt-search`, `/stocks/{symbol}/sec-filings`.
 */

import { z } from "zod";
import { healthStatusSchema } from "@/types/market/shared";

// ---------------------------------------------------------------------------
// 16. GET /stock/finmind-news — get_finmind_stock_news (public, per-symbol 財經新聞)
// ---------------------------------------------------------------------------

export const stockNewsRowSchema = z.object({
  symbol: z.string(),
  market: z.string(),
  trade_date: z.string(),
  description: z.string(),
  link: z.string(),
  source: z.string(),
  title: z.string(),
});

export type StockNewsRow = z.infer<typeof stockNewsRowSchema>;

export const finmindStockNewsResponseSchema = z.object({
  source_id: z.string(),
  currentness_caveat: z.string(),
  health_status: healthStatusSchema,
  rows: z.array(stockNewsRowSchema).default([]),
});

export type FinmindStockNewsResponse = z.infer<typeof finmindStockNewsResponseSchema>;

// ---------------------------------------------------------------------------
// 17. GET /stock/ptt-search — get_ptt_stock_search (public). Best-effort per-symbol PTT
//     search — matches symbol id/company name against each candidate post's BODY text (never
//     just the title), across Stock/Tech_Job/Warrant boards, over a caller-chosen lookback
//     window. `posted_date` mirrors `PttPostOut`/`sources/ptt.py::PttPost` field-for-field.
// ---------------------------------------------------------------------------

export const pttStockSearchPostSchema = z.object({
  board: z.string(),
  board_tag: z.string(),
  title: z.string(),
  post_tag: z.string().nullable().optional(),
  author: z.string(),
  push_count: z.string(),
  link: z.string(),
  posted_date: z.string().nullable().optional(),
});

export type PttStockSearchPost = z.infer<typeof pttStockSearchPostSchema>;

export const pttStockSearchResponseSchema = z.object({
  source_id: z.string(),
  currentness_caveat: z.string(),
  health_status: healthStatusSchema,
  posts: z.array(pttStockSearchPostSchema).default([]),
});

export type PttStockSearchResponse = z.infer<typeof pttStockSearchResponseSchema>;

// ---------------------------------------------------------------------------
// GET /stocks/{symbol}/sec-filings — get_sec_filings (public, US-only). Mirrors `SecFiling`/
// `GetSecFilingsOutput` (backend/src/tools/analyst_tools.py) field-for-field.
// ---------------------------------------------------------------------------

export const secFilingSchema = z.object({
  symbol: z.string(),
  market: z.string(),
  filing_date: z.string(),
  filing_type: z.string(),
  title: z.string(),
  edgar_url: z.string().nullable().optional(),
});

export type SecFiling = z.infer<typeof secFilingSchema>;

export const secFilingsResponseSchema = z.object({
  source_id: z.string(),
  currentness_caveat: z.string(),
  health_status: healthStatusSchema,
  filings: z.array(secFilingSchema).default([]),
});

export type SecFilingsResponse = z.infer<typeof secFilingsResponseSchema>;
