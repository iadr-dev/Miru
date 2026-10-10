import type { HTMLAttributes, ReactNode } from "react";
import { cn } from "@/lib/cn";

/** A loading placeholder shaped like the content it stands in for (web/CLAUDE.md §6: shaped
 * skeletons, never a bare spinner). The shimmer is a slow highlight sweep, not a pulse, and stops
 * under reduced motion. Size it with className (`h-4 w-1/3`). */
export function Skeleton({ className, ...rest }: HTMLAttributes<HTMLDivElement>): ReactNode {
  return (
    <div
      aria-hidden
      className={cn("skeleton rounded-[var(--radius-input)]", className)}
      {...rest}
    />
  );
}

/** A stack of text-line skeletons; the last line is shorter, like a real paragraph. */
export function SkeletonLines({
  lines = 3,
  className,
}: {
  lines?: number;
  className?: string;
}): ReactNode {
  return (
    <div aria-hidden className={cn("space-y-2.5", className)}>
      {Array.from({ length: lines }, (_, i) => (
        <Skeleton
          // biome-ignore lint/suspicious/noArrayIndexKey: a fixed-length decorative list.
          key={i}
          className={cn("h-3.5", i === lines - 1 ? "w-2/3" : "w-full")}
        />
      ))}
    </div>
  );
}
