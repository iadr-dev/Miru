"use client";

import { CheckCircleIcon, StarIcon } from "@phosphor-icons/react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { type ReactNode, useState } from "react";
import {
  DeleteBody,
  MoveBody,
  RenameBody,
  ReorderBody,
} from "@/app/[locale]/(app)/chat/cards/watchlist-group-bodies";
import { Select, TextInput } from "@/components/settings/form-controls";
import { Button } from "@/components/ui/button";
import { useWatchlists } from "@/hooks/use-watchlists";
import { getSymbolInfo } from "@/lib/api/market";
import { queryKeys } from "@/lib/api/query-keys";
import { addWatchlistItem, createWatchlist, removeWatchlistItem } from "@/lib/api/watchlist";
import { FRESHNESS } from "@/lib/freshness";
import { groupNameStyle } from "@/lib/watchlist-color";
import {
  addAlreadyDone,
  choiceReady,
  type GroupChoice,
  initialChoice,
  isGroupAction,
  matchingGroups,
  parseWatchlistProposal,
  removalTargets,
  type SymbolBatch,
  splitByClass,
  symbolsToAdd,
  type WatchlistProposal,
} from "@/lib/watchlist-proposal";
import type { WatchlistAssetClass, WatchlistGroupView } from "@/types/watchlist";

const NEW_GROUP = "__new__";

type Choices = Partial<Record<WatchlistAssetClass, GroupChoice>>;

/**
 * The `watchlist_proposal` card: the agent's `propose_watchlist_change` tool (backend/src/tools/
 * propose_watchlist_tools.py) only *suggests* adding or removing symbols - this card is the confirm
 * gate. The user checks each name (resolved from the roster, so a wrong guessed code is visible),
 * picks - or creates - the group, and taps the button, which calls the ordinary watchlist routes
 * (shared cache with the sidebar, so it updates too). The agent itself has no write path.
 */
export function WatchlistProposalCard({ data }: { data: Record<string, unknown> }): ReactNode {
  const t = useTranslations("chat.cards.watchlistProposal");
  const proposal = parseWatchlistProposal(data);
  const market = proposal?.market ?? "TW";
  const watchlists = useWatchlists(market);
  const info = useQuery({
    queryKey: queryKeys.marketRead("symbol_info", market, ...(proposal?.symbols ?? [])),
    queryFn: () => getSymbolInfo(proposal?.symbols ?? [], market),
    enabled: proposal !== null && proposal.symbols.length > 0,
    staleTime: FRESHNESS.reference,
    retry: false,
  });
  const [dismissed, setDismissed] = useState(false);
  const [done, setDone] = useState(false);

  if (!proposal) {
    return null;
  }

  const groups = watchlists.data ?? [];
  const names = new Map((info.data?.rows ?? []).map((row) => [row.symbol, row.name]));
  const rosterKnown = info.data?.data_available === true;
  const unknown = rosterKnown ? proposal.symbols.filter((s) => !names.has(s)) : [];
  const isDone =
    done || (proposal.action === "add" && !proposal.group_name && addAlreadyDone(groups, proposal));

  if (dismissed && !isDone) {
    return (
      <p className="mt-3 text-[13px] text-[var(--text-muted)]" aria-live="polite">
        {t("dismissed")}
      </p>
    );
  }

  return (
    <section aria-label={t(`title.${proposal.action}`)} className="surface-card mt-3 p-4">
      <div className="flex items-start gap-2">
        <StarIcon
          size={18}
          weight="regular"
          className="mt-0.5 shrink-0 text-[var(--accent)]"
          aria-hidden
        />
        <div className="min-w-0 flex-1">
          <p className="text-[12px] text-[var(--text-muted)]">{t(`title.${proposal.action}`)}</p>
          {isGroupAction(proposal.action) ? (
            <p className="mt-0.5 font-medium text-[14px] text-[var(--text)]">
              {proposal.group_name} · {proposal.market}
            </p>
          ) : (
            <SymbolList proposal={proposal} names={names} unknown={unknown} />
          )}
        </div>
      </div>

      {isDone ? (
        <p
          className="mt-3 flex items-center gap-1.5 text-[13px] text-[var(--accent-text)]"
          aria-live="polite"
        >
          <CheckCircleIcon size={16} weight="regular" aria-hidden />
          {t(`done.${proposal.action}`)}
        </p>
      ) : (
        <ProposalBody
          proposal={proposal}
          groups={groups}
          loading={watchlists.isLoading}
          unknown={unknown}
          onDone={() => setDone(true)}
          onDismiss={() => setDismissed(true)}
        />
      )}
    </section>
  );
}

