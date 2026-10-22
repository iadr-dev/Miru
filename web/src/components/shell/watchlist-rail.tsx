"use client";

import {
  DndContext,
  type DragEndEvent,
  KeyboardSensor,
  PointerSensor,
  useSensor,
  useSensors,
} from "@dnd-kit/core";
import {
  arrayMove,
  SortableContext,
  sortableKeyboardCoordinates,
  useSortable,
  verticalListSortingStrategy,
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import {
  BellSimpleIcon,
  CaretDownIcon,
  CaretRightIcon,
  ChartLineIcon,
  CheckIcon,
  DotsSixVerticalIcon,
  PaletteIcon,
  PlusIcon,
  ProhibitIcon,
  StarIcon,
  TrashIcon,
  XIcon,
} from "@phosphor-icons/react";
import * as AlertDialog from "@radix-ui/react-alert-dialog";
import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
import { useReducedMotion } from "motion/react";
import { useLocale, useTranslations } from "next-intl";
import { type ReactNode, useState } from "react";
import { Sparkline } from "@/components/market/sparkline";
import { SymbolLink } from "@/components/market/symbol-link";
import { ResizableRailHandle } from "@/components/shell/resizable-rail-handle";
import { Skeleton } from "@/components/ui/skeleton";
import { type SymbolQuote, useSymbolQuotes } from "@/hooks/use-symbol-quotes";
import {
  useCreateWatchlist,
  useDeleteWatchlist,
  useMoveWatchlistItem,
  useRemoveWatchlistItem,
  useRenameWatchlist,
  useReorderWatchlistGroup,
  useSetWatchlistColor,
  useWatchlists,
} from "@/hooks/use-watchlists";
import { useRouter } from "@/i18n/navigation";
import { cn } from "@/lib/cn";
import { formatNumber } from "@/lib/format-number";
import { directionColor, formatSignedChange } from "@/lib/signed";
import { compareHref } from "@/lib/stock-compare";
import { groupNameStyle } from "@/lib/watchlist-color";
import type { Market } from "@/types/settings";
import {
  WATCHLIST_COLORS,
  type WatchlistAssetClass,
  type WatchlistColor,
  type WatchlistGroupView,
  type WatchlistItem,
} from "@/types/watchlist";

type WatchlistTab = "tw" | "us" | "etf";
const TABS: readonly WatchlistTab[] = ["tw", "us", "etf"];

// TW=`(TW,stock)` · ETF=`(TW,etf)` · US=`(US,*)` — per ADR-0027, the rail's 3 tabs are NOT a
// 3-value backend enum, they're a client-side mapping over the real `market`×`asset_class` pair.
const TAB_SCOPE: Record<WatchlistTab, { market: Market; assetClass: WatchlistAssetClass }> = {
  tw: { market: "TW", assetClass: "stock" },
  us: { market: "US", assetClass: "stock" },
  etf: { market: "TW", assetClass: "etf" },
};

/**
 * The right watchlist rail — real data via `useWatchlists`, full group CRUD (create/rename/
 * delete), and `@dnd-kit` drag-and-drop for both group reorder and item reorder/move-across-
 * groups. Resizable/collapsible via `ResizableRailHandle` on the left edge. The `☆` toggle lives
 * in the top bar; this renders when open. Backend: `docs/backend/adr/0027-first-class-watchlist.md`.
 */
export function WatchlistRail({
  onClose,
  width,
  onWidthChange,
  sheet = false,
}: {
  readonly onClose: () => void;
  readonly width: number;
  readonly onWidthChange: (width: number) => void;
  /** Rendered inside the below-`lg` sheet: fills its container, no resize handle. */
  readonly sheet?: boolean;
}): ReactNode {
  const t = useTranslations("shell.watchlist");
  const [tab, setTab] = useState<WatchlistTab>("tw");
  const scope = TAB_SCOPE[tab];

  return (
    <div className="flex h-full">
      {sheet ? null : (
        <ResizableRailHandle
          width={width}
          onWidthChange={onWidthChange}
          onCollapse={onClose}
          ariaLabel={t("resizeHandle")}
        />
      )}
      <aside
        aria-label={t("title")}
        style={sheet ? undefined : { width }}
        className={cn(
          "flex h-full flex-col bg-[var(--surface)]",
          sheet ? "w-full" : "border-[var(--border)] border-l",
        )}
      >
        <div className="flex h-14 shrink-0 items-center justify-between border-[var(--border)] border-b px-4">
          <span className="inline-flex items-center gap-2 font-semibold text-[15px] text-[var(--text)]">
            <StarIcon size={18} weight="regular" className="text-[var(--accent)]" aria-hidden />
            {t("title")}
          </span>
          <button
            type="button"
            onClick={onClose}
            aria-label={t("close")}
            title={t("close")}
            className={cn(
              "inline-flex size-11 items-center justify-center rounded-[var(--radius-button)]",
              "text-[var(--text-muted)] transition-colors hover:bg-[var(--surface-2)] hover:text-[var(--text)]",
            )}
          >
            <XIcon size={18} weight="regular" />
          </button>
        </div>

        <div className="flex gap-1 border-[var(--border)] border-b p-3" role="tablist">
          {TABS.map((key) => {
            const active = key === tab;
            return (
              <button
                key={key}
                type="button"
                role="tab"
                aria-selected={active}
                onClick={() => setTab(key)}
                className={cn(
                  "min-h-11 flex-1 rounded-[var(--radius-pill)] px-3 font-medium text-sm transition-colors",
                  active
                    ? "bg-[var(--accent-weak)] text-[var(--text)]"
                    : "text-[var(--text-muted)] hover:text-[var(--text)]",
                )}
              >
                {t(`tabs.${key}`)}
              </button>
            );
          })}
        </div>

        <WatchlistTabPanel key={tab} market={scope.market} assetClass={scope.assetClass} />
      </aside>
    </div>
  );
}

function WatchlistTabPanel({
  market,
  assetClass,
}: {
  readonly market: Market;
  readonly assetClass: WatchlistAssetClass;
}): ReactNode {
  const t = useTranslations("shell.watchlist");
  const query = useWatchlists(market);
  const reduceMotion = useReducedMotion();
  const [creating, setCreating] = useState(false);
  const [newGroupName, setNewGroupName] = useState("");
  const createMutation = useCreateWatchlist(market);
  const reorderGroupMutation = useReorderWatchlistGroup(market);
  const moveItemMutation = useMoveWatchlistItem(market);

  const allGroups = (query.data ?? []).filter((view) => view.group.asset_class === assetClass);
  const groupIds = allGroups.map((view) => view.group.id);
  // One batched quotes read for the whole tab (every symbol in every group), handed down to the
  // rows - never one request per row.
  const tabSymbols = [
    ...new Set(allGroups.flatMap((view) => view.items.map((item) => item.symbol))),
  ];
  const { quotes, isLoading: quotesLoading } = useSymbolQuotes(tabSymbols, market);
  const quoteBySymbol = new Map(quotes.map((quote) => [quote.code, quote]));

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 4 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  );

  if (query.isLoading) {
    return (
      <div className="flex-1 space-y-2 p-3">
        {["a", "b", "c"].map((key) => (
          <div
            key={key}
            className="h-10 animate-pulse rounded-[var(--radius-input)] bg-[var(--surface-2)]"
          />
        ))}
      </div>
    );
  }

  if (query.isError) {
    return (
      <div className="flex flex-1 flex-col items-center justify-center gap-2 px-6 text-center">
        <p className="text-[13px] text-[var(--text-muted)]">{t("loadError")}</p>
        <button
          type="button"
          onClick={() => query.refetch()}
          className="text-[13px] text-[var(--accent-text)] hover:underline"
        >
          {t("retry")}
        </button>
      </div>
    );
  }

  const handleGroupDragEnd = (event: DragEndEvent): void => {
    const { active, over } = event;
    if (!over || active.id === over.id) return;
    const activeIndex = groupIds.indexOf(String(active.id));
    const overIndex = groupIds.indexOf(String(over.id));
    if (activeIndex === -1 || overIndex === -1) return;
    const reordered = arrayMove(groupIds, activeIndex, overIndex);
    const movedId = String(active.id);
    const movedGroup = allGroups.find((view) => view.group.id === movedId);
    if (!movedGroup) return;
    const newIndex = reordered.indexOf(movedId);
    const afterId = newIndex > 0 ? reordered[newIndex - 1] : null;
    const beforeId = newIndex < reordered.length - 1 ? reordered[newIndex + 1] : null;
    reorderGroupMutation.mutate({
      watchlistId: movedId,
      afterId: afterId === movedId ? null : (afterId ?? null),
      beforeId: beforeId === movedId ? null : (beforeId ?? null),
      version: movedGroup.group.version,
    });
  };

  const handleItemDragEnd = (
    event: DragEndEvent,
    sourceGroupId: string,
    sourceItems: readonly WatchlistItem[],
  ): void => {
    const { active, over } = event;
    if (!over) return;
    const activeItem = sourceItems.find((item) => item.id === active.id);
    if (!activeItem) return;

    // Dropped on another item within the SAME group → local reorder via move-to-self-with-after.
    const overInSameGroup = sourceItems.some((item) => item.id === over.id);
    if (overInSameGroup && active.id !== over.id) {
      const overIndex = sourceItems.findIndex((item) => item.id === over.id);
      const afterId = overIndex > 0 ? (sourceItems[overIndex - 1]?.id ?? null) : null;
      moveItemMutation.mutate({
        itemId: activeItem.id,
        currentWatchlistId: sourceGroupId,
        targetWatchlistId: sourceGroupId,
        afterId: afterId === activeItem.id ? null : afterId,
      });
      return;
    }

    // Dropped on a different group's drop zone (`over.id` is that group's own container id,
    // prefixed to disambiguate from item ids) → cross-group move, appended at the end.
    const overIdStr = String(over.id);
    if (overIdStr.startsWith("group-drop:")) {
      const targetGroupId = overIdStr.slice("group-drop:".length);
      if (targetGroupId !== sourceGroupId) {
        moveItemMutation.mutate({
          itemId: activeItem.id,
          currentWatchlistId: sourceGroupId,
          targetWatchlistId: targetGroupId,
        });
      }
    }
  };

  return (
    <div className="flex-1 overflow-y-auto">
      {allGroups.length === 0 ? (
        <p className="px-6 py-8 text-center text-[13px] text-[var(--text-muted)] leading-relaxed">
          {t("empty")}
        </p>
      ) : (
        <DndContext sensors={sensors} onDragEnd={handleGroupDragEnd}>
          <SortableContext items={groupIds} strategy={verticalListSortingStrategy}>
            <ul className="divide-y divide-[var(--border)]">
              {allGroups.map((view) => (
                <WatchlistGroupSection
                  key={view.group.id}
                  view={view}
                  market={market}
                  quoteBySymbol={quoteBySymbol}
                  quotesLoading={quotesLoading}
                  reduceMotion={reduceMotion ?? false}
                  onItemDragEnd={(event) => handleItemDragEnd(event, view.group.id, view.items)}
                />
              ))}
            </ul>
          </SortableContext>
        </DndContext>
      )}

      <div className="border-[var(--border)] border-t p-2">
        {creating ? (
          <form
            onSubmit={(e) => {
              e.preventDefault();
              const name = newGroupName.trim();
              if (!name) return;
              createMutation.mutate(
                { name, assetClass },
                {
                  onSuccess: () => {
                    setNewGroupName("");
                    setCreating(false);
                  },
                },
              );
            }}
            className="flex items-center gap-1.5 px-1 py-1"
          >
            <input
              // biome-ignore lint/a11y/noAutofocus: user just clicked "+ 新增分組" — this input replacing that button is the intended focus target.
              autoFocus
              value={newGroupName}
              onChange={(e) => setNewGroupName(e.target.value)}
              onBlur={() => {
                if (!newGroupName.trim()) setCreating(false);
              }}
              placeholder={t("newGroupPlaceholder")}
              className="min-h-9 flex-1 rounded-[var(--radius-input)] border border-[var(--border)] bg-[var(--surface)] px-2.5 text-[13px] text-[var(--text)] outline-none focus-visible:border-[var(--accent)]"
            />
          </form>
        ) : (
          <button
            type="button"
            onClick={() => setCreating(true)}
            className="flex min-h-11 w-full items-center gap-1.5 rounded-[var(--radius-input)] px-2.5 text-[13px] text-[var(--text-muted)] transition-colors hover:bg-[var(--surface-2)] hover:text-[var(--text)]"
          >
            <PlusIcon size={14} weight="bold" aria-hidden />
            {t("newGroup")}
          </button>
        )}
      </div>
    </div>
  );
}

