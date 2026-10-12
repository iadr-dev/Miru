"use client";

import { motion, useReducedMotion } from "motion/react";
import type { ReactNode } from "react";
import { cn } from "@/lib/cn";
import { entrance } from "@/lib/motion";

/** A calm "nothing here" panel: an icon, a title, one line of help and an optional action.
 * `compact` is for inline coverage gaps ("no data for this range") - it keeps the surrounding
 * frame and has no call to action; the roomier form is for a first-run empty list
 * (web/OVERVIEW.md §6 - do not conflate the two). */
export function EmptyState({
  icon,
  title,
  description,
  action,
  compact = false,
  className,
}: {
  icon?: ReactNode;
  title: ReactNode;
  description?: ReactNode;
  action?: ReactNode;
  compact?: boolean;
  className?: string;
}): ReactNode {
  const reduceMotion = useReducedMotion();
  return (
    <motion.div
      initial="hidden"
      animate="show"
      variants={entrance(reduceMotion)}
      className={cn(
        "flex flex-col items-center text-center",
        compact ? "gap-1.5 py-6" : "gap-3 px-6 py-12",
        className,
      )}
    >
      {icon ? (
        <span
          aria-hidden
          className="flex size-11 items-center justify-center rounded-full border border-[var(--border)] bg-[var(--surface-2)] text-[var(--text-muted)]"
        >
          {icon}
        </span>
      ) : null}
      <p className="font-medium text-[14px] text-[var(--text)]">{title}</p>
      {description ? (
        <p className="max-w-sm text-[13px] text-[var(--text-muted)] leading-relaxed">
          {description}
        </p>
      ) : null}
      {action ? <div className="mt-2">{action}</div> : null}
    </motion.div>
  );
}
