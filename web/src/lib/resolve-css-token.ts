/** Resolves a CSS custom property to its computed color — `lightweight-charts` draws to canvas
 * and can't read `var(--chart-1)` itself, so the token has to be resolved to a real color string
 * before being handed to the chart's options. Shared by every `lightweight-charts` chart
 * component (`timeseries-chart.tsx`, `technical-analysis-panel.tsx`). */
export function resolveToken(token: string): string {
  if (typeof window === "undefined") {
    return "#8a93a3";
  }
  const match = /var\((--[a-z0-9-]+)\)/.exec(token);
  if (!match) {
    return token;
  }
  const varName = match[1];
  if (!varName) {
    return token;
  }
  return getComputedStyle(document.documentElement).getPropertyValue(varName).trim() || token;
}
