// @vitest-environment jsdom
import { cleanup, fireEvent, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { renderWithIntl } from "@/test/render";
import { notificationSchema } from "@/types/settings";
import { type NotificationView, toNotificationView } from "./notification-payload";
import { NotificationRow } from "./notification-row";

afterEach(cleanup);

const FOOTER = "本通知為你設定條件的資料提醒，非投資建議。";

function view(payload: unknown, readAt: string | null = null): NotificationView {
  return toNotificationView(
    notificationSchema.parse({
      id: "n1",
      user_id: "u",
      source: "alert",
      source_id: "a1",
      channel: "in_app",
      dedupe_key: "k",
      payload,
      delivered_at: "2026-10-03T09:00:00Z",
      read_at: readAt,
    }),
  );
}

function renderRow(item: NotificationView, onOpen = vi.fn()): ReturnType<typeof vi.fn> {
  renderWithIntl(
    <ul>
      <NotificationRow item={item} onOpen={onOpen} />
    </ul>,
  );
  return onOpen;
}

const alertPayload = {
  title: "2330 - 收盤價高於 1000",
  body: `2330 台積電 收盤價 1,020\n${FOOTER}`,
  layout: {
    blocks: [
      {
        head: "🔔 2330 台積電",
        lines: ["收盤價 1,020，高於設定值 1,000", "條件：收盤價高於 1,000", "資料日期：2026-10-27"],
        fold: false,
      },
    ],
  },
  footer: FOOTER,
};

const stocks = Array.from({ length: 29 }, (_, i) => `${2300 + i} 公司${i}　收盤 ${100 + i}`);
const morningPayload = {
  title: "今日盯盤清單",
  body: "…",
  layout: {
    blocks: [
      { head: "📋 今日盯盤清單 2026-10-27", lines: [], fold: false },
      { head: "波動壓縮", lines: stocks, fold: true },
    ],
  },
  footer: FOOTER,
};

describe("NotificationRow with a layout", () => {
  it("shows the heading as the open button, one paragraph per line, and the footer", () => {
    const onOpen = renderRow(view(alertPayload));
    const heading = screen.getByRole("button", { name: "🔔 2330 台積電" });
    expect(heading.className).toContain("font-semibold");
    for (const line of alertPayload.layout.blocks[0]?.lines ?? []) {
      expect(screen.getByText(line).tagName).toBe("P");
    }
    expect(screen.getByText(FOOTER)).toBeTruthy();
    expect(screen.queryByText("2330 - 收盤價高於 1000")).toBeNull();
    fireEvent.click(heading);
    expect(onOpen).toHaveBeenCalledTimes(1);
  });

  it("shows a fold block as a collapsed native disclosure with its count", () => {
    const { container } = renderWithIntl(
      <ul>
        <NotificationRow item={view(morningPayload)} onOpen={vi.fn()} />
      </ul>,
    );
    const details = container.querySelector("details");
    expect(details).not.toBeNull();
    expect(details?.open).toBe(false);
    const summary = details?.querySelector("summary");
    expect(summary?.textContent).toContain("波動壓縮");
    expect(summary?.textContent).toContain("29 筆");
    expect(details?.querySelectorAll("p")).toHaveLength(29);
  });

  it("does not open the notification when the fold is toggled", () => {
    const onOpen = renderRow(view(morningPayload));
    fireEvent.click(screen.getByText("波動壓縮"));
    expect(onOpen).not.toHaveBeenCalled();
  });

  it("marks an unread row with text, not colour alone", () => {
    renderRow(view(alertPayload));
    expect(screen.getByText("未讀")).toBeTruthy();
    cleanup();
    renderRow(view(alertPayload, "2026-10-03T10:00:00Z"));
    expect(screen.queryByText("未讀")).toBeNull();
  });

  it("renders markup characters in a name as literal text and injects no elements", () => {
    const name = `A&B <img src=x onerror="alert(1)"> <script>x</script> 'q'`;
    const { container } = renderWithIntl(
      <ul>
        <NotificationRow
          item={view({
            title: name,
            body: name,
            layout: { blocks: [{ head: `🔔 ${name}`, lines: [name], fold: false }] },
            footer: FOOTER,
          })}
          onOpen={vi.fn()}
        />
      </ul>,
    );
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("script")).toBeNull();
    expect(screen.getByText(name).textContent).toBe(name);
    expect(screen.getByRole("button", { name: `🔔 ${name}` })).toBeTruthy();
  });
});

function plainPayload(lineCount: number): unknown {
  return {
    title: "清單",
    body: "…",
    layout: {
      blocks: [
        {
          head: "📋 清單",
          lines: Array.from({ length: lineCount }, (_, i) => `第 ${i + 1} 行`),
          fold: false,
        },
      ],
    },
    footer: FOOTER,
  };
}

