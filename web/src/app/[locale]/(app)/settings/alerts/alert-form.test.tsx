// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AlertForm, type AlertFormPreset } from "@/app/[locale]/(app)/settings/alerts/alert-form";
import {
  checkAlertSymbol,
  createAlert,
  listAlerts,
  SettingsApiError,
  updateAlert,
} from "@/lib/api/settings";
import { getWatchlists } from "@/lib/api/watchlist";
import { messages, renderWithIntl } from "@/test/render";
import { ALERT_TEMPLATE_KINDS, type AlertRule } from "@/types/settings";
import type { WatchlistGroupView } from "@/types/watchlist";

vi.mock("@/lib/api/settings", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api/settings")>();
  return {
    ...actual,
    checkAlertSymbol: vi.fn(),
    createAlert: vi.fn(),
    listAlerts: vi.fn(),
    updateAlert: vi.fn(),
  };
});
vi.mock("@/lib/api/watchlist", () => ({ getWatchlists: vi.fn() }));
vi.mock("@/app/[locale]/(app)/settings/alerts/per-stock-form", () => ({
  PerStockForm: (p: { initialGroupId?: string }) => <i data-per-stock={p.initialGroupId ?? ""} />,
}));

const t = messages.settings.alerts.form;

function group(id: string, name: string, market: "TW" | "US"): WatchlistGroupView {
  return {
    group: {
      id,
      user_id: "u1",
      name,
      market,
      asset_class: "stock",
      sort_order: "a",
      is_default: false,
      color: null,
      version: 1,
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z",
    },
    items: [],
  } as WatchlistGroupView;
}

const GROUPS = [group("g_tw", "台股群", "TW"), group("g_us", "美股群", "US")];

const RULE: AlertRule = {
  id: "r1",
  user_id: "u1",
  symbol: "2330",
  market: "TW",
  watchlist_id: null,
  origin_watchlist_id: null,
  condition: { kind: "close_price", comparator: "above", value: 100 },
  trigger_mode: "once_per_day",
  cooldown_seconds: null,
  state: {},
  expires_at: null,
  auto_paused_reason: null,
  explain: false,
  channels: [],
  quiet_hours: null,
  daily_limit: null,
  enabled: true,
  last_triggered: null,
  version: 3,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};

const GROUP_RULE: AlertRule = {
  ...RULE,
  symbol: null,
  market: null,
  watchlist_id: "g_tw",
  condition: { kind: "daily_change", direction: "up", percent: 5 },
};

const onDone = vi.fn();

function mount(props: { rule?: AlertRule; preset?: AlertFormPreset } = {}): HTMLElement {
  vi.mocked(getWatchlists).mockResolvedValue(GROUPS);
  vi.mocked(checkAlertSymbol).mockResolvedValue({ checked: true, exists: true } as never);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const view = renderWithIntl(
    <QueryClientProvider client={client}>
      <AlertForm
        onDone={onDone}
        {...(props.rule ? { rule: props.rule } : {})}
        {...(props.preset ? { preset: props.preset } : {})}
      />
    </QueryClientProvider>,
  );
  return view.container;
}

async function settle(rounds = 4): Promise<void> {
  for (let i = 0; i < rounds; i++) {
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
  }
}

function pick(label: string, value: string): void {
  fireEvent.change(screen.getByLabelText(label), { target: { value } });
}

function submit(): void {
  fireEvent.click(screen.getByRole("button", { name: t.submit }));
}

