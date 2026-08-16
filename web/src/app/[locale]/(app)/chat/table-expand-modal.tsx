"use client";

import { XIcon } from "@phosphor-icons/react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { type ReactNode, useCallback, useEffect, useRef } from "react";
import { cn } from "@/lib/cn";

/**
 * Full-table modal opened by `TableWithActions`'s expand button — for a table too tall/cramped
 * to read comfortably inline (e.g. a many-row director/shareholding table), this shows the exact
 * same rows at full size in a scrollable, focus-trapped overlay. Mirrors `ConfirmDialog`'s
 * focus-trap/Esc-to-close/motion pattern (`components/settings/confirm-dialog.tsx`) rather than
 * inventing a second modal convention — sized for a table instead of a short confirm prompt
 * (wider, scrollable body, no confirm/cancel footer).
 *
 * `sourceTable` is the already-rendered `<table>` DOM node itself (not an HTML string) — the body
 * clones it via `Node.cloneNode(true)` and mounts the clone as a real DOM node
 * (`appendChild`/`replaceChildren`), never through `dangerouslySetInnerHTML`/`innerHTML`, so
 * there is no HTML-string parsing step for anything to inject into. Re-rendering the
 * react-markdown table tree a second time here would need threading the same styled
 * `<table>`/`<th>`/`<td>` overrides through a second render path for no benefit; the caller
 * already has the live node to clone.
 */
export function TableExpandModal({
  open,
  title,
  sourceTable,
  closeLabel,
  onClose,
}: {
  open: boolean;
  title: string;
  sourceTable: HTMLTableElement | null;
  closeLabel: string;
  onClose: () => void;
}): ReactNode {
  const dialogRef = useRef<HTMLDivElement>(null);
  const bodyRef = useRef<HTMLDivElement>(null);
  const previouslyFocused = useRef<HTMLElement | null>(null);
  const reduceMotion = useReducedMotion();

  // Clone the live table node into the modal body on every open — a plain DOM clone, never an
  // HTML-string round-trip, so there is no parsing step for untrusted content to exploit (the
  // clone is of a node the browser already parsed and rendered inline; nothing new is parsed).
  useEffect(() => {
    const body = bodyRef.current;
    if (!open || !body || !sourceTable) {
      return;
    }
    const clone = sourceTable.cloneNode(true);
    body.replaceChildren(clone);
    return () => {
      body.replaceChildren();
    };
  }, [open, sourceTable]);

  const close = useCallback(() => {
    onClose();
  }, [onClose]);

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
  }, [open, close]);

  const titleId = "table-expand-modal-title";
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
            aria-label={closeLabel}
            className="absolute inset-0 bg-black/60"
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
            <div
              ref={bodyRef}
              className="min-h-0 flex-1 overflow-auto p-5 text-[14px] [&_table]:w-full [&_table]:border-collapse"
            />
          </motion.div>
        </motion.div>
      ) : null}
    </AnimatePresence>
  );
}
