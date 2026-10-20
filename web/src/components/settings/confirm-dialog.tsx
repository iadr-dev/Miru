"use client";

import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { type ReactNode, useCallback, useRef } from "react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/cn";
import { useFocusTrap } from "@/lib/use-focus-trap";

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
  children,
  confirmDisabled = false,
}: Readonly<{
  open: boolean;
  title: string;
  description?: string | undefined;
  confirmLabel: string;
  cancelLabel: string;
  onConfirm: () => void;
  onCancel: () => void;
  /** Extra content under the description (e.g. a type-to-confirm field). */
  children?: ReactNode;
  /** Blocks both the confirm button and the Enter shortcut. */
  confirmDisabled?: boolean;
}>): ReactNode {
  const dialogRef = useRef<HTMLDivElement>(null);

  const close = useCallback(() => {
    onCancel();
  }, [onCancel]);

  const confirm = useCallback(() => {
    if (!confirmDisabled) onConfirm();
  }, [confirmDisabled, onConfirm]);

  useFocusTrap(dialogRef, open, close, confirm);

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
            {children ? <div className="mt-4">{children}</div> : null}
            <div className="mt-5 flex justify-end gap-2">
              <Button variant="ghost" size="sm" onClick={close}>
                {cancelLabel}
              </Button>
              <Button variant="filled" size="sm" onClick={confirm} disabled={confirmDisabled}>
                {confirmLabel}
              </Button>
            </div>
          </motion.div>
        </motion.div>
      ) : null}
    </AnimatePresence>
  );
}
