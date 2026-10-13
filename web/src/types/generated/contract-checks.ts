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

import type { beginAuthResponseSchema } from "@/lib/api/schemas";
import type { speakResponseSchema, transcriptSchema } from "@/lib/api/voice";
import type { entitlementViewSchema, meResponseSchema } from "@/types/account";
import type { threadListResponseSchema, threadSchema } from "@/types/conversation";
import type { Accepts, ResponseOf as R } from "@/types/generated/contract";
import type {
  activePetResponseSchema,
  alertPreferenceSchema,
  alertRuleSchema,
  alertsResponseSchema,
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
  // POST /auth/oauth/{provider} — auth.beginOAuth
  Accepts<typeof beginAuthResponseSchema, R<"/api/v1/auth/oauth/{provider}", "post">>,
  // POST /auth/oauth/{provider}/link — auth.beginOAuthLink
  Accepts<typeof beginAuthResponseSchema, R<"/api/v1/auth/oauth/{provider}/link", "post">>,
  // POST /billing/change-plan — settings.changePlan
  Accepts<typeof subscriptionResponseSchema, R<"/api/v1/billing/change-plan", "post">>,
  // POST /billing/checkout — settings.checkout
  Accepts<typeof checkoutResponseSchema, R<"/api/v1/billing/checkout", "post">>,
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
  // GET /me — account.fetchMe
  Accepts<typeof meResponseSchema, R<"/api/v1/me", "get">>,
  // PATCH /me — settings.updateProfile
  Accepts<typeof meResponseSchema, R<"/api/v1/me", "patch">>,
  // GET /me/alert-preferences — settings.getAlertPreferences
  Accepts<typeof alertPreferenceSchema, R<"/api/v1/me/alert-preferences", "get">>,
  // PUT /me/alert-preferences — settings.putAlertPreferences
  Accepts<typeof alertPreferenceSchema, R<"/api/v1/me/alert-preferences", "put">>,
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
  // GET /schedules — settings.listSchedules
  Accepts<typeof schedulesResponseSchema, R<"/api/v1/schedules", "get">>,
  // POST /schedules — settings.createSchedule
  Accepts<typeof scheduleJobSchema, R<"/api/v1/schedules", "post">>,
  // PATCH /schedules/{job_id} — settings.updateSchedule
  Accepts<typeof scheduleJobSchema, R<"/api/v1/schedules/{job_id}", "patch">>,
  // GET /threads — threads.listThreads
  Accepts<typeof threadListResponseSchema, R<"/api/v1/threads", "get">>,
  // POST /threads — threads.createThread
  Accepts<typeof threadSchema, R<"/api/v1/threads", "post">>,
  // GET /threads/{thread_id} — threads.getThread
  Accepts<typeof threadSchema, R<"/api/v1/threads/{thread_id}", "get">>,
  // PATCH /threads/{thread_id} — threads.patchThread
  Accepts<typeof threadSchema, R<"/api/v1/threads/{thread_id}", "patch">>,
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

// Not checkable yet: the backend documents these responses only as a plain object
// (`dict[str, Any]`, or `dict[str, list[X]]` for a keyed wrapper), so there are no fields to
// compare. Each one becomes a check above once its route documents a response model.
//   DELETE /me
//   GET /analytics/rankings
//   GET /analytics/screener/factors
//   GET /analytics/screener/presets
//   GET /analytics/screener/screens
//   GET /analytics/{symbol}/score
//   GET /analytics/{symbol}/summary
//   GET /analytics/{symbol}/trend-score
//   GET /billing/history
//   GET /derivatives/taiex-vix
//   GET /funds/comparison
//   GET /funds/{symbol}/expense-ratio
//   GET /funds/{symbol}/fund-info
//   GET /funds/{symbol}/holdings
//   GET /funds/{symbol}/premium-discount
//   GET /funds/{symbol}/profile
//   GET /funds/{symbol}/theme-tags
//   GET /funds/{symbol}/trailing-returns
//   GET /macro
//   GET /market/active-etf-report
//   GET /market/advance-decline
//   GET /market/daily-signal
//   GET /market/disposition-board
//   GET /market/dividend-calendar
//   GET /market/etf-list
//   GET /market/etf-money-flow
//   GET /market/futures-snapshot
//   GET /market/holder-accumulation
//   GET /market/index-contribution
//   GET /market/index-strip
//   GET /market/industry-flow
//   GET /market/insider-transfer-declarations
//   GET /market/institutional-ranking
//   GET /market/macro-calendar
//   GET /market/margin-maintenance
//   GET /market/margin-short-balance
//   GET /market/material-announcement-feed
//   GET /market/material-announcements
//   GET /market/news-feed
//   GET /market/price-history
//   GET /market/ptt-board
//   GET /market/quotes
//   GET /market/sector-constituents
//   GET /market/sector-heatmap
//   GET /market/sector-rotation-rrg
//   GET /market/stock-heatmap
//   GET /market/symbol-info
//   GET /market/tag-screener
//   GET /market/tick-snapshot
//   GET /market/tw-rankings
//   GET /market/us-index-strip
//   GET /market/us-rankings
//   GET /search
//   GET /search
//   GET /stock/analyst-consensus
//   GET /stock/calendar-events
//   GET /stock/calendar-events-feed
//   GET /stock/chip-flows
//   GET /stock/company-profile
//   GET /stock/day-trading
//   GET /stock/disposition-securities
//   GET /stock/dividend-policy-summary
//   GET /stock/esg-score
//   GET /stock/etfs-holding-stock
//   GET /stock/financial-statement
//   GET /stock/finmind-news
//   GET /stock/foreign-shareholding
//   GET /stock/government-bank-flows
//   GET /stock/insider-shareholding
//   GET /stock/insider-transfer-declarations
//   GET /stock/institutional-flow
//   GET /stock/intraday-kbar
//   GET /stock/live-price-quote
//   GET /stock/live-quote
//   GET /stock/margin-short
//   GET /stock/month-revenue
//   GET /stock/peers
//   GET /stock/price-limit
//   GET /stock/price-tick
//   GET /stock/ptt-search
//   GET /stock/sec-filings
//   GET /stock/securities-lending
//   GET /stock/shareholder-dispersion
//   GET /stock/technical-indicators
//   GET /stock/valuation-ratios
//   GET /symbols/{symbol}/supply-chain
//   GET /symbols/{symbol}/themes
//   GET /themes
//   GET /themes/{key}
//   GET /themes/{key}/tier-heatmap
//   POST /analytics/screener/run
//   POST /analytics/screener/screens
