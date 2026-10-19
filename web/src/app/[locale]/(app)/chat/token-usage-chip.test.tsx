import { NextIntlClientProvider } from "next-intl";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { TokenUsageChip } from "@/app/[locale]/(app)/chat/token-usage-chip";
import { turnUsageDataSchema } from "@/types/usage";
import en from "../../../../../messages/en.json";

function render(raw: Record<string, unknown>): string {
  const usage = turnUsageDataSchema.parse({ input_tokens: 1000, output_tokens: 100, ...raw });
  return renderToStaticMarkup(
    <NextIntlClientProvider locale="en" messages={en}>
      <TokenUsageChip usage={usage} />
    </NextIntlClientProvider>,
  );
}

describe("TokenUsageChip cost", () => {
  it("shows an approximate estimate, labelled as one, when nothing was reported", () => {
    const html = render({ estimated_cost_micros: 4_000 });
    expect(html).toContain("≈$0.0040");
    expect(html).toContain(en.chat.usage.estimatedCost);
  });

  it("shows the provider charge unchanged, with no approx sign", () => {
    const html = render({ cost_micros: 1_500, estimated_cost_micros: 3_750 });
    expect(html).toContain("$0.0015");
    expect(html).not.toContain("≈");
  });

  it("shows no cost when both are zero", () => {
    const html = render({});
    expect(html).not.toContain("$");
    expect(html).not.toContain("≈");
  });

  it("parses an old stored event without the field", () => {
    expect(turnUsageDataSchema.parse({ input_tokens: 1 }).estimated_cost_micros).toBe(0);
  });
});
