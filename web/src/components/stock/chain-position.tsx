"use client";

import * as Popover from "@radix-ui/react-popover";
import { useQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { type ReactNode, useState } from "react";
import { FeatureLock } from "@/components/ui/feature-lock";
import { QueryFailure } from "@/components/ui/query-state";
import { Skeleton } from "@/components/ui/skeleton";
import { queryKeys } from "@/lib/api/query-keys";
import { getChainPositions } from "@/lib/api/stock-detail";
import { cn } from "@/lib/cn";
import type { Market } from "@/types/settings";
import type { ChainPosition, ChainPositionTier } from "@/types/stock/chain-position";

/** Reason codes the backend can send today. Anything else gets the generic "other basis" label,
 * so a newer backend never crashes or blanks an older page. */
const KNOWN_REASONS = [
  "override",
  "prior_curated",
  "prior_keyword",
  "graph_layering",
  "graph_propagation",
  "agree_prior_graph",
  "conflict_graph_over_prior",
  "conflict_prior_kept",
] as const;
type KnownReason = (typeof KNOWN_REASONS)[number];

function isKnownReason(code: string): code is KnownReason {
  return (KNOWN_REASONS as readonly string[]).includes(code);
}

/** Categorical tokens only - a chain position is a label, never a price move (no --up/--down). */
const TIER_COLOR: Record<ChainPositionTier, string> = {
  upstream: "var(--chart-1)",
  midstream: "var(--chart-3)",
  downstream: "var(--chart-4)",
};

type ConfidenceLevel = "high" | "medium" | "low";

function confidenceLevel(confidence: number): ConfidenceLevel {
  if (confidence >= 0.75) return "high";
  if (confidence >= 0.5) return "medium";
  return "low";
}

/** 推估: a keyword-only prior, or anything below 0.5 confidence, is a guess and says so. */
function isEstimated(position: ChainPosition): boolean {
  if (position.method !== "prior") return false;
  return position.confidence < 0.5 || position.reasons.some((r) => r.code === "prior_keyword");
}

/** 個股 › 產業分析's "where does this company sit in its chain" strip: the primary chain's
 * 上/中/下游 badge, the other chains it belongs to, and a 為什麼 popover with the reasons. An empty
 * result is shown as 尚未分類 - never a guessed position. */
export function ChainPositionBar({
  symbol,
  market,
}: {
  readonly symbol: string;
  readonly market: Market;
}): ReactNode {
  const t = useTranslations("stock.industry.chainPosition");
  const tLock = useTranslations("featureLock");
  const query = useQuery({
    queryKey: queryKeys.symbolRead("chain_positions", symbol, market),
    queryFn: () => getChainPositions(symbol, market),
  });
  const [selectedKey, setSelectedKey] = useState<string | null>(null);

  if (query.isLoading) {
    return (
      <section className="surface-card p-4">
        <Skeleton className="h-11 w-full" />
      </section>
    );
  }
  if (query.isError) {
    return <QueryFailure query={query} />;
  }
  const positions = query.data?.positions ?? [];
  const primary = positions.find((p) => p.is_primary) ?? positions[0];
  const active = positions.find((p) => p.chain_key === selectedKey) ?? primary;
  const others = positions.filter((p) => p !== active);
  const data = query.data;
  const locked = data?.locked ?? false;
  const hiddenChains = locked ? (data?.hidden_count ?? 0) : 0;

  return (
    <section aria-label={t("title")} className="surface-card @container p-4">
      <div className="flex flex-col gap-3 @md:flex-row @md:flex-wrap @md:items-center">
        <h2 className="font-semibold text-[13px] text-[var(--text-muted)]">{t("title")}</h2>
        {active ? (
          <div className="flex flex-wrap items-center gap-2">
            <PositionBadge position={active} />
            {locked ? (
              <FeatureLock message={tLock("chainPosition.why")} variant="chip" />
            ) : (
              <WhyPopover position={active} />
            )}
          </div>
        ) : (
          <p className="text-[13px] text-[var(--text-muted)]">{t("unclassified")}</p>
        )}
      </div>
      {hiddenChains > 0 ? (
        <div className="mt-3">
          <FeatureLock
            message={tLock("chainPosition.moreChains", { count: hiddenChains })}
            variant="chip"
          />
        </div>
      ) : null}
      {others.length > 0 ? (
        <div className="mt-3 flex flex-col gap-2 @md:flex-row @md:flex-wrap @md:items-center">
          <p className="text-[12px] text-[var(--text-muted)]">{t("otherChains")}</p>
          <ul className="m-0 flex list-none flex-wrap gap-2 p-0">
            {others.map((p) => (
              <li key={p.chain_key}>
                <OtherChainButton position={p} onSelect={() => setSelectedKey(p.chain_key)} />
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </section>
  );
}

function PositionBadge({ position }: { readonly position: ChainPosition }): ReactNode {
  const t = useTranslations("stock.industry");
  const estimated = isEstimated(position);
  const label = t(`tier.${position.position}` as const);
  return (
    <span className="flex flex-wrap items-center gap-1.5">
      <span
        role="img"
        aria-label={t("chainPosition.badgeAria", { name: position.chain_name, position: label })}
        title={estimated ? t("chainPosition.estimatedHint") : undefined}
        data-estimated={estimated ? "true" : "false"}
        className={cn(
          "inline-flex min-h-8 items-center gap-1.5 rounded-[var(--radius-pill)] border px-3 text-[13px]",
          estimated
            ? "border-[var(--border-strong)] border-dashed text-[var(--text-muted)]"
            : "border-[var(--border)] border-solid font-semibold text-[var(--text)]",
        )}
      >
        <span
          aria-hidden
          className={cn("size-2 shrink-0 rounded-full", estimated && "opacity-60")}
          style={{ backgroundColor: TIER_COLOR[position.position] }}
        />
        <span aria-hidden>
          {position.chain_name} · {label}
        </span>
      </span>
      {estimated ? (
        <span className="text-[12px] text-[var(--text-muted)]">{t("chainPosition.estimated")}</span>
      ) : null}
    </span>
  );
}

function OtherChainButton({
  position,
  onSelect,
}: {
  readonly position: ChainPosition;
  readonly onSelect: () => void;
}): ReactNode {
  const t = useTranslations("stock.industry");
  const label = t(`tier.${position.position}` as const);
  const estimated = isEstimated(position);
  return (
    <button
      type="button"
      onClick={onSelect}
      aria-label={t("chainPosition.switchAria", { name: position.chain_name, position: label })}
      className={cn(
        "inline-flex min-h-11 items-center gap-1.5 rounded-[var(--radius-pill)] border border-[var(--border)] px-3 text-[12.5px]",
        "hover:bg-[var(--surface-2)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-[var(--accent)]",
        estimated ? "border-dashed text-[var(--text-muted)]" : "text-[var(--text)]",
      )}
    >
      <span
        aria-hidden
        className="size-2 shrink-0 rounded-full"
        style={{ backgroundColor: TIER_COLOR[position.position] }}
      />
      <span aria-hidden>
        {position.chain_name} · {label}
      </span>
    </button>
  );
}

/** The reasons behind one position. Radix gives it focus management and Esc to close, the same
 * way the supply-chain sources popover works. */
function WhyPopover({ position }: { readonly position: ChainPosition }): ReactNode {
  const t = useTranslations("stock.industry");
  const label = t(`tier.${position.position}` as const);
  const level = t(`chainPosition.confidence.${confidenceLevel(position.confidence)}` as const);
  return (
    <Popover.Root>
      <Popover.Trigger asChild>
        <button
          type="button"
          aria-label={t("chainPosition.whyAria", { name: position.chain_name, position: label })}
          className="inline-flex min-h-11 min-w-11 items-center justify-center rounded-[var(--radius-input)] px-2 text-[12.5px] text-[var(--accent-text)] hover:bg-[var(--surface-2)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-[var(--accent)]"
        >
          {t("chainPosition.why")}
        </button>
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Content
          sideOffset={6}
          align="start"
          collisionPadding={8}
          aria-label={t("chainPosition.whyTitle")}
          className="z-50 w-[min(340px,calc(100vw-16px))] rounded-[var(--radius-card)] border border-[var(--border)] bg-[var(--surface)] p-3 shadow-lg"
        >
          <p className="mb-2 text-[11px] text-[var(--text-muted)]">{t("chainPosition.whyTitle")}</p>
          <ul className="m-0 flex list-none flex-col gap-1.5 p-0">
            {position.reasons.map((reason) => (
              <li
                key={`${reason.code}:${reason.detail ?? ""}`}
                className="text-[13px] text-[var(--text)] leading-snug"
              >
                {isKnownReason(reason.code)
                  ? t(`chainPosition.reason.${reason.code}` as const)
                  : t("chainPosition.reason.unknown")}
              </li>
            ))}
          </ul>
          <p className="mt-2 border-[var(--border)] border-t pt-2 text-[12px] text-[var(--text-muted)]">
            {t("chainPosition.confidenceLabel", { level })}
          </p>
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}
