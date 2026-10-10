/** Money amounts (capital, market value) as a reader says them, always with a unit.
 *
 * zh-TW uses the native 萬 / 億 / 兆 scale with the currency word after it ("160.50 億元",
 * "3.20 兆美元"); a raw 16,049,526,130 makes the reader count digits. Other locales use the
 * standard compact currency form ("NT$16.05B"). This is display formatting only - it never
 * replaces a full figure in a data sheet or table. */

export type AmountCurrency = "TWD" | "USD";

const ZH_SCALES: readonly { readonly size: number; readonly unit: string }[] = [
  { size: 1e12, unit: "兆" },
  { size: 1e8, unit: "億" },
  { size: 1e4, unit: "萬" },
];

const ZH_CURRENCY_WORD: Record<AmountCurrency, string> = { TWD: "元", USD: "美元" };

export function formatAmount(
  value: number | null | undefined,
  locale: string,
  currency: AmountCurrency,
): string {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return "—";
  }
  if (locale.startsWith("zh")) {
    const word = ZH_CURRENCY_WORD[currency];
    const scale = ZH_SCALES.find((s) => Math.abs(value) >= s.size);
    if (!scale) {
      return `${new Intl.NumberFormat(locale, { maximumFractionDigits: 0 }).format(value)} ${word}`;
    }
    const scaled = new Intl.NumberFormat(locale, {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    }).format(value / scale.size);
    return `${scaled} ${scale.unit}${word}`;
  }
  return new Intl.NumberFormat(locale, {
    style: "currency",
    currency,
    currencyDisplay: "symbol",
    notation: "compact",
    // Pinned both ends: ICU versions disagree on the currency default (one gives "$3.2T", another
    // "$3.20T"), and the label must not change with the runtime.
    minimumFractionDigits: 0,
    maximumFractionDigits: 2,
  }).format(value);
}
