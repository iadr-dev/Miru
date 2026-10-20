import type { HTMLAttributes, ReactNode } from "react";
import { cn } from "@/lib/cn";

type Padding = "none" | "sm" | "md" | "lg";

const PADDING: Record<Padding, string> = {
  none: "",
  sm: "p-3",
  md: "p-4 sm:p-5",
  lg: "p-5 sm:p-6",
};

type CardProps = HTMLAttributes<HTMLElement> & {
  /** The element to render: a `section` when the card is a titled region, otherwise a `div`. */
  as?: "div" | "section" | "article";
  padding?: Padding;
  /** Adds the hover lift; only for cards that are actually clickable. */
  interactive?: boolean;
};

/** The card: one material (radius, hairline border, fill, resting elevation - see `surface-card`
 * in globals.css) with a padding scale. Prefer this over repeating the classes. */
export function Card({
  as: Tag = "div",
  padding = "md",
  interactive = false,
  className,
  children,
  ...rest
}: CardProps): ReactNode {
  return (
    <Tag
      className={cn(
        interactive ? "surface-card-interactive" : "surface-card",
        PADDING[padding],
        className,
      )}
      {...rest}
    >
      {children}
    </Tag>
  );
}

/** A card heading row: title (+ optional one-line description) with an optional trailing action.
 * Keeps every card's header the same size and rhythm. */
export function CardHeader({
  title,
  description,
  action,
  className,
}: Readonly<{
  title: ReactNode;
  description?: ReactNode;
  action?: ReactNode;
  className?: string;
}>): ReactNode {
  return (
    <div className={cn("mb-4 flex items-start justify-between gap-3", className)}>
      <div className="min-w-0">
        <h2 className="font-semibold text-[15px] text-[var(--text)] leading-snug">{title}</h2>
        {description ? (
          <p className="mt-1 text-[12px] text-[var(--text-muted)] leading-relaxed">{description}</p>
        ) : null}
      </div>
      {action ? <div className="shrink-0">{action}</div> : null}
    </div>
  );
}
