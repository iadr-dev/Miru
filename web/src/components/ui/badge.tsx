import type { ReactNode } from "react";
import { cn } from "@/lib/cn";

type Variant = "solid" | "outline";

/** The shared shape every categorical chip on this app renders through — `asset-header.tsx`
 * previously hand-rolled this same `<span>` 4 separate times (ETF structure/active badge, theme
 * tag, distribution-frequency badge), only ever varying color/background; per `web/CLAUDE.md`
 * §2.1 ("duplicated markup across components → one shared component"), this is that one place.
 * `variant="solid"` is the ETF-structure-badge look (opaque `--surface-2` background, colored
 * text); `variant="outline"` is the theme-tag/distribution-frequency look (translucent
 * background + hairline border in the same color, for a free-text/variable label). Neither
 * variant picks its own color — every caller supplies one (a fixed `--chart-N` token for a
 * categorical fact, `hashToChartToken(label)` for a variable one, or `--up`/`--down` for the one
 * deliberate signed exception, 外資連買/賣 — see `tw-stock-signal-badges.tsx`). */
export function Badge({
  label,
  color,
  variant = "solid",
}: {
  readonly label: string;
  readonly color: string;
  readonly variant?: Variant;
}): ReactNode {
  return (
    <span
      className={cn(
        "rounded-[var(--radius-pill)] px-2.5 py-1 font-medium text-[12px]",
        variant === "solid" && "bg-[var(--surface-2)]",
      )}
      style={
        variant === "outline"
          ? { backgroundColor: `${color}26`, color, border: `1px solid ${color}40` }
          : { color }
      }
    >
      {label}
    </span>
  );
}
