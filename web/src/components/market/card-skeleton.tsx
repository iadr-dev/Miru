import type { ReactNode } from "react";
import { cn } from "@/lib/cn";

/**
 * The loading-state placeholder for a market card — renders the SAME `<section>` shell (border/
 * padding/radius/background) every real card in this dashboard already uses, so the card's own
 * box never changes size between loading and loaded (web/CLAUDE.md §6: shaped skeleton, never a
 * layout shift). `height` should match that specific card's typical loaded content height —
 * passed per call site via `QueryState`'s `skeleton` prop, not a one-size-fits-all guess.
 */
export function CardSkeleton({
  height,
  className,
}: {
  readonly height: number;
  readonly className?: string;
}): ReactNode {
  return (
    <section
      aria-hidden
      className={cn(
        "flex w-full flex-col rounded-[var(--radius-card)] border border-[var(--border)] bg-[var(--surface)] p-5",
        className,
      )}
    >
      <CardSkeletonBody height={height} />
    </section>
  );
}

/**
 * Just the shimmer block, no `<section>` shell — for a panel that already renders its own
 * `<section>` + title outside `QueryState` (e.g. `MaterialAnnouncementFeedPanel`) and only needs
 * the content area itself held at a stable height while loading.
 */
export function CardSkeletonBody({
  height,
  className,
}: {
  readonly height: number;
  readonly className?: string;
}): ReactNode {
  return (
    <div aria-hidden className={cn("animate-pulse space-y-3", className)} style={{ height }}>
      <div className="h-4 w-1/3 rounded-[var(--radius-input)] bg-[var(--surface-2)]" />
      <div className="h-full rounded-[var(--radius-input)] bg-[var(--surface-2)]" />
    </div>
  );
}
