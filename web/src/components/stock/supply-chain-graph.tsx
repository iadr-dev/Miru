"use client";

import { ArrowRightIcon } from "@phosphor-icons/react";
import { useQuery } from "@tanstack/react-query";
import { type MotionStyle, motion, useReducedMotion } from "motion/react";
import { useTranslations } from "next-intl";
import { type ReactNode, useMemo, useState } from "react";
import { CardSkeletonBody } from "@/components/market/card-skeleton";
import { Card, CardHeader } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { SignedChange } from "@/components/ui/signed-change";
import { useSymbolQuotes } from "@/hooks/use-symbol-quotes";
import { Link } from "@/i18n/navigation";
import { getSupplyChainEdges } from "@/lib/api/stock-detail";
import { cn } from "@/lib/cn";
import { entrance, staggerChildren } from "@/lib/motion";
import { groupPeers, type Peer, type PeerRole } from "@/lib/supply-chain";
import type { Market } from "@/types/settings";
import type { SupplyChainEdge } from "@/types/stock/profile";

/** Relationship colours are categorical (the non-signed chart tokens) - never --up/--down, which
 * mean a price move and nothing else (web/OVERVIEW.md §4). */
const ROLE_COLOR: Record<PeerRole, string> = {
  supplier: "var(--chart-1)",
  customer: "var(--chart-2)",
  competitor: "var(--chart-6)",
};

