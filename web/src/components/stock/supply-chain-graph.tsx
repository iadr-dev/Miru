"use client";

import { ArrowRightIcon, ArrowSquareOutIcon, BooksIcon } from "@phosphor-icons/react";
import * as Popover from "@radix-ui/react-popover";
import { useQuery } from "@tanstack/react-query";
import { type MotionStyle, motion, useReducedMotion } from "motion/react";
import { useTranslations } from "next-intl";
import { type ReactNode, useMemo, useState } from "react";
import { CardSkeletonBody } from "@/components/market/card-skeleton";
import { Card, CardHeader } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { FeatureLock } from "@/components/ui/feature-lock";
import { QueryFailure } from "@/components/ui/query-state";
import { SignedChange } from "@/components/ui/signed-change";
import { useSymbolQuotes } from "@/hooks/use-symbol-quotes";
import { Link } from "@/i18n/navigation";
import { queryKeys } from "@/lib/api/query-keys";
import { getSupplyChainEdges } from "@/lib/api/stock-detail";
import { cn } from "@/lib/cn";
import { entrance, staggerChildren } from "@/lib/motion";
import { safeHttpUrl } from "@/lib/safe-url";
import { groupPeers, type Peer, type PeerRole, peerKey } from "@/lib/supply-chain";
import type { Market } from "@/types/settings";
import type { EdgeConfidence, SupplyChainEdge } from "@/types/stock/profile";

/** Relationship colours are categorical (the non-signed chart tokens) - never --up/--down, which
 * mean a price move and nothing else (web/OVERVIEW.md §4). */
const ROLE_COLOR: Record<PeerRole, string> = {
  supplier: "var(--chart-1)",
  customer: "var(--chart-2)",
  competitor: "var(--chart-6)",
};

/** Evidence-grade chip colours: categorical too (a grade is not a price move). The chip always
 * carries its word, so colour is never the only cue. */
const GRADE_COLOR: Record<EdgeConfidence, string> = {
  high: "var(--chart-4)",
  medium: "var(--chart-3)",
  low: "var(--chart-6)",
};

/** Peers shown per side before "show all". Keeps the graph legible for a hub like TSMC. */
const COLLAPSED_COUNT = 6;
/** Height of one peer row in the diagram, px. The connector SVG shares this unit. */
const ROW_PX = 84;

type HoverProps = {
  readonly onMouseEnter: () => void;
  readonly onMouseLeave: () => void;
  readonly onFocus: () => void;
  readonly onBlur: () => void;
};

type QuoteLite = { price: number | null; changePct: number | null };

/** 供應鏈關係圖: the company in focus in the middle, its suppliers on the left and its customers
 * on the right, so supply flows left to right; competitors sit in a row beneath. Each peer links
 * to its own page and shows the day's move (the one place red/green appears - it is a price). */
