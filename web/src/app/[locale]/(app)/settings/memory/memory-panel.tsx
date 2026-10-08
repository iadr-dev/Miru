"use client";

import { MagnifyingGlassIcon, PencilSimpleIcon, PlusIcon, TrashIcon } from "@phosphor-icons/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useLocale, useTranslations } from "next-intl";
import { type FormEvent, type ReactNode, useEffect, useState } from "react";
import { ConfirmDialog } from "@/components/settings/confirm-dialog";
import { Select, TextInput, ToggleSwitch } from "@/components/settings/form-controls";
import { SettingsRow, SettingsSection } from "@/components/settings/settings-section";
import { SettingsError, SettingsLoading } from "@/components/settings/settings-states";
import { Button } from "@/components/ui/button";
import { Link } from "@/i18n/navigation";
import { fetchMe } from "@/lib/api/account";
import {
  clearMemories,
  createMemory,
  deleteMemory,
  getMemorySettings,
  listMemories,
  listPersonalityPresets,
  SettingsApiError,
  setMemoryEnabled,
  updateMemory,
} from "@/lib/api/settings";
import { formatDate } from "@/lib/billing-format";
import { contentFromText, keyFromText, memoryText, sourceThreadId } from "@/lib/memory";
import { parseServerDate } from "@/lib/parse-server-date";
import {
  MEMORY_SCOPES,
  type Memory,
  type MemoryScope,
  WRITABLE_MEMORY_TYPES,
  type WritableMemoryType,
} from "@/types/settings";

const SEARCH_DEBOUNCE_MS = 250;

/** 記憶 - what Miru remembers about the user's analysis focus: readable cards the user can search,
 * edit, delete or clear, an on/off switch, and where each memory came from and when it was last
 * used. Tone is deliberately not here (語氣設定 owns it). */
