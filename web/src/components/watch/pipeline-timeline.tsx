"use client";

import { CheckIcon } from "@phosphor-icons/react";
import { motion, useReducedMotion } from "motion/react";
import { useTranslations } from "next-intl";
import { type ReactNode, useEffect, useRef, useState } from "react";
import type { PipelineStep, PipelineStepKey } from "@/components/watch/run-status";
import {
  hhmm,
  minutesOf,
  nextCheck,
  positionOf,
  SESSION_END,
  SESSION_SLOTS,
  SESSION_START,
  taipeiClock,
} from "@/components/watch/timeline-math";
import { tint } from "@/components/watch/watch-format";
import { cn } from "@/lib/cn";
import type { StrategyWatchToday } from "@/types/strategy-watch";

const STEP_MINUTES: Record<PipelineStepKey, number> = {
  screen: 8 * 60,
  watch: SESSION_START,
  prices: 17 * 60 + 40,
  close: 18 * 60,
  flows: 20 * 60 + 10,
};

const CLOCK_TICK_MS = 30_000;
const TRACK_TOP = 46; // px from the top of the track box to the line

/** The current time, refreshed every 30 s: enough for a countdown in minutes. */
function useNow(): Date {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const id = window.setInterval(() => setNow(new Date()), CLOCK_TICK_MS);
    return () => window.clearInterval(id);
  }, []);
  return now;
}

/** The connector to the next step: amber dashes when its data is stale, a moving dash in its colour
 * on today's live list, and a still dash on a past day's list (nothing is in progress there). */
function flowStyle(next: StepView, live: boolean): Record<string, string> {
  if (next.stale) {
    return {
      backgroundImage: "repeating-linear-gradient(90deg, var(--warn) 0 6px, transparent 6px 12px)",
    };
  }
  if (live) return { "--flow-color": next.color };
  return {
    backgroundImage: `repeating-linear-gradient(90deg, ${next.color} 0 6px, transparent 6px 12px)`,
    opacity: "0.5",
  };
}

function tickColor(run: boolean, hit: boolean, color: string): string {
  if (!run) return "var(--border-strong)";
  return hit ? color : tint(color, 55);
}

function statusKey(view: StepView): "done" | "running" | "stale" | "pending" {
  if (view.done) return "done";
  if (view.running) return "running";
  return view.stale ? "stale" : "pending";
}

type StepView = PipelineStep & {
  position: number;
  color: string;
  running: boolean;
  stale: boolean;
};

/** The trading day as a track: each step at its time, the session's 28 checks as ticks, the
 * progress filled up to now, the running step breathing and a moving dash towards the next step.
 * Hover, focus or tap a step for what it does and how it went. */
