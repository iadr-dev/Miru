// @vitest-environment jsdom
import { cleanup, fireEvent, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { QueryState } from "@/components/ui/query-state";
import { ApiRequestError } from "@/lib/api/http";
import { messages, renderWithIntl } from "@/test/render";

afterEach(cleanup);

type Query = Parameters<typeof QueryState<string>>[0]["query"];

function query(overrides: Partial<Query>): Query {
  return {
    isLoading: false,
    isError: false,
    error: null,
    data: undefined,
    refetch: () => {},
    ...overrides,
  };
}

describe("QueryState", () => {
  it("shows the caller's skeleton while loading", () => {
    renderWithIntl(
      <QueryState query={query({ isLoading: true })} skeleton={<p>loading-shape</p>}>
        {(data) => <p>{data}</p>}
      </QueryState>,
    );
    expect(screen.getByText("loading-shape")).toBeTruthy();
  });

  it("shows a failure as an error with a working retry, not as empty", () => {
    const refetch = vi.fn();
    renderWithIntl(
      <QueryState query={query({ isError: true, error: new Error("boom"), refetch })}>
        {(data) => <p>{data}</p>}
      </QueryState>,
    );
    expect(screen.getByRole("alert").textContent).toContain(messages.market.cards.error);
    fireEvent.click(screen.getByRole("button", { name: messages.market.cards.retry }));
    expect(refetch).toHaveBeenCalledOnce();
  });

  it("shows the API's own message for an API error", () => {
    const error = new ApiRequestError(
      "ApiRequestError",
      "upstream_unavailable",
      "資料來源暫時無法連線",
      503,
      true,
    );
    renderWithIntl(
      <QueryState query={query({ isError: true, error })}>{(data) => <p>{data}</p>}</QueryState>,
    );
    expect(screen.getByRole("alert").textContent).toContain("資料來源暫時無法連線");
  });

  it("renders the children with the data once it arrives", () => {
    renderWithIntl(
      <QueryState query={query({ data: "2330 台積電" })}>{(data) => <p>{data}</p>}</QueryState>,
    );
    expect(screen.getByText("2330 台積電")).toBeTruthy();
  });
});
