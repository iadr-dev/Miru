"use client";

import { XIcon } from "@phosphor-icons/react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { type ReactNode, useCallback, useRef } from "react";
import { createPortal } from "react-dom";
import { cn } from "@/lib/cn";
import { useFocusTrap } from "@/lib/use-focus-trap";

/**
 * Generic "view complete data" modal — any card/chart/table's expand affordance opens this
 * instead of revealing more content inline. Mirrors `TableExpandModal`'s focus-trap/Esc-to-close/
 * motion contract (`chat/table-expand-modal.tsx`) but takes rendered `children` directly rather
 * than cloning a `<table>` DOM node, so it works for a leaderboard list, a gauge + trend list, a
 * chart, or any other card body — one shared modal shell instead of every surface hand-rolling
 * its own overlay. The backdrop is blurred (`backdrop-blur-sm`, matching `SettingsDialog`'s
 * existing overlay convention) so the dimmed page behind it still reads as "this app", not a flat
 * black scrim.
 *
 * Rendered via `createPortal` into `document.body` — NOT inline where the caller sits in the
 * tree. A `fixed` overlay only escapes its DOM ancestors' visual bounds, not their PAINT order:
 * any ancestor that creates a stacking context (`.market-rainbow-border`'s `isolation: isolate`
 * is exactly this) still traps this modal's z-order beneath every sibling card that comes later
 * in DOM order. Confirmed live: with market cards each wrapped in an isolated rainbow-border
 * div, only the LAST card's modal painted above the page — every earlier card's modal rendered
 * fixed-position-correct but visually buried under later cards. A portal sidesteps the whole
 * class of bug by never being a descendant of any of those wrappers in the first place.
 */
export function ExpandModal({
  open,
  title,
  closeLabel,
  onClose,
  children,
}: {
  open: boolean;
  title: string;
  closeLabel: string;
  onClose: () => void;
  children: ReactNode;
}): ReactNode {
  const dialogRef = useRef<HTMLDivElement>(null);
  const reduceMotion = useReducedMotion();

  const close = useCallback(() => {
    onClose();
  }, [onClose]);

  useFocusTrap(dialogRef, open, close);

  const titleId = "expand-modal-title";
  const fadeTransition = reduceMotion ? { duration: 0 } : { duration: 0.15 };
  const panelTransition = reduceMotion
    ? { duration: 0 }
    : { type: "spring" as const, stiffness: 220, damping: 24 };
  const panelMotionProps = reduceMotion
    ? { initial: false, animate: { opacity: 1 }, exit: { opacity: 0 } }
    : {
        initial: { opacity: 0, y: 8, scale: 0.98 },
        animate: { opacity: 1, y: 0, scale: 1 },
        exit: { opacity: 0, y: 8, scale: 0.98 },
      };

  const modal = (
    <AnimatePresence>
      {open ? (
        <motion.div
          className="fixed inset-0 z-50 flex items-center justify-center p-4"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={fadeTransition}
        >
          <button
            type="button"
            aria-label={closeLabel}
            className="absolute inset-0 bg-black/60 backdrop-blur-sm"
            onClick={close}
            tabIndex={-1}
          />
          <motion.div
            ref={dialogRef}
            role="dialog"
            aria-modal="true"
            aria-labelledby={titleId}
            className={cn(
              "relative flex max-h-[85dvh] w-full max-w-4xl flex-col rounded-[var(--radius-card)]",
              "border border-[var(--border)] bg-[var(--surface)] shadow-2xl",
            )}
            {...panelMotionProps}
            transition={panelTransition}
          >
            <div className="flex items-center justify-between gap-3 border-[var(--border)] border-b px-5 py-3">
              <h2 id={titleId} className="truncate font-medium text-[15px] text-[var(--text)]">
                {title}
              </h2>
              <button
                type="button"
                onClick={close}
                aria-label={closeLabel}
                title={closeLabel}
                className={cn(
                  "inline-flex size-8 shrink-0 items-center justify-center rounded-[var(--radius-button)]",
                  "text-[var(--text-muted)] transition-colors hover:bg-[var(--surface-2)] hover:text-[var(--text)]",
                  "!outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]",
                )}
              >
                <XIcon size={16} weight="regular" />
              </button>
            </div>
            <div className="min-h-0 flex-1 overflow-auto p-5 text-[14px]">{children}</div>
          </motion.div>
        </motion.div>
      ) : null}
    </AnimatePresence>
  );

  // `document` is unavailable during SSR; this component only ever runs its portal branch after
  // client hydration, matching every other client-only browser-API usage in this codebase (no
  // separate mount-effect needed since Next.js's client-component render already happens
  // client-side after the initial server-rendered pass).
  if (typeof document === "undefined") {
    return null;
  }
  return createPortal(modal, document.body);
}
