"use client";

import { useEffect, useState } from "react";

/** Returns `value`, delayed by `delayMs` after it stops changing — the standard type-ahead
 * debounce (search inputs, filter fields) so a query fires once per pause in typing, not once
 * per keystroke. */
export function useDebouncedValue<T>(value: T, delayMs: number): T {
  const [debounced, setDebounced] = useState(value);

  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delayMs);
    return () => clearTimeout(timer);
  }, [value, delayMs]);

  return debounced;
}
