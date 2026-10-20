import { setRequestLocale } from "next-intl/server";
import type { ReactNode } from "react";
import { PersonalityPanel } from "./personality-panel";

export default async function PersonalityPage({
  params,
}: Readonly<{
  params: Promise<{ locale: string }>;
}>): Promise<ReactNode> {
  const { locale } = await params;
  setRequestLocale(locale);
  return <PersonalityPanel />;
}
