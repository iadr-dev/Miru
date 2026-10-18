"use client";

import { GraphIcon, MagnifyingGlassIcon, XIcon } from "@phosphor-icons/react";
import { useTranslations } from "next-intl";
import { type KeyboardEvent, type ReactNode, useId, useMemo, useState } from "react";
import { CardSkeletonBody } from "@/components/market/card-skeleton";
import { Card, CardHeader } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { QueryState } from "@/components/ui/query-state";
import { useEntityGraph } from "@/hooks/use-entity-graph";
import { useThemes } from "@/hooks/use-themes";
import { Link } from "@/i18n/navigation";
import { cn } from "@/lib/cn";
import {
  isCompanyType,
  layoutEntityGraph,
  linkEnds,
  neighbourIds,
  type PlacedNode,
} from "@/lib/entity-graph-layout";
import {
  applyGraphFilter,
  companyHref,
  groupByType,
  matchingNodeIds,
  maxNodeWeight,
  type TypeGroup,
  themeKeyForEntity,
} from "@/lib/entity-graph-view";
import {
  ENTITY_TYPES,
  type EntityGraphResponse,
  type EntityLinkKind,
  type EntityNode,
  type EntityType,
} from "@/types/entity-graph";
import type { Market } from "@/types/settings";

/** Entity-type colours are categorical (the non-signed chart tokens): a type is a label, never a
 * price move, so --up/--down never appear here (web/OVERVIEW.md §4). Colour is never the only cue:
 * every type has a name in the legend and the list, and companies are circles where entities are
 * rounded squares. */
const TYPE_COLOR: Record<EntityType, string> = {
  tw_company: "var(--chart-1)",
  intl_company: "var(--chart-2)",
  technology: "var(--chart-4)",
  material: "var(--chart-3)",
  application: "var(--chart-5)",
  product: "var(--chart-6)",
};

const LABEL_MAX_CHARS = 9;

type NodeTarget =
  | { readonly kind: "stock"; readonly href: string }
  | { readonly kind: "theme"; readonly href: string }
  | { readonly kind: "detail" };

type ThemeRef = { readonly key: string; readonly name_zh: string; readonly name_en: string };

function targetOf(node: EntityNode, themes: readonly ThemeRef[]): NodeTarget {
  if (isCompanyType(node.type)) {
    const href = companyHref(node);
    return href === null ? { kind: "detail" } : { kind: "stock", href };
  }
  const themeKey = themeKeyForEntity(node.label, themes);
  return themeKey === null
    ? { kind: "detail" }
    : { kind: "theme", href: `/themes/${encodeURIComponent(themeKey)}` };
}

function truncateLabel(label: string): string {
  const chars = Array.from(label);
  return chars.length <= LABEL_MAX_CHARS ? label : `${chars.slice(0, LABEL_MAX_CHARS).join("")}…`;
}

/** 關聯圖譜: the company in focus in the middle, the entities it is mentioned with on a ring around
 * it and the other companies that share those entities on an outer ring. A phone gets the grouped
 * list instead of the canvas (the list is also the accessible reading of the same data). */
export function EntityGraph({
  symbol,
  market,
}: {
  readonly symbol: string;
  readonly market: Market;
}): ReactNode {
  const t = useTranslations("stock.industry.entityGraph");
  const query = useEntityGraph(symbol, market);
  const themesQuery = useThemes("concept");
  const themes = themesQuery.data?.themes ?? [];

  return (
    <Card as="section" className="@container">
      <CardHeader title={t("title")} description={t("hint")} />
      <QueryState query={query} skeleton={<CardSkeletonBody height={320} />}>
        {(graph) => <EntityGraphBody graph={graph} themes={themes} />}
      </QueryState>
    </Card>
  );
}

