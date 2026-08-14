import { describe, expect, it } from "vitest";
import { markdownToSpeech } from "@/lib/markdown-to-speech";

describe("markdownToSpeech", () => {
  it("strips headers", () => {
    expect(markdownToSpeech("# Title\n\n## Subtitle\n\n### Section")).toBe(
      "Title\n\nSubtitle\n\nSection",
    );
  });

  it("strips bold/italic/underscore emphasis", () => {
    expect(
      markdownToSpeech("This is **bold** and *italic* and __also bold__ and _also italic_."),
    ).toBe("This is bold and italic and also bold and also italic.");
  });

  it("strips inline code and code blocks", () => {
    expect(markdownToSpeech("Use `npm install` to set up.")).toBe("Use npm install to set up.");
    expect(markdownToSpeech("Before\n\n```js\nconst x = 1;\n```\n\nAfter")).toBe("Before\n\nAfter");
  });

  it("converts links to their text, dropping the URL", () => {
    expect(markdownToSpeech("See [the docs](https://example.com/docs) for more.")).toBe(
      "See the docs for more.",
    );
  });

  it("converts images to alt text", () => {
    expect(markdownToSpeech("![a chart](https://example.com/chart.png)")).toBe("a chart");
  });

  it("strips list markers (bullet and ordered)", () => {
    expect(markdownToSpeech("- first\n- second\n* third\n1. fourth\n2. fifth")).toBe(
      "first\nsecond\nthird\nfourth\nfifth",
    );
  });

  it("strips blockquote markers", () => {
    expect(markdownToSpeech("> a quoted line\n> another line")).toBe("a quoted line\nanother line");
  });

  it("strips horizontal rules", () => {
    expect(markdownToSpeech("Before\n\n---\n\nAfter")).toBe("Before\n\nAfter");
  });

  it("converts a GFM table into comma-separated prose, dropping pipes and the separator row", () => {
    const table = "| Metric | TSMC | UMC |\n|---|---|---|\n| Gross Margin | 56% | 30% |";
    expect(markdownToSpeech(table)).toBe("Metric, TSMC, UMC\nGross Margin, 56%, 30%");
  });

  it("strips strikethrough", () => {
    expect(markdownToSpeech("~~old price~~ new price")).toBe("old price new price");
  });

  it("handles a combined header+bold+table+link answer with no leftover markdown syntax", () => {
    const md = [
      "### Comparison",
      "",
      "**TSMC** vs **UMC** gross margin, see [source](https://example.com).",
      "",
      "| Metric | TSMC | UMC |",
      "|---|---|---|",
      "| Gross Margin | 56% | 30% |",
    ].join("\n");
    const result = markdownToSpeech(md);
    expect(result).not.toMatch(/[#*_|`]/);
    expect(result).not.toContain("https://example.com");
    expect(result).toContain("Comparison");
    expect(result).toContain("TSMC vs UMC gross margin, see source.");
    expect(result).toContain("Metric, TSMC, UMC");
    expect(result).toContain("Gross Margin, 56%, 30%");
  });
});
