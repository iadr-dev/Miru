/** Next.js 16 renamed the `middleware.ts` convention to `proxy.ts` (the export stays a default
 * export — `createMiddleware`'s factory function is unchanged, only the file/convention name
 * moved). This resolves the locale prefix on every request before rendering. */
import createMiddleware from "next-intl/middleware";
import { routing } from "@/i18n/routing";

export default createMiddleware(routing);

export const config = {
  // Run on every path except Next internals, the API proxy, and static assets (files with a dot).
  matcher: ["/((?!api|_next|_vercel|.*\\..*).*)"],
};
