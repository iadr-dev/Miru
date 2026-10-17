// @vitest-environment jsdom
import { act, cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import type { AnchorHTMLAttributes, ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ConsentScreen } from "@/components/oauth/consent-screen";
import { messages, renderWithIntl } from "@/test/render";
import type { ConsentResponse } from "@/types/oauth-consent";

vi.mock("next/image", () => ({
  // biome-ignore lint/performance/noImgElement: test double for next/image
  default: ({ alt }: { alt: string }) => <img alt={alt} />,
}));

vi.mock("@/i18n/navigation", () => ({
  Link: ({
    href,
    children,
    ...rest
  }: AnchorHTMLAttributes<HTMLAnchorElement> & { href: string }) => (
    <a href={href} {...rest}>
      {children as ReactNode}
    </a>
  ),
}));

const t = messages.oauthConsent;

function consent(overrides: Partial<ConsentResponse> = {}): ConsentResponse {
  return {
    request_id: "req-1",
    client: {
      name: "Claude",
      client_id: "client-1",
      kind: "registered",
      redirect_host: "claude.ai",
      verified: true,
    },
    redirect_uri: "https://claude.ai/api/mcp/auth_callback?code=leak&state=x",
    scopes: [
      { id: "market:read", description: "Read market data you can already see" },
      { id: "watchlist:read", description: "Read your watchlists" },
    ],
    resource: { url: "https://api.example.test/mcp", label: "Miru MCP" },
    nonce: "nonce-abc",
    expires_at: new Date(Date.now() + 5 * 60_000).toISOString(),
    ...overrides,
  };
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function apiError(status: number, code: string): Response {
  return json({ code, message: code, retryable: false }, status);
}

const assign = vi.fn();

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn());
  Object.defineProperty(window, "location", {
    configurable: true,
    value: { assign },
  });
});

