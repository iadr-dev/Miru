/**
 * Theme contracts — mirrors `backend/src/api/routers/themes.py` (`get_themes`/`get_theme_members`).
 * `kind` distinguishes an editorially-sourced industry-chain theme from an AI-researched concept
 * one — never presented identically (`confidence` on a member row is the tell).
 */
import { z } from "zod";

export const THEME_KINDS = ["industry", "concept"] as const;
export const themeKindSchema = z.enum(THEME_KINDS);
export type ThemeKind = z.infer<typeof themeKindSchema>;

export const themeSummarySchema = z.object({
  key: z.string(),
  name_zh: z.string(),
  name_en: z.string().default(""),
  kind: themeKindSchema,
  status: z.string(),
  member_count: z.number(),
});
export type ThemeSummary = z.infer<typeof themeSummarySchema>;

export const themesResponseSchema = z.object({
  theme_count: z.number(),
  themes: z.array(themeSummarySchema).default([]),
});
export type ThemesResponse = z.infer<typeof themesResponseSchema>;

export const themeMemberSchema = z.object({
  symbol: z.string(),
  market: z.string(),
  sub_industry: z.string().default(""),
  confidence: z.number(),
  source: z.string(),
});
export type ThemeMember = z.infer<typeof themeMemberSchema>;

export const themeMembersResponseSchema = z.object({
  theme_key: z.string(),
  found: z.boolean(),
  name_zh: z.string().default(""),
  name_en: z.string().default(""),
  kind: themeKindSchema.nullable(),
  member_count: z.number(),
  members: z.array(themeMemberSchema).default([]),
  message: z.string().default(""),
});
export type ThemeMembersResponse = z.infer<typeof themeMembersResponseSchema>;
