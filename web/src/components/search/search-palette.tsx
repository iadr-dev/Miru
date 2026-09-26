"use client";

import { MagnifyingGlassIcon } from "@phosphor-icons/react";
import { Command } from "cmdk";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { useTranslations } from "next-intl";
import { type ReactNode, useCallback, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useDebouncedValue } from "@/hooks/use-debounced-value";
import { useSymbolSearch } from "@/hooks/use-symbol-search";
import { useRouter } from "@/i18n/navigation";
import { cn } from "@/lib/cn";
import { useFocusTrap } from "@/lib/use-focus-trap";
import type { SearchHit } from "@/types/symbol-search";

const DEBOUNCE_MS = 250;

type PaletteMotionProps = {
  readonly fadeTransition: { duration: number };
  readonly panelTransition:
    | { duration: number }
    | { type: "spring"; stiffness: number; damping: number };
  readonly panelMotionProps:
    | { initial: false; animate: { opacity: number }; exit: { opacity: number } }
    | {
        initial: { opacity: number; y: number; scale: number };
        animate: { opacity: number; y: number; scale: number };
        exit: { opacity: number; y: number; scale: number };
      };
};

function usePaletteMotion(reduceMotion: boolean | null): PaletteMotionProps {
  if (reduceMotion) {
    return {
      fadeTransition: { duration: 0 },
      panelTransition: { duration: 0 },
      panelMotionProps: { initial: false, animate: { opacity: 1 }, exit: { opacity: 0 } },
    };
  }
  return {
    fadeTransition: { duration: 0.15 },
    panelTransition: { type: "spring", stiffness: 260, damping: 26 },
    panelMotionProps: {
      initial: { opacity: 0, y: -8, scale: 0.98 },
      animate: { opacity: 1, y: 0, scale: 1 },
      exit: { opacity: 0, y: -8, scale: 0.98 },
    },
  };
}

/** The two real, distinct queries (TW roster scan / US yfinance search) normalized and
 * regrouped into 公司/ETF, plus the derived loading/empty/error flags the palette body reads.
 * Pulled out of `SearchPalette` itself to keep that component's own cognitive load down to
 * "render the shell" — this hook owns "what do we show". */
function useSearchPaletteResults(debouncedQuery: string): {
  companies: readonly SearchHit[];
  etfs: readonly SearchHit[];
  isLoading: boolean;
  isEmpty: boolean;
  bothFailed: boolean;
  retry: () => void;
} {
  const twQuery = useSymbolSearch(debouncedQuery, "TW");
  const usQuery = useSymbolSearch(debouncedQuery, "US");

  const allHits = [...(twQuery.data ?? []), ...(usQuery.data ?? [])];
  const isLoading = debouncedQuery.length > 0 && (twQuery.isPending || usQuery.isPending);
  const bothFailed = twQuery.isError && usQuery.isError;
  const isEmpty = debouncedQuery.length > 0 && !isLoading && !bothFailed && allHits.length === 0;

  return {
    companies: allHits.filter((hit) => hit.kind === "stock"),
    etfs: allHits.filter((hit) => hit.kind === "etf"),
    isLoading,
    isEmpty,
    bothFailed,
    retry: () => {
      void twQuery.refetch();
      void usQuery.refetch();
    },
  };
}

/**
 * Full-screen 公司/ETF search command palette (Cmd/Ctrl+K or the top-bar search button). Two
 * real, distinct queries run in parallel — TW (ingested roster scan) and US (live yfinance
 * search) — normalized into one `SearchHit[]` and regrouped client-side into 公司/ETF sections,
 * matching the reference layout's grouped-with-count-badge sections. Neither market's failure
 * blanks the other's results (web/CLAUDE.md §6).
 */
