import { useEffect, useRef, useState } from "react";

/** Minimum reveal rate (characters/second) — the floor even when the backlog is tiny, so a short
 * sentence still visibly types rather than appearing to just "pop" in. */
const MIN_CHARS_PER_SECOND = 45;
/** Once the undisplayed backlog exceeds this many characters, the reveal rate scales up
 * proportionally (backlog / `CATCH_UP_SECONDS`) so a burst of buffered sentence-delayed text
 * (this codebase batches `message.delta` per completed sentence, not per token) never leaves the
 * typewriter trailing minutes behind the model — it catches up within roughly this many seconds
 * instead of trickling out at the fixed floor rate forever. */
const CATCH_UP_BACKLOG_THRESHOLD = 120;
const CATCH_UP_SECONDS = 2.5;

/** Smooths a monotonically-growing streamed string into a character-by-character reveal — the
 * Claude-Code-style typing effect. `target` arrives in sentence-sized jumps (this codebase's
 * `message.delta` batches per completed, guardrail-scanned sentence, never per raw token — see
 * `active-turn-view.tsx`'s module docstring), so rendering `target` directly makes the answer
 * visibly jump in sentence-sized chunks instead of a smooth continuous type. This hook decouples
 * "how much text has arrived" from "how much is shown": it advances the displayed text toward
 * `target` at a steady rate, speeding up only when the backlog grows large enough that a fixed
 * rate would fall meaningfully behind (a fast-finishing short turn should still feel like it
 * "finished typing" promptly, not sit at a stale partial for seconds after the real answer landed).
 *
 * Never reveals ahead of `target` (`target` is the sole source of truth — this never invents
 * characters), and snaps instantly to `target` whenever `target` is reset to a shorter string
 * (a new turn reusing the same component) or when `reducedMotion` is set (accessibility: no
 * motion-driven reveal, the full text is simply present). */
export function useTypewriter(target: string, reducedMotion: boolean): string {
  const [displayed, setDisplayed] = useState(target);
  const displayedRef = useRef(target);
  const targetRef = useRef(target);
  const frameRef = useRef<number | null>(null);
  const lastTickRef = useRef<number | null>(null);

  targetRef.current = target;

  useEffect(() => {
    // A shorter target than what's already displayed means the underlying text was reset (a new
    // turn), not that the stream retracted characters — snap to it rather than "erasing" forward.
    if (target.length < displayedRef.current.length || reducedMotion) {
      displayedRef.current = target;
      setDisplayed(target);
    }
  }, [target, reducedMotion]);

  useEffect(() => {
    if (reducedMotion) {
      return;
    }

    function tick(now: number): void {
      const last = lastTickRef.current;
      lastTickRef.current = now;
      const currentTarget = targetRef.current;
      const currentDisplayed = displayedRef.current;

      if (currentDisplayed.length >= currentTarget.length) {
        frameRef.current = requestAnimationFrame(tick);
        return;
      }

      const deltaSeconds = last === null ? 0 : (now - last) / 1000;
      const backlog = currentTarget.length - currentDisplayed.length;
      const rate =
        backlog > CATCH_UP_BACKLOG_THRESHOLD ? backlog / CATCH_UP_SECONDS : MIN_CHARS_PER_SECOND;
      const advance = Math.max(1, Math.round(rate * deltaSeconds));
      const nextLength = Math.min(currentTarget.length, currentDisplayed.length + advance);
      const next = currentTarget.slice(0, nextLength);
      displayedRef.current = next;
      setDisplayed(next);
      frameRef.current = requestAnimationFrame(tick);
    }

    frameRef.current = requestAnimationFrame(tick);
    return () => {
      if (frameRef.current !== null) {
        cancelAnimationFrame(frameRef.current);
      }
      frameRef.current = null;
      lastTickRef.current = null;
    };
  }, [reducedMotion]);

  return displayed;
}
