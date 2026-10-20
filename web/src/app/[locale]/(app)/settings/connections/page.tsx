import { setRequestLocale } from "next-intl/server";
import type { ReactNode } from "react";
import { ConnectionsPanel } from "./connections-panel";

export default async function ConnectionsPage({
  params,
}: Readonly<{
  params: Promise<{ locale: string }>;
}>): Promise<ReactNode> {
  const { locale } = await params;
  setRequestLocale(locale);
  return <ConnectionsPanel />;
}
