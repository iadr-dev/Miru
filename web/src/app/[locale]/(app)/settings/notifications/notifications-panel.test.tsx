// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { NotificationsPanel } from "@/app/[locale]/(app)/settings/notifications/notifications-panel";
import {
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
import { messages, renderWithIntl } from "@/test/render";
import type { AlertPreference } from "@/types/settings";

vi.mock("@/lib/api/settings", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api/settings")>();
  return {
    ...actual,
    getAlertPreferences: vi.fn(),
    getChannelStatus: vi.fn(),
    putAlertPreferences: vi.fn(),
    startLineLink: vi.fn(),
    startTelegramLink: vi.fn(),
    unlinkLine: vi.fn(),
    unlinkTelegram: vi.fn(),
  };
});
vi.mock("@/lib/web-push", () => ({
  webPushState: vi.fn(),
  enableWebPush: vi.fn(),
  disableWebPush: vi.fn(),
}));
vi.mock("react-timezone-select", () => ({
  default: (p: { value: string; onChange: (o: { value: string }) => void }) => (
    <button
      type="button"
      data-tz={p.value}
      onClick={() => p.onChange({ value: "America/Chicago" })}
    >
      tz
    </button>
  ),
}));

const t = messages.settings.notifications;
const tc = messages.settings.common;

const PREFS: AlertPreference = {
  user_id: "u1",
  channels: ["in_app", "email"],
  enabled_categories: [],
  quiet_hours: { start: "22:00", end: "06:00" },
  morning_digest: true,
  timezone: "Asia/Taipei",
  updated_at: "2026-01-01T00:00:00Z",
};

const STATUS: ChannelStatus = {
  email: true,
  push: false,
  line: false,
  telegram: false,
  telegram_available: false,
  line_linked: false,
  line_available: false,
  email_blocked: null,
  web_push_public_key: null,
  line_add_friend_url: null,
};

type Setup = {
  readonly prefs?: AlertPreference | "error" | "loading";
  readonly status?: Partial<ChannelStatus> | "error";
  readonly browser?: WebPushState;
};

function arrange(s: Setup): void {
  const prefs = s.prefs ?? PREFS;
  if (prefs === "loading") {
    vi.mocked(getAlertPreferences).mockReturnValue(new Promise(() => undefined));
  } else if (prefs === "error") {
    vi.mocked(getAlertPreferences).mockRejectedValue(new Error("boom"));
  } else {
    vi.mocked(getAlertPreferences).mockResolvedValue(prefs);
  }
  if (s.status === "error") {
    vi.mocked(getChannelStatus).mockRejectedValue(new Error("boom"));
  } else {
    vi.mocked(getChannelStatus).mockResolvedValue({ ...STATUS, ...s.status });
  }
  vi.mocked(webPushState).mockResolvedValue(s.browser ?? "off");
}

async function settle(rounds = 5): Promise<void> {
  for (let i = 0; i < rounds; i++) {
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
  }
}

function mount(s: Setup): { client: QueryClient; container: HTMLElement } {
  arrange(s);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const view = renderWithIntl(
    <QueryClientProvider client={client}>
      <NotificationsPanel />
    </QueryClientProvider>,
  );
  return { client, container: view.container };
}

async function html(s: Setup): Promise<string> {
  const { container } = mount(s);
  await settle();
  return container.innerHTML;
}

