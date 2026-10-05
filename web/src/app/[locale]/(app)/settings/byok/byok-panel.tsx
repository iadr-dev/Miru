"use client";

import { useMutation } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { type ReactNode, useState } from "react";
import { Select, TextInput } from "@/components/settings/form-controls";
import { SettingsRow, SettingsSection } from "@/components/settings/settings-section";
import { SavedToast } from "@/components/settings/settings-states";
import { Button } from "@/components/ui/button";
import { putKey, removeKey, SettingsApiError } from "@/lib/api/settings";
import { LLM_PROVIDERS, type LlmProvider } from "@/types/settings";

/** BYOK key store/remove — write-only secret, never echoed back after submission
 * (hard rule: clear the input on success). Always stores an
 * `llm_key` (the only kind that lifts the daily chat limit); `provider` is constrained to the
 * LLM providers this backend actually has an adapter for, not free text. */
export function ByokPanel(): ReactNode {
  const t = useTranslations("settings.byok");
  const tc = useTranslations("settings.common");

  const [provider, setProvider] = useState<LlmProvider>("anthropic");
  const [key, setKey] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  const putMutation = useMutation({
    mutationFn: () => putKey({ provider, kind: "llm_key", key }),
    onSuccess: () => {
      setKey("");
      setError(null);
      setSaved(true);
      window.setTimeout(() => setSaved(false), 2500);
    },
    onError: () => setError(tc("errorGeneric")),
  });

  const removeMutation = useMutation({
    mutationFn: () => removeKey(provider, "llm_key"),
    onSuccess: () => {
      setError(null);
      setSaved(true);
      window.setTimeout(() => setSaved(false), 2500);
    },
    onError: (err) => {
      setError(
        err instanceof SettingsApiError && err.status === 404
          ? t("noKeyToRemove")
          : tc("errorGeneric"),
      );
    },
  });

  return (
    <SettingsSection title={t("title")} description={t("description")}>
      <div className="space-y-4 py-1">
        <SettingsRow label={t("provider")}>
          <Select
            aria-label={t("provider")}
            value={provider}
            onChange={(e) => setProvider(e.target.value as LlmProvider)}
            className="w-48"
          >
            {LLM_PROVIDERS.map((p) => (
              <option key={p} value={p}>
                {t(`providerOption.${p}`)}
              </option>
            ))}
          </Select>
        </SettingsRow>
        <SettingsRow label={t("key")} hint={t("llmKeyHint")}>
          <TextInput
            aria-label={t("key")}
            type="password"
            autoComplete="off"
            value={key}
            onChange={(e) => setKey(e.target.value)}
            placeholder={t("keyPlaceholder")}
            className="w-56"
          />
        </SettingsRow>
        {error ? (
          <p role="alert" className="text-[13px] text-[var(--warn)]">
            {error}
          </p>
        ) : null}
        <div className="flex gap-2">
          <Button
            variant="filled"
            size="sm"
            disabled={!key || putMutation.isPending}
            onClick={() => putMutation.mutate()}
          >
            {t("save")}
          </Button>
          <Button
            variant="outline"
            size="sm"
            disabled={removeMutation.isPending}
            onClick={() => removeMutation.mutate()}
          >
            {t("remove")}
          </Button>
        </div>
      </div>
      {saved ? <SavedToast message={tc("saved")} /> : null}
    </SettingsSection>
  );
}
