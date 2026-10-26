// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import type { ReactElement } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { WelcomePullDialog } from "@/components/pets/welcome-pull-dialog";
import { getPetProgress, listPetCatalog, pullPet } from "@/lib/api/settings";
import { messages } from "@/test/render";
import enMessages from "../../../messages/en.json";

vi.mock("@/lib/api/settings", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api/settings")>();
  return { ...actual, getPetProgress: vi.fn(), listPetCatalog: vi.fn(), pullPet: vi.fn() };
});

const tw = messages.settings.pets.welcome;
const KEY = "miru.pet.welcome-dismissed";

const PROGRESS = { owned_count: 0, available_pulls: 1 };
const CATALOG = [
  {
    pet_id: "cat",
    name: "Cat",
    name_zh: "小貓",
    rarity: "rare",
    description: "A calm cat",
    description_zh: "安靜的貓",
  },
];

function mount(locale: "zh-TW" | "en" = "zh-TW"): {
  client: QueryClient;
  container: HTMLElement;
} {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const ui: ReactElement = (
    <QueryClientProvider client={client}>
      <NextIntlClientProvider
        locale={locale}
        messages={locale === "en" ? enMessages : messages}
        timeZone="Asia/Taipei"
      >
        <WelcomePullDialog />
      </NextIntlClientProvider>
    </QueryClientProvider>
  );
  const { container } = render(ui);
  return { client, container };
}

/** Motion animates inline `style` (opacity/transform) between frames; drop it so the snapshot is stable. */
function stableHtml(container: HTMLElement): string {
  return container.innerHTML.replaceAll(/ style="[^"]*"/g, "");
}

function memoryStorage(): Storage {
  const store = new Map<string, string>();
  return {
    get length(): number {
      return store.size;
    },
    clear: () => store.clear(),
    getItem: (k: string) => store.get(k) ?? null,
    key: (i: number) => Array.from(store.keys())[i] ?? null,
    removeItem: (k: string) => void store.delete(k),
    setItem: (k: string, v: string) => void store.set(k, v),
  };
}