function EntityGraphBody({
  graph,
  themes,
}: {
  readonly graph: EntityGraphResponse;
  readonly themes: readonly ThemeRef[];
}): ReactNode {
  const t = useTranslations("stock.industry.entityGraph");
  const markerId = `${useId().replaceAll(":", "")}-arrow`;
  const [minWeight, setMinWeight] = useState(1);
  const [hiddenTypes, setHiddenTypes] = useState<ReadonlySet<EntityType>>(new Set());
  const [search, setSearch] = useState("");
  const [activeId, setActiveId] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const maxWeight = maxNodeWeight(graph.nodes);
  const filtered = useMemo(
    () => applyGraphFilter(graph.nodes, graph.links, { minWeight, hiddenTypes }),
    [graph.nodes, graph.links, minWeight, hiddenTypes],
  );
  const layout = useMemo(() => layoutEntityGraph(filtered.nodes), [filtered.nodes]);
  const groups = useMemo(() => groupByType(filtered.nodes), [filtered.nodes]);
  const matches = useMemo(() => matchingNodeIds(filtered.nodes, search), [filtered.nodes, search]);
  const placedById = useMemo(
    () => new Map(layout.nodes.map((placed) => [placed.node.id, placed])),
    [layout.nodes],
  );
  const presentTypes = useMemo(
    () => ENTITY_TYPES.filter((type) => graph.nodes.some((n) => !n.focus && n.type === type)),
    [graph.nodes],
  );

  const focusNode = graph.nodes.find((node) => node.focus);
  const peers = graph.nodes.filter((node) => !node.focus);
  if (peers.length === 0) {
    return (
      <EmptyState
        compact
        icon={<GraphIcon size={20} weight="regular" />}
        title={t("emptyTitle")}
        description={t("emptyBody")}
      />
    );
  }

  const canvasLabel = t("canvasAria", {
    name: focusNode?.label ?? graph.symbol,
    count: filtered.nodes.length,
  });
  const neighbours = activeId === null ? null : neighbourIds(filtered.links, activeId);
  const isDimmed = (id: string): boolean => {
    if (activeId !== null) {
      return id !== activeId && !neighbours?.has(id);
    }
    return matches.size > 0 && !matches.has(id);
  };
  const selected = selectedId === null ? undefined : placedById.get(selectedId)?.node;

  function toggleType(type: EntityType): void {
    setHiddenTypes((current) => {
      const next = new Set(current);
      if (next.has(type)) next.delete(type);
      else next.add(type);
      return next;
    });
  }

  return (
    <div className="flex flex-col gap-4">
      <Controls
        maxWeight={maxWeight}
        minWeight={minWeight}
        onMinWeight={setMinWeight}
        search={search}
        onSearch={setSearch}
        matchCount={matches.size}
        types={presentTypes}
        hiddenTypes={hiddenTypes}
        onToggleType={toggleType}
        countOf={(type) => peers.filter((n) => n.type === type).length}
      />

      {filtered.nodes.length > 1 ? (
        <div className="hidden sm:block">
          <svg
            role="group"
            aria-label={canvasLabel}
            viewBox={`${layout.viewBox.x} ${layout.viewBox.y} ${layout.viewBox.w} ${layout.viewBox.h}`}
            className="mx-auto h-auto max-h-[640px] w-full"
          >
            <title>{canvasLabel}</title>
            <defs>
              <marker
                id={markerId}
                viewBox="0 0 10 10"
                refX="9"
                refY="5"
                markerWidth="7"
                markerHeight="7"
                orient="auto"
              >
                <path d="M0 0 L10 5 L0 10 z" fill="var(--text)" />
              </marker>
            </defs>
            {layout.ringRadii.map((radius) => (
              <circle
                key={radius}
                r={radius}
                fill="none"
                stroke="var(--border)"
                strokeDasharray="2 6"
                aria-hidden
              />
            ))}
            <g aria-hidden>
              {filtered.links.map((link) => {
                const from = placedById.get(link.source);
                const to = placedById.get(link.target);
                if (!from || !to) return null;
                const touchesActive = link.source === activeId || link.target === activeId;
                const faded = activeId === null ? matches.size > 0 : !touchesActive;
                return (
                  <LinkLine
                    key={`${link.source}>${link.target}>${link.kind}`}
                    kind={link.kind}
                    from={from}
                    to={to}
                    faded={faded}
                    markerId={markerId}
                  />
                );
              })}
            </g>
            {layout.nodes.map((placed) => (
              <GraphNode
                key={placed.node.id}
                placed={placed}
                target={targetOf(placed.node, themes)}
                dimmed={isDimmed(placed.node.id)}
                matched={matches.has(placed.node.id)}
                onActive={setActiveId}
                onSelect={setSelectedId}
              />
            ))}
          </svg>
        </div>
      ) : null}

      <Legend />

      {selected ? (
        <DetailCard
          node={selected}
          neighbourLabels={[...neighbourIds(filtered.links, selected.id)]
            .map((id) => placedById.get(id)?.node.label)
            .filter((label): label is string => label !== undefined)}
          onClose={() => setSelectedId(null)}
        />
      ) : null}

      <ListFallback groups={groups} themes={themes} onSelect={setSelectedId} />
    </div>
  );
}

