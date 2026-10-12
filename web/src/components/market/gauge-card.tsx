"use client";

import { useLocale } from "next-intl";
import type { ReactNode } from "react";
import { formatNumber } from "@/lib/format-number";

/**
 * A banded half-circle gauge for one real numeric value — the mockup's `.gauge-svg-wrap` shape,
 * generalized over any bounded metric (margin-maintenance %, a future score). The caller supplies
 * `bands` (its own domain's real cutoffs, e.g. TWSE's 融資維持率 warn/safe thresholds) and the
 * `--up`/`--down`/`--warn` token per band — this component never invents a threshold itself, it
 * only renders the one it's given. Direction is never color-alone: `bandLabel` is always shown as
 * text beside the needle, and the numeric value + unit ride in `aria-label`.
 */

export type GaugeBand = {
  /** Inclusive lower bound of this band, in the same units as `value`. */
  min: number;
  /** Exclusive upper bound (Infinity for the top band). */
  max: number;
  label: string;
  /** One of the design-token CSS vars, e.g. "var(--up)" | "var(--down)" | "var(--warn)". */
  color: string;
};

const GAUGE_MIN_ANGLE = -90;
const GAUGE_MAX_ANGLE = 90;
const GAUGE_RADIUS = 80;
const GAUGE_CENTER_X = 100;
const GAUGE_CENTER_Y = 100;
const NEEDLE_LENGTH = 62;
const GAUGE_WIDTH_PX = 210;

function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max);
}

function valueToAngleDeg(value: number, domainMin: number, domainMax: number): number {
  const t = clamp((value - domainMin) / (domainMax - domainMin), 0, 1);
  return GAUGE_MIN_ANGLE + t * (GAUGE_MAX_ANGLE - GAUGE_MIN_ANGLE);
}

function polarToCartesian(angleDeg: number, radius: number): { x: number; y: number } {
  // 0deg points up (12 o'clock) in this gauge's convention, sweeping left-to-right.
  const angleRad = ((angleDeg - 90) * Math.PI) / 180;
  return {
    x: GAUGE_CENTER_X + radius * Math.cos(angleRad),
    y: GAUGE_CENTER_Y + radius * Math.sin(angleRad),
  };
}

function describeArc(startAngle: number, endAngle: number, radius: number): string {
  const start = polarToCartesian(startAngle, radius);
  const end = polarToCartesian(endAngle, radius);
  const largeArcFlag = endAngle - startAngle <= 180 ? 0 : 1;
  return `M ${start.x} ${start.y} A ${radius} ${radius} 0 ${largeArcFlag} 1 ${end.x} ${end.y}`;
}

export function GaugeCard({
  title,
  value,
  unit,
  domainMin,
  domainMax,
  bands,
  decimals = 1,
  leftAxisLabel,
  rightAxisLabel,
  children,
}: {
  title: string;
  /** `null` renders the gauge in an honest empty state — never a fabricated needle position. */
  value: number | null;
  unit?: string;
  domainMin: number;
  domainMax: number;
  bands: readonly GaugeBand[];
  decimals?: number;
  leftAxisLabel: string;
  rightAxisLabel: string;
  children?: ReactNode;
}): ReactNode {
  const locale = useLocale();
  const activeBand = value === null ? null : bands.find((b) => value >= b.min && value < b.max);
  const needleAngle =
    value === null ? GAUGE_MIN_ANGLE : valueToAngleDeg(value, domainMin, domainMax);
  const needleTip = polarToCartesian(needleAngle, NEEDLE_LENGTH);

  return (
    <div className="flex flex-wrap items-center gap-6">
      <div className="shrink-0" style={{ width: GAUGE_WIDTH_PX }}>
        <div className="relative">
          <svg viewBox="0 0 200 108" width={GAUGE_WIDTH_PX} aria-hidden role="presentation">
            <title>{title}</title>
            {bands.map((band) => {
              const startAngle = valueToAngleDeg(band.min, domainMin, domainMax);
              const endAngle = valueToAngleDeg(
                Number.isFinite(band.max) ? band.max : domainMax,
                domainMin,
                domainMax,
              );
              return (
                <path
                  key={band.label}
                  d={describeArc(startAngle, endAngle, GAUGE_RADIUS)}
                  fill="none"
                  stroke={band.color}
                  strokeWidth={14}
                  strokeLinecap="round"
                />
              );
            })}
            {value !== null ? (
              <>
                <line
                  x1={GAUGE_CENTER_X}
                  y1={GAUGE_CENTER_Y}
                  x2={needleTip.x}
                  y2={needleTip.y}
                  stroke="var(--text)"
                  strokeWidth={3}
                  strokeLinecap="round"
                />
                <circle cx={GAUGE_CENTER_X} cy={GAUGE_CENTER_Y} r={5} fill="var(--text)" />
              </>
            ) : null}
          </svg>
          {/* Number + band chip float ABOVE the arc's own baseline (never inside its sweep),
           * so they can never collide with the needle or the arc strokes regardless of value. */}
          <div
            className="absolute inset-x-0 top-[52%] flex flex-col items-center gap-1 text-center"
            role="group"
            aria-label={
              value !== null
                ? `${title} ${formatNumber(value, locale, { decimals })}${unit ?? ""}${
                    activeBand ? ` ${activeBand.label}` : ""
                  }`
                : `${title}：資料暫無`
            }
          >
            <div className="font-num text-[30px] text-[var(--text)] tabular-nums leading-none">
              {value !== null ? formatNumber(value, locale, { decimals }) : "—"}
              {value !== null ? unit : ""}
            </div>
            {activeBand ? (
              <span
                aria-hidden
                className="rounded-[var(--radius-pill)] px-2 py-0.5 font-medium text-[11px]"
                style={{
                  color: activeBand.color,
                  backgroundColor: `color-mix(in srgb, ${activeBand.color} 14%, transparent)`,
                }}
              >
                {activeBand.label}
              </span>
            ) : null}
          </div>
        </div>
        <div className="mt-1 flex justify-between px-1 text-[10px] text-[var(--text-muted)]">
          <span>{leftAxisLabel}</span>
          <span>{rightAxisLabel}</span>
        </div>
      </div>
      {children}
    </div>
  );
}
