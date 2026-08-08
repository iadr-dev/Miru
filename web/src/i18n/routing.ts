/**
 * i18n routing contract for the whole app.
 *
 * Decision (documented per the brief): **locale-prefixed routing** with next-intl's App Router
 * middleware, `localePrefix: "always"`. Every route lives under `/<locale>/…` (`/zh-TW/…`,
 * `/en/…`). Rationale:
 *  - zh-TW is the brand-primary/default locale — it is designed first, not a translation shim.
 *  - An always-on prefix keeps the OAuth `redirect_uri` path stable and unambiguous
 *    (`/zh-TW/auth/callback/google`), which the provider console must match byte-for-byte.
 *  - RSC-native: message loading happens server-side per request (src/i18n/request.ts); the
 *    locale switch is a plain navigation, so it swaps the message set without a client reload.
 *
 * Future routes (market, chat, screener, settings) slot under the same `[locale]` segment with
 * zero routing changes.
 */
import { defineRouting } from "next-intl/routing";

export const routing = defineRouting({
  locales: ["zh-TW", "en"],
  defaultLocale: "zh-TW",
  localePrefix: "always",
});

export type AppLocale = (typeof routing.locales)[number];
