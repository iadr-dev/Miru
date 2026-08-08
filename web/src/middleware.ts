import createMiddleware from "next-intl/middleware";
import { routing } from "@/i18n/routing";

export default createMiddleware(routing);

export const config = {
  // Run on every path except Next internals, the API proxy, and static assets (files with a dot).
  matcher: ["/((?!api|_next|_vercel|.*\\..*).*)"],
};
