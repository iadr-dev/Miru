import { describe, expect, it } from "vitest";
import {
  layoutLabels,
  PLANE_HALF_EXTENT,
  planeAt,
  quadrantCounts,
  quadrantFor,
  quadrantTransitions,
  rrgRange,
  rrgToPlane,
  sectorColor,
  shortSectorName,
} from "./rrg-layout";

const point = (rsRatio: number, rsMomentum: number) => ({
  rs_ratio: rsRatio,
  rs_momentum: rsMomentum,
});

describe("shortSectorName", () => {
  it("drops TWSE's index suffix so names fit a chip or an in-scene label", () => {
    expect(shortSectorName("電腦及週邊設備類指數")).toBe("電腦及週邊設備");
    expect(shortSectorName("電子類指數")).toBe("電子");
    expect(shortSectorName("金融保險類指數")).toBe("金融保險");
    expect(shortSectorName("半導體指數")).toBe("半導體");
    expect(shortSectorName("航運業類指數")).toBe("航運業");
  });

  it("keeps a name that has no suffix, and never returns an empty label", () => {
    expect(shortSectorName("  生技醫療  ")).toBe("生技醫療");
    expect(shortSectorName("指數")).toBe("指數");
    expect(shortSectorName("")).toBe("");
  });
});

describe("layoutLabels", () => {
  const head = (id: string, x: number, z: number, name = "半導體") => ({ id, name, x, z });

  it("puts a lone label just above its head", () => {
    const offsets = layoutLabels([head("a", 0, 0)]);
    expect(offsets.get("a")).toEqual({ dx: 0, dz: -0.62 });
  });

  it("moves a label that would land on a neighbour's", () => {
    const offsets = layoutLabels([head("a", 1, 1), head("b", 1.1, 1.05)]);
    expect(offsets.get("a")).not.toEqual(offsets.get("b"));
  });

  it("leaves well-separated heads on the default spot", () => {
    const offsets = layoutLabels([head("a", -5, -5), head("b", 5, 5), head("c", 0, 0)]);
    expect([...offsets.values()].every((o) => o.dx === 0 && o.dz === -0.62)).toBe(true);
  });

  it("gives every head an offset, and is independent of input order", () => {
    const heads = [head("a", 2, 2), head("b", 2.05, 2), head("c", 2, 2.1), head("d", 1.95, 1.9)];
    const forward = layoutLabels(heads);
    const backward = layoutLabels([...heads].reverse());
    expect(forward.size).toBe(4);
    expect([...forward.entries()].sort()).toEqual([...backward.entries()].sort());
  });

  it("keeps placed labels from overlapping when there is room to spread them", () => {
    const heads = [
      head("a", 0, 0, "電子"),
      head("b", 0.2, 0.1, "金融"),
      head("c", -0.2, 0.05, "鋼鐵"),
    ];
    const offsets = layoutLabels(heads);
    const boxes = heads.map((h) => {
      const o = offsets.get(h.id) ?? { dx: 0, dz: 0 };
      const half = ([...h.name].length * 0.34 + 0.5) / 2;
      return {
        l: h.x + o.dx - half,
        r: h.x + o.dx + half,
        t: h.z + o.dz - 0.23,
        b: h.z + o.dz + 0.23,
      };
    });
    for (let i = 0; i < boxes.length; i++) {
      for (let j = i + 1; j < boxes.length; j++) {
        const a = boxes[i];
        const b = boxes[j];
        if (!a || !b) continue;
        expect(a.l < b.r && a.r > b.l && a.t < b.b && a.b > b.t).toBe(false);
      }
    }
  });
});

