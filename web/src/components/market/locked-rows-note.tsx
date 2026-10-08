"use client";

import { useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { Link } from "@/i18n/navigation";

/** The one way out under a ranking whose later rows are locked for this plan (the top-N preview
 * exception): a calm line and a link to the plans, shown only when at least one row is locked.
 * The rows themselves stay masked placeholders - this is the single CTA, not a wall. */
export function LockedRowsNote({
  rows,
}: {
  readonly rows: readonly { readonly locked?: boolean }[];
}): ReactNode {
  const t = useTranslations("market.teaser");
  if (!rows.some((row) => row.locked)) {
    return null;
  }
  return (
    <p className="mt-2 text-[12px] text-[var(--text-muted)]">
      {t("rowsLocked")}{" "}
      <Link
        href="/settings/billing"
        className="text-[var(--accent-text)] underline-offset-2 hover:underline"
      >
        {t("rowsCta")}
      </Link>
    </p>
  );
}
