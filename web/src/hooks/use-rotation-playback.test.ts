// @vitest-environment jsdom
import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useRotationPlayback } from "@/hooks/use-rotation-playback";

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["requestAnimationFrame", "cancelAnimationFrame", "performance"] });
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

const advance = (ms: number) => act(() => void vi.advanceTimersByTime(ms));

describe("useRotationPlayback", () => {
  it("starts playing from the oldest week and moves forward with the frame clock", () => {
    const { result } = renderHook(() => useRotationPlayback(27, { weeksPerSecond: 1 }));
    expect(result.current.currentWeek).toBe(0);
    expect(result.current.isPlaying).toBe(true);
    advance(2000);
    expect(result.current.currentWeek).toBeGreaterThan(1.5);
    expect(result.current.currentWeek).toBeLessThan(2.5);
  });

  it("loops back instead of running past the latest week", () => {
    const { result } = renderHook(() => useRotationPlayback(4, { weeksPerSecond: 3 }));
    advance(5000);
    expect(result.current.currentWeek).toBeLessThanOrEqual(3);
    expect(result.current.currentWeek).toBeGreaterThanOrEqual(0);
  });

  it("pauses and resumes from where it stopped", () => {
    const { result } = renderHook(() => useRotationPlayback(27, { weeksPerSecond: 1 }));
    advance(1000);
    act(() => result.current.togglePlaying());
    const paused = result.current.currentWeek;
    expect(result.current.isPlaying).toBe(false);
    advance(3000);
    expect(result.current.currentWeek).toBe(paused);
    act(() => result.current.togglePlaying());
    advance(1000);
    expect(result.current.currentWeek).toBeGreaterThan(paused);
  });

  it("scrubbing jumps to the week, clamps to the series, and pauses", () => {
    const { result } = renderHook(() => useRotationPlayback(10, { weeksPerSecond: 1 }));
    act(() => result.current.seek(6));
    expect(result.current.currentWeek).toBe(6);
    expect(result.current.isPlaying).toBe(false);
    act(() => result.current.seek(99));
    expect(result.current.currentWeek).toBe(9);
    act(() => result.current.seek(-4));
    expect(result.current.currentWeek).toBe(0);
    act(() => result.current.seek(Number.NaN));
    expect(result.current.currentWeek).toBe(0);
    advance(2000);
    expect(result.current.currentWeek).toBe(0); // still paused
  });

  it("with motion disabled it opens on the latest week, never ticks, and still scrubs", () => {
    const { result } = renderHook(() => useRotationPlayback(10, { enabled: false }));
    expect(result.current.currentWeek).toBe(9);
    expect(result.current.isPlaying).toBe(false);
    act(() => result.current.togglePlaying());
    expect(result.current.isPlaying).toBe(false); // play is a no-op without motion
    advance(3000);
    expect(result.current.currentWeek).toBe(9);
    act(() => result.current.seek(2));
    expect(result.current.currentWeek).toBe(2);
  });

  it("does not run for a series with a single week", () => {
    const { result } = renderHook(() => useRotationPlayback(1));
    expect(result.current.isPlaying).toBe(false);
    advance(2000);
    expect(result.current.currentWeek).toBe(0);
  });

  it("tracks the hovered sector", () => {
    const { result } = renderHook(() => useRotationPlayback(5));
    act(() => result.current.setHoveredSectorId("26"));
    expect(result.current.hoveredSectorId).toBe("26");
    act(() => result.current.setHoveredSectorId(null));
    expect(result.current.hoveredSectorId).toBeNull();
  });
});
