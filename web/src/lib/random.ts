/**
 * Randomness for ids, retry jitter and copy rotation. Built on the Web Crypto API; only when it is
 * missing (some SSR / test runtimes, insecure contexts) does it fall back to a deterministic
 * counter, which is unique per page load but not unpredictable. Never use it for secrets.
 */

const UINT32_RANGE = 2 ** 32;
let fallbackCounter = 0;

function webCrypto(): Crypto | null {
  return typeof crypto === "undefined" ? null : crypto;
}

/** An integer in `[0, max)`. Rejection sampling keeps it free of modulo bias. */
export function secureRandomInt(max: number): number {
  if (!Number.isInteger(max) || max < 1 || max > UINT32_RANGE) {
    throw new RangeError("max must be an integer between 1 and 2^32");
  }
  const c = webCrypto();
  if (c === null || typeof c.getRandomValues !== "function") {
    return fallbackCounter++ % max;
  }
  const limit = UINT32_RANGE - (UINT32_RANGE % max);
  const buf = new Uint32Array(1);
  let value = 0;
  do {
    c.getRandomValues(buf);
    value = buf[0] ?? 0;
  } while (value >= limit);
  return value % max;
}

/** A fresh unique id. `crypto.randomUUID` needs a secure context, so fall back to random bytes,
 * then to a counter, so a caller never fails over it. */
export function secureId(): string {
  const c = webCrypto();
  if (c !== null && typeof c.randomUUID === "function") {
    return c.randomUUID();
  }
  if (c !== null && typeof c.getRandomValues === "function") {
    const bytes = c.getRandomValues(new Uint8Array(16));
    return `${Date.now().toString(36)}-${Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("")}`;
  }
  fallbackCounter += 1;
  return `${Date.now().toString(36)}-${fallbackCounter.toString(36).padStart(8, "0")}`;
}
