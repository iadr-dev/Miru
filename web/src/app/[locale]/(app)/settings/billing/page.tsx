import { setRequestLocale } from "next-intl/server";
import type { ReactNode } from "react";
import { BillingPanel } from "./billing-panel";

export default async function BillingPage({
  params,
}: {
  params: Promise<{ locale: string }>;
}): Promise<ReactNode> {
  const { locale } = await params;
  setRequestLocale(locale);
  return <BillingPanel />;
}
