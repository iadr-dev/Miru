"use client";

import { useTranslations } from "next-intl";
import { type ReactNode, useEffect } from "react";

/** Route-level fallback for an error that escapes every panel boundary (the page itself failed to
 * render). Keeps the locale layout and offers a retry instead of a blank screen. */
export default function AppError({
  error,
  reset,
}: {
  readonly error: Error & { digest?: string };
  readonly reset: () => void;
}): ReactNode {
  const t = useTranslations("common.panelError");

  useEffect(() => {
    console.error("page_render_failed", error.digest ?? error.message);
  }, [error]);

  return (
    <div className="mx-auto max-w-md p-6">
      <section role="alert" className="surface-card p-5">
        <h1 className="font-semibold text-[16px] text-[var(--text)]">{t("pageTitle")}</h1>
        <p className="mt-1 text-[13px] text-[var(--text-muted)]">{t("body")}</p>
        <button
          type="button"
          onClick={reset}
          className="mt-3 font-medium text-[13px] text-[var(--accent-text)] hover:underline"
        >
          {t("retry")}
        </button>
      </section>
    </div>
  );
}
