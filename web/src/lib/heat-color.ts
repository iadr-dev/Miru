/**
 * Maps a signed percentage change to an `--up`/`--down` intensity — weak tint near zero,
 * full-strength token past ±3% (a calm, non-magnitude-scaled clamp, not a per-tile computed hex:
 * only the *mix ratio* between the two existing tokens varies, never a new color). Shared by
 * every heat-tile/heat-node surface (`sector-heatmap-treemap.tsx`, `stock-heatmap-treemap.tsx`,
 * the 產業分析 tier-tile/supply-chain-graph views) so the color scale never drifts between them.
 */
export function heatColor(pctChange: number): string {
  const magnitude = Math.min(Math.abs(pctChange) / 3, 1);
  const token = pctChange >= 0 ? "--up" : "--down";
  const weakToken = pctChange >= 0 ? "--up-weak" : "--down-weak";
  const pct = Math.round(30 + magnitude * 70);
  return `color-mix(in srgb, var(${token}) ${pct}%, var(${weakToken}))`;
}