export function SearchPalette({
  open,
  onClose,
}: Readonly<{
  open: boolean;
  onClose: () => void;
}>): ReactNode {
  const t = useTranslations("shell.search");
  const router = useRouter();
  const dialogRef = useRef<HTMLDivElement>(null);
  const [query, setQuery] = useState("");
  const debouncedQuery = useDebouncedValue(query, DEBOUNCE_MS);
  const motionProps = usePaletteMotion(useReducedMotion());

  const close = useCallback(() => {
    onClose();
    setQuery("");
  }, [onClose]);

  useFocusTrap(dialogRef, open, close);

  const { companies, etfs, isLoading, isEmpty, bothFailed, retry } =
    useSearchPaletteResults(debouncedQuery);

  const select = (hit: SearchHit): void => {
    close();
    router.push(`/stock/${encodeURIComponent(hit.symbol)}?market=${hit.market}`);
  };

  const overlay = (
    <AnimatePresence>
      {open ? (
        <motion.div
          className="fixed inset-0 z-50 flex items-start justify-center px-4 pt-[12vh]"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={motionProps.fadeTransition}
        >
          <button
            type="button"
            aria-label={t("label")}
            className="absolute inset-0 bg-black/60 backdrop-blur-sm"
            onClick={close}
            tabIndex={-1}
          />
          <motion.div
            ref={dialogRef}
            role="dialog"
            aria-modal="true"
            aria-label={t("label")}
            className={cn(
              "relative flex w-full max-w-lg flex-col overflow-hidden rounded-[var(--radius-card)]",
              "border border-[var(--border)] bg-[var(--surface)] shadow-2xl",
            )}
            {...motionProps.panelMotionProps}
            transition={motionProps.panelTransition}
          >
            <Command shouldFilter={false} loop label={t("label")}>
              <div className="flex items-center gap-2 border-[var(--border)] border-b px-4">
                <MagnifyingGlassIcon
                  size={18}
                  weight="regular"
                  className="shrink-0 text-[var(--text-muted)]"
                  aria-hidden
                />
                <Command.Input
                  autoFocus
                  value={query}
                  onValueChange={setQuery}
                  placeholder={t("inputPlaceholder")}
                  className={cn(
                    "min-h-14 w-full bg-transparent text-[15px] text-[var(--text)]",
                    "!outline-none placeholder:text-[var(--text-muted)]",
                  )}
                />
              </div>
              <Command.List className="max-h-[60vh] overflow-y-auto p-2">
                {isLoading ? <SearchPaletteLoadingRows label={t("loading")} /> : null}
                {isEmpty ? (
                  <p className="px-3 py-8 text-center text-[13px] text-[var(--text-muted)]">
                    {t("empty")}
                  </p>
                ) : null}
                {!isLoading && bothFailed ? (
                  <SearchPaletteErrorState
                    message={t("error")}
                    retryLabel={t("retry")}
                    onRetry={retry}
                  />
                ) : null}
                {!isLoading ? (
                  <>
                    <ResultsGroup label={t("groupCompany")} hits={companies} onSelect={select} />
                    <ResultsGroup label={t("groupEtf")} hits={etfs} onSelect={select} />
                  </>
                ) : null}
              </Command.List>
            </Command>
          </motion.div>
        </motion.div>
      ) : null}
    </AnimatePresence>
  );

  if (typeof document === "undefined") {
    return null;
  }
  return createPortal(overlay, document.body);
}

function SearchPaletteErrorState({
  message,
  retryLabel,
  onRetry,
}: Readonly<{
  message: string;
  retryLabel: string;
  onRetry: () => void;
}>): ReactNode {
  return (
    <div className="flex flex-col items-center gap-2 px-3 py-8 text-center">
      <p className="text-[13px] text-[var(--text-muted)]">{message}</p>
      <button
        type="button"
        onClick={onRetry}
        className="text-[13px] text-[var(--accent)] hover:underline"
      >
        {retryLabel}
      </button>
    </div>
  );
}

/** One 公司/ETF section: heading + count badge + rows. Shared by both groups so the
 * grouped-with-count-badge layout exists in exactly one place, not duplicated per kind. Renders
 * nothing when `hits` is empty — the caller doesn't need its own length check. */
function ResultsGroup({
  label,
  hits,
  onSelect,
}: Readonly<{
  label: string;
  hits: readonly SearchHit[];
  onSelect: (hit: SearchHit) => void;
}>): ReactNode {
  if (hits.length === 0) {
    return null;
  }
  return (
    <Command.Group heading={<GroupHeading label={label} count={hits.length} />}>
      {hits.map((hit) => (
        <SearchHitRow key={`${hit.market}:${hit.symbol}`} hit={hit} onSelect={onSelect} />
      ))}
    </Command.Group>
  );
}

function GroupHeading({
  label,
  count,
}: Readonly<{
  label: string;
  count: number;
}>): ReactNode {
  return (
    <div className="flex items-center justify-between px-2 py-1.5">
      <span className="font-medium text-[12px] text-[var(--text-muted)] uppercase tracking-wide">
        {label}
      </span>
      <span
        className={cn(
          "min-w-5 rounded-[var(--radius-pill)] bg-[var(--surface-2)] px-1.5 py-0.5 text-center",
          "font-num text-[11px] text-[var(--text-muted)] tabular-nums",
        )}
      >
        {count}
      </span>
    </div>
  );
}

function SearchHitRow({
  hit,
  onSelect,
}: Readonly<{
  hit: SearchHit;
  onSelect: (hit: SearchHit) => void;
}>): ReactNode {
  return (
    <Command.Item
      value={`${hit.market}:${hit.symbol}:${hit.name}`}
      onSelect={() => onSelect(hit)}
      className={cn(
        "flex min-h-11 cursor-pointer items-center gap-3 rounded-[8px] px-2 text-[var(--text)]",
        "data-[selected=true]:bg-[var(--surface-2)]",
      )}
    >
      <span className="font-num text-[13px] text-[var(--text-muted)] tabular-nums">
        {hit.symbol}
      </span>
      <span className="flex-1 truncate text-[14px]">{hit.name}</span>
      {hit.secondaryLabel ? (
        <span className="shrink-0 truncate text-[12px] text-[var(--text-muted)]">
          {hit.secondaryLabel}
        </span>
      ) : null}
    </Command.Item>
  );
}

function SearchPaletteLoadingRows({ label }: Readonly<{ label: string }>): ReactNode {
  return (
    <div className="flex flex-col gap-1 p-1" aria-live="off" role="status" aria-label={label}>
      {[0, 1, 2, 3].map((i) => (
        <div
          key={i}
          className="h-11 animate-pulse rounded-[8px] bg-[var(--surface-2)]"
          style={{ opacity: 1 - i * 0.15 }}
        />
      ))}
    </div>
  );
}
