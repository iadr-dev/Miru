/** Every cell for one month view of a 7-column calendar grid, including leading/trailing
 * adjacent-month days needed to fill the first/last week. Shared by every calendar-shaped
 * table on the site (`DividendCalendarTable`, the stock-detail page's own event calendar
 * card) so the grid-building logic can't drift between them. */
export function buildMonthGrid(year: number, month: number): { date: Date; inMonth: boolean }[] {
  const firstOfMonth = new Date(year, month - 1, 1);
  const firstWeekday = firstOfMonth.getDay();
  const daysInMonth = new Date(year, month, 0).getDate();
  const daysInPrevMonth = new Date(year, month - 1, 0).getDate();

  const cells: { date: Date; inMonth: boolean }[] = [];
  for (let i = firstWeekday - 1; i >= 0; i--) {
    cells.push({ date: new Date(year, month - 2, daysInPrevMonth - i), inMonth: false });
  }
  for (let day = 1; day <= daysInMonth; day++) {
    cells.push({ date: new Date(year, month - 1, day), inMonth: true });
  }
  while (cells.length % 7 !== 0) {
    const last = cells.at(-1);
    const nextDay = (last ? last.date.getDate() : 0) + 1;
    cells.push({ date: new Date(year, month, nextDay), inMonth: false });
  }
  return cells;
}

/** `YYYY-MM-DD` in local time — `Date.toISOString()` shifts to UTC, which drifts a calendar
 * day near midnight for TW-local (UTC+8) users. Shared alongside `buildMonthGrid` since every
 * calendar-shaped table needs both. */
export function toIsoDate(date: Date): string {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
}
