"use client";

import { CheckIcon } from "@phosphor-icons/react";
import { motion, useReducedMotion } from "motion/react";
import { useTranslations } from "next-intl";
import { type ReactNode, useEffect, useRef, useState } from "react";
import type { PipelineStep, PipelineStepKey } from "@/components/watch/run-status";
import {
  checksStalled,
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
import type { StrategyWatchSlot, StrategyWatchToday } from "@/types/strategy-watch";

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
function flowStyle({
  next,
  live,
}: Readonly<{ next: StepView; live: boolean }>): Record<string, string> {
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

/** True when `step` is not done and its note says its data stops before the list's session. */
function isStale(step: PipelineStep, runDate: string | null): boolean {
  if (step.done || runDate === null) return false;
  return step.note?.kind === "dataThrough" && step.note.date < runDate;
}

function buildViews(
  steps: readonly PipelineStep[],
  colors: Record<PipelineStepKey, string>,
  runDate: string | null,
  watch: Readonly<{ inSession: boolean; stalledSince: string | null | undefined }>,
): StepView[] {
  return steps.map((step) => {
    // `undefined` = the checks are landing; `null` = stalled with no check yet today.
    const stalled = step.key === "watch" && watch.stalledSince !== undefined;
    return {
      ...step,
      note: stalled ? { kind: "stalled", time: watch.stalledSince ?? null } : step.note,
      position: positionOf(STEP_MINUTES[step.key]),
      color: colors[step.key],
      running: step.key === "watch" && watch.inSession && !stalled,
      stale: stalled || isStale(step, runDate),
    };
  });
}

function lastDoneIndex(views: readonly StepView[]): number {
  return views.reduce((at, v, i) => (v.done ? i : at), -1);
}

function nextPending(views: readonly StepView[], lastDone: number): StepView | null {
  return views.find((v, i) => i > lastDone && !v.done) ?? null;
}

/** How far the progress fill runs: up to now on today's list, else to the last finished step. */
function fillOf(nowPosition: number | null, lastDone: number, views: readonly StepView[]): number {
  if (nowPosition !== null) return nowPosition;
  if (lastDone < 0) return 0;
  return views[lastDone]?.position ?? 0;
}

type Upcoming = ReturnType<typeof nextCheck>;

function upcomingCheck({ live, minutes }: Readonly<{ live: boolean; minutes: number }>): Upcoming {
  return live && minutes <= SESSION_END ? nextCheck(minutes) : null;
}

/** Keep "now" in view on a narrow screen, where the track scrolls sideways. */
function centerOnNow(box: HTMLDivElement | null, nowPosition: number | null): void {
  if (box === null || nowPosition === null || box.scrollWidth <= box.clientWidth) return;
  box.scrollLeft = (nowPosition / 100) * box.scrollWidth - box.clientWidth / 2;
}

function toggled(current: PipelineStepKey | null, key: PipelineStepKey): PipelineStepKey | null {
  return current === key ? null : key;
}

function closed(current: PipelineStepKey | null, key: PipelineStepKey): PipelineStepKey | null {
  return current === key ? null : current;
}

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
  const now = useNow();
  const clock = taipeiClock(now);
  const live = data.run_date !== null && data.run_date === clock.date;
  const scroller = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState<PipelineStepKey | null>(null);

  const inSession = live && clock.minutes >= SESSION_START && clock.minutes <= SESSION_END;
  const stalled = checksStalled({ live, minutes: clock.minutes, lastSlot: data.last_slot });
  const views = buildViews(steps, colors, data.run_date, {
    inSession,
    stalledSince: stalled ? data.last_slot : undefined,
  });
  const lastDone = lastDoneIndex(views);
  const next = nextPending(views, lastDone);
  const nowPosition = live ? positionOf(clock.minutes) : null;
  const fill = fillOf(nowPosition, lastDone, views);
  const upcoming = upcomingCheck({ live, minutes: clock.minutes });
  const checked = new Map(data.slots.map((s) => [s.slot, s]));

  useEffect(() => {
    centerOnNow(scroller.current, nowPosition);
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
        onToggle={(key) => setOpen((cur) => toggled(cur, key))}
      />
      <div ref={scroller} className="hidden overflow-x-auto pb-1 @3xl:block">
        {/* Side padding so the first and last step labels are never clipped at the edges. */}
        <div className="min-w-[820px] px-16">
          <div className="relative h-[112px]">
            <SessionBand left={sessionLeft} right={sessionRight} color={colors.watch} />
            <SessionSweep
              inSession={inSession && !stalled}
              nowPosition={nowPosition}
              left={sessionLeft}
              color={colors.watch}
            />
            <TrackFill fill={fill} colors={colors} />
            <CheckTicks checked={checked} color={colors.watch} />
            <FlowConnector views={views} lastDone={lastDone} next={next} live={live} />
            <NowMarker nowPosition={nowPosition} minutes={clock.minutes} />
            <NextCheckPill
              upcoming={upcoming}
              left={sessionLeft}
              right={sessionRight}
              color={colors.watch}
            />
            {/* The steps. */}
            {views.map((view, index) => (
              <StepMarker
                key={view.key}
                view={view}
                index={index}
                open={open === view.key}
                onOpen={() => setOpen(view.key)}
                onToggle={() => setOpen((cur) => toggled(cur, view.key))}
                onClose={() => setOpen((cur) => closed(cur, view.key))}
              />
            ))}
          </div>
        </div>
      </div>
      <div id="watch-step-details" aria-live="polite" className="hidden @3xl:block">
        <OpenStepCard openView={openView} data={data} />
      </div>
    </div>
  );
}

/** The session band the ticks sit in. */
function SessionBand({
  left,
  right,
  color,
}: Readonly<{ left: number; right: number; color: string }>): ReactNode {
  return (
    <div
      aria-hidden
      className="absolute rounded-[var(--radius-input)]"
      style={{
        left: `${left}%`,
        width: `${right - left}%`,
        top: TRACK_TOP - 18,
        height: 36,
        backgroundColor: tint(color, 8),
      }}
    />
  );
}

/** While the session is on, a soft light sweeps 09:00 → now: the market is being watched right
 * now. Clipped at now, so it never runs ahead of the present. */
function SessionSweep({
  inSession,
  nowPosition,
  left,
  color,
}: Readonly<{
  inSession: boolean;
  nowPosition: number | null;
  left: number;
  color: string;
}>): ReactNode {
  if (!inSession || nowPosition === null) return null;
  return (
    <div
      aria-hidden
      className="absolute overflow-hidden rounded-[var(--radius-input)]"
      style={{
        left: `${left}%`,
        width: `${Math.max(nowPosition - left, 0)}%`,
        top: TRACK_TOP - 18,
        height: 36,
      }}
    >
      <span
        className="watch-scan absolute inset-y-0 left-0 w-2/5"
        style={{
          backgroundImage: `linear-gradient(90deg, transparent, ${tint(color, 30)}, transparent)`,
        }}
      />
    </div>
  );
}

/** Base track and the progress fill, which runs through each step's colour. */
function TrackFill({
  fill,
  colors,
}: Readonly<{ fill: number; colors: Record<PipelineStepKey, string> }>): ReactNode {
  const reduceMotion = useReducedMotion();
  return (
    <>
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
    </>
  );
}

/** The 28 checks: filled once run, taller and coloured when something met its conditions. */
function CheckTicks({
  checked,
  color,
}: Readonly<{ checked: ReadonlyMap<string, StrategyWatchSlot>; color: string }>): ReactNode {
  return SESSION_SLOTS.map((slot, index) => (
    <CheckTick key={slot} slot={slot} index={index} run={checked.get(slot)} color={color} />
  ));
}

function CheckTick({
  slot,
  index,
  run,
  color,
}: Readonly<{
  slot: string;
  index: number;
  run: StrategyWatchSlot | undefined;
  color: string;
}>): ReactNode {
  const t = useTranslations("watch");
  const reduceMotion = useReducedMotion();
  const left = positionOf(minutesOf(slot));
  const hit = run !== undefined && run.all_met > 0;
  return (
    // The ticks grow in left to right once (the day's checks replaying), a hit glows,
    // and a tick lifts under the pointer.
    <motion.span
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
        backgroundColor: tickColor(run !== undefined, hit, color),
        boxShadow: hit ? `0 0 8px ${tint(color, 70)}` : undefined,
      }}
    />
  );
}

