"use client";

import {
  CheckIcon,
  DotsThreeIcon,
  PencilSimpleIcon,
  StarIcon,
  TrashIcon,
} from "@phosphor-icons/react";
import * as AlertDialog from "@radix-ui/react-alert-dialog";
import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "next/navigation";
import { useTranslations } from "next-intl";
import { type ReactNode, useEffect, useState } from "react";
import { useNewThreadStore } from "@/components/shell/new-thread-store";
import { useTypewriterOnce } from "@/hooks/use-typewriter-once";
import { useRouter } from "@/i18n/navigation";
import { queryKeys } from "@/lib/api/query-keys";
import { deleteThread, deleteThreads, listThreads, patchThread } from "@/lib/api/threads";
import { cn } from "@/lib/cn";
import type { Thread } from "@/types/conversation";

/** Recent conversations, folded directly into the primary sidebar (Claude-style "Recents") rather
 * than a separate Conversations panel next to the chat canvas. Hidden entirely when collapsed —
 * there's no icon-only representation of a conversation list that reads as anything but noise at
 * 64px. Reads the active thread straight from the URL (`?thread=`) rather than requiring every
 * sidebar consumer to thread it through as a prop across routes that aren't `/chat` at all.
 *
 * Starred threads pin into their own section above the recency-grouped list (mirroring the
 * backend's `starred` flag, which existed already but had no UI surface); the rest are grouped
 * into Today / Yesterday / Previous 7 days / Previous 30 days / Older date buckets, each internally
 * still sorted by `last_message_at` descending. */
