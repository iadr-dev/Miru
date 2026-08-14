"use client";

import { useSearchParams } from "next/navigation";
import { useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { Button } from "@/components/ui/button";
import { useRouter } from "@/i18n/navigation";
import { OAUTH_PROVIDERS, type OAuthProvider } from "@/lib/api/auth";
import { cn } from "@/lib/cn";

/** The backend error codes `_oauth_error_redirect` can send (auth.py). Anything else — a
 * tampered or missing query string — falls back to the generic "oauth_failed" copy rather than
 * rendering an unlocalized raw code. */
const KNOWN_CODES = new Set([
  "oauth_state_mismatch",
  "oauth_failed",
  "provider_already_linked",
  "unknown_provider",
]);

function isProvider(value: string | null): value is OAuthProvider {
  return value !== null && (OAUTH_PROVIDERS as readonly string[]).includes(value);
}

export function AuthErrorClient(): ReactNode {
  const t = useTranslations("auth");
  const router = useRouter();
  const searchParams = useSearchParams();

  const rawProvider = searchParams.get("provider");
  const provider: OAuthProvider = isProvider(rawProvider) ? rawProvider : "google";
  const rawCode = searchParams.get("code");
  const code = rawCode && KNOWN_CODES.has(rawCode) ? rawCode : "oauth_failed";
  const retryable = searchParams.get("retryable") === "1";
  const lineRetryUrl = provider === "line" ? readLineRetryUrl() : null;

  return (
    <div className="flex min-h-[100dvh] items-center justify-center bg-[var(--bg)] p-4">
      <div
        role="alert"
        className={cn(
          "w-full max-w-md rounded-[var(--radius-card)] border border-[var(--border)]",
          "bg-[var(--surface)] p-6 text-center",
        )}
      >
        <p className="font-semibold text-[var(--text)] text-lg">{t("error.title")}</p>
        <p className="mt-2 text-[var(--text-muted)] text-sm">
          {t(`error.${code}`, { provider: t(`provider.${provider}`) })}
        </p>
        <div className="mt-5 flex flex-col gap-2">
          {retryable ? (
            <Button variant="outline" onClick={() => router.replace("/")}>
              {t("error.retry")}
            </Button>
          ) : null}
          {provider === "line" && lineRetryUrl ? (
            <Button variant="outline" onClick={() => window.location.assign(lineRetryUrl)}>
              {t("error.lineRetry")}
            </Button>
          ) : null}
          <Button variant="ghost" onClick={() => router.replace("/")}>
            {t("error.back")}
          </Button>
        </div>
      </div>
    </div>
  );
}

function readLineRetryUrl(): string | null {
  try {
    return sessionStorage.getItem("miru-line-retry-url");
  } catch {
    return null;
  }
}
