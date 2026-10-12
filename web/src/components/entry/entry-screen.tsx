"use client";

import Image from "next/image";
import { useTranslations } from "next-intl";
import { type ReactNode, useState } from "react";
import { type AuthIntent, AuthModal } from "@/components/auth/auth-modal";
import { EntryComposer } from "@/components/entry/composer";
import { LanguageSwitcher } from "@/components/shell/language-switcher";
import { ThemeToggle } from "@/components/shell/theme-toggle";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/cn";

/** The entry page (chat-app landing pattern): full-bleed dark canvas, logo top-left, sign-in /
 * register top-right, a vertically-centered hero (wordmark + composer), disclaimer pinned bottom.
 * The composer is a come-on: an anonymous send opens the auth modal (chat needs auth). */
export function EntryScreen(): ReactNode {
  const t = useTranslations();
  const [modal, setModal] = useState<{ open: boolean; intent: AuthIntent }>({
    open: false,
    intent: "signIn",
  });
  const [promptedFromSend, setPromptedFromSend] = useState(false);

  const openModal = (intent: AuthIntent, fromSend = false): void => {
    setPromptedFromSend(fromSend);
    setModal({ open: true, intent });
  };

  return (
    <div className="relative flex min-h-[100dvh] flex-col bg-[var(--bg)]">
      {/* Top bar */}
      <header className="flex items-center justify-between px-4 py-4 sm:px-6">
        <div className="flex items-center gap-2.5">
          <Image
            src="/logo.png"
            alt={t("brand.logoAlt")}
            width={32}
            height={32}
            priority
            className="size-8 rounded-[8px]"
          />
          <span className="font-semibold text-[15px] text-[var(--text)] tracking-tight">
            {t("brand.name")}
          </span>
        </div>

        <div className="flex items-center gap-2 sm:gap-3">
          <LanguageSwitcher />
          <ThemeToggle />
          <div className="mx-1 hidden h-6 w-px bg-[var(--border)] sm:block" />
          <Button variant="ghost" size="sm" onClick={() => openModal("signIn")}>
            {t("topbar.signIn")}
          </Button>
          <Button
            variant="filled"
            size="sm"
            className="rounded-[var(--radius-pill)]"
            onClick={() => openModal("register")}
          >
            {t("topbar.register")}
          </Button>
        </div>
      </header>

      {/* Hero */}
      <main className="flex flex-1 flex-col items-center justify-center px-4 pb-8">
        <div className="flex w-full max-w-2xl flex-col items-center">
          <Image
            src="/logo.png"
            alt={t("brand.logoAlt")}
            width={72}
            height={72}
            priority
            className="size-16 rounded-[16px] sm:size-18"
          />
          <h1 className="mt-6 text-center font-semibold text-2xl text-[var(--text)] tracking-tight sm:text-3xl">
            {t("hero.tagline")}
          </h1>
          <p className="mt-3 max-w-xl text-center text-[15px] text-[var(--text-muted)] leading-relaxed">
            {t("hero.subtitle")}
          </p>

          <div className="mt-8 w-full">
            <EntryComposer onAttemptSend={() => openModal("signIn", true)} />
          </div>

          {promptedFromSend && !modal.open ? (
            <div
              role="status"
              className={cn(
                "mt-4 w-full rounded-[var(--radius-input)] border border-[var(--border)]",
                "bg-[var(--surface)] p-3 text-center text-[var(--text-muted)] text-sm",
              )}
            >
              {t("hero.composer.signInPromptBody")}
            </div>
          ) : null}
        </div>
      </main>

      {/* Footer disclaimer — governance non-trading framing (governance.md §1). */}
      <footer className="px-4 pt-2 pb-6">
        <p className="mx-auto max-w-2xl text-center text-[13px] text-[var(--text-muted)] leading-relaxed">
          {t("disclaimer.text")}
        </p>
      </footer>

      <AuthModal
        open={modal.open}
        intent={modal.intent}
        onClose={() => setModal((m) => ({ ...m, open: false }))}
      />
    </div>
  );
}
