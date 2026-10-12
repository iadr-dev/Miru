"use client";

import type { ReactNode } from "react";
import { AlertsPanel } from "@/app/[locale]/(app)/settings/alerts/alerts-panel";
import { BillingPanel } from "@/app/[locale]/(app)/settings/billing/billing-panel";
import { ByokPanel } from "@/app/[locale]/(app)/settings/byok/byok-panel";
import { ConnectionsPanel } from "@/app/[locale]/(app)/settings/connections/connections-panel";
import { MemoryPanel } from "@/app/[locale]/(app)/settings/memory/memory-panel";
import { NotificationsPanel } from "@/app/[locale]/(app)/settings/notifications/notifications-panel";
import { PersonalityPanel } from "@/app/[locale]/(app)/settings/personality/personality-panel";
import { PetsPanel } from "@/app/[locale]/(app)/settings/pets/pets-panel";
import { ProfilePanel } from "@/app/[locale]/(app)/settings/profile/profile-panel";
import { UsagePanel } from "@/app/[locale]/(app)/settings/usage/usage-panel";
import type { SettingsNavItem } from "@/components/settings/settings-nav-items";

const PANELS: Record<SettingsNavItem["messageKey"], () => ReactNode> = {
  profile: ProfilePanel,
  connections: ConnectionsPanel,
  personality: PersonalityPanel,
  pets: PetsPanel,
  billing: BillingPanel,
  byok: ByokPanel,
  usage: UsagePanel,
  alerts: AlertsPanel,
  notifications: NotificationsPanel,
  memory: MemoryPanel,
};

/** Maps the first `/settings/<segment>` path segment to its panel component — the single lookup
 * both the full-page route tree and the intercepted modal route resolve against, so the two never
 * drift into rendering different content for the same URL. */
export function SettingsPanelSwitch({ segment }: { segment: string | undefined }): ReactNode {
  const key = (segment ?? "profile") as SettingsNavItem["messageKey"];
  const Panel = PANELS[key] ?? ProfilePanel;
  return <Panel />;
}