export function SupplyChainGraph({
  symbol,
  market,
}: {
  readonly symbol: string;
  readonly market: Market;
}): ReactNode {
  const t = useTranslations("stock.industry");
  const tLock = useTranslations("featureLock");
  const query = useQuery({
    queryKey: queryKeys.symbolRead("supply_chain_edges", symbol, market),
    queryFn: () => getSupplyChainEdges(symbol, market),
  });
  const [hovered, setHovered] = useState<string | null>(null);
  const [expanded, setExpanded] = useState(false);

  const edges = query.data?.edges;
  const groups = useMemo(() => groupPeers(edges ?? [], symbol, market), [edges, symbol, market]);

  // Peers can sit in either market (TSMC supplies Apple), and the quote lookup is per market, so a
  // peer is looked up by (market, code) - the same code can exist in both markets.
  const allPeers = [...groups.supplier, ...groups.customer, ...groups.competitor];
  const codesIn = (m: Market): string[] => [
    ...new Set([
      ...(m === market ? [symbol] : []),
      ...allPeers.filter((p) => p.market === m).map((p) => p.symbol),
    ]),
  ];
  const twQuotes = useSymbolQuotes(codesIn("TW"), "TW");
  const usQuotes = useSymbolQuotes(codesIn("US"), "US");
  const quoteOf = (code: string, m: Market): QuoteLite | undefined =>
    (m === "TW" ? twQuotes : usQuotes).quotes.find((q) => q.code === code);

  if (query.isLoading) {
    return (
      <Card as="section">
        <CardSkeletonBody height={320} />
      </Card>
    );
  }
  if (query.isError) {
    return <QueryFailure query={query} />;
  }
  const data = query.data;
  if (!data) {
    return null;
  }
  if (data.edge_count === 0) {
    return (
      <Card as="section">
        <CardHeader title={t("supplyChainTitle")} />
        <EmptyState compact title={t("supplyChainUnavailable")} />
      </Card>
    );
  }

  const focusName = focusNameOf(data.edges, symbol);
  const limit = expanded ? Number.POSITIVE_INFINITY : COLLAPSED_COUNT;
  const suppliers = groups.supplier.slice(0, limit);
  const customers = groups.customer.slice(0, limit);
  const hiddenCount =
    groups.supplier.length - suppliers.length + (groups.customer.length - customers.length);
  const canToggle =
    groups.supplier.length > COLLAPSED_COUNT || groups.customer.length > COLLAPSED_COUNT;
  const rows = Math.max(suppliers.length, customers.length, 1);
  const height = rows * ROW_PX;
  const focusQuote = quoteOf(symbol, market);
  const sourcesLocked = data.sources_locked;

  return (
    <Card as="section" className="overflow-hidden">
      <CardHeader title={t("supplyChainTitle")} description={t("supplyChainHint")} />
      <p className="sr-only">
        {t("supplyChainGraphAria", { name: focusName, count: data.edge_count })}
      </p>

      <div className="mx-auto w-full max-w-[980px]">
        <ColumnHeadings focusName={focusName} />

        <div className="relative">
          <Connectors height={height} left={suppliers} right={customers} hovered={hovered} />
          <div className="relative grid gap-4 md:grid-cols-[36fr_28fr_36fr] md:gap-0">
            <PeerColumn
              side="left"
              kind="supplier"
              peers={suppliers}
              height={height}
              emptyLabel={t("supplyChainNoSuppliers")}
              hovered={hovered}
              onHover={setHovered}
              quoteOf={quoteOf}
              sourcesLocked={sourcesLocked}
            />

            <div className="relative hidden md:block" style={{ height }}>
              <FocusNode name={focusName} symbol={symbol} quote={focusQuote} />
            </div>
            <div className="md:hidden">
              <FocusNode name={focusName} symbol={symbol} quote={focusQuote} inline />
            </div>

            <PeerColumn
              side="right"
              kind="customer"
              peers={customers}
              height={height}
              emptyLabel={t("supplyChainNoCustomers")}
              hovered={hovered}
              onHover={setHovered}
              quoteOf={quoteOf}
              sourcesLocked={sourcesLocked}
            />
          </div>
        </div>
      </div>

      {groups.competitor.length > 0 ? (
        <div className="mx-auto mt-5 w-full max-w-[980px] border-[var(--border)] border-t pt-4">
          <RoleLabel kind="competitor" label={t("supplyChainCompetitors")} />
          <PeerList
            peers={groups.competitor}
            kind="competitor"
            hovered={hovered}
            onHover={setHovered}
            quoteOf={quoteOf}
            sourcesLocked={sourcesLocked}
            wrap
          />
        </div>
      ) : null}

      {data.locked && data.hidden_count > 0 ? (
        <FeatureLock
          className="mt-4"
          message={tLock("supplyChain.more", { count: data.hidden_count })}
          benefit={tLock("supplyChain.benefit")}
        />
      ) : null}

      {canToggle ? (
        <button
          type="button"
          onClick={() => setExpanded((prev) => !prev)}
          className="mt-4 min-h-11 rounded-[var(--radius-button)] px-3 text-[13px] text-[var(--accent-text)] hover:bg-[var(--surface-2)]"
        >
          {expanded ? t("supplyChainShowLess") : t("supplyChainShowAll", { count: hiddenCount })}
        </button>
      ) : null}
    </Card>
  );
}

