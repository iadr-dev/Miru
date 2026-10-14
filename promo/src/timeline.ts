// The film's single timing source. Shots, captions and every SFX pin are expressed relative to it.
// Review fixes: ask 120→150 (English typing back to the card's ~3 f/char), converge 168→200 and
// coverage 176→186 (their motion keeps the card's tuned length; the extra frames are a real hold so
// the caption / closing phrase is read before the cut), brand 176→206 (lockup hold ≥1 s, R1).
export const SHOTS = {
  noise: { from: 0, duration: 165 },
  ask: { from: 165, duration: 150 },
  converge: { from: 315, duration: 200 },
  answer: { from: 515, duration: 180 },
  title: { from: 695, duration: 90 },
  coverage: { from: 785, duration: 186 },
  brand: { from: 971, duration: 206 },
} as const;

// Converge's motion runs over the card's tuned 168 f; the rest of its shot is hold.
export const CONVERGE_MOTION = 168;

export const TOTAL_FRAMES = SHOTS.brand.from + SHOTS.brand.duration; // 1177 ≈ 39.2 s
