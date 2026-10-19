import { describe, expect, it } from "vitest";
import {
  applyGraphFilter,
  companyHref,
  groupByType,
  matchingNodeIds,
  maxNodeWeight,
  monogram,
  themeKeyForEntity,
  truncateLabel,
} from "@/lib/entity-graph-view";
import type { EntityLink, EntityNode, EntityType } from "@/types/entity-graph";

function node(id: string, type: EntityType, weight: number, focus = false): EntityNode {
  const isCompany = type === "tw_company" || type === "intl_company";
  return {
    id,
    label: id.toUpperCase(),
    type,
    symbol: isCompany ? id : null,
    market: isCompany ? (type === "tw_company" ? "TW" : "US") : null,
    weight,
    focus,
  };
}

const nodes: EntityNode[] = [
  node("9001", "tw_company", 12, true),
  node("laser", "technology", 8),
  node("film", "material", 2),
  node("9002", "tw_company", 5),
  node("zed", "intl_company", 1),
];
const links: EntityLink[] = [
  { source: "9001", target: "laser", weight: 8, kind: "mention" },
  { source: "9001", target: "film", weight: 2, kind: "mention" },
  { source: "9002", target: "laser", weight: 3, kind: "cooccurrence" },
  { source: "9001", target: "zed", weight: 1, kind: "supply" },
];

describe("applyGraphFilter", () => {
  it("drops light nodes and their links but never the focus node", () => {
    const out = applyGraphFilter(nodes, links, { minWeight: 5, hiddenTypes: new Set() });
    expect(out.nodes.map((n) => n.id)).toEqual(["9001", "laser", "9002"]);
    expect(out.links.map((l) => `${l.source}>${l.target}`)).toEqual(["9001>laser", "9002>laser"]);
    const heavy = applyGraphFilter(nodes, links, { minWeight: 99, hiddenTypes: new Set() });
    expect(heavy.nodes.map((n) => n.id)).toEqual(["9001"]);
    expect(heavy.links).toEqual([]);
  });

  it("hides toggled-off types", () => {
    const out = applyGraphFilter(nodes, links, {
      minWeight: 1,
      hiddenTypes: new Set<EntityType>(["technology", "intl_company"]),
    });
    expect(out.nodes.map((n) => n.id)).toEqual(["9001", "film", "9002"]);
    expect(out.links.map((l) => `${l.source}>${l.target}`)).toEqual(["9001>film"]);
  });
});

describe("maxNodeWeight", () => {
  it("ignores the focus node, so the slider tops out at the heaviest peer", () => {
    expect(maxNodeWeight(nodes)).toBe(8);
    expect(maxNodeWeight([])).toBe(0);
  });
});

describe("matchingNodeIds", () => {
  it("matches label substrings and symbol prefixes, ignoring case", () => {
    expect([...matchingNodeIds(nodes, "las")]).toEqual(["laser"]);
    expect([...matchingNodeIds(nodes, " ZED ")]).toEqual(["zed"]);
    expect([...matchingNodeIds(nodes, "900")].sort()).toEqual(["9001", "9002"]);
  });
  it("matches nothing for a blank query", () => {
    expect(matchingNodeIds(nodes, "   ").size).toBe(0);
  });
});

describe("groupByType", () => {
  it("groups in type order, heaviest first, skipping empty groups and the focus node", () => {
    const groups = groupByType(nodes);
    expect(groups.map((g) => [g.type, g.nodes.map((n) => n.id)])).toEqual([
      ["technology", ["laser"]],
      ["material", ["film"]],
      ["tw_company", ["9002"]],
      ["intl_company", ["zed"]],
    ]);
  });
});

describe("companyHref", () => {
  it("builds the stock page link with its market, and is null without a symbol", () => {
    expect(companyHref(node("9002", "tw_company", 1))).toBe("/stock/9002?market=TW");
    expect(companyHref(node("zed", "intl_company", 1))).toBe("/stock/zed?market=US");
    expect(companyHref(node("laser", "technology", 1))).toBeNull();
  });
});

describe("themeKeyForEntity", () => {
  const themes = [
    { key: "laser-optics", name_zh: "雷射光學", name_en: "Laser Optics" },
    { key: "film", name_zh: "薄膜", name_en: "" },
  ];
  it("matches a theme by name or key, and returns null otherwise", () => {
    expect(themeKeyForEntity("雷射光學", themes)).toBe("laser-optics");
    expect(themeKeyForEntity("laser optics", themes)).toBe("laser-optics");
    expect(themeKeyForEntity("Film", themes)).toBe("film");
    expect(themeKeyForEntity("unknown", themes)).toBeNull();
    expect(themeKeyForEntity("  ", themes)).toBeNull();
  });
});

describe("truncateLabel", () => {
  it("keeps short names and cuts long ones by characters, not bytes", () => {
    expect(truncateLabel("台積電")).toBe("台積電");
    expect(truncateLabel("一二三四五六七八九十")).toBe("一二三四五六七八九…");
    expect(truncateLabel("123456789")).toBe("123456789");
  });
});

describe("monogram", () => {
  it("takes the first glyph of a CJK name and the capital initial of a Latin one", () => {
    expect(monogram("台積電")).toBe("台");
    expect(monogram(" nvidia")).toBe("N");
    expect(monogram("")).toBe("");
  });
});
