"use client";

import { useTranslations } from "next-intl";
import type { ReactNode } from "react";
import {
  formatCostMicros,
  formatEstimateMicros,
  formatTokens,
  shortModelName,
} from "@/lib/usage-format";
import type { TurnUsageData } from "@/types/usage";

/** The quiet "how much did this answer use" line under an answer: input and output tokens, the
 * model(s) that served it, and the charge: the provider's own when it reported one, otherwise a marked estimate. Renders nothing for
 * a turn with no model usage (e.g. a cached reply), so there is never a "0 tokens" line. */
export function TokenUsageChip({ usage }: { usage: TurnUsageData | null | undefined }): ReactNode {
  const t = useTranslations("chat.usage");
  if (!usage || usage.input_tokens + usage.output_tokens === 0) {
    return null;
  }
  const charge = usage.cost_micros > 0;
  const estimate = !charge && usage.estimated_cost_micros > 0;
  const names = [...new Set(usage.models.map((m) => shortModelName(m.provider, m.model)))];
  const detail = t("summary", {
    input: formatTokens(usage.input_tokens),
    output: formatTokens(usage.output_tokens),
  });
  return (
    <p className="mt-2 flex flex-wrap items-center gap-x-2 font-num text-[12px] text-[var(--text-muted)] tabular-nums">
      <span>{detail}</span>
      {names.length > 0 ? <span aria-hidden>·</span> : null}
      {names.length > 0 ? <span>{names.join(", ")}</span> : null}
      {charge || estimate ? <span aria-hidden>·</span> : null}
      {charge ? <span>{formatCostMicros(usage.cost_micros)}</span> : null}
      {estimate ? (
        <span title={t("estimatedCost")}>
          {formatEstimateMicros(usage.estimated_cost_micros)}
          <span className="sr-only"> ({t("estimatedCost")})</span>
        </span>
      ) : null}
    </p>
  );
}