export function MemoryPanel(): ReactNode {
  const t = useTranslations("settings.memory");
  const tc = useTranslations("settings.common");
  const queryClient = useQueryClient();

  const [scope, setScope] = useState<MemoryScope | "all">("all");
  const [search, setSearch] = useState("");
  const [debounced, setDebounced] = useState("");
  const [creating, setCreating] = useState(false);
  const [confirmClear, setConfirmClear] = useState(false);

  useEffect(() => {
    const id = window.setTimeout(() => setDebounced(search), SEARCH_DEBOUNCE_MS);
    return () => window.clearTimeout(id);
  }, [search]);

  const settingsQuery = useQuery({
    queryKey: ["settings", "memory-settings"],
    queryFn: getMemorySettings,
  });
  const query = useQuery({
    queryKey: ["settings", "memory", scope, debounced],
    queryFn: () => listMemories(scope === "all" ? undefined : scope, debounced),
  });
  const refresh = (): void =>
    void queryClient.invalidateQueries({ queryKey: ["settings", "memory"] });

  const enabledMutation = useMutation({
    mutationFn: setMemoryEnabled,
    onSuccess: () =>
      void queryClient.invalidateQueries({ queryKey: ["settings", "memory-settings"] }),
  });
  const clearMutation = useMutation({
    mutationFn: () => clearMemories(scope === "all" ? undefined : scope),
    onSuccess: () => {
      setConfirmClear(false);
      refresh();
    },
  });

  const enabled = settingsQuery.data?.enabled ?? true;
  const memories = query.data ?? [];

  return (
    <div className="flex flex-col gap-6">
      <SettingsSection title={t("title")} description={t("description")}>
        <SettingsRow label={t("enabled")} hint={t("enabledHint")}>
          <ToggleSwitch
            checked={enabled}
            onChange={(next) => enabledMutation.mutate(next)}
            label={t("enabled")}
            disabled={settingsQuery.isLoading || enabledMutation.isPending}
          />
        </SettingsRow>
        {enabled ? null : (
          <p role="status" className="py-2 text-[13px] text-[var(--warn)]">
            {t("pausedNotice")}
          </p>
        )}
        <RelatedSettings />
      </SettingsSection>

      <SettingsSection title={t("listTitle")}>
        <div className="flex flex-wrap items-center gap-2 py-2">
          <div className="relative min-w-48 flex-1">
            <MagnifyingGlassIcon
              size={16}
              weight="regular"
              aria-hidden
              className="-translate-y-1/2 pointer-events-none absolute top-1/2 left-3 text-[var(--text-muted)]"
            />
            <TextInput
              type="search"
              aria-label={t("search")}
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder={t("searchPlaceholder")}
              className="w-full pl-9"
            />
          </div>
          <Select
            aria-label={t("scopeFilter")}
            value={scope}
            onChange={(e) => setScope(e.target.value as MemoryScope | "all")}
            className="w-36"
          >
            <option value="all">{t("scopeAll")}</option>
            {MEMORY_SCOPES.map((s) => (
              <option key={s} value={s}>
                {t(`scopeOption.${s}`)}
              </option>
            ))}
          </Select>
        </div>

        <MemoryList
          loading={query.isLoading}
          error={query.isError}
          onRetry={() => void query.refetch()}
          memories={memories}
          searching={debounced.trim() !== ""}
          errorMessage={tc("errorLoad")}
          onChanged={refresh}
        />

        <div className="flex flex-wrap items-center justify-between gap-2 pt-4">
          {creating ? null : (
            <Button variant="outline" size="sm" onClick={() => setCreating(true)}>
              <PlusIcon size={16} weight="regular" aria-hidden />
              {t("createCta")}
            </Button>
          )}
          {memories.length > 0 ? (
            <Button variant="ghost" size="sm" onClick={() => setConfirmClear(true)}>
              <TrashIcon size={16} weight="regular" aria-hidden />
              {t("clearAll")}
            </Button>
          ) : null}
        </div>
        {creating ? (
          <div className="mt-4">
            <MemoryForm
              onDone={() => {
                setCreating(false);
                refresh();
              }}
              onCancel={() => setCreating(false)}
            />
          </div>
        ) : null}
      </SettingsSection>

      <ConfirmDialog
        open={confirmClear}
        title={t("clearAll")}
        description={t("clearConfirm", { count: memories.length })}
        confirmLabel={t("clearConfirmYes")}
        cancelLabel={t("cancel")}
        onConfirm={() => clearMutation.mutate()}
        onCancel={() => setConfirmClear(false)}
      />
    </div>
  );
}

/** Where tone and the watchlist live, so memory is not mistaken for either. */
function RelatedSettings(): ReactNode {
  const t = useTranslations("settings.memory");
  const locale = useLocale();
  const me = useQuery({ queryKey: ["me"], queryFn: ({ signal }) => fetchMe(signal) });
  const presets = useQuery({
    queryKey: ["settings", "personality-presets"],
    queryFn: listPersonalityPresets,
  });
  const current = presets.data?.find((p) => p.id === me.data?.user.personality_id);
  const toneName = current ? (locale === "zh-TW" ? current.name_zh : current.name_en) : "—";

  return (
    <>
      <SettingsRow label={t("toneTitle")} hint={t("toneHint")}>
        <div className="flex flex-col items-end gap-1">
          <span className="text-[14px] text-[var(--text)]">{toneName}</span>
          <Link
            href="/settings/personality"
            className="text-[13px] text-[var(--accent-text)] underline-offset-2 hover:underline"
          >
            {t("toneLink")}
          </Link>
        </div>
      </SettingsRow>
      <p className="py-3 text-[12px] text-[var(--text-muted)] leading-relaxed">
        {t("watchlistHint")}
      </p>
    </>
  );
}

