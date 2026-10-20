import { useEffect, useRef, useState } from "react";
import { secureRandomInt } from "@/lib/random";

const TYPE_MS = 45;
const ERASE_MS = 22;
const HOLD_MS = 2200;
const PAUSE_MS = 400;

/**
 * Cycles a pool of example-question strings through a type → hold → erase → pick-next loop,
 * rendering the in-progress text so a caller can splice it into a static placeholder prefix.
 * Never repeats the immediately-previous pick (per a "random, not just sequential" cycle).
 * Honors `prefers-reduced-motion`: renders the first hint fully typed, static, no animation.
 */
export function useTypewriterHints(hints: readonly string[]): string {
  const [text, setText] = useState("");
  const reducedMotionRef = useRef(false);

  useEffect(() => {
    reducedMotionRef.current = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reducedMotionRef.current || hints.length === 0) {
      setText(hints[0] ?? "");
      return;
    }

    let cancelled = false;
    let timeoutId: ReturnType<typeof setTimeout>;
    let lastIndex = -1;

    const pickNext = (): string => {
      if (hints.length === 1) {
        return hints[0] as string;
      }
      let index = lastIndex;
      while (index === lastIndex) {
        index = secureRandomInt(hints.length);
      }
      lastIndex = index;
      return hints[index] as string;
    };

    const runCycle = (): void => {
      const target = pickNext();
      let charIndex = 0;

      const typeStep = (): void => {
        if (cancelled) {
          return;
        }
        charIndex += 1;
        setText(target.slice(0, charIndex));
        if (charIndex < target.length) {
          timeoutId = setTimeout(typeStep, TYPE_MS);
        } else {
          timeoutId = setTimeout(eraseStep, HOLD_MS);
        }
      };

      const eraseStep = (): void => {
        if (cancelled) {
          return;
        }
        charIndex -= 1;
        setText(target.slice(0, charIndex));
        if (charIndex > 0) {
          timeoutId = setTimeout(eraseStep, ERASE_MS);
        } else {
          timeoutId = setTimeout(runCycle, PAUSE_MS);
        }
      };

      typeStep();
    };

    runCycle();
    return () => {
      cancelled = true;
      clearTimeout(timeoutId);
    };
  }, [hints]);

  return text;
}
