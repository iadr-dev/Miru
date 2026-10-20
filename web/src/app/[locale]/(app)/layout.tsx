import { setRequestLocale } from "next-intl/server";
import type { ReactNode } from "react";
import { DataUpdatesBridge } from "@/components/providers/data-updates-bridge";
import { QueryProvider } from "@/components/providers/query-provider";
import { AppShell } from "@/components/shell/app-shell";

/**
 * Shared layout for the authenticated `(app)` route group (`/chat`, `/market`, `/stock`,
 * `/screener`, `/settings`). Owns the TanStack Query client + the app shell (sidebar/top bar/
 * watchlist rail) and the client auth guard, so every route inside is protected and framed
 * identically. The route-group parens keep the URL clean (`/chat`, not `/(app)/chat`).
 */
export default async function AppGroupLayout({
  children,
  modal,
  params,
}: Readonly<{
  children: ReactNode;
  modal: ReactNode;
  params: Promise<{ locale: string }>;
}>): Promise<ReactNode> {
  const { locale } = await params;
  setRequestLocale(locale);
  return (
    <QueryProvider>
      <DataUpdatesBridge />
      <AppShell modal={modal}>{children}</AppShell>
    </QueryProvider>
  );
}
