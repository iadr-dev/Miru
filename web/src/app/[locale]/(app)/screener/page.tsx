import { setRequestLocale } from "next-intl/server";
import type { ReactNode } from "react";
import { ScreenerWorkbench } from "@/components/screener/screener-workbench";

export default async function ScreenerPage({
  params,
}: {
  params: Promise<{ locale: string }>;
}): Promise<ReactNode> {
  const { locale } = await params;
  setRequestLocale(locale);
  return <ScreenerWorkbench />;
}
