/**
 * Compile-time contract checks: each line says "the response the backend documents for this
 * operation (web/openapi.json) is accepted by the Zod schema that parses it". A backend change that
 * renames, removes or retypes a field the frontend reads turns into a `tsc` error on that line,
 * naming the field. Type-only — never imported, nothing ships.
 *
 * Adding an API client: add its line here. Refreshing after a backend change:
 * `UPDATE_OPENAPI_CONTRACT=1 uv run pytest tests/api/test_openapi_contract.py` (backend/), then
 * `pnpm gen:api` (web/).
 */

import type { z } from "zod";
import type { beginAuthResponseSchema } from "@/lib/api/schemas";
import type {
  channelStatusSchema,
  lineLinkSchema,
  markReadResponseSchema,
  symbolCheckSchema,
  telegramLinkSchema,
} from "@/lib/api/settings";
import type { feedbackRecordSchema } from "@/lib/api/threads";
import type { speakResponseSchema, transcriptSchema } from "@/lib/api/voice";
import type { entitlementViewSchema, meResponseSchema } from "@/types/account";
import type { threadListResponseSchema, threadSchema } from "@/types/conversation";
import type { entityGraphResponseSchema } from "@/types/entity-graph";
import type {
  activeEtfReportResponseSchema,
  etfExpenseRatioResponseSchema,
  etfFundInfoResponseSchema,
  etfPremiumDiscountResponseSchema,
  etfThemeTagsResponseSchema,
  etfTrailingReturnsResponseSchema,
  fundComparisonResponseSchema,
  fundHoldingsResponseSchema,
  fundProfileResponseSchema,
} from "@/types/etf-detail";
import type { Accepts, ResponseOf as R } from "@/types/generated/contract";
import type { livePriceQuoteResponseSchema } from "@/types/live-quote";
import type {
  advanceDeclineResponseSchema,
  indexContributionResponseSchema,
} from "@/types/market/breadth";
import type {
  futuresSnapshotResponseSchema,
  optionVixResponseSchema,
} from "@/types/market/derivatives";
import type {
  dispositionBoardResponseSchema,
  insiderTransferDeclarationResponseSchema,
  materialAnnouncementFeedResponseSchema,
} from "@/types/market/disclosures";
import type { dividendCalendarResponseSchema, etfListResponseSchema } from "@/types/market/etf";
import type {
  etfMoneyFlowResponseSchema,
  industryFlowResponseSchema,
  institutionalRankingResponseSchema,
  marginMaintenanceResponseSchema,
  marginShortBalanceResponseSchema,
} from "@/types/market/flows";
import type {
  macroCalendarResponseSchema,
  macroIndicatorsResponseSchema,
} from "@/types/market/macro";
import type { newsFeedResponseSchema, pttBoardResponseSchema } from "@/types/market/news";
import type {
  marketIndexStripResponseSchema,
  priceHistoryResponseSchema,
  symbolInfoResponseSchema,
  symbolQuotesResponseSchema,
  tickSnapshotResponseSchema,
} from "@/types/market/quotes";
import type {
  dailySignalResponseSchema,
  holderAccumulationResponseSchema,
  scoreLeaderboardDataSchema,
  tagScreenerResponseSchema,
  twMarketRankingsResponseSchema,
  usMarketRankingsResponseSchema,
} from "@/types/market/rankings";
import type {
  sectorConstituentsResponseSchema,
  sectorHeatmapResponseSchema,
  sectorRotationRrgResponseSchema,
  stockHeatmapResponseSchema,
} from "@/types/market/sectors";
import type {
  usEconomicReleasesResponseSchema,
  usMacroDashboardResponseSchema,
  usMacroSeriesResponseSchema,
  usSeriesRevisionsResponseSchema,
} from "@/types/market/us-macro";
import type { consentDecisionResponseSchema, consentResponseSchema } from "@/types/oauth-consent";
import type {
  factorCatalogResponseSchema,
  runScreenerResponseSchema,
  screenerPresetsResponseSchema,
  screenerScreenSchema,
  screenerScreensResponseSchema,
} from "@/types/screener";
import type {
  activePetResponseSchema,
  alertPreferenceSchema,
  alertRuleSchema,
  alertsResponseSchema,
  billingHistoryResponseSchema,
  checkoutResponseSchema,
  connectionsResponseSchema,
  devicePushTokenSchema,
  invoiceResponseSchema,
  memoriesResponseSchema,
  memorySchema,
  memorySettingsSchema,
  notificationBadgeSchema,
  notificationsResponseSchema,
  ownedPetSchema,
  ownedPetsResponseSchema,
  personalityPresetsResponseSchema,
  petCatalogResponseSchema,
  petProgressSchema,
  petSettingsSchema,
  plansResponseSchema,
  portalResponseSchema,
  pullOutcomeSchema,
  scheduleJobSchema,
  schedulesResponseSchema,
  subscriptionResponseSchema,
} from "@/types/settings";
import type {
  stockScoreResponseSchema,
  stockSummaryResponseSchema,
  trendScoreResponseSchema,
} from "@/types/stock/ai";
import type { chainPositionsResponseSchema } from "@/types/stock/chain-position";
import type {
  chipFlowsResponseSchema,
  dayTradingResponseSchema,
  governmentBankFlowsResponseSchema,
  institutionalFlowResponseSchema,
  marginShortResponseSchema,
  securitiesLendingResponseSchema,
} from "@/types/stock/chip";
import type { dividendPolicySummaryResponseSchema } from "@/types/stock/dividends";
import type {
  calendarEventsFeedResponseSchema,
  stockCalendarEventsResponseSchema,
} from "@/types/stock/events";
import type {
  analystConsensusResponseSchema,
  financialStatementResponseSchema,
  monthRevenueResponseSchema,
  valuationRatiosResponseSchema,
} from "@/types/stock/fundamentals";
import type {
  finmindStockNewsResponseSchema,
  pttStockSearchResponseSchema,
  secFilingsResponseSchema,
} from "@/types/stock/news";
import type {
  etfsHoldingStockResponseSchema,
  foreignShareholdingResponseSchema,
  insiderShareholdingResponseSchema,
  shareholderDispersionResponseSchema,
  stockInsiderTransferDeclarationResponseSchema,
} from "@/types/stock/ownership";
import type {
  companyProfileSchema,
  esgScoreResponseSchema,
  stockMaterialAnnouncementsResponseSchema,
  stockPeersResponseSchema,
  supplyChainEdgesResponseSchema,
  symbolThemesResponseSchema,
  themeTierHeatmapResponseSchema,
} from "@/types/stock/profile";
import type {
  intradayKbarResponseSchema,
  liveQuoteResponseSchema,
  priceLimitResponseSchema,
  priceTickResponseSchema,
} from "@/types/stock/realtime";
import type { technicalIndicatorsResponseSchema } from "@/types/stock/technical";
import type {
  strategySubscriptionOrNullSchema,
  strategySubscriptionSchema,
  strategyWatchTodaySchema,
} from "@/types/strategy-watch";
import type {
  twSymbolSearchResponseSchema,
  usSymbolSearchResponseSchema,
} from "@/types/symbol-search";
import type { themeMembersResponseSchema, themesResponseSchema } from "@/types/theme";
import type { llmUsageSummarySchema, todayUsageSchema } from "@/types/usage";
import type {
  listWatchlistsResponseSchema,
  watchlistGroupSchema,
  watchlistItemSchema,
} from "@/types/watchlist";