export function RecentsList(): ReactNode {
  const t = useTranslations("chat.threads");
  const router = useRouter();
  const activeThreadId = useSearchParams().get("thread");
  const queryClient = useQueryClient();
  const query = useQuery({
    queryKey: queryKeys.threads,
    queryFn: ({ signal }) => listThreads(signal),
  });
  const newThread = useNewThreadStore();
  // Multi-select ("Select" mode) is a separate mode from normal browsing, not a per-row toggle
  // always available — entering it hides every row's own star/rename/delete menu (those verbs
  // don't apply to a multi-selection) and repurposes row clicks from "navigate" to "toggle
  // selection". `selectedIds` is cleared whenever select mode is exited (Cancel, or after a
  // successful bulk delete) so a later "Select" always starts from zero, never a stale carry-over.
  const [selecting, setSelecting] = useState(false);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  // Lives here, not inside `BulkDeleteDialog`, because a successful bulk delete calls
  // `exitSelecting` — which unmounts `BulkDeleteBar`/`BulkDeleteDialog` immediately — so a
  // partial-failure toast set inside either of those would never get a chance to render.
  const [partialFailureCount, setPartialFailureCount] = useState<number | null>(null);

  const onSelect = (threadId: string): void => {
    router.push(`/chat?thread=${encodeURIComponent(threadId)}`);
  };

  const invalidate = (): void => {
    void queryClient.invalidateQueries({ queryKey: queryKeys.threads });
  };

  const toggleSelected = (threadId: string): void => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(threadId)) {
        next.delete(threadId);
      } else {
        next.add(threadId);
      }
      return next;
    });
  };

  const exitSelecting = (): void => {
    setSelecting(false);
    setSelectedIds(new Set());
  };

  // Once the new thread's row has actually rendered (title present, revealed), the placeholder's
  // job is done — reset so a later send starts clean rather than replaying a stale reveal.
  const revealingThreadId =
    newThread.phase === "resolved" && !newThread.revealed ? newThread.threadId : null;
  const showPendingPlaceholder =
    newThread.phase !== "idle" &&
    (revealingThreadId === null || !query.data?.some((thread) => thread.id === revealingThreadId));

  if (query.isPending) {
    return (
      <div className="flex min-h-0 flex-1 flex-col overflow-y-auto px-3">
        {showPendingPlaceholder ? <PendingThreadRow /> : null}
        <RecentsSkeleton />
      </div>
    );
  }
  if (query.isError) {
    return (
      <div className="flex min-h-0 flex-1 flex-col overflow-y-auto px-3">
        {showPendingPlaceholder ? <PendingThreadRow /> : null}
        <p className="px-3 py-2 text-[13px] text-[var(--text-muted)]">{t("error")}</p>
      </div>
    );
  }
  if (query.data.length === 0) {
    return (
      <div className="flex min-h-0 flex-1 flex-col overflow-y-auto px-3">
        {showPendingPlaceholder ? <PendingThreadRow /> : null}
        <p className="px-3 py-2 text-[13px] text-[var(--text-muted)]">{t("empty")}</p>
      </div>
    );
  }

  const starred = query.data.filter((thread) => thread.starred);
  const unstarred = query.data.filter((thread) => !thread.starred);
  const groups = groupByRecency(unstarred);
  const allIds = query.data.map((thread) => thread.id);
  const allSelected = allIds.length > 0 && allIds.every((id) => selectedIds.has(id));

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-y-auto px-3">
      <RecentsHeader
        label={t("recents")}
        selecting={selecting}
        onEnterSelect={() => setSelecting(true)}
        onExitSelect={exitSelecting}
      />
      {selecting ? (
        <SelectAllRow
          checked={allSelected}
          count={allIds.length}
          onToggle={() => setSelectedIds(allSelected ? new Set() : new Set(allIds))}
        />
      ) : null}
      {showPendingPlaceholder ? <PendingThreadRow /> : null}
      {starred.length > 0 ? (
        <ThreadSection
          label={t("starred")}
          threads={starred}
          activeThreadId={activeThreadId}
          revealingThreadId={revealingThreadId}
          selecting={selecting}
          selectedIds={selectedIds}
          onSelect={onSelect}
          onToggleSelected={toggleSelected}
          onChanged={invalidate}
        />
      ) : null}
      {groups.map((group) => (
        <ThreadSection
          key={group.key}
          label={t(`groups.${group.key}`)}
          threads={group.threads}
          activeThreadId={activeThreadId}
          revealingThreadId={revealingThreadId}
          selecting={selecting}
          selectedIds={selectedIds}
          onSelect={onSelect}
          onToggleSelected={toggleSelected}
          onChanged={invalidate}
        />
      ))}
      {selecting && selectedIds.size > 0 ? (
        <BulkDeleteBar
          selectedIds={selectedIds}
          onChanged={invalidate}
          onDone={exitSelecting}
          onPartialFailure={setPartialFailureCount}
        />
      ) : null}
      {partialFailureCount !== null ? (
        <PartialFailureToast
          count={partialFailureCount}
          onDismiss={() => setPartialFailureCount(null)}
        />
      ) : null}
    </div>
  );
}

/** A transient "N couldn't be deleted" toast (the save-flow toast pattern,
 * adapted here rather than imported from `components/settings/settings-states.tsx` — that
 * component is settings-scoped, and this feature stays co-located per the project's own
 * "feature-only shapes stay co-located" convention). Auto-dismisses so it never lingers as
 * permanent chrome. */
function PartialFailureToast({
  count,
  onDismiss,
}: {
  count: number;
  onDismiss: () => void;
}): ReactNode {
  const t = useTranslations("chat.threads.select");
  useEffect(() => {
    const id = setTimeout(onDismiss, 4000);
    return () => clearTimeout(id);
  }, [onDismiss]);

  return (
    <div
      role="status"
      aria-live="polite"
      className={cn(
        "fixed bottom-6 left-1/2 z-40 -translate-x-1/2 rounded-[var(--radius-pill)]",
        "border border-[var(--border)] bg-[var(--surface)] px-4 py-2 text-[13px] text-[var(--text)] shadow-lg",
      )}
    >
      {t("partialFailed", { count })}
    </div>
  );
}

/** The "最近" section label + the "Select"/"Cancel select" toggle in its trailing edge — only
 * shown once there's at least one thread to select (the empty/error/loading states above never
 * reach this component). */
