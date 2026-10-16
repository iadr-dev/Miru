/**
 * The day timeline's geometry: where a Taipei wall-clock time sits on the track, the 28 intraday
 * check slots, and when the next check is due. Pure, so it is tested without a DOM.
 *
 * The track is piecewise linear rather than true-to-scale: the session (09:00–13:30) gets most of
 * the width because that is where the 28 checks are, and the evening refreshes (17:40, 18:00) would
 * otherwise sit on top of each other.
 */

/** [minutes since midnight, position in %] anchors, in order. */
const ANCHORS: readonly (readonly [number, number])[] = [
  [7 * 60 + 30, 0],
  [8 * 60, 4],
  [9 * 60, 14],
  [13 * 60 + 30, 60],
  [17 * 60 + 40, 72],
  [18 * 60, 82],
  [20 * 60 + 10, 95],
  [21 * 60, 100],
];

export const SESSION_START = 9 * 60;
export const SESSION_END = 13 * 60 + 30;
export const SLOT_MINUTES = 10;

export function minutesOf(hhmm: string): number {
  const [h, m] = hhmm.split(":").map(Number);
  return (h ?? 0) * 60 + (m ?? 0);
}

export function hhmm(minutes: number): string {
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}`;
}

/** Where `minutes` (since midnight, Taipei) sits on the track, in percent, clamped to the ends. */
export function positionOf(minutes: number): number {
  const first = ANCHORS[0];
  const last = ANCHORS.at(-1);
  if (first === undefined || last === undefined) return 0;
  if (minutes <= first[0]) return first[1];
  if (minutes >= last[0]) return last[1];
  for (let i = 1; i < ANCHORS.length; i++) {
    const [m1, p1] = ANCHORS[i] ?? last;
    const [m0, p0] = ANCHORS[i - 1] ?? first;
    if (minutes <= m1) return p0 + ((minutes - m0) / (m1 - m0)) * (p1 - p0);
  }
  return last[1];
}

/** The 28 check slots, "09:00" through "13:30". */
export const SESSION_SLOTS: readonly string[] = Array.from(
  { length: (SESSION_END - SESSION_START) / SLOT_MINUTES + 1 },
  (_, i) => hhmm(SESSION_START + i * SLOT_MINUTES),
);

/** The next check at or after `nowMinutes`, with minutes to wait, or null outside the session. */
export function nextCheck(nowMinutes: number): { slot: string; inMinutes: number } | null {
  if (nowMinutes > SESSION_END) return null;
  const from = Math.max(nowMinutes, SESSION_START);
  const next = Math.ceil((from - SESSION_START) / SLOT_MINUTES) * SLOT_MINUTES + SESSION_START;
  if (next > SESSION_END) return null;
  return { slot: hhmm(next), inMinutes: next - nowMinutes };
}

/** Taipei's date (YYYY-MM-DD) and minutes since midnight for `at`. */
export function taipeiClock(at: Date): { date: string; minutes: number } {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Taipei",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).formatToParts(at);
  const part = (type: string): string => parts.find((p) => p.type === type)?.value ?? "00";
  return {
    date: `${part("year")}-${part("month")}-${part("day")}`,
    minutes: Number(part("hour")) * 60 + Number(part("minute")),
  };
}
