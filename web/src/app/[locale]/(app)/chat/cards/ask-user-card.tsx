"use client";

import { QuestionIcon } from "@phosphor-icons/react";
import type { ReactNode } from "react";
import { cn } from "@/lib/cn";
import { asString } from "@/lib/format-number";

/**
 * The `ask_user` card: the model's informational mid-turn clarification
 * (`tools/ask_user_tools.py`), rendered inline where any other `rich_card` would appear. Tapping
 * an option (or typing a normal reply in the composer) is how the clarification gets "answered" —
 * there is no live pause/resume inside the turn that asked (see that tool's own module
 * docstring); a tapped option just sends its text as the next message.
 *
 * Deliberately its own component, not routed through `card-registry.ts`'s data-shape map — this
 * isn't a chart/table rendering of fetched market data, it's an interactive prompt, not one of
 * the 10 real data-card shapes.
 */
export function AskUserCard({
  data,
  onPick,
}: {
  data: Record<string, unknown>;
  onPick: (prompt: string) => void;
}): ReactNode {
  const question = asString(data.question);
  const options = Array.isArray(data.options)
    ? data.options.filter((o): o is string => typeof o === "string")
    : [];

  if (!question) {
    return null;
  }

  return (
    <div className="surface-card mt-3 p-4" aria-live="off">
      <div className="flex items-start gap-2">
        <QuestionIcon
          size={18}
          weight="regular"
          className="mt-0.5 shrink-0 text-[var(--accent)]"
          aria-hidden
        />
        <p className="text-[14px] text-[var(--text)]">{question}</p>
      </div>
      {options.length > 0 ? (
        <div className="mt-3 flex flex-wrap gap-2">
          {options.map((option) => (
            <button
              key={option}
              type="button"
              onClick={() => onPick(option)}
              className={cn(
                "min-h-11 rounded-[var(--radius-pill)] border border-[var(--border)] bg-[var(--surface-2)] px-4",
                "text-left text-[var(--text)] text-sm transition-colors",
                "hover:border-[var(--accent)]",
              )}
            >
              {option}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}
