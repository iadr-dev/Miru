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
