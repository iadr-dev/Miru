import type { ButtonHTMLAttributes, ReactNode } from "react";
import { cn } from "@/lib/cn";

type Variant = "filled" | "ghost" | "outline";
type Size = "sm" | "md";

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: Variant;
  size?: Size;
};

/** Vendored button, tokenized to the 藍鵲 palette (never the default shadcn skin).
 * `--accent` is chrome-only, so the filled CTA uses it here. Every variant meets the 44px
 * minimum touch target and inherits the global focus-visible ring. */
const VARIANTS: Record<Variant, string> = {
  filled: "bg-[var(--accent)] text-[var(--bg)] hover:opacity-90 font-medium",
  ghost: "bg-transparent text-[var(--text)] hover:bg-[var(--surface-2)]",
  outline:
    "bg-transparent text-[var(--text)] border border-[var(--border)] hover:bg-[var(--surface-2)]",
};

const SIZES: Record<Size, string> = {
  sm: "min-h-11 px-4 text-sm",
  md: "min-h-11 px-5 text-[15px]",
};

export function Button({
  variant = "filled",
  size = "md",
  className,
  type,
  children,
  ...rest
}: ButtonProps): ReactNode {
  return (
    <button
      type={type ?? "button"}
      className={cn(
        "inline-flex items-center justify-center gap-2 rounded-[var(--radius-button)]",
        "transition-colors duration-150 active:scale-[0.98]",
        "disabled:cursor-not-allowed disabled:opacity-50",
        VARIANTS[variant],
        SIZES[size],
        className,
      )}
      {...rest}
    >
      {children}
    </button>
  );
}
