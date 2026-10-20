"use client";

import { type QueryClient, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { type ReactNode, useEffect, useState } from "react";
import { DirtyBar } from "@/components/settings/dirty-bar";
import { ToggleSwitch } from "@/components/settings/form-controls";
import { SettingsRow, SettingsSection } from "@/components/settings/settings-section";
import { SavedToast, SettingsError, SettingsLoading } from "@/components/settings/settings-states";
import { queryKeys } from "@/lib/api/query-keys";
import {
  type AlertPreferenceRequest,
  getAlertPreferences,
  getChannelStatus,
  putAlertPreferences,
  startLineLink,
  startTelegramLink,
  unlinkLine,
  unlinkTelegram,
} from "@/lib/api/settings";
import { disableWebPush, enableWebPush, type WebPushState, webPushState } from "@/lib/web-push";
import {
  BrowserControl,
  ChannelsSection,
  LineControl,
  QuietHoursRows,
  TelegramControl,
} from "./notification-controls";
import { isFormDirty, toSaveRequest, useNotificationForm, withChannel } from "./notification-form";

type LineLinkState = { code: string; url: string | null };

/** Turn this browser's push on (when a public key is configured) or off. */
async function toggleBrowserPush(
  enable: boolean,
  publicKey: string | null | undefined,
): Promise<WebPushState> {
  if (enable && !publicKey) {
    return "off";
  }
  return enable && publicKey ? enableWebPush(publicKey) : disableWebPush();
}

function refreshChannelStatus(queryClient: QueryClient): void {
  void queryClient.invalidateQueries({ queryKey: queryKeys.settings("channel-status") });
}

/** While a code is waiting to be sent to the official account, check whether it was bound. */
function useLineLinkPolling(lineLink: LineLinkState | null, queryClient: QueryClient): void {
  useEffect(() => {
    if (lineLink === null) {
      return;
    }
    const timer = window.setInterval(() => refreshChannelStatus(queryClient), 4000);
    return () => window.clearInterval(timer);
  }, [lineLink, queryClient]);
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

  const form = useNotificationForm(query.data);
  const [saved, setSaved] = useState(false);
  const [telegramUrl, setTelegramUrl] = useState<string | null>(null);
  const [lineLink, setLineLink] = useState<LineLinkState | null>(null);
  // This browser's own notification state — it lives in the browser, not on the server.
  const [browserState, setBrowserState] = useState<WebPushState | null>(null);
  useEffect(() => {
    void webPushState().then(setBrowserState);
  }, []);

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
    mutationFn: (enable: boolean): Promise<WebPushState> =>
      toggleBrowserPush(enable, statusQuery.data?.web_push_public_key),
    onSuccess: (state) => {
      setBrowserState(state);
      refreshChannelStatus(queryClient);
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
      refreshChannelStatus(queryClient);
    },
  });
  useLineLinkPolling(lineLink, queryClient);
  useEffect(() => {
    if (statusQuery.data?.line_linked) {
      setLineLink(null);
    }
  }, [statusQuery.data?.line_linked]);
  const unlinkMutation = useMutation({
    mutationFn: () => unlinkTelegram(),
    onSuccess: () => {
      setTelegramUrl(null);
      refreshChannelStatus(queryClient);
    },
  });

  if (query.isLoading || form.channels === null) {
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
  const status = statusQuery.data;

  return (
    <SettingsSection title={t("title")} description={t("description")}>
      <ChannelsSection
        channels={form.channels}
        status={status}
        onToggle={(channel, checked) =>
          form.setChannels((prev) => withChannel(prev, channel, checked))
        }
        browserControl={
          <BrowserControl
            status={status}
            browserState={browserState}
            pending={browserMutation.isPending}
            onToggle={(enable) => browserMutation.mutate(enable)}
          />
        }
        lineControl={
          <LineControl
            status={status}
            lineLink={lineLink}
            linkPending={lineLinkMutation.isPending}
            unlinkPending={lineUnlinkMutation.isPending}
            onLink={() => lineLinkMutation.mutate()}
            onUnlink={() => lineUnlinkMutation.mutate()}
          />
        }
        telegramControl={
          <TelegramControl
            status={status}
            telegramUrl={telegramUrl}
            linkPending={linkMutation.isPending}
            unlinkPending={unlinkMutation.isPending}
            onLink={() => linkMutation.mutate()}
            onUnlink={() => unlinkMutation.mutate()}
            onOpenLink={() => window.setTimeout(() => refreshChannelStatus(queryClient), 8000)}
          />
        }
      />
      <QuietHoursRows
        quietOn={form.quietOn}
        setQuietOn={form.setQuietOn}
        quietStart={form.quietStart}
        setQuietStart={form.setQuietStart}
        quietEnd={form.quietEnd}
        setQuietEnd={form.setQuietEnd}
        timezone={form.timezone}
        setTimezone={form.setTimezone}
      />
      <p className="pb-3 text-[12px] text-[var(--text-muted)]">{t("quietHoursNote")}</p>
      <SettingsRow label={t("morningDigest")}>
        <ToggleSwitch
          checked={form.morningDigest}
          onChange={form.setMorningDigest}
          label={t("morningDigest")}
        />
      </SettingsRow>
      <p className="pb-3 text-[12px] text-[var(--text-muted)]">{t("morningDigestNote")}</p>
      <DirtyBar
        dirty={isFormDirty(form, original)}
        saving={mutation.isPending}
        saveLabel={tc("save")}
        cancelLabel={tc("cancel")}
        onSave={() => mutation.mutate(toSaveRequest(form))}
        onCancel={() => form.reset(original)}
      />
      {saved ? <SavedToast message={tc("saved")} /> : null}
    </SettingsSection>
  );
}
