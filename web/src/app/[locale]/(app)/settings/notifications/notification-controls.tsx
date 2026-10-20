"use client";

import { useTranslations } from "next-intl";
import type { ReactNode } from "react";
import TimezoneSelect from "react-timezone-select";
import { Checkbox, TextInput, ToggleSwitch } from "@/components/settings/form-controls";
import { SettingsRow } from "@/components/settings/settings-section";
import type { ChannelStatus } from "@/lib/api/settings";
import type { WebPushState } from "@/lib/web-push";
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
    backgroundColor: state.isSelected || state.isFocused ? "var(--surface-2)" : "transparent",
  }),
};

/** Whether a selectable channel can reach this user right now; in-app always can. */
function channelReady(channel: DeliveryChannelKind, status: ChannelStatus | undefined): boolean {
  if (channel === "in_app") {
    return true;
  }
  return status ? status[channel] : false;
}

/** The channel checkboxes, the status notes, and the per-channel link controls. */
export function ChannelsSection({
  channels,
  status,
  onToggle,
  browserControl,
  lineControl,
  telegramControl,
}: {
  readonly channels: readonly DeliveryChannelKind[];
  readonly status: ChannelStatus | undefined;
  readonly onToggle: (channel: DeliveryChannelKind, checked: boolean) => void;
  readonly browserControl: ReactNode;
  readonly lineControl: ReactNode;
  readonly telegramControl: ReactNode;
}): ReactNode {
  const t = useTranslations("settings.notifications");
  return (
    <div className="pb-4">
      <p className="mb-2 text-[13px] text-[var(--text-muted)]">{t("channels")}</p>
      <div className="flex flex-wrap gap-3">
        {DELIVERY_CHANNEL_KINDS.map((channel) => {
          const channelName = t(`channelOption.${channel}`);
          const notReadyKey =
            channel === "line" || channel === "telegram" ? "channelNotLinked" : "channelNotReady";
          const notReadyNote = t(notReadyKey);
          return (
            <Checkbox
              key={channel}
              checked={channel === "in_app" || channels.includes(channel)}
              disabled={channel === "in_app"}
              onChange={(checked) => onToggle(channel, checked)}
              label={
                channelReady(channel, status) ? channelName : `${channelName}（${notReadyNote}）`
              }
            />
          );
        })}
      </div>
      <p className="mt-2 text-[12px] text-[var(--text-muted)]">{t("channelReadyNote")}</p>
      {status?.email_blocked ? (
        <p className="mt-2 text-[12px] text-[var(--warn)]">
          {t(`emailBlocked.${status.email_blocked}`)}
        </p>
      ) : null}
      <div className="mt-3 flex flex-wrap items-center gap-3 text-[13px]">
        <span className="text-[var(--text-muted)]">{t("browser.label")}</span>
        {browserControl}
      </div>
      {status?.line_available ? (
        <div className="mt-3 space-y-1 text-[13px]">{lineControl}</div>
      ) : null}
      {status?.telegram_available ? (
        <div className="mt-3 flex flex-wrap items-center gap-3 text-[13px]">{telegramControl}</div>
      ) : null}
    </div>
  );
}

/** This browser's own push state: unsupported, blocked, not configured, on, or off. */
export function BrowserControl({
  status,
  browserState,
  pending,
  onToggle,
}: {
  readonly status: ChannelStatus | undefined;
  readonly browserState: WebPushState | null;
  readonly pending: boolean;
  readonly onToggle: (enable: boolean) => void;
}): ReactNode {
  const t = useTranslations("settings.notifications");
  if (browserState === "unsupported") {
    return <span className="text-[var(--text-muted)]">{t("browser.unsupported")}</span>;
  }
  if (browserState === "denied") {
    return <span className="text-[var(--warn)]">{t("browser.denied")}</span>;
  }
  if (!status?.web_push_public_key) {
    return <span className="text-[var(--text-muted)]">{t("browser.notConfigured")}</span>;
  }
  if (browserState === "on") {
    return (
      <>
        <span className="text-[var(--text)]">{t("browser.on")}</span>
        <button
          type="button"
          className="text-[var(--accent)] underline"
          onClick={() => onToggle(false)}
          disabled={pending}
        >
          {t("browser.disable")}
        </button>
      </>
    );
  }
  return (
    <button
      type="button"
      className="text-[var(--accent)] underline"
      onClick={() => onToggle(true)}
      disabled={pending || browserState === null}
    >
      {t("browser.enable")}
    </button>
  );
}

export function LineControl({
  status,
  lineLink,
  linkPending,
  unlinkPending,
  onLink,
  onUnlink,
}: {
  readonly status: ChannelStatus | undefined;
  readonly lineLink: { code: string; url: string | null } | null;
  readonly linkPending: boolean;
  readonly unlinkPending: boolean;
  readonly onLink: () => void;
  readonly onUnlink: () => void;
}): ReactNode {
  const t = useTranslations("settings.notifications");
  if (!status?.line_available) {
    return null;
  }
  if (status.line_linked) {
    return (
      <div className="flex flex-wrap items-center gap-3">
        <span className="text-[var(--text)]">{t("lineLinked")}</span>
        <button
          type="button"
          className="text-[var(--accent)] underline"
          onClick={onUnlink}
          disabled={unlinkPending}
        >
          {t("lineUnlink")}
        </button>
      </div>
    );
  }
  if (lineLink) {
    return (
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
    );
  }
  return (
    <button
      type="button"
      className="text-[var(--accent)] underline"
      onClick={onLink}
      disabled={linkPending}
    >
      {t("lineLink")}
    </button>
  );
}

export function TelegramControl({
  status,
  telegramUrl,
  linkPending,
  unlinkPending,
  onLink,
  onUnlink,
  onOpenLink,
}: {
  readonly status: ChannelStatus | undefined;
  readonly telegramUrl: string | null;
  readonly linkPending: boolean;
  readonly unlinkPending: boolean;
  readonly onLink: () => void;
  readonly onUnlink: () => void;
  readonly onOpenLink: () => void;
}): ReactNode {
  const t = useTranslations("settings.notifications");
  if (!status?.telegram_available) {
    return null;
  }
  if (status.telegram) {
    return (
      <>
        <span className="text-[var(--text)]">{t("telegramLinked")}</span>
        <button
          type="button"
          className="text-[var(--accent)] underline"
          onClick={onUnlink}
          disabled={unlinkPending}
        >
          {t("telegramUnlink")}
        </button>
      </>
    );
  }
  if (telegramUrl) {
    return (
      <a
        href={telegramUrl}
        target="_blank"
        rel="noreferrer"
        className="text-[var(--accent)] underline"
        onClick={onOpenLink}
      >
        {t("telegramOpenLink")}
      </a>
    );
  }
  return (
    <button
      type="button"
      className="text-[var(--accent)] underline"
      onClick={onLink}
      disabled={linkPending}
    >
      {t("telegramLink")}
    </button>
  );
}

/** The quiet-hours switch with its start/end times, and the timezone the window is read in. */
export function QuietHoursRows({
  quietOn,
  setQuietOn,
  quietStart,
  setQuietStart,
  quietEnd,
  setQuietEnd,
  timezone,
  setTimezone,
}: {
  readonly quietOn: boolean;
  readonly setQuietOn: (on: boolean) => void;
  readonly quietStart: string;
  readonly setQuietStart: (start: string) => void;
  readonly quietEnd: string;
  readonly setQuietEnd: (end: string) => void;
  readonly timezone: string;
  readonly setTimezone: (timezone: string) => void;
}): ReactNode {
  const t = useTranslations("settings.notifications");
  return (
    <>
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
    </>
  );
}
