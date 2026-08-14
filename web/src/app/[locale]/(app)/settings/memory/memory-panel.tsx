"use client";

import { PlusIcon, TrashIcon } from "@phosphor-icons/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { type FormEvent, type ReactNode, useState } from "react";
import { Select, TextInput } from "@/components/settings/form-controls";
import { SettingsRow, SettingsSection } from "@/components/settings/settings-section";
import { SettingsError, SettingsLoading } from "@/components/settings/settings-states";
import { Button } from "@/components/ui/button";
import { createMemory, deleteMemory, listMemories } from "@/lib/api/settings";
import {
  MEMORY_SCOPES,
  type MemoryScope,
  WRITABLE_MEMORY_TYPES,
  type WritableMemoryType,
} from "@/types/settings";

/** Memory — list + delete is the must-have (docs/web/settings/README.md); a minimal create form
 * is included since key/type/content are simple fields. */
export function MemoryPanel(): ReactNode {
  const t = useTranslations("settings.memory");
  const tc = useTranslations("settings.common");
  const queryClient = useQueryClient();

  const [scope, setScope] = useState<MemoryScope | "all">("all");
  const [creating, setCreating] = useState(false);

  const query = useQuery({
    queryKey: ["settings", "memory", scope],
    queryFn: () => listMemories(scope === "all" ? undefined : scope),
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => deleteMemory(id),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["settings", "memory"] }),
  });

  return (
    <SettingsSection title={t("title")} description={t("description")}>
      <SettingsRow label={t("scopeFilter")}>
        <Select
          aria-label={t("scopeFilter")}
          value={scope}
          onChange={(e) => setScope(e.target.value as MemoryScope | "all")}
          className="w-40"
        >
          <option value="all">{t("scopeAll")}</option>
          {MEMORY_SCOPES.map((s) => (
            <option key={s} value={s}>
              {t(`scopeOption.${s}`)}
            </option>
          ))}
        </Select>
      </SettingsRow>

      {query.isLoading ? (
        <div className="pt-4">
          <SettingsLoading rows={3} />
        </div>
      ) : query.isError ? (
        <div className="pt-4">
          <SettingsError message={tc("errorLoad")} onRetry={() => void query.refetch()} />
        </div>
      ) : (query.data ?? []).length === 0 && !creating ? (
        <div className="py-6 text-center">
          <p className="text-[14px] text-[var(--text-muted)]">{t("empty")}</p>
          <Button variant="filled" size="sm" className="mt-3" onClick={() => setCreating(true)}>
            <PlusIcon size={16} weight="regular" aria-hidden />
            {t("createCta")}
          </Button>
        </div>
      ) : (
        <>
          <ul className="divide-y divide-[var(--border)]">
            {(query.data ?? []).map((memory) => (
              <li key={memory.id} className="flex items-start justify-between gap-3 py-3">
                <div className="min-w-0">
                  <p className="font-medium text-[14px] text-[var(--text)]">
                    {memory.key} · {t(`typeOption.${memory.type}`)}
                  </p>
                  <p className="truncate text-[12px] text-[var(--text-muted)]">
                    {JSON.stringify(memory.content)}
                  </p>
                </div>
                <button
                  type="button"
                  onClick={() => deleteMutation.mutate(memory.id)}
                  disabled={deleteMutation.isPending}
                  aria-label={t("deleteLabel", { key: memory.key })}
                  className="inline-flex size-11 shrink-0 items-center justify-center rounded-[var(--radius-button)] text-[var(--text-muted)] hover:bg-[var(--surface-2)] hover:text-[var(--warn)]"
                >
                  <TrashIcon size={18} weight="regular" />
                </button>
              </li>
            ))}
          </ul>
          {!creating ? (
            <Button variant="outline" size="sm" className="mt-4" onClick={() => setCreating(true)}>
              <PlusIcon size={16} weight="regular" aria-hidden />
              {t("createCta")}
            </Button>
          ) : null}
        </>
      )}

      {creating ? (
        <div className="mt-4">
          <MemoryForm onDone={() => setCreating(false)} />
        </div>
      ) : null}
    </SettingsSection>
  );
}

function MemoryForm({ onDone }: { onDone: () => void }): ReactNode {
  const t = useTranslations("settings.memory.form");
  const queryClient = useQueryClient();

  const [key, setKey] = useState("");
  const [type, setType] = useState<WritableMemoryType>("preference");
  const [content, setContent] = useState("");
  const [error, setError] = useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: createMemory,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["settings", "memory"] });
      onDone();
    },
    onError: () => setError(t("errorGeneric")),
  });

  const onSubmit = (e: FormEvent<HTMLFormElement>): void => {
    e.preventDefault();
    if (!key.trim()) {
      setError(t("errorKeyRequired"));
      return;
    }
    let parsedContent: Record<string, unknown> = {};
    if (content.trim()) {
      try {
        const parsed: unknown = JSON.parse(content);
        if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
          setError(t("errorContentInvalid"));
          return;
        }
        parsedContent = parsed as Record<string, unknown>;
      } catch {
        setError(t("errorContentInvalid"));
        return;
      }
    }
    setError(null);
    mutation.mutate({ key: key.trim(), type, content: parsedContent });
  };

  return (
    <form
      aria-label={t("title")}
      onSubmit={onSubmit}
      className="space-y-4 rounded-[var(--radius-input)] border border-[var(--border)] bg-[var(--surface-2)] p-4"
    >
      <div>
        <label className="mb-1 block text-[13px] text-[var(--text-muted)]" htmlFor="memory-key">
          {t("key")}
        </label>
        <TextInput id="memory-key" value={key} onChange={(e) => setKey(e.target.value)} />
      </div>
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
          value={content}
          onChange={(e) => setContent(e.target.value)}
          placeholder='{"note": "..."}'
          rows={3}
          className="!outline-none w-full rounded-[var(--radius-input)] border border-[var(--border)] bg-[var(--surface)] p-2 text-[13px] text-[var(--text)] focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
        />
      </div>
      {error ? (
        <p role="alert" className="text-[12px] text-[var(--warn)]">
          {error}
        </p>
      ) : null}
      <div className="flex justify-end gap-2">
        <Button type="button" variant="ghost" size="sm" onClick={onDone}>
          {t("cancel")}
        </Button>
        <Button type="submit" variant="filled" size="sm" disabled={mutation.isPending}>
          {t("submit")}
        </Button>
      </div>
    </form>
  );
}
