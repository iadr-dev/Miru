"use client";

import { useTranslations } from "next-intl";
import { useEffect } from "react";
import { toast } from "@/components/ui/toast";
import { onQueryFailed } from "@/lib/api/query-failures";

/** A page fires many reads, so an outage would otherwise raise one message per request. */
const QUIET_MS = 30_000;

/** Mount once in the app shell: says, at most once per window, that some data could not be
 * loaded, so an empty-looking card is not mistaken for "there is no data". */
export function QueryFailureNotice(): null {
  const t = useTranslations("common");

  useEffect(() => {
    let lastShown = 0;
    return onQueryFailed(() => {
      const now = Date.now();
      if (now - lastShown < QUIET_MS) return;
      lastShown = now;
      toast.error(t("loadFailed"));
    });
  }, [t]);

  return null;
}