function Controls({
  maxWeight,
  minWeight,
  onMinWeight,
  search,
  onSearch,
  matchCount,
  types,
  hiddenTypes,
  onToggleType,
  countOf,
}: {
  readonly maxWeight: number;
  readonly minWeight: number;
  readonly onMinWeight: (value: number) => void;
  readonly search: string;
  readonly onSearch: (value: string) => void;
  readonly matchCount: number;
  readonly types: readonly EntityType[];
  readonly hiddenTypes: ReadonlySet<EntityType>;
  readonly onToggleType: (type: EntityType) => void;
  readonly countOf: (type: EntityType) => number;
}): ReactNode {
  const t = useTranslations("stock.industry.entityGraph");
  const sliderId = useId();
  const searchId = useId();
  const sliderMax = Math.max(1, Math.ceil(maxWeight));
  let matchStatus = "";
  if (search.trim().length > 0) {
    matchStatus = matchCount === 0 ? t("noMatches") : t("matches", { count: matchCount });
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="grid gap-3 @lg:grid-cols-2">
        <div className="flex flex-col gap-1">
          <div className="flex justify-between text-[12px] text-[var(--text-muted)]">
            <label htmlFor={sliderId}>{t("sliderLabel")}</label>
            <output htmlFor={sliderId} className="font-num text-[var(--text)] tabular-nums">
              {minWeight}
            </output>
          </div>
          <input
            id={sliderId}
            type="range"
            min={1}
            max={sliderMax}
            step={1}
            value={Math.min(minWeight, sliderMax)}
            disabled={sliderMax <= 1}
            onChange={(event) => onMinWeight(Number(event.target.value))}
            className="min-h-11 w-full accent-[var(--accent)] disabled:opacity-50"
          />
        </div>
        <div className="flex flex-col gap-1">
          <label htmlFor={searchId} className="text-[12px] text-[var(--text-muted)]">
            {t("searchLabel")}
          </label>
          <div className="relative">
            <MagnifyingGlassIcon
              size={14}
              weight="regular"
              aria-hidden
              className="pointer-events-none absolute inset-y-0 left-2.5 my-auto text-[var(--text-muted)]"
            />
            <input
              id={searchId}
              type="search"
              value={search}
              onChange={(event) => onSearch(event.target.value)}
              placeholder={t("searchPlaceholder")}
              className="min-h-11 w-full rounded-[var(--radius-input)] border border-[var(--border)] bg-[var(--surface)] pr-3 pl-8 text-[var(--text)] text-sm focus-visible:outline-2 focus-visible:outline-[var(--accent)]"
            />
          </div>
          <p role="status" className="min-h-4 text-[11px] text-[var(--text-muted)]">
            {matchStatus}
          </p>
        </div>
      </div>

      <div role="group" aria-label={t("typeFilterLabel")} className="flex flex-wrap gap-2">
        {types.map((type) => {
          const on = !hiddenTypes.has(type);
          return (
            <button
              key={type}
              type="button"
              aria-pressed={on}
              onClick={() => onToggleType(type)}
              className={cn(
                "flex min-h-11 items-center gap-1.5 rounded-[var(--radius-pill)] border px-3 text-[13px] transition-colors",
                on
                  ? "border-[var(--accent)] bg-[var(--accent-weak)] text-[var(--text)]"
                  : "border-[var(--border)] text-[var(--text-muted)] hover:text-[var(--text)]",
              )}
            >
              <TypeSwatch type={type} />
              {t(`type.${type}` as const)}
              <span className="font-num text-[11px] tabular-nums opacity-70">{countOf(type)}</span>
            </button>
          );
        })}
      </div>
    </div>
  );
}

