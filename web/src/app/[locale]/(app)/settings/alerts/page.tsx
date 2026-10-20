import { setRequestLocale } from "next-intl/server";
import type { ReactNode } from "react";
import { AlertsPanel } from "./alerts-panel";

export default async function AlertsPage({
  params,
}: Readonly<{
  params: Promise<{ locale: string }>;
}>): Promise<ReactNode> {
  const { locale } = await params;
  setRequestLocale(locale);
  return <AlertsPanel />;
}