/** A moving dash from the last finished step to the next one; amber and still when the next
 * step's data is stale. */
function FlowConnector({
  views,
  lastDone,
  next,
  live,
}: Readonly<{
  views: readonly StepView[];
  lastDone: number;
  next: StepView | null;
  live: boolean;
}>): ReactNode {
  if (next === null || lastDone < 0) return null;
  const from = views[lastDone]?.position ?? 0;
  return (
    <span
      aria-hidden
      className={cn("absolute h-1 rounded-full", live && !next.stale && "watch-flow")}
      style={{
        top: TRACK_TOP,
        left: `${from}%`,
        width: `${next.position - from}%`,
        ...flowStyle({ next, live }),
      }}
    />
  );
}

/** Now. */
function NowMarker({
  nowPosition,
  minutes,
}: Readonly<{ nowPosition: number | null; minutes: number }>): ReactNode {
  const t = useTranslations("watch");
  if (nowPosition === null) return null;
  return (
    <div
      className="absolute flex -translate-x-1/2 flex-col items-center"
      style={{ left: `${nowPosition}%`, top: 0 }}
    >
      <span className="whitespace-nowrap rounded-[var(--radius-pill)] bg-[var(--text)] px-1.5 py-px font-num text-[10.5px] text-[var(--bg)] tabular-nums">
        {t("pipeline.now", { time: hhmm(minutes) })}
      </span>
      <span aria-hidden className="h-[38px] w-px bg-[var(--text)] opacity-60" />
    </div>
  );
}

/** The next check, while the session is on (or about to be). */
function NextCheckPill({
  upcoming,
  left,
  right,
  color,
}: Readonly<{ upcoming: Upcoming; left: number; right: number; color: string }>): ReactNode {
  const t = useTranslations("watch");
  if (upcoming === null) return null;
  return (
    <span
      className="absolute -translate-x-1/2 whitespace-nowrap rounded-[var(--radius-pill)] px-2 py-0.5 text-[11px] text-[var(--text)]"
      style={{
        left: `${(left + right) / 2}%`,
        top: TRACK_TOP + 20,
        backgroundColor: tint(color, 18),
      }}
    >
      {t("pipeline.nextCheck", { time: upcoming.slot, minutes: upcoming.inMinutes })}
    </span>
  );
}

/** The detail card of the step that is open on the wide track. */
function OpenStepCard({
  openView,
  data,
}: Readonly<{ openView: StepView | null; data: StrategyWatchToday }>): ReactNode {
  const reduceMotion = useReducedMotion();
  if (openView === null) return null;
  return (
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
    case "stalled":
      text = t("pipeline.stalled");
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
  if (view.note?.kind === "stalled") {
    lines.push(
      view.note.time === null
        ? t("pipeline.stalledNone")
        : t("pipeline.stalledDetail", { time: view.note.time }),
    );
  } else if (view.stale) {
    lines.push(t("pipeline.stale"));
  }
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