function TypeSwatch({ type }: { readonly type: EntityType }): ReactNode {
  return (
    <span
      aria-hidden
      className={cn(
        "inline-block size-2.5 border",
        isCompanyType(type) ? "rounded-full" : "rounded-[3px]",
      )}
      style={{
        borderColor: TYPE_COLOR[type],
        backgroundColor: `color-mix(in srgb, ${TYPE_COLOR[type]} 35%, transparent)`,
      }}
    />
  );
}

function Legend(): ReactNode {
  const t = useTranslations("stock.industry.entityGraph");
  return (
    <div className="hidden flex-col gap-1.5 text-[12px] text-[var(--text-muted)] sm:flex">
      <ul className="flex flex-wrap items-center gap-x-4 gap-y-1.5" aria-label={t("legendTitle")}>
        <LegendLine kind="supply" label={t("linkSupply")} />
        <LegendLine kind="mention" label={t("linkMention")} />
        <LegendLine kind="cooccurrence" label={t("linkCooccurrence")} />
      </ul>
      <p>{t("cooccurrenceNote")}</p>
    </div>
  );
}

function LegendLine({
  kind,
  label,
}: {
  readonly kind: EntityLinkKind;
  readonly label: string;
}): ReactNode {
  return (
    <li className="flex items-center gap-1.5">
      <svg width="28" height="10" aria-hidden className="shrink-0">
        <title>{label}</title>
        <line x1="1" y1="5" x2={kind === "supply" ? 22 : 27} y2="5" {...linkStyle(kind)} />
        {kind === "supply" ? <path d="M21 1 L27 5 L21 9 z" fill="var(--text)" /> : null}
      </svg>
      {label}
    </li>
  );
}

function linkStyle(kind: EntityLinkKind): {
  stroke: string;
  strokeWidth: number;
  strokeDasharray?: string;
} {
  switch (kind) {
    case "supply":
      return { stroke: "var(--text)", strokeWidth: 2 };
    case "cooccurrence":
      return { stroke: "var(--text-muted)", strokeWidth: 1.25, strokeDasharray: "4 3" };
    case "mention":
      return { stroke: "var(--text-muted)", strokeWidth: 1 };
    default:
      return assertNeverKind(kind);
  }
}

function assertNeverKind(kind: never): never {
  throw new Error(`Unknown link kind: ${String(kind)}`);
}

function linkOpacity(kind: EntityLinkKind, faded: boolean): number {
  if (faded) return 0.12;
  return kind === "mention" ? 0.55 : 0.9;
}

function LinkLine({
  kind,
  from,
  to,
  faded,
  markerId,
}: {
  readonly kind: EntityLinkKind;
  readonly from: PlacedNode;
  readonly to: PlacedNode;
  readonly faded: boolean;
  readonly markerId: string;
}): ReactNode {
  const { x1, y1, x2, y2 } = linkEnds(from, to);
  return (
    <line
      x1={x1}
      y1={y1}
      x2={x2}
      y2={y2}
      {...linkStyle(kind)}
      opacity={linkOpacity(kind, faded)}
      {...(kind === "supply" ? { markerEnd: `url(#${markerId})` } : {})}
      className="motion-safe:transition-opacity"
    />
  );
}

