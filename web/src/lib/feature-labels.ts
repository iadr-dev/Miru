/** Feature ids the backend gates by tier (`accounts/entitlements.py::FEATURE_MIN_TIER`) that have
 * a translated name. An id this list has not caught up to falls back to the raw key. */
const LABELED_FEATURES = new Set([
  "live_quote_stream",
  "ai_summary",
  "swot",
  "full_score",
  "deep_research",
  "tick_minute_k",
  "supply_chain",
  "concept_themes",
]);

type Translate = (key: string) => string;

/** `t` is `useTranslations("settings.usage")`. */
export function featureLabel(t: Translate, feature: string): string {
  return LABELED_FEATURES.has(feature) ? t(`featureOption.${feature}`) : feature;
}
