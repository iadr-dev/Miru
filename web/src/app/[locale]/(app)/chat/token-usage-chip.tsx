"use client";

import { useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { formatCostMicros, formatTokens, shortModelName } from "@/lib/usage-format";
import type { TurnUsageData } from "@/types/usage";

/** The quiet "how much did this answer use" line under an answer: input and output tokens, the
 * model(s) that served it, and a charge only when the provider reported one. Renders nothing for
 * a turn with no model usage (e.g. a cached reply), so there is never a "0 tokens" line. */
export function TokenUsageChip({ usage }: { usage: TurnUsageData | null | undefined }): ReactNode {
  const t = useTranslations("chat.usage");
  if (!usage || usage.input_tokens + usage.output_tokens === 0) {
    return null;
  }
  const names = [...new Set(usage.models.map((m) => shortModelName(m.provider, m.model)))];
  const detail = t("summary", {
    input: formatTokens(usage.input_tokens),
    output: formatTokens(usage.output_tokens),
  });
  return (
    <p className="font-num mt-2 flex flex-wrap items-center gap-x-2 text-[12px] text-[var(--text-muted)] tabular-nums">
      <span>{detail}</span>
      {names.length > 0 ? <span aria-hidden>·</span> : null}
      {names.length > 0 ? <span>{names.join(", ")}</span> : null}
      {usage.cost_micros > 0 ? <span aria-hidden>·</span> : null}
      {usage.cost_micros > 0 ? <span>{formatCostMicros(usage.cost_micros)}</span> : null}
    </p>
  );
}