function SymbolList({
  proposal,
  names,
  unknown,
}: {
  proposal: WatchlistProposal;
  names: Map<string, string>;
  unknown: string[];
}): ReactNode {
  const t = useTranslations("chat.cards.watchlistProposal");
  return (
    <ul className="m-0 mt-0.5 flex list-none flex-col gap-0.5 p-0">
      {proposal.symbols.map((symbol) => (
        <li key={symbol} className="text-[14px] text-[var(--text)]">
          <span className="font-medium">{names.get(symbol) ?? symbol}</span>{" "}
          <span className="font-num text-[var(--text-muted)]">
            {symbol} · {proposal.market}
          </span>
          {unknown.includes(symbol) ? (
            <span className="ml-2 text-[12px] text-[var(--warn)]">{t("unknownSymbol")}</span>
          ) : null}
        </li>
      ))}
    </ul>
  );
}

function ProposalBody({
  proposal,
  groups,
  loading,
  unknown,
  onDone,
  onDismiss,
}: {
  proposal: WatchlistProposal;
  groups: WatchlistGroupView[];
  loading: boolean;
  unknown: string[];
  onDone: () => void;
  onDismiss: () => void;
}): ReactNode {
  const shared = { proposal, groups, loading, onDone, onDismiss };
  switch (proposal.action) {
    case "add":
      return <AddBody {...shared} unknown={unknown} />;
    case "remove":
      return <RemoveBody {...shared} />;
    case "move":
      return <MoveBody {...shared} />;
    case "rename_group":
      return <RenameBody {...shared} />;
    case "delete_group":
      return <DeleteBody {...shared} />;
    case "reorder_group":
      return <ReorderBody {...shared} />;
  }
}

function AddBody({
  proposal,
  groups,
  loading,
  unknown,
  onDone,
  onDismiss,
}: {
  proposal: WatchlistProposal;
  groups: WatchlistGroupView[];
  loading: boolean;
  unknown: string[];
  onDone: () => void;
  onDismiss: () => void;
}): ReactNode {
  const t = useTranslations("chat.cards.watchlistProposal");
  const queryClient = useQueryClient();
  const [picked, setPicked] = useState<Choices>({});
  const [pending, setPending] = useState(false);
  const [failed, setFailed] = useState(false);

  // A symbol the roster does not know is left out: the user sees it flagged and nothing is added.
  const batches = splitByClass(proposal.symbols.filter((s) => !unknown.includes(s)));
  const choiceFor = (assetClass: WatchlistAssetClass): GroupChoice =>
    picked[assetClass] ?? initialChoice(groups, proposal, assetClass);
  const ready = batches.length > 0 && batches.every((b) => choiceReady(choiceFor(b.assetClass)));

  const apply = async (): Promise<void> => {
    setPending(true);
    setFailed(false);
    try {
      for (const batch of batches) {
        const choice = choiceFor(batch.assetClass);
        const groupId =
          choice.kind === "existing"
            ? choice.id
            : (await createWatchlist(choice.name.trim(), proposal.market, batch.assetClass)).id;
        for (const symbol of symbolsToAdd(groups, choice, batch)) {
          await addWatchlistItem(groupId, symbol);
        }
      }
      await queryClient.invalidateQueries({ queryKey: queryKeys.watchlists });
      onDone();
    } catch {
      setFailed(true);
      await queryClient.invalidateQueries({ queryKey: queryKeys.watchlists });
    } finally {
      setPending(false);
    }
  };

  return (
    <div className="mt-3 flex flex-col gap-2">
      {batches.map((batch) => (
        <GroupPicker
          key={batch.assetClass}
          batch={batch}
          groups={groups}
          market={proposal.market}
          choice={choiceFor(batch.assetClass)}
          onChange={(next) => setPicked((prev) => ({ ...prev, [batch.assetClass]: next }))}
          disabled={loading || pending}
        />
      ))}
      <div className="flex flex-wrap items-center gap-2">
        <Button variant="filled" size="sm" disabled={!ready || pending || loading} onClick={apply}>
          {pending ? t("adding") : t("add")}
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
    </div>
  );
}

