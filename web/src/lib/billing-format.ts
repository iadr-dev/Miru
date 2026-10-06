/** Formatting for Paddle amounts and transactions on the billing page. Paddle states money as a
 * string in the currency's minor units ("29900" TWD = NT$299.00); these turn that into display
 * text without inventing or rounding anything. */

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

export type TransactionSummary = {
  key: string;
  date: string | null;
  amount: string | null;
  status: string | null;
};

function record(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null ? (value as Record<string, unknown>) : null;
}

function text(value: unknown): string | null {
  return typeof value === "string" && value !== "" ? value : null;
}

/** The few facts a billing-history row shows, pulled defensively from a Paddle transaction
 * (anything missing becomes null rather than throwing). */
export function summarizeTransaction(
  tx: Record<string, unknown>,
  index: number,
  locale: string,
): TransactionSummary {
  const totals = record(record(tx.details)?.totals);
  const total = text(totals?.grand_total) ?? text(totals?.total);
  const currency = text(tx.currency_code) ?? text(totals?.currency_code);
  return {
    key: text(tx.id) ?? `tx-${index}`,
    date: formatDate(text(tx.billed_at) ?? text(tx.created_at), locale),
    amount: total && currency ? formatMinorAmount(total, currency, locale) : null,
    status: text(tx.status),
  };
}