function ColumnHeadings({ focusName }: { readonly focusName: string }): ReactNode {
  const t = useTranslations("stock.industry");
  return (
    <div className="mb-3 hidden items-center text-[12px] text-[var(--text-muted)] md:grid md:grid-cols-[36fr_28fr_36fr]">
      <RoleLabel kind="supplier" label={t("supplyChainSuppliers")} />
      <span className="flex items-center justify-center gap-1.5">
        <ArrowRightIcon size={12} weight="regular" aria-hidden />
        <span className="truncate">{t("supplyChainFlow", { name: focusName })}</span>
        <ArrowRightIcon size={12} weight="regular" aria-hidden />
      </span>
      <RoleLabel kind="customer" label={t("supplyChainCustomers")} align="end" />
    </div>
  );
}

function RoleLabel({
  kind,
  label,
  align = "start",
}: {
  readonly kind: PeerRole;
  readonly label: string;
  readonly align?: "start" | "end";
}): ReactNode {
  return (
    <span
      className={cn(
        "mb-2 flex items-center gap-1.5 font-medium text-[12px] text-[var(--text)] md:mb-0",
        align === "end" && "md:justify-end",
      )}
    >
      <span
        aria-hidden
        className="size-2 rounded-full"
        style={{ backgroundColor: ROLE_COLOR[kind] }}
      />
      {label}
    </span>
  );
}

function PeerColumn({
  side,
  kind: role,
  peers,
  height,
  emptyLabel,
  hovered,
  onHover,
  quoteOf,
  sourcesLocked,
}: {
  readonly side: "left" | "right";
  readonly kind: PeerRole;
  readonly peers: readonly Peer[];
  readonly height: number;
  readonly emptyLabel: string;
  readonly hovered: string | null;
  readonly onHover: (symbol: string | null) => void;
  readonly quoteOf: (code: string, market: Market) => QuoteLite | undefined;
  readonly sourcesLocked: boolean;
}): ReactNode {
  const t = useTranslations("stock.industry");
  const heading = role === "supplier" ? t("supplyChainSuppliers") : t("supplyChainCustomers");
  return (
    <div className={cn("min-w-0", side === "left" ? "md:pr-3" : "md:pl-3")}>
      <div className="md:hidden">
        <RoleLabel kind={role} label={heading} />
      </div>
      {peers.length === 0 ? (
        <p className="py-3 text-[12px] text-[var(--text-muted)]">{emptyLabel}</p>
      ) : (
        <PeerList
          peers={peers}
          kind={role}
          hovered={hovered}
          onHover={onHover}
          quoteOf={quoteOf}
          sourcesLocked={sourcesLocked}
          height={height}
          side={side}
        />
      )}
    </div>
  );
}

