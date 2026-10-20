import { setRequestLocale } from "next-intl/server";
import type { ReactNode } from "react";
import { MemoryPanel } from "./memory-panel";

export default async function MemoryPage({
  params,
}: Readonly<{
  params: Promise<{ locale: string }>;
}>): Promise<ReactNode> {
  const { locale } = await params;
  setRequestLocale(locale);
  return <MemoryPanel />;
}
