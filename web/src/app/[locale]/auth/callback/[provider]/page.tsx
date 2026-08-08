import { setRequestLocale } from "next-intl/server";
import type { ReactNode } from "react";
import { CallbackClient } from "./callback-client";

/** OAuth redirect target. The heavy lifting (reading code+state, calling the backend, redirecting)
 * is client-side because it depends on the browser URL + credentialed fetch. */
export default async function CallbackPage({
  params,
}: {
  params: Promise<{ locale: string; provider: string }>;
}): Promise<ReactNode> {
  const { locale, provider } = await params;
  setRequestLocale(locale);
  return <CallbackClient provider={provider} />;
}