beforeEach(() => {
  vi.clearAllMocks();
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("NotificationsPanel states", () => {
  it("loading", async () => {
    expect(await html({ prefs: "loading" })).toMatchSnapshot();
  });

  it("load error with retry", async () => {
    expect(await html({ prefs: "error" })).toMatchSnapshot();
  });

  it("saved preferences, email ready, nothing dirty", async () => {
    expect(await html({})).toMatchSnapshot();
  });

  it("no saved quiet window means on with the default preset", async () => {
    expect(await html({ prefs: { ...PREFS, quiet_hours: null } })).toMatchSnapshot();
  });

  it("an empty window (start == end) means quiet hours off", async () => {
    expect(
      await html({ prefs: { ...PREFS, quiet_hours: { start: "00:00", end: "00:00" } } }),
    ).toMatchSnapshot();
  });

  it("channel status failed to load", async () => {
    expect(await html({ status: "error" })).toMatchSnapshot();
  });

  it("blocked email shows its reason", async () => {
    expect(await html({ status: { email: false, email_blocked: "bounced" } })).toMatchSnapshot();
  });

  it("all channels selected and all ready", async () => {
    expect(
      await html({
        prefs: { ...PREFS, channels: ["in_app", "line", "telegram", "email", "push"] },
        status: { push: true, line: true, telegram: true },
      }),
    ).toMatchSnapshot();
  });

  it.each<WebPushState>(["unsupported", "denied", "off", "on"])(
    "browser notifications when the browser is %s and a key is configured",
    async (browser) => {
      expect(await html({ browser, status: { web_push_public_key: "KEY" } })).toMatchSnapshot();
    },
  );

  it("browser notifications without a configured key", async () => {
    expect(await html({ browser: "off" })).toMatchSnapshot();
  });

  it("LINE linked", async () => {
    expect(
      await html({ status: { line_available: true, line_linked: true, line: true } }),
    ).toMatchSnapshot();
  });

  it("LINE available but not linked", async () => {
    expect(await html({ status: { line_available: true } })).toMatchSnapshot();
  });

  it("Telegram linked", async () => {
    expect(await html({ status: { telegram_available: true, telegram: true } })).toMatchSnapshot();
  });

  it("Telegram available but not linked", async () => {
    expect(await html({ status: { telegram_available: true } })).toMatchSnapshot();
  });
});

describe("NotificationsPanel actions", () => {
  it("shows the save bar when a channel changes and saves the new set", async () => {
    mount({});
    await settle();
    expect(screen.queryByText(tc.save)).toBeNull();
    fireEvent.click(screen.getByLabelText(`${t.channelOption.push}（${t.channelNotReady}）`));
    expect(screen.getByText(tc.save)).toBeTruthy();
    vi.mocked(putAlertPreferences).mockResolvedValue(PREFS);
    fireEvent.click(screen.getByText(tc.save));
    await settle();
    expect(putAlertPreferences).toHaveBeenCalledWith({
      channels: ["in_app", "email", "push"],
      quiet_hours: { start: "22:00", end: "06:00" },
      morning_digest: true,
      timezone: "Asia/Taipei",
    });
    expect(screen.getByText(tc.saved)).toBeTruthy();
  });

  it("unchecking a channel removes it", async () => {
    mount({});
    await settle();
    fireEvent.click(screen.getByLabelText(t.channelOption.email));
    vi.mocked(putAlertPreferences).mockResolvedValue(PREFS);
    fireEvent.click(screen.getByText(tc.save));
    await settle();
    expect(vi.mocked(putAlertPreferences).mock.calls[0]?.[0].channels).toEqual(["in_app"]);
  });

  it("turning quiet hours off saves an empty window", async () => {
    mount({});
    await settle();
    fireEvent.click(screen.getAllByRole("switch")[0] as HTMLElement);
    vi.mocked(putAlertPreferences).mockResolvedValue(PREFS);
    fireEvent.click(screen.getByText(tc.save));
    await settle();
    expect(vi.mocked(putAlertPreferences).mock.calls[0]?.[0].quiet_hours).toEqual({
      start: "00:00",
      end: "00:00",
    });
  });

  it("a zero-length window falls back to the default preset when saved", async () => {
    mount({});
    await settle();
    fireEvent.change(screen.getByLabelText(t.quietEnd), { target: { value: "22:00" } });
    vi.mocked(putAlertPreferences).mockResolvedValue(PREFS);
    fireEvent.click(screen.getByText(tc.save));
    await settle();
    expect(vi.mocked(putAlertPreferences).mock.calls[0]?.[0].quiet_hours).toEqual({
      start: "00:00",
      end: "08:00",
    });
  });

  it("changing the timezone and the digest marks the form dirty; cancel restores", async () => {
    mount({});
    await settle();
    fireEvent.click(screen.getByText("tz"));
    expect(screen.getByText(tc.save)).toBeTruthy();
    fireEvent.click(screen.getByText(tc.cancel));
    expect(screen.queryByText(tc.save)).toBeNull();
    fireEvent.click(screen.getAllByRole("switch")[1] as HTMLElement);
    expect(screen.getByText(tc.save)).toBeTruthy();
    fireEvent.click(screen.getByText(tc.cancel));
    expect(screen.queryByText(tc.save)).toBeNull();
  });

  it("editing the quiet start marks the form dirty", async () => {
    mount({});
    await settle();
    fireEvent.change(screen.getByLabelText(t.quietStart), { target: { value: "21:00" } });
    expect(screen.getByText(tc.save)).toBeTruthy();
  });

  it("enables browser notifications with the public key", async () => {
    mount({ browser: "off", status: { web_push_public_key: "KEY" } });
    await settle();
    vi.mocked(enableWebPush).mockResolvedValue("on");
    fireEvent.click(screen.getByText(t.browser.enable));
    await settle();
    expect(enableWebPush).toHaveBeenCalledWith("KEY");
    expect(screen.getByText(t.browser.on)).toBeTruthy();
  });

  it("disables browser notifications", async () => {
    mount({ browser: "on", status: { web_push_public_key: "KEY" } });
    await settle();
    vi.mocked(disableWebPush).mockResolvedValue("off");
    fireEvent.click(screen.getByText(t.browser.disable));
    await settle();
    expect(disableWebPush).toHaveBeenCalled();
    expect(screen.getByText(t.browser.enable)).toBeTruthy();
  });

  it("links Telegram and opens the link, then unlinks", async () => {
    mount({ status: { telegram_available: true } });
    await settle();
    vi.mocked(startTelegramLink).mockResolvedValue({
      url: "https://t.me/x",
      expires_in_seconds: 60,
    });
    fireEvent.click(screen.getByText(t.telegramLink));
    await settle();
    const link = screen.getByText(t.telegramOpenLink) as HTMLAnchorElement;
    expect(link.href).toBe("https://t.me/x");
  });

  it("unlinks Telegram", async () => {
    mount({ status: { telegram_available: true, telegram: true } });
    await settle();
    vi.mocked(unlinkTelegram).mockResolvedValue(undefined as never);
    fireEvent.click(screen.getByText(t.telegramUnlink));
    await settle();
    expect(unlinkTelegram).toHaveBeenCalled();
  });

  it("starts a LINE link, shows the code and the add-friend link", async () => {
    mount({ status: { line_available: true } });
    await settle();
    vi.mocked(startLineLink).mockResolvedValue({
      code: "ABC123",
      add_friend_url: "https://line.me/x",
      expires_in_seconds: 60,
    });
    fireEvent.click(screen.getByText(t.lineLink));
    await settle();
    expect(screen.getByText("ABC123")).toBeTruthy();
    expect(screen.getByText(t.lineAddFriend)).toBeTruthy();
    expect(screen.getByText(t.lineCodeNote)).toBeTruthy();
  });

  it("a LINE code without an add-friend link omits it", async () => {
    mount({ status: { line_available: true } });
    await settle();
    vi.mocked(startLineLink).mockResolvedValue({
      code: "ABC123",
      add_friend_url: null,
      expires_in_seconds: 60,
    });
    fireEvent.click(screen.getByText(t.lineLink));
    await settle();
    expect(screen.queryByText(t.lineAddFriend)).toBeNull();
  });

  it("polls the channel status every 4 seconds while a LINE code is waiting", async () => {
    mount({ status: { line_available: true } });
    await settle();
    vi.mocked(startLineLink).mockResolvedValue({
      code: "ABC123",
      add_friend_url: null,
      expires_in_seconds: 60,
    });
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    fireEvent.click(screen.getByText(t.lineLink));
    await settle();
    const before = vi.mocked(getChannelStatus).mock.calls.length;
    act(() => {
      vi.advanceTimersByTime(4000);
    });
    await settle();
    expect(vi.mocked(getChannelStatus).mock.calls).toHaveLength(before + 1);
  });

  it("drops the waiting code once the status says LINE is linked", async () => {
    const { client } = mount({ status: { line_available: true } });
    await settle();
    vi.mocked(startLineLink).mockResolvedValue({
      code: "ABC123",
      add_friend_url: null,
      expires_in_seconds: 60,
    });
    fireEvent.click(screen.getByText(t.lineLink));
    await settle();
    expect(screen.getByText("ABC123")).toBeTruthy();
    act(() => {
      client.setQueryData(["settings", "channel-status"], {
        ...STATUS,
        line_available: true,
        line_linked: true,
        line: true,
      });
    });
    await settle();
    expect(screen.queryByText("ABC123")).toBeNull();
    expect(screen.getByText(t.lineLinked)).toBeTruthy();
  });

  it("unlinks LINE", async () => {
    mount({ status: { line_available: true, line_linked: true, line: true } });
    await settle();
    vi.mocked(unlinkLine).mockResolvedValue(undefined as never);
    fireEvent.click(screen.getByText(t.lineUnlink));
    await settle();
    expect(unlinkLine).toHaveBeenCalled();
  });
});
