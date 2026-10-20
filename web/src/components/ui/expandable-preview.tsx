"use client";

import type { ReactNode } from "react";
import { ExpandModal } from "@/components/ui/expand-modal";

/**
 * A capped inline preview plus an "expand to N" button that opens the full content in an
 * `ExpandModal`. The caller owns the `expanded` state and passes already-translated labels.
 */
export function ExpandablePreview({
  preview,
  full,
  showExpand,
  expandLabel,
  title,
  closeLabel,
  expanded,
  onExpandedChange,
}: Readonly<{
  preview: ReactNode;
  full: ReactNode;
  showExpand: boolean;
  expandLabel: string;
  title: string;
  closeLabel: string;
  expanded: boolean;
  onExpandedChange: (next: boolean) => void;
}>): ReactNode {
  return (
    <>
      {preview}
      {showExpand ? (
        <button
          type="button"
          onClick={() => onExpandedChange(true)}
          className="mt-2.5 flex min-h-11 w-full items-center justify-center gap-1.5 rounded-[var(--radius-input)] bg-[var(--surface-2)] text-[12.5px] text-[var(--text-muted)] transition-colors hover:bg-[var(--surface-3)] hover:text-[var(--text)]"
        >
          {expandLabel}
        </button>
      ) : null}
      <ExpandModal
        open={expanded}
        title={title}
        closeLabel={closeLabel}
        onClose={() => onExpandedChange(false)}
      >
        {full}
      </ExpandModal>
    </>
  );
}
