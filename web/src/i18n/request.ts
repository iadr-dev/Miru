/** Per-request message loading for RSC. `requestLocale` corresponds to the `[locale]` route
 * segment matched by `proxy.ts`'s `next-intl` middleware (which runs on every request); it can be
 * `undefined` for routes outside that segment, so we fall back to the configured default. */

import { hasLocale } from "next-intl";
import { getRequestConfig } from "next-intl/server";
import { routing } from "@/i18n/routing";

export default getRequestConfig(async ({ requestLocale }) => {
  const requested = await requestLocale;
  const locale = hasLocale(routing.locales, requested) ? requested : routing.defaultLocale;

  return {
    locale,
    messages: (await import(`../../messages/${locale}.json`)).default,
  };
});
