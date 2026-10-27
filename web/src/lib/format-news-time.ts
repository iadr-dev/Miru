import { parseServerDate } from "@/lib/parse-server-date";

const MINUTE_MS = 60_000;
const HOUR_MS = 60 * MINUTE_MS;
const DAY_MS = 24 * HOUR_MS;

type NewsTime = {
  /** What the row shows: "5 minutes ago", "3 hours ago", or "10/27 20:10" for anything older. */
  readonly label: string;
  /** The full local date and time, for the tooltip. */
  readonly full: string;
};

/** A news timestamp for people. The API sends a UTC ISO string (`2026-10-27T12:10:05Z`); this turns
 * it into a relative label for the last day and a short local date and time after that, in the
 * viewer's locale and time zone. A value that is not a date comes back unchanged, so a bad feed
 * item never renders as "Invalid Date". `now` is injected so the result is testable. */
export function formatNewsTime(iso: string, locale: string, now: Date = new Date()): NewsTime {
  const date = parseServerDate(iso);
  if (Number.isNaN(date.getTime())) {
    return { label: iso, full: iso };
  }
  const full = new Intl.DateTimeFormat(locale, { dateStyle: "medium", timeStyle: "short" }).format(
    date,
  );
  const age = now.getTime() - date.getTime();
  if (age < 0 || age >= DAY_MS) {
    const short = new Intl.DateTimeFormat(locale, {
      month: "numeric",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    }).format(date);
    return { label: short, full };
  }
  const relative = new Intl.RelativeTimeFormat(locale, { numeric: "auto" });
  if (age < MINUTE_MS) {
    return { label: relative.format(0, "second"), full };
  }
  if (age < HOUR_MS) {
    return { label: relative.format(-Math.floor(age / MINUTE_MS), "minute"), full };
  }
  return { label: relative.format(-Math.floor(age / HOUR_MS), "hour"), full };
}
