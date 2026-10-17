"use client";

import { ShieldWarningIcon, WarningCircleIcon } from "@phosphor-icons/react";
import Image from "next/image";
import { useLocale, useTranslations } from "next-intl";
import { type ReactNode, useCallback, useEffect, useRef, useState } from "react";
import { AuthModal } from "@/components/auth/auth-modal";
import { Button } from "@/components/ui/button";
import { Link } from "@/i18n/navigation";
import { ConsentApiError, decideConsent, getConsent } from "@/lib/api/oauth-consent";
import { cn } from "@/lib/cn";
import type { ConsentDecision, ConsentResponse } from "@/types/oauth-consent";

/** Error codes the consent endpoints answer with that have their own copy. Answering again cannot
 * help with any of them: the request has to be started again from the app. */
const KNOWN_ERRORS = [
  "oauth_nonce_mismatch",
  "oauth_browser_mismatch",
  "oauth_user_mismatch",
  "oauth_request_not_found",
] as const;

function isKnownError(code: string): boolean {
  return (KNOWN_ERRORS as readonly string[]).includes(code);
}

type Phase =
  | { kind: "loading" }
  | { kind: "ready"; consent: ConsentResponse }
  | { kind: "signin" }
  | { kind: "failed"; code: string };

type Props = {
  /** `?request=` - the consent request to show. */
  requestId: string | null;
  /** `?error=` - the API refused the client or redirect URI before any consent was created. */
  error: string | null;
};

/**
 * The page a third-party AI client sends the user to from `/oauth/authorize`. One centred card:
 * who is asking, what it can do, exactly where the browser goes next, and Allow / Deny. The page
 * answers with the single-use nonce from the GET and then navigates to the URL the API returns.
 *
 * Server state is deliberately not in the TanStack cache: the request carries a single-use nonce,
 * must never be served stale or refetched in the background, and is read once per visit.
 */
export function ConsentScreen({ requestId, error }: Props): ReactNode {
  if (error !== null || requestId === null) {
    return (
      <ConsentFrame>
        <LinkErrorCard />
      </ConsentFrame>
    );
  }
  return <ConsentRequest requestId={requestId} />;
}

function ConsentRequest({ requestId }: { requestId: string }): ReactNode {
  const locale = useLocale();
  const [phase, setPhase] = useState<Phase>({ kind: "loading" });

  useEffect(() => {
    let cancelled = false;
    setPhase({ kind: "loading" });
    getConsent(requestId).then(
      (consent) => {
        if (!cancelled) setPhase({ kind: "ready", consent });
      },
      (err: unknown) => {
        if (cancelled) return;
        if (err instanceof ConsentApiError && err.status === 401) {
          setPhase({ kind: "signin" });
        } else {
          setPhase({ kind: "failed", code: err instanceof ConsentApiError ? err.code : "unknown" });
        }
      },
    );
    return () => {
      cancelled = true;
    };
  }, [requestId]);

  const returnPath = `/${locale}/oauth/consent?${new URLSearchParams({ request: requestId }).toString()}`;

  return (
    <ConsentFrame>
      {phase.kind === "loading" ? <LoadingCard /> : null}
      {phase.kind === "ready" ? (
        <ReadyCard
          consent={phase.consent}
          onSignedOut={() => setPhase({ kind: "signin" })}
          onFailed={(code) => setPhase({ kind: "failed", code })}
        />
      ) : null}
      {phase.kind === "failed" ? <FailedCard code={phase.code} /> : null}
      {phase.kind === "signin" ? <SignInCard next={returnPath} /> : null}
    </ConsentFrame>
  );
}

function ConsentFrame({ children }: { children: ReactNode }): ReactNode {
  return (
    <main className="flex min-h-[100dvh] items-center justify-center bg-[var(--bg)] p-4">
      <div
        className={cn(
          "w-full max-w-md rounded-[var(--radius-card)] border border-[var(--border)]",
          "bg-[var(--surface)] p-6 shadow-2xl",
        )}
      >
        {children}
      </div>
    </main>
  );
}

function Brand(): ReactNode {
  const t = useTranslations("brand");
  return (
    <div className="mb-5 flex items-center gap-2">
      <Image
        src="/logo.png"
        alt={t("logoAlt")}
        width={28}
        height={28}
        className="size-7 rounded-[8px]"
      />
      <span className="font-semibold text-[15px] text-[var(--text)] tracking-tight">
        {t("name")}
      </span>
    </div>
  );
}