beforeEach(() => {
  Object.defineProperty(window, "localStorage", { value: memoryStorage(), configurable: true });
  vi.mocked(getPetProgress).mockResolvedValue(PROGRESS as never);
  vi.mocked(listPetCatalog).mockResolvedValue(CATALOG as never);
  vi.mocked(pullPet).mockResolvedValue({
    pet_id: "cat",
    rarity: "rare",
    is_duplicate: false,
    xp_awarded: 0,
    auto_equipped: true,
  });
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("WelcomePullDialog", () => {
  it("renders nothing while progress loads", () => {
    vi.mocked(getPetProgress).mockReturnValue(new Promise(() => undefined));
    mount();
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("renders nothing when the user already owns a pet or has no pull waiting", async () => {
    vi.mocked(getPetProgress).mockResolvedValue({ owned_count: 2, available_pulls: 1 } as never);
    mount();
    await waitFor(() => expect(getPetProgress).toHaveBeenCalled());
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByRole("dialog")).toBeNull();
    cleanup();
    vi.mocked(getPetProgress).mockResolvedValue({ owned_count: 0, available_pulls: 0 } as never);
    mount();
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("renders nothing once dismissed before", async () => {
    window.localStorage.setItem(KEY, "1");
    mount();
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("offers the free pull as an intro dialog", async () => {
    const { container } = mount();
    await screen.findByRole("dialog");
    expect(screen.getByRole("heading").textContent).toBe(tw.title);
    expect(screen.getByText(tw.body)).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(stableHtml(container)).toMatchSnapshot();
  });

  it("closing with 'later' remembers the dismissal", async () => {
    mount();
    await screen.findByRole("dialog");
    const laters = screen.getAllByRole("button", { name: tw.later, hidden: true });
    expect(laters).toHaveLength(2);
    fireEvent.click(laters[1] as HTMLElement);
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(window.localStorage.getItem(KEY)).toBe("1");
  });

  it("the backdrop closes and remembers", async () => {
    mount();
    await screen.findByRole("dialog");
    fireEvent.click(
      screen.getAllByRole("button", { name: tw.later, hidden: true })[0] as HTMLElement,
    );
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(window.localStorage.getItem(KEY)).toBe("1");
  });

  it("Escape closes and remembers", async () => {
    mount();
    await screen.findByRole("dialog");
    fireEvent.keyDown(document, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(window.localStorage.getItem(KEY)).toBe("1");
  });

  it("pulls: shows the pending label, then the reveal, and invalidates pet queries", async () => {
    let resolve: (v: unknown) => void = () => undefined;
    vi.mocked(pullPet).mockReturnValue(
      new Promise((r) => {
        resolve = r;
      }) as never,
    );
    const { client, container } = mount();
    await screen.findByRole("dialog");
    const invalidate = vi.spyOn(client, "invalidateQueries");
    fireEvent.click(screen.getByRole("button", { name: tw.pull }));
    const pending = await screen.findByRole("button", { name: tw.pulling });
    expect(pending.hasAttribute("disabled")).toBe(true);
    expect(pullPet).toHaveBeenCalledTimes(1);
    resolve({
      pet_id: "cat",
      rarity: "rare",
      is_duplicate: false,
      xp_awarded: 0,
      auto_equipped: true,
    });
    await screen.findByText(tw.equipped);
    expect(screen.getByRole("heading").textContent).toBe(tw.revealTitle.replace("{name}", "小貓"));
    expect(screen.getByText(messages.settings.pets.rarity.rare)).toBeTruthy();
    expect(screen.queryByText("安靜的貓")).toBeNull();
    expect(screen.queryByText("A calm cat")).toBeNull();
    expect(invalidate).toHaveBeenCalledTimes(3);
    expect(stableHtml(container)).toMatchSnapshot();
    // finishing the reveal closes WITHOUT recording a dismissal
    fireEvent.click(screen.getByRole("button", { name: tw.start }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(window.localStorage.getItem(KEY)).toBeNull();
  });

  it("reveals a duplicate with the xp conversion, and a pet missing from the catalog by id", async () => {
    vi.mocked(pullPet).mockResolvedValue({
      pet_id: "ghost",
      rarity: "common",
      is_duplicate: true,
      xp_awarded: 25,
      auto_equipped: false,
    });
    const { container } = mount();
    await screen.findByRole("dialog");
    fireEvent.click(screen.getByRole("button", { name: tw.pull }));
    await screen.findByText(tw.duplicate.replace("{xp}", "25"));
    expect(screen.getByRole("heading").textContent).toBe(tw.revealTitle.replace("{name}", "ghost"));
    expect(stableHtml(container)).toMatchSnapshot();
  });

  it("shows the catalog description outside zh-TW", async () => {
    mount("en");
    await screen.findByRole("dialog");
    fireEvent.click(screen.getByRole("button", { name: enMessages.settings.pets.welcome.pull }));
    await screen.findByText("A calm cat");
  });

  it("omits the description when the catalog entry is unknown (en)", async () => {
    vi.mocked(pullPet).mockResolvedValue({
      pet_id: "ghost",
      rarity: "common",
      is_duplicate: false,
      xp_awarded: 0,
      auto_equipped: false,
    });
    mount("en");
    await screen.findByRole("dialog");
    fireEvent.click(screen.getByRole("button", { name: enMessages.settings.pets.welcome.pull }));
    await screen.findByText(enMessages.settings.pets.welcome.equipped);
    expect(screen.queryByText("A calm cat")).toBeNull();
  });

  it("shows an alert on failure and clears it when retrying", async () => {
    vi.mocked(pullPet).mockRejectedValueOnce(new Error("boom"));
    const { container } = mount();
    await screen.findByRole("dialog");
    fireEvent.click(screen.getByRole("button", { name: tw.pull }));
    await screen.findByRole("alert");
    expect(screen.getByRole("alert").textContent).toBe(tw.error);
    expect(stableHtml(container)).toMatchSnapshot();
    fireEvent.click(screen.getByRole("button", { name: tw.pull }));
    await screen.findByText(tw.equipped);
    expect(screen.queryByRole("alert")).toBeNull();
    expect(pullPet).toHaveBeenCalledTimes(2);
  });

  it("Enter outside a button pulls, and does not pull twice while pending", async () => {
    vi.mocked(pullPet).mockReturnValue(new Promise(() => undefined));
    mount();
    await screen.findByRole("dialog");
    (document.activeElement as HTMLElement | null)?.blur();
    fireEvent.keyDown(document, { key: "Enter" });
    await screen.findByRole("button", { name: tw.pulling });
    fireEvent.keyDown(document, { key: "Enter" });
    expect(pullPet).toHaveBeenCalledTimes(1);
  });

  it("Enter during the reveal finishes it", async () => {
    mount();
    await screen.findByRole("dialog");
    fireEvent.click(screen.getByRole("button", { name: tw.pull }));
    await screen.findByText(tw.equipped);
    (document.activeElement as HTMLElement | null)?.blur();
    fireEvent.keyDown(document, { key: "Enter" });
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(window.localStorage.getItem(KEY)).toBeNull();
  });
});
