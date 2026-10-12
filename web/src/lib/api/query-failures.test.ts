import { afterEach, describe, expect, it, vi } from "vitest";
import { isGenuineFailure, onQueryFailed, reportQueryFailure } from "@/lib/api/query-failures";

describe("query failure signal", () => {
  let stop: (() => void) | undefined;
  afterEach(() => stop?.());

  it("counts server errors and no-response failures only", () => {
    expect(isGenuineFailure({ status: 500 })).toBe(true);
    expect(isGenuineFailure({ status: 503 })).toBe(true);
    expect(isGenuineFailure(new TypeError("Failed to fetch"))).toBe(true);
    for (const status of [400, 401, 403, 404, 422, 429]) {
      expect(isGenuineFailure({ status })).toBe(false);
    }
  });

  it("notifies listeners for a genuine failure and not for a handled one", () => {
    const heard = vi.fn();
    stop = onQueryFailed(heard);

    reportQueryFailure({ status: 404 });
    reportQueryFailure({ status: 502 });

    expect(heard).toHaveBeenCalledTimes(1);
  });
});
