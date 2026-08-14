"use client";

import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { type ReactNode, useCallback, useEffect, useRef } from "react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/cn";

/**
 * Focus-trapped confirm dialog (Esc cancels, Enter/click confirms) — shared by every Settings
 * delete flow (alerts, schedules, memory). Mirrors the trap pattern in `auth-modal.tsx`.
 */
export function ConfirmDialog({
  open,
  title,
  description,
  confirmLabel,
  cancelLabel,
  onConfirm,
  onCancel,
}: {
  open: boolean;
  title: string;
  description?: string;
  confirmLabel: string;
  cancelLabel: string;
  onConfirm: () => void;
  onCancel: () => void;
}): ReactNode {
  const dialogRef = useRef<HTMLDivElement>(null);
  const previouslyFocused = useRef<HTMLElement | null>(null);

  const close = useCallback(() => {
    onCancel();
  }, [onCancel]);

  useEffect(() => {
    if (!open) {
      return;
    }
    previouslyFocused.current = document.activeElement as HTMLElement | null;
    const dialog = dialogRef.current;
    const focusables = (): HTMLElement[] =>
      dialog
        ? Array.from(
            dialog.querySelectorAll<HTMLElement>(
              'button, [href], input, [tabindex]:not([tabindex="-1"])',
            ),
          ).filter((el) => !el.hasAttribute("disabled"))
        : [];
    focusables()[0]?.focus();

    const onKeyDown = (e: KeyboardEvent): void => {
      if (e.key === "Escape") {
        e.preventDefault();
        close();
        return;
      }
      if (e.key === "Enter" && !(document.activeElement instanceof HTMLButtonElement)) {
        // A button's own Enter activation already fires its onClick — only intercept Enter when
        // focus is elsewhere (e.g. the dialog's root), so Cancel-focused-then-Enter cancels
        // instead of confirming.
        e.preventDefault();
        onConfirm();
        return;
      }
      if (e.key === "Tab") {
        const items = focusables();
        if (items.length === 0) {
          return;
        }
        const first = items[0];
        const last = items[items.length - 1];
        if (!first || !last) {
          return;
        }
        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault();
          last.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first.focus();
        }
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      previouslyFocused.current?.focus?.();
    };
  }, [open, close, onConfirm]);

  const titleId = "confirm-dialog-title";
  const descId = "confirm-dialog-description";
  const reduceMotion = useReducedMotion();
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

  return (
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
            aria-label={cancelLabel}
            className="absolute inset-0 bg-black/60"
            onClick={close}
            tabIndex={-1}
          />
          <motion.div
            ref={dialogRef}
            role="dialog"
            aria-modal="true"
            aria-labelledby={titleId}
            aria-describedby={description ? descId : undefined}
            className={cn(
              "relative w-full max-w-sm rounded-[var(--radius-card)] border border-[var(--border)]",
              "bg-[var(--surface)] p-6 shadow-2xl",
            )}
            {...panelMotionProps}
            transition={panelTransition}
          >
            <h2 id={titleId} className="font-semibold text-[var(--text)] text-lg">
              {title}
            </h2>
            {description ? (
              <p id={descId} className="mt-2 text-[var(--text-muted)] text-sm">
                {description}
              </p>
            ) : null}
            <div className="mt-5 flex justify-end gap-2">
              <Button variant="ghost" size="sm" onClick={close}>
                {cancelLabel}
              </Button>
              <Button variant="filled" size="sm" onClick={onConfirm}>
                {confirmLabel}
              </Button>
            </div>
          </motion.div>
        </motion.div>
      ) : null}
    </AnimatePresence>
  );
}