function MemoryList({
  loading,
  error,
  onRetry,
  memories,
  searching,
  errorMessage,
  onChanged,
}: {
  loading: boolean;
  error: boolean;
  onRetry: () => void;
  memories: Memory[];
  searching: boolean;
  errorMessage: string;
  onChanged: () => void;
}): ReactNode {
  const t = useTranslations("settings.memory");
  if (loading) {
    return <SettingsLoading rows={3} />;
  }
  if (error) {
    return <SettingsError message={errorMessage} onRetry={onRetry} />;
  }
  if (memories.length === 0) {
    return (
      <p className="py-6 text-center text-[14px] text-[var(--text-muted)]">
        {searching ? t("noResults") : t("empty")}
      </p>
    );
  }
  return (
    <ul className="divide-y divide-[var(--border)]">
      {memories.map((memory) => (
        <MemoryItem key={memory.id} memory={memory} onChanged={onChanged} />
      ))}
    </ul>
  );
}

function MemoryItem({ memory, onChanged }: { memory: Memory; onChanged: () => void }): ReactNode {
  const t = useTranslations("settings.memory");
  const [editing, setEditing] = useState(false);

  const removeMutation = useMutation({
    mutationFn: () => deleteMemory(memory.id),
    onSuccess: onChanged,
  });

  return (
    <li className="py-3">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <p className="mb-1 text-[12px] text-[var(--text-muted)]">
            {t(`typeOption.${memory.type}`)}
          </p>
          {editing ? (
            <MemoryEditor
              memory={memory}
              onDone={() => {
                setEditing(false);
                onChanged();
              }}
              onCancel={() => setEditing(false)}
            />
          ) : (
            <p className="whitespace-pre-wrap text-[14px] text-[var(--text)] leading-relaxed">
              {memoryText(memory)}
            </p>
          )}
          <MemoryMeta memory={memory} />
        </div>
        {editing ? null : (
          <div className="flex shrink-0">
            <button
              type="button"
              onClick={() => setEditing(true)}
              aria-label={t("editLabel", { key: memory.key })}
              className="inline-flex size-11 items-center justify-center rounded-[var(--radius-button)] text-[var(--text-muted)] hover:bg-[var(--surface-2)] hover:text-[var(--text)]"
            >
              <PencilSimpleIcon size={18} weight="regular" />
            </button>
            <button
              type="button"
              onClick={() => removeMutation.mutate()}
              disabled={removeMutation.isPending}
              aria-label={t("deleteLabel", { key: memory.key })}
              className="inline-flex size-11 items-center justify-center rounded-[var(--radius-button)] text-[var(--text-muted)] hover:bg-[var(--surface-2)] hover:text-[var(--warn)]"
            >
              <TrashIcon size={18} weight="regular" />
            </button>
          </div>
        )}
      </div>
    </li>
  );
}

/** When it was saved, when and how often it was used, and the chat it came from. */
function MemoryMeta({ memory }: { memory: Memory }): ReactNode {
  const t = useTranslations("settings.memory");
  const locale = useLocale();
  const day = (iso: string): string => formatDate(parseServerDate(iso).toISOString(), locale) ?? "";
  const thread = sourceThreadId(memory);

  return (
    <p className="mt-1.5 flex flex-wrap gap-x-3 gap-y-0.5 text-[12px] text-[var(--text-muted)]">
      <span>{t("updated", { date: day(memory.updated_at) })}</span>
      <span>
        {memory.last_used_at ? t("lastUsed", { date: day(memory.last_used_at) }) : t("neverUsed")}
      </span>
      {memory.use_count > 0 ? <span>{t("usedTimes", { count: memory.use_count })}</span> : null}
      {thread ? (
        <Link
          href={`/chat?thread=${encodeURIComponent(thread)}`}
          className="text-[var(--accent-text)] underline-offset-2 hover:underline"
        >
          {t("fromChat")}
        </Link>
      ) : null}
    </p>
  );
}

