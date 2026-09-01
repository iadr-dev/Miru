import type { ReactNode } from "react";
import { cn } from "@/lib/cn";

/**
 * Wraps a Market-page panel card in the animated rainbow-gradient border
 * (`.market-rainbow-border`, `web/src/app/globals.css`) — a deliberate, documented exception to
 * design-system.md §8's anti-gradient rule, scoped to `/market` cards only. Every market card
 * renders its own `<section className="rounded-[var(--radius-card)] border ...">` shell; this
 * wraps that shell rather than replacing it; the plain `--border` hairline on the inner
 * `<section>` still shows through the animated ring's padding.
 */
export function RainbowBorderCard({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}): ReactNode {
  return (
    <div className={cn("market-rainbow-border rounded-[var(--radius-card)]", className)}>
      {children}
    </div>
  );
}