function PeerList({
  peers,
  kind: role,
  hovered,
  onHover,
  quoteOf,
  sourcesLocked,
  height,
  side,
  wrap = false,
}: {
  readonly peers: readonly Peer[];
  readonly kind: PeerRole;
  readonly hovered: string | null;
  readonly onHover: (key: string | null) => void;
  readonly quoteOf: (code: string, market: Market) => QuoteLite | undefined;
  readonly sourcesLocked: boolean;
  readonly height?: number;
  readonly side?: "left" | "right";
  readonly wrap?: boolean;
}): ReactNode {
  const reduceMotion = useReducedMotion();
  return (
    <motion.ul
      initial="hidden"
      animate="show"
      variants={staggerChildren(reduceMotion)}
      className={cn(
        "m-0 list-none p-0",
        wrap
          ? "flex flex-wrap gap-2"
          : // The diagram's row height only exists beside the focus node (md and up); stacked on
            // a phone the list is just as tall as its peers.
            "grid gap-2 md:min-h-[var(--peer-list-height)] md:grid-rows-[repeat(var(--peer-rows),1fr)] md:gap-0",
      )}
      style={
        !wrap && height
          ? ({
              "--peer-list-height": `${height}px`,
              "--peer-rows": peers.length,
            } as MotionStyle)
          : {}
      }
    >
      {peers.map((peer) => (
        <motion.li
          key={`${role}:${peerKey(peer)}`}
          variants={entrance(reduceMotion)}
          className={cn(
            "flex items-center",
            wrap ? "" : "min-h-12 py-1",
            side === "left" && "md:justify-end",
          )}
        >
          <PeerNode
            peer={peer}
            dimmed={hovered !== null && hovered !== peerKey(peer)}
            onHover={onHover}
            quote={quoteOf(peer.symbol, peer.market)}
            sourcesLocked={sourcesLocked}
            compact={wrap}
          />
        </motion.li>
      ))}
    </motion.ul>
  );
}

function PeerNode({
  peer,
  dimmed,
  onHover,
  quote,
  sourcesLocked,
  compact,
}: {
  readonly peer: Peer;
  readonly dimmed: boolean;
  readonly onHover: (key: string | null) => void;
  readonly quote: QuoteLite | undefined;
  readonly sourcesLocked: boolean;
  readonly compact: boolean;
}): ReactNode {
  const t = useTranslations("stock.industry");
  const tLock = useTranslations("featureLock");
  const change = quote?.changePct ?? null;
  const roleLabel = t(`supplyChainRole.${peer.role}`);
  const gradeLabel = t(`supplyChainGrade.${peer.grade}`);
  const key = peerKey(peer);
  const hoverProps = {
    onMouseEnter: () => onHover(key),
    onMouseLeave: () => onHover(null),
    onFocus: () => onHover(key),
    onBlur: () => onHover(null),
  };
  return (
    <div
      className={cn(
        "group flex w-full max-w-[300px] items-stretch rounded-[var(--radius-input)] border border-[var(--border)]",
        "bg-[var(--surface-2)] transition-[background-color,border-color,opacity]",
        "duration-[var(--dur-base)] ease-[var(--ease-out)] hover:border-[var(--border-strong)] hover:bg-[var(--surface-3)]",
        dimmed && "opacity-65",
        compact && "w-auto",
      )}
      style={{ boxShadow: `inset 3px 0 0 ${ROLE_COLOR[peer.role]}` }}
    >
      <Link
        {...hoverProps}
        href={`/stock/${encodeURIComponent(peer.symbol)}?market=${peer.market}`}
        aria-label={`${peer.name} ${peer.symbol} - ${roleLabel}${change === null ? "" : ` ${formatChange(change)}`}`}
        className="flex min-w-0 flex-1 items-center gap-2.5 rounded-[var(--radius-input)] py-1.5 pr-2 pl-3"
      >
        <span className="min-w-0 flex-1">
          <span className="block truncate font-medium text-[13px] text-[var(--text)] leading-tight">
            {peer.name}
          </span>
          <span className="block truncate font-num text-[11px] text-[var(--text-muted)] leading-tight">
            {peer.symbol}
            {peer.market === "US" ? " · US" : ""}
          </span>
          {peer.product === null ? null : (
            <span className="mt-0.5 block truncate text-[11px] text-[var(--text)] leading-tight">
              {peer.product}
            </span>
          )}
          <span className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-0.5">
            <span className="inline-flex items-center gap-1 rounded-full border border-[var(--border)] px-1.5 text-[10px] text-[var(--text-muted)] leading-4">
              <span
                aria-hidden
                className="size-1.5 rounded-full"
                style={{ backgroundColor: GRADE_COLOR[peer.grade] }}
              />
              {gradeLabel}
            </span>
            {peer.asOf === null ? null : (
              <span className="font-num text-[10px] text-[var(--text-muted)] leading-4">
                {t("supplyChainAsOf", { date: peer.asOf })}
              </span>
            )}
          </span>
        </span>
        {change === null ? null : <ChangeBadge value={change} />}
      </Link>
      {peer.sources.length > 0 ? <SourcesPopover peer={peer} hoverProps={hoverProps} /> : null}
      {peer.sources.length === 0 && sourcesLocked ? (
        <FeatureLock
          variant="icon"
          message={tLock("supplyChain.sourcesLocked")}
          className="rounded-r-[var(--radius-input)] rounded-l-none border-[var(--border)] border-l"
        />
      ) : null}
    </div>
  );
}

