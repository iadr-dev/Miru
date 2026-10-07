/** Display helpers for token / cost figures on the usage page and the chat token chip. */

/** 1234 -> "1.2k", 3_400_000 -> "3.4M". Under 1,000 the exact number; never scientific. */
export function formatTokens(value: number): string {
  if (value < 1000) {
    return String(Math.max(0, Math.round(value)));
  }
  const units: [number, string][] = [
    [1_000_000_000, "B"],
    [1_000_000, "M"],
    [1000, "k"],
  ];
  for (const [size, suffix] of units) {
    if (value >= size) {
      const scaled = value / size;
      // one decimal under 100, none above, and drop a trailing ".0"
      const text =
        scaled < 100 ? scaled.toFixed(1).replace(/\.0$/, "") : String(Math.round(scaled));
      return `${text}${suffix}`;
    }
  }
  return String(value);
}

/** Millionths of a USD -> "$0.0014" (4 decimals under a cent-ish, 2 above). */
export function formatCostMicros(micros: number): string {
  const dollars = micros / 1_000_000;
  if (dollars === 0) {
    return "$0";
  }
  return dollars < 1 ? `$${dollars.toFixed(4)}` : `$${dollars.toFixed(2)}`;
}

/** A model id without a redundant `provider/` prefix (OpenRouter ids already carry one). */
export function shortModelName(provider: string, model: string): string {
  return model.startsWith(`${provider}/`) ? model.slice(provider.length + 1) : model;
}
