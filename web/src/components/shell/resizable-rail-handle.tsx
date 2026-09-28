"use client";

import type { KeyboardEvent, ReactNode, PointerEvent as ReactPointerEvent } from "react";
import { useCallback, useRef } from "react";
import { WATCHLIST_WIDTH_MIN } from "@/components/shell/use-shell-ui";

// Dragging the handle within this many px of the collapse edge (min width) snaps the rail
// closed in the same gesture — the Claude-Code-artifact-panel behavior the user referenced
// (drag to resize; drag far enough and it closes), not a separate resize-only affordance.
const COLLAPSE_SNAP_THRESHOLD_PX = 40;

/**
 * A 6px draggable strip on the watchlist rail's left edge — `cursor: col-resize`, plain pointer
 * events (no new dependency; this app has no resizable-panel library and one panel doesn't
 * justify pulling one in). Dragging left/right resizes within `[WATCHLIST_WIDTH_MIN,
 * WATCHLIST_WIDTH_MAX]`; dragging past `COLLAPSE_SNAP_THRESHOLD_PX` below the min width calls
 * `onCollapse` (the existing `closeWatchlist()` from `useWatchlistOpen()`) instead of clamping —
 * confirmed with the user as the intended one-gesture close behavior.
 */
export function ResizableRailHandle({
  width,
  onWidthChange,
  onCollapse,
  ariaLabel,
}: {
  readonly width: number;
  readonly onWidthChange: (width: number) => void;
  readonly onCollapse: () => void;
  readonly ariaLabel: string;
}): ReactNode {
  const dragState = useRef<{ startX: number; startWidth: number } | null>(null);

  const handlePointerMove = useCallback(
    (event: PointerEvent): void => {
      const drag = dragState.current;
      if (!drag) return;
      // The rail sits on the right edge of the viewport — dragging the handle LEFT (negative
      // delta) grows the rail, dragging RIGHT shrinks it, so width tracks `-deltaX`.
      const deltaX = event.clientX - drag.startX;
      const nextWidth = drag.startWidth - deltaX;
      if (nextWidth < WATCHLIST_WIDTH_MIN - COLLAPSE_SNAP_THRESHOLD_PX) {
        onCollapse();
        return;
      }
      onWidthChange(nextWidth);
    },
    [onCollapse, onWidthChange],
  );

  const handlePointerUp = useCallback((): void => {
    dragState.current = null;
    window.removeEventListener("pointermove", handlePointerMove);
    window.removeEventListener("pointerup", handlePointerUp);
  }, [handlePointerMove]);

  const handlePointerDown = useCallback(
    (event: ReactPointerEvent<HTMLButtonElement>): void => {
      dragState.current = { startX: event.clientX, startWidth: width };
      window.addEventListener("pointermove", handlePointerMove);
      window.addEventListener("pointerup", handlePointerUp);
    },
    [width, handlePointerMove, handlePointerUp],
  );

  // Keyboard equivalent of the drag gesture — arrow keys resize in 16px steps, matching the
  // reduced-motion/keyboard-first accessibility convention the rest of this rail follows.
  const handleKeyDown = useCallback(
    (event: KeyboardEvent<HTMLButtonElement>): void => {
      const STEP = 16;
      if (event.key === "ArrowLeft") {
        onWidthChange(width + STEP);
        event.preventDefault();
      } else if (event.key === "ArrowRight") {
        onWidthChange(width - STEP);
        event.preventDefault();
      }
    },
    [width, onWidthChange],
  );

  return (
    <button
      type="button"
      aria-label={ariaLabel}
      onPointerDown={handlePointerDown}
      onKeyDown={handleKeyDown}
      className="group relative z-10 w-1.5 shrink-0 cursor-col-resize touch-none border-0 bg-transparent p-0 focus-visible:outline-none"
    >
      <span
        aria-hidden
        className="absolute inset-y-0 left-1/2 w-px -translate-x-1/2 bg-[var(--border)] transition-colors group-hover:bg-[var(--accent)] group-focus-visible:bg-[var(--accent)]"
      />
    </button>
  );
}
