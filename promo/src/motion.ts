// Copied from the skill's demos/_fixtures/Motion.tsx (easing table, seg, lerp, deterministic
// rand) so every adapted shot keeps the exact curves its demo was tuned with.
export const E = {
  linear: (t: number) => t,
  outQuad: (t: number) => t * (2 - t),
  inOutQuad: (t: number) => (t < 0.5 ? 2 * t * t : -1 + (4 - 2 * t) * t),
  inCubic: (t: number) => t * t * t,
  outCubic: (t: number) => 1 - Math.pow(1 - t, 3),
  inOutCubic: (t: number) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2),
  outQuart: (t: number) => 1 - Math.pow(1 - t, 4),
  outBack: (t: number, s = 1.70158) => 1 + (s + 1) * Math.pow(t - 1, 3) + s * Math.pow(t - 1, 2),
};

export const lerp = (t: number, a: number, b: number) => a + (b - a) * t;

export const seg = (t: number, t0: number, t1: number, ease: (x: number) => number = E.linear) =>
  ease(Math.min(1, Math.max(0, (t - t0) / (t1 - t0))));

// Deterministic pseudo-random (same formula and seeds as the demos): no Math.random anywhere.
export const rand = (seed: number) => {
  const x = Math.sin(seed * 127.1 + 311.7) * 43758.5453;
  return x - Math.floor(x);
};

// Normalised progress of a shot: 0 on its first frame, 1 on its last (the demos' useT mapping).
export const tOf = (frame: number, duration: number) =>
  Math.min(1, Math.max(0, frame / Math.max(1, duration - 1)));
