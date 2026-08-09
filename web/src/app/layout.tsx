import type { ReactNode } from "react";
import { notoSansTC, plexMono } from "@/app/fonts";
import { NO_FLASH_THEME_SCRIPT } from "@/lib/theme";
import "@/app/globals.css";

/** Root layout owns <html>: the no-flash theme script (runs before paint) and the self-hosted
 * font variables. The `lang` attribute and the NextIntl provider live in the [locale] layout,
 * which is the real per-request boundary. */
export default function RootLayout({ children }: { children: ReactNode }): ReactNode {
  return (
    <html
      lang="zh-TW"
      suppressHydrationWarning
      className={`${notoSansTC.variable} ${plexMono.variable}`}
    >
      <head>
        {/* biome-ignore lint/security/noDangerouslySetInnerHtml: static, app-authored no-flash boot script (no user/model input) */}
        <script dangerouslySetInnerHTML={{ __html: NO_FLASH_THEME_SCRIPT }} />
      </head>
      <body>{children}</body>
    </html>
  );
}