describe("quadrant transitions and counts", () => {
  const sectors = [
    {
      industry_id: "1",
      industry_name: "電子類指數",
      points: [point(95, 105), point(105, 106), point(106, 95)],
    },
    {
      industry_id: "2",
      industry_name: "金融保險類指數",
      points: [point(110, 110), point(111, 112), point(112, 111)],
    },
    { industry_id: "3", industry_name: "鋼鐵類指數", points: [point(90, 90), point(92, 91)] },
  ];

  it("reports a sector that crossed into another quadrant, and only that sector", () => {
    expect(quadrantTransitions(sectors, 0, 1)).toEqual([
      { industryId: "1", industryName: "電子類指數", from: "improving", to: "leading" },
    ]);
    expect(quadrantTransitions(sectors, 1, 2)).toEqual([
      { industryId: "1", industryName: "電子類指數", from: "leading", to: "weakening" },
    ]);
  });

  it("ignores a sector with no point for one of the weeks", () => {
    expect(quadrantTransitions(sectors, 1, 2).some((t) => t.industryId === "3")).toBe(false);
  });

  it("counts the sectors in each quadrant for a week", () => {
    expect(quadrantCounts(sectors, 0)).toEqual({
      leading: 1,
      weakening: 0,
      lagging: 1,
      improving: 1,
    });
    expect(quadrantCounts(sectors, 1)).toEqual({
      leading: 2,
      weakening: 0,
      lagging: 1,
      improving: 0,
    });
  });

  it("clamps a week past the end of a shorter series instead of dropping the sector", () => {
    expect(quadrantCounts(sectors, 5).lagging).toBe(1);
  });

  it("uses the same classification as quadrantFor, with 100 belonging to the strong/rising side", () => {
    expect(quadrantFor(100, 100)).toBe("leading");
    expect(
      quadrantCounts([{ industry_id: "x", industry_name: "x", points: [point(100, 99.9)] }], 0)
        .weakening,
    ).toBe(1);
  });
});

describe("plane scale", () => {
  const series = (ratios: number[]) => [
    {
      industry_id: "1",
      industry_name: "電子類指數",
      points: ratios.map((r) => point(r, 100)),
    },
  ];

  it("has a floor and rounds up to a step", () => {
    expect(rrgRange([])).toBe(20);
    expect(rrgRange(series([101, 99, 103]))).toBe(20);
    expect(rrgRange(series([160, 160, 160, 160]))).toBe(60);
  });

  it("is set by the bulk of the data, not one outlier", () => {
    const ratios = [...Array.from({ length: 40 }, (_, i) => 100 + (i % 8) * 10), 100000];
    expect(rrgRange(series(ratios))).toBeLessThan(200);
  });

  it("puts 100 on the axes, keeps order, and stays inside the frame", () => {
    expect(rrgToPlane(100, 100, 80)).toEqual({ x: 0, y: 0 });
    const a = rrgToPlane(120, 100, 80).x;
    const b = rrgToPlane(150, 100, 80).x;
    expect(b).toBeGreaterThan(a);
    expect(rrgToPlane(100000, -100000, 80).x).toBeLessThanOrEqual(PLANE_HALF_EXTENT);
    expect(rrgToPlane(100000, -100000, 80).y).toBeGreaterThanOrEqual(-PLANE_HALF_EXTENT);
    expect(rrgToPlane(60, 100, 80).x).toBeCloseTo(-rrgToPlane(140, 100, 80).x);
  });

  it("gives neighbouring sectors different colours", () => {
    expect(new Set([0, 1, 2, 3, 4, 5, 6, 7, 8, 9].map(sectorColor)).size).toBe(10);
  });
});

describe("planeAt", () => {
  const plane = [
    { x: 0, y: 0 },
    { x: 4, y: 2 },
    { x: 2, y: 6 },
    { x: 8, y: 6 },
  ];

  it("passes exactly through every week", () => {
    plane.forEach((p, week) => {
      expect(planeAt(plane, week)).toEqual(p);
    });
  });

  it("clamps outside the series and is empty for no data", () => {
    expect(planeAt(plane, -3)).toEqual(plane[0]);
    expect(planeAt(plane, 99)).toEqual(plane[3]);
    expect(planeAt([], 1)).toBeNull();
  });

  it("stays inside the frame even where the curve would overshoot", () => {
    const edge = [
      { x: -9.5, y: 0 },
      { x: 9.5, y: 0 },
      { x: 9.5, y: 0 },
      { x: -9.5, y: 0 },
    ];
    for (let week = 0; week <= 3; week += 0.25) {
      const at = planeAt(edge, week);
      expect(Math.abs(at?.x ?? 99)).toBeLessThanOrEqual(9.5);
    }
  });
});
