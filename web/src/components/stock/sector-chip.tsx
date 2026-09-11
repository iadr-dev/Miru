import type { ReactNode } from "react";
import { hashToChartToken } from "@/lib/chart-token-hash";

/** `docs/web/stock-detail.md` §7's `SectorChip` — muted sector-token bg (~15% α) + solid-hue
 * text + hairline of the same hue, pill 999px. Category only, never a price. */
export function SectorChip({ label }: { readonly label: string }): ReactNode {
  const token = hashToChartToken(label);
  return (
    <span
      className="rounded-[var(--radius-pill)] px-2.5 py-1 font-medium text-[12px]"
      style={{
        backgroundColor: `${token}26`,
        color: token,
        border: `1px solid ${token}40`,
      }}
    >
      {label}
    </span>
  );
}
