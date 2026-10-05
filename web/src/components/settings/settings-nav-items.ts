import type { Icon } from "@phosphor-icons/react";
import {
  BellIcon,
  BrainIcon,
  CreditCardIcon,
  GaugeIcon,
  KeyIcon,
  LinkIcon,
  NotebookIcon,
  PawPrintIcon,
  SpeakerHighIcon,
  UserIcon,
} from "@phosphor-icons/react";

/** The Settings sub-page list. `messageKey` indexes the
 * `settings.nav` i18n namespace. Order matches the spec's table top-to-bottom. */
export type SettingsNavItem = {
  href:
    | "/settings/profile"
    | "/settings/connections"
    | "/settings/personality"
    | "/settings/pets"
    | "/settings/billing"
    | "/settings/byok"
    | "/settings/usage"
    | "/settings/alerts"
    | "/settings/notifications"
    | "/settings/memory";
  messageKey:
    | "profile"
    | "connections"
    | "personality"
    | "pets"
    | "billing"
    | "byok"
    | "usage"
    | "alerts"
    | "notifications"
    | "memory";
  icon: Icon;
};

export const SETTINGS_NAV_ITEMS: readonly SettingsNavItem[] = [
  { href: "/settings/profile", messageKey: "profile", icon: UserIcon },
  { href: "/settings/connections", messageKey: "connections", icon: LinkIcon },
  { href: "/settings/personality", messageKey: "personality", icon: BrainIcon },
  { href: "/settings/pets", messageKey: "pets", icon: PawPrintIcon },
  { href: "/settings/billing", messageKey: "billing", icon: CreditCardIcon },
  { href: "/settings/byok", messageKey: "byok", icon: KeyIcon },
  { href: "/settings/usage", messageKey: "usage", icon: GaugeIcon },
  { href: "/settings/alerts", messageKey: "alerts", icon: SpeakerHighIcon },
  { href: "/settings/notifications", messageKey: "notifications", icon: BellIcon },
  { href: "/settings/memory", messageKey: "memory", icon: NotebookIcon },
] as const;
