// @vitest-environment jsdom
import { cleanup } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { DispositionStockCard } from "@/components/market/disposition-stock-card";
import { renderWithIntl } from "@/test/render";
import type { DispositionBoardCardRow } from "@/types/market/disclosures";

afterEach(cleanup);

const EMPTY: DispositionBoardCardRow = {
  rank: 1,
  symbol: "2330",
  market: "TW",
  company_name: null,
  security_type: null,
  close: null,
  change_pct: null,
  volume: null,
  turnover: null,
  institutional_net: null,
  margin_ratio: null,
  margin_caveat: null,
  margin_health_status: null,
  day_trading_ratio: null,
  bucket: "at_risk",
  disposition_measure: null,
  disposition_period_start: null,
  disposition_period_end: null,
  matching_interval_minutes: null,
  flags: [],
  attention_flags_remaining: null,
  estimated_days_until_disposition: null,
  days_until_exiting: null,
  projection_caveat: "",
};

const FULL: DispositionBoardCardRow = {
  ...EMPTY,
  company_name: "台積電",
  security_type: "twse",
  close: 1000.5,
  change_pct: 1.456,
  volume: 12_345_000,
  turnover: 5_000_000_000,
  institutional_net: 2500,
  margin_ratio: 12.34,
  day_trading_ratio: 45.67,
  matching_interval_minutes: 5,
  flags: ["資", "處"],
  attention_flags_remaining: 2,
  estimated_days_until_disposition: 3,
  disposition_period_start: "2026-01-01",
  disposition_period_end: "2026-01-12",
};

const SPARK = Array.from({ length: 25 }, (_, i) => 100 + i);

function html(card: DispositionBoardCardRow, sparkline: readonly number[] = []): string {
  const { container } = renderWithIntl(<DispositionStockCard card={card} sparkline={sparkline} />);
  return container.innerHTML;
}

describe("DispositionStockCard characterization", () => {
  it("renders a fully populated at-risk card", () => {
    expect(html(FULL, SPARK)).toMatchSnapshot();
  });

  it("renders a card with every optional value missing", () => {
    expect(html(EMPTY)).toMatchSnapshot();
  });

  it("falls back to the symbol when the company name is empty", () => {
    expect(html({ ...EMPTY, company_name: "" })).toMatchSnapshot();
  });

  it("renders the OTC label and the in-disposition bucket", () => {
    expect(html({ ...EMPTY, security_type: "tpex", bucket: "in_disposition" })).toMatchSnapshot();
  });

  it("renders the exiting-soon bucket with days left and with exiting today", () => {
    expect(html({ ...EMPTY, bucket: "exiting_soon", days_until_exiting: 4 })).toMatchSnapshot();
    cleanup();
    expect(html({ ...EMPTY, bucket: "exiting_soon", days_until_exiting: 0 })).toMatchSnapshot();
  });

  it("renders a falling price with the down direction", () => {
    expect(html({ ...EMPTY, close: 50, change_pct: -2.5 })).toMatchSnapshot();
  });

  it("treats a zero change as up", () => {
    expect(html({ ...EMPTY, close: 50, change_pct: 0 })).toMatchSnapshot();
  });

  it("colours a negative institutional net", () => {
    expect(html({ ...EMPTY, institutional_net: -1200 })).toMatchSnapshot();
  });

  it("shows the attention-flags line without an estimate", () => {
    expect(html({ ...EMPTY, attention_flags_remaining: 1 })).toMatchSnapshot();
  });

  it("shows the period only when both dates exist", () => {
    expect(html({ ...EMPTY, disposition_period_start: "2026-01-01" })).toMatchSnapshot();
  });

  it("shows the degraded margin caveat only for a non-ok status with a caveat", () => {
    const degraded = html({ ...EMPTY, margin_health_status: "stale", margin_caveat: "x" });
    cleanup();
    const ok = html({ ...EMPTY, margin_health_status: "ok", margin_caveat: "x" });
    cleanup();
    const noCaveat = html({ ...EMPTY, margin_health_status: "stale", margin_caveat: null });
    expect(degraded).toMatchSnapshot();
    expect(ok).not.toBe(degraded);
    expect(noCaveat).toBe(ok);
  });

  it("shows moving averages only for the windows the sparkline covers", () => {
    expect(html(EMPTY, [1, 2, 3, 4, 5, 6])).toMatchSnapshot();
    cleanup();
    expect(
      html(
        EMPTY,
        Array.from({ length: 12 }, (_, i) => i + 1),
      ),
    ).toMatchSnapshot();
  });
});