const textareaClass =
  "!outline-none w-full rounded-[var(--radius-input)] border border-[var(--border)] bg-[var(--surface)] p-2 text-[14px] text-[var(--text)] focus-visible:ring-2 focus-visible:ring-[var(--accent)]";

function MemoryEditor({
  memory,
  onDone,
  onCancel,
}: {
  memory: Memory;
  onDone: () => void;
  onCancel: () => void;
}): ReactNode {
  const t = useTranslations("settings.memory");
  const tf = useTranslations("settings.memory.form");
  const [text, setText] = useState(memoryText(memory));
  const [error, setError] = useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: () => updateMemory(memory.id, contentFromText(text)),
    onSuccess: onDone,
    onError: (err) => setError(saveErrorMessage(err, tf)),
  });

  return (
    <div className="space-y-2">
      <textarea
        aria-label={t("editLabel", { key: memory.key })}
        value={text}
        onChange={(e) => setText(e.target.value)}
        rows={3}
        className={textareaClass}
      />
      {error ? (
        <p role="alert" className="text-[12px] text-[var(--warn)]">
          {error}
        </p>
      ) : null}
      <div className="flex gap-2">
        <Button
          variant="filled"
          size="sm"
          disabled={mutation.isPending || text.trim() === ""}
          onClick={() => mutation.mutate()}
        >
          {t("save")}
        </Button>
        <Button variant="ghost" size="sm" onClick={onCancel}>
          {t("cancel")}
        </Button>
      </div>
    </div>
  );
}

/** The server refuses keys and buy/sell advice (422 `memory_rejected`); anything else is generic. */
function saveErrorMessage(err: unknown, t: (key: string) => string): string {
  return err instanceof SettingsApiError && err.status === 422
    ? t("errorRejected")
    : t("errorGeneric");
}

function MemoryForm({ onDone, onCancel }: { onDone: () => void; onCancel: () => void }): ReactNode {
  const t = useTranslations("settings.memory.form");
  const tm = useTranslations("settings.memory");

  const [type, setType] = useState<WritableMemoryType>("preference");
  const [text, setText] = useState("");
  const [error, setError] = useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: createMemory,
    onSuccess: onDone,
    onError: (err) => setError(saveErrorMessage(err, t)),
  });

  const onSubmit = (e: FormEvent<HTMLFormElement>): void => {
    e.preventDefault();
    if (!text.trim()) {
      setError(t("errorKeyRequired"));
      return;
    }
    setError(null);
    mutation.mutate({ key: keyFromText(text), type, content: contentFromText(text) });
  };

  return (
    <form
      aria-label={t("title")}
      onSubmit={onSubmit}
      className="space-y-4 rounded-[var(--radius-input)] border border-[var(--border)] bg-[var(--surface-2)] p-4"
    >
      <div>
        <label className="mb-1 block text-[13px] text-[var(--text-muted)]" htmlFor="memory-type">
          {t("type")}
        </label>
        <Select
          id="memory-type"
          value={type}
          onChange={(e) => setType(e.target.value as WritableMemoryType)}
        >
          {WRITABLE_MEMORY_TYPES.map((v) => (
            <option key={v} value={v}>
              {t(`typeOption.${v}`)}
            </option>
          ))}
        </Select>
      </div>
      <div>
        <label className="mb-1 block text-[13px] text-[var(--text-muted)]" htmlFor="memory-content">
          {t("content")}
        </label>
        <textarea
          id="memory-content"
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder={tm("textExample")}
          rows={3}
          className={textareaClass}
        />
      </div>
      {error ? (
        <p role="alert" className="text-[12px] text-[var(--warn)]">
          {error}
        </p>
      ) : null}
      <div className="flex justify-end gap-2">
        <Button type="button" variant="ghost" size="sm" onClick={onCancel}>
          {t("cancel")}
        </Button>
        <Button type="submit" variant="filled" size="sm" disabled={mutation.isPending}>
          {t("submit")}
        </Button>
      </div>
    </form>
  );
}
