"use client";

import { useQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { type ReactNode, useEffect } from "react";
import { useRouter } from "@/i18n/navigation";
import { fetchMe, UnauthenticatedError } from "@/lib/api/account";
import { startSessionKeepAlive } from "@/lib/api/auth";
import { queryKeys } from "@/lib/api/query-keys";
import type { MeResponse } from "@/types/account";

/**
 * Client-side auth guard for the authenticated `(app)` route group.
 *
 * Why client-side and not an RSC server guard: the backend sets the session as an `httpOnly`
 * cookie on ITS OWN origin (`:8000`) with CORS `allow_credentials` — there is no Next proxy
 * rewrite, so the Next server (`:3000`) can't read that cookie to gate in RSC. The only place the
 * session is legible is a credentialed browser fetch, exactly what the existing OAuth flow uses.
 * So the guard calls `GET /me` from the browser; a 401 bounces the visitor back to `/` (where the
 * sign-in modal lives). While the check is in flight it shows a calm shell-level loading state
 * rather than flashing the protected UI.
 *
 * The resolved `MeResponse` is handed to children via a render prop so the top bar reads the real
 * tier/account without a second `/me` round-trip.
 */
export function AuthGuard({ children }: { children: (me: MeResponse) => ReactNode }): ReactNode {
  const t = useTranslations("shell");
  const router = useRouter();

  const query = useQuery({
    queryKey: queryKeys.me,
    queryFn: ({ signal }) => fetchMe(signal),
    retry: (failureCount, error) =>
      error instanceof UnauthenticatedError ? false : failureCount < 1,
  });

  const isUnauthenticated = query.error instanceof UnauthenticatedError;

  useEffect(() => {
    if (isUnauthenticated) {
      router.replace("/");
    }
  }, [isUnauthenticated, router]);

  // Keep the short-lived access token fresh while signed in, so live streams and public data
  // routes never fall back to anonymous (free-tier) answers mid-session.
  const signedIn = query.isSuccess;
  useEffect(() => (signedIn ? startSessionKeepAlive() : undefined), [signedIn]);

  if (query.isSuccess) {
    return <>{children(query.data)}</>;
  }

  // Unauthenticated (redirecting) or any other pending/error state → calm full-height placeholder.
  return (
    <div
      className="flex min-h-[100dvh] items-center justify-center bg-[var(--bg)]"
      aria-live="polite"
    >
      <span className="text-[var(--text-muted)] text-sm">
        {isUnauthenticated ? t("authRequired") : t("loading")}
      </span>
    </div>
  );
}