function WatchlistGroupSection({
  view,
  market,
  quoteBySymbol,
  quotesLoading,
  reduceMotion,
  onItemDragEnd,
}: {
  readonly view: WatchlistGroupView;
  readonly market: Market;
  readonly quoteBySymbol: ReadonlyMap<string, SymbolQuote>;
  readonly quotesLoading: boolean;
  readonly reduceMotion: boolean;
  readonly onItemDragEnd: (event: DragEndEvent) => void;
}): ReactNode {
  const t = useTranslations("shell.watchlist");
  const [expanded, setExpanded] = useState(true);
  const [renaming, setRenaming] = useState(false);
  const [nameDraft, setNameDraft] = useState(view.group.name);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const renameMutation = useRenameWatchlist(market);
  const colorMutation = useSetWatchlistColor(market);
  const deleteMutation = useDeleteWatchlist(market);
  const router = useRouter();
  const alertable = market === "TW" || market === "US";
  // The 技術分析 compare view for the group's first five TW stocks (stocks only; needs two).
  const compareSymbols =
    market === "TW" && view.group.asset_class === "stock"
      ? view.items.map((item) => item.symbol)
      : [];
  const compareLink = compareSymbols.length >= 2 ? compareHref(compareSymbols) : null;

  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({
    id: view.group.id,
  });
  const style = {
    transform: CSS.Transform.toString(transform),
    transition: reduceMotion ? undefined : transition,
  };

  const itemIds = view.items.map((item) => item.id);
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 4 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  );

  const submitRename = (): void => {
    const name = nameDraft.trim();
    if (!name || name === view.group.name) {
      setRenaming(false);
      setNameDraft(view.group.name);
      return;
    }
    renameMutation.mutate(
      { watchlistId: view.group.id, name, version: view.group.version },
      { onSettled: () => setRenaming(false) },
    );
  };

  return (
    <li
      ref={setNodeRef}
      style={{ ...style, opacity: isDragging ? 0.5 : 1 }}
      className="bg-[var(--surface)]"
    >
      <div className="group flex items-center gap-1.5 py-2 pr-2 pl-1">
        <button
          type="button"
          {...attributes}
          {...listeners}
          aria-label={t("dragGroup")}
          className="flex size-8 shrink-0 items-center justify-center text-[var(--text-muted)] hover:text-[var(--text)]"
        >
          <DotsSixVerticalIcon size={14} weight="bold" aria-hidden />
        </button>
        <button
          type="button"
          onClick={() => setExpanded((prev) => !prev)}
          aria-expanded={expanded}
          className="flex size-8 shrink-0 items-center justify-center text-[var(--text-muted)] hover:text-[var(--text)]"
        >
          {expanded ? (
            <CaretDownIcon size={14} weight="bold" aria-hidden />
          ) : (
            <CaretRightIcon size={14} weight="bold" aria-hidden />
          )}
        </button>
        {renaming ? (
          <input
            // biome-ignore lint/a11y/noAutofocus: user just clicked the group name to rename it — this input replacing that label is the intended focus target.
            autoFocus
            value={nameDraft}
            onChange={(e) => setNameDraft(e.target.value)}
            onBlur={submitRename}
            onKeyDown={(e) => {
              if (e.key === "Enter") submitRename();
              if (e.key === "Escape") {
                setRenaming(false);
                setNameDraft(view.group.name);
              }
            }}
            className="min-h-8 min-w-0 flex-1 rounded-[var(--radius-input)] border border-[var(--border)] bg-[var(--surface)] px-2 text-[13px] text-[var(--text)] outline-none focus-visible:border-[var(--accent)]"
          />
        ) : (
          <button
            type="button"
            onClick={() => setRenaming(true)}
            className="min-w-0 flex-1 truncate text-left font-medium text-[13px] text-[var(--text)] hover:underline"
            style={groupNameStyle(view.group)}
          >
            {view.group.name}
          </button>
        )}
        <GroupColorMenu
          name={view.group.name}
          color={view.group.color ?? null}
          onPick={(color) =>
            colorMutation.mutate({
              watchlistId: view.group.id,
              color,
              version: view.group.version,
            })
          }
        />
        <span className="shrink-0 rounded-[var(--radius-pill)] bg-[var(--surface-2)] px-2 py-0.5 text-[11px] text-[var(--text-muted)]">
          {view.items.length}
        </span>
        {compareLink ? (
          <button
            type="button"
            onClick={() => router.push(compareLink)}
            aria-label={t("compareGroup", { name: view.group.name })}
            className="-my-1.5 flex size-11 shrink-0 items-center justify-center text-[var(--text-muted)] hover:text-[var(--text)]"
          >
            <ChartLineIcon size={14} weight="regular" aria-hidden />
          </button>
        ) : null}
        {alertable ? (
          <DropdownMenu.Root>
            <DropdownMenu.Trigger asChild>
              <button
                type="button"
                aria-label={t("alertGroup", { name: view.group.name })}
                className="flex size-8 shrink-0 items-center justify-center text-[var(--text-muted)] opacity-0 transition-opacity hover:text-[var(--text)] focus-visible:opacity-100 group-hover:opacity-100 data-[state=open]:opacity-100"
              >
                <BellSimpleIcon size={14} weight="regular" aria-hidden />
              </button>
            </DropdownMenu.Trigger>
            <DropdownMenu.Portal>
              <DropdownMenu.Content
                align="end"
                sideOffset={6}
                className="z-50 min-w-56 rounded-[var(--radius-card)] border border-[var(--border)] bg-[var(--surface)] p-1 shadow-lg"
              >
                <DropdownMenu.Item
                  onSelect={() =>
                    router.push(`/settings/alerts?alert=group&group=${view.group.id}`)
                  }
                  className="!outline-none min-h-11 cursor-pointer rounded-[var(--radius-button)] px-3 py-2 text-[13px] text-[var(--text)] data-[highlighted]:bg-[var(--surface-2)]"
                >
                  <span className="block">{t("alertGroupRelative")}</span>
                  <span className="block text-[11px] text-[var(--text-muted)]">
                    {t("alertGroupRelativeHint")}
                  </span>
                </DropdownMenu.Item>
                <DropdownMenu.Item
                  onSelect={() =>
                    router.push(`/settings/alerts?alert=perStock&group=${view.group.id}`)
                  }
                  className="!outline-none min-h-11 cursor-pointer rounded-[var(--radius-button)] px-3 py-2 text-[13px] text-[var(--text)] data-[highlighted]:bg-[var(--surface-2)]"
                >
                  <span className="block">{t("alertGroupPerStock")}</span>
                  <span className="block text-[11px] text-[var(--text-muted)]">
                    {t("alertGroupPerStockHint")}
                  </span>
                </DropdownMenu.Item>
              </DropdownMenu.Content>
            </DropdownMenu.Portal>
          </DropdownMenu.Root>
        ) : null}
        <button
          type="button"
          onClick={() => setConfirmingDelete(true)}
          aria-label={t("deleteGroup")}
          className="flex size-8 shrink-0 items-center justify-center text-[var(--text-muted)] opacity-0 transition-opacity hover:text-[var(--warn)] focus-visible:opacity-100 group-hover:opacity-100"
        >
          <TrashIcon size={14} weight="regular" aria-hidden />
        </button>
      </div>

      {expanded && view.items.length === 0 ? (
        <GroupDropZone groupId={view.group.id}>
          <p className="px-4 py-3 text-[12px] text-[var(--text-muted)]">{t("emptyGroup")}</p>
        </GroupDropZone>
      ) : null}
      {expanded && view.items.length > 0 ? (
        <DndContext sensors={sensors} onDragEnd={onItemDragEnd}>
          <SortableContext items={itemIds} strategy={verticalListSortingStrategy}>
            <GroupDropZone groupId={view.group.id}>
              <ul className="divide-y divide-[var(--border)]">
                {view.items.map((item) => (
                  <WatchlistStockRow
                    key={item.id}
                    item={item}
                    market={market}
                    quote={quoteBySymbol.get(item.symbol)}
                    quoteLoading={quotesLoading}
                    watchlistId={view.group.id}
                    reduceMotion={reduceMotion}
                  />
                ))}
              </ul>
            </GroupDropZone>
          </SortableContext>
        </DndContext>
      ) : null}

      <AlertDialog.Root open={confirmingDelete} onOpenChange={setConfirmingDelete}>
        <AlertDialog.Portal>
          <AlertDialog.Overlay className="fixed inset-0 z-50 bg-black/50" />
          <AlertDialog.Content
            className={cn(
              "fixed top-1/2 left-1/2 z-50 w-[min(360px,92vw)] -translate-x-1/2 -translate-y-1/2",
              "rounded-[var(--radius-card)] border border-[var(--border)] bg-[var(--surface)] p-5 shadow-2xl",
            )}
          >
            <AlertDialog.Title className="font-medium text-[15px] text-[var(--text)]">
              {t("deleteGroupConfirmTitle")}
            </AlertDialog.Title>
            <AlertDialog.Description className="mt-2 text-[13px] text-[var(--text-muted)]">
              {t("deleteGroupConfirmBody", { name: view.group.name })}
            </AlertDialog.Description>
            <div className="mt-4 flex justify-end gap-2">
              <AlertDialog.Cancel asChild>
                <button
                  type="button"
                  className="inline-flex min-h-9 items-center rounded-[var(--radius-button)] px-3 text-[13px] text-[var(--text)] transition-colors hover:bg-[var(--surface-2)]"
                >
                  {t("cancel")}
                </button>
              </AlertDialog.Cancel>
              <AlertDialog.Action asChild>
                <button
                  type="button"
                  onClick={() => deleteMutation.mutate(view.group.id)}
                  disabled={deleteMutation.isPending}
                  className="inline-flex min-h-9 items-center rounded-[var(--radius-button)] bg-[var(--warn)] px-3 text-[13px] text-[var(--bg)] transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {t("deleteGroupConfirm")}
                </button>
              </AlertDialog.Action>
            </div>
          </AlertDialog.Content>
        </AlertDialog.Portal>
      </AlertDialog.Root>
    </li>
  );
}

