"use client";

import {
  ArrowCircleUpIcon,
  CaretUpDownIcon,
  CheckIcon,
  GearIcon,
  SignOutIcon,
  TranslateIcon,
} from "@phosphor-icons/react";
import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
import { useLocale, useTranslations } from "next-intl";
import { type ReactNode, useState } from "react";
import { Link, usePathname, useRouter } from "@/i18n/navigation";
import { type AppLocale, routing } from "@/i18n/routing";
import { logout } from "@/lib/api/auth";
import { cn } from "@/lib/cn";
import type { MeResponse, Tier } from "@/types/account";

const TIER_LABEL_STYLE: Record<Tier, string> = {
  free: "text-[var(--text-muted)]",
  premium: "text-[var(--accent-text)]",
  pro: "text-[var(--accent-text)]",
};

/** Sidebar-bottom account chip (avatar + name + plan) that opens the account menu — replaces the
 * old Sisi footer + separate Settings nav row. Settings/Upgrade route to the real pages (Settings
 * opens as the `@modal` overlay via normal `Link` navigation, intercepted automatically); Language
 * is a submenu so switching locale doesn't require leaving the menu. */
export function AccountChip({ me, collapsed }: { me: MeResponse; collapsed: boolean }): ReactNode {
  const t = useTranslations("shell.account");
  const tTier = useTranslations("shell.tier");
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [pending, setPending] = useState(false);

  const name = me.user.display_name || me.user.email || t("label");
  const initials = initialsFor(name);
  const tier = me.entitlements.tier;

  // logout() is best-effort and always resolves (never rejects) — see its own contract.
  const onSignOut = (): void => {
    setPending(true);
    void logout().then(() => router.replace("/"));
  };

  return (
    <DropdownMenu.Root open={open} onOpenChange={setOpen}>
      <DropdownMenu.Trigger asChild>
        <button
          type="button"
          aria-label={t("menu")}
          className={cn(
            "flex min-h-11 w-full items-center gap-2 rounded-[var(--radius-button)] px-2 transition-colors",
            "text-[var(--text)] hover:bg-[var(--surface-2)]",
            "!outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]",
            collapsed && "justify-center px-0",
          )}
        >
          <span
            aria-hidden
            className="inline-flex size-7 shrink-0 items-center justify-center rounded-full bg-[var(--accent-weak)] font-medium text-[12px] text-[var(--accent-text)]"
          >
            {initials}
          </span>
          {collapsed ? null : (
            <>
              <span className="flex min-w-0 flex-1 flex-col items-start leading-tight">
                <span className="truncate font-medium text-[13px]">{name}</span>
                <span className={cn("truncate text-[12px]", TIER_LABEL_STYLE[tier])}>
                  {tTier(tier)}
                </span>
              </span>
              <CaretUpDownIcon
                size={14}
                weight="regular"
                aria-hidden
                className="shrink-0 text-[var(--text-muted)]"
              />
            </>
          )}
        </button>
      </DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content
          side="top"
          align="start"
          sideOffset={8}
          className={cn(
            "z-50 w-56 rounded-[var(--radius-card)] border border-[var(--border)]",
            "bg-[var(--surface)] p-1.5 shadow-2xl",
          )}
        >
          <div className="border-[var(--border)] border-b px-2.5 pb-2">
            <p className="truncate text-[12px] text-[var(--text-muted)]">{me.user.email ?? name}</p>
          </div>
          <div className="pt-1">
            <MenuLink href="/settings" icon={GearIcon} label={t("settings")} />
            <LanguageSubmenu />
            <MenuLink href="/settings/billing" icon={ArrowCircleUpIcon} label={t("upgrade")} />
          </div>
          <DropdownMenu.Separator className="my-1 h-px bg-[var(--border)]" />
          <DropdownMenu.Item
            onSelect={onSignOut}
            disabled={pending}
            className={cn(
              "flex min-h-11 cursor-pointer items-center gap-2 rounded-[var(--radius-button)] px-2.5",
              "text-[var(--text)] text-sm outline-none transition-colors",
              "data-[disabled]:cursor-not-allowed data-[highlighted]:bg-[var(--surface-2)] data-[disabled]:opacity-50",
            )}
          >
            <SignOutIcon size={16} weight="regular" aria-hidden />
            {t("signOut")}
          </DropdownMenu.Item>
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  );
}

function MenuLink({
  href,
  icon: Icon,
  label,
}: {
  href: "/settings" | "/settings/billing";
  icon: typeof GearIcon;
  label: string;
}): ReactNode {
  return (
    <DropdownMenu.Item asChild>
      <Link
        href={href}
        className={cn(
          "flex min-h-11 items-center gap-2 rounded-[var(--radius-button)] px-2.5",
          "text-[var(--text)] text-sm outline-none transition-colors",
          "data-[highlighted]:bg-[var(--surface-2)]",
        )}
      >
        <Icon size={16} weight="regular" aria-hidden />
        {label}
      </Link>
    </DropdownMenu.Item>
  );
}

function LanguageSubmenu(): ReactNode {
  const t = useTranslations("shell.account");
  const tLang = useTranslations("topbar.language");
  const active = useLocale();
  const pathname = usePathname();
  const router = useRouter();

  const switchTo = (locale: AppLocale): void => {
    router.replace(pathname, { locale });
  };

  return (
    <DropdownMenu.Sub>
      <DropdownMenu.SubTrigger
        className={cn(
          "flex min-h-11 items-center gap-2 rounded-[var(--radius-button)] px-2.5",
          "text-[var(--text)] text-sm outline-none transition-colors",
          "data-[highlighted]:bg-[var(--surface-2)] data-[state=open]:bg-[var(--surface-2)]",
        )}
      >
        <TranslateIcon size={16} weight="regular" aria-hidden />
        <span className="flex-1">{t("language")}</span>
      </DropdownMenu.SubTrigger>
      <DropdownMenu.Portal>
        <DropdownMenu.SubContent
          sideOffset={4}
          className={cn(
            "z-50 w-44 rounded-[var(--radius-card)] border border-[var(--border)]",
            "bg-[var(--surface)] p-1.5 shadow-2xl",
          )}
        >
          {routing.locales.map((locale) => (
            <DropdownMenu.Item
              key={locale}
              onSelect={() => switchTo(locale)}
              className={cn(
                "flex min-h-11 cursor-pointer items-center gap-2 rounded-[var(--radius-button)] px-2.5",
                "text-[var(--text)] text-sm outline-none transition-colors",
                "data-[highlighted]:bg-[var(--surface-2)]",
              )}
            >
              <span className="flex-1">{tLang(locale)}</span>
              {locale === active ? (
                <CheckIcon size={14} weight="regular" className="text-[var(--accent)]" />
              ) : null}
            </DropdownMenu.Item>
          ))}
        </DropdownMenu.SubContent>
      </DropdownMenu.Portal>
    </DropdownMenu.Sub>
  );
}

function initialsFor(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) {
    return "?";
  }
  const first = parts[0]?.[0] ?? "";
  const last = parts.length > 1 ? (parts[parts.length - 1]?.[0] ?? "") : "";
  return (first + last).toUpperCase();
}
