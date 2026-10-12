"use client";

import { XIcon } from "@phosphor-icons/react";
import { AnimatePresence, motion } from "motion/react";
import { useTranslations } from "next-intl";
import { type ReactNode, useCallback, useRef, useState } from "react";
import { GoogleIcon, LineIcon } from "@/components/icons/brand-icons";
import { Button } from "@/components/ui/button";
import { AuthError, beginOAuth, type OAuthProvider } from "@/lib/api/auth";
import { cn } from "@/lib/cn";
import { useFocusTrap } from "@/lib/use-focus-trap";

export type AuthIntent = "signIn" | "register";

type AuthModalProps = {
  open: boolean;
  intent: AuthIntent;
  onClose: () => void;
};

type Phase =
  | { kind: "idle" }
  | { kind: "redirecting"; provider: OAuthProvider }
  | { kind: "error"; provider: OAuthProvider; code: string; retryable: boolean };

/**
 * OAuth-only auth modal (Google + LINE). Both the 登入 and 註冊 CTAs open this same modal — the
 * `intent` only changes copy emphasis, there is no separate password form (the backend is OAuth-only).
 *
 * On provider click: POST /auth/oauth/{provider}; on success we do a FULL browser navigation to the
 * returned authorization_url (a real redirect, not a fetch — required for LINE auto-login and for the
 * provider to land back on our callback route). Errors render inline as real, localized states:
 * provider_unconfigured (e.g. LINE not set in dev), oauth_failed (retryable), network_error, etc.
 * LINE's retry_authorization_url is surfaced as a fallback link when present.
 */
export function AuthModal({ open, intent, onClose }: AuthModalProps): ReactNode {
  const t = useTranslations("auth");
  const [phase, setPhase] = useState<Phase>({ kind: "idle" });
  const dialogRef = useRef<HTMLDivElement>(null);

  const handleClose = useCallback(() => {
    setPhase({ kind: "idle" });
    onClose();
  }, [onClose]);

  useFocusTrap(dialogRef, open, handleClose);

  const start = async (provider: OAuthProvider): Promise<void> => {
    setPhase({ kind: "redirecting", provider });
    try {
      const { authorization_url, retry_authorization_url } = await beginOAuth(provider);
      // Full-page navigation (not fetch) so cookies are set and the provider can redirect back.
      if (provider === "line" && retry_authorization_url) {
        // Stash the retry URL so the callback error state can offer it if auto-login stalls.
        try {
          sessionStorage.setItem("miru-line-retry-url", retry_authorization_url);
        } catch {
          // best-effort
        }
      }
      window.location.assign(authorization_url);
    } catch (err) {
      const authErr = err instanceof AuthError ? err : null;
      setPhase({
        kind: "error",
        provider,
        code: authErr?.code ?? "oauth_failed",
        retryable: authErr?.retryable ?? true,
      });
    }
  };

  const titleId = "auth-modal-title";
  const descId = "auth-modal-desc";

  return (
    <AnimatePresence>
      {open ? (
        <motion.div
          className="fixed inset-0 z-50 flex items-center justify-center p-4"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.15 }}
        >
          {/* scrim */}
          <button
            type="button"
            aria-label={t("modal.close")}
            className="absolute inset-0 bg-black/60"
            onClick={handleClose}
            tabIndex={-1}
          />
          <motion.div
            ref={dialogRef}
            role="dialog"
            aria-modal="true"
            aria-labelledby={titleId}
            aria-describedby={descId}
            className={cn(
              "relative w-full max-w-md rounded-[var(--radius-card)] border border-[var(--border)]",
              "bg-[var(--surface)] p-6 shadow-2xl",
            )}
            initial={{ opacity: 0, y: 8, scale: 0.98 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 8, scale: 0.98 }}
            transition={{ type: "spring", stiffness: 220, damping: 24 }}
          >
            <button
              type="button"
              onClick={handleClose}
              aria-label={t("modal.close")}
              className={cn(
                "absolute top-3 right-3 inline-flex size-11 items-center justify-center",
                "rounded-[var(--radius-button)] text-[var(--text-muted)] hover:bg-[var(--surface-2)]",
              )}
            >
              <XIcon size={18} weight="regular" />
            </button>

            <h2 id={titleId} className="font-semibold text-[var(--text)] text-xl">
              {intent === "register" ? t("modal.registerTitle") : t("modal.signInTitle")}
            </h2>
            <p id={descId} className="mt-1 text-[var(--text-muted)] text-sm">
              {intent === "register" ? t("modal.registerSubtitle") : t("modal.signInSubtitle")}
            </p>

            {phase.kind === "error" ? (
              <AuthErrorPanel
                provider={phase.provider}
                code={phase.code}
                retryable={phase.retryable}
                onRetry={() => start(phase.provider)}
              />
            ) : null}

            <div className="mt-6 flex flex-col gap-3">
              <Button
                variant="outline"
                onClick={() => start("google")}
                disabled={phase.kind === "redirecting"}
                className="w-full justify-start"
              >
                <GoogleIcon size={20} />
                <span className="flex-1 text-center">{t("modal.continueWithGoogle")}</span>
              </Button>
              <Button
                variant="outline"
                onClick={() => start("line")}
                disabled={phase.kind === "redirecting"}
                className="w-full justify-start"
              >
                <LineIcon size={20} />
                <span className="flex-1 text-center">{t("modal.continueWithLine")}</span>
              </Button>
            </div>

            {phase.kind === "redirecting" ? (
              <p className="mt-4 text-center text-[var(--text-muted)] text-sm" aria-live="polite">
                {t("state.redirecting", { provider: t(`provider.${phase.provider}`) })}
              </p>
            ) : null}

            <p className="mt-6 text-center text-[13px] text-[var(--text-muted)] leading-relaxed">
              {t("modal.terms")}
            </p>
          </motion.div>
        </motion.div>
      ) : null}
    </AnimatePresence>
  );
}

function AuthErrorPanel({
  provider,
  code,
  retryable,
  onRetry,
}: {
  provider: OAuthProvider;
  code: string;
  retryable: boolean;
  onRetry: () => void;
}): ReactNode {
  const t = useTranslations("auth");
  const providerLabel = t(`provider.${provider}`);
  // Only the keys the flow can actually surface; unknown → oauth_failed.
  const known = new Set([
    "provider_unconfigured",
    "oauth_state_mismatch",
    "oauth_failed",
    "provider_already_linked",
    "unknown_provider",
    "network_error",
    "bad_response",
  ]);
  const key = known.has(code) ? code : "oauth_failed";

  return (
    <div
      role="alert"
      className={cn(
        "mt-4 rounded-[var(--radius-input)] border border-[var(--warn)]/40",
        "bg-[var(--surface-2)] p-3 text-[var(--text)] text-sm",
      )}
    >
      <p className="font-medium">{t("error.title")}</p>
      <p className="mt-1 text-[var(--text-muted)]">
        {t(`error.${key}`, { provider: providerLabel })}
      </p>
      {retryable ? (
        <button
          type="button"
          onClick={onRetry}
          className="mt-2 min-h-9 font-medium text-[var(--accent)] text-sm hover:underline"
        >
          {t("error.retry")}
        </button>
      ) : null}
    </div>
  );
}
