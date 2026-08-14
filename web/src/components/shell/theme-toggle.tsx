"use client";

import { MoonIcon, SunIcon } from "@phosphor-icons/react";
import { useTranslations } from "next-intl";
import { type ReactNode, useEffect, useState } from "react";
import { cn } from "@/lib/cn";
import { isTheme, THEME_STORAGE_KEY, type Theme } from "@/lib/theme";

/** Theme toggle. The initial theme is already on <html data-theme> from the no-flash boot script;
 * this control reads it back after mount, then flips + persists. Accessible name is always present
 * (never an icon alone). */
export function ThemeToggle(): ReactNode {
  const t = useTranslations("topbar.theme");
  const [theme, setThemeState] = useState<Theme>("dark");

  useEffect(() => {
    const current = document.documentElement.getAttribute("data-theme");
    if (isTheme(current)) {
      setThemeState(current);
    }
  }, []);

  const setTheme = (next: Theme): void => {
    document.documentElement.setAttribute("data-theme", next);
    try {
      localStorage.setItem(THEME_STORAGE_KEY, next);
    } catch {
      // persistence is best-effort; the in-session toggle still works
    }
    setThemeState(next);
  };

  const next: Theme = theme === "dark" ? "light" : "dark";
  const label = next === "dark" ? t("toDark") : t("toLight");

  return (
    <button
      type="button"
      onClick={() => setTheme(next)}
      aria-label={label}
      title={label}
      className={cn(
        "inline-flex size-11 items-center justify-center rounded-[var(--radius-button)]",
        "text-[var(--text-muted)] transition-colors hover:bg-[var(--surface-2)] hover:text-[var(--text)]",
      )}
    >
      {theme === "dark" ? (
        <MoonIcon size={20} weight="regular" />
      ) : (
        <SunIcon size={20} weight="regular" />
      )}
    </button>
  );
}
