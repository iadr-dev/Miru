import type { Viewport } from "next";
import type { ReactNode } from "react";
import { notoSansTC, plexMono, sourceSerif } from "@/app/fonts";
import { NO_FLASH_THEME_SCRIPT } from "@/lib/theme";
import "@/app/globals.css";

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

/** Root layout owns <html>: the no-flash theme script (runs before paint) and the self-hosted
 * font variables. The `lang` attribute and the NextIntl provider live in the [locale] layout,
 * which is the real per-request boundary. */
export default function RootLayout({ children }: { children: ReactNode }): ReactNode {
  return (
    <html
      lang="zh-TW"
      suppressHydrationWarning
      className={`${notoSansTC.variable} ${plexMono.variable} ${sourceSerif.variable}`}
    >
      <head>
        {/* biome-ignore lint/security/noDangerouslySetInnerHtml: static, app-authored no-flash boot script (no user/model input) */}
        <script dangerouslySetInnerHTML={{ __html: NO_FLASH_THEME_SCRIPT }} />
      </head>
      <body>{children}</body>
    </html>
  );
}
