"use client";

import { BrainIcon, CheckCircleIcon } from "@phosphor-icons/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "next/navigation";
import { useTranslations } from "next-intl";
import { type ReactNode, useState } from "react";
import { Button } from "@/components/ui/button";
import { Link } from "@/i18n/navigation";
import {
  createMemory,
  getMemorySettings,
  listMemories,
  SettingsApiError,
} from "@/lib/api/settings";
import { contentFromText, keyFromText } from "@/lib/memory";
import { findExistingMemory, parseMemoryProposal } from "@/lib/memory-proposal";

const MEMORY_KEY = ["settings", "memory"] as const;

/**
 * The `memory_proposal` card: the agent's `propose_memory` tool (backend/src/tools/
 * propose_memory_tools.py) only *suggests* something to remember - this card is the confirm gate.
 * Nothing is saved until the user taps 儲存, which calls the ordinary `POST /memory` (auth and the
 * governance scan apply there). The chat it came from is recorded as the memory's source, and a
 * paused memory switch turns the button into a link to turn memory back on.
 */
export function MemoryProposalCard({ data }: { data: Record<string, unknown> }): ReactNode {
  const t = useTranslations("chat.cards.memoryProposal");
  const tm = useTranslations("settings.memory");
  const queryClient = useQueryClient();
  const threadId = useSearchParams().get("thread");
  const [dismissed, setDismissed] = useState(false);

  const proposal = parseMemoryProposal(data);

  const settings = useQuery({
    queryKey: ["settings", "memory-settings"],
    queryFn: getMemorySettings,
    enabled: proposal !== null,
    staleTime: 30_000,
    retry: false,
  });
  const memories = useQuery({
    queryKey: [...MEMORY_KEY, "all", ""],
    queryFn: () => listMemories(),
    enabled: proposal !== null,
    staleTime: 30_000,
    retry: false,
  });

  const save = useMutation({
    mutationFn: createMemory,
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: MEMORY_KEY }),
  });

  if (!proposal) {
    return null;
  }

  const existing = findExistingMemory(memories.data ?? [], proposal);
  const saved = save.isSuccess || existing !== null;
  const paused = settings.data?.enabled === false;

  if (dismissed && !saved) {
    return (
      <p className="mt-3 text-[13px] text-[var(--text-muted)]" aria-live="polite">
        {t("dismissed")}
      </p>
    );
  }

  return (
    <section aria-label={t("title")} className="mt-3 surface-card p-4">
      <div className="flex items-start gap-2">
        <BrainIcon
          size={18}
          weight="regular"
          className="mt-0.5 shrink-0 text-[var(--accent)]"
          aria-hidden
        />
        <div className="min-w-0">
          <p className="text-[12px] text-[var(--text-muted)]">
            {t("title")} · {tm(`typeOption.${proposal.memory_type}`)}
          </p>
          <p className="mt-0.5 whitespace-pre-wrap text-[14px] text-[var(--text)]">
            {proposal.text}
          </p>
        </div>
      </div>

      <ProposalAction
        saved={saved}
        alreadyThere={existing !== null && !save.isSuccess}
        paused={paused}
        pending={save.isPending}
        onSave={() =>
          save.mutate({
            key: keyFromText(proposal.text),
            type: proposal.memory_type,
            content: contentFromText(proposal.text),
            source_ref: threadId ? `thread:${threadId}` : null,
          })
        }
        onDismiss={() => setDismissed(true)}
      />

      {save.isError ? (
        <p role="alert" className="mt-2 text-[12px] text-[var(--warn)]">
          {save.error instanceof SettingsApiError && save.error.status === 422
            ? t("errorRejected")
            : t("errorGeneric")}
        </p>
      ) : null}

      <p className="mt-3 text-[11px] text-[var(--text-muted)] leading-snug">{t("caveat")}</p>
    </section>
  );
}

function ProposalAction({
  saved,
  alreadyThere,
  paused,
  pending,
  onSave,
  onDismiss,
}: {
  saved: boolean;
  alreadyThere: boolean;
  paused: boolean;
  pending: boolean;
  onSave: () => void;
  onDismiss: () => void;
}): ReactNode {
  const t = useTranslations("chat.cards.memoryProposal");
  if (saved) {
    return (
      <p
        className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-1 text-[13px] text-[var(--accent-text)]"
        aria-live="polite"
      >
        <span className="inline-flex items-center gap-1.5">
          <CheckCircleIcon size={16} weight="regular" aria-hidden />
          {alreadyThere ? t("alreadySet") : t("saved")}
        </span>
        <Link href="/settings/memory" className="underline-offset-2 hover:underline">
          {t("manage")}
        </Link>
      </p>
    );
  }
  if (paused) {
    return (
      <p className="mt-3 text-[13px] text-[var(--text-muted)]">
        {t("paused")}{" "}
        <Link
          href="/settings/memory"
          className="text-[var(--accent-text)] underline-offset-2 hover:underline"
        >
          {t("turnOn")}
        </Link>
      </p>
    );
  }
  return (
    <div className="mt-3 flex flex-wrap items-center gap-2">
      <Button variant="filled" size="sm" disabled={pending} onClick={onSave}>
        {pending ? t("saving") : t("save")}
      </Button>
      <Button variant="ghost" size="sm" onClick={onDismiss}>
        {t("dismiss")}
      </Button>
    </div>
  );
}
