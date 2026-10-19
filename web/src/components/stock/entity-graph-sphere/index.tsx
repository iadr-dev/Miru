"use client";

import { ArrowLeftIcon } from "@phosphor-icons/react";
import { useReducedMotion } from "motion/react";
import { useTranslations } from "next-intl";
import { type FocusEvent, type ReactNode, useEffect, useMemo, useRef, useState } from "react";
import { isCompanyType } from "@/lib/entity-graph-layout";
import { layoutEntitySphere } from "@/lib/entity-graph-sphere";
import { truncateLabel } from "@/lib/entity-graph-view";
import type { EntityLink, EntityNode } from "@/types/entity-graph";
import { SCENE_HEX, TYPE_HEX } from "./palette";
import type { SphereItem, SphereLinkItem } from "./scene-types";
import { SphereCanvas } from "./sphere-canvas";

export type EntityGraphSphereProps = {
  /** The nodes and links left after the filters, exactly what the flat graph draws. */
  readonly nodes: readonly EntityNode[];
  readonly links: readonly EntityLink[];
  readonly hoveredId: string | null;
  readonly selectedId: string | null;
  /** Nodes drawn faint (not part of the highlighted neighbourhood or search). */
  readonly dimmedIds: ReadonlySet<string>;
  readonly matchedIds: ReadonlySet<string>;
  readonly onHover: (id: string | null) => void;
  readonly onSelect: (id: string) => void;
  readonly onBackToFlat: () => void;
};

/**
 * The optional 3D reading of the 關聯圖譜: the company in focus at the front of a sphere and the
 * rest spread around it (`lib/entity-graph-sphere`). The canvas is a decorative duplicate of the
 * list under the graph (`aria-hidden`); the list stays the accessible, keyboard path. The only
 * motion is a slow idle turn that stops for good at the first touch, and never runs while the
 * pointer or focus is inside, the scene is off screen, or reduced motion is set.
 */
export function EntityGraphSphere({
  nodes,
  links,
  hoveredId,
  selectedId,
  dimmedIds,
  matchedIds,
  onHover,
  onSelect,
  onBackToFlat,
}: EntityGraphSphereProps): ReactNode {
  const t = useTranslations("stock.industry.entityGraph");
  const reducedMotion = useReducedMotion() ?? false;
  const panelRef = useRef<HTMLDivElement>(null);
  const [inside, setInside] = useState(false);
  const [visible, setVisible] = useState(true);

  useEffect(() => {
    const panel = panelRef.current;
    if (!panel || typeof IntersectionObserver === "undefined") return;
    const observer = new IntersectionObserver((entries) => {
      setVisible(entries.some((entry) => entry.isIntersecting));
    });
    observer.observe(panel);
    return () => observer.disconnect();
  }, []);

  const placed = useMemo(() => layoutEntitySphere(nodes), [nodes]);
  const focusId = placed.find((p) => p.node.focus)?.node.id ?? null;
  // Hover wins, then the chosen node, then the company in focus: its links show by default.
  const activeId = hoveredId ?? selectedId ?? focusId;

  const items = useMemo<SphereItem[]>(
    () =>
      placed.map((p) => ({
        placed: p,
        color: TYPE_HEX[p.node.type],
        label: truncateLabel(p.node.label),
        company: isCompanyType(p.node.type),
        dimmed: dimmedIds.has(p.node.id),
        matched: matchedIds.has(p.node.id),
        active: p.node.id === activeId,
      })),
    [placed, dimmedIds, matchedIds, activeId],
  );

  const activeLinks = useMemo<SphereLinkItem[]>(() => {
    const byId = new Map(placed.map((p) => [p.node.id, p]));
    return links.flatMap((link) => {
      if (link.source !== activeId && link.target !== activeId) return [];
      const from = byId.get(link.source);
      const to = byId.get(link.target);
      return from && to
        ? [{ key: `${link.source}>${link.target}>${link.kind}`, kind: link.kind, from, to }]
        : [];
    });
  }, [placed, links, activeId]);

  const leave = (event: FocusEvent<HTMLDivElement>): void => {
    if (!event.currentTarget.contains(event.relatedTarget)) setInside(false);
  };

  return (
    <div className="flex flex-col gap-2">
      {/* biome-ignore lint/a11y/noStaticElementInteractions: it only watches pointer and focus to pause the idle turn; it has no action of its own. */}
      <div
        ref={panelRef}
        onPointerEnter={() => setInside(true)}
        onPointerLeave={() => setInside(false)}
        onFocus={() => setInside(true)}
        onBlur={leave}
        className={`relative h-[clamp(20rem,75cqw,36rem)] w-full overflow-hidden rounded-[var(--radius-card)] border border-[var(--border)] ${SCENE_HEX.panelClass}`}
      >
        <div aria-hidden="true" className="absolute inset-0">
          <SphereCanvas
            items={items}
            links={activeLinks}
            selectedId={selectedId}
            paused={inside || !visible}
            reducedMotion={reducedMotion}
            onHover={onHover}
            onSelect={onSelect}
          />
        </div>
        <button
          type="button"
          onClick={onBackToFlat}
          style={{
            color: SCENE_HEX.buttonText,
            backgroundColor: SCENE_HEX.buttonBackground,
            borderColor: SCENE_HEX.buttonBorder,
          }}
          className="absolute top-2 left-2 flex min-h-11 items-center gap-1.5 rounded-[var(--radius-pill)] border px-3 text-[13px] focus-visible:outline-2 focus-visible:outline-[var(--accent)] active:scale-[0.98]"
        >
          <ArrowLeftIcon size={14} weight="regular" aria-hidden />
          {t("backToFlat")}
        </button>
      </div>
      <p className="text-[12px] text-[var(--text-muted)]">{t("sphereHint")}</p>
    </div>
  );
}
