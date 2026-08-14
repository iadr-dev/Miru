import { ChartLineIcon } from "@phosphor-icons/react/dist/ssr";
import { getTranslations, setRequestLocale } from "next-intl/server";
import type { ReactNode } from "react";
import { ComingSoon } from "@/components/shell/coming-soon";

export default async function StockPage({
  params,
}: {
  params: Promise<{ locale: string }>;
}): Promise<ReactNode> {
  const { locale } = await params;
  setRequestLocale(locale);
  const t = await getTranslations({ locale, namespace: "nav" });
  return <ComingSoon icon={ChartLineIcon} title={t("stock.label")} body={t("comingSoon")} />;
}
