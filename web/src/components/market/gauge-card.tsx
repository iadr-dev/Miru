"use client";

import type { ReactNode } from "react";
import { formatNumber } from "@/app/[locale]/(app)/chat/cards/card-format";
import { cn } from "@/lib/cn";

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
  const activeBand = value === null ? null : bands.find((b) => value >= b.min && value < b.max);
  const needleAngle =
    value === null ? GAUGE_MIN_ANGLE : valueToAngleDeg(value, domainMin, domainMax);
  const needleTip = polarToCartesian(needleAngle, NEEDLE_LENGTH);

  return (
    <div className="flex items-center gap-6">
      <div className="relative shrink-0" style={{ width: GAUGE_WIDTH_PX }}>
        <svg viewBox="0 0 200 118" width={GAUGE_WIDTH_PX} aria-hidden role="presentation">
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
                strokeWidth={16}
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
          <text x={20} y={112} fontSize={9} fill="var(--text-muted)">
            {leftAxisLabel}
          </text>
          <text x={150} y={112} fontSize={9} fill="var(--text-muted)">
            {rightAxisLabel}
          </text>
        </svg>
        <div
          className="absolute top-[66%] left-1/2 -translate-x-1/2 -translate-y-1/2 text-center"
          role="group"
          aria-label={
            value !== null
              ? `${title} ${formatNumber(value, "zh-TW", { decimals })}${unit ?? ""}${
                  activeBand ? ` ${activeBand.label}` : ""
                }`
              : `${title}：資料暫無`
          }
        >
          <div className="font-num text-[34px] text-[var(--text)] tabular-nums">
            {value !== null ? formatNumber(value, "zh-TW", { decimals }) : "—"}
            {value !== null ? unit : ""}
          </div>
          {activeBand ? (
            <div
              className={cn("mt-0.5 text-[13px]")}
              style={{ color: activeBand.color }}
              aria-hidden
            >
              {activeBand.label}
            </div>
          ) : null}
        </div>
      </div>
      {children}
    </div>
  );
}
