"use client";

import { XIcon } from "@phosphor-icons/react";
import { useTranslations } from "next-intl";
import { type KeyboardEvent, type ReactNode, useId, useState } from "react";
import { useDebouncedValue } from "@/hooks/use-debounced-value";
import { useSymbolSearch } from "@/hooks/use-symbol-search";
import { cn } from "@/lib/cn";
import { COMPARE_MAX_SYMBOLS, isCompareSymbol } from "@/lib/stock-compare";
import type { SearchHit } from "@/types/symbol-search";

const SEARCH_DEBOUNCE_MS = 250;
const MAX_OPTIONS = 8;

/** The chips of the stocks being compared (each removable) plus a small combobox that adds one
 * more, up to the limit. At the limit the input is disabled and a calm hint says why. */
export function ComparePicker({
  symbols,
  names,
  colors,
  onAdd,
  onRemove,
}: {
  readonly symbols: readonly string[];
  readonly names: ReadonlyMap<string, string>;
  readonly colors: Readonly<Record<string, string>>;
  readonly onAdd: (hit: SearchHit) => void;
  readonly onRemove: (symbol: string) => void;
}): ReactNode {
  const t = useTranslations("stock.technical.compare");
  const baseId = useId();
  const listId = `${baseId}-list`;
  const hintId = `${baseId}-hint`;
  const [text, setText] = useState("");
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const query = useDebouncedValue(text.trim(), SEARCH_DEBOUNCE_MS);
  const search = useSymbolSearch(query, "TW");
  const full = symbols.length >= COMPARE_MAX_SYMBOLS;

  const options = (search.data ?? [])
    .filter((hit) => isCompareSymbol(hit.symbol) && !symbols.includes(hit.symbol))
    .slice(0, MAX_OPTIONS);
  const showList = open && query.length > 0 && !full;
  const searching = query.length > 0 && search.isPending;
  const activeIndex = Math.min(active, Math.max(options.length - 1, 0));
  const optionId = (index: number): string => `${baseId}-opt-${index}`;

  const choose = (hit: SearchHit): void => {
    onAdd(hit);
    setText("");
    setActive(0);
    setOpen(false);
  };

  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>): void => {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setOpen(true);
      setActive(options.length === 0 ? 0 : (activeIndex + 1) % options.length);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setActive(options.length === 0 ? 0 : (activeIndex - 1 + options.length) % options.length);
    } else if (event.key === "Enter") {
      const hit = options[activeIndex];
      if (showList && hit) {
        event.preventDefault();
        choose(hit);
      }
    } else if (event.key === "Escape") {
      setOpen(false);
    }
  };

  return (
    <div className="flex flex-col gap-2">
      <ul aria-label={t("selectedLabel")} className="flex flex-wrap gap-2">
        {symbols.map((symbol) => {
          const name = names.get(symbol) ?? symbol;
          return (
            <li
              key={symbol}
              className="flex min-h-11 items-center gap-2 rounded-[var(--radius-pill)] border border-[var(--border)] bg-[var(--surface)] pr-1 pl-3 text-[13px]"
            >
              <span
                aria-hidden
                className="size-2.5 shrink-0 rounded-full"
                style={{ backgroundColor: colors[symbol] ?? "var(--chart-1)" }}
              />
              <span className="whitespace-nowrap text-[var(--text)]">
                <span className="font-num tabular-nums">{symbol}</span>
                {name === symbol ? null : <span className="ml-1.5">{name}</span>}
              </span>
              <button
                type="button"
                onClick={() => onRemove(symbol)}
                aria-label={t("remove", { name: name === symbol ? symbol : `${symbol} ${name}` })}
                className="flex size-11 shrink-0 items-center justify-center rounded-full text-[var(--text-muted)] hover:text-[var(--text)]"
              >
                <XIcon size={14} weight="bold" aria-hidden />
              </button>
            </li>
          );
        })}
      </ul>

      <div className="relative max-w-sm">
        <input
          type="text"
          role="combobox"
          aria-label={t("pickerLabel")}
          aria-expanded={showList}
          aria-controls={listId}
          aria-autocomplete="list"
          aria-describedby={full ? hintId : undefined}
          {...(showList && options.length > 0
            ? { "aria-activedescendant": optionId(activeIndex) }
            : {})}
          autoComplete="off"
          disabled={full}
          value={text}
          placeholder={t("pickerPlaceholder")}
          onChange={(event) => {
            setText(event.target.value);
            setActive(0);
            setOpen(true);
          }}
          onFocus={() => setOpen(true)}
          onBlur={() => setOpen(false)}
          onKeyDown={onKeyDown}
          className="min-h-11 w-full rounded-[var(--radius-input)] border border-[var(--border)] bg-[var(--surface)] px-3 text-[14px] text-[var(--text)] outline-none placeholder:text-[var(--text-muted)] focus-visible:outline-2 focus-visible:outline-[var(--accent)] disabled:opacity-60"
        />
        <div
          className={cn(
            "absolute z-20 mt-1 max-h-72 w-full overflow-y-auto rounded-[var(--radius-card)] border border-[var(--border)] bg-[var(--surface)] p-1 shadow-lg",
            !showList && "hidden",
          )}
        >
          <div id={listId} role="listbox" aria-label={t("pickerLabel")}>
            {options.map((hit, index) => (
              <button
                key={hit.symbol}
                type="button"
                id={optionId(index)}
                role="option"
                tabIndex={-1}
                aria-selected={index === activeIndex}
                // Pointer selection must not blur the input first, or the list closes before the click.
                onMouseDown={(event) => event.preventDefault()}
                onClick={() => choose(hit)}
                className={cn(
                  "flex min-h-11 w-full items-center gap-2 rounded-[var(--radius-button)] px-3 text-left text-[13px] text-[var(--text)]",
                  index === activeIndex && "bg-[var(--surface-2)]",
                )}
              >
                <span className="font-num tabular-nums">{hit.symbol}</span>
                <span className="min-w-0 flex-1 truncate">{hit.name}</span>
              </button>
            ))}
          </div>
          {searching ? (
            <p className="px-3 py-2 text-[13px] text-[var(--text-muted)]">{t("pickerSearching")}</p>
          ) : null}
          {!searching && options.length === 0 ? (
            <p className="px-3 py-2 text-[13px] text-[var(--text-muted)]">{t("pickerNone")}</p>
          ) : null}
        </div>
        {full ? (
          <p id={hintId} className="mt-1.5 text-[12px] text-[var(--text-muted)]">
            {t("limitHint", { max: COMPARE_MAX_SYMBOLS })}
          </p>
        ) : null}
      </div>
    </div>
  );
}
