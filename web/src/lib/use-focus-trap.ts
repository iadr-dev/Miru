"use client";

import { useEffect, useRef } from "react";

/**
 * Esc-to-close + Tab/Shift-Tab focus wrap + restore-focus-to-opener-on-close, for a modal/dialog
 * rendered inside `dialogRef`. Shared by `AuthModal`, `ConfirmDialog`, and `TableExpandModal` —
 * factored out once a third consumer duplicated the identical trap logic verbatim.
 *
 * `onEscape`/`onEnter` are optional per-caller key handlers (Enter isn't meaningful for every
 * dialog shape — `ConfirmDialog` uses it to confirm, `AuthModal`/`TableExpandModal` don't).
 */
export function useFocusTrap(
  dialogRef: React.RefObject<HTMLElement | null>,
  open: boolean,
  onEscape: () => void,
  onEnter?: () => void,
): void {
  const previouslyFocused = useRef<HTMLElement | null>(null);

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
        onEscape();
        return;
      }
      if (e.key === "Enter" && onEnter && !(document.activeElement instanceof HTMLButtonElement)) {
        // A button's own Enter activation already fires its onClick — only intercept Enter when
        // focus is elsewhere (e.g. the dialog's root), so a focused Cancel button's Enter still
        // cancels instead of confirming.
        e.preventDefault();
        onEnter();
        return;
      }
      if (e.key === "Tab") {
        const items = focusables();
        if (items.length === 0) {
          return;
        }
        const first = items[0];
        const last = items.at(-1);
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
  }, [open, dialogRef, onEscape, onEnter]);
}
