import { setRequestLocale } from "next-intl/server";
import type { ReactNode } from "react";
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
}: Readonly<{
  params: Promise<{ locale: string; key: string }>;
}>): Promise<ReactNode> {
  const { locale, key } = await params;
  setRequestLocale(locale);

  return (
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-3 p-4 sm:p-6">
      <ThemePage themeKey={decodeKey(key)} />
    </div>
  );
}
