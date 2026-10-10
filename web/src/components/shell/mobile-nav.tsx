"use client";

import { DotsThreeIcon } from "@phosphor-icons/react";
import * as Dialog from "@radix-ui/react-dialog";
import { useTranslations } from "next-intl";
import { type ReactNode, useEffect } from "react";
import { LanguageSwitcher } from "@/components/shell/language-switcher";
import { NAV_ITEMS } from "@/components/shell/nav-items";
import { Sidebar } from "@/components/shell/sidebar";
import { ThemeToggle } from "@/components/shell/theme-toggle";
import { Link, usePathname } from "@/i18n/navigation";
import { cn } from "@/lib/cn";
import type { MeResponse } from "@/types/account";

/**
 * Phone navigation (below `md`, 768px): a bottom tab bar for the four primary destinations plus a
 * "more" tab that opens the full sidebar (recent chats, account, settings) as a left drawer. The
 * bar sits in the thumb zone, pads for the home indicator (`env(safe-area-inset-bottom)`) and its
 * height is the shared `--bottom-nav-h` token pages subtract from the viewport.
 */
export function MobileTabBar({
  drawerOpen,
  onOpenDrawer,
}: {
  drawerOpen: boolean;
  onOpenDrawer: () => void;
}): ReactNode {
  const t = useTranslations();
  const pathname = usePathname();

  return (
    <nav
      aria-label={t("nav.mobile")}
      className={cn(
        "glass fixed inset-x-0 bottom-0 z-40 flex border-[var(--border)] border-t md:hidden",
        "bg-[color-mix(in_srgb,var(--surface)_88%,transparent)] backdrop-blur-[20px]",
        "pb-[env(safe-area-inset-bottom)] pl-[env(safe-area-inset-left)] pr-[env(safe-area-inset-right)]",
      )}
    >
      {NAV_ITEMS.map((item) => {
        const active = pathname.startsWith(item.href);
        const Icon = item.icon;
        return (
          <Link
            key={item.href}
            href={item.href}
            aria-current={active ? "page" : undefined}
            className={cn(
              "flex min-h-[3.75rem] min-w-0 flex-1 flex-col items-center justify-center gap-0.5 px-1",
              "text-[11px] transition-colors",
              active ? "text-[var(--accent-text)]" : "text-[var(--text-muted)]",
            )}
          >
            <Icon size={22} weight={active ? "fill" : "regular"} aria-hidden />
            <span className="max-w-full truncate">{t(`nav.${item.messageKey}.label`)}</span>
          </Link>
        );
      })}
      <button
        type="button"
        onClick={onOpenDrawer}
        aria-haspopup="dialog"
        aria-expanded={drawerOpen}
        className="flex min-h-[3.75rem] min-w-0 flex-1 flex-col items-center justify-center gap-0.5 px-1 text-[11px] text-[var(--text-muted)]"
      >
        <DotsThreeIcon size={22} weight="bold" aria-hidden />
        <span className="max-w-full truncate">{t("nav.more")}</span>
      </button>
    </nav>
  );
}

/** The full sidebar as a left drawer for phones. Closes on navigation. Theme + language live here
 * on the narrowest screens, where the top bar has no room for them. */
export function MobileDrawer({
  open,
  onOpenChange,
  me,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  me: MeResponse;
}): ReactNode {
  const t = useTranslations();
  const pathname = usePathname();

  // biome-ignore lint/correctness/useExhaustiveDependencies: close whenever the route changes
  useEffect(() => {
    onOpenChange(false);
  }, [pathname, onOpenChange]);

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-[var(--overlay)] md:hidden" />
        <Dialog.Content
          aria-describedby={undefined}
          className={cn(
            "fixed inset-y-0 left-0 z-50 flex w-[min(20rem,88vw)] flex-col bg-[var(--surface)] shadow-[var(--shadow-3)] md:hidden",
            "pt-[env(safe-area-inset-top)] pl-[env(safe-area-inset-left)]",
          )}
        >
          <Dialog.Title className="sr-only">{t("nav.primary")}</Dialog.Title>
          <div className="min-h-0 flex-1">
            <Sidebar collapsed={false} onToggle={() => onOpenChange(false)} me={me} fluid />
          </div>
          <div className="flex shrink-0 items-center justify-between gap-2 border-[var(--border)] border-t px-3 py-2 pb-[max(0.5rem,env(safe-area-inset-bottom))] sm:hidden">
            <LanguageSwitcher />
            <ThemeToggle />
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
