import { setRequestLocale } from "next-intl/server";
import type { ReactNode } from "react";
import { ProfileForm } from "./profile-form";

export default async function ProfilePage({
  params,
}: {
  params: Promise<{ locale: string }>;
}): Promise<ReactNode> {
  const { locale } = await params;
  setRequestLocale(locale);
  return <ProfileForm />;
}
