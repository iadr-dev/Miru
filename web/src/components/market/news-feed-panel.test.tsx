// @vitest-environment jsdom
import { cleanup } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { MacroCalendarEmpty } from "@/components/market/macro-calendar-panel";
import { NewsFeedContent } from "@/components/market/news-feed-panel";
import { NewsRows } from "@/components/us-stock/news-filings-panel";
import { messages, renderWithIntl } from "@/test/render";
import type { NewsFeedResponse } from "@/types/market/news";

afterEach(cleanup);

const feedMessages = messages.market.cards.newsFeed;
const base = { source_id: "NEWS", currentness_caveat: "caveat" } as const;
const item = {
  title: "台積電法說會",
  link: "https://news.cnyes.com/a",
  source: "cnyes",
  published_at: "2026-10-27T01:00:00+00:00",
};

function renderFeed(data: NewsFeedResponse) {
  return renderWithIntl(
    <NewsFeedContent data={data} expanded={false} onExpandedChange={() => {}} />,
  );
}

describe("NewsFeedContent", () => {
  it("lists items when the feed answered", () => {
    const { container } = renderFeed({ ...base, health_status: "ok", items: [item] });
    expect(container.textContent).toContain("台積電法說會");
    expect(container.textContent).not.toContain(feedMessages.empty);
  });

  it("keeps showing items even when the source reports it is stale", () => {
    const { container } = renderFeed({ ...base, health_status: "stale", items: [item] });
    expect(container.textContent).toContain("台積電法說會");
    expect(container.textContent).not.toContain(feedMessages.unavailable);
  });

  it("says the source is unreachable, not 'no news', on an outage", () => {
    const { container } = renderFeed({ ...base, health_status: "unreachable", items: [] });
    expect(container.textContent).toContain(feedMessages.unavailable);
    expect(container.textContent).not.toContain(feedMessages.empty);
  });

  it("treats an unavailable tier the same as an outage", () => {
    const { container } = renderFeed({ ...base, health_status: "unavailable", items: [] });
    expect(container.textContent).toContain(feedMessages.unavailable);
  });

  it("shows the plain empty text when the feeds answered with nothing", () => {
    const { container } = renderFeed({ ...base, health_status: "stale", items: [] });
    expect(container.textContent).toContain(feedMessages.empty);
    expect(container.textContent).not.toContain(feedMessages.unavailable);
  });
});

describe("NewsRows (stock page news tab)", () => {
  const rowMessages = messages.usStock.newsFilings;

  it("lists items", () => {
    const { container } = renderWithIntl(<NewsRows items={[item]} health="ok" />);
    expect(container.textContent).toContain("台積電法說會");
  });

  it("reports an outage instead of 'no related news'", () => {
    const { container } = renderWithIntl(<NewsRows items={[]} health="unreachable" />);
    expect(container.textContent).toContain(rowMessages.newsUnavailable);
    expect(container.textContent).not.toContain(rowMessages.newsEmpty);
  });

  it("shows the plain empty text when the source answered with nothing", () => {
    const { container } = renderWithIntl(<NewsRows items={[]} health="stale" />);
    expect(container.textContent).toContain(rowMessages.newsEmpty);
  });
});

describe("MacroCalendarEmpty", () => {
  const calendarMessages = messages.market.cards.macroCalendar;

  it("reports an outage instead of an empty window", () => {
    const { container } = renderWithIntl(<MacroCalendarEmpty health="unreachable" />);
    expect(container.textContent).toContain(calendarMessages.unavailable);
    expect(container.textContent).not.toContain(calendarMessages.empty);
  });

  it("shows the plain empty text when the source answered with nothing", () => {
    const { container } = renderWithIntl(<MacroCalendarEmpty health="stale" />);
    expect(container.textContent).toContain(calendarMessages.empty);
  });
});
