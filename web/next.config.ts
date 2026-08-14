import type { NextConfig } from "next";
import createNextIntlPlugin from "next-intl/plugin";

const withNextIntl = createNextIntlPlugin("./src/i18n/request.ts");

const nextConfig: NextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  // Typed routes for locale-aware `<Link>` href checking (promoted out of experimental in Next 15).
  typedRoutes: true,
  // Next.js 16 auto-writes/refreshes its own agent-rules guidance into CLAUDE.md on every `next
  // dev`/`next build`. Our hand-maintained coding-standards doc lives at web/OVERVIEW.md instead,
  // so CLAUDE.md is Next's file to own — leave this on (the default).
  agentRules: true,
};

export default withNextIntl(nextConfig);
