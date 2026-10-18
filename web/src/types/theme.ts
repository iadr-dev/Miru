/**
 * Theme contracts — mirrors `backend/src/api/routers/themes.py` (`get_themes`/`get_theme_members`).
 * `kind` distinguishes an editorially-sourced industry-chain theme from an AI-researched concept
 * one — never presented identically (`confidence` on a member row is the tell).
 */
import { z } from "zod";

export const THEME_KINDS = ["industry", "concept"] as const;
export const themeKindSchema = z.enum(THEME_KINDS);
export type ThemeKind = z.infer<typeof themeKindSchema>;

/** Where a member sits in a concept theme's chain. `related` = in the theme but not placed on it. */
export const THEME_POSITIONS = ["upstream", "midstream", "downstream", "related"] as const;
export const themePositionSchema = z.enum(THEME_POSITIONS);
export type ThemePosition = z.infer<typeof themePositionSchema>;

export const themeSummarySchema = z.object({
  key: z.string(),
  name_zh: z.string(),
  name_en: z.string().default(""),
  kind: themeKindSchema,
  status: z.string(),
  member_count: z.number(),
  /** Additive: a short plain-language description; absent on older payloads. */
  description: z.string().default(""),
  /** Additive: keys of related themes; absent on older payloads. */
  related_keys: z.array(z.string()).default([]),
});
export type ThemeSummary = z.infer<typeof themeSummarySchema>;

export const themesResponseSchema = z.object({
  theme_count: z.number(),
  themes: z.array(themeSummarySchema).default([]),
  /** Additive: true when some concept themes were withheld for the caller's plan. */
  concept_locked: z.boolean().default(false),
});
export type ThemesResponse = z.infer<typeof themesResponseSchema>;

export const themeMemberSchema = z.object({
  symbol: z.string(),
  market: z.string(),
  sub_industry: z.string().default(""),
  confidence: z.number(),
  source: z.string(),
  /** Additive: chain position within a concept theme; null/absent when unplaced. */
  position: themePositionSchema.nullable().default(null),
});
export type ThemeMember = z.infer<typeof themeMemberSchema>;

export const themeMembersResponseSchema = z.object({
  theme_key: z.string(),
  found: z.boolean(),
  name_zh: z.string().default(""),
  name_en: z.string().default(""),
  kind: themeKindSchema.nullable(),
  member_count: z.number(),
  description: z.string().default(""),
  related_keys: z.array(z.string()).default([]),
  members: z.array(themeMemberSchema).default([]),
  message: z.string().default(""),
  /** Additive: true when the server withheld part of this answer for the caller's plan. */
  locked: z.boolean().default(false),
  /** Additive: how many items were withheld (0 when nothing was). */
  hidden_count: z.number().default(0),
});
export type ThemeMembersResponse = z.infer<typeof themeMembersResponseSchema>;
