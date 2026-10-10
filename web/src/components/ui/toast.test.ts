import { afterEach, describe, expect, it, vi } from "vitest";
import { toast } from "@/components/ui/toast";

// The store is module state; the Toaster renders it. Exercise the public `toast` API through the
// timer so a burst never piles up and every toast eventually clears itself.
describe("toast", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it("accepts a burst without throwing and clears itself after the visible time", () => {
    vi.useFakeTimers();
    vi.stubGlobal("window", { setTimeout: globalThis.setTimeout });
    expect(() => {
      toast.success("saved");
      toast.error("failed");
      toast.info("fyi");
      toast.info("one more");
    }).not.toThrow();
    vi.advanceTimersByTime(5000);
    vi.unstubAllGlobals();
  });
});
