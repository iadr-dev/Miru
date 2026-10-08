import { NextIntlClientProvider } from "next-intl";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { TierComparison } from "@/app/[locale]/(app)/settings/billing/tier-comparison";
import type { TierSummary } from "@/types/settings";
import en from "../../../../../../messages/en.json";

const tiers: TierSummary[] = [
  {
    tier: "free",
    chat_limit_per_day: 3,
    requests_per_minute: 60,
    sponsor_data: false,
    features: [],
    deep_research_limit_per_day: null,
  },
  {
    tier: "premium",
    chat_limit_per_day: 100,
    requests_per_minute: 300,
    sponsor_data: true,
    features: ["ai_summary", "live_quote_stream"],
    deep_research_limit_per_day: null,
  },
  {
    tier: "pro",
    chat_limit_per_day: null,
    requests_per_minute: 600,
    sponsor_data: true,
    features: ["ai_summary", "deep_research", "live_quote_stream", "tick_minute_k"],
    deep_research_limit_per_day: 5,
  },
];

function render(current: string): string {
  return renderToStaticMarkup(
    <NextIntlClientProvider locale="en" messages={en}>
      <TierComparison tiers={tiers} current={current} />
    </NextIntlClientProvider>,
  );
}

describe("TierComparison", () => {
  it("shows each tier's real limits side by side", () => {
    const html = render("free");
    expect(html).toContain("3 chats a day");
    expect(html).toContain("100 chats a day");
    expect(html).toContain("Unlimited chats");
    expect(html).toContain("Public data only");
    expect(html).toContain("Deep research, 5 reports a day");
  });

  it("marks only the caller's own plan", () => {
    expect(render("premium").match(/Your plan/g)).toHaveLength(1);
  });

  it("renders nothing without tiers", () => {
    const html = renderToStaticMarkup(
      <NextIntlClientProvider locale="en" messages={en}>
        <TierComparison tiers={[]} current="free" />
      </NextIntlClientProvider>,
    );
    expect(html).toBe("");
  });
});
