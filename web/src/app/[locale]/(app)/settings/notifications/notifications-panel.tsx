"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { type ReactNode, useEffect, useState } from "react";
import TimezoneSelect from "react-timezone-select";
import { DirtyBar } from "@/components/settings/dirty-bar";
import { Checkbox, TextInput, ToggleSwitch } from "@/components/settings/form-controls";
import { SettingsRow, SettingsSection } from "@/components/settings/settings-section";
import { SavedToast, SettingsError, SettingsLoading } from "@/components/settings/settings-states";
import { queryKeys } from "@/lib/api/query-keys";
import {
  type AlertPreferenceRequest,
  type ChannelStatus,
  getAlertPreferences,
  getChannelStatus,
  putAlertPreferences,
  startLineLink,
  startTelegramLink,
  unlinkLine,
  unlinkTelegram,
} from "@/lib/api/settings";
import { disableWebPush, enableWebPush, type WebPushState, webPushState } from "@/lib/web-push";
import { DELIVERY_CHANNEL_KINDS, type DeliveryChannelKind } from "@/types/settings";

/** The timezone list comes from the browser's `Intl` data via react-timezone-select — nothing is
 * hard-coded, so DST and newly added zones stay correct. Styled with the app's tokens. */
const TIMEZONE_SELECT_STYLES = {
  control: (base: object, state: { isFocused: boolean }) => ({
    ...base,
    minHeight: 40,
    borderRadius: "var(--radius-input)",
    backgroundColor: "var(--surface)",
    borderColor: state.isFocused ? "var(--accent)" : "var(--border)",
    boxShadow: "none",
    fontSize: 14,
  }),
  singleValue: (base: object) => ({ ...base, color: "var(--text)" }),
  input: (base: object) => ({ ...base, color: "var(--text)" }),
  menu: (base: object) => ({
    ...base,
    backgroundColor: "var(--surface)",
    border: "1px solid var(--border)",
    zIndex: 30,
  }),
  option: (base: object, state: { isFocused: boolean; isSelected: boolean }) => ({
    ...base,
    fontSize: 14,
    color: "var(--text)",
    backgroundColor: state.isSelected
      ? "var(--surface-2)"
      : state.isFocused
        ? "var(--surface-2)"
        : "transparent",
  }),
};

const DEFAULT_QUIET_START = "00:00";
const DEFAULT_QUIET_END = "08:00";

type QuietState = readonly [on: boolean, start: string, end: string];

/** The window as the form holds it: an empty window (start == end) is "off". */
function quietOf(window: { start: string; end: string } | null): QuietState {
  if (window === null || window.start === window.end) {
    return [false, DEFAULT_QUIET_START, DEFAULT_QUIET_END];
  }
  return [true, window.start, window.end];
}

/** A comparable string for the form's quiet-hours state (off ignores the stale times). */
function quietSetting(on: boolean, start: string, end: string): string {
  return on ? `${start}-${end}` : "off";
}

function quietToSave(on: boolean, start: string, end: string): { start: string; end: string } {
  if (!on) {
    return { start: DEFAULT_QUIET_START, end: DEFAULT_QUIET_START };
  }
  // An empty or zero-length window would read as "off"; fall back to the default preset.
  return start === "" || end === "" || start === end
    ? { start: DEFAULT_QUIET_START, end: DEFAULT_QUIET_END }
    : { start, end };
}

/** Whether a selectable channel can reach this user right now; in-app always can. */
function channelReady(channel: DeliveryChannelKind, status: ChannelStatus | undefined): boolean {
  if (channel === "in_app") {
    return true;
  }
  return status ? status[channel] : false;
}

/** Notification preferences (not tier-gated). In-app is always on and is the inbox; the other
 * channels only deliver when they can reach the user (an email address, a registered device, a
 * LINE sign-in, a linked Telegram chat) and the server has that provider configured. Notifications
 * that arrive during quiet hours are kept in the inbox and sent to the external channels as one
 * digest when the window ends. */
