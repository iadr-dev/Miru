import { getTranslations, setRequestLocale } from "next-intl/server";
import { type ReactNode, Suspense } from "react";
import { Caveat } from "@/app/[locale]/(app)/chat/caveat";
import { WatchDashboard } from "@/components/watch/watch-dashboard";

export default async function WatchPage({
  params,
}: {
  params: Promise<{ locale: string }>;
}): Promise<ReactNode> {
  const { locale } = await params;
  setRequestLocale(locale);
  const t = await getTranslations("watch");
  // The dashboard reads `?strategy=&symbol=` (a notification opens straight onto its stock).
  return (
    <Suspense>
      <WatchDashboard caveat={<Caveat text={t("disclaimer")} />} />
    </Suspense>
  );
}
