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

/** An estimated charge, always marked with the approximately sign so it is never read as billed. */
export function formatEstimateMicros(micros: number): string {
  return `≈${formatCostMicros(micros)}`;
}

// `claude-opus-5-5`, `claude-sonnet-4-6`, `claude-haiku-4-5-20251001` (family, major, minor, an
// optional date) and `claude-sonnet-5` (no minor).
const CLAUDE_MODEL_ID = /^claude-(opus|sonnet|haiku)-(\d+)(?:-(\d{1,2}))?(?:-\d{8})?$/;

/** The name a person recognises for a Claude model id ("Claude Opus 5.5"), or null. */
function claudeDisplayName(model: string): string | null {
  const match = CLAUDE_MODEL_ID.exec(model);
  if (match === null) {
    return null;
  }
  const [, family, major, minor] = match;
  const name = `${family?.charAt(0).toUpperCase()}${family?.slice(1)}`;
  return `Claude ${name} ${minor === undefined ? major : `${major}.${minor}`}`;
}

/** How a model is named on screen: a Claude id as its product name, any other id without a
 * redundant `provider/` prefix (OpenRouter ids already carry one). */
export function shortModelName(provider: string, model: string): string {
  if (provider === "anthropic") {
    const friendly = claudeDisplayName(model);
    if (friendly !== null) {
      return friendly;
    }
  }
  return model.startsWith(`${provider}/`) ? model.slice(provider.length + 1) : model;
}
