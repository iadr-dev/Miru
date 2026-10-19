"use client";

import { useSearchParams } from "next/navigation";
import { useCallback } from "react";
import { usePathname, useRouter } from "@/i18n/navigation";
import { withSearchParams } from "@/lib/url-params";

/** Read the current query string and patch it in place (`null` removes a key) with
 * `router.replace(…, { scroll: false })` — the same navigation `useDetailTab` uses, so a locale
 * switch (a real navigation that remounts the page) keeps the state, and a patch never scrolls. */
export function useUrlParams(): {
  readonly searchParams: URLSearchParams;
  readonly setParams: (updates: Readonly<Record<string, string | null>>) => void;
} {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const setParams = useCallback(
    (updates: Readonly<Record<string, string | null>>) => {
      router.replace(withSearchParams(pathname, searchParams, updates), { scroll: false });
    },
    [pathname, router, searchParams],
  );
  return { searchParams, setParams };
}