/** The citations behind one relationship: a button that opens a small list of publisher links.
 * Radix gives it focus management and Esc to close; each link opens in a new tab and says so to a
 * screen reader. Only http(s) sources reach here (see groupPeers). */
function SourcesPopover({
  peer,
  hoverProps,
}: {
  readonly peer: Peer;
  readonly hoverProps: HoverProps;
}): ReactNode {
  const t = useTranslations("stock.industry");
  return (
    <Popover.Root>
      <Popover.Trigger asChild>
        <button
          {...hoverProps}
          type="button"
          aria-label={t("supplyChainSourcesAria", { name: peer.name, count: peer.sources.length })}
          title={t("supplyChainSources")}
          className="flex min-h-11 min-w-11 shrink-0 flex-col items-center justify-center gap-0.5 rounded-r-[var(--radius-input)] border-[var(--border)] border-l text-[var(--text-muted)] hover:bg-[var(--surface-3)] hover:text-[var(--text)]"
        >
          <BooksIcon size={14} weight="regular" aria-hidden />
          <span className="font-num text-[10px] leading-none">{peer.sources.length}</span>
        </button>
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Content
          sideOffset={6}
          align="end"
          collisionPadding={8}
          aria-label={t("supplyChainSourcesTitle", { name: peer.name })}
          className="z-50 w-[min(320px,calc(100vw-16px))] rounded-[var(--radius-card)] border border-[var(--border)] bg-[var(--surface)] p-1.5 shadow-lg"
        >
          <p className="px-2.5 py-1.5 text-[11px] text-[var(--text-muted)]">
            {t("supplyChainSourcesTitle", { name: peer.name })}
          </p>
          <ul className="m-0 list-none p-0">
            {peer.sources.map((source) => (
              <li key={source.url}>
                <a
                  href={safeHttpUrl(source.url) ?? undefined}
                  target="_blank"
                  rel="noopener noreferrer"
                  aria-label={t("supplyChainSourceLinkAria", { publisher: source.publisher })}
                  className="flex min-h-11 items-center gap-2 rounded-[var(--radius-input)] px-2.5 text-[13px] text-[var(--accent-text)] hover:bg-[var(--surface-2)]"
                >
                  <span className="min-w-0 flex-1 truncate">{source.publisher}</span>
                  {source.published_on ? (
                    <span className="shrink-0 font-num text-[11px] text-[var(--text-muted)]">
                      {source.published_on}
                    </span>
                  ) : null}
                  <ArrowSquareOutIcon size={14} weight="regular" aria-hidden className="shrink-0" />
                </a>
              </li>
            ))}
          </ul>
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}

function FocusNode({
  name,
  symbol,
  quote,
  inline = false,
}: {
  readonly name: string;
  readonly symbol: string;
  readonly quote: QuoteLite | undefined;
  readonly inline?: boolean;
}): ReactNode {
  const change = quote?.changePct ?? null;
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center gap-0.5 rounded-[var(--radius-card)] border-2",
        "border-[var(--accent)] bg-[var(--surface-2)] px-3 py-3 text-center",
        inline ? "w-full" : "absolute top-1/2 right-3 left-3 -translate-y-1/2",
      )}
    >
      <span className="truncate font-semibold text-[15px] text-[var(--text)]">{name}</span>
      <span className="font-num text-[11px] text-[var(--text-muted)]">{symbol}</span>
      {change === null ? null : <ChangeBadge value={change} />}
    </div>
  );
}

