"use client";

import { useTranslations } from "next-intl";
import { type ReactNode, useState } from "react";
import { Button } from "@/components/ui/button";
import { useRouter } from "@/i18n/navigation";
import { logout } from "@/lib/api/auth";

export function SignOutButton(): ReactNode {
  const t = useTranslations("market.stub");
  const router = useRouter();
  const [pending, setPending] = useState(false);

  const onClick = async (): Promise<void> => {
    setPending(true);
    await logout();
    router.replace("/");
  };

  return (
    <Button variant="outline" size="sm" disabled={pending} onClick={onClick}>
      {t("signOut")}
    </Button>
  );
}
