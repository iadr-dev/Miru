"use client";

import { LockIcon } from "@phosphor-icons/react";
import { useTranslations } from "next-intl";
import { type ReactNode, useState } from "react";
import { AuthModal } from "@/components/auth/auth-modal";
import { useIsAnonymous } from "@/hooks/use-is-anonymous";
import { Link } from "@/i18n/navigation";
import { cn } from "@/lib/cn";

/** The billing / plans page the upgrade CTA opens. */
const BILLING_HREF = "/settings/billing" as const;

const CTA_CLASS = cn(
  "inline-flex min-h-11 shrink-0 items-center justify-center rounded-[var(--radius-button)] px-4",
  "bg-[var(--accent)] font-medium text-[13px] text-[var(--bg)] transition-colors duration-150",
  "hover:opacity-90 active:scale-[0.98]",
  "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--accent)]",
);

const CHIP_CLASS = cn(
  "inline-flex min-h-11 items-center gap-1.5 rounded-[var(--radius-pill)] border border-[var(--border)]",
  "px-3 text-[12.5px] text-[var(--text-muted)] transition-colors hover:text-[var(--text)]",
  "focus-visible:outline-2 focus-visible:outline-[var(--accent)]",
);

const ICON_CLASS = cn(
  "inline-flex size-11 shrink-0 items-center justify-center rounded-[var(--radius-input)]",
  "text-[var(--text-muted)] transition-colors hover:bg-[var(--surface-3)] hover:text-[var(--text)]",
  "focus-visible:outline-2 focus-visible:outline-[var(--accent)]",
);

type Variant = "row" | "chip" | "icon";

/**
 * The one inline "more on Pro" prompt (web/OVERVIEW.md §4, §8: a locked capability is an upgrade
 * moment, never an error). The server decides what a plan sees and sends only that; this renders a
 * calm prompt where the rest would continue, with the caller's static copy and a hidden count the
 * server reported. It never carries, blurs or hints at withheld content.
 *
 * - `row`: a bordered row (lock icon, `message`, one-line `benefit`, CTA) at the end of a list.
 * - `chip`: one compact pill for inline strips.
 * - `icon`: a 44px lock button for a spot that is a single control (e.g. the sources button).
 *
 * The CTA is "upgrade" (billing page) for a signed-in caller and the sign-in flow for an
 * anonymous one.
 */
export function FeatureLock({
  message,
  benefit,
  variant = "row",
  className,
}: {
  /** What is withheld, e.g. "3 more companies". Static copy plus the server's count. */
  readonly message: string;
  /** One short line on what Pro adds. Row variant only. */
  readonly benefit?: string | undefined;
  readonly variant?: Variant;
  readonly className?: string | undefined;
}): ReactNode {
  const t = useTranslations("featureLock");
  const anonymous = useIsAnonymous();
  const [signInOpen, setSignInOpen] = useState(false);
  const modal = anonymous ? (
    <AuthModal open={signInOpen} intent="signIn" onClose={() => setSignInOpen(false)} />
  ) : null;

  if (variant === "row") {
    return (
      <div
        data-feature-lock
        className={cn(
          "@container flex flex-col gap-2 rounded-[var(--radius-input)] border border-[var(--border)]",
          "@md:flex-row @md:items-center @md:justify-between @md:gap-4 bg-[var(--surface-2)] p-3",
          className,
        )}
      >
        <div className="flex min-w-0 items-start gap-2.5">
          <LockIcon
            size={16}
            weight="regular"
            aria-hidden
            className="mt-0.5 shrink-0 text-[var(--text-muted)]"
          />
          <p className="min-w-0 break-words text-[13px] text-[var(--text)] leading-snug">
            <span className="font-medium">{message}</span>
            {benefit ? <span className="text-[var(--text-muted)]"> · {benefit}</span> : null}
          </p>
        </div>
        {anonymous ? (
          <button type="button" onClick={() => setSignInOpen(true)} className={CTA_CLASS}>
            {t("signIn")}
          </button>
        ) : (
          <Link href={BILLING_HREF} className={CTA_CLASS}>
            {t("upgrade")}
          </Link>
        )}
        {modal}
      </div>
    );
  }

  const isIcon = variant === "icon";
  const content = (
    <>
      <LockIcon size={isIcon ? 16 : 14} weight="regular" aria-hidden />
      {isIcon ? null : message}
    </>
  );
  const common = {
    "data-feature-lock": "",
    className: cn(isIcon ? ICON_CLASS : CHIP_CLASS, className),
    ...(isIcon ? { "aria-label": message, title: message } : {}),
  };
  return (
    <>
      {anonymous ? (
        <button type="button" onClick={() => setSignInOpen(true)} {...common}>
          {content}
        </button>
      ) : (
        <Link href={BILLING_HREF} {...common}>
          {content}
        </Link>
      )}
      {modal}
    </>
  );
}
