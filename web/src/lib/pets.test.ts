import { describe, expect, it } from "vitest";
import { petName, shouldOfferWelcomePull, xpFraction } from "@/lib/pets";
import type { PetCatalogEntry } from "@/types/settings";

const catalog: PetCatalogEntry[] = [
  {
    pet_id: "leopard_cat",
    name: "Shan the Leopard Cat",
    name_zh: "石虎小珊",
    rarity: "rare",
    description: "",
  },
];

describe("petName", () => {
  it("uses the reader's language and falls back to the id for an unknown pet", () => {
    expect(petName(catalog, "leopard_cat", "zh-TW")).toBe("石虎小珊");
    expect(petName(catalog, "leopard_cat", "en")).toBe("Shan the Leopard Cat");
    expect(petName(catalog, "new_pet", "en")).toBe("new_pet");
  });
});

describe("xpFraction", () => {
  const progress = { xp_per_level: 100, max_level: 30 };
  it("is the share of the level's XP, clamped to 0..1", () => {
    expect(xpFraction(3, 25, progress)).toBe(0.25);
    expect(xpFraction(3, -5, progress)).toBe(0);
    expect(xpFraction(3, 250, progress)).toBe(1);
  });
  it("reads as full at max level", () => {
    expect(xpFraction(30, 0, progress)).toBe(1);
  });
});

describe("shouldOfferWelcomePull", () => {
  it("is for a user with no pet and a pull waiting", () => {
    expect(shouldOfferWelcomePull({ owned_count: 0, available_pulls: 1 })).toBe(true);
    expect(shouldOfferWelcomePull({ owned_count: 1, available_pulls: 1 })).toBe(false);
    expect(shouldOfferWelcomePull({ owned_count: 0, available_pulls: 0 })).toBe(false);
    expect(shouldOfferWelcomePull(undefined)).toBe(false);
  });
});
