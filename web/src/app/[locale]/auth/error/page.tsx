import { setRequestLocale } from "next-intl/server";
import type { ReactNode } from "react";
import { Suspense } from "react";
import { AuthErrorClient } from "./error-client";

/** Landing target for a failed OAuth callback. The backend does a full top-level 302 here
 * (never bare JSON — the browser is mid-navigation, not making a fetch) with `?provider=&code=&
 * retryable=` describing what went wrong; see `_oauth_error_redirect` in
 * `backend/src/api/routers/auth.py`. This page renders that outcome and offers a way back.
 * `useSearchParams()` in the client leaf requires a Suspense boundary (Next.js App Router). */
export default async function AuthErrorPage({
  params,
}: Readonly<{
  params: Promise<{ locale: string }>;
}>): Promise<ReactNode> {
  const { locale } = await params;
  setRequestLocale(locale);
  return (
    <Suspense fallback={null}>
      <AuthErrorClient />
    </Suspense>
  );
}
