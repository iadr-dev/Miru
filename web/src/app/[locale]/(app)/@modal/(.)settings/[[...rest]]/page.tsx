import { setRequestLocale } from "next-intl/server";
import type { ReactNode } from "react";
import { SettingsPanelSwitch } from "@/app/[locale]/(app)/settings/settings-panel-switch";

/**
 * Intercepts in-app navigation to `/settings/*` and renders it as a modal over whatever page the
 * user was on, instead of a full-page navigation — the `(.)` intercepting-route convention matches
 * same-level sibling segments, i.e. any `/settings/...` reached from within the `(app)` group. A
 * hard refresh or a fresh tab bypasses interception entirely and hits the real `/settings/*` pages
 * (`../../../settings/[sub]/page.tsx`), which render the identical panel via `SettingsPanelSwitch`
 * — one content source, two entry shells.
 */
export default async function InterceptedSettingsPage({
  params,
}: Readonly<{
  params: Promise<{ locale: string; rest?: string[] }>;
}>): Promise<ReactNode> {
  const { locale, rest } = await params;
  setRequestLocale(locale);
  return <SettingsPanelSwitch segment={rest?.[0]} />;
}