function RecentsHeader({
  label,
  selecting,
  onEnterSelect,
  onExitSelect,
}: {
  label: string;
  selecting: boolean;
  onEnterSelect: () => void;
  onExitSelect: () => void;
}): ReactNode {
  const t = useTranslations("chat.threads.select");
  return (
    <div className="flex items-center justify-between pt-2 pb-1">
      <p className="px-3 font-medium text-[11px] text-[var(--text-muted)] uppercase tracking-wide">
        {label}
      </p>
      <button
        type="button"
        onClick={selecting ? onExitSelect : onEnterSelect}
        className={cn(
          "min-h-7 rounded-[var(--radius-pill)] px-2.5 font-medium text-[12px] transition-colors",
          "text-[var(--accent-text)] hover:bg-[var(--surface-2)]",
          "!outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]",
        )}
      >
        {selecting ? t("exit") : t("enter")}
      </button>
    </div>
  );
}

function SelectAllRow({
  checked,
  count,
  onToggle,
}: {
  checked: boolean;
  count: number;
  onToggle: () => void;
}): ReactNode {
  const t = useTranslations("chat.threads.select");
  return (
    <button
      type="button"
      onClick={onToggle}
      className={cn(
        "flex min-h-9 w-full items-center gap-2.5 rounded-[var(--radius-button)] px-3 text-left",
        "text-[13px] text-[var(--text-muted)] transition-colors hover:bg-[var(--surface-2)]",
        "!outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]",
      )}
    >
      <RowCheckbox checked={checked} />
      {t("selectAll", { count })}
    </button>
  );
}

/** Shown from the instant a message is sent with no thread selected until the new thread's row
 * (with its generated title) actually renders in the list below — covers both "thread doesn't
 * exist server-side yet" and "it exists but the list hasn't refetched" without the placeholder
 * ever having a real id to link to. */
function PendingThreadRow(): ReactNode {
  const t = useTranslations("chat.threads");
  return (
    <div className="pt-2 pb-1">
      <div
        role="status"
        aria-label={t("creating")}
        className="mx-0 h-9 animate-pulse rounded-[var(--radius-button)] bg-[var(--surface-2)]"
      />
    </div>
  );
}

const GROUP_ORDER = ["today", "yesterday", "previous7Days", "previous30Days", "older"] as const;
type GroupKey = (typeof GROUP_ORDER)[number];

function groupByRecency(threads: Thread[]): { key: GroupKey; threads: Thread[] }[] {
  const buckets = new Map<GroupKey, Thread[]>();
  const now = Date.parse(threads[0]?.last_message_at ?? "") || 0;
  const startOfToday = startOfDay(now || Date.parse(new Date().toISOString()));
  for (const thread of threads) {
    const key = bucketFor(Date.parse(thread.last_message_at), startOfToday);
    const bucket = buckets.get(key);
    if (bucket) {
      bucket.push(thread);
    } else {
      buckets.set(key, [thread]);
    }
  }
  return GROUP_ORDER.filter((key) => buckets.has(key)).map((key) => ({
    key,
    // biome-ignore lint/style/noNonNullAssertion: key came from buckets.has() just above
    threads: buckets.get(key)!,
  }));
}

function startOfDay(epochMs: number): number {
  const d = new Date(epochMs);
  d.setHours(0, 0, 0, 0);
  return d.getTime();
}

const DAY_MS = 24 * 60 * 60 * 1000;

function bucketFor(epochMs: number, startOfTodayMs: number): GroupKey {
  const days = Math.floor((startOfTodayMs - startOfDay(epochMs)) / DAY_MS);
  if (days <= 0) {
    return "today";
  }
  if (days === 1) {
    return "yesterday";
  }
  if (days <= 7) {
    return "previous7Days";
  }
  if (days <= 30) {
    return "previous30Days";
  }
  return "older";
}

