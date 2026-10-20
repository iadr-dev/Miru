import { useEffect, useState } from "react";
import type { AlertPreferenceRequest } from "@/lib/api/settings";
import type { AlertPreference, DeliveryChannelKind } from "@/types/settings";

const DEFAULT_QUIET_START = "00:00";
const DEFAULT_QUIET_END = "08:00";

type QuietWindow = { start: string; end: string };
type QuietState = readonly [on: boolean, start: string, end: string];

/** The window as the form holds it: an empty window (start == end) is "off". */
function quietOf(window: QuietWindow | null): QuietState {
  if (window === null || window.start === window.end) {
    return [false, DEFAULT_QUIET_START, DEFAULT_QUIET_END];
  }
  return [true, window.start, window.end];
}

/** A comparable string for the form's quiet-hours state (off ignores the stale times). */
function quietSetting(on: boolean, start: string, end: string): string {
  return on ? `${start}-${end}` : "off";
}

function quietToSave(on: boolean, start: string, end: string): QuietWindow {
  if (!on) {
    return { start: DEFAULT_QUIET_START, end: DEFAULT_QUIET_START };
  }
  // An empty or zero-length window would read as "off"; fall back to the default preset.
  return start === "" || end === "" || start === end
    ? { start: DEFAULT_QUIET_START, end: DEFAULT_QUIET_END }
    : { start, end };
}

/** What the form shows for a saved window: no window yet is "on, default preset"; a saved empty
 * window is "off". */
function hydratedQuiet(window: QuietWindow | null): QuietState {
  const off = window !== null && window.start === window.end;
  if (window && !off) {
    return [true, window.start, window.end];
  }
  return [!off, DEFAULT_QUIET_START, DEFAULT_QUIET_END];
}

/** Order-insensitive equality check for the channel set — the sort is only to normalize
 * ordering before comparing, so an explicit lexicographic compare fn (not locale-aware) is
 * both correct and required. */
function byCodePoint(a: string, b: string): number {
  if (a < b) return -1;
  return a > b ? 1 : 0;
}

/** Add or remove one channel from the selection. */
export function withChannel(
  prev: DeliveryChannelKind[] | null,
  channel: DeliveryChannelKind,
  checked: boolean,
): DeliveryChannelKind[] {
  const base = prev ?? [];
  return checked ? [...base, channel] : base.filter((c) => c !== channel);
}

type NotificationFormValues = {
  readonly channels: DeliveryChannelKind[] | null;
  readonly quietOn: boolean;
  readonly quietStart: string;
  readonly quietEnd: string;
  readonly morningDigest: boolean;
  readonly timezone: string;
};

/** Whether the form differs from what is saved. */
export function isFormDirty(form: NotificationFormValues, original: AlertPreference): boolean {
  const { channels, quietOn, quietStart, quietEnd, morningDigest, timezone } = form;
  return (
    JSON.stringify([...(channels ?? [])].sort(byCodePoint)) !==
      JSON.stringify([...original.channels].sort(byCodePoint)) ||
    quietSetting(quietOn, quietStart, quietEnd) !==
      quietSetting(...quietOf(original.quiet_hours)) ||
    morningDigest !== original.morning_digest ||
    timezone !== original.timezone
  );
}

export function toSaveRequest(form: NotificationFormValues): AlertPreferenceRequest {
  return {
    channels: form.channels ?? [],
    quiet_hours: quietToSave(form.quietOn, form.quietStart, form.quietEnd),
    morning_digest: form.morningDigest,
    timezone: form.timezone,
  };
}

export type NotificationForm = NotificationFormValues & {
  readonly setChannels: (
    update: (prev: DeliveryChannelKind[] | null) => DeliveryChannelKind[],
  ) => void;
  readonly setQuietOn: (on: boolean) => void;
  readonly setQuietStart: (start: string) => void;
  readonly setQuietEnd: (end: string) => void;
  readonly setMorningDigest: (on: boolean) => void;
  readonly setTimezone: (timezone: string) => void;
  /** Put every field back to the saved values. */
  readonly reset: (original: AlertPreference) => void;
};

/**
 * The editable copy of the saved preferences. It starts empty (`channels === null` means "not
 * loaded yet") and is filled from the first saved value that arrives. Quiet hours are on with a
 * window, or off; off is stored as an empty window (start == end), which never matches.
 */
export function useNotificationForm(saved: AlertPreference | undefined): NotificationForm {
  const [channels, setChannels] = useState<DeliveryChannelKind[] | null>(null);
  const [quietOn, setQuietOn] = useState(true);
  const [quietStart, setQuietStart] = useState(DEFAULT_QUIET_START);
  const [quietEnd, setQuietEnd] = useState(DEFAULT_QUIET_END);
  const [morningDigest, setMorningDigest] = useState(false);
  const [timezone, setTimezone] = useState("Asia/Taipei");

  useEffect(() => {
    if (saved && channels === null) {
      setChannels(saved.channels);
      const [on, start, end] = hydratedQuiet(saved.quiet_hours);
      setQuietOn(on);
      setQuietStart(start);
      setQuietEnd(end);
      setMorningDigest(saved.morning_digest);
      setTimezone(saved.timezone);
    }
  }, [saved, channels]);

  const reset = (original: AlertPreference): void => {
    setChannels(original.channels);
    const [on, start, end] = quietOf(original.quiet_hours);
    setQuietOn(on);
    setQuietStart(start);
    setQuietEnd(end);
    setMorningDigest(original.morning_digest);
    setTimezone(original.timezone);
  };

  return {
    channels,
    quietOn,
    quietStart,
    quietEnd,
    morningDigest,
    timezone,
    setChannels,
    setQuietOn,
    setQuietStart,
    setQuietEnd,
    setMorningDigest,
    setTimezone,
    reset,
  };
}
