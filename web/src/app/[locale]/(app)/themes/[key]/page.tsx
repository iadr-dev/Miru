import { getTranslations, setRequestLocale } from "next-intl/server";
import type { ReactNode } from "react";
import { Caveat } from "@/app/[locale]/(app)/chat/caveat";
import { ThemePage } from "@/components/themes/theme-page";

function decodeKey(raw: string): string {
  try {
    return decodeURIComponent(raw);
  } catch {
    return raw;
  }
}

/** `/themes/{key}` — a concept theme's page. A thin server shell: it resolves the params and the
 * required caveat, and the client panel does the (signed-in-tier-dependent) reads. */
export default async function ThemeRoutePage({
  params,
}: {
  params: Promise<{ locale: string; key: string }>;
}): Promise<ReactNode> {
  const { locale, key } = await params;
  setRequestLocale(locale);
  const tCaveat = await getTranslations({ locale, namespace: "chat.caveat" });

  return (
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-3 p-4 sm:p-6">
      <ThemePage themeKey={decodeKey(key)} />
      <div>
        <Caveat text={tCaveat("nonTrading")} />
      </div>
    </div>
  );
}
