/** Formatting for Paddle amounts and transactions on the billing page. Paddle states money as a
 * string in the currency's minor units ("29900" TWD = NT$299.00); these turn that into display
 * text without inventing or rounding anything. */
import type { BillingTransaction } from "@/types/settings";

/** `amount` (minor units, as Paddle sends it) + ISO currency -> "$10.99" / "$299.00" / "¥1,500".
 * Falls back to the raw values if either is not something Intl understands. */
export function formatMinorAmount(amount: string, currency: string, locale: string): string {
  const minor = Number(amount);
  if (!Number.isFinite(minor)) {
    return `${amount} ${currency}`;
  }
  try {
    const format = new Intl.NumberFormat(locale, { style: "currency", currency });
    const digits = format.resolvedOptions().maximumFractionDigits ?? 2;
    return format.format(minor / 10 ** digits);
  } catch {
    return `${amount} ${currency}`;
  }
}

/** A short calendar date in the user's locale, or null for a missing / unparsable value. */
export function formatDate(iso: string | null | undefined, locale: string): string | null {
  if (!iso) {
    return null;
  }
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) {
    return null;
  }
  return new Intl.DateTimeFormat(locale, { dateStyle: "medium" }).format(date);
}

/** Paddle creates a draft/ready transaction the moment checkout opens, and a canceled one is a
 * checkout that never charged anything. None of these is a bill, so none shows in the history. */
export function isBillableTransaction(tx: Pick<BillingTransaction, "status">): boolean {
  return tx.status !== "draft" && tx.status !== "ready" && tx.status !== "canceled";
}

export type TransactionSummary = {
  id: string;
  date: string | null;
  amount: string | null;
  status: string;
};

/** The text of one billing-history row, from the transaction the backend sends. */
export function summarizeTransaction(tx: BillingTransaction, locale: string): TransactionSummary {
  return {
    id: tx.id,
    date: formatDate(tx.billed_at ?? tx.created_at, locale),
    amount:
      tx.total && tx.currency_code ? formatMinorAmount(tx.total, tx.currency_code, locale) : null,
    status: tx.status,
  };
}
