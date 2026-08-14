import { setRequestLocale } from "next-intl/server";
import type { ReactNode } from "react";
import { PetsPanel } from "./pets-panel";

export default async function PetsPage({
  params,
}: {
  params: Promise<{ locale: string }>;
}): Promise<ReactNode> {
  const { locale } = await params;
  setRequestLocale(locale);
  return <PetsPanel />;
}