function ThreadSection({
  label,
  threads,
  activeThreadId,
  revealingThreadId,
  selecting,
  selectedIds,
  onSelect,
  onToggleSelected,
  onChanged,
}: {
  label: string;
  threads: Thread[];
  activeThreadId: string | null;
  revealingThreadId: string | null;
  selecting: boolean;
  selectedIds: Set<string>;
  onSelect: (threadId: string) => void;
  onToggleSelected: (threadId: string) => void;
  onChanged: () => void;
}): ReactNode {
  return (
    <div className="pt-2 pb-1">
      <p className="px-3 pb-1 font-medium text-[11px] text-[var(--text-muted)] uppercase tracking-wide">
        {label}
      </p>
      <ul className="flex flex-col gap-0.5">
        {threads.map((thread) => (
          <ThreadRow
            key={thread.id}
            thread={thread}
            active={thread.id === activeThreadId}
            revealing={thread.id === revealingThreadId}
            selecting={selecting}
            checked={selectedIds.has(thread.id)}
            onSelect={() => onSelect(thread.id)}
            onToggleSelected={() => onToggleSelected(thread.id)}
            onChanged={onChanged}
          />
        ))}
      </ul>
    </div>
  );
}

/** A small, unstyled checkbox glyph — a filled `--accent` box with a check when checked, an
 * outlined `--border` box otherwise. Not a native `<input type="checkbox">`: every row here is
 * already a `<button>` (the whole row toggles selection, not just a small hit target inside it),
 * and nesting an interactive checkbox inside an interactive button is invalid HTML — this is
 * purely the visual indicator, with the actual toggle semantics living on the row's own button. */
function RowCheckbox({ checked }: { checked: boolean }): ReactNode {
  return (
    <span
      aria-hidden
      className={cn(
        "flex size-4 shrink-0 items-center justify-center rounded-[3px] border transition-colors",
        checked
          ? "border-[var(--accent)] bg-[var(--accent)]"
          : "border-[var(--border)] bg-transparent",
      )}
    >
      {checked ? <CheckIcon size={11} weight="bold" className="text-[var(--bg)]" /> : null}
    </span>
  );
}

