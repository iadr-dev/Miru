/** Fixtures for the US macro tests, written from the documented response shapes (no live data
 * exists until the backend has a data key configured). */

import type {
  DashboardSeries,
  MacroPoint,
  MacroUnits,
  UsEconomicReleasesResponse,
  UsMacroDashboardResponse,
  UsMacroSeriesResponse,
  UsSeriesRevisionsResponse,
  YieldCurveSpread,
} from "@/types/market/us-macro";

export const CAVEAT = "Official US statistics, published on each series' own schedule.";

export function point(date: string, value: number): MacroPoint {
  return { date, value };
}

/** `count` consecutive daily points ending on `endDate`, drifting around `base`. */
export function dailyPoints(endDate: string, count: number, base: number): MacroPoint[] {
  const end = Date.parse(`${endDate}T00:00:00Z`);
  return Array.from({ length: count }, (_, index) => {
    const date = new Date(end - (count - 1 - index) * 86_400_000).toISOString().slice(0, 10);
    return point(date, Number((base + Math.sin(index / 20)).toFixed(3)));
  });
}

export function dashboardSeries(overrides: Partial<DashboardSeries> = {}): DashboardSeries {
  return {
    series_id: "UNRATE",
    label: "Unemployment rate",
    title: "Unemployment Rate",
    units: "lin",
    unit_kind: "percent",
    frequency: "monthly",
    higher_means: "higher_unemployment_share",
    higher_means_text: "A higher value means a larger share of the labor force is unemployed.",
    copyrighted_third_party: false,
    latest: point("2026-09-01", 4.3),
    prior: point("2026-08-01", 4.1),
    change: 0.2,
    last_observation_date: "2026-09-01",
    release_name: "Employment Situation",
    next_release_date: "2026-11-06",
    sparkline: [
      point("2026-05-01", 4.0),
      point("2026-06-01", 4.1),
      point("2026-07-01", 4.2),
      point("2026-08-01", 4.1),
      point("2026-09-01", 4.3),
    ],
    ...overrides,
  };
}

export function spread(overrides: Partial<YieldCurveSpread> = {}): YieldCurveSpread {
  return {
    series_id: "T10Y2Y",
    label: "10-Year minus 2-Year Treasury spread",
    latest: point("2026-10-15", -0.12),
    state: "inverted",
    inverted_since: "2026-08-30",
    days_inverted: 47,
    since_window_start: false,
    last_inverted_date: "2026-10-15",
    ...overrides,
  };
}

export function dashboard(
  overrides: Partial<UsMacroDashboardResponse> = {},
): UsMacroDashboardResponse {
  return {
    panels: [
      {
        group: "yield_curve_recession",
        label: "Yield curve and recession",
        series: [
          dashboardSeries({
            series_id: "DGS10",
            label: "10-Year Treasury yield",
            frequency: "daily",
            last_observation_date: "2026-10-15",
          }),
        ],
      },
      {
        group: "inflation",
        label: "Inflation",
        series: [
          dashboardSeries({
            series_id: "CPIAUCSL",
            label: "Consumer prices (CPI), all items",
            units: "pc1",
            latest: point("2026-09-01", 3.1),
            prior: point("2026-08-01", 2.9),
            change: 0.2,
          }),
          dashboardSeries({
            series_id: "CPIAUCSL",
            label: "Consumer prices (CPI), all items",
            units: "pch",
            latest: point("2026-09-01", 0.3),
            prior: point("2026-08-01", 0.4),
            change: -0.1,
          }),
        ],
      },
      {
        group: "labor",
        label: "Labor market",
        series: [
          dashboardSeries(),
          dashboardSeries({
            series_id: "PAYEMS",
            label: "Total nonfarm payrolls",
            unit_kind: "count",
            latest: point("2026-09-01", 159_800),
            prior: point("2026-08-01", 159_650),
            change: 150,
          }),
        ],
      },
      { group: "growth", label: "Growth", series: [] },
    ],
    yield_curve: [
      spread(),
      spread({
        series_id: "T10Y3M",
        label: "10-Year minus 3-Month Treasury spread",
        latest: point("2026-10-15", 0.42),
        state: "positive",
        inverted_since: null,
        days_inverted: 0,
        last_inverted_date: "2026-03-02",
      }),
    ],
    recession_bands: [
      { start: "2026-03-01", end: "2026-05-31", ongoing: false },
      { start: "2026-09-01", end: "2026-09-01", ongoing: true },
    ],
    recession_note: "Recession dates are set retroactively.",
    as_of: "2026-10-15",
    data_available: true,
    unavailable_reason: null,
    currentness_caveat: CAVEAT,
    ...overrides,
  };
}

