import { useEffect, useRef, useState } from "react";

const TYPE_MS = 28;

/**
 * Types `text` out once, character by character, then calls `onDone`. Used for the one-shot
 * reveal of a thread's auto-generated title (as opposed to `useTypewriterHints`'s looping
 * type/hold/erase cycle for composer placeholder examples). Honors `prefers-reduced-motion`:
 * renders the full text immediately and calls `onDone` on the same tick.
 *
 * `active` gates the effect so a row only types once, the first time it becomes the reveal
 * target — re-renders while inactive (or after completion) don't restart it.
 */
export function useTypewriterOnce(text: string, active: boolean, onDone: () => void): string {
  const [shown, setShown] = useState(active ? "" : text);
  const onDoneRef = useRef(onDone);
  onDoneRef.current = onDone;

  useEffect(() => {
    if (!active) {
      return;
    }
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches || text.length === 0) {
      setShown(text);
      onDoneRef.current();
      return;
    }

    let cancelled = false;
    let charIndex = 0;
    let timeoutId: ReturnType<typeof setTimeout>;

    const step = (): void => {
      if (cancelled) {
        return;
      }
      charIndex += 1;
      setShown(text.slice(0, charIndex));
      if (charIndex < text.length) {
        timeoutId = setTimeout(step, TYPE_MS);
      } else {
        onDoneRef.current();
      }
    };

    step();
    return () => {
      cancelled = true;
      clearTimeout(timeoutId);
    };
  }, [active, text]);

  return shown;
}
