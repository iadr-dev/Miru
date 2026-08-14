import { ChartBarIcon } from "@phosphor-icons/react/dist/ssr";
import { getTranslations, setRequestLocale } from "next-intl/server";
import type { ReactNode } from "react";

/** Market landing (the product default: `/` → `/market`). The real dashboard is future work; this
 * pass keeps a calm placeholder inside the now-real app shell (sidebar/top bar/rail). Sign-out moved
 * into the shell's account menu, so this is content-only. */
export default async function MarketPage({
  params,
}: {
  params: Promise<{ locale: string }>;
}): Promise<ReactNode> {
  const { locale } = await params;
  setRequestLocale(locale);
  const t = await getTranslations({ locale, namespace: "market.stub" });
  const tNav = await getTranslations({ locale, namespace: "nav" });

  return (
    <div className="flex min-h-full items-center justify-center p-6">
      <div className="w-full max-w-lg rounded-[var(--radius-card)] border border-[var(--border)] bg-[var(--surface)] p-8 text-center">
        <div className="mx-auto grid size-12 place-items-center rounded-[var(--radius-button)] bg-[var(--accent-weak)]">
          <ChartBarIcon size={24} weight="regular" className="text-[var(--accent)]" />
        </div>
        <h1 className="mt-4 font-semibold text-[var(--text)] text-xl">{tNav("market.label")}</h1>
        <p className="mt-2 text-[var(--text-muted)] text-sm">{t("body")}</p>
      </div>
    </div>
  );
}
