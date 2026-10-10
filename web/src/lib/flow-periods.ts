/** Groups daily 三大法人 net flow into the bars the 摘要 chart draws: one bar per trading day, per
 * week (Monday-start) or per calendar month. Each bar sums the three buckets' net shares; the
 * price is the last close inside the group, so the line reads like a weekly/monthly close. */

export type FlowPeriod = "day" | "week" | "month";

export type DailyFlowPoint = {
  readonly trade_date: string; // YYYY-MM-DD
  readonly foreign: number;
  readonly trust: number;
  readonly dealer: number;
  readonly price: number | null;
};

export type FlowBar = {
  /** Group key, also the x-axis label: the date, the week's Monday, or YYYY-MM. */
  readonly label: string;
  readonly foreign: number;
  readonly trust: number;
  readonly dealer: number;
  readonly price: number | null;
};

/** About 3 months of trading days: the chart's window at every period. */
export const FLOW_CHART_TRADING_DAYS = 63;

function weekStart(isoDate: string): string {
  const [y, m, d] = isoDate.split("-").map(Number);
  const date = new Date(Date.UTC(y ?? 1970, (m ?? 1) - 1, d ?? 1));
  const offset = (date.getUTCDay() + 6) % 7; // Monday = 0
  date.setUTCDate(date.getUTCDate() - offset);
  return date.toISOString().slice(0, 10);
}

function groupKey(isoDate: string, period: FlowPeriod): string {
  if (period === "month") return isoDate.slice(0, 7);
  if (period === "week") return weekStart(isoDate);
  return isoDate;
}

/** `points` must be ascending by date. Returns ascending bars. */
export function groupFlowByPeriod(
  points: readonly DailyFlowPoint[],
  period: FlowPeriod,
): FlowBar[] {
  const bars: FlowBar[] = [];
  for (const point of points) {
    const label = groupKey(point.trade_date, period);
    const last = bars.at(-1);
    if (last && last.label === label) {
      bars[bars.length - 1] = {
        label,
        foreign: last.foreign + point.foreign,
        trust: last.trust + point.trust,
        dealer: last.dealer + point.dealer,
        price: point.price ?? last.price,
      };
    } else {
      const { foreign, trust, dealer, price } = point;
      bars.push({ label, foreign, trust, dealer, price });
    }
  }
  return bars;
}
