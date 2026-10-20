"use client";

import { MagnifyingGlassIcon, SignOutIcon, StarIcon, UserIcon } from "@phosphor-icons/react";
import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
import { useTranslations } from "next-intl";
import { type ReactNode, useEffect, useState } from "react";
import { MarketTabSwitcher } from "@/components/market/market-tabs";
import { SearchPalette } from "@/components/search/search-palette";
import { useSearchPaletteStore } from "@/components/search/search-palette-store";
import { LanguageSwitcher } from "@/components/shell/language-switcher";
import { NotificationsBell } from "@/components/shell/notifications-bell";
import { ThemeToggle } from "@/components/shell/theme-toggle";
import { usePathname, useRouter } from "@/i18n/navigation";
import { logout } from "@/lib/api/auth";
import { cn } from "@/lib/cn";
import type { MeResponse, Tier } from "@/types/account";

/**
 * Sticky glass top bar. Carries the 搜尋股票 command-palette
 * placeholder (visual only — stock search is future Stock/Screener work), the 台股/美股/ETF
 * market-tab switcher (only on `/market` — everywhere else it would be a dead control, since
 * only the Market page reads its selection), the notifications inbox bell, the real tier badge
 * (from `/me`), the account menu (real sign-out via `logout()`), plus the existing theme +
 * language controls and the watchlist rail toggle. Glass falls back to solid `--surface` under
 * `prefers-reduced-transparency` (globals.css `.glass`).
 */
export function TopBar({
  me,
  onToggleWatchlist,
  watchlistOpen,
}: Readonly<{
  me: MeResponse;
  onToggleWatchlist: () => void;
  watchlistOpen: boolean;
}>): ReactNode {
  const t = useTranslations("shell");
  const pathname = usePathname();
  const onMarketPage = pathname === "/market";

  return (
    <header
      className={cn(
        "glass sticky top-0 z-30 shrink-0 pt-[env(safe-area-inset-top)]",
        "pr-[max(0.75rem,env(safe-area-inset-right))] pl-[max(0.75rem,env(safe-area-inset-left))] sm:px-4",
        "border-[var(--border)] border-b bg-[color-mix(in_srgb,var(--surface)_72%,transparent)]",
        "backdrop-blur-[20px] backdrop-saturate-150",
      )}
    >
      <div className="flex h-14 items-center gap-1 xs:gap-2">
        <SearchPlaceholder />
        {onMarketPage ? (
          <div className="hidden md:block">
            <MarketTabSwitcher />
          </div>
        ) : null}

        <div className="ml-auto flex shrink-0 items-center gap-0.5 sm:gap-2">
          <NotificationsBell />
          <TierBadge tier={me.entitlements.tier} />
          <div className="mx-0.5 hidden h-6 w-px bg-[var(--border)] sm:block" />
          {/* Theme + language fold into the phone drawer below `sm`, where the bar has no room. */}
          <div className="hidden items-center gap-1 sm:flex sm:gap-2">
            <LanguageSwitcher />
            <ThemeToggle />
          </div>
          <button
            type="button"
            onClick={onToggleWatchlist}
            aria-label={t("watchlist.toggle")}
            aria-pressed={watchlistOpen}
            title={t("watchlist.toggle")}
            className={cn(
              "inline-flex size-11 items-center justify-center rounded-[var(--radius-button)]",
              "transition-colors hover:bg-[var(--surface-2)]",
              // Lit gold while the watchlist is open; back to the quiet default when it closes.
              watchlistOpen
                ? "text-[var(--star)]"
                : "text-[var(--text-muted)] hover:text-[var(--text)]",
            )}
          >
            <StarIcon size={20} weight={watchlistOpen ? "fill" : "regular"} />
          </button>
          <AccountMenu me={me} />
        </div>
      </div>
      {/* Phones/tablets: the market switcher gets its own row so it never fights the actions. */}
      {onMarketPage ? (
        <div className="overflow-x-auto pb-2 lg:hidden">
          <MarketTabSwitcher />
        </div>
      ) : null}
    </header>
  );
}

/** The 股票搜尋 command-palette trigger. Opens `SearchPalette` (公司/ETF, TW roster + US
 * yfinance search) on click or on the global Cmd/Ctrl+K shortcut. Open state lives in
 * `useSearchPaletteStore` (not local state) so other surfaces — e.g. the `/stock` empty
 * state's "搜尋股票" CTA — can open the same palette. */
