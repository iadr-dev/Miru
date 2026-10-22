import { type ComponentType, cloneElement, type ReactElement } from "react";

type SizedChild = ReactElement<{ width?: number; height?: number }>;

/** jsdom has no layout, so recharts' `ResponsiveContainer` renders nothing; swap it for a
 * container that hands its chart a fixed size. Use inside `vi.mock("recharts", …)`. */
export function withFixedSizeContainer<T extends object>(
  actual: T,
): T & { ResponsiveContainer: ComponentType<{ children: SizedChild }> } {
  return {
    ...actual,
    ResponsiveContainer: ({ children }: { children: SizedChild }) =>
      cloneElement(children, { width: 600, height: 300 }),
  };
}
