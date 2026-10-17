// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ChainPositionBar } from "@/components/stock/chain-position";
import { getChainPositions } from "@/lib/api/stock-detail";
import { messages, renderWithIntl } from "@/test/render";
import type { ChainPosition } from "@/types/stock/chain-position";

vi.mock("@/lib/api/stock-detail", () => ({ getChainPositions: vi.fn() }));

const t = messages.stock.industry.chainPosition;
const tier = messages.stock.industry.tier;

// Invented chains and companies only.
const base: ChainPosition = {
  chain_key: "chain_alpha",
  chain_name: "Alpha Chain",
  position: "upstream",
  is_primary: true,
  method: "evidence",
  confidence: 0.9,
  reasons: [{ code: "graph_layering", detail: null }],
  computed_at: "2026-01-01T00:00:00Z",
};

function renderBar(positions: ChainPosition[]): void {
  vi.mocked(getChainPositions).mockResolvedValue({ symbol: "1001", market: "TW", positions });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  renderWithIntl(
    <QueryClientProvider client={client}>
      <ChainPositionBar symbol="1001" market="TW" />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("ChainPositionBar", () => {
  it.each([
    ["upstream", tier.upstream],
    ["midstream", tier.midstream],
    ["downstream", tier.downstream],
  ] as const)("shows the %s badge with the chain name", async (position, label) => {
    renderBar([{ ...base, position }]);
    const badge = await screen.findByRole("img", { name: `Alpha Chain：${label}` });
    expect(badge.getAttribute("data-estimated")).toBe("false");
    expect(screen.queryByText(t.estimated)).toBeNull();
  });

  it("marks a keyword prior as an estimate", async () => {
    renderBar([
      {
        ...base,
        method: "prior",
        confidence: 0.8,
        reasons: [{ code: "prior_keyword", detail: null }],
      },
    ]);
    const badge = await screen.findByRole("img", { name: /Alpha Chain/ });
    expect(badge.getAttribute("data-estimated")).toBe("true");
    expect(screen.getByText(t.estimated)).toBeTruthy();
  });

  it("marks a low-confidence prior as an estimate but not a curated one", async () => {
    renderBar([
      { ...base, method: "prior", confidence: 0.3, reasons: [{ code: "prior_curated" }] },
    ]);
    expect((await screen.findByRole("img")).getAttribute("data-estimated")).toBe("true");
    cleanup();
    renderBar([
      { ...base, method: "prior", confidence: 0.8, reasons: [{ code: "prior_curated" }] },
    ]);
    await waitFor(() =>
      expect(screen.getByRole("img").getAttribute("data-estimated")).toBe("false"),
    );
  });

  it("shows 尚未分類 for an empty result and no badge", async () => {
    renderBar([]);
    expect(await screen.findByText(t.unclassified)).toBeTruthy();
    expect(screen.queryByRole("img")).toBeNull();
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("lists the other chains and switches the shown badge", async () => {
    renderBar([
      base,
      {
        ...base,
        chain_key: "chain_beta",
        chain_name: "Beta Chain",
        position: "downstream",
        is_primary: false,
      },
    ]);
    await screen.findByRole("img", { name: `Alpha Chain：${tier.upstream}` });
    fireEvent.click(screen.getByRole("button", { name: `查看Beta Chain：${tier.downstream}` }));
    expect(screen.getByRole("img", { name: `Beta Chain：${tier.downstream}` })).toBeTruthy();
    expect(screen.getByRole("button", { name: `查看Alpha Chain：${tier.upstream}` })).toBeTruthy();
  });

  it("opens the 為什麼 popover from a focusable button, falls back on unknown codes, and closes on Escape", async () => {
    renderBar([
      {
        ...base,
        confidence: 0.6,
        reasons: [
          { code: "conflict_graph_over_prior", detail: null },
          { code: "brand_new_code", detail: "x" },
        ],
      },
    ]);
    const trigger = await screen.findByRole("button", { name: /為什麼/ });
    expect(trigger.tagName).toBe("BUTTON");
    trigger.focus();
    expect(document.activeElement).toBe(trigger);
    fireEvent.click(trigger);
    expect(await screen.findByText(t.reason.conflict_graph_over_prior)).toBeTruthy();
    expect(screen.getByText(t.reason.unknown)).toBeTruthy();
    expect(screen.getByText(`信心程度：${t.confidence.medium}`)).toBeTruthy();
    expect(screen.queryByText("brand_new_code")).toBeNull();
    fireEvent.keyDown(document.activeElement ?? document.body, { key: "Escape" });
    await waitFor(() =>
      expect(screen.queryByText(t.reason.conflict_graph_over_prior)).toBeNull(),
    );
  });
});
