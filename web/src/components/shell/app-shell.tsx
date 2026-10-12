"use client";

import * as Dialog from "@radix-ui/react-dialog";
import { useTranslations } from "next-intl";
import { type ReactNode, useCallback, useState } from "react";
import { WelcomePullDialog } from "@/components/pets/welcome-pull-dialog";
import { AuthGuard } from "@/components/shell/auth-guard";
import { MobileDrawer, MobileTabBar } from "@/components/shell/mobile-nav";
import { Sidebar } from "@/components/shell/sidebar";
import { TabStripScroll } from "@/components/shell/tab-strip-scroll";
import { TopBar } from "@/components/shell/top-bar";
import {
  useIsDesktopRail,
  useSidebarCollapsed,
  useWatchlistOpen,
  useWatchlistWidth,
} from "@/components/shell/use-shell-ui";
import { WatchlistRail } from "@/components/shell/watchlist-rail";
import { Toaster } from "@/components/ui/toast";
import { QueryFailureNotice } from "./query-failure-notice";
import { RateLimitNotice } from "./rate-limit-notice";

/**
 * The authenticated app frame, responsive across the device range:
 *
 * - `>= lg` (1024px): collapsible left sidebar + top bar + dockable right watchlist rail.
 * - `md` (768-1023px): icon sidebar; the watchlist opens as a right sheet.
 * - `< md` (phones, foldable covers): no sidebar - a bottom tab bar + a drawer for the rest; the
 *   watchlist is a full-height sheet.
 *
 * Static show/hide is pure CSS (no layout wait on JS); the sheets/drawer are the only JS-gated
 * pieces. Uses `min-h-[100dvh]` (not `h-screen`) per web/OVERVIEW.md §3. Wraps everything in the
 * client `AuthGuard` so an unauthenticated visitor is bounced before any protected UI paints.
 */
export function AppShell({
  children,
  modal,
}: {
  children: ReactNode;
  modal?: ReactNode;
}): ReactNode {
  const { collapsed, toggle: toggleSidebar } = useSidebarCollapsed();
  const { open: railOpen, toggle: toggleRail, close: closeRail } = useWatchlistOpen();
  const { width: watchlistWidth, setWidth: setWatchlistWidth } = useWatchlistWidth();
  const isDesktopRail = useIsDesktopRail();
  // Below lg the watchlist is a transient sheet with its own (non-persisted) open state, so a rail
  // left open on a desktop never pops a sheet over a phone on load.
  const [sheetOpen, setSheetOpen] = useState(false);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const closeSheet = useCallback(() => setSheetOpen(false), []);

  const watchlistOpen = isDesktopRail ? railOpen : sheetOpen;
  const toggleWatchlist = (): void => {
    if (isDesktopRail) {
      toggleRail();
    } else {
      setSheetOpen((prev) => !prev);
    }
  };

  return (
    <AuthGuard>
      {(me) => (
        <div className="flex min-h-[100dvh] bg-[var(--bg)]">
          <div className="sticky top-0 hidden h-[100dvh] shrink-0 md:block">
            <Sidebar collapsed={collapsed} onToggle={toggleSidebar} me={me} />
          </div>

          <div className="flex min-w-0 flex-1 flex-col">
            <TopBar me={me} onToggleWatchlist={toggleWatchlist} watchlistOpen={watchlistOpen} />
            {/* A readable measure: on a very wide screen the page stops stretching and sits centred, so cards
                and tables keep a comfortable line length instead of spanning 2,000px. The bottom padding
                clears the phone tab bar (0 from md up). */}
            <main className="mx-auto min-h-0 w-full 3xl:max-w-[1840px] max-w-[1600px] flex-1 pb-[var(--bottom-nav-h)]">
              {children}
            </main>
          </div>

          {isDesktopRail && railOpen ? (
            <div className="sticky top-0 h-[100dvh] shrink-0">
              <WatchlistRail
                onClose={closeRail}
                width={watchlistWidth}
                onWidthChange={setWatchlistWidth}
              />
            </div>
          ) : null}

          {isDesktopRail ? null : <WatchlistSheet open={sheetOpen} onClose={closeSheet} />}

          <MobileTabBar drawerOpen={drawerOpen} onOpenDrawer={() => setDrawerOpen(true)} />
          <MobileDrawer open={drawerOpen} onOpenChange={setDrawerOpen} me={me} />

          {modal}
          <WelcomePullDialog />
          <Toaster />
          <RateLimitNotice />
          <QueryFailureNotice />
          <TabStripScroll />
        </div>
      )}
    </AuthGuard>
  );
}

/** The watchlist below `lg`: a right-edge sheet (full width on a phone, 24rem on a tablet). */
function WatchlistSheet({ open, onClose }: { open: boolean; onClose: () => void }): ReactNode {
  const t = useTranslations("shell.watchlist");
  return (
    <Dialog.Root open={open} onOpenChange={(next) => (next ? undefined : onClose())}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-[var(--overlay)]" />
        <Dialog.Content
          aria-describedby={undefined}
          className="fixed inset-y-0 right-0 z-50 flex w-[min(24rem,100vw)] flex-col bg-[var(--surface)] pt-[env(safe-area-inset-top)] pr-[env(safe-area-inset-right)] pb-[env(safe-area-inset-bottom)] shadow-[var(--shadow-3)]"
        >
          <Dialog.Title className="sr-only">{t("toggle")}</Dialog.Title>
          <div className="min-h-0 flex-1">
            <WatchlistRail onClose={onClose} width={384} onWidthChange={() => undefined} sheet />
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
