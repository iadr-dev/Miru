"use client";

import { CheckCircleIcon, StarIcon } from "@phosphor-icons/react";
import { useTranslations } from "next-intl";
import { type ReactNode, useState } from "react";
import { Select } from "@/components/settings/form-controls";
import { Button } from "@/components/ui/button";
import { useAddWatchlistItem, useWatchlists } from "@/hooks/use-watchlists";
import {
  defaultGroupId,
  groupHolding,
  groupsForProposal,
  parseWatchlistProposal,
} from "@/lib/watchlist-proposal";

/**
 * The `watchlist_proposal` card: the agent's `propose_watchlist_add` tool (backend/src/tools/
 * propose_watchlist_tools.py) only *suggests* adding a symbol - this card is the confirm gate. The
 * user picks the group and taps 加入, which calls the ordinary watchlist add (shared with the star
 * button, so the sidebar updates too). The agent itself has no write path.
 */
export function WatchlistProposalCard({ data }: { data: Record<string, unknown> }): ReactNode {
  const t = useTranslations("chat.cards.watchlistProposal");
  const proposal = parseWatchlistProposal(data);
  const market = proposal?.market ?? "TW";
  const watchlists = useWatchlists(market);
  const add = useAddWatchlistItem(market);
  const [picked, setPicked] = useState("");
  const [dismissed, setDismissed] = useState(false);

  if (!proposal) {
    return null;
  }

  const groups = watchlists.data ?? [];
  const candidates = groupsForProposal(groups, proposal);
  const holding = groupHolding(groups, proposal);
  const added = add.isSuccess || holding !== null;
  const groupId = picked || defaultGroupId(candidates);

  if (dismissed && !added) {
    return (
      <p className="mt-3 text-[13px] text-[var(--text-muted)]" aria-live="polite">
        {t("dismissed")}
      </p>
    );
  }

  return (
    <section
      aria-label={t("title")}
      className="mt-3 rounded-[var(--radius-card)] border border-[var(--border)] bg-[var(--surface)] p-4"
    >
      <div className="flex items-start gap-2">
        <StarIcon
          size={18}
          weight="regular"
          className="mt-0.5 shrink-0 text-[var(--accent)]"
          aria-hidden
        />
        <div className="min-w-0">
          <p className="text-[12px] text-[var(--text-muted)]">{t("title")}</p>
          <p className="font-medium text-[14px] text-[var(--text)]">
            {proposal.symbol} · {proposal.market}
          </p>
        </div>
      </div>

      {added ? (
        <p
          className="mt-3 flex items-center gap-1.5 text-[13px] text-[var(--accent-text)]"
          aria-live="polite"
        >
          <CheckCircleIcon size={16} weight="regular" aria-hidden />
          {holding !== null && !add.isSuccess
            ? t("alreadyIn", { group: holding.group.name })
            : t("added")}
        </p>
      ) : (
        <ProposalActions
          loading={watchlists.isLoading}
          noGroup={candidates.length === 0}
          pending={add.isPending}
          groupId={groupId}
          groups={candidates.map((view) => ({ id: view.group.id, name: view.group.name }))}
          onPick={setPicked}
          onAdd={() => add.mutate({ watchlistId: groupId, symbol: proposal.symbol })}
          onDismiss={() => setDismissed(true)}
        />
      )}

      {add.isError ? (
        <p role="alert" className="mt-2 text-[12px] text-[var(--warn)]">
          {t("errorGeneric")}
        </p>
      ) : null}
    </section>
  );
}

function ProposalActions({
  loading,
  noGroup,
  pending,
  groupId,
  groups,
  onPick,
  onAdd,
  onDismiss,
}: {
  loading: boolean;
  noGroup: boolean;
  pending: boolean;
  groupId: string;
  groups: { id: string; name: string }[];
  onPick: (id: string) => void;
  onAdd: () => void;
  onDismiss: () => void;
}): ReactNode {
  const t = useTranslations("chat.cards.watchlistProposal");
  if (!loading && noGroup) {
    return <p className="mt-3 text-[13px] text-[var(--text-muted)]">{t("noGroup")}</p>;
  }
  return (
    <div className="mt-3 flex flex-wrap items-center gap-2">
      <Select
        aria-label={t("group")}
        value={groupId}
        disabled={loading}
        onChange={(e) => onPick(e.target.value)}
        className="w-44"
      >
        {groups.map((g) => (
          <option key={g.id} value={g.id}>
            {g.name}
          </option>
        ))}
      </Select>
      <Button variant="filled" size="sm" disabled={pending || loading || !groupId} onClick={onAdd}>
        {pending ? t("adding") : t("add")}
      </Button>
      <Button variant="ghost" size="sm" onClick={onDismiss}>
        {t("dismiss")}
      </Button>
    </div>
  );
}
