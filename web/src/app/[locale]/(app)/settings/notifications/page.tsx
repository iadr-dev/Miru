import { setRequestLocale } from "next-intl/server";
import type { ReactNode } from "react";
import { NotificationsPanel } from "./notifications-panel";

export default async function NotificationsPage({
  params,
}: Readonly<{
  params: Promise<{ locale: string }>;
}>): Promise<ReactNode> {
  const { locale } = await params;
  setRequestLocale(locale);
  return <NotificationsPanel />;
}
