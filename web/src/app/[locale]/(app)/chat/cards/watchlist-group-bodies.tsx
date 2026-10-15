"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { type ReactNode, useState } from "react";
import { Select } from "@/components/settings/form-controls";
import { Button } from "@/components/ui/button";
import { queryKeys } from "@/lib/api/query-keys";
import {
  deleteWatchlist,
  moveWatchlistItem,
  renameWatchlist,
  reorderWatchlistGroup,
} from "@/lib/api/watchlist";
import { groupNameStyle } from "@/lib/watchlist-color";
import {
  findGroups,
  movePlan,
  reorderPlan,
  type WatchlistProposal,
} from "@/lib/watchlist-proposal";
import type { WatchlistGroupView } from "@/types/watchlist";

type BodyProps = {
  proposal: WatchlistProposal;
  groups: WatchlistGroupView[];
  loading: boolean;
  onDone: () => void;
  onDismiss: () => void;
};

/** Runs one confirmed action: busy state, refreshing the sidebar's cache, and a retryable error. */
function useApply(onDone: () => void): {
  pending: boolean;
  failed: boolean;
  run: (action: () => Promise<void>) => Promise<void>;
} {
  const queryClient = useQueryClient();
  const [pending, setPending] = useState(false);
  const [failed, setFailed] = useState(false);
  const run = async (action: () => Promise<void>): Promise<void> => {
    setPending(true);
    setFailed(false);
    try {
      await action();
      await queryClient.invalidateQueries({ queryKey: queryKeys.watchlists });
      onDone();
    } catch {
      setFailed(true);
      await queryClient.invalidateQueries({ queryKey: queryKeys.watchlists });
    } finally {
      setPending(false);
    }
  };
  return { pending, failed, run };
}

function Buttons({
  label,
  pendingLabel,
  pending,
  disabled,
  failed,
  onApply,
  onDismiss,
}: {
  label: string;
  pendingLabel: string;
  pending: boolean;
  disabled: boolean;
  failed: boolean;
  onApply: () => void;
  onDismiss: () => void;
}): ReactNode {
  const t = useTranslations("chat.cards.watchlistProposal");
  return (
    <>
      <div className="flex flex-wrap items-center gap-2">
        <Button variant="filled" size="sm" disabled={disabled || pending} onClick={onApply}>
          {pending ? pendingLabel : label}
        </Button>
        <Button variant="ghost" size="sm" disabled={pending} onClick={onDismiss}>
          {t("dismiss")}
        </Button>
      </div>
      {failed ? (
        <p role="alert" className="text-[12px] text-[var(--warn)]">
          {t("errorGeneric")}
        </p>
      ) : null}
    </>
  );
}

/** Picks which group a name means when the user has one stock group and one ETF group called the
 * same thing; with a single match it just shows the name. Returns the chosen view. */
function useGroupMatch(
  groups: WatchlistGroupView[],
  proposal: WatchlistProposal,
): {
  matches: WatchlistGroupView[];
  chosen: WatchlistGroupView | undefined;
  setId: (id: string) => void;
} {
  const matches = findGroups(groups, proposal.market, proposal.group_name);
  const [id, setId] = useState("");
  const chosen = matches.find((v) => v.group.id === id) ?? matches[0];
  return { matches, chosen, setId };
}

function MatchPicker({
  matches,
  chosen,
  onPick,
}: {
  matches: WatchlistGroupView[];
  chosen: WatchlistGroupView | undefined;
  onPick: (id: string) => void;
}): ReactNode {
  const t = useTranslations("chat.cards.watchlistProposal");
  if (matches.length < 2) {
    return null;
  }
  return (
    <Select
      aria-label={t("whichGroup")}
      value={chosen?.group.id ?? ""}
      onChange={(e) => onPick(e.target.value)}
      className="w-52"
    >
      {matches.map((v) => (
        <option key={v.group.id} value={v.group.id} style={groupNameStyle(v.group)}>
          {v.group.name} · {t(`groupClass.${v.group.asset_class}`)}
        </option>
      ))}
    </Select>
  );
}

function GroupNotFound({ name }: { name: string | null | undefined }): ReactNode {
  const t = useTranslations("chat.cards.watchlistProposal");
  return (
    <p className="mt-3 text-[13px] text-[var(--text-muted)]">
      {t("groupNotFound", { name: name ?? "" })}
    </p>
  );
}

