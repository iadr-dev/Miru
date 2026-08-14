"use client";

import { DotsThreeIcon, PencilSimpleIcon, StarIcon, TrashIcon } from "@phosphor-icons/react";
import * as AlertDialog from "@radix-ui/react-alert-dialog";
import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "next/navigation";
import { useTranslations } from "next-intl";
import { type ReactNode, useState } from "react";
import { useNewThreadStore } from "@/components/shell/new-thread-store";
import { useTypewriterOnce } from "@/hooks/use-typewriter-once";
import { useRouter } from "@/i18n/navigation";
import { deleteThread, listThreads, patchThread } from "@/lib/api/threads";
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
  const query = useQuery({ queryKey: ["threads"], queryFn: ({ signal }) => listThreads(signal) });
  const newThread = useNewThreadStore();

  const onSelect = (threadId: string): void => {
    router.push(`/chat?thread=${encodeURIComponent(threadId)}`);
  };

  const invalidate = (): void => {
    void queryClient.invalidateQueries({ queryKey: ["threads"] });
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

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-y-auto px-3">
      {showPendingPlaceholder ? <PendingThreadRow /> : null}
      {starred.length > 0 ? (
        <ThreadSection
          label={t("starred")}
          threads={starred}
          activeThreadId={activeThreadId}
          revealingThreadId={revealingThreadId}
          onSelect={onSelect}
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
          onSelect={onSelect}
          onChanged={invalidate}
        />
      ))}
    </div>
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
  onSelect,
  onChanged,
}: {
  label: string;
  threads: Thread[];
  activeThreadId: string | null;
  revealingThreadId: string | null;
  onSelect: (threadId: string) => void;
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
            onSelect={() => onSelect(thread.id)}
            onChanged={onChanged}
          />
        ))}
      </ul>
    </div>
  );
}

function ThreadRow({
  thread,
  active,
  revealing,
  onSelect,
  onChanged,
}: {
  thread: Thread;
  active: boolean;
  revealing: boolean;
  onSelect: () => void;
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
        onClick={onSelect}
        aria-current={active ? "true" : undefined}
        title={title}
        className={cn(
          "flex min-h-9 w-full items-center rounded-[var(--radius-button)] px-3 pr-8 text-left transition-colors",
          active
            ? "bg-[var(--accent-weak)] text-[var(--text)]"
            : "text-[var(--text-muted)] hover:bg-[var(--surface-2)] hover:text-[var(--text)]",
        )}
      >
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

      <DeleteThreadDialog
        thread={thread}
        open={deleteOpen}
        onOpenChange={setDeleteOpen}
        onChanged={onChanged}
      />
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

function RecentsSkeleton(): ReactNode {
  return (
    <ul className="flex flex-col gap-1 px-3 py-3" aria-hidden>
      {[0, 1, 2, 3].map((i) => (
        <li key={i} className="h-9 rounded-[var(--radius-button)] bg-[var(--surface-2)]" />
      ))}
    </ul>
  );
}
