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

/** Sidebar collapse state: persisted per user via localStorage, auto-collapses
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

const WATCHLIST_WIDTH_KEY = "miru-watchlist-width";
const WATCHLIST_WIDTH_DEFAULT = 300;
const WATCHLIST_WIDTH_MIN = 240;
const WATCHLIST_WIDTH_MAX = 480;

function readWidth(): number {
  try {
    const raw = localStorage.getItem(WATCHLIST_WIDTH_KEY);
    const parsed = raw === null ? Number.NaN : Number(raw);
    if (Number.isNaN(parsed)) return WATCHLIST_WIDTH_DEFAULT;
    return Math.min(WATCHLIST_WIDTH_MAX, Math.max(WATCHLIST_WIDTH_MIN, parsed));
  } catch {
    return WATCHLIST_WIDTH_DEFAULT;
  }
}

function writeWidth(width: number): void {
  try {
    localStorage.setItem(WATCHLIST_WIDTH_KEY, String(width));
  } catch {
    // persistence is best-effort; the in-session width still works
  }
}

/** Watchlist rail width (px), persisted, clamped to `[240, 480]` — the resize-handle's own
 * bounds (`ResizableRailHandle`). Read after mount (same hydration-mismatch avoidance as
 * `useSidebarCollapsed`), default 300px on first load. */
export function useWatchlistWidth(): {
  width: number;
  setWidth: (width: number) => void;
} {
  const [width, setWidthState] = useState(WATCHLIST_WIDTH_DEFAULT);

  useEffect(() => {
    setWidthState(readWidth());
  }, []);

  const setWidth = useCallback((next: number): void => {
    const clamped = Math.min(WATCHLIST_WIDTH_MAX, Math.max(WATCHLIST_WIDTH_MIN, next));
    setWidthState(clamped);
    writeWidth(clamped);
  }, []);

  return { width, setWidth };
}

export { WATCHLIST_WIDTH_MAX, WATCHLIST_WIDTH_MIN };

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
