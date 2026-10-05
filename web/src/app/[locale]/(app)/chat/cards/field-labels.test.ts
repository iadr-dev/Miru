import { describe, expect, it } from "vitest";
import {
  fieldLabel,
  LABELED_FIELDS,
  valueLabel,
} from "@/app/[locale]/(app)/chat/cards/field-labels";
import en from "../../../../../../messages/en.json";
import zh from "../../../../../../messages/zh-TW.json";

/** A translator over a messages file: `t("fields.year")` reads `chat.cards.fields.year`, and a
 * missing key throws — exactly the failure a mapped-but-untranslated label would cause live. */
function translator(messages: typeof en) {
  return ((key: string) => {
    let node: unknown = messages.chat.cards;
    for (const part of key.split(".")) {
      node = (node as Record<string, unknown> | undefined)?.[part];
    }
    if (typeof node !== "string") {
      throw new Error(`MISSING_MESSAGE chat.cards.${key}`);
    }
    return node;
  }) as unknown as Parameters<typeof fieldLabel>[0];
}

describe("card field labels", () => {
  for (const [name, messages] of [
    ["en", en],
    ["zh-TW", zh],
  ] as const) {
    const t = translator(messages);

    it(`${name}: every column the report charts and theme tables show has a translation`, () => {
      for (const key of [
        "year",
        "metric",
        "value",
        "label",
        "deviation_pct",
        "return_pct",
        "premium_discount_pct",
        "sub_industry",
      ]) {
        expect(() => fieldLabel(t, key)).not.toThrow();
        expect(fieldLabel(t, key)).not.toBe(key);
      }
    });

    it(`${name}: every mapped field key has a message (a gap would crash the card)`, () => {
      for (const key of LABELED_FIELDS) {
        expect(() => fieldLabel(t, key), key).not.toThrow();
      }
    });

    it(`${name}: enum cell values are translated, free text is left alone`, () => {
      expect(valueLabel(t, "kind", "industry")).not.toBe("industry");
      expect(valueLabel(t, "tier", "upstream")).not.toBe("upstream");
      expect(valueLabel(t, "kind", "晶圓製造")).toBe("晶圓製造");
      expect(valueLabel(t, "name_zh", "industry")).toBe("industry"); // not an enum column
    });
  }
});
