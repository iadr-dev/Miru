"use client";

import { useEffect, useMemo, useRef, useState } from "react";

/** How many weeks of playback advance per real second — a fixed, UI-chosen constant (never
 * derived from any sector's own data magnitude, per web/OVERVIEW.md §4's non-trading-motion rule:
 * a bigger move never gets a faster animation). At this rate a ~26-week plotted history takes
 * ~65s to complete one loop — slow enough to actually watch each week's comet-tail/badge motion
 * settle before the next one starts, rather than a blur. This view has no user-facing transport
 * controls, so playback always runs (or, under reduced motion, always sits still) for its whole
 * mounted lifetime. */
const DEFAULT_WEEKS_PER_SECOND = 0.4;

/**
 * Drives 板塊輪動's continuous, always-on playback loop off `requestAnimationFrame` (never
 * `setInterval`/`setTimeout`) — `currentWeek` advances by `weeksPerSecond * delta` every
 * rendered frame, so it tracks the actual frame clock (pauses correctly on a backgrounded tab,
 * never drifts from wall-clock the way a fixed-interval timer would under frame drops). Uses
 * `requestAnimationFrame` directly rather than R3F's `useFrame` because this hook's own state
 * (`currentWeek`, `hoveredSectorId`) is consumed by `RankPanel`, a DOM sibling of the `<Canvas>`,
 * as well as by the 3D scene inside it — `useFrame` only runs for components rendered INSIDE a
 * `Canvas`, so it can't live here without forcing the DOM consumer to be a canvas child too.
 *
 * `currentWeek` is intentionally a float — `RotationCanvas`'s badges/comet-tails interpolate
 * their position between the two integer weeks it straddles every frame, which is what makes the
 * playback read as smooth motion rather than a weekly jump-cut. `enabled=false` (reduced motion)
 * freezes `currentWeek` at 0 rather than ticking — the scene shows its final/first state, never
 * an animated loop, per web/OVERVIEW.md §7's reduced-motion tiers.
 */
export function useRotationPlayback(
  weekCount: number,
  options?: { readonly weeksPerSecond?: number; readonly enabled?: boolean },
): {
  currentWeek: number;
  hoveredSectorId: string | null;
  setHoveredSectorId: (id: string | null) => void;
} {
  const weeksPerSecond = options?.weeksPerSecond ?? DEFAULT_WEEKS_PER_SECOND;
  const enabled = options?.enabled ?? true;
  const maxWeek = Math.max(weekCount - 1, 0);

  const [currentWeek, setCurrentWeek] = useState(0);
  const [hoveredSectorId, setHoveredSectorId] = useState<string | null>(null);
  const currentWeekRef = useRef(0);
  const lastFrameTimeRef = useRef<number | null>(null);

  useEffect(() => {
    if (!enabled || maxWeek <= 0) return;
    let frameId: number;
    const tick = (now: number): void => {
      const last = lastFrameTimeRef.current;
      lastFrameTimeRef.current = now;
      if (last !== null) {
        const delta = (now - last) / 1000;
        const next = currentWeekRef.current + weeksPerSecond * delta;
        // Loops back to the start once the trail reaches the latest plotted week — a
        // continuous "keeps rotating" narrative rather than a one-shot playback that
        // dead-ends and sits idle.
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
  }, [enabled, maxWeek, weeksPerSecond]);

  return useMemo(
    () => ({ currentWeek, hoveredSectorId, setHoveredSectorId }),
    [currentWeek, hoveredSectorId],
  );
}
