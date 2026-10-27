// @vitest-environment jsdom
import { cleanup } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { PttBoardContent } from "@/components/market/ptt-board-panel";
import { messages, renderWithIntl } from "@/test/render";
import type { PttBoardResponse } from "@/types/market/news";

afterEach(cleanup);

const pttMessages = messages.market.cards.pttBoard;

function render(data: PttBoardResponse) {
  return renderWithIntl(
    <PttBoardContent data={data} expanded={false} onExpandedChange={() => {}} />,
  );
}

const base = { source_id: "PTT", currentness_caveat: "caveat" } as const;

describe("PttBoardContent", () => {
  it("lists posts when the source answered with posts", () => {
    const { container } = render({
      ...base,
      health_status: "ok",
      posts: [
        {
          board: "Stock",
          board_tag: "stock_buzz",
          title: "標的 2330",
          author: "a",
          push_count: "爆",
          link: "https://www.ptt.cc/bbs/Stock/x.html",
        },
      ],
    });
    expect(container.textContent).toContain("標的 2330");
    expect(container.textContent).toContain(pttMessages.buzzCaveat);
    expect(container.textContent).not.toContain(pttMessages.empty);
  });

  it("says the source is unreachable, not 'no posts', on an outage", () => {
    const { container } = render({ ...base, health_status: "unreachable", posts: [] });
    expect(container.textContent).toContain(pttMessages.unavailable);
    expect(container.textContent).not.toContain(pttMessages.empty);
  });

  it("treats unavailable tiers the same as an outage", () => {
    const { container } = render({ ...base, health_status: "unavailable", posts: [] });
    expect(container.textContent).toContain(pttMessages.unavailable);
  });

  it("shows the plain empty text when the source is healthy but nothing is posted", () => {
    const { container } = render({ ...base, health_status: "stale", posts: [] });
    expect(container.textContent).toContain(pttMessages.empty);
    expect(container.textContent).not.toContain(pttMessages.unavailable);
  });
});
