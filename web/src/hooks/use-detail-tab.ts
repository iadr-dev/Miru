"use client";

/**
 * Shared `?tab=` URL-state machine for every asset-detail page (stock, ETF, and whatever comes
 * next). Extracted from `stock-tab-bar.tsx`'s original inline implementation — that component
 * kept the active tab in `?tab=` rather than local `useState` because a locale switch
 * (`LanguageSwitcher`'s `router.replace(pathname, { locale })`) is a real navigation that
 * remounts the whole page tree, which would silently reset a plain `useState` back to the
 * default every time. Generic over the caller's own tab-key union so a second (ETF) and future
 * third detail-page kind reuse this exact state machine instead of re-deriving it.
 */
import { useSearchParams } from "next/navigation";
import { useCallback } from "react";
import { usePathname, useRouter } from "@/i18n/navigation";
import { withSearchParams } from "@/lib/url-params";

export function useDetailTab<T extends string>(
  tabs: readonly T[],
  defaultTab: T,
): { tab: T; setTab: (next: T) => void } {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const rawTab = searchParams.get("tab");
  const tab: T = (tabs as readonly string[]).includes(rawTab ?? "") ? (rawTab as T) : defaultTab;

  const setTab = useCallback(
    (next: T) => {
      const href = withSearchParams(pathname, searchParams, {
        tab: next === defaultTab ? null : next,
      });
      router.replace(href, { scroll: false });
    },
    [pathname, router, searchParams, defaultTab],
  );

  return { tab, setTab };
}
