"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { type ReactNode, useState } from "react";
import { GoogleIcon, LineIcon } from "@/components/icons/brand-icons";
import { SettingsRow, SettingsSection } from "@/components/settings/settings-section";
import { SettingsError, SettingsLoading } from "@/components/settings/settings-states";
import { Button } from "@/components/ui/button";
import { listConnections, SettingsApiError, unlinkConnection } from "@/lib/api/settings";
import { OAUTH_LINK_PROVIDERS, type OAuthAccountLike } from "@/types/settings";

const PROVIDER_ICON: Record<(typeof OAUTH_LINK_PROVIDERS)[number], typeof GoogleIcon> = {
  google: GoogleIcon,
  line: LineIcon,
};

export function ConnectionsPanel(): ReactNode {
  const t = useTranslations("settings.connections");
  const tc = useTranslations("settings.common");
  const queryClient = useQueryClient();
  const [error, setError] = useState<string | null>(null);

  const query = useQuery({ queryKey: ["settings", "connections"], queryFn: listConnections });

  const mutation = useMutation({
    mutationFn: (provider: "google" | "line") => unlinkConnection(provider),
    onSuccess: () => {
      setError(null);
      void queryClient.invalidateQueries({ queryKey: ["settings", "connections"] });
    },
    onError: (err) => {
      if (err instanceof SettingsApiError && err.code === "last_login_method") {
        setError(t("errorLastMethod"));
      } else {
        setError(tc("errorGeneric"));
      }
    },
  });

  if (query.isLoading) {
    return (
      <SettingsSection title={t("title")}>
        <SettingsLoading rows={2} />
      </SettingsSection>
    );
  }

  if (query.isError) {
    return (
      <SettingsSection title={t("title")}>
        <SettingsError message={tc("errorLoad")} onRetry={() => void query.refetch()} />
      </SettingsSection>
    );
  }

  const linked = new Map<string, OAuthAccountLike>((query.data ?? []).map((c) => [c.provider, c]));

  return (
    <SettingsSection title={t("title")} description={t("description")}>
      {error ? (
        <p role="alert" className="pb-3 text-[13px] text-[var(--warn)]">
          {error}
        </p>
      ) : null}
      {OAUTH_LINK_PROVIDERS.map((provider) => {
        const account = linked.get(provider);
        const Icon = PROVIDER_ICON[provider];
        return (
          <SettingsRow
            key={provider}
            label={t(`provider.${provider}`)}
            hint={account?.email_at_provider ?? t("notLinked")}
          >
            <div className="flex items-center gap-3">
              <Icon size={20} />
              {account ? (
                <Button
                  variant="outline"
                  size="sm"
                  disabled={mutation.isPending}
                  onClick={() => mutation.mutate(provider)}
                >
                  {t("unlink")}
                </Button>
              ) : (
                <span className="text-[13px] text-[var(--text-muted)]">{t("notLinked")}</span>
              )}
            </div>
          </SettingsRow>
        );
      })}
    </SettingsSection>
  );
}
