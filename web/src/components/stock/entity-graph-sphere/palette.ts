/**
 * Concrete colour values for the 3D relation graph. three.js cannot read CSS variables, so this
 * folder is, like `components/market/sector-rotation-scene/`, the one place hex values are allowed
 * (web/OVERVIEW.md §2). The type colours are the dark-theme `--chart-*` tokens from
 * `src/app/tokens.css`, mapped exactly as the flat graph maps them (`TYPE_COLOR` in
 * `entity-graph.tsx`): categorical, never the signed up/down colours. The scene always sits on its
 * own dark panel, so the dark-theme values are the ones that read in both themes.
 */
import type { EntityType } from "@/types/entity-graph";

export const TYPE_HEX: Record<EntityType, string> = {
  tw_company: "#6aa0f0",
  intl_company: "#b58ae0",
  technology: "#4fb5a5",
  material: "#e5b53d",
  application: "#e27aa3",
  product: "#a39787",
};

export const SCENE_HEX = {
  /** Panel background: a dark radial wash, the same family as the sector rotation scene. */
  panelClass: "bg-[radial-gradient(120%_90%_at_50%_35%,#15183a_0%,#080a18_70%)]",
  guide: "#8b93c9",
  supply: "#f3f5ff",
  mention: "#aab1d6",
  labelText: "#eef0fb",
  labelBackground: "rgba(8, 10, 24, 0.82)",
  buttonText: "#eef0fb",
  buttonBackground: "rgba(8, 10, 24, 0.82)",
  buttonBorder: "rgba(170, 177, 214, 0.45)",
} as const;
