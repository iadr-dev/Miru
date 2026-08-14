import { WarningIcon } from "@phosphor-icons/react/dist/ssr";
import type { ReactNode } from "react";
import { cn } from "@/lib/cn";

/**
 * The required, non-removable non-trading caveat (web/CLAUDE.md §4, README §8). Rendered as a
 * pinned `--warn`-tinted chip on every analytical answer. There is deliberately no `hidden`/
 * dismissible prop — governance forbids a way to hide it. The text is the governance copy already
 * in the message catalog (`chat.caveat.nonTrading`), not rewritten here.
 */
export function Caveat({ text }: { text: string }): ReactNode {
  return (
    <p
      className={cn(
        "mt-3 inline-flex items-start gap-1.5 rounded-[var(--radius-pill)] border px-3 py-1.5",
        "border-[color-mix(in_srgb,var(--warn)_40%,transparent)] bg-[var(--surface-2)]",
        "text-[13px] text-[var(--text-muted)] leading-relaxed",
      )}
    >
      <WarningIcon
        size={14}
        weight="regular"
        aria-hidden
        className="mt-0.5 shrink-0 text-[var(--warn)]"
      />
      <span>{text}</span>
    </p>
  );
}
