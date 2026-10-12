"use client";

import { useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { SETTINGS_NAV_ITEMS } from "@/components/settings/settings-nav-items";
import { Link, usePathname } from "@/i18n/navigation";
import { cn } from "@/lib/cn";

/** Left sub-page nav for the Settings two-pane layout:
 * `divide-y --border` rows, active = `--accent-weak` pill + 2px `--accent` edge bar. */
export function SettingsNav(): ReactNode {
  const t = useTranslations("settings.nav");
  const pathname = usePathname();

  return (
    <nav aria-label={t("label")} className="shrink-0 lg:w-full">
      <ul className="surface-card flex divide-x divide-[var(--border)] overflow-x-auto [scrollbar-width:none] lg:block lg:divide-x-0 lg:divide-y-0 lg:overflow-visible lg:rounded-none lg:border-0 lg:bg-transparent">
        {SETTINGS_NAV_ITEMS.map((item) => {
          const Icon = item.icon;
          const active = pathname === item.href || pathname.startsWith(`${item.href}/`);
          return (
            <li key={item.href} className="shrink-0 lg:shrink">
              <Link
                href={item.href}
                replace
                aria-current={active ? "page" : undefined}
                className={cn(
                  "relative flex min-h-11 items-center gap-3 px-3 text-sm transition-colors",
                  "lg:mb-1 lg:rounded-[var(--radius-button)]",
                  active
                    ? "bg-[var(--accent-weak)] text-[var(--text)]"
                    : "text-[var(--text-muted)] hover:bg-[var(--surface-2)] hover:text-[var(--text)]",
                )}
              >
                {active ? (
                  <span className="absolute top-1.5 bottom-1.5 left-0 w-0.5 rounded-full bg-[var(--accent)]" />
                ) : null}
                <Icon
                  size={18}
                  weight="regular"
                  aria-hidden
                  className={active ? "shrink-0 text-[var(--accent)]" : "shrink-0"}
                />
                <span className="font-medium">{t(item.messageKey)}</span>
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
