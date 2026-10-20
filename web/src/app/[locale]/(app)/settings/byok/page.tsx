import { setRequestLocale } from "next-intl/server";
import type { ReactNode } from "react";
import { ByokPanel } from "./byok-panel";

export default async function ByokPage({
  params,
}: Readonly<{
  params: Promise<{ locale: string }>;
}>): Promise<ReactNode> {
  const { locale } = await params;
  setRequestLocale(locale);
  return <ByokPanel />;
}