function GraphNode({
  placed,
  target,
  dimmed,
  matched,
  onActive,
  onSelect,
}: {
  readonly placed: PlacedNode;
  readonly target: NodeTarget;
  readonly dimmed: boolean;
  readonly matched: boolean;
  readonly onActive: (id: string | null) => void;
  readonly onSelect: (id: string) => void;
}): ReactNode {
  const t = useTranslations("stock.industry.entityGraph");
  const { node, x, y, r } = placed;
  const color = TYPE_COLOR[node.type];
  const typeLabel = t(`type.${node.type}` as const);
  const weightLabel = node.focus ? "" : `, ${t("weight", { count: node.weight })}`;
  const name = `${node.label}, ${typeLabel}${weightLabel}`;
  const label = truncateLabel(node.label);
  const outward = labelPlacement(placed);

  const body = (
    <g
      opacity={dimmed ? 0.22 : 1}
      className="motion-safe:transition-opacity"
      transform={`translate(${x} ${y})`}
    >
      <title>{name}</title>
      {matched ? <circle r={r + 7} fill="none" stroke="var(--accent)" strokeWidth={2.5} /> : null}
      <circle
        r={r + 5}
        fill="none"
        stroke="var(--accent)"
        strokeWidth={2}
        className="opacity-0 group-focus-visible:opacity-100"
      />
      {isCompanyType(node.type) ? (
        <circle
          r={r}
          fill={`color-mix(in srgb, ${color} 30%, var(--surface))`}
          stroke={color}
          strokeWidth={node.focus ? 3 : 1.5}
        />
      ) : (
        <rect
          x={-r}
          y={-r}
          width={r * 2}
          height={r * 2}
          rx={5}
          fill={`color-mix(in srgb, ${color} 30%, var(--surface))`}
          stroke={color}
          strokeWidth={1.5}
        />
      )}
      <text
        x={outward.x}
        y={outward.y}
        textAnchor={outward.anchor}
        fontSize={node.focus ? 13 : 11}
        fontWeight={node.focus ? 600 : 400}
        fill="var(--text)"
        stroke="var(--bg)"
        strokeWidth={3}
        paintOrder="stroke"
        aria-hidden
      >
        {label}
      </text>
    </g>
  );

  const highlight = (): void => onActive(node.id);
  const clearHighlight = (): void => onActive(null);

  if (node.focus) {
    return <g aria-label={name}>{body}</g>;
  }
  if (target.kind !== "detail") {
    return (
      <Link
        href={target.href}
        aria-label={`${name} — ${target.kind === "stock" ? t("openStock") : t("openTheme")}`}
        className="group outline-none"
        onMouseEnter={highlight}
        onMouseLeave={clearHighlight}
        onFocus={highlight}
        onBlur={clearHighlight}
      >
        {body}
      </Link>
    );
  }
  const activate = (): void => onSelect(node.id);
  return (
    <g
      role="button"
      tabIndex={0}
      aria-label={`${name} — ${t("showDetail")}`}
      className="group cursor-pointer outline-none"
      onClick={activate}
      onKeyDown={(event: KeyboardEvent<SVGGElement>) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          activate();
        }
      }}
      onMouseEnter={highlight}
      onMouseLeave={clearHighlight}
      onFocus={highlight}
      onBlur={clearHighlight}
    >
      {body}
    </g>
  );
}

/** Where a node's label goes: below the centre node, otherwise pushed outward from the centre. */
type LabelPlacement = { x: number; y: number; anchor: "start" | "middle" | "end" };

function labelPlacement(placed: PlacedNode): LabelPlacement {
  const { x, y, r, ring } = placed;
  if (ring === 0) {
    return { x: 0, y: r + 16, anchor: "middle" };
  }
  const distance = Math.hypot(x, y) || 1;
  const ux = x / distance;
  const uy = y / distance;
  if (Math.abs(ux) < 0.35) {
    return { x: 0, y: uy < 0 ? -(r + 8) : r + 16, anchor: "middle" };
  }
  return { x: ux > 0 ? r + 7 : -(r + 7), y: 4, anchor: ux > 0 ? "start" : "end" };
}