afterEach(() => {
  cleanup();
  assign.mockReset();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

function urls(): string[] {
  return vi.mocked(fetch).mock.calls.map((call) => String(call[0]));
}

describe("ConsentScreen", () => {
  it("shows a loading state, then the request", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(json(consent()));
    renderWithIntl(<ConsentScreen requestId="req-1" error={null} />);

    expect(screen.getByRole("status").textContent).toBe(t.loading);
    const heading = await screen.findByRole("heading", { level: 1 });
    expect(heading.textContent).toBe(t.title.replace("{client}", "Claude"));
    expect(document.activeElement).toBe(heading);
    expect(screen.getByText("Read your watchlists")).toBeTruthy();
    expect(screen.getByText("Read market data you can already see")).toBeTruthy();
    expect(urls()[0]).toContain("/oauth/consent/req-1");
  });

  it("shows only origin and path of the destination, never the query", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(json(consent()));
    renderWithIntl(<ConsentScreen requestId="req-1" error={null} />);
    await screen.findByRole("heading", { level: 1 });
    expect(screen.getByText("https://claude.ai/api/mcp/auth_callback")).toBeTruthy();
    expect(screen.queryByText(/leak/)).toBeNull();
  });

  it("has no unverified warning for a verified client", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(json(consent()));
    renderWithIntl(<ConsentScreen requestId="req-1" error={null} />);
    await screen.findByRole("heading", { level: 1 });
    expect(screen.queryByText(t.unverified.title)).toBeNull();
  });

  it("warns about an unverified app", async () => {
    const base = consent();
    vi.mocked(fetch).mockResolvedValueOnce(
      json(consent({ client: { ...base.client, verified: false } })),
    );
    renderWithIntl(<ConsentScreen requestId="req-1" error={null} />);
    expect(await screen.findByText(t.unverified.title)).toBeTruthy();
    expect(screen.getByText(t.unverified.body.replace("{client}", "Claude"))).toBeTruthy();
  });

  it("approve posts the nonce and navigates to redirect_to", async () => {
    vi.mocked(fetch)
      .mockResolvedValueOnce(json(consent()))
      .mockResolvedValueOnce(json({ redirect_to: "https://claude.ai/cb?code=abc" }));
    renderWithIntl(<ConsentScreen requestId="req-1" error={null} />);
    await screen.findByRole("heading", { level: 1 });

    fireEvent.click(
      screen.getByRole("button", { name: t.allowLabel.replace("{client}", "Claude") }),
    );

    await waitFor(() => expect(assign).toHaveBeenCalledWith("https://claude.ai/cb?code=abc"));
    const [url, init] = vi.mocked(fetch).mock.calls[1] ?? [];
    expect(String(url)).toContain("/oauth/consent/req-1");
    expect(init?.method).toBe("POST");
    expect(init?.credentials).toBe("include");
    expect(JSON.parse(String(init?.body))).toEqual({ decision: "approve", nonce: "nonce-abc" });
    // Both answers stay disabled while the browser leaves.
    for (const button of screen.getAllByRole("button")) {
      expect((button as HTMLButtonElement).disabled).toBe(true);
    }
  });

  it("deny posts the nonce with decision deny", async () => {
    vi.mocked(fetch)
      .mockResolvedValueOnce(json(consent()))
      .mockResolvedValueOnce(json({ redirect_to: "https://claude.ai/cb?error=access_denied" }));
    renderWithIntl(<ConsentScreen requestId="req-1" error={null} />);
    await screen.findByRole("heading", { level: 1 });

    fireEvent.click(
      screen.getByRole("button", { name: t.denyLabel.replace("{client}", "Claude") }),
    );

    await waitFor(() =>
      expect(assign).toHaveBeenCalledWith("https://claude.ai/cb?error=access_denied"),
    );
    const init = vi.mocked(fetch).mock.calls[1]?.[1];
    expect(JSON.parse(String(init?.body))).toEqual({ decision: "deny", nonce: "nonce-abc" });
  });

  it("disables both answers while a decision is in flight", async () => {
    let finish: (response: Response) => void = () => undefined;
    vi.mocked(fetch)
      .mockResolvedValueOnce(json(consent()))
      .mockReturnValueOnce(new Promise<Response>((resolve) => (finish = resolve)));
    renderWithIntl(<ConsentScreen requestId="req-1" error={null} />);
    await screen.findByRole("heading", { level: 1 });

    fireEvent.click(
      screen.getByRole("button", { name: t.allowLabel.replace("{client}", "Claude") }),
    );

    await waitFor(() => {
      for (const button of screen.getAllByRole("button")) {
        expect((button as HTMLButtonElement).disabled).toBe(true);
      }
    });
    await act(async () => {
      finish(json({ redirect_to: "https://claude.ai/cb" }));
    });
  });

  it("counts down and disables the answers once the request expires", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(
      json(consent({ expires_at: new Date(Date.now() + 2_000).toISOString() })),
    );
    renderWithIntl(<ConsentScreen requestId="req-1" error={null} />);
    await screen.findByRole("heading", { level: 1 });
    expect(screen.getByText(t.expiresIn.replace("{time}", "0:02"))).toBeTruthy();

    expect(await screen.findByText(t.expired, {}, { timeout: 4_000 })).toBeTruthy();
    expect(
      (
        screen.getByRole("button", {
          name: t.denyLabel.replace("{client}", "Claude"),
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(true);
  });

  it.each([
    ["oauth_browser_mismatch", 403],
    ["oauth_user_mismatch", 403],
    ["oauth_request_not_found", 404],
  ] as const)("shows the %s message when the request cannot be read", async (code, status) => {
    vi.mocked(fetch).mockResolvedValueOnce(apiError(status, code));
    renderWithIntl(<ConsentScreen requestId="req-1" error={null} />);

    expect(await screen.findByRole("alert")).toBeTruthy();
    expect(screen.getByText(t.errors[code])).toBeTruthy();
    expect(screen.getByRole("link", { name: t.home }).getAttribute("href")).toBe("/");
    expect(screen.queryByRole("button", { name: /Claude/ })).toBeNull();
  });

  it("shows the nonce mismatch message when the decision is refused", async () => {
    vi.mocked(fetch)
      .mockResolvedValueOnce(json(consent()))
      .mockResolvedValueOnce(apiError(403, "oauth_nonce_mismatch"));
    renderWithIntl(<ConsentScreen requestId="req-1" error={null} />);
    await screen.findByRole("heading", { level: 1 });

    fireEvent.click(
      screen.getByRole("button", { name: t.allowLabel.replace("{client}", "Claude") }),
    );

    expect(await screen.findByText(t.errors.oauth_nonce_mismatch)).toBeTruthy();
    expect(assign).not.toHaveBeenCalled();
  });

  it("lets the user retry after a transient failure on a decision", async () => {
    vi.mocked(fetch)
      .mockResolvedValueOnce(json(consent()))
      .mockResolvedValueOnce(json({ code: "boom", message: "boom", retryable: true }, 500));
    renderWithIntl(<ConsentScreen requestId="req-1" error={null} />);
    await screen.findByRole("heading", { level: 1 });

    fireEvent.click(
      screen.getByRole("button", { name: t.allowLabel.replace("{client}", "Claude") }),
    );

    expect(await screen.findByText(t.errors.retry)).toBeTruthy();
    expect(
      (
        screen.getByRole("button", {
          name: t.allowLabel.replace("{client}", "Claude"),
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(false);
  });

  it("shows a calm error with a link home for ?error=, without calling the API", () => {
    renderWithIntl(<ConsentScreen requestId={null} error="invalid_redirect_uri" />);
    expect(screen.getByRole("alert")).toBeTruthy();
    expect(screen.getByText(t.linkError.body)).toBeTruthy();
    expect(screen.getByRole("link", { name: t.home }).getAttribute("href")).toBe("/");
    expect(screen.queryByText(/invalid_redirect_uri/)).toBeNull();
    expect(fetch).not.toHaveBeenCalled();
  });

  it("on 401 refreshes once, then offers sign-in that returns to this page", async () => {
    vi.mocked(fetch)
      .mockResolvedValueOnce(apiError(401, "unauthenticated")) // GET
      .mockResolvedValueOnce(apiError(401, "no_refresh_token")) // POST /auth/refresh
      .mockResolvedValueOnce(
        json({
          authorization_url: "https://accounts.example.test/auth",
          retry_authorization_url: null,
        }),
      ); // begin OAuth
    renderWithIntl(<ConsentScreen requestId="req 1" error={null} />);

    expect(await screen.findByRole("heading", { name: t.signIn.title })).toBeTruthy();
    expect(urls()[1]).toContain("/auth/refresh");
    expect(urls().filter((u) => u.includes("/oauth/consent/"))).toHaveLength(1);

    fireEvent.click(screen.getByRole("button", { name: messages.auth.modal.continueWithGoogle }));

    await waitFor(() => expect(assign).toHaveBeenCalledWith("https://accounts.example.test/auth"));
    const beginUrl = new URL(urls()[2] ?? "");
    expect(beginUrl.pathname).toMatch(/\/auth\/oauth\/google$/);
    expect(beginUrl.searchParams.get("next")).toBe("/zh-TW/oauth/consent?request=req+1");
  });

  it("uses the refreshed session when the refresh succeeds", async () => {
    vi.mocked(fetch)
      .mockResolvedValueOnce(apiError(401, "unauthenticated"))
      .mockResolvedValueOnce(json({ ok: true })) // refresh
      .mockResolvedValueOnce(json(consent()));
    renderWithIntl(<ConsentScreen requestId="req-1" error={null} />);
    const heading = await screen.findByRole("heading", { level: 1 });
    expect(heading.textContent).toBe(t.title.replace("{client}", "Claude"));
  });
});