export function PipelineTimeline({
  data,
  steps,
  colors,
}: Readonly<{
  data: StrategyWatchToday;
  steps: readonly PipelineStep[];
  colors: Record<PipelineStepKey, string>;
}>): ReactNode {
  const t = useTranslations("watch");
  const reduceMotion = useReducedMotion();
  const now = useNow();
  const clock = taipeiClock(now);
  const live = data.run_date !== null && data.run_date === clock.date;
  const scroller = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState<PipelineStepKey | null>(null);

  const inSession = live && clock.minutes >= SESSION_START && clock.minutes <= SESSION_END;
  const views: StepView[] = steps.map((step) => ({
    ...step,
    position: positionOf(STEP_MINUTES[step.key]),
    color: colors[step.key],
    running: step.key === "watch" && inSession,
    stale:
      !step.done &&
      step.note?.kind === "dataThrough" &&
      data.run_date !== null &&
      step.note.date < data.run_date,
  }));
  const lastDone = views.reduce((at, v, i) => (v.done ? i : at), -1);
  const next = views.find((v, i) => i > lastDone && !v.done) ?? null;
  const nowPosition = live ? positionOf(clock.minutes) : null;
  const fill = nowPosition ?? (lastDone >= 0 ? (views[lastDone]?.position ?? 0) : 0);
  const upcoming = live && clock.minutes <= SESSION_END ? nextCheck(clock.minutes) : null;
  const checked = new Map(data.slots.map((s) => [s.slot, s]));

  // Keep "now" in view on a narrow screen, where the track scrolls sideways.
  useEffect(() => {
    const box = scroller.current;
    if (box === null || nowPosition === null || box.scrollWidth <= box.clientWidth) return;
    box.scrollLeft = (nowPosition / 100) * box.scrollWidth - box.clientWidth / 2;
  }, [nowPosition]);

  const sessionLeft = positionOf(SESSION_START);
  const sessionRight = positionOf(SESSION_END);

  const openView = views.find((v) => v.key === open) ?? null;
  return (
    <div className="@container">
      <StepList
        views={views}
        data={data}
        open={open}
        onToggle={(key) => setOpen((now) => (now === key ? null : key))}
      />
      <div ref={scroller} className="hidden overflow-x-auto pb-1 @3xl:block">
        {/* Side padding so the first and last step labels are never clipped at the edges. */}
        <div className="min-w-[820px] px-16">
          <div className="relative h-[112px]">
            {/* The session band the ticks sit in. */}
            <div
              aria-hidden
              className="absolute rounded-[var(--radius-input)]"
              style={{
                left: `${sessionLeft}%`,
                width: `${sessionRight - sessionLeft}%`,
                top: TRACK_TOP - 18,
                height: 36,
                backgroundColor: tint(colors.watch, 8),
              }}
            />
            {/* While the session is on, a soft light sweeps 09:00 → now: the market is being
                watched right now. Clipped at now, so it never runs ahead of the present. */}
            {inSession && nowPosition !== null ? (
              <div
                aria-hidden
                className="absolute overflow-hidden rounded-[var(--radius-input)]"
                style={{
                  left: `${sessionLeft}%`,
                  width: `${Math.max(nowPosition - sessionLeft, 0)}%`,
                  top: TRACK_TOP - 18,
                  height: 36,
                }}
              >
                <span
                  className="watch-scan absolute inset-y-0 left-0 w-2/5"
                  style={{
                    backgroundImage: `linear-gradient(90deg, transparent, ${tint(colors.watch, 30)}, transparent)`,
                  }}
                />
              </div>
            ) : null}
            {/* Base track and the progress fill, which runs through each step's colour. */}
            <div
              aria-hidden
              className="absolute right-0 left-0 h-1 rounded-full bg-[var(--border)]"
              style={{ top: TRACK_TOP }}
            />
            <motion.div
              aria-hidden
              className="absolute left-0 h-1 rounded-full"
              style={{
                top: TRACK_TOP,
                backgroundImage: `linear-gradient(90deg, ${colors.screen}, ${colors.watch}, ${colors.prices}, ${colors.close}, ${colors.flows})`,
                backgroundSize: `${fill > 0 ? (100 / fill) * 100 : 100}% 100%`,
              }}
              initial={reduceMotion ? false : { width: 0 }}
              animate={{ width: `${fill}%` }}
              transition={{ duration: reduceMotion ? 0 : 0.8, ease: "easeOut" }}
            />
            {/* The 28 checks: filled once run, taller and coloured when something met its conditions. */}
            {SESSION_SLOTS.map((slot, index) => {
              const run = checked.get(slot);
              const left = positionOf(minutesOf(slot));
              const hit = run !== undefined && run.all_met > 0;
              return (
                // The ticks grow in left to right once (the day's checks replaying), a hit glows,
                // and a tick lifts under the pointer.
                <motion.span
                  key={slot}
                  aria-hidden
                  initial={reduceMotion ? false : { scaleY: 0, opacity: 0 }}
                  animate={{ scaleY: 1, opacity: run ? 1 : 0.5 }}
                  transition={{
                    duration: reduceMotion ? 0 : 0.28,
                    delay: reduceMotion ? 0 : 0.35 + index * 0.022,
                    ease: "easeOut",
                  }}
                  title={
                    run
                      ? t("pipeline.hitsAt", {
                          slot,
                          checked: run.checked,
                          met: run.all_met,
                          alerted: run.alertable,
                        })
                      : slot
                  }
                  className="absolute w-[3px] -translate-x-1/2 cursor-default rounded-full transition-[scale,filter] duration-150 hover:scale-y-125 hover:brightness-125 motion-reduce:transition-none motion-reduce:hover:scale-y-100"
                  style={{
                    left: `${left}%`,
                    top: hit ? TRACK_TOP - 12 : TRACK_TOP - 7,
                    height: hit ? 28 : 18,
                    backgroundColor: tickColor(run !== undefined, hit, colors.watch),
                    boxShadow: hit ? `0 0 8px ${tint(colors.watch, 70)}` : undefined,
                  }}
                />
              );
            })}
            {/* A moving dash from the last finished step to the next one; amber and still when the
            next step's data is stale. */}
            {next !== null && lastDone >= 0 ? (
              <span
                aria-hidden
                className={cn("absolute h-1 rounded-full", live && !next.stale && "watch-flow")}
                style={{
                  top: TRACK_TOP,
                  left: `${views[lastDone]?.position ?? 0}%`,
                  width: `${next.position - (views[lastDone]?.position ?? 0)}%`,
                  ...flowStyle(next, live),
                }}
              />
            ) : null}
            {/* Now. */}
            {nowPosition !== null ? (
              <div
                className="absolute flex -translate-x-1/2 flex-col items-center"
                style={{ left: `${nowPosition}%`, top: 0 }}
              >
                <span className="whitespace-nowrap rounded-[var(--radius-pill)] bg-[var(--text)] px-1.5 py-px font-num text-[10.5px] text-[var(--bg)] tabular-nums">
                  {t("pipeline.now", { time: hhmm(clock.minutes) })}
                </span>
                <span aria-hidden className="h-[38px] w-px bg-[var(--text)] opacity-60" />
              </div>
            ) : null}
            {/* The next check, while the session is on (or about to be). */}
            {upcoming !== null ? (
              <span
                className="absolute -translate-x-1/2 whitespace-nowrap rounded-[var(--radius-pill)] px-2 py-0.5 text-[11px] text-[var(--text)]"
                style={{
                  left: `${(sessionLeft + sessionRight) / 2}%`,
                  top: TRACK_TOP + 20,
                  backgroundColor: tint(colors.watch, 18),
                }}
              >
                {t("pipeline.nextCheck", { time: upcoming.slot, minutes: upcoming.inMinutes })}
              </span>
            ) : null}
            {/* The steps. */}
            {views.map((view, index) => (
              <StepMarker
                key={view.key}
                view={view}
                index={index}
                open={open === view.key}
                onOpen={() => setOpen(view.key)}
                onToggle={() => setOpen((now) => (now === view.key ? null : view.key))}
                onClose={() => setOpen((now) => (now === view.key ? null : now))}
              />
            ))}
          </div>
        </div>
      </div>
      <div id="watch-step-details" aria-live="polite" className="hidden @3xl:block">
        {openView !== null ? (
          <motion.div
            key={openView.key}
            initial={reduceMotion ? false : { opacity: 0, y: -4 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: reduceMotion ? 0 : 0.18 }}
            className="mt-1 rounded-[var(--radius-input)] p-3 text-[12px]"
            style={{
              backgroundColor: tint(openView.stale ? "var(--warn)" : openView.color, 8),
              borderLeft: `3px solid ${openView.stale ? "var(--warn)" : openView.color}`,
            }}
          >
            <StepDetails view={openView} data={data} />
          </motion.div>
        ) : null}
      </div>
    </div>
  );
}

