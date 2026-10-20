import { afterEach, describe, expect, it, vi } from "vitest";
import { secureId, secureRandomInt } from "@/lib/random";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("secureRandomInt", () => {
  it("stays within [0, max)", () => {
    for (let i = 0; i < 200; i++) {
      const n = secureRandomInt(7);
      expect(Number.isInteger(n)).toBe(true);
      expect(n).toBeGreaterThanOrEqual(0);
      expect(n).toBeLessThan(7);
    }
  });

  it("returns 0 for max 1", () => {
    expect(secureRandomInt(1)).toBe(0);
  });

  it("rejects a bad max", () => {
    expect(() => secureRandomInt(0)).toThrow(RangeError);
    expect(() => secureRandomInt(1.5)).toThrow(RangeError);
  });

  it("rejects the biased tail of the 32-bit range", () => {
    const draws = [0xffffffff, 5];
    vi.stubGlobal("crypto", {
      getRandomValues: (buf: Uint32Array) => {
        buf[0] = draws.shift() ?? 0;
        return buf;
      },
    });
    expect(secureRandomInt(10)).toBe(5);
    expect(draws).toHaveLength(0);
  });

  it("falls back to a deterministic counter without crypto", () => {
    vi.stubGlobal("crypto", undefined);
    const values = [secureRandomInt(1000), secureRandomInt(1000), secureRandomInt(1000)];
    expect(values[1]).toBe((values[0] ?? 0) + 1);
    expect(values[2]).toBe((values[1] ?? 0) + 1);
  });
});

describe("secureId", () => {
  it("uses randomUUID when present", () => {
    vi.stubGlobal("crypto", { randomUUID: () => "uuid-1" });
    expect(secureId()).toBe("uuid-1");
  });

  it("falls back to random bytes when randomUUID is missing", () => {
    vi.stubGlobal("crypto", {
      getRandomValues: (bytes: Uint8Array) => {
        bytes.fill(255);
        return bytes;
      },
    });
    expect(secureId()).toMatch(/^[0-9a-z]+-f{32}$/);
  });

  it("falls back to a counter without crypto, still unique", () => {
    vi.stubGlobal("crypto", undefined);
    const a = secureId();
    const b = secureId();
    expect(a).not.toBe(b);
  });

  it("gives distinct ids from the real crypto", () => {
    expect(secureId()).not.toBe(secureId());
  });
});
