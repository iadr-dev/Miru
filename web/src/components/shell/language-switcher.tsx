"use client";

import { Translate } from "@phosphor-icons/react";
import { useLocale, useTranslations } from "next-intl";
import { type ReactNode, useTransition } from "react";
import { usePathname, useRouter } from "@/i18n/navigation";
import { type AppLocale, routing } from "@/i18n/routing";
import { cn } from "@/lib/cn";

/** Language switcher. Uses next-intl locale-aware navigation, so switching is a plain client
 * navigation to the same route under the other locale prefix — the message set swaps without a
 * jarring full reload. Rendered as a compact segmented control so both options are one tap away
 * (a first-run decision per the design system, not buried in settings). */
export function LanguageSwitcher(): ReactNode {
  const t = useTranslations("topbar.language");
  const active = useLocale();
  const pathname = usePathname();
  const router = useRouter();
  const [isPending, startTransition] = useTransition();

  const switchTo = (locale: AppLocale): void => {
    if (locale === active) {
      return;
    }
    startTransition(() => {
      router.replace(pathname, { locale });
    });
  };

  return (
    <div
      className={cn(
        "inline-flex items-center gap-1 rounded-[var(--radius-pill)] border border-[var(--border)]",
        "bg-[var(--surface)] p-1",
      )}
      role="group"
      aria-label={t("label")}
    >
      <Translate size={16} weight="regular" aria-hidden className="ml-1 text-[var(--text-muted)]" />
      {routing.locales.map((locale) => {
        const isActive = locale === active;
        return (
          <button
            key={locale}
            type="button"
            onClick={() => switchTo(locale)}
            disabled={isPending}
            aria-pressed={isActive}
            aria-label={t("switchTo", { language: t(locale) })}
            className={cn(
              "min-h-9 rounded-[var(--radius-pill)] px-3 text-sm transition-colors",
              isActive
                ? "bg-[var(--accent-weak)] text-[var(--text)]"
                : "text-[var(--text-muted)] hover:text-[var(--text)]",
            )}
          >
            {locale === "zh-TW" ? "繁中" : "EN"}
          </button>
        );
      })}
    </div>
  );
}