function DetailCard({
  node,
  neighbourLabels,
  onClose,
}: {
  readonly node: EntityNode;
  readonly neighbourLabels: readonly string[];
  readonly onClose: () => void;
}): ReactNode {
  const t = useTranslations("stock.industry.entityGraph");
  return (
    <section
      aria-label={t("detailTitle", { name: node.label })}
      className="rounded-[var(--radius-input)] border border-[var(--border)] bg-[var(--surface-2)] p-3"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="break-words font-semibold text-[14px] text-[var(--text)]">{node.label}</p>
          <p className="mt-0.5 flex items-center gap-1.5 text-[12px] text-[var(--text-muted)]">
            <TypeSwatch type={node.type} />
            {t(`type.${node.type}` as const)} · {t("weight", { count: node.weight })}
          </p>
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label={t("detailClose")}
          className="flex size-11 shrink-0 items-center justify-center rounded-full text-[var(--text-muted)] hover:text-[var(--text)] focus-visible:outline-2 focus-visible:outline-[var(--accent)]"
        >
          <XIcon size={16} weight="regular" aria-hidden />
        </button>
      </div>
      <p className="mt-2 text-[12px] text-[var(--text-muted)]">{t("detailNoTheme")}</p>
      {neighbourLabels.length > 0 ? (
        <p className="mt-2 break-words text-[12px] text-[var(--text)]">
          <span className="text-[var(--text-muted)]">{t("detailConnected")}</span>{" "}
          {neighbourLabels.join("、")}
        </p>
      ) : null}
    </section>
  );
}

function ListFallback({
  groups,
  themes,
  onSelect,
}: {
  readonly groups: readonly TypeGroup[];
  readonly themes: readonly ThemeRef[];
  readonly onSelect: (id: string) => void;
}): ReactNode {
  const t = useTranslations("stock.industry.entityGraph");
  return (
    <section aria-label={t("listTitle")} className="flex flex-col gap-3">
      <h3 className="font-semibold text-[13px] text-[var(--text)]">{t("listTitle")}</h3>
      {groups.length === 0 ? (
        <p className="text-[13px] text-[var(--text-muted)]">{t("filteredEmpty")}</p>
      ) : (
        groups.map((group) => (
          <div key={group.type} className="flex flex-col gap-1.5">
            <h4 className="flex items-center gap-1.5 text-[12px] text-[var(--text-muted)]">
              <TypeSwatch type={group.type} />
              {t(`type.${group.type}` as const)}
              <span className="font-num tabular-nums">({group.nodes.length})</span>
            </h4>
            <ul className="grid gap-1.5 @md:grid-cols-2">
              {group.nodes.map((node) => (
                <li key={node.id}>
                  <ListRow node={node} target={targetOf(node, themes)} onSelect={onSelect} />
                </li>
              ))}
            </ul>
          </div>
        ))
      )}
    </section>
  );
}

function ListRow({
  node,
  target,
  onSelect,
}: {
  readonly node: EntityNode;
  readonly target: NodeTarget;
  readonly onSelect: (id: string) => void;
}): ReactNode {
  const t = useTranslations("stock.industry.entityGraph");
  const rowClass =
    "flex min-h-11 w-full items-center justify-between gap-3 rounded-[var(--radius-input)] border border-[var(--border)] px-3 text-left text-[13px] text-[var(--text)] hover:bg-[var(--surface-2)] focus-visible:outline-2 focus-visible:outline-[var(--accent)]";
  const content = (
    <>
      <span className="min-w-0 break-words">
        {node.label}
        {node.symbol ? (
          <span className="ml-1.5 font-num text-[11px] text-[var(--text-muted)] tabular-nums">
            {node.symbol}
          </span>
        ) : null}
      </span>
      <span className="shrink-0 font-num text-[11px] text-[var(--text-muted)] tabular-nums">
        {t("weight", { count: node.weight })}
      </span>
    </>
  );
  if (target.kind === "detail") {
    return (
      <button type="button" className={rowClass} onClick={() => onSelect(node.id)}>
        {content}
      </button>
    );
  }
  return (
    <Link href={target.href} className={rowClass}>
      {content}
    </Link>
  );
}