export type ContractChecks = [
  // GET /alerts — settings.listAlerts
  Accepts<typeof alertsResponseSchema, R<"/api/v1/alerts", "get">>,
  // POST /alerts — settings.createAlert
  Accepts<typeof alertRuleSchema, R<"/api/v1/alerts", "post">>,
  // PATCH /alerts/{alert_id} — settings.updateAlert
  Accepts<typeof alertRuleSchema, R<"/api/v1/alerts/{alert_id}", "patch">>,
  // POST /alerts/batch — settings.createAlertsBatch
  Accepts<typeof alertsResponseSchema, R<"/api/v1/alerts/batch", "post">>,
  // GET /alerts/symbol-check — settings.checkAlertSymbol
  Accepts<typeof symbolCheckSchema, R<"/api/v1/alerts/symbol-check", "get">>,
  // GET /analytics/{symbol}/score — stock-detail.getStockScore
  Accepts<typeof stockScoreResponseSchema, R<"/api/v1/analytics/{symbol}/score", "get">>,
  // GET /analytics/{symbol}/summary — stock-detail.getStockSummary
  Accepts<typeof stockSummaryResponseSchema, R<"/api/v1/analytics/{symbol}/summary", "get">>,
  // GET /analytics/{symbol}/trend-score — stock-detail.getTrendScore
  Accepts<typeof trendScoreResponseSchema, R<"/api/v1/analytics/{symbol}/trend-score", "get">>,
  // GET /analytics/rankings — market.getScoreLeaderboard
  Accepts<typeof scoreLeaderboardDataSchema, R<"/api/v1/analytics/rankings", "get">>,
  // GET /analytics/screener/factors — screener.getFactorCatalog
  Accepts<typeof factorCatalogResponseSchema, R<"/api/v1/analytics/screener/factors", "get">>,
  // GET /analytics/screener/presets — screener.getScreenerPresets
  Accepts<typeof screenerPresetsResponseSchema, R<"/api/v1/analytics/screener/presets", "get">>,
  // POST /analytics/screener/run — screener.runScreener
  Accepts<typeof runScreenerResponseSchema, R<"/api/v1/analytics/screener/run", "post">>,
  // GET /analytics/screener/screens — screener.listScreenerScreens
  Accepts<typeof screenerScreensResponseSchema, R<"/api/v1/analytics/screener/screens", "get">>,
  // POST /analytics/screener/screens — screener.saveScreenerScreen
  Accepts<typeof screenerScreenSchema, R<"/api/v1/analytics/screener/screens", "post">>,
  // POST /auth/oauth/{provider} — auth.beginOAuth
  Accepts<typeof beginAuthResponseSchema, R<"/api/v1/auth/oauth/{provider}", "post">>,
  // POST /auth/oauth/{provider}/link — auth.beginOAuthLink
  Accepts<typeof beginAuthResponseSchema, R<"/api/v1/auth/oauth/{provider}/link", "post">>,
  // POST /billing/change-plan — settings.changePlan
  Accepts<typeof subscriptionResponseSchema, R<"/api/v1/billing/change-plan", "post">>,
  // POST /billing/checkout — settings.checkout
  Accepts<typeof checkoutResponseSchema, R<"/api/v1/billing/checkout", "post">>,
  // GET /billing/history — settings.getBillingHistory
  Accepts<typeof billingHistoryResponseSchema, R<"/api/v1/billing/history", "get">>,
  // GET /billing/invoice/{transaction_id} — settings.getInvoiceUrl
  Accepts<typeof invoiceResponseSchema, R<"/api/v1/billing/invoice/{transaction_id}", "get">>,
  // GET /billing/plans — settings.listPlans
  Accepts<typeof plansResponseSchema, R<"/api/v1/billing/plans", "get">>,
  // POST /billing/portal — settings.openBillingPortal
  Accepts<typeof portalResponseSchema, R<"/api/v1/billing/portal", "post">>,
  // POST /billing/resume — settings.resumeSubscription
  Accepts<typeof subscriptionResponseSchema, R<"/api/v1/billing/resume", "post">>,
  // GET /billing/subscription — settings.getSubscription
  Accepts<typeof subscriptionResponseSchema, R<"/api/v1/billing/subscription", "get">>,
  // POST /billing/sync — settings.syncCheckout
  Accepts<typeof subscriptionResponseSchema, R<"/api/v1/billing/sync", "post">>,
  // GET /derivatives/taiex-vix — market.getTaiexVix
  Accepts<typeof optionVixResponseSchema, R<"/api/v1/derivatives/taiex-vix", "get">>,
  // GET /funds/{symbol}/expense-ratio — etf-detail.getEtfExpenseRatio
  Accepts<typeof etfExpenseRatioResponseSchema, R<"/api/v1/funds/{symbol}/expense-ratio", "get">>,
  // GET /funds/{symbol}/fund-info — etf-detail.getEtfFundInfo
  Accepts<typeof etfFundInfoResponseSchema, R<"/api/v1/funds/{symbol}/fund-info", "get">>,
  // GET /funds/{symbol}/holdings — etf-detail.getFundHoldings
  Accepts<typeof fundHoldingsResponseSchema, R<"/api/v1/funds/{symbol}/holdings", "get">>,
  // GET /funds/{symbol}/premium-discount — etf-detail.getEtfPremiumDiscount
  Accepts<
    typeof etfPremiumDiscountResponseSchema,
    R<"/api/v1/funds/{symbol}/premium-discount", "get">
  >,
  // GET /funds/{symbol}/profile — etf-detail.getFundProfile
  Accepts<typeof fundProfileResponseSchema, R<"/api/v1/funds/{symbol}/profile", "get">>,
  // GET /funds/{symbol}/theme-tags — etf-detail.getEtfThemeTags
  Accepts<typeof etfThemeTagsResponseSchema, R<"/api/v1/funds/{symbol}/theme-tags", "get">>,
  // GET /funds/{symbol}/trailing-returns — etf-detail.getEtfTrailingReturns
  Accepts<
    typeof etfTrailingReturnsResponseSchema,
    R<"/api/v1/funds/{symbol}/trailing-returns", "get">
  >,
  // GET /funds/comparison — etf-detail.getFundTotalReturn
  Accepts<typeof fundComparisonResponseSchema, R<"/api/v1/funds/comparison", "get">>,
  // GET /macro — market.getMacroIndicators
  Accepts<typeof macroIndicatorsResponseSchema, R<"/api/v1/macro", "get">>,
  // GET /macro/us/dashboard — us-macro.getUsMacroDashboard
  Accepts<typeof usMacroDashboardResponseSchema, R<"/api/v1/macro/us/dashboard", "get">>,
  // GET /macro/us/releases — us-macro.getUsEconomicReleases
  Accepts<typeof usEconomicReleasesResponseSchema, R<"/api/v1/macro/us/releases", "get">>,
  // GET /macro/us/revisions — us-macro.getUsSeriesRevisions
  Accepts<typeof usSeriesRevisionsResponseSchema, R<"/api/v1/macro/us/revisions", "get">>,
  // GET /macro/us/series — us-macro.getUsMacroSeries
  Accepts<typeof usMacroSeriesResponseSchema, R<"/api/v1/macro/us/series", "get">>,
  // GET /market/active-etf-report — etf-detail.getActiveEtfReport
  Accepts<typeof activeEtfReportResponseSchema, R<"/api/v1/market/active-etf-report", "get">>,
  // GET /market/advance-decline — market.getAdvanceDecline
  Accepts<typeof advanceDeclineResponseSchema, R<"/api/v1/market/advance-decline", "get">>,
  // GET /market/daily-signal — market.getDailySignal
  Accepts<typeof dailySignalResponseSchema, R<"/api/v1/market/daily-signal", "get">>,
  // GET /market/disposition-board — market.getDispositionBoard
  Accepts<typeof dispositionBoardResponseSchema, R<"/api/v1/market/disposition-board", "get">>,
  // GET /market/dividend-calendar — market.getDividendCalendar
  Accepts<typeof dividendCalendarResponseSchema, R<"/api/v1/market/dividend-calendar", "get">>,
  // GET /market/etf-list — market.getEtfList
  Accepts<typeof etfListResponseSchema, R<"/api/v1/market/etf-list", "get">>,
  // GET /market/etf-money-flow — market.getEtfMoneyFlow
  Accepts<typeof etfMoneyFlowResponseSchema, R<"/api/v1/market/etf-money-flow", "get">>,
  // GET /market/futures-snapshot — market.getFuturesSnapshot
  Accepts<typeof futuresSnapshotResponseSchema, R<"/api/v1/market/futures-snapshot", "get">>,
  // GET /market/holder-accumulation — market.getHolderAccumulation
  Accepts<typeof holderAccumulationResponseSchema, R<"/api/v1/market/holder-accumulation", "get">>,
  // GET /market/index-contribution — market.getIndexContribution
  Accepts<typeof indexContributionResponseSchema, R<"/api/v1/market/index-contribution", "get">>,
  // GET /market/index-strip — market.getMarketIndexStrip
  Accepts<typeof marketIndexStripResponseSchema, R<"/api/v1/market/index-strip", "get">>,
  // GET /market/industry-flow — market.getIndustryFlow
  Accepts<typeof industryFlowResponseSchema, R<"/api/v1/market/industry-flow", "get">>,
  // GET /market/insider-transfer-declarations — market.getInsiderTransferDeclarations
  Accepts<
    typeof insiderTransferDeclarationResponseSchema,
    R<"/api/v1/market/insider-transfer-declarations", "get">
  >,
  // GET /market/institutional-ranking — market.getInstitutionalRanking
  Accepts<
    typeof institutionalRankingResponseSchema,
    R<"/api/v1/market/institutional-ranking", "get">
  >,
  // GET /market/macro-calendar — market.getMacroCalendar
  Accepts<typeof macroCalendarResponseSchema, R<"/api/v1/market/macro-calendar", "get">>,
  // GET /market/margin-maintenance — market.getMarginMaintenance
  Accepts<typeof marginMaintenanceResponseSchema, R<"/api/v1/market/margin-maintenance", "get">>,
  // GET /market/margin-short-balance — market.getMarginShortBalance
  Accepts<typeof marginShortBalanceResponseSchema, R<"/api/v1/market/margin-short-balance", "get">>,
  // GET /market/material-announcement-feed — market.getMaterialAnnouncementFeed
  Accepts<
    typeof materialAnnouncementFeedResponseSchema,
    R<"/api/v1/market/material-announcement-feed", "get">
  >,
  // GET /market/material-announcements — stock-detail.getStockMaterialAnnouncements
  Accepts<
    typeof stockMaterialAnnouncementsResponseSchema,
    R<"/api/v1/market/material-announcements", "get">
  >,
  // GET /market/news-feed — market.getNewsFeed
  Accepts<typeof newsFeedResponseSchema, R<"/api/v1/market/news-feed", "get">>,
  // GET /market/price-history — market.getPriceHistory
  Accepts<typeof priceHistoryResponseSchema, R<"/api/v1/market/price-history", "get">>,
  // GET /market/ptt-board — market.getPttBoard
  Accepts<typeof pttBoardResponseSchema, R<"/api/v1/market/ptt-board", "get">>,
  // GET /market/quotes — market.getSymbolQuotes
  Accepts<typeof symbolQuotesResponseSchema, R<"/api/v1/market/quotes", "get">>,
  // GET /market/sector-constituents — market.getSectorConstituents
  Accepts<typeof sectorConstituentsResponseSchema, R<"/api/v1/market/sector-constituents", "get">>,
  // GET /market/sector-heatmap — market.getSectorHeatmap
  Accepts<typeof sectorHeatmapResponseSchema, R<"/api/v1/market/sector-heatmap", "get">>,
  // GET /market/sector-rotation-rrg — market.getSectorRotationRrg
  Accepts<typeof sectorRotationRrgResponseSchema, R<"/api/v1/market/sector-rotation-rrg", "get">>,
  // GET /market/stock-heatmap — market.getStockHeatmap
  Accepts<typeof stockHeatmapResponseSchema, R<"/api/v1/market/stock-heatmap", "get">>,
  // GET /market/symbol-info — market.getSymbolInfo
  Accepts<typeof symbolInfoResponseSchema, R<"/api/v1/market/symbol-info", "get">>,
  // GET /market/tag-screener — market.getTagScreenerForSymbols
  Accepts<typeof tagScreenerResponseSchema, R<"/api/v1/market/tag-screener", "get">>,
  // GET /market/tick-snapshot — market.getTickSnapshot
  Accepts<typeof tickSnapshotResponseSchema, R<"/api/v1/market/tick-snapshot", "get">>,
  // GET /market/tw-rankings — market.getTwMarketRankings
  Accepts<typeof twMarketRankingsResponseSchema, R<"/api/v1/market/tw-rankings", "get">>,
  // GET /market/us-index-strip — market.getUsMarketIndexStrip
  Accepts<typeof marketIndexStripResponseSchema, R<"/api/v1/market/us-index-strip", "get">>,
  // GET /market/us-rankings — market.getUsMarketRankings
  Accepts<typeof usMarketRankingsResponseSchema, R<"/api/v1/market/us-rankings", "get">>,
  // GET /me — account.fetchMe
  Accepts<typeof meResponseSchema, R<"/api/v1/me", "get">>,
  // PATCH /me — settings.updateProfile
  Accepts<typeof meResponseSchema, R<"/api/v1/me", "patch">>,
  // GET /me/alert-preferences — settings.getAlertPreferences
  Accepts<typeof alertPreferenceSchema, R<"/api/v1/me/alert-preferences", "get">>,
  // PUT /me/alert-preferences — settings.putAlertPreferences
  Accepts<typeof alertPreferenceSchema, R<"/api/v1/me/alert-preferences", "put">>,
  // GET /me/channels — settings.getChannelStatus
  Accepts<typeof channelStatusSchema, R<"/api/v1/me/channels", "get">>,
  // POST /me/channels/line/link — settings.startLineLink
  Accepts<typeof lineLinkSchema, R<"/api/v1/me/channels/line/link", "post">>,
  // POST /me/channels/telegram/link — settings.startTelegramLink
  Accepts<typeof telegramLinkSchema, R<"/api/v1/me/channels/telegram/link", "post">>,
  // GET /me/connections — settings.listConnections
  Accepts<typeof connectionsResponseSchema, R<"/api/v1/me/connections", "get">>,
  // POST /me/devices — settings.registerDevice
  Accepts<typeof devicePushTokenSchema, R<"/api/v1/me/devices", "post">>,
  // GET /memory — settings.listMemories
  Accepts<typeof memoriesResponseSchema, R<"/api/v1/memory", "get">>,
  // POST /memory — settings.createMemory
  Accepts<typeof memorySchema, R<"/api/v1/memory", "post">>,
  // PATCH /memory/{memory_id} — settings.updateMemory
  Accepts<typeof memorySchema, R<"/api/v1/memory/{memory_id}", "patch">>,
  // GET /memory/settings — settings.getMemorySettings
  Accepts<typeof memorySettingsSchema, R<"/api/v1/memory/settings", "get">>,
  // PUT /memory/settings — settings.setMemoryEnabled
  Accepts<typeof memorySettingsSchema, R<"/api/v1/memory/settings", "put">>,
  // GET /notifications — settings.listNotifications
  Accepts<typeof notificationsResponseSchema, R<"/api/v1/notifications", "get">>,
  // GET /notifications/badge — settings.getNotificationBadge
  Accepts<typeof notificationBadgeSchema, R<"/api/v1/notifications/badge", "get">>,
  // POST /notifications/read — settings.markNotificationsRead
  Accepts<typeof markReadResponseSchema, R<"/api/v1/notifications/read", "post">>,
  // GET /personality/presets — settings.listPersonalityPresets
  Accepts<typeof personalityPresetsResponseSchema, R<"/api/v1/personality/presets", "get">>,
  // GET /pets — settings.listOwnedPets
  Accepts<typeof ownedPetsResponseSchema, R<"/api/v1/pets", "get">>,
  // PUT /pets/{record_id}/equip — settings.equipPet
  Accepts<typeof ownedPetSchema, R<"/api/v1/pets/{record_id}/equip", "put">>,
  // GET /pets/active — settings.getActivePet
  Accepts<typeof activePetResponseSchema, R<"/api/v1/pets/active", "get">>,
  // GET /pets/catalog — settings.listPetCatalog
  Accepts<typeof petCatalogResponseSchema, R<"/api/v1/pets/catalog", "get">>,
  // GET /pets/progress — settings.getPetProgress
  Accepts<typeof petProgressSchema, R<"/api/v1/pets/progress", "get">>,
  // POST /pets/pull — settings.pullPet
  Accepts<typeof pullOutcomeSchema, R<"/api/v1/pets/pull", "post">>,
  // GET /pets/settings — settings.getPetSettings
  Accepts<typeof petSettingsSchema, R<"/api/v1/pets/settings", "get">>,
  // PATCH /pets/settings — settings.updatePetSettings
  Accepts<typeof petSettingsSchema, R<"/api/v1/pets/settings", "patch">>,
  // GET /strategy-watch/subscription — strategy-watch.getStrategySubscription
  Accepts<typeof strategySubscriptionOrNullSchema, R<"/api/v1/strategy-watch/subscription", "get">>,
  // PUT /strategy-watch/subscription — strategy-watch.putStrategySubscription
  Accepts<typeof strategySubscriptionSchema, R<"/api/v1/strategy-watch/subscription", "put">>,
  // GET /strategy-watch/today — strategy-watch.getStrategyWatchToday
  Accepts<typeof strategyWatchTodaySchema, R<"/api/v1/strategy-watch/today", "get">>,
  // GET /schedules — settings.listSchedules
  Accepts<typeof schedulesResponseSchema, R<"/api/v1/schedules", "get">>,
  // POST /schedules — settings.createSchedule
  Accepts<typeof scheduleJobSchema, R<"/api/v1/schedules", "post">>,
  // PATCH /schedules/{job_id} — settings.updateSchedule
  Accepts<typeof scheduleJobSchema, R<"/api/v1/schedules/{job_id}", "patch">>,
  // GET /search — search.searchTwSymbols, search.searchUsSymbols
  Accepts<
    z.ZodUnion<[typeof twSymbolSearchResponseSchema, typeof usSymbolSearchResponseSchema]>,
    R<"/api/v1/search", "get">
  >,
  // GET /stock/analyst-consensus — stock-detail.getAnalystConsensus
  Accepts<typeof analystConsensusResponseSchema, R<"/api/v1/stock/analyst-consensus", "get">>,
  // GET /stock/calendar-events — stock-detail.getStockCalendarEvents
  Accepts<typeof stockCalendarEventsResponseSchema, R<"/api/v1/stock/calendar-events", "get">>,
  // GET /stock/calendar-events-feed — stock-detail.getCalendarEventsFeed
  Accepts<typeof calendarEventsFeedResponseSchema, R<"/api/v1/stock/calendar-events-feed", "get">>,
  // GET /stock/chip-flows — stock-detail.getStockChipFlows
  Accepts<typeof chipFlowsResponseSchema, R<"/api/v1/stock/chip-flows", "get">>,
  // GET /stock/company-profile — stock-detail.getCompanyProfile
  Accepts<typeof companyProfileSchema, R<"/api/v1/stock/company-profile", "get">>,
  // GET /stock/day-trading — stock-detail.getStockDayTrading
  Accepts<typeof dayTradingResponseSchema, R<"/api/v1/stock/day-trading", "get">>,
  // GET /stock/dividend-policy-summary — stock-detail.getDividendPolicySummary
  Accepts<
    typeof dividendPolicySummaryResponseSchema,
    R<"/api/v1/stock/dividend-policy-summary", "get">
  >,
  // GET /stock/esg-score — stock-detail.getEsgScore
  Accepts<typeof esgScoreResponseSchema, R<"/api/v1/stock/esg-score", "get">>,
  // GET /stock/etfs-holding-stock — stock-detail.getEtfsHoldingStock
  Accepts<typeof etfsHoldingStockResponseSchema, R<"/api/v1/stock/etfs-holding-stock", "get">>,
  // GET /stock/financial-statement — stock-detail.getFinancialStatement
  Accepts<typeof financialStatementResponseSchema, R<"/api/v1/stock/financial-statement", "get">>,
  // GET /stock/finmind-news — stock-detail.getFinmindStockNews
  Accepts<typeof finmindStockNewsResponseSchema, R<"/api/v1/stock/finmind-news", "get">>,
  // GET /stock/foreign-shareholding — stock-detail.getForeignShareholding
  Accepts<typeof foreignShareholdingResponseSchema, R<"/api/v1/stock/foreign-shareholding", "get">>,
  // GET /stock/government-bank-flows — stock-detail.getGovernmentBankFlows
  Accepts<
    typeof governmentBankFlowsResponseSchema,
    R<"/api/v1/stock/government-bank-flows", "get">
  >,
  // GET /stock/insider-shareholding — stock-detail.getInsiderShareholding
  Accepts<typeof insiderShareholdingResponseSchema, R<"/api/v1/stock/insider-shareholding", "get">>,
  // GET /stock/insider-transfer-declarations — stock-detail.getStockInsiderTransferDeclarations
  Accepts<
    typeof stockInsiderTransferDeclarationResponseSchema,
    R<"/api/v1/stock/insider-transfer-declarations", "get">
  >,
  // GET /stock/institutional-flow — stock-detail.getStockInstitutionalFlow
  Accepts<typeof institutionalFlowResponseSchema, R<"/api/v1/stock/institutional-flow", "get">>,
  // GET /stock/intraday-kbar — stock-detail.getIntradayKbar
  Accepts<typeof intradayKbarResponseSchema, R<"/api/v1/stock/intraday-kbar", "get">>,
  // GET /stock/live-price-quote — stock-detail.getLivePriceQuote
  Accepts<typeof livePriceQuoteResponseSchema, R<"/api/v1/stock/live-price-quote", "get">>,
  // GET /stock/live-quote — stock-detail.getLiveQuote
  Accepts<typeof liveQuoteResponseSchema, R<"/api/v1/stock/live-quote", "get">>,
  // GET /stock/margin-short — stock-detail.getStockMarginShort
  Accepts<typeof marginShortResponseSchema, R<"/api/v1/stock/margin-short", "get">>,
  // GET /stock/month-revenue — stock-detail.getMonthRevenue
  Accepts<typeof monthRevenueResponseSchema, R<"/api/v1/stock/month-revenue", "get">>,
  // GET /stock/peers — stock-detail.getStockPeers
  Accepts<typeof stockPeersResponseSchema, R<"/api/v1/stock/peers", "get">>,
  // GET /stock/price-limit — stock-detail.getPriceLimit
  Accepts<typeof priceLimitResponseSchema, R<"/api/v1/stock/price-limit", "get">>,
  // GET /stock/price-tick — stock-detail.getPriceTick
  Accepts<typeof priceTickResponseSchema, R<"/api/v1/stock/price-tick", "get">>,
  // GET /stock/ptt-search — stock-detail.getPttStockSearch
  Accepts<typeof pttStockSearchResponseSchema, R<"/api/v1/stock/ptt-search", "get">>,
  // GET /stock/sec-filings — stock-detail.getSecFilings
  Accepts<typeof secFilingsResponseSchema, R<"/api/v1/stock/sec-filings", "get">>,
  // GET /stock/securities-lending — stock-detail.getSecuritiesLending
  Accepts<typeof securitiesLendingResponseSchema, R<"/api/v1/stock/securities-lending", "get">>,
  // GET /stock/shareholder-dispersion — stock-detail.getShareholderDispersion
  Accepts<
    typeof shareholderDispersionResponseSchema,
    R<"/api/v1/stock/shareholder-dispersion", "get">
  >,
  // GET /stock/technical-indicators — stock-detail.getTechnicalIndicators
  Accepts<typeof technicalIndicatorsResponseSchema, R<"/api/v1/stock/technical-indicators", "get">>,
  // GET /stock/valuation-ratios — stock-detail.getStockValuationRatios
  Accepts<typeof valuationRatiosResponseSchema, R<"/api/v1/stock/valuation-ratios", "get">>,
  // GET /symbols/{symbol}/supply-chain — stock-detail.getSupplyChainEdges
  Accepts<typeof supplyChainEdgesResponseSchema, R<"/api/v1/symbols/{symbol}/supply-chain", "get">>,
  // GET /symbols/{symbol}/chain-positions — stock-detail.getChainPositions
  Accepts<
    typeof chainPositionsResponseSchema,
    R<"/api/v1/symbols/{symbol}/chain-positions", "get">
  >,
  // GET /symbols/{symbol}/themes — stock-detail.getSymbolThemes
  Accepts<typeof symbolThemesResponseSchema, R<"/api/v1/symbols/{symbol}/themes", "get">>,
  // GET /symbols/{symbol}/entity-graph — entity-graph.getEntityGraph
  Accepts<typeof entityGraphResponseSchema, R<"/api/v1/symbols/{symbol}/entity-graph", "get">>,
  // GET /themes — theme.getThemes
  Accepts<typeof themesResponseSchema, R<"/api/v1/themes", "get">>,
  // GET /themes/{key} — theme.getThemeMembers
  Accepts<typeof themeMembersResponseSchema, R<"/api/v1/themes/{key}", "get">>,
  // GET /themes/{key}/tier-heatmap — stock-detail.getThemeTierHeatmap
  Accepts<typeof themeTierHeatmapResponseSchema, R<"/api/v1/themes/{key}/tier-heatmap", "get">>,
  // GET /threads — threads.listThreads
  Accepts<typeof threadListResponseSchema, R<"/api/v1/threads", "get">>,
  // POST /threads — threads.createThread
  Accepts<typeof threadSchema, R<"/api/v1/threads", "post">>,
  // GET /threads/{thread_id} — threads.getThread
  Accepts<typeof threadSchema, R<"/api/v1/threads/{thread_id}", "get">>,
  // PATCH /threads/{thread_id} — threads.patchThread
  Accepts<typeof threadSchema, R<"/api/v1/threads/{thread_id}", "patch">>,
  // POST /threads/{thread_id}/turns/{turn_id}/feedback — threads.submitTurnFeedback
  Accepts<
    typeof feedbackRecordSchema,
    R<"/api/v1/threads/{thread_id}/turns/{turn_id}/feedback", "post">
  >,
  // GET /oauth/consent/{request_id} — oauth-consent.getConsent
  Accepts<typeof consentResponseSchema, R<"/api/v1/oauth/consent/{request_id}", "get">>,
  // POST /oauth/consent/{request_id} — oauth-consent.decideConsent
  Accepts<typeof consentDecisionResponseSchema, R<"/api/v1/oauth/consent/{request_id}", "post">>,
  // GET /usage/llm — settings.getLlmUsage
  Accepts<typeof llmUsageSummarySchema, R<"/api/v1/usage/llm", "get">>,
  // GET /usage/quota — settings.getUsageQuota
  Accepts<typeof entitlementViewSchema, R<"/api/v1/usage/quota", "get">>,
  // GET /usage/today — settings.getTodayUsage
  Accepts<typeof todayUsageSchema, R<"/api/v1/usage/today", "get">>,
  // POST /voice/speak — voice.speakText
  Accepts<typeof speakResponseSchema, R<"/api/v1/voice/speak", "post">>,
  // POST /voice/transcribe — voice.transcribeAudio
  Accepts<typeof transcriptSchema, R<"/api/v1/voice/transcribe", "post">>,
  // GET /watchlists — watchlist.getWatchlists
  Accepts<typeof listWatchlistsResponseSchema, R<"/api/v1/watchlists", "get">>,
  // POST /watchlists — watchlist.createWatchlist
  Accepts<typeof watchlistGroupSchema, R<"/api/v1/watchlists", "post">>,
  // PATCH /watchlists/{watchlist_id} — watchlist.renameWatchlist
  Accepts<typeof watchlistGroupSchema, R<"/api/v1/watchlists/{watchlist_id}", "patch">>,
  // POST /watchlists/{watchlist_id}/items/{item_id}/move — watchlist.moveWatchlistItem
  Accepts<
    typeof watchlistItemSchema,
    R<"/api/v1/watchlists/{watchlist_id}/items/{item_id}/move", "post">
  >,
  // PUT /watchlists/{watchlist_id}/items/{symbol} — watchlist.addWatchlistItem
  Accepts<typeof watchlistItemSchema, R<"/api/v1/watchlists/{watchlist_id}/items/{symbol}", "put">>,
];

// Not JSON responses (event streams, redirects), so there is no body to check.
//   GET /auth/oauth/{provider}/callback
//   GET /quote/stream
//   GET /updates/stream
