import type { ReactNode } from "react";
import { Skeleton } from "@/components/ui/skeleton";
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
    <section aria-hidden className={cn("surface-card flex w-full flex-col p-5", className)}>
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
    <div aria-hidden className={cn("flex flex-col gap-3", className)} style={{ height }}>
      <Skeleton className="h-4 w-1/3 shrink-0" />
      <Skeleton className="min-h-0 flex-1" />
    </div>
  );
}
