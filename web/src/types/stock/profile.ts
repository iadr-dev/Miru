/**
 * Stock profile — `/market/material-announcements`, `/stock/company-profile`, `/stock/esg-score`,
 * `/symbols/{symbol}/themes`, `/symbols/{symbol}/supply-chain`, `/themes/{key}/tier-heatmap`,
 * `/stocks/{symbol}/peers` (`backend/src/api/routers/market_rest.py`). Row field
 * names mirror `backend/src/schemas/capabilities.py`.
 */

import { z } from "zod";
import { healthStatusSchema } from "@/types/market/shared";
import { dividendEventSchema } from "@/types/stock/dividends";

// ---------------------------------------------------------------------------
// 0. GET /market/material-announcements — get_material_announcements (public, per-symbol).
//    Lives with the stock contracts (not `types/market/`) since the stock page's 基本資料 tab
//    is its consumer.
// ---------------------------------------------------------------------------

export const materialAnnouncementSchema = z.object({
  symbol: z.string(),
  market: z.string(),
  company_name: z.string(),
  announced_date: z.string(),
  announced_time: z.string(),
  subject: z.string(),
  detail: z.string(),
  qualifying_clause: z.string().nullable().optional(),
});

export type MaterialAnnouncement = z.infer<typeof materialAnnouncementSchema>;

export const stockMaterialAnnouncementsResponseSchema = z.object({
  source_id: z.string(),
  currentness_caveat: z.string(),
  health_status: healthStatusSchema,
  announcements: z.array(materialAnnouncementSchema).default([]),
});

export type StockMaterialAnnouncementsResponse = z.infer<
  typeof stockMaterialAnnouncementsResponseSchema
>;

export const companyProfileSchema = z.object({
  symbol: z.string(),
  market: z.string(),
  sector: z.string().nullable().optional(),
  industry: z.string().nullable().optional(),
  latest_dividend: dividendEventSchema.nullable().optional(),
  market_value: z.number().nullable().optional(),
  per: z.number().nullable().optional(),
  dividend_yield: z.number().nullable().optional(),
  eps: z.number().nullable().optional(),
  chairman: z.string().nullable().optional(),
  auditor: z.string().nullable().optional(),
  headquarters: z.string().nullable().optional(),
  website: z.string().nullable().optional(),
  capital: z.number().nullable().optional(),
  spokesperson: z.string().nullable().optional(),
  founding_date: z.string().nullable().optional(),
  industry_classification_code: z.string().nullable().optional(),
  revenue_yoy: z.number().nullable().optional(),
  margins: z.number().nullable().optional(),
  sector_caveat: z.string(),
  dividend_caveat: z.string(),
  missing_fields: z.array(z.string()).default([]),
});

export type CompanyProfile = z.infer<typeof companyProfileSchema>;

// ---------------------------------------------------------------------------
// 4. GET /stock/esg-score — get_esg_score (public, coverage-gated)
// ---------------------------------------------------------------------------

export const esgScoreSchema = z.object({
  symbol: z.string(),
  market: z.string(),
  as_of: z.string(),
  total_esg_score: z.number().nullable().optional(),
  esg_risk_percentile: z.number().nullable().optional(),
});

export type EsgScoreValue = z.infer<typeof esgScoreSchema>;

export const esgScoreResponseSchema = z.object({
  source_id: z.string(),
  currentness_caveat: z.string(),
  health_status: healthStatusSchema,
  esg: esgScoreSchema.nullable().optional(),
});

export type EsgScoreResponse = z.infer<typeof esgScoreResponseSchema>;

// ---------------------------------------------------------------------------
// 18. GET /symbols/{symbol}/themes — get_symbol_themes (public). Already wired in
//     backend/src/api/routers/themes.py before this session — kept here since it's consumed by
//     the stock-detail page's 基本資料 tab (概念股 chips).
// ---------------------------------------------------------------------------

export const symbolThemeSchema = z.object({
  theme_key: z.string(),
  name_zh: z.string(),
  kind: z.string(),
  sub_industry: z.string().default(""),
  confidence: z.number().default(1),
});

export type SymbolTheme = z.infer<typeof symbolThemeSchema>;

export const symbolThemesResponseSchema = z.object({
  symbol: z.string(),
  market: z.string(),
  theme_count: z.number().default(0),
  themes: z.array(symbolThemeSchema).default([]),
});

export type SymbolThemesResponse = z.infer<typeof symbolThemesResponseSchema>;

// ---------------------------------------------------------------------------
// GET /symbols/{symbol}/supply-chain — get_supply_chain_edges (public). Mirrors
// `SupplyChainEdge`/`GetSupplyChainEdgesOutput` (backend/src/tools/theme_tools.py) field-for-
// field. 產業分析 tab's 供應鏈關係圖 card — every supply/customer/competitor edge involving one
// symbol, from either side; `from_symbol`/`to_symbol` disambiguate direction per edge.
// ---------------------------------------------------------------------------

export const EDGE_TYPES = ["supplies", "customer", "competes"] as const;

export const edgeTypeSchema = z.enum(EDGE_TYPES);

export type EdgeType = z.infer<typeof edgeTypeSchema>;

export const EDGE_CONFIDENCES = ["high", "medium", "low"] as const;

export const edgeConfidenceSchema = z.enum(EDGE_CONFIDENCES);

export type EdgeConfidence = z.infer<typeof edgeConfidenceSchema>;

export const supplyChainEdgeSchema = z.object({
  from_symbol: z.string(),
  to_symbol: z.string(),
  market: z.string(),
  edge_type: edgeTypeSchema,
  confidence: edgeConfidenceSchema,
  revenue_pct: z.number().nullable().optional(),
  source: z.enum(["seed", "llm_extraction"]),
  from_name: z.string(),
  to_name: z.string(),
});