function ThreadRow({
  thread,
  active,
  revealing,
  selecting,
  checked,
  onSelect,
  onToggleSelected,
  onChanged,
}: {
  thread: Thread;
  active: boolean;
  revealing: boolean;
  selecting: boolean;
  checked: boolean;
  onSelect: () => void;
  onToggleSelected: () => void;
  onChanged: () => void;
}): ReactNode {
  const t = useTranslations("chat.threads");
  const [renaming, setRenaming] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const fullTitle = thread.title || t("untitled");
  const typedTitle = useTypewriterOnce(fullTitle, revealing, () =>
    useNewThreadStore.getState().markRevealed(),
  );
  const title = revealing ? typedTitle : fullTitle;

  const starMutation = useMutation({
    mutationFn: () => patchThread(thread.id, { starred: !thread.starred }, thread.version),
    onSuccess: onChanged,
  });

  if (renaming) {
    return (
      <li>
        <RenameForm thread={thread} onDone={() => setRenaming(false)} onChanged={onChanged} />
      </li>
    );
  }

  return (
    <li className="group relative">
      <button
        type="button"
        onClick={selecting ? onToggleSelected : onSelect}
        aria-current={!selecting && active ? "true" : undefined}
        aria-pressed={selecting ? checked : undefined}
        title={title}
        className={cn(
          "flex min-h-9 w-full items-center gap-2.5 rounded-[var(--radius-button)] px-3 text-left transition-colors",
          !selecting && "pr-8",
          active || checked
            ? "bg-[var(--accent-weak)] text-[var(--text)]"
            : "text-[var(--text-muted)] hover:bg-[var(--surface-2)] hover:text-[var(--text)]",
        )}
      >
        {selecting ? <RowCheckbox checked={checked} /> : null}
        {revealing && title.length === 0 ? (
          <span
            aria-hidden
            className="h-3.5 w-24 animate-pulse rounded-full bg-[var(--text-muted)]/30"
          />
        ) : (
          <span className="min-w-0 flex-1 truncate text-[13px]">
            {title}
            {revealing && title.length < fullTitle.length ? (
              <span aria-hidden className="ml-px inline-block w-px animate-pulse bg-current" />
            ) : null}
          </span>
        )}
      </button>

      {selecting ? null : (
        <DropdownMenu.Root>
          <DropdownMenu.Trigger asChild>
            <button
              type="button"
              aria-label={t("menu.label")}
              className={cn(
                "absolute inset-y-0 right-1 inline-flex w-7 items-center justify-center rounded-[var(--radius-button)]",
                "text-[var(--text-muted)] opacity-0 transition-opacity hover:bg-[var(--surface)] hover:text-[var(--text)]",
                "focus-visible:opacity-100 group-hover:opacity-100 data-[state=open]:opacity-100",
                "!outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]",
              )}
            >
              <DotsThreeIcon size={16} weight="bold" aria-hidden />
            </button>
          </DropdownMenu.Trigger>
          <DropdownMenu.Portal>
            <DropdownMenu.Content
              side="right"
              align="start"
              sideOffset={4}
              className={cn(
                "z-50 w-48 rounded-[var(--radius-card)] border border-[var(--border)]",
                "bg-[var(--surface)] p-1.5 shadow-2xl",
              )}
            >
              <DropdownMenu.Item
                onSelect={() => starMutation.mutate()}
                className={cn(
                  "flex min-h-9 cursor-pointer items-center gap-2 rounded-[var(--radius-button)] px-2.5",
                  "text-[var(--text)] text-sm outline-none transition-colors",
                  "data-[highlighted]:bg-[var(--surface-2)]",
                )}
              >
                <StarIcon
                  size={16}
                  weight={thread.starred ? "fill" : "regular"}
                  aria-hidden
                  className={thread.starred ? "text-[var(--accent)]" : undefined}
                />
                {thread.starred ? t("menu.unstar") : t("menu.star")}
              </DropdownMenu.Item>
              <DropdownMenu.Item
                onSelect={() => setRenaming(true)}
                className={cn(
                  "flex min-h-9 cursor-pointer items-center gap-2 rounded-[var(--radius-button)] px-2.5",
                  "text-[var(--text)] text-sm outline-none transition-colors",
                  "data-[highlighted]:bg-[var(--surface-2)]",
                )}
              >
                <PencilSimpleIcon size={16} weight="regular" aria-hidden />
                {t("menu.rename")}
              </DropdownMenu.Item>
              <DropdownMenu.Separator className="my-1 h-px bg-[var(--border)]" />
              <DropdownMenu.Item
                onSelect={() => setDeleteOpen(true)}
                className={cn(
                  "flex min-h-9 cursor-pointer items-center gap-2 rounded-[var(--radius-button)] px-2.5",
                  "text-[var(--warn)] text-sm outline-none transition-colors",
                  "data-[highlighted]:bg-[var(--surface-2)]",
                )}
              >
                <TrashIcon size={16} weight="regular" aria-hidden />
                {t("menu.delete")}
              </DropdownMenu.Item>
            </DropdownMenu.Content>
          </DropdownMenu.Portal>
        </DropdownMenu.Root>
      )}

      {selecting ? null : (
        <DeleteThreadDialog
          thread={thread}
          open={deleteOpen}
          onOpenChange={setDeleteOpen}
          onChanged={onChanged}
        />
      )}
    </li>
  );
}