/** The same day as a vertical list, for a narrow container where the sideways track has no room for
 * its labels: one row per step with its time, status and note; tap a row for what it does. */
function StepList({
  views,
  data,
  open,
  onToggle,
}: Readonly<{
  views: readonly StepView[];
  data: StrategyWatchToday;
  open: PipelineStepKey | null;
  onToggle: (key: PipelineStepKey) => void;
}>): ReactNode {
  const t = useTranslations("watch");
  return (
    <ol className="m-0 flex list-none flex-col p-0 @3xl:hidden">
      {views.map((view, index) => {
        const expanded = open === view.key;
        const color = view.stale ? "var(--warn)" : view.color;
        return (
          <li key={view.key} className="relative pl-9">
            {index < views.length - 1 ? (
              <span
                aria-hidden
                className="absolute top-7 bottom-0 left-[13px] w-px bg-[var(--border)]"
              />
            ) : null}
            <button
              type="button"
              onClick={() => onToggle(view.key)}
              aria-expanded={expanded}
              aria-controls={`watch-step-${view.key}`}
              className="flex min-h-11 w-full flex-col items-start gap-0.5 py-2 text-left focus-visible:outline-2 focus-visible:outline-[var(--accent)] focus-visible:outline-offset-2"
            >
              <span
                aria-hidden
                className="absolute top-2 left-0 flex size-[26px] items-center justify-center rounded-full border-2"
                style={{
                  borderColor: color,
                  backgroundColor: view.done ? view.color : "var(--surface)",
                }}
              >
                {view.done ? (
                  <CheckIcon size={13} weight="bold" className="text-[var(--bg)]" aria-hidden />
                ) : (
                  <span className="size-2 rounded-full" style={{ backgroundColor: color }} />
                )}
              </span>
              <span className="flex flex-wrap items-baseline gap-x-2">
                <span className="font-medium text-[13px] text-[var(--text)]">
                  {t(`pipeline.${view.key}`)}
                </span>
                <span className="text-[11px] text-[var(--text-muted)]">
                  {t(`pipeline.${statusKey(view)}`)}
                </span>
              </span>
              <StepNote view={view} wrap />
            </button>
            {expanded ? (
              <div
                id={`watch-step-${view.key}`}
                className="mb-2 rounded-[var(--radius-input)] p-3 text-[12px]"
                style={{
                  backgroundColor: tint(color, 8),
                  borderLeft: `3px solid ${color}`,
                }}
              >
                <StepDetails view={view} data={data} />
              </div>
            ) : null}
          </li>
        );
      })}
    </ol>
  );
}

