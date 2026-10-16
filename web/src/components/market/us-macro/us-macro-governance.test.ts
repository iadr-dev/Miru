import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import en from "../../../../messages/en.json";
import zhTW from "../../../../messages/zh-TW.json";

const SRC = path.resolve(__dirname, "../../..");
const FEATURE_DIR = path.join(SRC, "components/market/us-macro");
const FEATURE_FILES = [
  ...readdirSync(FEATURE_DIR)
    .filter((name) => /\.tsx?$/.test(name) && !name.includes(".test."))
    .map((name) => path.join(FEATURE_DIR, name)),
  path.join(SRC, "lib/us-macro-chart.ts"),
  path.join(SRC, "lib/us-macro-format.ts"),
  path.join(SRC, "hooks/use-us-macro.ts"),
  path.join(SRC, "hooks/use-series-label.ts"),
];

function leafKeys(value: unknown, prefix = ""): string[] {
  if (typeof value !== "object" || value === null) {
    return [prefix];
  }
  return Object.entries(value).flatMap(([key, child]) =>
    leafKeys(child, prefix ? `${prefix}.${key}` : key),
  );
}

describe("US macro governance", () => {
  it("never references the signed up/down tokens or direction glyphs in its source", () => {
    expect(FEATURE_FILES.length).toBeGreaterThan(10);
    for (const file of FEATURE_FILES) {
      const source = readFileSync(file, "utf8");
      expect(source, path.basename(file)).not.toMatch(/--up|--down|SignedChange|directionColor/);
      expect(source, path.basename(file)).not.toMatch(/[▲▼]/);
    }
  });

  it("keeps the same copy keys in zh-TW and en", () => {
    expect(leafKeys(en.market.cards.usMacro).sort()).toEqual(
      leafKeys(zhTW.market.cards.usMacro).sort(),
    );
  });

  it("names no data provider and uses no trading language", () => {
    const banned =
      /FRED|St\.? Louis|Federal Reserve Bank|聖路易|FinMind|Yahoo|Sponsor|買進|賣出|目標價|進場|停損|明牌|飆股/i;
    expect(JSON.stringify(zhTW.market.cards.usMacro)).not.toMatch(banned);
    expect(JSON.stringify(en.market.cards.usMacro)).not.toMatch(banned);
  });
});
