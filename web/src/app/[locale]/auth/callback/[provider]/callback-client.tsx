"use client";

import { useSearchParams } from "next/navigation";
import { useTranslations } from "next-intl";
import { type ReactNode, useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { useRouter } from "@/i18n/navigation";
import { AuthError, completeOAuth, OAUTH_PROVIDERS, type OAuthProvider } from "@/lib/api/auth";
import { cn } from "@/lib/cn";

type State = { kind: "working" } | { kind: "error"; code: string; retryable: boolean };

function isProvider(value: string): value is OAuthProvider {
  return (OAUTH_PROVIDERS as readonly string[]).includes(value);
}

/** Frontend OAuth callback (redirect_uri points here — see .env.local.example). The provider
 * redirects the browser back with ?code=&state=; we forward those to the backend's JSON callback
 * (GET /auth/oauth/{provider}/callback) with credentials so it can verify state against the httpOnly
 * flow cookies and set the session cookies on our origin. On success we land at /market. */
export function CallbackClient({ provider }: Readonly<{ provider: string }>): ReactNode {
  const t = useTranslations("auth");
  const router = useRouter();
  const searchParams = useSearchParams();
  const [state, setState] = useState<State>({ kind: "working" });
  const ran = useRef(false);

  const code = searchParams.get("code");
  const oauthState = searchParams.get("state");

  useEffect(() => {
    if (ran.current) {
      return;
    }
    ran.current = true;

    if (!isProvider(provider)) {
      setState({ kind: "error", code: "unknown_provider", retryable: false });
      return;
    }
    if (!code || !oauthState) {
      setState({ kind: "error", code: "missing_params", retryable: false });
      return;
    }

    completeOAuth(provider, code, oauthState)
      .then(() => {
        // Signed in — cookies are set; land on the default authenticated route.
        router.replace("/market");
      })
      .catch((err: unknown) => {
        const authErr = err instanceof AuthError ? err : null;
        setState({
          kind: "error",
          code: authErr?.code ?? "oauth_failed",
          retryable: authErr?.retryable ?? true,
        });
      });
  }, [provider, code, oauthState, router]);

  if (state.kind === "working") {
    return (
      <Centered>
        <div className="flex flex-col items-center gap-3" aria-live="polite">
          <Spinner />
          <p className="text-[var(--text-muted)] text-sm">{t("state.completing")}</p>
        </div>
      </Centered>
    );
  }

  const lineRetryUrl = readLineRetryUrl();

  return (
    <Centered>
      <div
        role="alert"
        className={cn(
          "w-full max-w-md rounded-[var(--radius-card)] border border-[var(--border)]",
          "bg-[var(--surface)] p-6 text-center",
        )}
      >
        <p className="font-semibold text-[var(--text)] text-lg">{t("error.title")}</p>
        <p className="mt-2 text-[var(--text-muted)] text-sm">
          {t(`error.${state.code}`, { provider: t(`provider.${provider}`) })}
        </p>
        <div className="mt-5 flex flex-col gap-2">
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
    </Centered>
  );
}

function readLineRetryUrl(): string | null {
  try {
    return sessionStorage.getItem("miru-line-retry-url");
  } catch {
    return null;
  }
}

function Centered({ children }: Readonly<{ children: ReactNode }>): ReactNode {
  return (
    <div className="flex min-h-[100dvh] items-center justify-center bg-[var(--bg)] p-4">
      {children}
    </div>
  );
}

function Spinner(): ReactNode {
  return (
    <span
      aria-hidden
      className="size-6 animate-spin rounded-full border-2 border-[var(--border)] border-t-[var(--accent)]"
    />
  );
}
