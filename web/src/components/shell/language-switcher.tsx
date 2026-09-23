"use client";

import { TranslateIcon } from "@phosphor-icons/react";
import { useSearchParams } from "next/navigation";
import { useLocale, useTranslations } from "next-intl";
import { type ReactNode, Suspense, useTransition } from "react";
import { usePathname, useRouter } from "@/i18n/navigation";
import { type AppLocale, routing } from "@/i18n/routing";
import { cn } from "@/lib/cn";

/** Language switcher. Uses next-intl locale-aware navigation, so switching is a plain client
 * navigation to the same route under the other locale prefix — the message set swaps without a
 * jarring full reload. Rendered as a compact segmented control so both options are one tap away
 * (a first-run decision per the design system, not buried in settings). Carries the current
 * search params through (`router.replace` only swaps the path/locale, never the query string on
 * its own) — without this, any page keeping UI state in the URL (e.g. the stock-detail page's
 * `?tab=`) silently resets to its default on every language switch.
 *
 * Wrapped in its own `Suspense` boundary since it reads `useSearchParams()` — Next.js requires
 * that boundary on any statically-prerendered route a caller mounts this in (e.g. the `/[locale]`
 * entry page), otherwise `next build` fails the export with a CSR-bailout error. The fallback
 * mirrors the control's static chrome so there's no layout shift while search params resolve. */
export function LanguageSwitcher(): ReactNode {
  return (
    <Suspense fallback={<LanguageSwitcherFallback />}>
      <LanguageSwitcherImpl />
    </Suspense>
  );
}

function LanguageSwitcherFallback(): ReactNode {
  return (
    <div
      className={cn(
        "inline-flex items-center gap-1 rounded-[var(--radius-pill)] border border-[var(--border)]",
        "bg-[var(--surface)] p-1",
      )}
    >
      <TranslateIcon
        size={16}
        weight="regular"
        aria-hidden
        className="ml-1 text-[var(--text-muted)]"
      />
    </div>
  );
}

function LanguageSwitcherImpl(): ReactNode {
  const t = useTranslations("topbar.language");
  const active = useLocale();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const router = useRouter();
  const [isPending, startTransition] = useTransition();

  const switchTo = (locale: AppLocale): void => {
    if (locale === active) {
      return;
    }
    const query = searchParams.toString();
    startTransition(() => {
      router.replace(query ? `${pathname}?${query}` : pathname, { locale });
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
      <TranslateIcon
        size={16}
        weight="regular"
        aria-hidden
        className="ml-1 text-[var(--text-muted)]"
      />
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
              "min-h-11 rounded-[var(--radius-pill)] px-3 text-sm transition-colors",
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
