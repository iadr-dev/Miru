"use client";

import type { ReactNode } from "react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/cn";

/** Sticky save/cancel bar shown when a Settings form pane is dirty. */
export function DirtyBar({
  dirty,
  saving,
  saveLabel,
  cancelLabel,
  onSave,
  onCancel,
}: {
  dirty: boolean;
  saving: boolean;
  saveLabel: string;
  cancelLabel: string;
  onSave: () => void;
  onCancel: () => void;
}): ReactNode {
  if (!dirty) {
    return null;
  }
  return (
    <div
      className={cn(
        "sticky bottom-0 z-10 -mx-5 mt-4 flex items-center justify-end gap-2 border-t",
        "border-[var(--border)] bg-[var(--surface)] px-5 py-3",
      )}
    >
      <Button variant="ghost" size="sm" onClick={onCancel} disabled={saving}>
        {cancelLabel}
      </Button>
      <Button variant="filled" size="sm" onClick={onSave} disabled={saving}>
        {saveLabel}
      </Button>
    </div>
  );
}
