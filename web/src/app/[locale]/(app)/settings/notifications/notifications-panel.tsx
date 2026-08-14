"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { type ReactNode, useEffect, useState } from "react";
import { DirtyBar } from "@/components/settings/dirty-bar";
import { Checkbox, TextInput, ToggleSwitch } from "@/components/settings/form-controls";
import { SettingsRow, SettingsSection } from "@/components/settings/settings-section";
import { SavedToast, SettingsError, SettingsLoading } from "@/components/settings/settings-states";
import { type AlertPreferenceRequest, putAlertPreferences } from "@/lib/api/settings";
import { DELIVERY_CHANNEL_KINDS, type DeliveryChannelKind } from "@/types/settings";

/** Notification preferences (not tier-gated). Only `in_app` actually delivers today — LINE/
 * telegram/email/push are configured but not wired to real delivery, and that limitation is
 * stated plainly per docs/web/settings/README.md. There is no `GET` for this resource, so the
 * current state is read via an empty-body `PUT` (a no-op partial update per the request's
 * `Partial<...>` shape) rather than duplicating a getter the backend doesn't expose. */
export function NotificationsPanel(): ReactNode {
  const t = useTranslations("settings.notifications");
  const tc = useTranslations("settings.common");
  const queryClient = useQueryClient();

  const query = useQuery({
    queryKey: ["settings", "alert-preferences"],
    queryFn: () => putAlertPreferences({}),
  });

  const [channels, setChannels] = useState<DeliveryChannelKind[] | null>(null);
  const [quietStart, setQuietStart] = useState("");
  const [quietEnd, setQuietEnd] = useState("");
  const [dailyLimit, setDailyLimit] = useState("");
  const [morningDigest, setMorningDigest] = useState(false);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    if (query.data && channels === null) {
      setChannels(query.data.channels);
      setQuietStart(query.data.quiet_hours?.start ?? "");
      setQuietEnd(query.data.quiet_hours?.end ?? "");
      setDailyLimit(
        query.data.daily_limit === null || query.data.daily_limit === undefined
          ? ""
          : String(query.data.daily_limit),
      );
      setMorningDigest(query.data.morning_digest);
    }
  }, [query.data, channels]);

  const mutation = useMutation({
    mutationFn: (body: AlertPreferenceRequest) => putAlertPreferences(body),
    onSuccess: (data) => {
      queryClient.setQueryData(["settings", "alert-preferences"], data);
      setSaved(true);
      window.setTimeout(() => setSaved(false), 2500);
    },
  });

  if (query.isLoading || channels === null) {
    return (
      <SettingsSection title={t("title")}>
        <SettingsLoading rows={4} />
      </SettingsSection>
    );
  }

  if (query.isError || !query.data) {
    return (
      <SettingsSection title={t("title")}>
        <SettingsError message={tc("errorLoad")} onRetry={() => void query.refetch()} />
      </SettingsSection>
    );
  }

  const original = query.data;
  // Order-insensitive equality check for the channel set — the sort is only to normalize
  // ordering before comparing, so an explicit lexicographic compare fn (not locale-aware) is
  // both correct and required.
  const byCodePoint = (a: string, b: string): number => (a < b ? -1 : a > b ? 1 : 0);
  const dirty =
    JSON.stringify([...channels].sort(byCodePoint)) !==
      JSON.stringify([...original.channels].sort(byCodePoint)) ||
    quietStart !== (original.quiet_hours?.start ?? "") ||
    quietEnd !== (original.quiet_hours?.end ?? "") ||
    dailyLimit !== (original.daily_limit === null ? "" : String(original.daily_limit)) ||
    morningDigest !== original.morning_digest;

  const toggleChannel = (channel: DeliveryChannelKind, checked: boolean): void => {
    setChannels((prev) => {
      const base = prev ?? [];
      return checked ? [...base, channel] : base.filter((c) => c !== channel);
    });
  };

  const onSave = (): void => {
    const hasQuietHours = quietStart.trim() !== "" && quietEnd.trim() !== "";
    mutation.mutate({
      channels: channels ?? [],
      quiet_hours: hasQuietHours ? { start: quietStart, end: quietEnd } : null,
      daily_limit: dailyLimit.trim() === "" ? null : Math.trunc(Number(dailyLimit)),
      morning_digest: morningDigest,
    });
  };

  const onCancel = (): void => {
    setChannels(original.channels);
    setQuietStart(original.quiet_hours?.start ?? "");
    setQuietEnd(original.quiet_hours?.end ?? "");
    setDailyLimit(original.daily_limit === null ? "" : String(original.daily_limit));
    setMorningDigest(original.morning_digest);
  };

  return (
    <SettingsSection title={t("title")} description={t("description")}>
      <div className="pb-4">
        <p className="mb-2 text-[13px] text-[var(--text-muted)]">{t("channels")}</p>
        <div className="flex flex-wrap gap-3">
          {DELIVERY_CHANNEL_KINDS.map((channel) => (
            <Checkbox
              key={channel}
              checked={(channels ?? []).includes(channel)}
              onChange={(checked) => toggleChannel(channel, checked)}
              label={t(`channelOption.${channel}`)}
            />
          ))}
        </div>
        <p className="mt-2 text-[12px] text-[var(--warn)]">{t("deliveryLimitationNote")}</p>
      </div>
      <SettingsRow label={t("quietHours")}>
        <div className="flex items-center gap-2">
          <TextInput
            type="time"
            aria-label={t("quietStart")}
            value={quietStart}
            onChange={(e) => setQuietStart(e.target.value)}
            className="w-28"
          />
          <span className="text-[var(--text-muted)]">–</span>
          <TextInput
            type="time"
            aria-label={t("quietEnd")}
            value={quietEnd}
            onChange={(e) => setQuietEnd(e.target.value)}
            className="w-28"
          />
        </div>
      </SettingsRow>
      <SettingsRow label={t("dailyLimit")}>
        <TextInput
          type="number"
          min={0}
          aria-label={t("dailyLimit")}
          value={dailyLimit}
          onChange={(e) => setDailyLimit(e.target.value)}
          className="w-28"
        />
      </SettingsRow>
      <SettingsRow label={t("morningDigest")}>
        <ToggleSwitch
          checked={morningDigest}
          onChange={setMorningDigest}
          label={t("morningDigest")}
        />
      </SettingsRow>
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