/** Peers shown per side before "show all". Keeps the graph legible for a hub like TSMC. */
const COLLAPSED_COUNT = 6;
/** Height of one peer row in the diagram, px. The connector SVG shares this unit. */
const ROW_PX = 60;

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
  const query = useQuery({
    queryKey: ["supply_chain_edges", symbol, market],
    queryFn: () => getSupplyChainEdges(symbol, market),
  });
  const [hovered, setHovered] = useState<string | null>(null);
  const [expanded, setExpanded] = useState(false);

  const edges = query.data?.edges;
  const groups = useMemo(() => groupPeers(edges ?? [], symbol), [edges, symbol]);

  // Peers can sit in either market (TSMC supplies Apple), and the quote lookup is per market.
  const allPeers = [...groups.supplier, ...groups.customer, ...groups.competitor];
  const twCodes = [symbol, ...allPeers.map((p) => p.symbol).filter(isTaiwanCode)];
  const usCodes = allPeers.map((p) => p.symbol).filter((code) => !isTaiwanCode(code));
  const twQuotes = useSymbolQuotes([...new Set(twCodes)], "TW");
  const usQuotes = useSymbolQuotes([...new Set(usCodes)], "US");
  const quoteOf = (code: string): QuoteLite | undefined =>
    [...twQuotes.quotes, ...usQuotes.quotes].find((q) => q.code === code);

  if (query.isLoading) {
    return (
      <Card as="section">
        <CardSkeletonBody height={320} />
      </Card>
    );
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
  const focusQuote = quoteOf(symbol);

  return (
    <Card as="section" className="overflow-hidden">
      <CardHeader title={t("supplyChainTitle")} description={t("supplyChainHint")} />
      <p className="sr-only">
        {t("supplyChainGraphAria", { name: focusName, count: data.edge_count })}
      </p>

      <div className="mx-auto w-full max-w-[980px]">
        <ColumnHeadings focusName={focusName} />

        <div className="relative">
          <Connectors
            height={height}
            left={suppliers}
            right={customers}
            edges={data.edges}
            symbol={symbol}
            hovered={hovered}
          />
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
              market={market}
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
              market={market}
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
            market={market}
            wrap
          />
        </div>
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
  market,
}: {
  readonly side: "left" | "right";
  readonly kind: PeerRole;
  readonly peers: readonly Peer[];
  readonly height: number;
  readonly emptyLabel: string;
  readonly hovered: string | null;
  readonly onHover: (symbol: string | null) => void;
  readonly quoteOf: (code: string) => QuoteLite | undefined;
  readonly market: Market;
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
          market={market}
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
  market,
  height,
  side,
  wrap = false,
}: {
  readonly peers: readonly Peer[];
  readonly kind: PeerRole;
  readonly hovered: string | null;
  readonly onHover: (symbol: string | null) => void;
  readonly quoteOf: (code: string) => QuoteLite | undefined;
  readonly market: Market;
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
          key={`${role}:${peer.symbol}`}
          variants={entrance(reduceMotion)}
          className={cn(
            "flex items-center",
            wrap ? "" : "min-h-12",
            side === "left" && "md:justify-end",
          )}
        >
          <PeerNode
            peer={peer}
            dimmed={hovered !== null && hovered !== peer.symbol}
            onHover={onHover}
            quote={quoteOf(peer.symbol)}
            market={market}
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
  market,
  compact,
}: {
  readonly peer: Peer;
  readonly dimmed: boolean;
  readonly onHover: (symbol: string | null) => void;
  readonly quote: QuoteLite | undefined;
  readonly market: Market;
  readonly compact: boolean;
}): ReactNode {
  const t = useTranslations("stock.industry");
  const peerMarket: Market = isTaiwanCode(peer.symbol) ? "TW" : "US";
  const change = quote?.changePct ?? null;
  const roleLabel = t(`supplyChainRole.${peer.role}`);
  return (
    <Link
      href={`/stock/${encodeURIComponent(peer.symbol)}?market=${peerMarket === market ? market : peerMarket}`}
      onMouseEnter={() => onHover(peer.symbol)}
      onMouseLeave={() => onHover(null)}
      onFocus={() => onHover(peer.symbol)}
      onBlur={() => onHover(null)}
      aria-label={`${peer.name} ${peer.symbol} - ${roleLabel}${change === null ? "" : ` ${formatChange(change)}`}`}
      className={cn(
        "group flex w-full max-w-[300px] items-center gap-2.5 rounded-[var(--radius-input)] border border-[var(--border)]",
        "bg-[var(--surface-2)] py-1.5 pr-2.5 pl-3 transition-[background-color,border-color,opacity]",
        "duration-[var(--dur-base)] ease-[var(--ease-out)] hover:border-[var(--border-strong)] hover:bg-[var(--surface-3)]",
        dimmed && "opacity-65",
        compact && "w-auto",
      )}
      style={{ boxShadow: `inset 3px 0 0 ${ROLE_COLOR[peer.role]}` }}
    >
      <span className="min-w-0 flex-1">
        <span className="block truncate font-medium text-[13px] text-[var(--text)] leading-tight">
          {peer.name}
        </span>
        <span className="block font-num text-[11px] text-[var(--text-muted)] leading-tight">
          {peer.symbol}
        </span>
      </span>
      {change === null ? null : <ChangeBadge value={change} />}
    </Link>
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
  edges,
  symbol,
  hovered,
}: {
  readonly height: number;
  readonly left: readonly Peer[];
  readonly right: readonly Peer[];
  readonly edges: readonly SupplyChainEdge[];
  readonly symbol: string;
  readonly hovered: string | null;
}): ReactNode {
  const mid = height / 2;
  const strengthOf = (peer: Peer): number => {
    const edge = edges.find(
      (e) =>
        (e.from_symbol === symbol && e.to_symbol === peer.symbol) ||
        (e.to_symbol === symbol && e.from_symbol === peer.symbol),
    );
    const pct = edge?.revenue_pct ?? null;
    return pct === null ? 1.4 : Math.min(3.4, 1.4 + pct / 25);
  };
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
            key={`l-${peer.symbol}`}
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
            key={`r-${peer.symbol}`}
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
  const active = hovered === peer.symbol;
  return (
    <path
      d={d}
      fill="none"
      stroke={ROLE_COLOR[peer.role]}
      strokeWidth={active ? width + 0.8 : width}
      strokeOpacity={hovered === null ? 0.5 : active ? 0.95 : 0.15}
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

function isTaiwanCode(code: string): boolean {
  return /^\d/.test(code);
}

function formatChange(value: number): string {
  return `${value > 0 ? "+" : ""}${value.toFixed(2)}%`;
}