/** Price move with the required ▲/▼ and sign, in the market colours. */
function ChangeBadge({ value }: { readonly value: number }): ReactNode {
  return (
    <SignedChange
      value={value}
      decimals={2}
      unit="%"
      spaced={false}
      labelled={false}
      className="shrink-0 text-[11px]"
    />
  );
}

/** Curved connectors from each supplier into the focus node and from it out to each customer, drawn
 * as one overlay across the three columns (md and up). The SVG's x runs 0-100 across the row and
 * its y runs in pixels, so a row's centre is simply `(index + 0.5) * height / count`. The columns
 * are 36/28/36 wide, so a supplier's inner edge is near x=34.5 and the focus node's near 37.5.
 * Strength shows as stroke width when the edge carries a `revenue_pct`. */
function Connectors({
  height,
  left,
  right,
  hovered,
}: {
  readonly height: number;
  readonly left: readonly Peer[];
  readonly right: readonly Peer[];
  readonly hovered: string | null;
}): ReactNode {
  const mid = height / 2;
  const strengthOf = (peer: Peer): number =>
    peer.revenuePct === null ? 1.4 : Math.min(3.4, 1.4 + peer.revenuePct / 25);
  return (
    // biome-ignore lint/a11y/noSvgWithoutTitle: purely decorative connector overlay (aria-hidden)
    <svg
      aria-hidden
      className="pointer-events-none absolute inset-0 hidden h-full w-full overflow-visible md:block"
      viewBox={`0 0 100 ${height}`}
      preserveAspectRatio="none"
    >
      {left.map((peer, i) => {
        const y = ((i + 0.5) * height) / left.length;
        return (
          <ConnectorPath
            key={`l-${peerKey(peer)}`}
            d={`M34.5 ${y} C 36 ${y}, 36 ${mid}, 37.5 ${mid}`}
            peer={peer}
            hovered={hovered}
            width={strengthOf(peer)}
          />
        );
      })}
      {right.map((peer, i) => {
        const y = ((i + 0.5) * height) / right.length;
        return (
          <ConnectorPath
            key={`r-${peerKey(peer)}`}
            d={`M62.5 ${mid} C 64 ${mid}, 64 ${y}, 65.5 ${y}`}
            peer={peer}
            hovered={hovered}
            width={strengthOf(peer)}
          />
        );
      })}
    </svg>
  );
}

function ConnectorPath({
  d,
  peer,
  hovered,
  width,
}: {
  readonly d: string;
  readonly peer: Peer;
  readonly hovered: string | null;
  readonly width: number;
}): ReactNode {
  const active = hovered === peerKey(peer);
  return (
    <path
      d={d}
      fill="none"
      stroke={ROLE_COLOR[peer.role]}
      strokeWidth={active ? width + 0.8 : width}
      strokeOpacity={connectorOpacity(hovered, active)}
      strokeLinecap="round"
      vectorEffect="non-scaling-stroke"
      style={{ transition: "stroke-opacity var(--dur-base) var(--ease-out)" }}
    />
  );
}

function focusNameOf(edges: readonly SupplyChainEdge[], symbol: string): string {
  const asFrom = edges.find((e) => e.from_symbol === symbol)?.from_name;
  const asTo = edges.find((e) => e.to_symbol === symbol)?.to_name;
  return asFrom ?? asTo ?? symbol;
}

function formatChange(value: number): string {
  return `${value > 0 ? "+" : ""}${value.toFixed(2)}%`;
}

function connectorOpacity(hovered: string | null, active: boolean): number {
  if (hovered === null) return 0.5;
  return active ? 0.95 : 0.15;
}