function SearchPlaceholder(): ReactNode {
  const t = useTranslations("shell.search");
  const open = useSearchPaletteStore((s) => s.open);
  const setOpen = useSearchPaletteStore((s) => s.setOpen);

  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent): void => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setOpen(true);
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [setOpen]);

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        aria-label={t("label")}
        className={cn(
          "flex min-h-11 min-w-0 max-w-md flex-1 items-center gap-2 rounded-[var(--radius-input)] px-2.5 sm:px-3",
          "border border-[var(--border)] bg-[var(--surface)] text-left text-[var(--text-muted)] text-sm",
          "transition-colors hover:border-[var(--text-muted)]",
        )}
      >
        <MagnifyingGlassIcon size={16} weight="regular" aria-hidden />
        <span className="flex-1 truncate">{t("placeholder")}</span>
        <kbd
          className={cn(
            "hidden rounded-[6px] border border-[var(--border)] bg-[var(--surface-2)] px-1.5",
            "py-0.5 font-num text-[11px] text-[var(--text-muted)] xl:inline",
          )}
        >
          {t("hint")}
        </kbd>
      </button>
      <SearchPalette open={open} onClose={() => setOpen(false)} />
    </>
  );
}

const TIER_STYLE: Record<Tier, string> = {
  free: "border-[var(--border)] text-[var(--text-muted)]",
  premium: "border-[var(--accent)] text-[var(--accent-text)]",
  pro: "border-[var(--accent)] text-[var(--accent-text)]",
};

/** Real tier badge from `/me` entitlements — never fakes premium. */
function TierBadge({ tier }: Readonly<{ tier: Tier }>): ReactNode {
  const t = useTranslations("shell.tier");
  return (
    <span
      className={cn(
        "hidden items-center rounded-[var(--radius-pill)] border px-2.5 py-1 font-medium text-[12px] sm:inline-flex",
        TIER_STYLE[tier],
      )}
    >
      {t(tier)}
    </span>
  );
}

/** Radix `DropdownMenu` (not a hand-rolled `mousedown`/`keydown`-listener div) so `Esc`-close and
 * focus-return-to-trigger come for free, matching every other menu in the shell. */
function AccountMenu({ me }: Readonly<{ me: MeResponse }>): ReactNode {
  const t = useTranslations("shell.account");
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [pending, setPending] = useState(false);

  // logout() is best-effort and always resolves (never rejects) — see its own contract.
  const onSignOut = (): void => {
    setPending(true);
    void logout().then(() => router.replace("/"));
  };

  const name = me.user.display_name || me.user.email || t("label");

  return (
    <DropdownMenu.Root open={open} onOpenChange={setOpen}>
      <DropdownMenu.Trigger asChild>
        <button
          type="button"
          aria-label={t("menu")}
          className={cn(
            "inline-flex size-11 shrink-0 items-center justify-center rounded-full",
            "bg-[var(--surface-2)] text-[var(--text-muted)] transition-colors",
            "hover:text-[var(--text)]",
            "!outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]",
          )}
        >
          <UserIcon size={20} weight="regular" aria-hidden />
        </button>
      </DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content
          side="bottom"
          align="end"
          sideOffset={8}
          className={cn(
            "z-50 w-56 rounded-[var(--radius-card)] border border-[var(--border)]",
            "bg-[var(--surface)] p-2 shadow-lg",
          )}
        >
          <div className="border-[var(--border)] border-b px-3 py-2">
            <p className="text-[12px] text-[var(--text-muted)]">{t("signedInAs")}</p>
            <p className="truncate font-medium text-[var(--text)] text-sm">{name}</p>
          </div>
          <DropdownMenu.Item
            onSelect={onSignOut}
            disabled={pending}
            className={cn(
              "mt-1 flex min-h-11 w-full cursor-pointer items-center gap-2 rounded-[var(--radius-button)] px-3",
              "text-[var(--text)] text-sm outline-none transition-colors",
              "data-[highlighted]:bg-[var(--surface-2)]",
              "data-[disabled]:cursor-not-allowed data-[disabled]:opacity-50",
            )}
          >
            <SignOutIcon size={18} weight="regular" aria-hidden />
            {t("signOut")}
          </DropdownMenu.Item>
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  );
}