export function seriesResponse(
  seriesId: string,
  units: MacroUnits = "lin",
  points: MacroPoint[] = dailyPoints("2026-10-15", 420, 0.2),
): UsMacroSeriesResponse {
  return {
    series_id: seriesId,
    units,
    frequency: "monthly",
    points,
    metadata: {
      series_id: seriesId,
      title: "Consumer Price Index for All Urban Consumers",
      label: "Consumer prices (CPI), all items",
      group: "inflation",
      group_label: "Inflation",
      units,
      default_units: "pc1",
      available_units: ["lin", "pc1", "pch"],
      unit_kind: units === "lin" ? "index" : "percent",
      units_description: "Index 1982-1984=100",
      frequency: "Monthly",
      seasonal_adjustment: "Seasonally Adjusted",
      last_updated: "2026-10-10",
      notes: "Long explanatory note about how the index is built.",
      higher_means: "faster_price_growth",
      higher_means_text: "A higher value means prices are rising faster.",
      copyrighted_third_party: false,
      release_name: "Consumer Price Index",
      next_release_date: "2026-11-12",
    },
    total_observations: points.length,
    observation_range: { start: points[0]?.date ?? "2026-01-01", end: "2026-10-15" },
    as_of: "2026-10-15",
    data_available: true,
    unavailable_reason: null,
    currentness_caveat: CAVEAT,
  };
}

export function releases(): UsEconomicReleasesResponse {
  return {
    from_date: "2026-10-01",
    to_date: "2026-11-15",
    releases: [
      {
        release_id: 10,
        release_name: "Consumer Price Index",
        release_date: "2026-10-14",
        status: "released",
        series: [
          {
            series_id: "CPIAUCSL",
            label: "Consumer prices (CPI), all items",
            units: "pc1",
            unit_kind: "percent",
            latest: point("2026-09-01", 3.1),
            previous: point("2026-08-01", 2.9),
            change: 0.2,
            last_observation_date: "2026-09-01",
          },
        ],
      },
      {
        release_id: 50,
        release_name: "Employment Situation",
        release_date: "2026-11-06",
        status: "upcoming",
        series: [
          {
            series_id: "UNRATE",
            label: "Unemployment rate",
            units: "lin",
            unit_kind: "percent",
            latest: point("2026-09-01", 4.3),
            previous: point("2026-08-01", 4.1),
            change: 0.2,
            last_observation_date: "2026-09-01",
          },
        ],
      },
    ],
    as_of: "2026-10-15",
    data_available: true,
    unavailable_reason: null,
    currentness_caveat: CAVEAT,
  };
}

export function revisions(): UsSeriesRevisionsResponse {
  return {
    series_id: "CPIAUCSL",
    label: "Consumer prices (CPI), all items",
    units_description: "Index 1982-1984=100",
    rows: [
      {
        observation_date: "2026-08-01",
        first_release_date: "2026-09-11",
        first_release_value: 321.4,
        latest_value: 321.5,
        latest_vintage_date: "2026-10-14",
        revision: 0.1,
        revision_count: 1,
        vintages: [
          { as_of: "2026-09-11", value: 321.4 },
          { as_of: "2026-10-14", value: 321.5 },
        ],
      },
    ],
    first_release_note: "The first release is the earliest stored value.",
    as_of: "2026-10-15",
    data_available: true,
    unavailable_reason: null,
    currentness_caveat: CAVEAT,
  };
}
