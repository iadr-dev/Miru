"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { type ReactNode, useState } from "react";
import { DefaultModelSetting } from "@/app/[locale]/(app)/settings/byok/default-model-setting";
import { Select, TextInput } from "@/components/settings/form-controls";
import { SettingsRow, SettingsSection } from "@/components/settings/settings-section";
import { SavedToast } from "@/components/settings/settings-states";
import { Button } from "@/components/ui/button";
import { QueryFailure } from "@/components/ui/query-state";
import { ProviderBadge } from "@/components/usage/provider-badge";
import { queryKeys } from "@/lib/api/query-keys";
import { listSavedKeys, putKey, removeKey, SettingsApiError } from "@/lib/api/settings";
import { LLM_PROVIDERS, type LlmProvider } from "@/types/settings";

/** A key the server has but will never send back: a generic mask when it has no preview. */
const GENERIC_MASK = "••••••••••••";

/** BYOK keys, one per LLM provider. The provider select drives the field below it:
 * - a provider with a saved key shows that key MASKED (a server-made preview, never the key) with
 *   "Replace" / "Remove";
 * - a provider without one shows an empty field to paste into.
 * Keys live on the server against the account (not in cookies or browser storage), so they survive
 * signing out and a cleared cookie jar; the raw value is write-only and cleared after saving. */
export function ByokPanel(): ReactNode {
  const t = useTranslations("settings.byok");
  const tc = useTranslations("settings.common");

  const [provider, setProvider] = useState<LlmProvider>("anthropic");
  const [key, setKey] = useState("");
  const [replacing, setReplacing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const queryClient = useQueryClient();
  const keysQuery = useQuery({ queryKey: queryKeys.byokKeys, queryFn: listSavedKeys });
  const savedKeys = keysQuery.data ?? [];
  const current = savedKeys.find((k) => k.provider === provider) ?? null;
  const showMasked = current !== null && !replacing;

  const chooseProvider = (next: LlmProvider): void => {
    setProvider(next);
    setKey("");
    setReplacing(false);
    setError(null);
  };

  // Saving or removing a key changes the chat limit, the model picker and the usage page.
  const refreshKeyDependents = (): void => {
    void queryClient.invalidateQueries({ queryKey: queryKeys.byokKeys });
    void queryClient.invalidateQueries({ queryKey: queryKeys.me });
    void queryClient.invalidateQueries({ queryKey: queryKeys.settings("usage-quota") });
  };
  const flashSaved = (): void => {
    setSaved(true);
    window.setTimeout(() => setSaved(false), 2500);
  };

  const putMutation = useMutation({
    mutationFn: () => putKey({ provider, kind: "llm_key", key }),
    onSuccess: () => {
      refreshKeyDependents();
      setKey("");
      setReplacing(false);
      setError(null);
      flashSaved();
    },
    onError: () => setError(tc("errorGeneric")),
  });

  const removeMutation = useMutation({
    mutationFn: () => removeKey(provider, "llm_key"),
    onSuccess: () => {
      refreshKeyDependents();
      setKey("");
      setReplacing(false);
      setError(null);
      flashSaved();
    },
    onError: (err) => {
      setError(
        err instanceof SettingsApiError && err.status === 404
          ? t("noKeyToRemove")
          : tc("errorGeneric"),
      );
    },
  });

  const noSavedKeys = keysQuery.isError ? (
    <QueryFailure query={keysQuery} />
  ) : (
    <p className="text-[13px] text-[var(--text-muted)]">{t("noneSaved")}</p>
  );

  return (
    <SettingsSection title={t("title")} description={t("description")}>
      <div className="space-y-4 py-1">
        <div className="flex flex-col gap-2">
          <p className="font-medium text-[14px] text-[var(--text)]">{t("saved")}</p>
          {savedKeys.length > 0 ? (
            <ul className="m-0 flex list-none flex-wrap gap-1.5 p-0">
              {savedKeys.map((k) => (
                <li key={k.provider}>
                  <button
                    type="button"
                    aria-label={t("selectProvider", {
                      provider: t(`providerOption.${k.provider}`),
                    })}
                    onClick={() => chooseProvider(k.provider as LlmProvider)}
                    className="!outline-none rounded-full focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
                  >
                    <ProviderBadge provider={k.provider} />
                  </button>
                </li>
              ))}
            </ul>
          ) : (
            noSavedKeys
          )}
          <p className="text-[12px] text-[var(--text-muted)] leading-relaxed">{t("howItWorks")}</p>
        </div>

        <SettingsRow label={t("provider")}>
          <Select
            aria-label={t("provider")}
            value={provider}
            onChange={(e) => chooseProvider(e.target.value as LlmProvider)}
            className="w-48"
          >
            {LLM_PROVIDERS.map((p) => (
              <option key={p} value={p}>
                {savedKeys.some((k) => k.provider === p)
                  ? t("providerSaved", { provider: t(`providerOption.${p}`) })
                  : t(`providerOption.${p}`)}
              </option>
            ))}
          </Select>
        </SettingsRow>

        <SettingsRow label={t("key")} hint={showMasked ? t("keySavedHint") : t("llmKeyHint")}>
          {showMasked ? (
            <TextInput
              aria-label={t("key")}
              type="text"
              readOnly
              value={current?.hint ?? GENERIC_MASK}
              className="w-56 font-num text-[var(--text-muted)]"
            />
          ) : (
            <TextInput
              aria-label={t("key")}
              type="password"
              autoComplete="off"
              value={key}
              onChange={(e) => setKey(e.target.value)}
              placeholder={t("keyPlaceholder")}
              className="w-56"
            />
          )}
        </SettingsRow>

        {error ? (
          <p role="alert" className="text-[13px] text-[var(--warn)]">
            {error}
          </p>
        ) : null}

        <div className="flex flex-wrap gap-2">
          {showMasked ? (
            <>
              <Button variant="outline" size="sm" onClick={() => setReplacing(true)}>
                {t("replace")}
              </Button>
              <Button
                variant="outline"
                size="sm"
                disabled={removeMutation.isPending}
                onClick={() => removeMutation.mutate()}
              >
                {t("remove")}
              </Button>
            </>
          ) : (
            <>
              <Button
                variant="filled"
                size="sm"
                disabled={!key || putMutation.isPending}
                onClick={() => putMutation.mutate()}
              >
                {t("save")}
              </Button>
              {current !== null ? (
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => {
                    setReplacing(false);
                    setKey("");
                  }}
                >
                  {t("cancel")}
                </Button>
              ) : null}
            </>
          )}
        </div>

        <div className="border-[var(--border)] border-t pt-4">
          <DefaultModelSetting />
        </div>
      </div>
      {saved ? <SavedToast message={tc("saved")} /> : null}
    </SettingsSection>
  );
}
