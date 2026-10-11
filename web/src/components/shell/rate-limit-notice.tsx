"use client";

import { useTranslations } from "next-intl";
import { useEffect } from "react";
import { toast } from "@/components/ui/toast";
import { onRateLimited } from "@/lib/api/auth";

/** One page load fires many requests, so a limit hit would otherwise raise a wall of identical
 * messages: show it at most once per window. */
const QUIET_MS = 15_000;

/** Mount once in the app shell: turns the fetch layer's "still rate limited" signal into a single
 * calm toast (the page keeps showing its last good data meanwhile). */
export function RateLimitNotice(): null {
  const t = useTranslations("common");

  useEffect(() => {
    let lastShown = 0;
    return onRateLimited((seconds) => {
      const now = Date.now();
      if (now - lastShown < QUIET_MS) return;
      lastShown = now;
      toast.info(seconds === null ? t("rateLimited") : t("rateLimitedWait", { seconds }));
    });
  }, [t]);

  return null;
}
