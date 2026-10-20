"use client";

import { XIcon } from "@phosphor-icons/react";
import * as Dialog from "@radix-ui/react-dialog";
import { motion, useReducedMotion } from "motion/react";
import { useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { SettingsNav } from "@/components/settings/settings-nav";
import { useRouter } from "@/i18n/navigation";
import { cn } from "@/lib/cn";

/**
 * The Settings overlay — a real Radix `Dialog` (correct focus-trap/aria-modal semantics, so this
 * doesn't hand-roll what `ConfirmDialog` already had to fix once) rendered by the intercepted
 * `/settings/*` route inside the `@modal` parallel-route slot. The page behind it is whatever the
 * user was actually on — Next's own route interception, not a fake dimmed shell — so closing
 * (`Esc`, backdrop click, or the ✕) calls `router.back()` to land exactly back there.
 */
export function SettingsDialog({ children }: Readonly<{ children: ReactNode }>): ReactNode {
  const t = useTranslations("settings.common");
  const router = useRouter();
  const reduceMotion = useReducedMotion();

  const close = (): void => {
    router.back();
  };

  return (
    <Dialog.Root
      open
      onOpenChange={(open) => {
        if (!open) {
          close();
        }
      }}
    >
      <Dialog.Portal>
        <Dialog.Overlay asChild>
          <motion.div
            className="fixed inset-0 z-50 bg-black/50 backdrop-blur-sm"
            initial={{ opacity: reduceMotion ? 1 : 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: reduceMotion ? 0 : 0.15 }}
          />
        </Dialog.Overlay>
        <Dialog.Content
          className={cn(
            // A fixed frame: the dialog keeps one size whichever section is open, so the nav never
            // moves under the cursor; a long section scrolls inside it. On a small screen it fills
            // the viewport instead of floating.
            "fixed inset-0 z-50 h-[100dvh] w-screen",
            "lg:inset-auto lg:top-1/2 lg:left-1/2 lg:h-[min(680px,85vh)] lg:w-[min(880px,92vw)]",
            "lg:-translate-x-1/2 lg:-translate-y-1/2",
            "overflow-hidden border border-[var(--border)] lg:rounded-[var(--radius-card)]",
            "bg-[var(--surface)] shadow-2xl",
            "flex flex-col lg:flex-row",
          )}
        >
          <Dialog.Title className="sr-only">{t("settingsTitle")}</Dialog.Title>
          <div className="shrink-0 border-[var(--border)] border-b p-3 pr-14 lg:w-56 lg:overflow-y-auto lg:overflow-x-hidden lg:border-r lg:border-b-0 lg:p-5">
            <SettingsNav />
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto p-6">
            <div className="mx-auto max-w-[560px]">{children}</div>
          </div>
          <Dialog.Close
            aria-label={t("cancel")}
            className={cn(
              "absolute top-3 right-3 inline-flex size-11 items-center justify-center",
              "rounded-[var(--radius-button)] text-[var(--text-muted)] hover:bg-[var(--surface-2)]",
              "!outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]",
            )}
          >
            <XIcon size={18} weight="regular" />
          </Dialog.Close>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
