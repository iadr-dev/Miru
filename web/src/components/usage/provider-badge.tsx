import type { ReactNode } from "react";
import { providerStyle } from "@/lib/provider-style";

/** A provider's name with its colour dot - the same colour the chart uses for that provider. */
export function ProviderBadge({ provider }: Readonly<{ provider: string }>): ReactNode {
  const { label, color } = providerStyle(provider);
  return (
    <span
      className="inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-[11px] text-[var(--text)]"
      style={{ background: `color-mix(in srgb, ${color} 16%, transparent)` }}
    >
      <span aria-hidden className="size-2 rounded-full" style={{ background: color }} />
      {label}
    </span>
  );
}
