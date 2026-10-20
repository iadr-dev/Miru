import type { Metadata } from "next";
import { setRequestLocale } from "next-intl/server";
import type { ReactNode } from "react";
import { ConsentScreen } from "@/components/oauth/consent-screen";

/** A one-off, per-user screen: never indexed, and never framed (see the headers in
 * `next.config.ts`). */
export const metadata: Metadata = {
  robots: { index: false, follow: false },
};

type SearchParams = Record<string, string | string[] | undefined>;

function first(value: string | string[] | undefined): string | null {
  const one = Array.isArray(value) ? value[0] : value;
  return one && one.length > 0 ? one : null;
}

/** Where a third-party AI client sends the user from the API's `/oauth/authorize`:
 * `?request=<id>` to ask for consent, or `?error=<code>` when the client or its redirect URI was
 * refused (shown here, never redirected). */
export default async function OAuthConsentPage({
  params,
  searchParams,
}: Readonly<{
  params: Promise<{ locale: string }>;
  searchParams: Promise<SearchParams>;
}>): Promise<ReactNode> {
  const { locale } = await params;
  setRequestLocale(locale);
  const query = await searchParams;
  return <ConsentScreen requestId={first(query.request)} error={first(query.error)} />;
}