function GroupPicker({
  batch,
  groups,
  market,
  choice,
  onChange,
  disabled,
}: {
  batch: SymbolBatch;
  groups: WatchlistGroupView[];
  market: WatchlistProposal["market"];
  choice: GroupChoice;
  onChange: (next: GroupChoice) => void;
  disabled: boolean;
}): ReactNode {
  const t = useTranslations("chat.cards.watchlistProposal");
  const candidates = matchingGroups(groups, market, batch.assetClass);
  const label = t(`groupFor.${batch.assetClass}`);
  return (
    <div className="flex flex-wrap items-center gap-2">
      <Select
        aria-label={label}
        value={choice.kind === "existing" ? choice.id : NEW_GROUP}
        disabled={disabled}
        onChange={(e) =>
          onChange(
            e.target.value === NEW_GROUP
              ? { kind: "new", name: choice.kind === "new" ? choice.name : "" }
              : { kind: "existing", id: e.target.value },
          )
        }
        className="w-44"
      >
        {candidates.map((v) => (
          <option key={v.group.id} value={v.group.id} style={groupNameStyle(v.group)}>
            {v.group.name}
          </option>
        ))}
        <option value={NEW_GROUP}>{t("newGroup")}</option>
      </Select>
      {choice.kind === "new" ? (
        <TextInput
          aria-label={t("newGroupName")}
          placeholder={t("newGroupName")}
          maxLength={30}
          value={choice.name}
          disabled={disabled}
          onChange={(e) => onChange({ kind: "new", name: e.target.value })}
          className="w-44"
        />
      ) : null}
    </div>
  );
}

function RemoveBody({
  proposal,
  groups,
  loading,
  onDone,
  onDismiss,
}: {
  proposal: WatchlistProposal;
  groups: WatchlistGroupView[];
  loading: boolean;
  onDone: () => void;
  onDismiss: () => void;
}): ReactNode {
  const t = useTranslations("chat.cards.watchlistProposal");
  const queryClient = useQueryClient();
  const [pending, setPending] = useState(false);
  const [failed, setFailed] = useState(false);
  const targets = removalTargets(groups, proposal);
  const present = targets.filter((target) => target.groups.length > 0);

  const apply = async (): Promise<void> => {
    setPending(true);
    setFailed(false);
    try {
      for (const target of present) {
        for (const g of target.groups) {
          await removeWatchlistItem(g.id, target.symbol);
        }
      }
      await queryClient.invalidateQueries({ queryKey: queryKeys.watchlists });
      onDone();
    } catch {
      setFailed(true);
      await queryClient.invalidateQueries({ queryKey: queryKeys.watchlists });
    } finally {
      setPending(false);
    }
  };

  if (!loading && present.length === 0) {
    return <p className="mt-3 text-[13px] text-[var(--text-muted)]">{t("notInWatchlist")}</p>;
  }
  return (
    <div className="mt-3 flex flex-col gap-2">
      <ul className="m-0 flex list-none flex-col gap-0.5 p-0 text-[12px] text-[var(--text-muted)]">
        {targets.map((target) => (
          <li key={target.symbol}>
            {target.groups.length > 0
              ? t("removeFrom", {
                  symbol: target.symbol,
                  groups: target.groups.map((g) => g.name).join("、"),
                })
              : t("skipNotIn", { symbol: target.symbol })}
          </li>
        ))}
      </ul>
      <div className="flex flex-wrap items-center gap-2">
        <Button
          variant="filled"
          size="sm"
          disabled={pending || loading || present.length === 0}
          onClick={apply}
        >
          {pending ? t("removing") : t("remove")}
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
    </div>
  );
}
