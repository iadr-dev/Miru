import type { SpherePlacedNode } from "@/lib/entity-graph-sphere";

/** One node of the sphere, ready to draw. Plain strings only: React context (next-intl) does not
 * cross the `<Canvas>` boundary, so everything the scene shows is resolved before it. */
export type SphereItem = {
  readonly placed: SpherePlacedNode;
  readonly color: string;
  /** The name shown on the label (already shortened). */
  readonly label: string;
  /** Companies are drawn as spheres, every other entity as a rounded cube. */
  readonly company: boolean;
  readonly dimmed: boolean;
  readonly matched: boolean;
  /** Hovered, selected, or (by default) the company in focus. */
  readonly active: boolean;
};

export type SphereLinkItem = {
  readonly key: string;
  readonly kind: "mention" | "cooccurrence" | "supply";
  readonly from: SpherePlacedNode;
  readonly to: SpherePlacedNode;
};

export type SphereSceneProps = {
  readonly items: readonly SphereItem[];
  /** Only the active node's links; the sphere would be unreadable with all of them. */
  readonly links: readonly SphereLinkItem[];
  readonly selectedId: string | null;
  /** True while the pointer or focus is inside, or the scene is off screen: no idle turning. */
  readonly paused: boolean;
  readonly reducedMotion: boolean;
  readonly onHover: (id: string | null) => void;
  readonly onSelect: (id: string) => void;
};
