import { setRequestLocale } from "next-intl/server";
import type { ReactNode } from "react";
import { SettingsNav } from "@/components/settings/settings-nav";

/**
 * Two-pane Settings shell: left sub-page nav + right content
 * pane, single column ~640px max on the right. Stacks (nav above content) below `lg`. Stays a
 * server component — the nav itself is the one client leaf that needs `usePathname`.
 */
export default async function SettingsLayout({
  children,
  params,
}: {
  children: ReactNode;
  params: Promise<{ locale: string }>;
}): Promise<ReactNode> {
  const { locale } = await params;
  setRequestLocale(locale);
  return (
    <div className="mx-auto flex min-h-full max-w-4xl flex-col gap-6 p-6 lg:flex-row lg:gap-10">
      <SettingsNav />
      <div className="min-w-0 flex-1">
        <div className="mx-auto max-w-[640px]">{children}</div>
      </div>
    </div>
  );
}
