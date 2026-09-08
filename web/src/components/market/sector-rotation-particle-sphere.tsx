"use client";

import { useReducedMotion } from "motion/react";
import { useLocale, useTranslations } from "next-intl";
import { type ReactNode, useMemo } from "react";
import { formatNumber } from "@/app/[locale]/(app)/chat/cards/card-format";
import { SectorRotationBarTable } from "@/components/market/sector-rotation-bar-chart";
import { cn } from "@/lib/cn";
import type { SectorRotationDayRow } from "@/types/market";

const SPHERE_RADIUS = 130;
const MIN_NODE_SIZE = 8;
const MAX_NODE_SIZE = 28;
// A fixed, data-independent rotation period — every render of this view spins at the SAME
// speed regardless of how large any industry's net_flow is (web/CLAUDE.md §4 "non-trading
// motion": a bigger move never gets a bigger/faster animation). Only each node's static
// size/color encodes the data, never the rotation itself.
const ROTATION_PERIOD_S = 32;
// Plotting every industry (often 40+) on the sphere makes nodes overlap into an unreadable
// blob — same "top-N-not-whole-market reasoning for a ranked visual" `SectorRotationBumpChart`
// (its predecessor) used, not a data-completeness cut: the table below still lists every
// industry actually present, ranked, so nothing is hidden — only the SPATIAL rendering is capped.
const MAX_PLOTTED_NODES = 24;

interface SphereNode {
  row: SectorRotationDayRow;
  x: number;
  y: number;
  z: number;
  size: number;
}

/** Distributes `rows.length` points evenly across a sphere's surface via the Fibonacci-sphere
 * (golden-spiral) method — the standard even-point-distribution algorithm, avoiding the
 * pole-clustering a naive lat/long grid would produce. Each point's radius from center encodes
 * nothing (purely a layout algorithm); node SIZE (below) is what encodes `|net_flow|` magnitude. */
export function layoutSphere(rows: readonly SectorRotationDayRow[]): SphereNode[] {
  const n = rows.length;
  if (n === 0) return [];
  const maxAbsFlow = Math.max(...rows.map((r) => Math.abs(r.net_flow)), 1);
  const goldenAngle = Math.PI * (3 - Math.sqrt(5));
  return rows.map((row, index) => {
    const yFrac = 1 - (index / Math.max(n - 1, 1)) * 2; // 1 → -1
    const radiusAtY = Math.sqrt(Math.max(1 - yFrac * yFrac, 0));
    const theta = goldenAngle * index;
    const magnitude = Math.min(Math.abs(row.net_flow) / maxAbsFlow, 1);
    return {
      row,
      x: Math.cos(theta) * radiusAtY * SPHERE_RADIUS,
      y: yFrac * SPHERE_RADIUS,
      z: Math.sin(theta) * radiusAtY * SPHERE_RADIUS,
      size: MIN_NODE_SIZE + magnitude * (MAX_NODE_SIZE - MIN_NODE_SIZE),
    };
  });
}

/**
 * 板塊輪動 球體粒子 view — one node per industry (latest trading day), laid out on a sphere via
 * `layoutSphere`, continuously rotating at a FIXED speed (never magnitude-scaled, per
 * web/CLAUDE.md §4). A node's SIZE encodes `|net_flow|` magnitude and its COLOR is `--up`/`--down`
 * by sign — the same signed-money encoding `SectorRotationBarChart` uses, just spatial instead of
 * a bar length. Pure CSS 3D transforms (no new charting/WebGL dependency): each node is a
 * `position: absolute` div positioned via `translate3d`, the whole scene rotates via a CSS
 * keyframe on the container — `motion/react` stays the only animation library in this bundle.
 *
 * The sphere itself is `aria-hidden` — it's a decorative/exploratory visual with no meaningful DOM
 * reading order, so `SectorRotationBarTable` underneath (the same table `SectorRotationBarChart`
 * uses) is this view's real accessible content, not a fallback for a chart that degrades
 * gracefully on its own.
 */
export function SectorRotationParticleSphere({
  rows,
  tradeDates,
}: {
  readonly rows: readonly SectorRotationDayRow[];
  readonly tradeDates: readonly string[];
}): ReactNode {
  const locale = useLocale();
  const t = useTranslations("market.cards.sectorHeatmap");
  const reduceMotion = useReducedMotion();

  const latestDate = tradeDates.at(-1);
  const latestRows = useMemo(
    () =>
      rows
        .filter((row) => row.trade_date === latestDate)
        .sort((a, b) => Math.abs(b.net_flow) - Math.abs(a.net_flow)),
    [rows, latestDate],
  );
  const nodes = useMemo(() => layoutSphere(latestRows.slice(0, MAX_PLOTTED_NODES)), [latestRows]);

  if (rows.length === 0 || tradeDates.length === 0) {
    return <p className="text-[13px] text-[var(--text-muted)]">{t("rotationUnavailable")}</p>;
  }

  return (
    <div>
      <div
        aria-hidden
        className="relative mx-auto flex items-center justify-center"
        style={{ width: SPHERE_RADIUS * 2 + 60, height: SPHERE_RADIUS * 2 + 60, perspective: 900 }}
      >
        <div
          className={cn(
            "relative",
            reduceMotion ? null : "animate-[sector-sphere-spin_32s_linear_infinite]",
          )}
          style={{
            width: 0,
            height: 0,
            transformStyle: "preserve-3d",
            animationDuration: `${ROTATION_PERIOD_S}s`,
          }}
        >
          {nodes.map((node) => {
            const up = node.row.net_flow >= 0;
            return (
              <div
                key={node.row.industry}
                className="absolute flex items-center justify-center rounded-full font-num text-[9px] tabular-nums"
                style={{
                  width: node.size * 2,
                  height: node.size * 2,
                  left: -node.size,
                  top: -node.size,
                  transform: `translate3d(${node.x}px, ${-node.y}px, ${node.z}px)`,
                  backgroundColor: up ? "var(--up)" : "var(--down)",
                  color: "var(--surface)",
                }}
                title={`${node.row.industry} ${formatNumber(node.row.net_flow, locale)}`}
              />
            );
          })}
        </div>
      </div>

      <SectorRotationBarTable rows={[...latestRows].sort((a, b) => b.net_flow - a.net_flow)} />
    </div>
  );
}
