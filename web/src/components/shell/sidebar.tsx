"use client";

import { CaretLeftIcon, CaretRightIcon, PlusIcon } from "@phosphor-icons/react";
import { LayoutGroup, motion } from "motion/react";
import Image from "next/image";
import { useTranslations } from "next-intl";
import { type ReactNode, Suspense, useState } from "react";
import { AccountChip } from "@/components/shell/account-chip";
import { NAV_ITEMS, type NavItem } from "@/components/shell/nav-items";
import { RecentsList } from "@/components/shell/recents-list";
import { Link, usePathname, useRouter } from "@/i18n/navigation";
import { createThread } from "@/lib/api/threads";
import { cn } from "@/lib/cn";
import type { MeResponse } from "@/types/account";

/**
 * The collapsible left rail. Expanded 240px (icon + zh label + en
 * sublabel), collapsed 64px icon rail with `title` tooltips. Active = `--accent-weak` pill + 2px
 * `--accent` left edge bar + accent icon; a shared-layout indicator (`layoutId`) glides between
 * items on navigate. Width tween is `transition-all duration-300 ease-out`; reduced-motion snaps
 * (globals.css zeroes transition-duration). Recent conversations are folded directly into this
 * rail below the primary nav (Claude-style, replacing the old separate Conversations panel next
 * to the chat canvas); the account chip at the bottom replaces the old Sisi footer + Settings row
 * — Settings now opens as a modal overlay from that menu instead of a dedicated nav destination.
 */
export function Sidebar({
  collapsed,
  onToggle,
  me,
  fluid = false,
}: Readonly<{
  collapsed: boolean;
  onToggle: () => void;
  me: MeResponse;
  /** Fill the parent's width instead of the fixed rail widths (the phone drawer). */
  fluid?: boolean;
}>): ReactNode {
  const t = useTranslations();
  const pathname = usePathname();
  const router = useRouter();
  const [creating, setCreating] = useState(false);

  const startNewChat = async (): Promise<void> => {
    setCreating(true);
    try {
      const thread = await createThread();
      router.push(`/chat?thread=${encodeURIComponent(thread.id)}`);
    } catch {
      // Fall back to a fresh /chat (the first send auto-creates a thread server-side anyway).
      router.push("/chat");
    } finally {
      setCreating(false);
    }
  };

  return (
    <nav
      aria-label={t("nav.primary")}
      className={cn(
        "flex h-full flex-col border-[var(--border)] border-r bg-[var(--surface)]",
        "transition-all duration-300 ease-out",
        fluid ? "w-full" : collapsed ? "w-16" : "w-60",
      )}
    >
      {/* Header: logo + collapse toggle */}
      <div
        className={cn(
          "flex h-14 shrink-0 items-center gap-2 border-[var(--border)] border-b px-3",
          collapsed ? "justify-center" : "justify-between",
        )}
      >
        {collapsed ? null : (
          <div className="flex min-w-0 items-center gap-2">
            <Image
              src="/logo.png"
              alt={t("brand.logoAlt")}
              width={28}
              height={28}
              className="size-7 shrink-0 rounded-[8px]"
            />
            <span className="truncate font-semibold text-[15px] text-[var(--text)] tracking-tight">
              {t("brand.name")}
            </span>
          </div>
        )}
        <button
          type="button"
          onClick={onToggle}
          aria-label={collapsed ? t("nav.expand") : t("nav.collapse")}
          aria-expanded={!collapsed}
          title={collapsed ? t("nav.expand") : t("nav.collapse")}
          className={cn(
            "inline-flex size-11 items-center justify-center rounded-[var(--radius-button)]",
            "text-[var(--text-muted)] transition-colors hover:bg-[var(--surface-2)] hover:text-[var(--text)]",
          )}
        >
          {collapsed ? (
            <CaretRightIcon size={18} weight="regular" />
          ) : (
            <CaretLeftIcon size={18} weight="regular" />
          )}
        </button>
      </div>

      {/* New-chat CTA */}
      <div className="px-3 pt-3">
        <button
          type="button"
          onClick={startNewChat}
          disabled={creating}
          title={collapsed ? t("nav.newChat") : undefined}
          className={cn(
            "inline-flex min-h-11 w-full items-center gap-2 rounded-[var(--radius-button)]",
            // A tonal button, not a solid block: the sidebar's orange stays a quiet signature, and
            // the solid fill is kept for the actions inside a page (send, save, confirm).
            "border border-[color-mix(in_srgb,var(--accent)_45%,transparent)] bg-[var(--accent-weak)]",
            "font-medium text-[15px] text-[var(--accent-text)]",
            "transition-[background-color,border-color] duration-[var(--dur-base)] ease-[var(--ease-out)]",
            "hover:border-[var(--accent)] hover:bg-[color-mix(in_srgb,var(--accent)_22%,var(--accent-weak))]",
            "active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-50",
            collapsed ? "justify-center px-0" : "justify-start px-4",
          )}
        >
          <PlusIcon size={18} weight="regular" aria-hidden />
          {collapsed ? <span className="sr-only">{t("nav.newChat")}</span> : t("nav.newChat")}
        </button>
      </div>

      {/* Primary nav */}
      <LayoutGroup id="sidebar-nav">
        <ul className="flex shrink-0 flex-col gap-1 px-3 py-3">
          {NAV_ITEMS.map((item) => (
            <li key={item.href}>
              <NavRow item={item} collapsed={collapsed} active={pathname.startsWith(item.href)} />
            </li>
          ))}
        </ul>
      </LayoutGroup>

      {/* Recents */}
      {collapsed ? null : (
        <Suspense fallback={null}>
          <RecentsList />
        </Suspense>
      )}

      {/* Footer: account chip */}
      <div className="shrink-0 border-[var(--border)] border-t p-3">
        <AccountChip me={me} collapsed={collapsed} />
      </div>
    </nav>
  );
}

function NavRow({
  item,
  collapsed,
  active,
}: Readonly<{
  item: NavItem;
  collapsed: boolean;
  active: boolean;
}>): ReactNode {
  const t = useTranslations("nav");
  const Icon = item.icon;
  const label = t(`${item.messageKey}.label`);
  const sublabel = t(`${item.messageKey}.sublabel`);

  return (
    <Link
      href={item.href}
      aria-current={active ? "page" : undefined}
      aria-label={collapsed ? label : undefined}
      title={collapsed ? label : undefined}
      className={cn(
        "group relative flex min-h-11 items-center gap-3 rounded-[var(--radius-button)] px-3",
        "transition-colors",
        active
          ? "bg-[var(--accent-weak)] text-[var(--text)]"
          : "text-[var(--text-muted)] hover:bg-[var(--surface-2)] hover:text-[var(--text)]",
        collapsed && "justify-center px-0",
      )}
    >
      {active ? (
        <motion.span
          layoutId="nav-active-edge"
          className="absolute top-1.5 bottom-1.5 left-0 w-0.5 rounded-full bg-[var(--accent)]"
          transition={{ type: "spring", stiffness: 220, damping: 24 }}
        />
      ) : null}
      <Icon
        size={20}
        weight="regular"
        aria-hidden
        className={active ? "shrink-0 text-[var(--accent)]" : "shrink-0"}
      />
      {collapsed ? null : (
        <span className="flex min-w-0 flex-col leading-tight">
          <span className="truncate font-medium text-[15px]">{label}</span>
          <span className="truncate text-[12px] text-[var(--text-muted)]">{sublabel}</span>
        </span>
      )}
    </Link>
  );
}
