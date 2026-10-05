import type { ReactNode } from "react";
import { SettingsDialog } from "@/components/settings/settings-dialog";

/**
 * Hosts the dialog shell in a layout — not the catch-all page — so it persists while the user
 * switches sections. A page under `[[...rest]]` remounts whenever `rest` changes, which replayed the
 * overlay's fade-in (the blurred backdrop flickered off/on on every nav click); a layout does not.
 */
export default function InterceptedSettingsLayout({
  children,
}: {
  children: ReactNode;
}): ReactNode {
  return <SettingsDialog>{children}</SettingsDialog>;
}
