// @vitest-environment jsdom
import { cleanup, fireEvent, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { DefaultModelSetting } from "@/app/[locale]/(app)/settings/byok/default-model-setting";
import { useByokActive, useModelDefault, useModels, useSetModelDefault } from "@/hooks/use-models";
import { messages, renderWithIntl } from "@/test/render";

vi.mock("@/hooks/use-models", () => ({
  useByokActive: vi.fn(),
  useModels: vi.fn(),
  useModelDefault: vi.fn(),
  useSetModelDefault: vi.fn(),
}));

const copy = messages.settings.byok.defaultModel;
const mutate = vi.fn();

type Loose = Partial<Record<string, unknown>>;
function setup(opts: { byok: boolean | undefined; models?: Loose; saved?: Loose }): void {
  vi.mocked(useByokActive).mockReturnValue(opts.byok);
  vi.mocked(useModels).mockReturnValue({
    data: undefined,
    isLoading: false,
    isError: false,
    refetch: vi.fn(),
    ...opts.models,
  } as unknown as ReturnType<typeof useModels>);
  vi.mocked(useModelDefault).mockReturnValue({
    data: { default_model: null },
    isLoading: false,
    isError: false,
    refetch: vi.fn(),
    ...opts.saved,
  } as unknown as ReturnType<typeof useModelDefault>);
  vi.mocked(useSetModelDefault).mockReturnValue({ mutate } as unknown as ReturnType<
    typeof useSetModelDefault
  >);
}

const MODELS = {
  data: {
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
        provider: "openai",
        id: "openai/gpt-5.4",
        label_zh: "GPT-5.4",
        label_en: "GPT-5.4",
        is_default: false,
      },
    ],
  },
};

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("DefaultModelSetting", () => {
  it("tells a caller without their own keys that the platform chooses, with no selector", () => {
    setup({ byok: false });
    renderWithIntl(<DefaultModelSetting />);
    expect(screen.getByText(copy.platformNote)).toBeTruthy();
    expect(screen.queryByRole("combobox")).toBeNull();
    expect(vi.mocked(useModels)).toHaveBeenCalledWith(false);
  });

  it("lists the catalog grouped by provider and saves a pick", () => {
    setup({ byok: true, models: MODELS });
    renderWithIntl(<DefaultModelSetting />);
    const select = screen.getByRole("combobox", { name: copy.label }) as HTMLSelectElement;
    expect(select.className).toContain("min-h-11");
    expect(select.value).toBe("");
    expect(screen.getByRole("option", { name: copy.auto })).toBeTruthy();
    expect(screen.getAllByRole("group").map((g) => g.getAttribute("label"))).toEqual([
      "Anthropic",
      "OpenAI",
    ]);
    fireEvent.change(select, { target: { value: "openai/gpt-5.4" } });
    expect(mutate).toHaveBeenCalledWith("openai/gpt-5.4");
  });

  it("shows the saved default, and choosing automatic clears it", () => {
    setup({
      byok: true,
      models: MODELS,
      saved: { data: { default_model: "openai/gpt-5.4" } },
    });
    renderWithIntl(<DefaultModelSetting />);
    const select = screen.getByRole("combobox", { name: copy.label }) as HTMLSelectElement;
    expect(select.value).toBe("openai/gpt-5.4");
    fireEvent.change(select, { target: { value: "" } });
    expect(mutate).toHaveBeenCalledWith(null);
  });

  it("is a skeleton while loading", () => {
    setup({ byok: true, models: { isLoading: true } });
    renderWithIntl(<DefaultModelSetting />);
    expect(screen.queryByRole("combobox")).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("shows an inline retry when the catalog fails", () => {
    const refetch = vi.fn();
    setup({ byok: true, models: { isError: true, error: new Error("x"), refetch } });
    renderWithIntl(<DefaultModelSetting />);
    fireEvent.click(screen.getByRole("button", { name: messages.market.cards.retry }));
    expect(refetch).toHaveBeenCalled();
  });

  it("explains an empty catalog for a BYOK caller", () => {
    setup({ byok: true, models: { data: { selectable: true, models: [] } } });
    renderWithIntl(<DefaultModelSetting />);
    expect(screen.getByText(copy.empty)).toBeTruthy();
    expect(screen.queryByRole("combobox")).toBeNull();
  });
});