/** The heading takes focus when it appears so a screen reader starts at the question. */
function FocusedHeading({ children }: { children: ReactNode }): ReactNode {
  const ref = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    ref.current?.focus();
  }, []);
  return (
    <h1
      ref={ref}
      tabIndex={-1}
      className="font-semibold text-[var(--text)] text-xl leading-snug outline-none"
    >
      {children}
    </h1>
  );
}

function LoadingCard(): ReactNode {
  const t = useTranslations("oauthConsent");
  return (
    <div aria-busy="true">
      <Brand />
      <p role="status" className="text-[var(--text-muted)] text-sm">
        {t("loading")}
      </p>
      <div aria-hidden className="mt-4 space-y-3">
        <div className="h-5 w-3/4 animate-pulse rounded bg-[var(--surface-2)]" />
        <div className="h-4 w-full animate-pulse rounded bg-[var(--surface-2)]" />
        <div className="h-4 w-5/6 animate-pulse rounded bg-[var(--surface-2)]" />
        <div className="mt-6 h-11 w-full animate-pulse rounded bg-[var(--surface-2)]" />
      </div>
    </div>
  );
}

function ErrorCard({ title, body }: { title: string; body: string }): ReactNode {
  const t = useTranslations("oauthConsent");
  return (
    <div>
      <Brand />
      <div role="alert">
        <FocusedHeading>{title}</FocusedHeading>
        <p className="mt-2 text-[var(--text-muted)] text-sm leading-relaxed">{body}</p>
      </div>
      <Link
        href="/"
        className={cn(
          "mt-5 inline-flex min-h-11 w-full items-center justify-center",
          "rounded-[var(--radius-button)] border border-[var(--border)] px-5",
          "font-medium text-[15px] text-[var(--text)] hover:bg-[var(--surface-2)]",
        )}
      >
        {t("home")}
      </Link>
    </div>
  );
}

function LinkErrorCard(): ReactNode {
  const t = useTranslations("oauthConsent");
  return <ErrorCard title={t("linkError.title")} body={t("linkError.body")} />;
}

function FailedCard({ code }: { code: string }): ReactNode {
  const t = useTranslations("oauthConsent");
  const key = isKnownError(code) ? code : "generic";
  return <ErrorCard title={t("errorTitle")} body={t(`errors.${key}`)} />;
}

function SignInCard({ next }: { next: string }): ReactNode {
  const t = useTranslations("oauthConsent");
  const [open, setOpen] = useState(true);
  return (
    <div>
      <Brand />
      <FocusedHeading>{t("signIn.title")}</FocusedHeading>
      <p className="mt-2 text-[var(--text-muted)] text-sm leading-relaxed">{t("signIn.body")}</p>
      <Button className="mt-5 w-full" onClick={() => setOpen(true)}>
        {t("signIn.action")}
      </Button>
      <AuthModal open={open} intent="signIn" onClose={() => setOpen(false)} next={next} />
    </div>
  );
}