export type SupplyChainEdge = z.infer<typeof supplyChainEdgeSchema>;

export const supplyChainEdgesResponseSchema = z.object({
  symbol: z.string(),
  market: z.string(),
  edge_count: z.number().default(0),
  edges: z.array(supplyChainEdgeSchema).default([]),
});

export type SupplyChainEdgesResponse = z.infer<typeof supplyChainEdgesResponseSchema>;

// ---------------------------------------------------------------------------
// GET /themes/{key}/tier-heatmap — get_theme_tier_heatmap (public). Mirrors
// `SubIndustryHeat`/`GetThemeTierHeatmapOutput` (backend/src/tools/theme_tools.py) field-for-
// field. 產業分析 tab's 產業鏈 tier-tile view — a theme's upstream/midstream/downstream sub-
// industry tiles, each colored by that sub-industry's own members' real average price change.
// A `null` `pct_change` means no member had a real close on both days (never a fabricated 0%).
// ---------------------------------------------------------------------------

export const subIndustryHeatSchema = z.object({
  sub_industry: z.string(),
  tier: z.enum(["upstream", "midstream", "downstream"]),
  tier_source: z.enum(["curated", "heuristic"]),
  pct_change: z.number().nullable().optional(),
  member_count: z.number().default(0),
  priced_member_count: z.number().default(0),
});

export type SubIndustryHeat = z.infer<typeof subIndustryHeatSchema>;

export const themeTierHeatmapResponseSchema = z.object({
  theme_key: z.string(),
  found: z.boolean(),
  trade_date: z.string().nullable().optional(),
  prior_trade_date: z.string().nullable().optional(),
  data_available: z.boolean().default(true),
  unavailable_reason: z.string().nullable().optional(),
  upstream: z.array(subIndustryHeatSchema).default([]),
  midstream: z.array(subIndustryHeatSchema).default([]),
  downstream: z.array(subIndustryHeatSchema).default([]),
  message: z.string().default(""),
});

export type ThemeTierHeatmapResponse = z.infer<typeof themeTierHeatmapResponseSchema>;

// ---------------------------------------------------------------------------
// GET /stocks/{symbol}/peers — get_stock_peers (public). Mirrors `GetStockPeersOutput`
// (backend/src/tools/analyst_tools.py) field-for-field.
// ---------------------------------------------------------------------------

export const sectorOverviewCompanyRowSchema = z.object({
  market: z.string(),
  sector_key: z.string(),
  row_kind: z.string(),
  symbol: z.string(),
  name: z.string(),
  rating: z.string().nullable().optional(),
  market_weight: z.number().nullable().optional(),
});

export type SectorOverviewCompanyRow = z.infer<typeof sectorOverviewCompanyRowSchema>;

// `ytd_return`/`last_price`/`target_price`/`growth_estimate` come from `yfinance.Industry.
// top_performing_companies`/`top_growth_companies` — only populated on a `row_kind=
// "performing_company"`/`"growth_company"` row respectively; a plain `row_kind="company"` row
// (from `top_companies`) never carries any of the four.
export const industryOverviewCompanyRowSchema = z.object({
  market: z.string(),
  industry_key: z.string(),
  sector_key: z.string(),
  row_kind: z.string(),
  symbol: z.string(),
  name: z.string(),
  rating: z.string().nullable().optional(),
  market_weight: z.number().nullable().optional(),
  ytd_return: z.number().nullable().optional(),
  last_price: z.number().nullable().optional(),
  target_price: z.number().nullable().optional(),
  growth_estimate: z.number().nullable().optional(),
});

export type IndustryOverviewCompanyRow = z.infer<typeof industryOverviewCompanyRowSchema>;

// Mirrors `SectorOverviewStats` (backend/src/schemas/capabilities.py) — the ONE aggregate-level
// summary for a whole sector (market cap/companies/employees), not a per-company row.
export const sectorOverviewStatsSchema = z.object({
  market: z.string(),
  sector_key: z.string(),
  companies_count: z.number().nullable().optional(),
  market_cap: z.number().nullable().optional(),
  market_weight: z.number().nullable().optional(),
  employee_count: z.number().nullable().optional(),
  description: z.string().nullable().optional(),
});

export type SectorOverviewStats = z.infer<typeof sectorOverviewStatsSchema>;

// Mirrors `SectorIndustryWeightRow` — one row of this sector's own industry breakdown by market
// weight (`yfinance.Sector.industries`). The data source for the sector-industries treemap.
export const sectorIndustryWeightRowSchema = z.object({
  market: z.string(),
  sector_key: z.string(),
  industry_key: z.string(),
  name: z.string(),
  market_weight: z.number(),
});

export type SectorIndustryWeightRow = z.infer<typeof sectorIndustryWeightRowSchema>;

export const stockPeersResponseSchema = z.object({
  source_id: z.string(),
  currentness_caveat: z.string(),
  health_status: healthStatusSchema,
  sector: z.string().nullable().optional(),
  industry: z.string().nullable().optional(),
  sector_peers: z.array(sectorOverviewCompanyRowSchema).default([]),
  industry_peers: z.array(industryOverviewCompanyRowSchema).default([]),
  stats: sectorOverviewStatsSchema.nullable().optional(),
  industries: z.array(sectorIndustryWeightRowSchema).default([]),
  unavailable_reason: z.string().nullable().optional(),
});

export type StockPeersResponse = z.infer<typeof stockPeersResponseSchema>;