function StepMarker({
  view,
  index,
  open,
  onOpen,
  onToggle,
  onClose,
}: Readonly<{
  view: StepView;
  index: number;
  open: boolean;
  onOpen: () => void;
  onToggle: () => void;
  onClose: () => void;
}>): ReactNode {
  const t = useTranslations("watch");
  const reduceMotion = useReducedMotion();
  const cardId = "watch-step-details";
  const keyLabel = t(`pipeline.${view.key}`);
  const statusLabel = t(`pipeline.${statusKey(view)}`);
  return (
    <motion.div
      className="absolute flex -translate-x-1/2 flex-col items-center"
      style={{ left: `${view.position}%`, top: TRACK_TOP - 11 }}
      initial={reduceMotion ? false : { opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: reduceMotion ? 0 : 0.3, delay: reduceMotion ? 0 : index * 0.07 }}
      onHoverStart={onOpen}
      onHoverEnd={onClose}
    >
      <button
        type="button"
        onClick={onToggle}
        onFocus={onOpen}
        aria-expanded={open}
        aria-controls={cardId}
        aria-label={`${keyLabel} · ${statusLabel}`}
        className="relative flex size-[26px] items-center justify-center rounded-full border-2 bg-[var(--surface)] focus-visible:outline-2 focus-visible:outline-[var(--accent)] focus-visible:outline-offset-2"
        style={{
          borderColor: view.stale ? "var(--warn)" : view.color,
          backgroundColor: view.done ? view.color : "var(--surface)",
        }}
      >
        {view.running ? (
          <span
            aria-hidden
            className="watch-breathe absolute inset-0 rounded-full"
            style={{ backgroundColor: view.color }}
          />
        ) : null}
        {view.done ? (
          <CheckIcon size={13} weight="bold" className="text-[var(--bg)]" aria-hidden />
        ) : (
          <span
            aria-hidden
            className="size-2 rounded-full"
            style={{ backgroundColor: view.stale ? "var(--warn)" : view.color }}
          />
        )}
      </button>
      <span className="mt-1.5 whitespace-nowrap font-medium text-[12px] text-[var(--text)]">
        {t(`pipeline.${view.key}`)}
      </span>
      <StepNote view={view} />
    </motion.div>
  );
}

function StepNote({ view, wrap = false }: Readonly<{ view: StepView; wrap?: boolean }>): ReactNode {
  const t = useTranslations("watch");
  const note = view.note;
  if (note === null) return null;
  let text: string;
  switch (note.kind) {
    case "asOf":
      text = t("asOf", { date: note.date });
      break;
    case "lastCheck":
      text = t("pipeline.lastCheck", { time: note.time });
      break;
    case "notStarted":
      text = t("pipeline.notStarted");
      break;
    case "dataThrough":
      text = t("pipeline.dataThrough", { date: note.date });
      break;
    default:
      text = t("pipeline.noData");
  }
  return (
    <span
      className={cn(
        wrap ? "text-[11px]" : "whitespace-nowrap text-[11px]",
        view.stale ? "text-[var(--warn)]" : "text-[var(--text-muted)]",
      )}
    >
      {view.stale ? "⚠ " : ""}
      {text}
    </span>
  );
}

function StepDetails({
  view,
  data,
}: Readonly<{ view: StepView; data: StrategyWatchToday }>): ReactNode {
  const t = useTranslations("watch");
  const lines: string[] = [];
  if (view.key === "screen" && data.run_date !== null) {
    const total = Object.values(data.counts).reduce((sum, n) => sum + n, 0);
    lines.push(t("pipeline.setups", { count: total }));
  }
  if (view.key === "watch") {
    lines.push(t("pipeline.checks", { done: data.slots.length, total: SESSION_SLOTS.length }));
    const last = data.slots.at(-1);
    if (last) {
      lines.push(
        t("pipeline.hitsAt", {
          slot: last.slot,
          checked: last.checked,
          met: last.all_met,
          alerted: last.alertable,
        }),
      );
    }
  }
  if (view.key === "close") {
    lines.push(
      t("pipeline.closeCount", { count: data.events.filter((e) => e.kind === "close").length }),
    );
  }
  if (view.stale) lines.push(t("pipeline.stale"));
  return (
    <>
      <p className="mb-1 font-medium text-[12.5px] text-[var(--text)]">
        {t(`pipeline.${view.key}`)}
      </p>
      <p className="text-[var(--text-muted)] leading-relaxed">{t(`pipeline.desc.${view.key}`)}</p>
      {lines.length > 0 ? (
        <ul className="mt-2 space-y-0.5 border-[var(--border)] border-t pt-2 text-[var(--text)]">
          {lines.map((line) => (
            <li key={line}>{line}</li>
          ))}
        </ul>
      ) : null}
    </>
  );
}
