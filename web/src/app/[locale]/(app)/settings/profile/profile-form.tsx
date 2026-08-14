"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { type ReactNode, useEffect, useState } from "react";
import { CountryCombobox } from "@/components/settings/country-combobox";
import { DirtyBar } from "@/components/settings/dirty-bar";
import { FieldError, TextInput } from "@/components/settings/form-controls";
import { SettingsRow, SettingsSection } from "@/components/settings/settings-section";
import { SavedToast, SettingsError, SettingsLoading } from "@/components/settings/settings-states";
import { fetchMe } from "@/lib/api/account";
import { SettingsApiError, updateProfile } from "@/lib/api/settings";
import { countryByDialCode } from "@/lib/countries";
import type { MeResponse } from "@/types/account";

type FormState = {
  display_name: string;
  birthday: string;
  phone_country_code: string;
  phone_number: string;
  nationality: string;
};

function fromMe(me: MeResponse): FormState {
  return {
    display_name: me.user.display_name ?? "",
    birthday: me.user.birthday ?? "",
    phone_country_code: me.user.phone_country_code ?? "",
    phone_number: me.user.phone_number ?? "",
    nationality: me.user.nationality ?? "",
  };
}

export function ProfileForm(): ReactNode {
  const t = useTranslations("settings.profile");
  const tc = useTranslations("settings.common");
  const queryClient = useQueryClient();

  const meQuery = useQuery({ queryKey: ["me"], queryFn: ({ signal }) => fetchMe(signal) });
  const [form, setForm] = useState<FormState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    if (meQuery.data && form === null) {
      setForm(fromMe(meQuery.data));
    }
  }, [meQuery.data, form]);

  const mutation = useMutation({
    mutationFn: () => {
      if (!form) {
        throw new Error("form not ready");
      }
      return updateProfile({
        display_name: form.display_name || null,
        birthday: form.birthday || null,
        phone_country_code: form.phone_country_code || null,
        phone_number: form.phone_number || null,
        nationality: form.nationality || null,
      });
    },
    onSuccess: (data) => {
      queryClient.setQueryData(["me"], { user: data.user, entitlements: data.entitlements });
      setForm(fromMe({ user: data.user, entitlements: data.entitlements }));
      setError(null);
      setSaved(true);
      window.setTimeout(() => setSaved(false), 2500);
    },
    onError: (err) => {
      setError(
        err instanceof SettingsApiError && err.code === "unknown_personality"
          ? tc("errorUnknownPersonality")
          : tc("errorGeneric"),
      );
    },
  });

  if (meQuery.isLoading || form === null) {
    return (
      <SettingsSection title={t("title")}>
        <SettingsLoading rows={4} />
      </SettingsSection>
    );
  }

  if (meQuery.isError || !meQuery.data) {
    return (
      <SettingsSection title={t("title")}>
        <SettingsError message={tc("errorLoad")} onRetry={() => void meQuery.refetch()} />
      </SettingsSection>
    );
  }

  const original = fromMe(meQuery.data);
  const dirty = JSON.stringify(original) !== JSON.stringify(form);

  const set =
    (key: keyof FormState) =>
    (value: string): void => {
      setForm((prev) => (prev ? { ...prev, [key]: value } : prev));
    };

  const onSave = (): void => {
    mutation.mutate();
  };
  const onCancel = (): void => {
    setForm(original);
    setError(null);
  };

  return (
    <SettingsSection title={t("title")} description={t("description")}>
      <SettingsRow label={t("displayName")}>
        <div className="w-56">
          <TextInput
            aria-label={t("displayName")}
            value={form.display_name}
            onChange={(e) => set("display_name")(e.target.value)}
            maxLength={80}
          />
        </div>
      </SettingsRow>
      <SettingsRow label={t("birthday")} hint={t("birthdayHint")}>
        <div className="w-56">
          <TextInput
            type="date"
            aria-label={t("birthday")}
            value={form.birthday}
            onChange={(e) => set("birthday")(e.target.value)}
          />
        </div>
      </SettingsRow>
      <SettingsRow label={t("phone")}>
        <div className="flex w-72 gap-2">
          <div className="w-32 shrink-0">
            <CountryCombobox
              value={countryByDialCode(form.phone_country_code)?.iso2 ?? null}
              onSelect={(country) => {
                setForm((prev) =>
                  prev
                    ? {
                        ...prev,
                        phone_country_code: `+${country.dialCode}`,
                        // Picking a dial code sets the matching nationality too — the two fields
                        // stay correlated on the same ISO code in both directions.
                        nationality: country.iso2,
                      }
                    : prev,
                );
              }}
              placeholder={t("phoneCountryCodePlaceholder")}
              searchPlaceholder={t("countrySearchPlaceholder")}
              emptyLabel={t("countrySearchEmpty")}
              aria-label={t("phoneCountryCode")}
              display="dialCode"
            />
          </div>
          <TextInput
            aria-label={t("phoneNumber")}
            value={form.phone_number}
            onChange={(e) => set("phone_number")(e.target.value)}
          />
        </div>
      </SettingsRow>
      <SettingsRow label={t("nationality")}>
        <div className="w-56">
          <CountryCombobox
            value={form.nationality || null}
            onSelect={(country) => {
              setForm((prev) =>
                prev
                  ? {
                      ...prev,
                      nationality: country.iso2,
                      // Symmetric to the phone-code side: picking a nationality sets the
                      // matching dial code too.
                      phone_country_code: `+${country.dialCode}`,
                    }
                  : prev,
              );
            }}
            placeholder={t("nationalityPlaceholder")}
            searchPlaceholder={t("countrySearchPlaceholder")}
            emptyLabel={t("countrySearchEmpty")}
            aria-label={t("nationality")}
            display="name"
          />
        </div>
      </SettingsRow>
      {error ? (
        <div className="pt-4">
          <FieldError message={error} />
        </div>
      ) : null}
      <DirtyBar
        dirty={dirty}
        saving={mutation.isPending}
        saveLabel={tc("save")}
        cancelLabel={tc("cancel")}
        onSave={onSave}
        onCancel={onCancel}
      />
      {saved ? <SavedToast message={tc("saved")} /> : null}
    </SettingsSection>
  );
}