export function NotificationsPanel(): ReactNode {
  const t = useTranslations("settings.notifications");
  const tc = useTranslations("settings.common");
  const queryClient = useQueryClient();

  const query = useQuery({
    queryKey: queryKeys.settings("alert-preferences"),
    queryFn: () => getAlertPreferences(),
  });
  const statusQuery = useQuery({
    queryKey: queryKeys.settings("channel-status"),
    queryFn: () => getChannelStatus(),
  });

  const [channels, setChannels] = useState<DeliveryChannelKind[] | null>(null);
  // Quiet hours are on with a window, or off. Off is stored as an empty window (start == end),
  // which never matches; "no window saved" would mean the default preset instead.
  const [quietOn, setQuietOn] = useState(true);
  const [quietStart, setQuietStart] = useState(DEFAULT_QUIET_START);
  const [quietEnd, setQuietEnd] = useState(DEFAULT_QUIET_END);
  const [morningDigest, setMorningDigest] = useState(false);
  const [timezone, setTimezone] = useState("Asia/Taipei");
  const [saved, setSaved] = useState(false);
  const [telegramUrl, setTelegramUrl] = useState<string | null>(null);
  const [lineLink, setLineLink] = useState<{ code: string; url: string | null } | null>(null);
  // This browser's own notification state — it lives in the browser, not on the server.
  const [browserState, setBrowserState] = useState<WebPushState | null>(null);
  useEffect(() => {
    void webPushState().then(setBrowserState);
  }, []);

  useEffect(() => {
    if (query.data && channels === null) {
      setChannels(query.data.channels);
      const window = query.data.quiet_hours;
      const off = window !== null && window.start === window.end;
      setQuietOn(!off);
      setQuietStart(window && !off ? window.start : DEFAULT_QUIET_START);
      setQuietEnd(window && !off ? window.end : DEFAULT_QUIET_END);
      setMorningDigest(query.data.morning_digest);
      setTimezone(query.data.timezone);
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

  const linkMutation = useMutation({
    mutationFn: () => startTelegramLink(),
    onSuccess: (link) => setTelegramUrl(link.url),
  });
  const browserMutation = useMutation({
    mutationFn: async (enable: boolean): Promise<WebPushState> => {
      const publicKey = statusQuery.data?.web_push_public_key;
      if (enable && !publicKey) {
        return "off";
      }
      return enable && publicKey ? enableWebPush(publicKey) : disableWebPush();
    },
    onSuccess: (state) => {
      setBrowserState(state);
      void queryClient.invalidateQueries({ queryKey: queryKeys.settings("channel-status") });
    },
  });
  const lineLinkMutation = useMutation({
    mutationFn: () => startLineLink(),
    onSuccess: (link) => setLineLink({ code: link.code, url: link.add_friend_url }),
  });
  const lineUnlinkMutation = useMutation({
    mutationFn: () => unlinkLine(),
    onSuccess: () => {
      setLineLink(null);
      void queryClient.invalidateQueries({ queryKey: queryKeys.settings("channel-status") });
    },
  });
  // While a code is waiting to be sent to the official account, check whether it was bound.
  useEffect(() => {
    if (lineLink === null) {
      return;
    }
    const timer = window.setInterval(() => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.settings("channel-status") });
    }, 4000);
    return () => window.clearInterval(timer);
  }, [lineLink, queryClient]);
  useEffect(() => {
    if (statusQuery.data?.line_linked) {
      setLineLink(null);
    }
  }, [statusQuery.data?.line_linked]);
  const unlinkMutation = useMutation({
    mutationFn: () => unlinkTelegram(),
    onSuccess: () => {
      setTelegramUrl(null);
      void queryClient.invalidateQueries({ queryKey: queryKeys.settings("channel-status") });
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
    quietSetting(quietOn, quietStart, quietEnd) !==
      quietSetting(...quietOf(original.quiet_hours)) ||
    morningDigest !== original.morning_digest ||
    timezone !== original.timezone;

  const toggleChannel = (channel: DeliveryChannelKind, checked: boolean): void => {
    setChannels((prev) => {
      const base = prev ?? [];
      return checked ? [...base, channel] : base.filter((c) => c !== channel);
    });
  };

  const onSave = (): void => {
    mutation.mutate({
      channels: channels ?? [],
      quiet_hours: quietToSave(quietOn, quietStart, quietEnd),
      morning_digest: morningDigest,
      timezone,
    });
  };

  const onCancel = (): void => {
    setChannels(original.channels);
    const [on, start, end] = quietOf(original.quiet_hours);
    setQuietOn(on);
    setQuietStart(start);
    setQuietEnd(end);
    setMorningDigest(original.morning_digest);
    setTimezone(original.timezone);
  };

  const status = statusQuery.data;

  return (
    <SettingsSection title={t("title")} description={t("description")}>
      <div className="pb-4">
        <p className="mb-2 text-[13px] text-[var(--text-muted)]">{t("channels")}</p>
        <div className="flex flex-wrap gap-3">
          {DELIVERY_CHANNEL_KINDS.map((channel) => (
            <Checkbox
              key={channel}
              checked={channel === "in_app" || (channels ?? []).includes(channel)}
              disabled={channel === "in_app"}
              onChange={(checked) => toggleChannel(channel, checked)}
              label={
                channelReady(channel, status)
                  ? t(`channelOption.${channel}`)
                  : `${t(`channelOption.${channel}`)}（${t(channel === "line" || channel === "telegram" ? "channelNotLinked" : "channelNotReady")}）`
              }
            />
          ))}
        </div>
        <p className="mt-2 text-[12px] text-[var(--text-muted)]">{t("channelReadyNote")}</p>
        {status?.email_blocked ? (
          <p className="mt-2 text-[12px] text-[var(--warn)]">
            {t(`emailBlocked.${status.email_blocked}`)}
          </p>
        ) : null}
        <div className="mt-3 flex flex-wrap items-center gap-3 text-[13px]">
          <span className="text-[var(--text-muted)]">{t("browser.label")}</span>
          {browserState === "unsupported" ? (
            <span className="text-[var(--text-muted)]">{t("browser.unsupported")}</span>
          ) : browserState === "denied" ? (
            <span className="text-[var(--warn)]">{t("browser.denied")}</span>
          ) : !status?.web_push_public_key ? (
            <span className="text-[var(--text-muted)]">{t("browser.notConfigured")}</span>
          ) : browserState === "on" ? (
            <>
              <span className="text-[var(--text)]">{t("browser.on")}</span>
              <button
                type="button"
                className="text-[var(--accent)] underline"
                onClick={() => browserMutation.mutate(false)}
                disabled={browserMutation.isPending}
              >
                {t("browser.disable")}
              </button>
            </>
          ) : (
            <button
              type="button"
              className="text-[var(--accent)] underline"
              onClick={() => browserMutation.mutate(true)}
              disabled={browserMutation.isPending || browserState === null}
            >
              {t("browser.enable")}
            </button>
          )}
        </div>
        {status?.line_available ? (
          <div className="mt-3 space-y-1 text-[13px]">
            {status.line_linked ? (
              <div className="flex flex-wrap items-center gap-3">
                <span className="text-[var(--text)]">{t("lineLinked")}</span>
                <button
                  type="button"
                  className="text-[var(--accent)] underline"
                  onClick={() => lineUnlinkMutation.mutate()}
                  disabled={lineUnlinkMutation.isPending}
                >
                  {t("lineUnlink")}
                </button>
              </div>
            ) : lineLink ? (
              <div className="space-y-1">
                <p className="text-[var(--text)]">
                  {t("lineSendCode")}{" "}
                  <code className="rounded bg-[var(--surface-2)] px-2 py-0.5 font-num tracking-widest">
                    {lineLink.code}
                  </code>
                </p>
                {lineLink.url ? (
                  <a
                    href={lineLink.url}
                    target="_blank"
                    rel="noreferrer"
                    className="text-[var(--accent)] underline"
                  >
                    {t("lineAddFriend")}
                  </a>
                ) : null}
                <p className="text-[12px] text-[var(--text-muted)]">{t("lineCodeNote")}</p>
              </div>
            ) : (
              <button
                type="button"
                className="text-[var(--accent)] underline"
                onClick={() => lineLinkMutation.mutate()}
                disabled={lineLinkMutation.isPending}
              >
                {t("lineLink")}
              </button>
            )}
          </div>
        ) : null}
        {status?.telegram_available ? (
          <div className="mt-3 flex flex-wrap items-center gap-3 text-[13px]">
            {status.telegram ? (
              <>
                <span className="text-[var(--text)]">{t("telegramLinked")}</span>
                <button
                  type="button"
                  className="text-[var(--accent)] underline"
                  onClick={() => unlinkMutation.mutate()}
                  disabled={unlinkMutation.isPending}
                >
                  {t("telegramUnlink")}
                </button>
              </>
            ) : telegramUrl ? (
              <a
                href={telegramUrl}
                target="_blank"
                rel="noreferrer"
                className="text-[var(--accent)] underline"
                onClick={() =>
                  window.setTimeout(
                    () =>
                      void queryClient.invalidateQueries({
                        queryKey: queryKeys.settings("channel-status"),
                      }),
                    8000,
                  )
                }
              >
                {t("telegramOpenLink")}
              </a>
            ) : (
              <button
                type="button"
                className="text-[var(--accent)] underline"
                onClick={() => linkMutation.mutate()}
                disabled={linkMutation.isPending}
              >
                {t("telegramLink")}
              </button>
            )}
          </div>
        ) : null}
      </div>
      <SettingsRow label={t("quietHours")}>
        <div className="flex flex-wrap items-center justify-end gap-3">
          <ToggleSwitch checked={quietOn} onChange={setQuietOn} label={t("quietHours")} />
          {quietOn ? (
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
          ) : null}
        </div>
      </SettingsRow>
      <SettingsRow label={t("timezone")}>
        <div className="w-72 max-w-full">
          <TimezoneSelect
            value={timezone}
            onChange={(option) => setTimezone(option.value)}
            labelStyle="altName"
            styles={TIMEZONE_SELECT_STYLES}
            aria-label={t("timezone")}
          />
        </div>
      </SettingsRow>
      <p className="pb-3 text-[12px] text-[var(--text-muted)]">{t("quietHoursNote")}</p>
      <SettingsRow label={t("morningDigest")}>
        <ToggleSwitch
          checked={morningDigest}
          onChange={setMorningDigest}
          label={t("morningDigest")}
        />
      </SettingsRow>
      <p className="pb-3 text-[12px] text-[var(--text-muted)]">{t("morningDigestNote")}</p>
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
