"use client";

import {
  CameraIcon,
  CheckIcon,
  GlobeIcon,
  MagnifyingGlassIcon,
  PaperclipIcon,
  PlusIcon,
} from "@phosphor-icons/react";
import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
import { useTranslations } from "next-intl";
import { type ReactNode, useState } from "react";
import { useRouter } from "@/i18n/navigation";
import { cn } from "@/lib/cn";

/** The `+` feature-menu trigger. "Add files or photos" opens the hidden file input (`onAddFiles`);
 * "Take a screenshot" and "Web search" were already wired for real — the CAPTURE step of a
 * screenshot is a pure-browser flow (`captureScreenshot`, above) with no backend dependency, and
 * web search is a real `web_search: bool` field `/agent/query` (and `regenerate`/`edit`) accept,
 * so "Web search" renders as a checkable toggle row rather than a disabled stub. */
export function FeatureMenu({
  label,
  addFilesLabel,
  webSearchEnabled,
  onWebSearchEnabledChange,
  deepResearch,
  deepResearchLocked,
  onAddFiles,
  onCaptureScreenshot,
}: {
  label: string;
  addFilesLabel: string;
  webSearchEnabled: boolean;
  onWebSearchEnabledChange: (enabled: boolean) => void;
  deepResearch: { enabled: boolean; onChange: (enabled: boolean) => void } | null;
  deepResearchLocked: boolean;
  onAddFiles: () => void;
  onCaptureScreenshot: () => void;
}): ReactNode {
  const t = useTranslations("chat.composer");
  const router = useRouter();
  const [open, setOpen] = useState(false);

  return (
    <DropdownMenu.Root open={open} onOpenChange={setOpen}>
      <DropdownMenu.Trigger asChild>
        <button
          type="button"
          aria-label={label}
          className={cn(
            "inline-flex size-11 shrink-0 items-center justify-center rounded-[var(--radius-button)]",
            "text-[var(--text-muted)] transition-colors hover:bg-[var(--surface-2)] hover:text-[var(--text)]",
            "!outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]",
            "data-[state=open]:bg-[var(--surface-2)] data-[state=open]:text-[var(--text)]",
          )}
        >
          <PlusIcon size={20} weight="regular" />
        </button>
      </DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content
          side="top"
          align="start"
          sideOffset={8}
          className={cn(
            "z-50 w-64 rounded-[var(--radius-card)] border border-[var(--border)]",
            "bg-[var(--surface)] p-1.5 shadow-2xl",
          )}
        >
          <FeatureMenuItem icon={PaperclipIcon} label={addFilesLabel} onSelect={onAddFiles} />
          <FeatureMenuItem
            icon={CameraIcon}
            label={t("featureMenuItems.screenshot")}
            onSelect={onCaptureScreenshot}
          />
          <DropdownMenu.Separator className="my-1 h-px bg-[var(--border)]" />
          <FeatureMenuItem
            icon={GlobeIcon}
            label={t("featureMenuItems.webSearch")}
            checked={webSearchEnabled}
            onSelect={() => onWebSearchEnabledChange(!webSearchEnabled)}
          />
          {deepResearch !== null ? (
            <FeatureMenuItem
              icon={MagnifyingGlassIcon}
              label={t("featureMenuItems.deepResearch")}
              checked={deepResearch.enabled}
              onSelect={() => deepResearch.onChange(!deepResearch.enabled)}
            />
          ) : deepResearchLocked ? (
            <FeatureMenuItem
              icon={MagnifyingGlassIcon}
              label={t("featureMenuItems.deepResearchLocked")}
              onSelect={() => router.push("/settings/billing")}
            />
          ) : null}
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  );
}

/** One `FeatureMenu` row. `disabled` (the "add files" stub) blocks `onSelect` and lets Radix's own
 * styling hook cover the dimmed look. A row that instead passes `checked` (a boolean, not just
 * `undefined`) is a real toggle — e.g. "Web search" — and shows a trailing checkmark exactly the
 * way `ModelMenu`'s selected-item row does below, rather than inventing a new active-state
 * convention. "Take a screenshot" passes neither: it's real but one-shot, not a toggle. */
function FeatureMenuItem({
  icon: Icon,
  label,
  disabled,
  checked,
  onSelect,
}: {
  icon: typeof PlusIcon;
  label: string;
  disabled?: boolean;
  checked?: boolean;
  onSelect?: () => void;
}): ReactNode {
  return (
    <DropdownMenu.Item
      disabled={disabled}
      onSelect={onSelect}
      className={cn(
        "flex min-h-9 items-center gap-2.5 rounded-[var(--radius-button)] px-2.5",
        "text-[var(--text)] text-sm outline-none transition-colors",
        disabled
          ? "text-[var(--text-muted)]"
          : "cursor-pointer data-[highlighted]:bg-[var(--surface-2)]",
        "data-[disabled]:cursor-not-allowed data-[disabled]:opacity-50",
      )}
    >
      <Icon size={16} weight="regular" aria-hidden />
      <span className="flex-1">{label}</span>
      {checked ? (
        <CheckIcon size={14} weight="regular" className="text-[var(--accent)]" aria-hidden />
      ) : null}
    </DropdownMenu.Item>
  );
}
