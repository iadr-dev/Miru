// @vitest-environment jsdom
import { cleanup, fireEvent, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ModelMenu } from "@/app/[locale]/(app)/chat/composer-parts/model-menu";
import { useModels } from "@/hooks/use-models";
import { messages, renderWithIntl } from "@/test/render";
import type { ModelCatalog } from "@/types/models";

vi.mock("@/hooks/use-models", () => ({ useModels: vi.fn() }));

const CATALOG: ModelCatalog = {
  selectable: true,
  models: [
    {
      provider: "anthropic",
      id: "anthropic/claude-sonnet-5-5",
      label_zh: "Claude Sonnet 5.5",
      label_en: "Claude Sonnet 5.5",
      is_default: true,
    },
    {
      provider: "anthropic",
      id: "anthropic/claude-opus-5-5",
      label_zh: "Claude Opus 5.5",
      label_en: "Claude Opus 5.5",
      is_default: false,
    },
    {
      provider: "openai",
      id: "openai/gpt-5.4",
      label_zh: "GPT-5.4",
      label_en: "GPT-5.4",
      is_default: false,
    },
  ],
};

type Q = ReturnType<typeof useModels>;
function mockModels(partial: Partial<Record<string, unknown>>): void {
  vi.mocked(useModels).mockReturnValue({
    data: undefined,
    isLoading: false,
    isError: false,
    isSuccess: false,
    refetch: vi.fn(),
    ...partial,
  } as unknown as Q);
}

function open(): void {
  fireEvent.keyDown(screen.getByRole("button", { name: messages.chat.composer.model }), {
    key: "Enter",
  });
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const copy = messages.chat.composer.modelMenu;

describe("ModelMenu", () => {
  it("renders nothing for a caller without their own keys (no picker, no lock chip)", () => {
    mockModels({});
    const { container } = renderWithIntl(
      <ModelMenu
        label={messages.chat.composer.model}
        byokActive={false}
        selectedId={null}
        onSelect={vi.fn()}
      />,
    );
    expect(container.innerHTML).toBe("");
    expect(vi.mocked(useModels)).toHaveBeenCalledWith(false);
  });

  it("renders nothing while it is still unknown whether the caller is BYOK", () => {
    mockModels({});
    const { container } = renderWithIntl(
      <ModelMenu
        label={messages.chat.composer.model}
        byokActive={undefined}
        selectedId={null}
        onSelect={vi.fn()}
      />,
    );
    expect(container.innerHTML).toBe("");
  });

  it("shows grouped options, marks the main model, and reports a pick", () => {
    mockModels({ data: CATALOG, isSuccess: true });
    const onSelect = vi.fn();
    renderWithIntl(
      <ModelMenu
        label={messages.chat.composer.model}
        byokActive
        selectedId={null}
        onSelect={onSelect}
      />,
    );
    // Trigger names the effective main model when the chat has no pick of its own.
    expect(
      screen.getByRole("button", { name: messages.chat.composer.model }).textContent,
    ).toContain("Claude Sonnet 5.5");
    open();
    const items = screen.getAllByRole("menuitemradio");
    expect(items.map((i) => i.textContent)).toEqual([
      `${copy.useDefault}Claude Sonnet 5.5`,
      `Claude Sonnet 5.5${copy.mainBadge}`,
      "Claude Opus 5.5",
      "GPT-5.4",
    ]);
    expect(screen.getByText("Anthropic")).toBeTruthy();
    expect(screen.getByText("OpenAI")).toBeTruthy();
    // 44px targets
    expect(items.every((i) => i.className.includes("min-h-11"))).toBe(true);
    fireEvent.click(screen.getByText("GPT-5.4"));
    expect(onSelect).toHaveBeenCalledWith("openai/gpt-5.4");
  });

  it("'use my default' clears the chat's own pick", () => {
    mockModels({ data: CATALOG, isSuccess: true });
    const onSelect = vi.fn();
    renderWithIntl(
      <ModelMenu
        label={messages.chat.composer.model}
        byokActive
        selectedId="openai/gpt-5.4"
        onSelect={onSelect}
      />,
    );
    open();
    fireEvent.click(screen.getByText(copy.useDefault));
    expect(onSelect).toHaveBeenCalledWith(null);
  });

  it("offers a retry when the list fails to load", () => {
    const refetch = vi.fn();
    mockModels({ isError: true, refetch });
    renderWithIntl(
      <ModelMenu
        label={messages.chat.composer.model}
        byokActive
        selectedId={null}
        onSelect={vi.fn()}
      />,
    );
    open();
    expect(screen.getByRole("alert").textContent).toContain(copy.loadError);
    fireEvent.click(screen.getByRole("button", { name: copy.retry }));
    expect(refetch).toHaveBeenCalled();
  });

  it("says so when the saved keys unlock no models", () => {
    mockModels({ data: { selectable: true, models: [] }, isSuccess: true });
    renderWithIntl(
      <ModelMenu
        label={messages.chat.composer.model}
        byokActive
        selectedId={null}
        onSelect={vi.fn()}
      />,
    );
    open();
    expect(screen.getByText(copy.empty)).toBeTruthy();
  });
});