describe("NotificationRow clamp", () => {
  it("shows a tall row's top and a collapsed control, hiding the rest but not the footer", () => {
    renderRow(view(plainPayload(10)));
    const control = screen.getByRole("button", { name: "顯示全部" });
    expect(control.getAttribute("aria-expanded")).toBe("false");
    expect(control.className).toContain("min-h-11");
    expect(control.className).toContain("focus-visible:ring-2");
    expect(screen.getByText("第 5 行")).toBeTruthy();
    expect(screen.queryByText("第 6 行")).toBeNull();
    expect(screen.getByText(FOOTER)).toBeTruthy();
  });

  it("expands to every line, then collapses again, with the footer throughout", () => {
    renderRow(view(plainPayload(10)));
    fireEvent.click(screen.getByRole("button", { name: "顯示全部" }));
    const control = screen.getByRole("button", { name: "收合" });
    expect(control.getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByText("第 10 行")).toBeTruthy();
    expect(screen.getByText(FOOTER)).toBeTruthy();
    fireEvent.click(control);
    expect(screen.queryByText("第 10 行")).toBeNull();
    expect(screen.getByText(FOOTER)).toBeTruthy();
    expect(screen.getByRole("button", { name: "顯示全部" }).getAttribute("aria-expanded")).toBe(
      "false",
    );
  });

  it("does not open the notification when the control is toggled", () => {
    const onOpen = renderRow(view(plainPayload(10)));
    fireEvent.click(screen.getByRole("button", { name: "顯示全部" }));
    fireEvent.click(screen.getByRole("button", { name: "收合" }));
    expect(onOpen).not.toHaveBeenCalled();
  });

  it("keeps the heading as the only open button while clamped", () => {
    const onOpen = renderRow(view(plainPayload(10)));
    fireEvent.click(screen.getByRole("button", { name: "📋 清單" }));
    expect(onOpen).toHaveBeenCalledTimes(1);
  });

  it("shows a short row whole with no control", () => {
    renderRow(view(plainPayload(3)));
    expect(screen.queryByRole("button", { name: "顯示全部" })).toBeNull();
    expect(screen.getByText("第 3 行")).toBeTruthy();
    expect(screen.getByText(FOOTER)).toBeTruthy();
  });

  it("shows a row exactly at the budget whole with no control", () => {
    renderRow(view(plainPayload(5)));
    expect(screen.queryByRole("button", { name: "顯示全部" })).toBeNull();
    expect(screen.getByText("第 5 行")).toBeTruthy();
  });

  it("clamps a long morning list to its heading and fold, footer still shown", () => {
    const longMorning = {
      ...morningPayload,
      layout: {
        blocks: [
          ...morningPayload.layout.blocks,
          { head: "量能放大", lines: stocks, fold: true },
          { head: "站上均線", lines: stocks, fold: true },
          { head: "籌碼轉強", lines: stocks, fold: true },
          { head: "突破整理", lines: stocks, fold: true },
          { head: "跌深反彈", lines: stocks, fold: true },
        ],
      },
    };
    const { container } = renderWithIntl(
      <ul>
        <NotificationRow item={view(longMorning)} onOpen={vi.fn()} />
      </ul>,
    );
    expect(container.querySelectorAll("details")).toHaveLength(5);
    expect(screen.getByRole("button", { name: "顯示全部" })).toBeTruthy();
    expect(screen.getByText(FOOTER)).toBeTruthy();
  });
});

describe("NotificationRow whose first block is a fold with a head", () => {
  const foldFirst = {
    title: "今日盯盤清單",
    body: "…",
    layout: { blocks: [{ head: "波動壓縮", lines: ["2330", "2317"], fold: true }] },
    footer: FOOTER,
  };

  it("still has exactly one open button, labelled with the title", () => {
    const onOpen = renderRow(view(foldFirst));
    const buttons = screen.getAllByRole("button");
    expect(buttons).toHaveLength(1);
    expect(buttons[0]?.textContent).toBe("今日盯盤清單");
    fireEvent.click(buttons[0] as HTMLElement);
    expect(onOpen).toHaveBeenCalledTimes(1);
  });
});

describe("NotificationRow without a usable layout", () => {
  it("falls back to the title and the plain body", () => {
    const item = view({ title: "排程摘要", body: `一行\n二行\n${FOOTER}` });
    const onOpen = renderRow(item);
    fireEvent.click(screen.getByRole("button", { name: "排程摘要" }));
    expect(onOpen).toHaveBeenCalledWith(item);
    expect(screen.getByText(/一行/).className).toContain("whitespace-pre-line");
  });

  it("falls back when the layout is invalid", () => {
    const item = view({
      title: "排程摘要",
      body: "內文",
      layout: { blocks: [{ head: 5, lines: "x", fold: "y" }] },
    });
    renderRow(item);
    expect(screen.getByRole("button", { name: "排程摘要" })).toBeTruthy();
    expect(screen.getByText("內文")).toBeTruthy();
  });
});
