"use client";

import { useLocale, useTranslations } from "next-intl";
import type { CSSProperties, ReactNode } from "react";
import { cn } from "@/lib/cn";
import {
  DIRECTION_COLOR,
  directionOf,
  formatSignedChange,
  type SignedFormatOptions,
} from "@/lib/signed";

/**
 * A signed market move rendered the one approved way: ▲/▼/– glyph, the absolute value, `--up`
 * (red) / `--down` (green) / muted colour, and an aria-label that says the direction in words.
 * Use it for every price change, % change, net flow or delta instead of hand-rolling the glyph
 * and colour (see `lib/signed.ts`).
 *
 * `labelled={false}` when the surrounding row already carries an aria-label: the cell is then
 * hidden from assistive tech instead of being read twice.
 */
export function SignedChange({
  value,
  decimals,
  unit,
  spaced,
  locale,
  labelled = true,
  className,
  style,
}: SignedFormatOptions & {
  readonly value: number | null | undefined;
  readonly locale?: string;
  readonly labelled?: boolean;
  readonly className?: string;
  readonly style?: CSSProperties;
}): ReactNode {
  const activeLocale = useLocale();
  const t = useTranslations("common.direction");
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return (
      <span className={cn("font-num text-[var(--text-muted)] tabular-nums", className)}>—</span>
    );
  }
  const loc = locale ?? activeLocale;
  const direction = directionOf(value, decimals);
  const text = formatSignedChange(value, loc, { decimals, unit, spaced });
  return (
    <span
      className={cn("whitespace-nowrap font-num tabular-nums", className)}
      style={{ color: DIRECTION_COLOR[direction], ...style }}
      {...(labelled
        ? { role: "img", "aria-label": `${t(direction)} ${text.replace(/^[▲▼–]\s?/, "")}` }
        : { "aria-hidden": true })}
    >
      {text}
    </span>
  );
}
