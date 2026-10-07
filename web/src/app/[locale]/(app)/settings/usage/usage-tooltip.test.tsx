import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { UsageTooltip } from "@/app/[locale]/(app)/settings/usage/llm-usage-card";
import type { Series } from "@/lib/usage-series";

const series: Series[] = [
  {
    key: "openai/gpt-5.4-mini",
    provider: "openai",
    model: "gpt-5.4-mini",
    color: "var(--chart-2)",
  },
  { key: "anthropic/sonnet", provider: "anthropic", model: "sonnet", color: "var(--chart-1)" },
];

function render(payload: { dataKey: string; value: number }[], active = true): string {
  return renderToStaticMarkup(
    <UsageTooltip
      active={active}
      label="10/7"
      payload={payload}
      series={series}
      otherLabel="Other"
      totalLabel="Total"
    />,
  );
}

describe("UsageTooltip", () => {
  it("lists models largest first with their provider, then the total", () => {
    const html = render([
      { dataKey: "openai/gpt-5.4-mini", value: 3100 },
      { dataKey: "anthropic/sonnet", value: 9000 },
    ]);
    expect(html.indexOf("sonnet")).toBeLessThan(html.indexOf("gpt-5.4-mini"));
    expect(html).toContain("Anthropic · sonnet");
    expect(html).toContain("OpenAI · gpt-5.4-mini");
    expect(html).toContain("12.1k");
    expect(html).toContain("Total");
  });

  it("omits models with no usage that day and renders nothing when inactive", () => {
    const html = render([
      { dataKey: "openai/gpt-5.4-mini", value: 0 },
      { dataKey: "anthropic/sonnet", value: 50 },
    ]);
    expect(html).not.toContain("gpt-5.4-mini");
    expect(render([{ dataKey: "anthropic/sonnet", value: 5 }], false)).toBe("");
  });
});
