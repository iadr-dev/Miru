import type { Metadata, Viewport } from "next";
import { notFound } from "next/navigation";
import { hasLocale, NextIntlClientProvider } from "next-intl";
import { getTranslations, setRequestLocale } from "next-intl/server";
import type { ReactNode } from "react";
import { notoSansTC, plexMono, sourceSerif } from "@/app/fonts";
import { routing } from "@/i18n/routing";
import { NO_FLASH_THEME_SCRIPT } from "@/lib/theme";
import "@/app/globals.css";

export function generateStaticParams(): Array<{ locale: string }> {
  return routing.locales.map((locale) => ({ locale }));
}

/** `viewportFit: "cover"` lets the page paint under the notch / home indicator / foldable hinge so the
 * safe-area insets (`env(safe-area-inset-*)`) are meaningful; the shell pads with them explicitly.
 * Pinch-zoom stays enabled (accessibility). The theme colour tints the mobile browser chrome. */
export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#f6f1e8" },
    { media: "(prefers-color-scheme: dark)", color: "#100d0b" },
  ],
};

export async function generateMetadata({
  params,
}: {
  params: Promise<{ locale: string }>;
}): Promise<Metadata> {
  const { locale } = await params;
  const t = await getTranslations({ locale, namespace: "meta" });
  return {
    title: t("title"),
    description: t("description"),
  };
}

/** The root layout (every page lives under `[locale]`; the proxy routes bare paths here). It owns
 * <html> so `lang` is the active locale — screen readers, the browser's translate prompt and CJK
 * line-breaking all read it from the document root — plus the no-flash theme script (runs before
 * paint), the self-hosted font variables, and the NextIntl provider. */
export default async function LocaleLayout({
  children,
  params,
}: Readonly<{
  children: ReactNode;
  params: Promise<{ locale: string }>;
}>): Promise<ReactNode> {
  const { locale } = await params;
  if (!hasLocale(routing.locales, locale)) {
    notFound();
  }
  setRequestLocale(locale);

  return (
    <html
      lang={locale}
      suppressHydrationWarning
      className={`${notoSansTC.variable} ${plexMono.variable} ${sourceSerif.variable}`}
    >
      <head>
        {/* biome-ignore lint/security/noDangerouslySetInnerHtml: static, app-authored no-flash boot script (no user/model input) */}
        <script dangerouslySetInnerHTML={{ __html: NO_FLASH_THEME_SCRIPT }} />
      </head>
      <body>
        <NextIntlClientProvider>{children}</NextIntlClientProvider>
      </body>
    </html>
  );
}