function RenameForm({
  thread,
  onDone,
  onChanged,
}: {
  thread: Thread;
  onDone: () => void;
  onChanged: () => void;
}): ReactNode {
  const t = useTranslations("chat.threads");
  const [value, setValue] = useState(thread.title);

  const renameMutation = useMutation({
    mutationFn: (title: string) => patchThread(thread.id, { title }, thread.version),
    onSuccess: () => {
      onChanged();
      onDone();
    },
    onError: onDone,
  });

  const commit = (): void => {
    const trimmed = value.trim();
    if (trimmed && trimmed !== thread.title) {
      renameMutation.mutate(trimmed);
    } else {
      onDone();
    }
  };

  return (
    <input
      // biome-ignore lint/a11y/noAutofocus: user just chose "Rename" from a menu — this input replacing the row's label is the intended focus target.
      autoFocus
      name="thread-title"
      value={value}
      placeholder={t("rename.placeholder")}
      onChange={(e) => setValue(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => {
        if (e.key === "Enter") {
          e.currentTarget.blur();
        } else if (e.key === "Escape") {
          onDone();
        }
      }}
      className={cn(
        "flex min-h-9 w-full items-center rounded-[var(--radius-button)] border border-[var(--accent)] bg-[var(--surface)] px-3",
        "text-[13px] text-[var(--text)]",
        "!outline-none",
      )}
    />
  );
}

function DeleteThreadDialog({
  thread,
  open,
  onOpenChange,
  onChanged,
}: {
  thread: Thread;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onChanged: () => void;
}): ReactNode {
  const t = useTranslations("chat.threads");

  const deleteMutation = useMutation({
    mutationFn: () => deleteThread(thread.id),
    onSuccess: () => {
      onChanged();
      onOpenChange(false);
    },
  });

  return (
    <AlertDialog.Root open={open} onOpenChange={onOpenChange}>
      <AlertDialog.Portal>
        <AlertDialog.Overlay className="fixed inset-0 z-50 bg-black/50" />
        <AlertDialog.Content
          className={cn(
            "fixed top-1/2 left-1/2 z-50 w-[min(360px,92vw)] -translate-x-1/2 -translate-y-1/2",
            "rounded-[var(--radius-card)] border border-[var(--border)] bg-[var(--surface)] p-5 shadow-2xl",
          )}
        >
          <AlertDialog.Title className="font-medium text-[15px] text-[var(--text)]">
            {t("delete.confirmTitle")}
          </AlertDialog.Title>
          <AlertDialog.Description className="mt-2 text-[13px] text-[var(--text-muted)]">
            {t("delete.confirmBody", { title: thread.title || t("untitled") })}
          </AlertDialog.Description>
          <div className="mt-4 flex justify-end gap-2">
            <AlertDialog.Cancel asChild>
              <button
                type="button"
                className={cn(
                  "inline-flex min-h-9 items-center rounded-[var(--radius-button)] px-3 text-[13px]",
                  "text-[var(--text)] transition-colors hover:bg-[var(--surface-2)]",
                )}
              >
                {t("delete.cancel")}
              </button>
            </AlertDialog.Cancel>
            <AlertDialog.Action asChild>
              <button
                type="button"
                onClick={() => deleteMutation.mutate()}
                disabled={deleteMutation.isPending}
                className={cn(
                  "inline-flex min-h-9 items-center rounded-[var(--radius-button)] bg-[var(--warn)] px-3 text-[13px] text-[var(--bg)]",
                  "transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50",
                )}
              >
                {t("delete.confirm")}
              </button>
            </AlertDialog.Action>
          </div>
        </AlertDialog.Content>
      </AlertDialog.Portal>
    </AlertDialog.Root>
  );
}

/** The bottom action bar, shown only once ≥1 row is checked — pinned below the thread list
 * (`RecentsList` renders it last, inside the same scroll container's flow, not floating/fixed;
 * the sidebar footer/account-chip below it stays put — see `sidebar.tsx`'s own layout, which
 * this component doesn't need to know about). Delete opens the plural confirm dialog; only a
 * successful (or partially-successful, see `deleteThreads`) delete exits select mode. */
function BulkDeleteBar({
  selectedIds,
  onChanged,
  onDone,
  onPartialFailure,
}: {
  selectedIds: Set<string>;
  onChanged: () => void;
  onDone: () => void;
  onPartialFailure: (count: number) => void;
}): ReactNode {
  const t = useTranslations("chat.threads.select");
  const [confirmOpen, setConfirmOpen] = useState(false);

  return (
    <div
      className={cn(
        "sticky bottom-0 mt-1 flex items-center justify-between gap-2 rounded-[var(--radius-card)]",
        "border border-[var(--border)] bg-[var(--surface)] px-3 py-2 shadow-lg",
      )}
    >
      <span className="text-[13px] text-[var(--text-muted)]">
        {t("selectedCount", { count: selectedIds.size })}
      </span>
      <button
        type="button"
        onClick={() => setConfirmOpen(true)}
        className={cn(
          "inline-flex min-h-9 items-center gap-1.5 rounded-[var(--radius-button)] px-3 text-[13px]",
          "text-[var(--warn)] transition-colors hover:bg-[var(--surface-2)]",
        )}
      >
        <TrashIcon size={16} weight="regular" aria-hidden />
        {t("delete")}
      </button>
      <BulkDeleteDialog
        selectedIds={selectedIds}
        open={confirmOpen}
        onOpenChange={setConfirmOpen}
        onChanged={onChanged}
        onDone={onDone}
        onPartialFailure={onPartialFailure}
      />
    </div>
  );
}

function BulkDeleteDialog({
  selectedIds,
  open,
  onOpenChange,
  onChanged,
  onDone,
  onPartialFailure,
}: {
  selectedIds: Set<string>;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onChanged: () => void;
  onDone: () => void;
  onPartialFailure: (count: number) => void;
}): ReactNode {
  const t = useTranslations("chat.threads.select");

  const deleteMutation = useMutation({
    mutationFn: () => deleteThreads([...selectedIds]),
    onSuccess: ({ failedIds }) => {
      onChanged();
      onOpenChange(false);
      // A partial failure (some threads deleted, some not) still exits select mode — the list
      // refetch above shows exactly what survived, which is the honest state to land on rather
      // than leaving the user stuck in select mode second-guessing which ones failed. The
      // count-only toast (no per-title detail) mirrors how `deleteThreads` itself only reports
      // ids, not titles, to the caller. Reported to the parent (`RecentsList`) rather than shown
      // here, since `onDone` below unmounts this component before a locally-owned toast could
      // ever render.
      if (failedIds.length > 0) {
        onPartialFailure(failedIds.length);
      }
      onDone();
    },
  });

  return (
    <AlertDialog.Root open={open} onOpenChange={onOpenChange}>
      <AlertDialog.Portal>
        <AlertDialog.Overlay className="fixed inset-0 z-50 bg-black/50" />
        <AlertDialog.Content
          className={cn(
            "fixed top-1/2 left-1/2 z-50 w-[min(360px,92vw)] -translate-x-1/2 -translate-y-1/2",
            "rounded-[var(--radius-card)] border border-[var(--border)] bg-[var(--surface)] p-5 shadow-2xl",
          )}
        >
          <AlertDialog.Title className="font-medium text-[15px] text-[var(--text)]">
            {t("confirmTitle", { count: selectedIds.size })}
          </AlertDialog.Title>
          <AlertDialog.Description className="mt-2 text-[13px] text-[var(--text-muted)]">
            {t("confirmBody")}
          </AlertDialog.Description>
          <div className="mt-4 flex justify-end gap-2">
            <AlertDialog.Cancel asChild>
              <button
                type="button"
                className={cn(
                  "inline-flex min-h-9 items-center rounded-[var(--radius-button)] px-3 text-[13px]",
                  "text-[var(--text)] transition-colors hover:bg-[var(--surface-2)]",
                )}
              >
                {t("cancel")}
              </button>
            </AlertDialog.Cancel>
            <AlertDialog.Action asChild>
              <button
                type="button"
                onClick={() => deleteMutation.mutate()}
                disabled={deleteMutation.isPending}
                className={cn(
                  "inline-flex min-h-9 items-center rounded-[var(--radius-button)] bg-[var(--warn)] px-3 text-[13px] text-[var(--bg)]",
                  "transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50",
                )}
              >
                {t("confirm")}
              </button>
            </AlertDialog.Action>
          </div>
        </AlertDialog.Content>
      </AlertDialog.Portal>
    </AlertDialog.Root>
  );
}

function RecentsSkeleton(): ReactNode {
  return (
    <ul className="flex flex-col gap-1 px-3 py-3" aria-hidden>
      {[0, 1, 2, 3].map((i) => (
        <li key={i} className="h-9 rounded-[var(--radius-button)] bg-[var(--surface-2)]" />
      ))}
    </ul>
  );
}
