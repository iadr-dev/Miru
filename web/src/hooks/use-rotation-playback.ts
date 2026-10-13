"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

/** How many weeks of playback advance per real second — a fixed, UI-chosen constant (never
 * derived from any sector's own data magnitude, per web/OVERVIEW.md §4's non-trading-motion rule:
 * a bigger move never gets a faster animation). At this rate a ~26-week plotted history takes
 * ~65s to complete one loop — slow enough to actually watch each week's comet-tail/badge motion
 * settle before the next one starts, rather than a blur. */
const DEFAULT_WEEKS_PER_SECOND = 0.4;

export interface RotationPlayback {
  /** Fractional position in the weekly series: `Math.floor` is the week, the rest is the glide
   * toward the next one, so badges and tails move smoothly instead of jumping weekly. */
  readonly currentWeek: number;
  readonly hoveredSectorId: string | null;
  readonly setHoveredSectorId: (id: string | null) => void;
  readonly isPlaying: boolean;
  /** Play / pause. A no-op when motion is disabled (reduced motion): the scene is then driven by
   * scrubbing alone. */
  readonly togglePlaying: () => void;
  /** Jump to a week (clamped to the series) and pause there — scrubbing is always deliberate. */
  readonly seek: (week: number) => void;
}

/**
 * Drives 板塊輪動's playback clock off `requestAnimationFrame` (never `setInterval`/`setTimeout`) —
 * `currentWeek` advances by `weeksPerSecond * delta` every rendered frame, so it tracks the actual
 * frame clock (pauses correctly on a backgrounded tab, never drifts from wall-clock the way a
 * fixed-interval timer would under frame drops). Uses `requestAnimationFrame` directly rather than
 * R3F's `useFrame` because this hook's state (`currentWeek`, `hoveredSectorId`) is consumed by DOM
 * siblings of the `<Canvas>` (the rank panel, the timeline) as well as by the scene inside it —
 * `useFrame` only runs for components rendered INSIDE a `Canvas`.
 *
 * It starts playing from the oldest week and loops once it reaches the latest. `enabled=false`
 * (reduced motion) never ticks: the scene opens on the LATEST week — the most informative frozen
 * frame — and moves only when the user scrubs, which snaps rather than animates (reduced-motion
 * tier 2, web/OVERVIEW.md §7).
 */
export function useRotationPlayback(
  weekCount: number,
  options?: { readonly weeksPerSecond?: number; readonly enabled?: boolean },
): RotationPlayback {
  const weeksPerSecond = options?.weeksPerSecond ?? DEFAULT_WEEKS_PER_SECOND;
  const enabled = options?.enabled ?? true;
  const maxWeek = Math.max(weekCount - 1, 0);

  const [currentWeek, setCurrentWeek] = useState(() => (enabled ? 0 : maxWeek));
  const [isPlaying, setIsPlaying] = useState(enabled);
  const [hoveredSectorId, setHoveredSectorId] = useState<string | null>(null);
  const currentWeekRef = useRef(currentWeek);
  const lastFrameTimeRef = useRef<number | null>(null);

  const running = enabled && isPlaying && maxWeek > 0;

  useEffect(() => {
    if (!running) return;
    let frameId: number;
    const tick = (now: number): void => {
      const last = lastFrameTimeRef.current;
      lastFrameTimeRef.current = now;
      if (last !== null) {
        const delta = (now - last) / 1000;
        const next = currentWeekRef.current + weeksPerSecond * delta;
        // Loops back to the start once the trail reaches the latest plotted week — a
        // continuous "keeps rotating" narrative rather than a one-shot playback that dead-ends.
        const wrapped = next > maxWeek ? next % maxWeek : next;
        currentWeekRef.current = wrapped;
        setCurrentWeek(wrapped);
      }
      frameId = requestAnimationFrame(tick);
    };
    frameId = requestAnimationFrame(tick);
    return () => {
      cancelAnimationFrame(frameId);
      lastFrameTimeRef.current = null;
    };
  }, [running, maxWeek, weeksPerSecond]);

  const togglePlaying = useCallback(() => {
    if (enabled) setIsPlaying((playing) => !playing);
  }, [enabled]);

  const seek = useCallback(
    (week: number) => {
      const clamped = Math.min(Math.max(Number.isFinite(week) ? week : 0, 0), maxWeek);
      currentWeekRef.current = clamped;
      setCurrentWeek(clamped);
      setIsPlaying(false);
    },
    [maxWeek],
  );

  return useMemo(
    () => ({
      currentWeek,
      hoveredSectorId,
      setHoveredSectorId,
      isPlaying: running,
      togglePlaying,
      seek,
    }),
    [currentWeek, hoveredSectorId, running, togglePlaying, seek],
  );
}