beforeEach(() => {
  vi.clearAllMocks();
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("AlertForm rendering", () => {
  it("create form defaults to a symbol rule on the price template", async () => {
    const c = mount();
    await settle();
    expect(c.innerHTML).toMatchSnapshot();
  });

  it.each(ALERT_TEMPLATE_KINDS)("template %s shows its own fields (TW)", async (kind) => {
    const c = mount();
    await settle();
    pick(t.template, kind);
    expect(c.innerHTML).toMatchSnapshot();
  });

  it("US market offers only the templates that have data", async () => {
    const c = mount();
    await settle();
    pick(t.market, "US");
    expect(c.innerHTML).toMatchSnapshot();
  });

  it("watchlist scope lists the groups and a hint", async () => {
    const c = mount({ preset: { scope: "watchlist" } });
    await settle();
    expect(c.innerHTML).toMatchSnapshot();
  });

  it("a TW group offers the Taiwan-only templates, a US group does not", async () => {
    const c = mount({ preset: { scope: "watchlist", groupId: "g_tw" } });
    await settle();
    const tw = c.innerHTML;
    pick(t.watchlist, "g_us");
    expect(c.innerHTML).not.toBe(tw);
    expect(c.innerHTML).toMatchSnapshot();
    expect(tw).toMatchSnapshot();
  });

  it("per-stock scope hands over to the per-stock form", async () => {
    const c = mount({ preset: { scope: "perStock", groupId: "g_tw" } });
    await settle();
    expect(c.innerHTML).toMatchSnapshot();
  });

  it("edit mode for a symbol rule", async () => {
    const c = mount({ rule: RULE });
    await settle();
    expect(c.innerHTML).toMatchSnapshot();
  });

  it("edit mode for a watchlist rule", async () => {
    const c = mount({ rule: GROUP_RULE });
    await settle();
    expect(c.innerHTML).toMatchSnapshot();
  });

  it("a preset symbol and market prefill the form", async () => {
    const c = mount({ preset: { scope: "symbol", symbol: "AAPL", market: "US" } });
    await settle();
    expect(c.innerHTML).toMatchSnapshot();
  });

  it("preview shows the symbol and the fact phrase once the value is valid", async () => {
    const c = mount();
    await settle();
    fireEvent.change(screen.getByLabelText(t.symbol), { target: { value: " 2330 " } });
    fireEvent.change(screen.getByLabelText(t.value), { target: { value: "123.5" } });
    expect(c.innerHTML).toMatchSnapshot();
  });
});

describe("AlertForm validation and switching", () => {
  it("submitting an empty symbol rule shows the required errors", async () => {
    const c = mount();
    await settle();
    submit();
    expect(c.innerHTML).toMatchSnapshot();
    expect(createAlert).not.toHaveBeenCalled();
  });

  it("an out-of-range value shows the range error", async () => {
    const c = mount();
    await settle();
    fireEvent.change(screen.getByLabelText(t.symbol), { target: { value: "2330" } });
    fireEvent.change(screen.getByLabelText(t.value), { target: { value: "-5" } });
    submit();
    expect(c.innerHTML).toMatchSnapshot();
    expect(createAlert).not.toHaveBeenCalled();
  });

  it("submitting a watchlist rule without a group asks for one", async () => {
    const c = mount({ preset: { scope: "watchlist" } });
    await settle();
    submit();
    expect(c.innerHTML).toMatchSnapshot();
    expect(createAlert).not.toHaveBeenCalled();
  });

  it("switching to a US symbol drops a Taiwan-only template", async () => {
    mount();
    await settle();
    pick(t.template, "attention_entry");
    pick(t.market, "US");
    expect((screen.getByLabelText(t.template) as HTMLSelectElement).value).toBe("close_price");
  });

  it("switching to a US symbol keeps a template both markets have", async () => {
    mount();
    await settle();
    pick(t.template, "volume_surge");
    pick(t.market, "US");
    expect((screen.getByLabelText(t.template) as HTMLSelectElement).value).toBe("volume_surge");
  });

  it("switching to the watchlist scope drops the price template", async () => {
    mount();
    await settle();
    pick(t.scope, "watchlist");
    expect((screen.getByLabelText(t.template) as HTMLSelectElement).value).toBe("daily_change");
  });

  it("switching to the watchlist scope keeps a scale-free template", async () => {
    mount();
    await settle();
    pick(t.template, "volume_surge");
    pick(t.scope, "watchlist");
    expect((screen.getByLabelText(t.template) as HTMLSelectElement).value).toBe("volume_surge");
  });

  it("switching back to a US symbol scope drops a Taiwan-only template", async () => {
    mount({ preset: { scope: "watchlist", groupId: "g_tw", market: "US" } });
    await settle();
    pick(t.template, "attention_entry");
    pick(t.scope, "symbol");
    expect((screen.getByLabelText(t.template) as HTMLSelectElement).value).toBe("close_price");
  });

  it("choosing a US group after a Taiwan-only template resets the template", async () => {
    mount({ preset: { scope: "watchlist", groupId: "g_tw" } });
    await settle();
    pick(t.template, "attention_entry");
    pick(t.watchlist, "g_us");
    expect((screen.getByLabelText(t.template) as HTMLSelectElement).value).toBe("daily_change");
  });

  it("choosing the empty group option treats the market as US", async () => {
    mount({ preset: { scope: "watchlist", groupId: "g_tw" } });
    await settle();
    pick(t.template, "attention_entry");
    pick(t.watchlist, "");
    expect((screen.getByLabelText(t.template) as HTMLSelectElement).value).toBe("daily_change");
  });

  it("the group hint link switches to the per-stock form", async () => {
    const c = mount({ preset: { scope: "watchlist" } });
    await settle();
    fireEvent.click(screen.getByRole("button", { name: t.groupHintAction }));
    expect(c.querySelector("[data-per-stock]")).not.toBeNull();
  });

  it("Escape closes the form", async () => {
    mount();
    await settle();
    fireEvent.keyDown(screen.getByRole("form"), { key: "Escape" });
    expect(onDone).toHaveBeenCalledTimes(1);
  });

  it("other keys do nothing", async () => {
    mount();
    await settle();
    fireEvent.keyDown(screen.getByRole("form"), { key: "a" });
    expect(onDone).not.toHaveBeenCalled();
  });

  it("the cancel button closes the form", async () => {
    mount();
    await settle();
    fireEvent.click(screen.getByRole("button", { name: t.cancel }));
    expect(onDone).toHaveBeenCalledTimes(1);
  });

  it("warns once about an unknown ticker after the user pauses typing", async () => {
    mount();
    await settle();
    vi.mocked(checkAlertSymbol).mockResolvedValue({ checked: true, exists: false } as never);
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    fireEvent.change(screen.getByLabelText(t.symbol), { target: { value: "appl" } });
    act(() => {
      vi.advanceTimersByTime(600);
    });
    vi.useRealTimers();
    await settle();
    expect(checkAlertSymbol).toHaveBeenCalledWith("APPL", "TW");
    expect(screen.getByRole("status").textContent).toContain("APPL");
  });

  it("does not warn when the lookup itself failed", async () => {
    mount();
    await settle();
    vi.mocked(checkAlertSymbol).mockResolvedValue({ checked: false, exists: false } as never);
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    fireEvent.change(screen.getByLabelText(t.symbol), { target: { value: "appl" } });
    act(() => {
      vi.advanceTimersByTime(600);
    });
    vi.useRealTimers();
    await settle();
    expect(screen.queryByRole("status")).toBeNull();
  });
});

describe("AlertForm submit", () => {
  it("creates a symbol rule", async () => {
    mount();
    await settle();
    fireEvent.change(screen.getByLabelText(t.symbol), { target: { value: " 2330 " } });
    fireEvent.change(screen.getByLabelText(t.value), { target: { value: "123.5" } });
    vi.mocked(createAlert).mockResolvedValue(RULE);
    submit();
    await settle();
    expect(createAlert).toHaveBeenCalledWith({
      symbol: "2330",
      market: "TW",
      condition: { kind: "close_price", comparator: "above", value: 123.5 },
      trigger_mode: "once_per_day",
      expires_in_days: 90,
    });
    expect(onDone).toHaveBeenCalledTimes(1);
  });

  it("creates a watchlist rule that never expires", async () => {
    mount({ preset: { scope: "watchlist", groupId: "g_tw" } });
    await settle();
    pick(t.expiry, "never");
    pick(t.triggerMode, "once");
    fireEvent.change(screen.getByLabelText(t.percent), { target: { value: "5" } });
    vi.mocked(createAlert).mockResolvedValue(GROUP_RULE);
    submit();
    await settle();
    expect(createAlert).toHaveBeenCalledWith({
      watchlist_id: "g_tw",
      condition: { kind: "daily_change", direction: "up", percent: 5 },
      trigger_mode: "once",
      expires_in_days: null,
    });
  });

  it("closes without saving when an edit changed nothing", async () => {
    mount({ rule: RULE });
    await settle();
    fireEvent.click(screen.getByRole("button", { name: t.save }));
    await settle();
    expect(updateAlert).not.toHaveBeenCalled();
    expect(onDone).toHaveBeenCalledTimes(1);
  });

  it("sends only the changed fields with the version when editing", async () => {
    mount({ rule: RULE });
    await settle();
    fireEvent.change(screen.getByLabelText(t.value), { target: { value: "150" } });
    vi.mocked(updateAlert).mockResolvedValue(RULE);
    fireEvent.click(screen.getByRole("button", { name: t.save }));
    await settle();
    expect(updateAlert).toHaveBeenCalledWith("r1", {
      expected_version: 3,
      condition: { kind: "close_price", comparator: "above", value: 150 },
    });
    expect(onDone).toHaveBeenCalledTimes(1);
  });

  it("sends the expiry change when editing", async () => {
    mount({ rule: RULE });
    await settle();
    pick(t.expiry, "never");
    vi.mocked(updateAlert).mockResolvedValue(RULE);
    fireEvent.click(screen.getByRole("button", { name: t.save }));
    await settle();
    expect(updateAlert).toHaveBeenCalledWith("r1", { expected_version: 3, expires_in_days: null });
  });

  it.each([
    ["cap_reached", t.errorCap],
    ["other", t.errorCreate],
  ])("shows the %s failure on create", async (code, message) => {
    mount();
    await settle();
    fireEvent.change(screen.getByLabelText(t.symbol), { target: { value: "2330" } });
    fireEvent.change(screen.getByLabelText(t.value), { target: { value: "10" } });
    vi.mocked(createAlert).mockRejectedValue(new SettingsApiError(code, "x", 400, false));
    submit();
    await settle();
    expect(screen.getByRole("alert").textContent).toBe(message);
  });

  it("shows a generic failure on update", async () => {
    mount({ rule: RULE });
    await settle();
    fireEvent.change(screen.getByLabelText(t.value), { target: { value: "150" } });
    vi.mocked(updateAlert).mockRejectedValue(new Error("boom"));
    fireEvent.click(screen.getByRole("button", { name: t.save }));
    await settle();
    expect(screen.getByRole("alert").textContent).toBe(t.errorUpdate);
  });

  it("folds another device's change into the open form after a stale save", async () => {
    mount({ rule: RULE });
    await settle();
    fireEvent.change(screen.getByLabelText(t.value), { target: { value: "150" } });
    vi.mocked(updateAlert).mockRejectedValue(
      new SettingsApiError("stale_version", "x", 409, false),
    );
    vi.mocked(listAlerts).mockResolvedValue([{ ...RULE, version: 4, trigger_mode: "once" }]);
    fireEvent.click(screen.getByRole("button", { name: t.save }));
    await settle();
    expect(screen.getByText(t.merged)).toBeTruthy();
    expect((screen.getByLabelText(t.triggerMode) as HTMLSelectElement).value).toBe("once");
    expect((screen.getByLabelText(t.value) as HTMLInputElement).value).toBe("150");
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("flags a conflict when both sides changed the same field", async () => {
    mount({ rule: RULE });
    await settle();
    fireEvent.change(screen.getByLabelText(t.value), { target: { value: "150" } });
    vi.mocked(updateAlert).mockRejectedValue(
      new SettingsApiError("stale_version", "x", 409, false),
    );
    vi.mocked(listAlerts).mockResolvedValue([
      {
        ...RULE,
        version: 4,
        condition: { kind: "close_price", comparator: "above", value: 200 },
      },
    ]);
    fireEvent.click(screen.getByRole("button", { name: t.save }));
    await settle();
    expect(screen.getByText(t.mergedConflict)).toBeTruthy();
    expect((screen.getByLabelText(t.value) as HTMLInputElement).value).toBe("150");
  });

  it("keeps the stale message when the rule was deleted meanwhile", async () => {
    mount({ rule: RULE });
    await settle();
    fireEvent.change(screen.getByLabelText(t.value), { target: { value: "150" } });
    vi.mocked(updateAlert).mockRejectedValue(
      new SettingsApiError("stale_version", "x", 409, false),
    );
    vi.mocked(listAlerts).mockResolvedValue([]);
    fireEvent.click(screen.getByRole("button", { name: t.save }));
    await settle();
    expect(screen.getByRole("alert").textContent).toBe(t.errorStale);
  });

  it("keeps the stale message when reloading the rule fails", async () => {
    mount({ rule: RULE });
    await settle();
    fireEvent.change(screen.getByLabelText(t.value), { target: { value: "150" } });
    vi.mocked(updateAlert).mockRejectedValue(
      new SettingsApiError("stale_version", "x", 409, false),
    );
    vi.mocked(listAlerts).mockRejectedValue(new Error("offline"));
    fireEvent.click(screen.getByRole("button", { name: t.save }));
    await settle();
    expect(screen.getByRole("alert").textContent).toBe(t.errorStale);
  });
});
