import { setRequestLocale } from "next-intl/server";
import { type ReactNode, Suspense } from "react";
import { WatchDashboard } from "@/components/watch/watch-dashboard";

export default async function WatchPage({
  params,
}: {
  params: Promise<{ locale: string }>;
}): Promise<ReactNode> {
  const { locale } = await params;
  setRequestLocale(locale);
  // The dashboard reads `?date=&strategy=&symbol=` (a notification opens straight onto its stock).
  return (
    <Suspense>
      <WatchDashboard />
    </Suspense>
  );
}
