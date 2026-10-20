"use client";

import { CaretDownIcon, CheckIcon, MagnifyingGlassIcon } from "@phosphor-icons/react";
import * as Popover from "@radix-ui/react-popover";
import { Command } from "cmdk";
import * as Flags from "country-flag-icons/react/3x2";
import { useLocale } from "next-intl";
import { type ReactNode, useState } from "react";
import { cn } from "@/lib/cn";
import { COUNTRIES, type CountryEntry } from "@/lib/countries";

function CountryFlag({ iso2 }: Readonly<{ iso2: string }>): ReactNode {
  const Flag = (Flags as Record<string, (props: { className?: string }) => ReactNode>)[iso2];
  if (!Flag) {
    return <span className="inline-block h-3 w-4 shrink-0 rounded-[2px] bg-[var(--surface-2)]" />;
  }
  return <Flag className="h-3 w-4 shrink-0 rounded-[2px]" />;
}

type CountryComboboxProps = {
  /** Selected country's ISO 3166-1 alpha-2 code, or `null` if nothing is selected yet. */
  value: string | null;
  onSelect: (country: CountryEntry) => void;
  placeholder: string;
  searchPlaceholder: string;
  emptyLabel: string;
  "aria-label": string;
  /** `"name"` shows the country name (nationality field); `"dialCode"` shows the flag + dial code
   * (phone-country field). Both search the same way, over name and dial code together. */
  display: "name" | "dialCode";
};

/** Searchable, flag-labelled country picker shared by the nationality and phone-country-code
 * fields — both read the same `COUNTRIES` dataset keyed by ISO alpha-2, so selecting one can set
 * the other via a plain lookup on that code. */
export function CountryCombobox({
  value,
  onSelect,
  placeholder,
  searchPlaceholder,
  emptyLabel,
  display,
  ...rest
}: Readonly<CountryComboboxProps>): ReactNode {
  const locale = useLocale();
  const [open, setOpen] = useState(false);
  const selected = value ? COUNTRIES.find((c) => c.iso2 === value) : undefined;
  const nameFor = (c: CountryEntry): string => (locale === "zh-TW" ? c.nameZh : c.nameEn);
  const describe = (c: CountryEntry, separator: string): string => {
    if (display === "dialCode") return `+${c.dialCode}${separator}${nameFor(c)}`;
    return nameFor(c);
  };

  return (
    <Popover.Root open={open} onOpenChange={setOpen}>
      <Popover.Trigger
        type="button"
        aria-label={
          selected ? `${rest["aria-label"]}: ${describe(selected, " ")}` : rest["aria-label"]
        }
        className={cn(
          "flex min-h-11 w-full items-center gap-2 rounded-[var(--radius-input)] border border-[var(--border)]",
          "bg-[var(--surface)] px-3 text-left text-[14px] text-[var(--text)]",
          "!outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]",
        )}
      >
        {selected ? <CountryFlag iso2={selected.iso2} /> : null}
        <span className={cn("flex-1 truncate", !selected && "text-[var(--text-muted)]")}>
          {selected ? describe(selected, " · ") : placeholder}
        </span>
        <CaretDownIcon size={14} weight="regular" className="shrink-0 text-[var(--text-muted)]" />
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Content
          align="start"
          sideOffset={4}
          className={cn(
            "z-50 w-[280px] overflow-hidden rounded-[var(--radius-input)] border border-[var(--border)]",
            "bg-[var(--surface)] shadow-xl",
          )}
        >
          <Command loop label={rest["aria-label"]}>
            <div className="flex items-center gap-2 border-[var(--border)] border-b px-3">
              <MagnifyingGlassIcon
                size={14}
                weight="regular"
                className="text-[var(--text-muted)]"
              />
              <Command.Input
                placeholder={searchPlaceholder}
                className={cn(
                  "min-h-11 w-full bg-transparent text-[14px] text-[var(--text)]",
                  "placeholder:text-[var(--text-muted)]",
                  // No focus ring on this field at all per design. The global focus-visible rule
                  // (globals.css) is deliberately unlayered CSS, which always wins over a layered
                  // Tailwind utility regardless of specificity — a plain `outline-none` can't
                  // override it, hence the `!` (important) modifier here.
                  "!outline-none",
                )}
              />
            </div>
            <Command.List className="max-h-64 overflow-y-auto p-1">
              <Command.Empty className="px-3 py-6 text-center text-[13px] text-[var(--text-muted)]">
                {emptyLabel}
              </Command.Empty>
              {COUNTRIES.map((country) => (
                <Command.Item
                  key={country.iso2}
                  value={`${country.nameEn} ${country.nameZh} ${country.iso2} ${country.dialCode}`}
                  onSelect={() => {
                    onSelect(country);
                    setOpen(false);
                  }}
                  className={cn(
                    "flex min-h-9 cursor-pointer items-center gap-2 rounded-[8px] px-2 text-[13px] text-[var(--text)]",
                    "data-[selected=true]:bg-[var(--surface-2)]",
                  )}
                >
                  <CountryFlag iso2={country.iso2} />
                  <span className="flex-1 truncate">{nameFor(country)}</span>
                  {display === "dialCode" ? (
                    <span className="text-[12px] text-[var(--text-muted)] tabular-nums">
                      +{country.dialCode}
                    </span>
                  ) : null}
                  {selected?.iso2 === country.iso2 ? (
                    <CheckIcon size={14} weight="regular" className="text-[var(--accent)]" />
                  ) : null}
                </Command.Item>
              ))}
            </Command.List>
          </Command>
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}
