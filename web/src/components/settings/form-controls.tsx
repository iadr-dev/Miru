"use client";

import type {
  InputHTMLAttributes,
  ReactNode,
  SelectHTMLAttributes,
  TextareaHTMLAttributes,
} from "react";
import { cn } from "@/lib/cn";

type TextInputProps = InputHTMLAttributes<HTMLInputElement>;

/** Shared text/number input styling for Settings forms (input radius 10px,
 * `--surface` + 1px `--border`, focus-visible `--accent` ring). */
export function TextInput({ className, ...rest }: TextInputProps): ReactNode {
  return (
    <input
      className={cn(
        "min-h-11 w-full rounded-[var(--radius-input)] border border-[var(--border)]",
        "bg-[var(--surface)] px-3 text-[14px] text-[var(--text)] placeholder:text-[var(--text-muted)]",
        "!outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]",
        className,
      )}
      {...rest}
    />
  );
}

/** Multi-line text, styled like `TextInput` so a form never mixes two field looks. */
export function Textarea({
  className,
  ...rest
}: TextareaHTMLAttributes<HTMLTextAreaElement>): ReactNode {
  return (
    <textarea
      className={cn(
        "w-full rounded-[var(--radius-input)] border border-[var(--border)]",
        "bg-[var(--surface)] px-3 py-2 text-[14px] text-[var(--text)] leading-relaxed",
        "placeholder:text-[var(--text-muted)]",
        "!outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]",
        className,
      )}
      {...rest}
    />
  );
}

type SelectProps = SelectHTMLAttributes<HTMLSelectElement> & { children: ReactNode };

export function Select({ className, children, ...rest }: SelectProps): ReactNode {
  return (
    <select
      className={cn(
        "min-h-11 w-full rounded-[var(--radius-input)] border border-[var(--border)]",
        "bg-[var(--surface)] px-3 text-[14px] text-[var(--text)]",
        "!outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]",
        className,
      )}
      {...rest}
    >
      {children}
    </select>
  );
}

/** Accessible toggle switch — `--accent` active fill, 44px hit target via the wrapping label. */
export function ToggleSwitch({
  checked,
  onChange,
  label,
  disabled,
}: {
  checked: boolean;
  onChange: (next: boolean) => void;
  label: string;
  disabled?: boolean;
}): ReactNode {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={cn(
        // The visible track stays 24px tall (h-6); padding grows the actual hit target to the
        // required 44px minimum without changing how the switch looks.
        "inline-flex min-h-11 min-w-11 shrink-0 items-center justify-center",
        "!outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]",
        "disabled:cursor-not-allowed disabled:opacity-50",
      )}
    >
      <span
        className={cn(
          "relative inline-flex h-6 w-11 items-center rounded-[var(--radius-pill)] transition-colors",
          checked ? "bg-[var(--accent)]" : "bg-[var(--surface-2)]",
        )}
      >
        <span
          className={cn(
            "inline-block size-5 translate-x-0.5 rounded-full bg-[var(--bg)] transition-transform",
            checked && "translate-x-[22px]",
          )}
        />
      </span>
    </button>
  );
}

export function Checkbox({
  checked,
  onChange,
  label,
  disabled,
}: {
  checked: boolean;
  onChange: (next: boolean) => void;
  label: string;
  disabled?: boolean;
}): ReactNode {
  return (
    <label
      className={cn(
        "flex min-h-11 items-center gap-2 text-[14px] text-[var(--text)]",
        disabled && "cursor-not-allowed opacity-50",
      )}
    >
      <input
        type="checkbox"
        checked={checked}
        disabled={disabled}
        onChange={(e) => onChange(e.target.checked)}
        className={cn(
          "size-4 rounded border-[var(--border)] accent-[var(--accent)]",
          // The global focus-visible rule draws its ring at --radius-button (12px) offset 2px
          // outward — badly mismatched against a 16px checkbox. Override with a ring sized to
          // this control's own shape instead of removing the indicator.
          "!outline-none focus-visible:rounded-[3px] focus-visible:ring-2",
          "focus-visible:ring-[var(--accent)] focus-visible:ring-offset-1",
        )}
      />
      {label}
    </label>
  );
}

/** Inline `--warn` field error, shown under an input on validation failure. */
export function FieldError({ message }: { message: string }): ReactNode {
  return (
    <p role="alert" className="mt-1 text-[12px] text-[var(--warn)]">
      {message}
    </p>
  );
}

export function Label({ htmlFor, children }: { htmlFor?: string; children: ReactNode }): ReactNode {
  return (
    <label htmlFor={htmlFor} className="mb-1 block text-[13px] text-[var(--text-muted)]">
      {children}
    </label>
  );
}
