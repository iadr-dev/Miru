/**
 * News and community — `/market/news-feed`, `/market/ptt-board`.
 */

import { z } from "zod";
import { healthStatusSchema } from "@/types/market/shared";

// ---------------------------------------------------------------------------
// 26. GET /market/news-feed — get_news_feed (public). Mirrors
//     `NewsFeedItemOut`/`GetNewsFeedOutput` (backend/src/tools/news_tools.py) field-for-field.
// ---------------------------------------------------------------------------

export const newsFeedItemSchema = z.object({
  title: z.string(),
  link: z.string(),
  source: z.string(),
  published_at: z.string(),
  summary: z.string().nullable().optional(),
});

export type NewsFeedItem = z.infer<typeof newsFeedItemSchema>;

export const newsFeedResponseSchema = z.object({
  currentness_caveat: z.string(),
  items: z.array(newsFeedItemSchema).default([]),
});

export type NewsFeedResponse = z.infer<typeof newsFeedResponseSchema>;

// ---------------------------------------------------------------------------
// 27. GET /market/ptt-board — get_ptt_board (public). Mirrors
//     `PttPostOut`/`GetPttBoardOutput` (backend/src/tools/ptt_tools.py) field-for-field.
// ---------------------------------------------------------------------------

// Mirrors `tools.ptt_tools.PTT_BOARDS` exactly — the 5 boards this tool can fetch.
export const PTT_BOARDS = ["Stock", "Tech_Job", "Warrant", "Beauty", "Gossiping"] as const;

export const pttBoardNameSchema = z.enum(PTT_BOARDS);

export type PttBoardName = z.infer<typeof pttBoardNameSchema>;

export const pttPostSchema = z.object({
  board: z.string(),
  board_tag: z.string(),
  title: z.string(),
  post_tag: z.string().nullable().optional(),
  author: z.string(),
  push_count: z.string(),
  link: z.string(),
  posted_date: z.string().nullable().optional(),
});

export type PttPost = z.infer<typeof pttPostSchema>;

export const pttBoardResponseSchema = z.object({
  source_id: z.string(),
  currentness_caveat: z.string(),
  health_status: healthStatusSchema,
  posts: z.array(pttPostSchema).default([]),
});

export type PttBoardResponse = z.infer<typeof pttBoardResponseSchema>;
