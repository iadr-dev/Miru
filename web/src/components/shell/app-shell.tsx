"use client";

import type { ReactNode } from "react";
import { AuthGuard } from "@/components/shell/auth-guard";
import { Sidebar } from "@/components/shell/sidebar";
import { TopBar } from "@/components/shell/top-bar";
import { useSidebarCollapsed, useWatchlistOpen } from "@/components/shell/use-shell-ui";
import { WatchlistRail } from "@/components/shell/watchlist-rail";

/**
 * The authenticated app frame: collapsible left sidebar + sticky glass top bar + toggleable right
 * watchlist rail, with the routed page in the center. Uses a CSS grid app frame (`min-h-[100dvh]`,
 * not `h-screen`, per web/CLAUDE.md §3). Wraps everything in the client `AuthGuard` so an
 * unauthenticated visitor is bounced to `/` before any protected UI paints — the guard resolves
 * `/me` once and hands the session to the top bar.
 */
export function AppShell({
  children,
  modal,
}: {
  children: ReactNode;
  modal?: ReactNode;
}): ReactNode {
  const { collapsed, toggle: toggleSidebar } = useSidebarCollapsed();
  const {
    open: watchlistOpen,
    toggle: toggleWatchlist,
    close: closeWatchlist,
  } = useWatchlistOpen();

  return (
    <AuthGuard>
      {(me) => (
        <div className="flex min-h-[100dvh] bg-[var(--bg)]">
          <div className="sticky top-0 h-[100dvh] shrink-0">
            <Sidebar collapsed={collapsed} onToggle={toggleSidebar} me={me} />
          </div>

          <div className="flex min-w-0 flex-1 flex-col">
            <TopBar me={me} onToggleWatchlist={toggleWatchlist} />
            <main className="min-h-0 flex-1">{children}</main>
          </div>

          {watchlistOpen ? (
            <div className="sticky top-0 hidden h-[100dvh] shrink-0 lg:block">
              <WatchlistRail onClose={closeWatchlist} />
            </div>
          ) : null}

          {modal}
        </div>
      )}
    </AuthGuard>
  );
}