/** A stable drop-target id (`group-drop:{groupId}`) so a cross-group item drop can land on a
 * group even when that group has zero items — `useSortable`'s own items need at least one real
 * child, so this wraps every group's item list (or its empty-state) in one addressable region. */
function GroupDropZone({
  groupId,
  children,
}: {
  readonly groupId: string;
  readonly children: ReactNode;
}): ReactNode {
  const { setNodeRef } = useSortable({ id: `group-drop:${groupId}` });
  return <div ref={setNodeRef}>{children}</div>;
}

function WatchlistStockRow({
  item,
  market,
  watchlistId,
  reduceMotion,
  quote,
  quoteLoading,
}: {
  readonly item: WatchlistItem;
  readonly market: Market;
  readonly quote: SymbolQuote | undefined;
  /** True while the tab's batched quotes read is in flight: rows shimmer instead of showing the
   * bare code and "—", which reads as "no data". */
  readonly quoteLoading: boolean;
  readonly watchlistId: string;
  readonly reduceMotion: boolean;
}): ReactNode {
  const locale = useLocale();
  const t = useTranslations("shell.watchlist");
  const removeMutation = useRemoveWatchlistItem(market);
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({
    id: item.id,
  });
  const style = {
    transform: CSS.Transform.toString(transform),
    transition: reduceMotion ? undefined : transition,
  };
  const pctChange = quote?.changePct ?? null;
  const hasChange = pctChange !== null;
  const color = hasChange ? directionColor(pctChange, 2) : "var(--text-muted)";
  const name = quote?.name ?? item.symbol;
  const pending = quoteLoading && quote?.price == null;
  const router = useRouter();
  const alertable = market === "TW" || market === "US";

  return (
    <li
      ref={setNodeRef}
      style={{ ...style, opacity: isDragging ? 0.5 : 1 }}
      role="group"
      aria-label={`${name} ${item.symbol} ${
        hasChange ? formatSignedChange(pctChange, locale, { decimals: 2, unit: "%" }) : t("empty")
      }`}
      className="group flex items-center gap-2 py-2 pr-2 pl-1"
    >
      <button
        type="button"
        {...attributes}
        {...listeners}
        aria-label={t("dragItem")}
        className="flex size-7 shrink-0 items-center justify-center text-[var(--text-muted)] opacity-0 hover:text-[var(--text)] focus-visible:opacity-100 group-hover:opacity-100"
      >
        <DotsSixVerticalIcon size={12} weight="bold" aria-hidden />
      </button>
      <SymbolLink symbol={item.symbol} market={market} className="min-w-0 flex-1 truncate">
        {pending && quote?.name == null ? (
          <Skeleton className="inline-block h-3 w-12 align-middle" />
        ) : (
          <span className="truncate text-[13px] text-[var(--text)] hover:underline">{name}</span>
        )}
        <span className="ml-1.5 text-[11px] text-[var(--text-muted)]">{item.symbol}</span>
      </SymbolLink>
      <span aria-hidden className="h-[20px] w-10 shrink-0">
        {pending ? (
          <Skeleton className="h-full w-full" />
        ) : (
          <Sparkline values={quote?.sparkline ?? []} color={color} />
        )}
      </span>
      <span
        aria-hidden
        className="w-16 shrink-0 text-right font-num text-[12px] tabular-nums"
        style={{ color }}
      >
        <RailPrice pending={pending} price={quote?.price ?? null} />
      </span>
      {alertable ? (
        <button
          type="button"
          onClick={() =>
            router.push(
              `/settings/alerts?alert=symbol&symbol=${encodeURIComponent(item.symbol)}&market=${market}`,
            )
          }
          aria-label={t("alertItem", { name })}
          className="flex size-7 shrink-0 items-center justify-center text-[var(--text-muted)] opacity-0 transition-opacity hover:text-[var(--text)] focus-visible:opacity-100 group-hover:opacity-100"
        >
          <BellSimpleIcon size={12} weight="regular" aria-hidden />
        </button>
      ) : null}
      <button
        type="button"
        onClick={() => removeMutation.mutate({ watchlistId, symbol: item.symbol })}
        aria-label={t("removeItem")}
        className="flex size-7 shrink-0 items-center justify-center text-[var(--text-muted)] opacity-0 transition-opacity hover:text-[var(--warn)] focus-visible:opacity-100 group-hover:opacity-100"
      >
        <XIcon size={12} weight="bold" aria-hidden />
      </button>
    </li>
  );
}

