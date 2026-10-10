"use client";

import { CheckCircleIcon, InfoIcon, WarningIcon, XIcon } from "@phosphor-icons/react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { create } from "zustand";
import { cn } from "@/lib/cn";
import { DURATION, EASE } from "@/lib/motion";

type ToastKind = "success" | "error" | "info";

type ToastItem = { id: number; kind: ToastKind; message: string };

type ToastStore = {
  items: ToastItem[];
  push: (kind: ToastKind, message: string) => void;
  dismiss: (id: number) => void;
};

const VISIBLE_MS = 4500;
const MAX_VISIBLE = 3;
let nextId = 1;

const useToastStore = create<ToastStore>((set, get) => ({
  items: [],
  push: (kind, message) => {
    const id = nextId++;
    // Newest last; keep the stack short so a burst of failures never covers the page.
    set((state) => ({ items: [...state.items, { id, kind, message }].slice(-MAX_VISIBLE) }));
    window.setTimeout(() => get().dismiss(id), VISIBLE_MS);
  },
  dismiss: (id) => set((state) => ({ items: state.items.filter((item) => item.id !== id) })),
}));

/** Fire a toast from anywhere (event handlers, mutation callbacks). Pass an already-translated
 * message. Use it for the result of something the user just did; an error that needs a retry
 * belongs inline next to the control instead. */
export const toast = {
  success: (message: string): void => useToastStore.getState().push("success", message),
  error: (message: string): void => useToastStore.getState().push("error", message),
  info: (message: string): void => useToastStore.getState().push("info", message),
};

const ICON: Record<ToastKind, typeof InfoIcon> = {
  success: CheckCircleIcon,
  error: WarningIcon,
  info: InfoIcon,
};

const ICON_COLOR: Record<ToastKind, string> = {
  success: "text-[var(--success)]",
  error: "text-[var(--warn)]",
  info: "text-[var(--info)]",
};

/** Mount once in the app shell. Errors announce assertively, the rest politely. */
export function Toaster(): ReactNode {
  const t = useTranslations("common.toast");
  const items = useToastStore((state) => state.items);
  const dismiss = useToastStore((state) => state.dismiss);
  const reduceMotion = useReducedMotion();

  return (
    <div className="pointer-events-none fixed right-4 bottom-4 z-[60] flex w-[min(360px,calc(100vw-2rem))] flex-col gap-2">
      <AnimatePresence initial={false}>
        {items.map((item) => {
          const Icon = ICON[item.kind];
          return (
            <motion.div
              key={item.id}
              layout={!reduceMotion}
              role={item.kind === "error" ? "alert" : "status"}
              initial={reduceMotion ? false : { opacity: 0, y: 12, scale: 0.98 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              exit={{ opacity: 0, transition: { duration: DURATION.fast } }}
              transition={{ duration: DURATION.base, ease: EASE.out }}
              className={cn(
                "pointer-events-auto flex items-start gap-3 rounded-[var(--radius-card)] border",
                "border-[var(--border-strong)] bg-[var(--surface)] px-4 py-3 shadow-[var(--shadow-3)]",
              )}
            >
              <Icon
                size={18}
                weight="regular"
                aria-hidden
                className={cn("mt-0.5 shrink-0", ICON_COLOR[item.kind])}
              />
              <p className="min-w-0 flex-1 text-[13px] text-[var(--text)] leading-relaxed">
                {item.message}
              </p>
              <button
                type="button"
                onClick={() => dismiss(item.id)}
                aria-label={t("dismiss")}
                className="-mr-1 flex size-8 shrink-0 items-center justify-center rounded-[var(--radius-button)] text-[var(--text-muted)] hover:bg-[var(--surface-2)] hover:text-[var(--text)]"
              >
                <XIcon size={14} weight="regular" aria-hidden />
              </button>
            </motion.div>
          );
        })}
      </AnimatePresence>
    </div>
  );
}