/** The origin and path the browser will be sent to - never the query (it carries the code). */
function destinationOf(redirectUri: string): string {
  try {
    const url = new URL(redirectUri);
    return `${url.origin}${url.pathname}`;
  } catch {
    return redirectUri.split(/[?#]/)[0] ?? redirectUri;
  }
}

function useSecondsLeft(expiresAt: string): number {
  const deadline = Date.parse(expiresAt);
  const compute = useCallback(
    () => (Number.isNaN(deadline) ? 0 : Math.max(0, Math.ceil((deadline - Date.now()) / 1000))),
    [deadline],
  );
  const [left, setLeft] = useState(compute);
  useEffect(() => {
    setLeft(compute());
    const timer = window.setInterval(() => setLeft(compute()), 1000);
    return () => window.clearInterval(timer);
  }, [compute]);
  return left;
}

function formatClock(totalSeconds: number): string {
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes}:${String(seconds).padStart(2, "0")}`;
}

function ReadyCard({
  consent,
  onSignedOut,
  onFailed,
}: {
  consent: ConsentResponse;
  onSignedOut: () => void;
  onFailed: (code: string) => void;
}): ReactNode {
  const t = useTranslations("oauthConsent");
  const [pending, setPending] = useState<ConsentDecision | null>(null);
  const [retryError, setRetryError] = useState<string | null>(null);
  const secondsLeft = useSecondsLeft(consent.expires_at);
  const expired = secondsLeft === 0;
  const busy = pending !== null;

  const decide = async (decision: ConsentDecision): Promise<void> => {
    setPending(decision);
    setRetryError(null);
    try {
      const redirectTo = await decideConsent(consent.request_id, decision, consent.nonce);
      // Leave the buttons disabled: the page is about to be replaced by the client's own.
      window.location.assign(redirectTo);
    } catch (err) {
      if (err instanceof ConsentApiError && err.status === 401) {
        onSignedOut();
      } else if (err instanceof ConsentApiError && isKnownError(err.code)) {
        onFailed(err.code);
      } else {
        setPending(null);
        setRetryError(t("errors.retry"));
      }
    }
  };

  return (
    <div>
      <Brand />
      <FocusedHeading>{t("title", { client: consent.client.name })}</FocusedHeading>

      {consent.client.verified ? null : (
        <div
          role="note"
          className={cn(
            "mt-4 flex gap-3 rounded-[var(--radius-input)] border border-[var(--warn)]/50",
            "bg-[var(--surface-2)] p-3 text-sm",
          )}
        >
          <ShieldWarningIcon
            size={20}
            weight="regular"
            aria-hidden
            className="mt-0.5 shrink-0 text-[var(--warn)]"
          />
          <div>
            <p className="font-medium text-[var(--text)]">{t("unverified.title")}</p>
            <p className="mt-1 text-[var(--text-muted)] leading-relaxed">
              {t("unverified.body", { client: consent.client.name })}
            </p>
          </div>
        </div>
      )}

      <section aria-labelledby="consent-scopes" className="mt-5">
        <h2 id="consent-scopes" className="font-medium text-[var(--text)] text-sm">
          {t("scopesTitle")}
        </h2>
        <ul className="mt-2 list-disc space-y-1.5 pl-5 text-[var(--text-muted)] text-sm leading-relaxed">
          {consent.scopes.map((scope) => (
            <li key={scope.id}>{scope.description}</li>
          ))}
        </ul>
      </section>

      <section aria-labelledby="consent-destination" className="mt-5">
        <h2 id="consent-destination" className="font-medium text-[var(--text)] text-sm">
          {t("destinationTitle")}
        </h2>
        <p className="mt-1 text-[13px] text-[var(--text-muted)]">{t("destinationHint")}</p>
        <p
          className={cn(
            "mt-2 break-all rounded-[var(--radius-input)] border border-[var(--border)]",
            "bg-[var(--surface-2)] px-3 py-2 font-num text-[13px] text-[var(--text)]",
          )}
        >
          {destinationOf(consent.redirect_uri)}
        </p>
      </section>

      <p className="mt-4 text-[13px] text-[var(--text-muted)]">
        {expired ? (
          <span role="alert" className="text-[var(--warn)]">
            {t("expired")}
          </span>
        ) : (
          t("expiresIn", { time: formatClock(secondsLeft) })
        )}
      </p>

      <div aria-live="polite">
        {retryError === null ? null : (
          <p
            role="alert"
            className="mt-3 flex items-start gap-2 text-[var(--text)] text-sm leading-relaxed"
          >
            <WarningCircleIcon
              size={18}
              weight="regular"
              aria-hidden
              className="mt-0.5 shrink-0 text-[var(--warn)]"
            />
            {retryError}
          </p>
        )}
      </div>

      <div className="mt-5 grid grid-cols-2 gap-3">
        <Button
          variant="outline"
          disabled={busy || expired}
          onClick={() => void decide("deny")}
          aria-label={t("denyLabel", { client: consent.client.name })}
        >
          {pending === "deny" ? t("working") : t("deny")}
        </Button>
        <Button
          disabled={busy || expired}
          onClick={() => void decide("approve")}
          aria-label={t("allowLabel", { client: consent.client.name })}
        >
          {pending === "approve" ? t("working") : t("allow")}
        </Button>
      </div>
      <p className="mt-4 text-center text-[13px] text-[var(--text-muted)] leading-relaxed">
        {t("footer")}
      </p>
    </div>
  );
}