/** Pick a group's label colour from the palette (or none). The swatches are the non-signed chart
 * tokens, so a group colour is never confused with a price move. */
function RailPrice({
  pending,
  price,
}: {
  readonly pending: boolean;
  readonly price: number | null;
}): ReactNode {
  const locale = useLocale();
  if (pending) return <Skeleton className="ml-auto h-3 w-12" />;
  return price === null ? "—" : formatNumber(price, locale, { decimals: 1 });
}

function GroupColorMenu({
  name,
  color,
  onPick,
}: {
  readonly name: string;
  readonly color: WatchlistColor | null;
  readonly onPick: (color: WatchlistColor | null) => void;
}): ReactNode {
  const t = useTranslations("shell.watchlist");
  return (
    <DropdownMenu.Root>
      <DropdownMenu.Trigger asChild>
        <button
          type="button"
          aria-label={t("colorGroup", { name })}
          className="flex size-8 shrink-0 items-center justify-center text-[var(--text-muted)] opacity-0 transition-opacity hover:text-[var(--text)] focus-visible:opacity-100 group-hover:opacity-100 data-[state=open]:opacity-100"
        >
          <PaletteIcon size={14} weight="regular" aria-hidden />
        </button>
      </DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content
          align="end"
          sideOffset={6}
          className="z-50 rounded-[var(--radius-card)] border border-[var(--border)] bg-[var(--surface)] p-2 shadow-lg"
        >
          <div className="flex items-center gap-1">
            {WATCHLIST_COLORS.map((key) => (
              <DropdownMenu.Item
                key={key}
                aria-label={t(`color.${key}`)}
                onSelect={() => onPick(key)}
                className="!outline-none flex size-9 cursor-pointer items-center justify-center rounded-full data-[highlighted]:bg-[var(--surface-2)]"
              >
                <span
                  className="flex size-5 items-center justify-center rounded-full"
                  style={{ backgroundColor: `var(--${key})` }}
                >
                  {color === key ? (
                    <CheckIcon size={12} weight="bold" className="text-[var(--bg)]" aria-hidden />
                  ) : null}
                </span>
              </DropdownMenu.Item>
            ))}
            <DropdownMenu.Item
              aria-label={t("color.none")}
              onSelect={() => onPick(null)}
              className="!outline-none flex size-9 cursor-pointer items-center justify-center rounded-full text-[var(--text-muted)] data-[highlighted]:bg-[var(--surface-2)]"
            >
              <ProhibitIcon size={16} weight="regular" aria-hidden />
            </DropdownMenu.Item>
          </div>
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  );
}