export function MoveBody({ proposal, groups, loading, onDone, onDismiss }: BodyProps): ReactNode {
  const t = useTranslations("chat.cards.watchlistProposal");
  const { matches, chosen, setId } = useGroupMatch(groups, proposal);
  const { pending, failed, run } = useApply(onDone);
  if (!loading && !chosen) {
    return <GroupNotFound name={proposal.group_name} />;
  }
  const plan = chosen ? movePlan(groups, proposal, chosen) : { steps: [], skipped: [] };

  return (
    <div className="mt-3 flex flex-col gap-2">
      <MatchPicker matches={matches} chosen={chosen} onPick={setId} />
      <ul className="m-0 flex list-none flex-col gap-0.5 p-0 text-[12px] text-[var(--text-muted)]">
        {plan.steps.map((step) => (
          <li key={step.symbol}>
            {t("moveFrom", {
              symbol: step.symbol,
              from: step.fromName,
              to: chosen?.group.name ?? "",
            })}
          </li>
        ))}
        {plan.skipped.map((skip) => (
          <li key={skip.symbol}>{t(`moveSkip.${skip.reason}`, { symbol: skip.symbol })}</li>
        ))}
      </ul>
      <Buttons
        label={t("move")}
        pendingLabel={t("moving")}
        pending={pending}
        disabled={loading || plan.steps.length === 0}
        failed={failed}
        onApply={() =>
          void run(async () => {
            for (const step of plan.steps) {
              await moveWatchlistItem(step.itemId, step.fromId, chosen?.group.id ?? "");
            }
          })
        }
        onDismiss={onDismiss}
      />
    </div>
  );
}

export function RenameBody({ proposal, groups, loading, onDone, onDismiss }: BodyProps): ReactNode {
  const t = useTranslations("chat.cards.watchlistProposal");
  const { matches, chosen, setId } = useGroupMatch(groups, proposal);
  const { pending, failed, run } = useApply(onDone);
  if (!loading && !chosen) {
    return <GroupNotFound name={proposal.group_name} />;
  }
  const newName = proposal.new_name?.trim() ?? "";
  const clash = findGroups(groups, proposal.market, newName).some(
    (v) => v.group.asset_class === chosen?.group.asset_class && v.group.id !== chosen?.group.id,
  );

  return (
    <div className="mt-3 flex flex-col gap-2">
      <MatchPicker matches={matches} chosen={chosen} onPick={setId} />
      <p className="text-[13px] text-[var(--text)]">
        {t("renameTo", { from: chosen?.group.name ?? "", to: newName })}
      </p>
      {clash ? <p className="text-[12px] text-[var(--warn)]">{t("nameTaken")}</p> : null}
      <Buttons
        label={t("rename")}
        pendingLabel={t("renaming")}
        pending={pending}
        disabled={loading || !chosen || newName === "" || clash}
        failed={failed}
        onApply={() =>
          void run(async () => {
            if (chosen) {
              await renameWatchlist(chosen.group.id, newName, chosen.group.version);
            }
          })
        }
        onDismiss={onDismiss}
      />
    </div>
  );
}

export function DeleteBody({ proposal, groups, loading, onDone, onDismiss }: BodyProps): ReactNode {
  const t = useTranslations("chat.cards.watchlistProposal");
  const { matches, chosen, setId } = useGroupMatch(groups, proposal);
  const { pending, failed, run } = useApply(onDone);
  if (!loading && !chosen) {
    return <GroupNotFound name={proposal.group_name} />;
  }

  return (
    <div className="mt-3 flex flex-col gap-2">
      <MatchPicker matches={matches} chosen={chosen} onPick={setId} />
      <p className="text-[13px] text-[var(--warn)]">
        {t("deleteWarning", {
          name: chosen?.group.name ?? "",
          count: chosen?.items.length ?? 0,
        })}
      </p>
      <Buttons
        label={t("delete")}
        pendingLabel={t("deleting")}
        pending={pending}
        disabled={loading || !chosen}
        failed={failed}
        onApply={() =>
          void run(async () => {
            if (chosen) {
              await deleteWatchlist(chosen.group.id);
            }
          })
        }
        onDismiss={onDismiss}
      />
    </div>
  );
}

export function ReorderBody({
  proposal,
  groups,
  loading,
  onDone,
  onDismiss,
}: BodyProps): ReactNode {
  const t = useTranslations("chat.cards.watchlistProposal");
  const { matches, chosen, setId } = useGroupMatch(groups, proposal);
  const { pending, failed, run } = useApply(onDone);
  if (!loading && !chosen) {
    return <GroupNotFound name={proposal.group_name} />;
  }
  const position = proposal.position ?? "first";
  const plan = chosen ? reorderPlan(groups, chosen, position) : null;

  return (
    <div className="mt-3 flex flex-col gap-2">
      <MatchPicker matches={matches} chosen={chosen} onPick={setId} />
      <p className="text-[13px] text-[var(--text)]">
        {plan?.noop
          ? t("alreadyThere", { name: chosen?.group.name ?? "" })
          : t(`reorderTo.${position}`, { name: chosen?.group.name ?? "" })}
      </p>
      <Buttons
        label={t("reorder")}
        pendingLabel={t("reordering")}
        pending={pending}
        disabled={loading || !chosen || plan === null || plan.noop}
        failed={failed}
        onApply={() =>
          void run(async () => {
            if (chosen && plan) {
              await reorderWatchlistGroup(
                chosen.group.id,
                plan.afterId,
                plan.beforeId,
                chosen.group.version,
              );
            }
          })
        }
        onDismiss={onDismiss}
      />
    </div>
  );
}
