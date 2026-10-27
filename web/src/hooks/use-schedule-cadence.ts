"use client";

import { useTranslations } from "next-intl";
import { type ScheduleTemplate, weeklyWhenOfTrigger } from "@/lib/schedules/templates";
import type { ScheduleTrigger } from "@/types/settings";

/** The plain-language cadence of a catalogue template ("每週一 08:30"). The weekly watchlist review
 * runs on the user's own weekday and time, so its cadence is read from the trigger the job (or the
 * proposal) carries; every other template has one fixed cadence line. */
export function useScheduleCadence(): (
  template: ScheduleTemplate,
  trigger?: ScheduleTrigger,
) => string {
  const t = useTranslations("settings.alerts.schedules.templates");
  const weekdays = useTranslations("settings.alerts.schedules.weekdays");
  return (template, trigger) => {
    if (template.takesTargets) {
      return t(`${template.id}.cadence`);
    }
    const when = weeklyWhenOfTrigger(trigger ?? template.trigger);
    return t(`${template.id}.cadence`, {
      weekday: weekdays(String(when.weekday)),
      time: when.time,
    });
  };
}
