import { describe, expect, it } from "vitest";
import fixture from "@/types/__fixtures__/alerts-api.json";
import { type AlertRule, alertRuleSchema } from "@/types/settings";
import { findExistingRule, parseProposal, toCreateRequest } from "./proposal";

// Exactly what the backend's `propose_alert` tool returns (tests/tools/test_propose_alert_tools.py),
// plus the `non_trading_caveat` stamp the agent loop adds to every card.
const backendCard = {
  symbol: "2330",
  market: "TW",
  condition: { kind: "close_price", comparator: "above", value: 600.5 },
  trigger_mode: "once_per_day",
  expires_in_days: 90,
  summary: "2330 收盤價高於 600.5",
  requires_user_confirmation: true,
  non_trading_caveat: "…",
};

describe("parseProposal", () => {
  it("accepts the backend card payload and ignores the extra keys", () => {
    const proposal = parseProposal(backendCard);
    expect(proposal).toEqual({
      symbol: "2330",
      market: "TW",
      condition: { kind: "close_price", comparator: "above", value: 600.5 },
      trigger_mode: "once_per_day",
      expires_in_days: 90,
    });
  });

  it("keeps a null expiry (never expires) instead of defaulting it", () => {
    expect(parseProposal({ ...backendCard, expires_in_days: null })?.expires_in_days).toBeNull();
  });

  it("returns null for anything that would make the create button fail", () => {
    expect(parseProposal({})).toBeNull();
    expect(parseProposal({ ...backendCard, market: "JP" })).toBeNull();
    expect(parseProposal({ ...backendCard, symbol: "" })).toBeNull();
    expect(parseProposal({ ...backendCard, condition: { kind: "made_up" } })).toBeNull();
    expect(
      parseProposal({
        ...backendCard,
        condition: { kind: "close_price", comparator: "above", value: -5 },
      }),
    ).toBeNull();
  });
});

describe("toCreateRequest", () => {
  it("maps 1:1 onto the POST /alerts body — no fields invented, none dropped", () => {
    const proposal = parseProposal(backendCard);
    expect(proposal).not.toBeNull();
    if (!proposal) return;
    expect(toCreateRequest(proposal)).toEqual({
      symbol: "2330",
      market: "TW",
      condition: { kind: "close_price", comparator: "above", value: 600.5 },
      trigger_mode: "once_per_day",
      expires_in_days: 90,
    });
  });
});

describe("findExistingRule", () => {
  const rule = (patch: Partial<Record<string, unknown>>): AlertRule =>
    alertRuleSchema.parse({
      id: "a",
      user_id: "u",
      symbol: "2330",
      market: "TW",
      condition: { kind: "close_price", comparator: "above", value: 600.5 },
      quiet_hours: null,
      daily_limit: null,
      last_triggered: null,
      created_at: "2026-10-01T00:00:00Z",
      updated_at: "2026-10-01T00:00:00Z",
      ...patch,
    });
  const proposal = parseProposal(backendCard);

  it("finds an identical enabled rule", () => {
    expect(proposal && findExistingRule([rule({})], proposal)?.id).toBe("a");
  });

  it("does not match a different condition, symbol, market, or a disabled rule", () => {
    if (!proposal) throw new Error("fixture must parse");
    const others = [
      rule({ condition: { kind: "close_price", comparator: "below", value: 600.5 } }),
      rule({ symbol: "2317" }),
      rule({ market: "US" }),
      rule({ enabled: false }),
    ];
    expect(findExistingRule(others, proposal)).toBeNull();
  });
});

describe("contract: the REAL alert_proposal card from the backend", () => {
  // Captured from `ProposeAlertTool` through `_build_card` (the same governance scan the chat loop
  // applies) — regenerate with backend/tests/api/_gen_alerts_fixture.py. If the tool or the scan
  // changes the card shape, this fails here instead of as a dead button in chat.
  const real = fixture.proposal_card as Record<string, unknown>;

  it("parses, and maps to a valid create request", () => {
    const proposal = parseProposal(real);
    expect(proposal).not.toBeNull();
    if (!proposal) return;
    expect(proposal.condition).toEqual({
      kind: "institutional_streak",
      investor: "foreign",
      direction: "buy",
      days: 3,
    });
    expect(toCreateRequest(proposal)).toMatchObject({
      symbol: "2330",
      market: "TW",
      trigger_mode: "once_per_day",
      expires_in_days: 90,
    });
  });

  it("carries the confirm flag and the governance caveat the card must not depend on", () => {
    expect(real.requires_user_confirmation).toBe(true);
    expect(typeof real.non_trading_caveat).toBe("string");
  });
});
