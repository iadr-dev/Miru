import { redirect } from "@/i18n/navigation";

/** `/settings` has no content of its own — it always redirects to the first sub-page. */
export default async function SettingsIndexPage({
  params,
}: {
  params: Promise<{ locale: string }>;
}): Promise<never> {
  const { locale } = await params;
  return redirect({ href: "/settings/profile", locale });
}
