"use client";

import { CaretDownIcon } from "@phosphor-icons/react";
import { useFormatter, useTranslations } from "next-intl";
import { type ReactNode, useState } from "react";
import { cn } from "@/lib/cn";
import { parseServerDate } from "@/lib/parse-server-date";
import {
  clampBlocks,
  type LayoutBlock,
  type NotificationView,
  ROW_LINE_BUDGET,
} from "./notification-payload";

type Block = LayoutBlock;

const WRAP = "[overflow-wrap:anywhere]";
const OPEN_BUTTON = cn(
  "!outline-none text-left text-[13px] text-[var(--text)]",
  "after:absolute after:inset-0 after:content-['']",
  "focus-visible:after:ring-2 focus-visible:after:ring-[var(--accent)] focus-visible:after:ring-inset",
  WRAP,
);

/** Pairs each item with a key made of its own text and how many equal ones came before it, so a
 * repeated line or block still gets a stable, unique key without using its position. */
function keyed<T>(items: readonly T[], text: (item: T) => string): { key: string; item: T }[] {
  const seen = new Map<string, number>();
  return items.map((item) => {
    const base = text(item);
    const count = seen.get(base) ?? 0;
    seen.set(base, count + 1);
    return { key: `${base}#${count}`, item };
  });
}

function Lines({ lines }: { lines: readonly string[] }): ReactNode {
  return keyed(lines, (line) => line).map(({ key, item }) => (
    <p key={key} className={cn("text-[12px] text-[var(--text-muted)] leading-relaxed", WRAP)}>
      {item}
    </p>
  ));
}

/** A folded block: a native disclosure, collapsed, so a long list does not bury the rest of the
 * notification. It sits above the row's open overlay (`z-10`) so toggling it never opens the
 * notification. */
function FoldBlock({ block }: { block: Block }): ReactNode {
  const t = useTranslations("shell.notifications");
  return (
    <details className="group relative z-10 rounded-[var(--radius-button)] border border-[var(--border)]">
      <summary
        className={cn(
          "!outline-none flex min-h-11 cursor-pointer list-none items-center justify-between gap-2 px-3",
          "text-[12px] [&::-webkit-details-marker]:hidden",
          "rounded-[var(--radius-button)] focus-visible:ring-2 focus-visible:ring-[var(--accent)]",
        )}
      >
        <span className={cn("min-w-0 text-[var(--text)]", WRAP)}>
          {block.head ? <span className="font-semibold">{block.head} </span> : null}
          <span className="font-num text-[var(--text-muted)]">
            {t("foldCount", { count: block.lines.length })}
          </span>
        </span>
        <CaretDownIcon
          aria-hidden
          size={14}
          className="shrink-0 text-[var(--text-muted)] transition-transform group-open:rotate-180 motion-reduce:transition-none"
        />
      </summary>
      <div className="flex flex-col gap-0.5 px-3 pb-3">
        <Lines lines={block.lines} />
      </div>
    </details>
  );
}

function PlainBlock({ block, lead }: { block: Block; lead: ReactNode }): ReactNode {
  return (
    <div className="flex min-w-0 flex-col gap-0.5">
      {lead ??
        (block.head ? (
          <p className={cn("font-semibold text-[13px] text-[var(--text)]", WRAP)}>{block.head}</p>
        ) : null)}
      <Lines lines={block.lines} />
    </div>
  );
}

/** One inbox entry. The whole row opens the notification through a stretched button (the heading),
 * so there is no nested interactive element; a fold block and the show-all control are the things
 * layered above it. A layout taller than the line budget shows only its top, ending in a control
 * that reveals the rest; the non-advice footer stays visible either way. The
 * layout is drawn from React text nodes only. Without a usable layout the title and plain body are
 * shown as before. */
export function NotificationRow({
  item,
  onOpen,
}: {
  item: NotificationView;
  onOpen: (item: NotificationView) => void;
}): ReactNode {
  const t = useTranslations("shell.notifications");
  const format = useFormatter();
  const [expanded, setExpanded] = useState(false);
  const open = (): void => onOpen(item);
  const blocks = item.layout?.blocks ?? [];
  const clamp = clampBlocks(blocks, ROW_LINE_BUDGET);
  const clamped = clamp.hiddenLineCount > 0;
  const shown = clamped && !expanded ? clamp.shown : blocks;
  const first = blocks[0];
  const headLeads = first !== undefined && !first.fold && first.head !== "";
  const openButton = (
    <button type="button" onClick={open} className={cn(OPEN_BUTTON, "font-semibold")}>
      {headLeads ? first.head : item.title}
    </button>
  );

  return (
    <li className="relative flex gap-2 px-4 py-3 hover:bg-[var(--surface-2)]">
      {item.unread ? (
        <span className="mt-1.5 size-2 shrink-0 rounded-full bg-[var(--accent)]">
          <span className="sr-only">{t("unreadItem")}</span>
        </span>
      ) : null}
      <div className="flex min-w-0 flex-1 flex-col gap-2">
        {item.layout ? (
          <>
            {headLeads ? null : openButton}
            {keyed(shown, (b) => `${b.head}|${b.lines.length}|${b.fold}`).map(
              ({ key, item: block }, index) =>
                block.fold ? (
                  <FoldBlock key={key} block={block} />
                ) : (
                  <PlainBlock
                    key={key}
                    block={block}
                    lead={index === 0 && headLeads ? openButton : null}
                  />
                ),
            )}
            {clamped ? (
              <button
                type="button"
                aria-expanded={expanded}
                onClick={() => setExpanded(!expanded)}
                className={cn(
                  "!outline-none relative z-10 inline-flex min-h-11 items-center self-start",
                  "rounded-[var(--radius-button)] px-2 font-medium text-[12px] text-[var(--accent)]",
                  "focus-visible:ring-2 focus-visible:ring-[var(--accent)]",
                )}
              >
                {expanded ? t("showLess") : t("showAll", { count: clamp.hiddenLineCount })}
              </button>
            ) : null}
            {item.footer ? (
              <p className={cn("text-[11px] text-[var(--text-muted)]", WRAP)}>{item.footer}</p>
            ) : null}
          </>
        ) : (
          <div className="flex min-w-0 flex-col gap-1">
            <button
              type="button"
              onClick={open}
              className={cn(OPEN_BUTTON, item.unread ? "font-semibold" : "font-normal")}
            >
              {item.title}
            </button>
            <span className={cn("whitespace-pre-line text-[12px] text-[var(--text-muted)]", WRAP)}>
              {item.body}
            </span>
          </div>
        )}
        <span className="font-num text-[11px] text-[var(--text-muted)]">
          {format.dateTime(parseServerDate(item.deliveredAt), {
            dateStyle: "medium",
            timeStyle: "short",
          })}
        </span>
      </div>
    </li>
  );
}
