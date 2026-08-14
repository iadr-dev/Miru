"use client";

import { useCallback, useEffect, useState } from "react";

const SIDEBAR_KEY = "miru-sidebar-collapsed";
const WATCHLIST_KEY = "miru-watchlist-open";

function readBool(key: string, fallback: boolean): boolean {
  try {
    const raw = localStorage.getItem(key);
    return raw === null ? fallback : raw === "1";
  } catch {
    return fallback;
  }
}

function writeBool(key: string, value: boolean): void {
  try {
    localStorage.setItem(key, value ? "1" : "0");
  } catch {
    // persistence is best-effort; the in-session toggle still works
  }
}

/** Sidebar collapse state (design-system §6.2): persisted per user via localStorage, auto-collapses
 * at the tablet breakpoint. Reads persisted state after mount to avoid a hydration mismatch (the
 * server can't know localStorage), defaulting to expanded on desktop. */
export function useSidebarCollapsed(): {
  collapsed: boolean;
  toggle: () => void;
} {
  const [collapsed, setCollapsed] = useState(false);
  const [userSet, setUserSet] = useState(false);

  useEffect(() => {
    setCollapsed(readBool(SIDEBAR_KEY, false));
  }, []);

  // Auto-collapse at the 768–1279px tablet band unless the user has explicitly chosen this session.
  useEffect(() => {
    const tablet = window.matchMedia("(min-width: 768px) and (max-width: 1279px)");
    const apply = (): void => {
      if (!userSet && tablet.matches) {
        setCollapsed(true);
      }
    };
    apply();
    tablet.addEventListener("change", apply);
    return () => tablet.removeEventListener("change", apply);
  }, [userSet]);

  const toggle = useCallback(() => {
    setUserSet(true);
    setCollapsed((prev) => {
      const next = !prev;
      writeBool(SIDEBAR_KEY, next);
      return next;
    });
  }, []);

  return { collapsed, toggle };
}

/** Right watchlist rail open/closed, persisted. Default closed on first load (rail is opt-in). */
export function useWatchlistOpen(): {
  open: boolean;
  toggle: () => void;
  close: () => void;
} {
  const [open, setOpen] = useState(false);

  useEffect(() => {
    setOpen(readBool(WATCHLIST_KEY, false));
  }, []);

  const toggle = useCallback(() => {
    setOpen((prev) => {
      const next = !prev;
      writeBool(WATCHLIST_KEY, next);
      return next;
    });
  }, []);

  const close = useCallback(() => {
    setOpen(false);
    writeBool(WATCHLIST_KEY, false);
  }, []);

  return { open, toggle, close };
}
