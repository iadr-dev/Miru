/**
 * The one sign → direction → glyph/colour mapping for market moves (紅漲綠跌: `--up` is the red
 * 漲 token, `--down` the green 跌 token). Every price change, % change, net flow or delta on screen
 * goes through here so the rule cannot drift between tables, and so a value that *displays* as
 * zero (0.00%) reads as flat rather than as a red ▲.
 *
 * Direction is never colour alone: callers pair the colour with the glyph and an aria-label
 * (`<SignedChange>` does all three).
 */
import { formatNumber } from "@/lib/format-number";

export type Direction = "up" | "down" | "flat";

export const DIRECTION_GLYPH: Readonly<Record<Direction, string>> = {
  up: "▲",
  down: "▼",
  flat: "–",
};

export const DIRECTION_COLOR: Readonly<Record<Direction, string>> = {
  up: "var(--up)",
  down: "var(--down)",
  flat: "var(--text-muted)",
};

/** `decimals` is the precision the value is shown at: a value that rounds to zero there is flat. */
export function directionOf(value: number, decimals?: number): Direction {
  if (!Number.isFinite(value)) {
    return "flat";
  }
  const shown = decimals === undefined ? value : Number(value.toFixed(decimals));
  if (shown > 0) {
    return "up";
  }
  return shown < 0 ? "down" : "flat";
}

export function directionGlyph(value: number, decimals?: number): string {
  return DIRECTION_GLYPH[directionOf(value, decimals)];
}

export function directionColor(value: number, decimals?: number): string {
  return DIRECTION_COLOR[directionOf(value, decimals)];
}

export type SignedFormatOptions = {
  readonly decimals?: number;
  /** Appended after the number, e.g. "%". */
  readonly unit?: string;
  /** "▲ 1.23" (default) or "▲1.23". */
  readonly spaced?: boolean;
};

/** "▲ 1.23%" / "▼ 0.40%" / "– 0.00%": glyph + absolute value (the glyph carries the sign). */
export function formatSignedChange(
  value: number,
  locale: string,
  { decimals, unit = "", spaced = true }: SignedFormatOptions = {},
): string {
  const glyph = directionGlyph(value, decimals);
  const magnitude = formatNumber(
    Math.abs(value),
    locale,
    decimals === undefined ? undefined : { decimals },
  );
  return `${glyph}${spaced ? " " : ""}${magnitude}${unit}`;
}
