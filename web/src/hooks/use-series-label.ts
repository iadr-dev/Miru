"use client";

import { useTranslations } from "next-intl";

/** Resolves a series' display name: the zh-TW/en name from the messages when one exists, else the
 * label the API sent (a series added on the backend still shows something sensible). */
export function useSeriesLabel(): (seriesId: string, fallback: string) => string {
  const t = useTranslations("market.cards.usMacro.seriesLabels");
  return (seriesId, fallback) => (t.has(seriesId) ? t(seriesId) : fallback);
}
