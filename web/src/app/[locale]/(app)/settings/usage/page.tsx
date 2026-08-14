import { setRequestLocale } from "next-intl/server";
import type { ReactNode } from "react";
import { UsagePanel } from "./usage-panel";

export default async function UsagePage({
  params,
}: {
  params: Promise<{ locale: string }>;
}): Promise<ReactNode> {
  const { locale } = await params;
  setRequestLocale(locale);
  return <UsagePanel />;
}
