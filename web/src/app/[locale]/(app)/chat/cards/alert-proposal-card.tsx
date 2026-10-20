"use client";

import { BellRingingIcon, CheckCircleIcon } from "@phosphor-icons/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { type ReactNode, useState } from "react";
import { Button } from "@/components/ui/button";
import { Link } from "@/i18n/navigation";
import { conditionSummary } from "@/lib/alerts/condition";
import { findExistingRule, parseProposal, toCreateRequest } from "@/lib/alerts/proposal";
import { createAlert, listAlerts, SettingsApiError } from "@/lib/api/settings";
import { FRESHNESS } from "@/lib/freshness";

const ALERTS_KEY = ["settings", "alerts"] as const;

/**
 * The `alert_proposal` card: the agent's `propose_alert` tool (backend/src/tools/
 * propose_alert_tools.py) only *suggests* a rule — this card is the confirm gate. Nothing exists
 * until the user taps 建立, which makes this component call the ordinary `POST /alerts` (auth,
 * fair-use caps and market checks all enforced there, exactly as from Settings). The agent itself
 * has no write path.
 *
 * Deliberately its own component, not routed through `card-registry.ts`'s data-shape map — like
 * `ask_user`, this is an interactive prompt, not a rendering of fetched market data.
 */
export function AlertProposalCard({
  data,
}: Readonly<{ data: Record<string, unknown> }>): ReactNode {
  const t = useTranslations("chat.cards.alertProposal");
  const ts = useTranslations("settings.alerts.summary");
  const tc = useTranslations("settings.common");
  const queryClient = useQueryClient();
  const [dismissed, setDismissed] = useState(false);

  const proposal = parseProposal(data);

  // Already-set detection covers a card re-rendered from thread history and a double tap: an
  // identical enabled rule means there is nothing left to confirm.
  const alertsQuery = useQuery({
    queryKey: ALERTS_KEY,
    queryFn: listAlerts,
    enabled: proposal !== null,
    staleTime: FRESHNESS.standard,
    retry: false,
  });

  const create = useMutation({
    mutationFn: createAlert,
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ALERTS_KEY }),
  });

  if (!proposal) {
    return null;
  }

  const summary = conditionSummary(proposal.condition);
  const condition = ts(summary.key, summary.values);
  const existing = findExistingRule(alertsQuery.data ?? [], proposal);
  const created = create.isSuccess || existing !== null;

  const expiry =
    proposal.expires_in_days === null
      ? t("neverExpires")
      : t("expiresIn", { days: proposal.expires_in_days });

  if (dismissed && !created) {
    return (
      <p className="mt-3 text-[13px] text-[var(--text-muted)]" aria-live="polite">
        {t("dismissed")}
      </p>
    );
  }

  return (
    <section aria-label={t("title")} className="surface-card mt-3 p-4">
      <div className="flex items-start gap-2">
        <BellRingingIcon
          size={18}
          weight="regular"
          className="mt-0.5 shrink-0 text-[var(--accent)]"
          aria-hidden
        />
        <div className="min-w-0">
          <p className="text-[12px] text-[var(--text-muted)]">{t("title")}</p>
          <p className="font-medium text-[14px] text-[var(--text)]">
            {proposal.symbol} · {tc(`marketOption.${proposal.market}`)}
          </p>
          <p className="mt-0.5 text-[14px] text-[var(--text)]">{condition}</p>
          <p className="mt-0.5 text-[12px] text-[var(--text-muted)]">
            {t(`frequency.${proposal.trigger_mode}`)} · {expiry}
          </p>
        </div>
      </div>

      {created ? (
        <p
          className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-1 text-[13px] text-[var(--accent-text)]"
          aria-live="polite"
        >
          <span className="inline-flex items-center gap-1.5">
            <CheckCircleIcon size={16} weight="regular" aria-hidden />
            {existing !== null && !create.isSuccess ? t("alreadySet") : t("created")}
          </span>
          <Link href="/settings/alerts" className="underline-offset-2 hover:underline">
            {t("manage")}
          </Link>
        </p>
      ) : (
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <Button
            variant="filled"
            size="sm"
            disabled={create.isPending}
            onClick={() => create.mutate(toCreateRequest(proposal))}
          >
            {create.isPending ? t("creating") : t("create")}
          </Button>
          <Button variant="ghost" size="sm" onClick={() => setDismissed(true)}>
            {t("dismiss")}
          </Button>
        </div>
      )}

      {create.isError ? (
        <p role="alert" className="mt-2 text-[12px] text-[var(--warn)]">
          {create.error instanceof SettingsApiError && create.error.code === "cap_reached"
            ? t("errorCap")
            : t("errorGeneric")}
        </p>
      ) : null}

      <p className="mt-3 text-[11px] text-[var(--text-muted)] leading-snug">{t("caveat")}</p>
    </section>
  );
}
