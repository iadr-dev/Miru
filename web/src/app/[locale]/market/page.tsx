import { ChartBar } from "@phosphor-icons/react/dist/ssr";
import { getTranslations, setRequestLocale } from "next-intl/server";
import type { ReactNode } from "react";
import { SignOutButton } from "@/components/market/sign-out-button";

/** Bare authenticated-landing stub. `/` → `/market` is the product default landing
 * (docs/web/README.md §4). The real Market dashboard is future work; this proves the OAuth flow
 * lands somewhere real. Rendered as an RSC shell with a small client sign-out leaf. */
export default async function MarketStubPage({
  params,
}: {
  params: Promise<{ locale: string }>;
}): Promise<ReactNode> {
  const { locale } = await params;
  setRequestLocale(locale);
  const t = await getTranslations({ locale, namespace: "market.stub" });
  const tDisc = await getTranslations({ locale, namespace: "disclaimer" });

  return (
    <div className="flex min-h-[100dvh] flex-col items-center justify-center bg-[var(--bg)] p-4">
      <div className="w-full max-w-lg rounded-[var(--radius-card)] border border-[var(--border)] bg-[var(--surface)] p-8 text-center">
        <div className="mx-auto grid size-12 place-items-center rounded-[var(--radius-button)] bg-[var(--accent-weak)]">
          <ChartBar size={24} weight="regular" className="text-[var(--accent)]" />
        </div>
        <h1 className="mt-4 font-semibold text-[var(--text)] text-xl">{t("signedIn")}</h1>
        <p className="mt-2 text-[var(--text-muted)] text-sm">{t("body")}</p>
        <div className="mt-6 flex justify-center">
          <SignOutButton />
        </div>
      </div>
      <p className="mt-6 max-w-lg text-center text-[13px] text-[var(--text-muted)]">
        {tDisc("text")}
      </p>
    </div>
  );
}
