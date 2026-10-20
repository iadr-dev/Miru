import { setRequestLocale } from "next-intl/server";
import type { ReactNode } from "react";
import { EntryScreen } from "@/components/entry/entry-screen";

/** Entry page — the app's "landing" is the composer (chat-app pattern), not a marketing site.
 * RSC shell; all interactivity lives in the EntryScreen client leaf. */
export default async function EntryPage({
  params,
}: Readonly<{
  params: Promise<{ locale: string }>;
}>): Promise<ReactNode> {
  const { locale } = await params;
  setRequestLocale(locale);
  return <EntryScreen />;
}
