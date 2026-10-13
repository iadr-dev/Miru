"use client";

import { CubeIcon, PauseIcon, PlayIcon, SquaresFourIcon } from "@phosphor-icons/react";
import { useLocale, useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { cn } from "@/lib/cn";

export type RotationView = "flat" | "3d";

function formatWeekEnding(iso: string | undefined, locale: string): string {
  if (!iso) return "";
  const date = new Date(`${iso}T00:00:00Z`);
  if (Number.isNaN(date.getTime())) return iso;
  return new Intl.DateTimeFormat(locale, {
    year: "numeric",
    month: "short",
    day: "numeric",
    timeZone: "UTC",
  }).format(date);
}

/** The transport bar under the scene: play/pause, a scrubber over the real weeks, the week's
 * ending date, and the flat/3D switch. Scrubbing pauses playback (the hook does that). */
export function RotationControls({
  weekEndings,
  currentWeek,
  isPlaying,
  canPlay,
  view,
  onTogglePlaying,
  onSeek,
  onViewChange,
}: {
  readonly weekEndings: readonly string[];
  readonly currentWeek: number;
  readonly isPlaying: boolean;
  readonly canPlay: boolean;
  readonly view: RotationView;
  readonly onTogglePlaying: () => void;
  readonly onSeek: (week: number) => void;
  readonly onViewChange: (view: RotationView) => void;
}): ReactNode {
  const t = useTranslations("market.cards.sectorHeatmap.rotationRrg");
  const locale = useLocale();
  const lastWeek = Math.max(weekEndings.length - 1, 0);
  const week = Math.round(currentWeek);
  const date = formatWeekEnding(weekEndings[week], locale);
  const weekOf = t("weekOf", { n: week + 1, total: weekEndings.length });
  const views: readonly { id: RotationView; label: string; icon: ReactNode }[] = [
    { id: "flat", label: t("viewFlat"), icon: <SquaresFourIcon size={14} aria-hidden /> },
    { id: "3d", label: t("view3d"), icon: <CubeIcon size={14} aria-hidden /> },
  ];

  return (
    <div className="pointer-events-auto flex flex-wrap items-center gap-x-3 gap-y-0 rounded-[var(--radius-card)] border border-[var(--border)] bg-[var(--surface)]/70 px-3 py-1.5 backdrop-blur-md">
      {canPlay ? (
        <button
          type="button"
          onClick={onTogglePlaying}
          aria-label={isPlaying ? t("pause") : t("play")}
          className="order-1 grid size-11 shrink-0 place-items-center rounded-full bg-[var(--surface-2)] text-[var(--text)] transition-transform active:scale-95"
        >
          {isPlaying ? <PauseIcon size={18} weight="fill" /> : <PlayIcon size={18} weight="fill" />}
        </button>
      ) : null}
      <input
        type="range"
        min={0}
        max={lastWeek}
        step={1}
        value={week}
        onChange={(event) => onSeek(Number(event.target.value))}
        aria-label={t("scrub")}
        aria-valuetext={`${weekOf} · ${t("weekEnding", { date })}`}
        className="order-4 h-11 min-w-0 basis-full accent-[var(--accent)] sm:order-2 sm:flex-1 sm:basis-0"
      />
      <div className="order-2 min-w-0 flex-1 sm:order-3 sm:w-36 sm:flex-none sm:text-right">
        <div className="whitespace-nowrap font-num font-semibold text-[13px] text-[var(--text)] tabular-nums">
          {date}
        </div>
        <div className="whitespace-nowrap font-num text-[11px] text-[var(--text-muted)] tabular-nums">
          {weekOf}
        </div>
      </div>
      <div
        role="group"
        aria-label={t("viewLabel")}
        className="order-3 flex shrink-0 gap-1 sm:order-4"
      >
        {views.map((item) => (
          <button
            key={item.id}
            type="button"
            aria-pressed={view === item.id}
            aria-label={item.label}
            onClick={() => onViewChange(item.id)}
            className={cn(
              "flex h-11 min-w-11 items-center justify-center gap-1 rounded-[var(--radius-input)] px-2.5 text-[12px] transition-colors active:scale-95",
              view === item.id
                ? "bg-[var(--surface-2)] text-[var(--text)]"
                : "text-[var(--text-muted)] hover:text-[var(--text)]",
            )}
          >
            {item.icon}
            <span className="hidden sm:inline">{item.label}</span>
          </button>
        ))}
      </div>
    </div>
  );
}
