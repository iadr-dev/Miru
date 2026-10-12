// @vitest-environment jsdom
import { cleanup, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { SignedChange } from "@/components/ui/signed-change";
import { messages, renderWithIntl } from "@/test/render";

afterEach(cleanup);

const words = messages.common.direction;

describe("SignedChange", () => {
  it("renders a rise red with ▲ and says the direction in its label", () => {
    renderWithIntl(<SignedChange value={1.45} decimals={2} unit="%" />);
    const cell = screen.getByRole("img");
    expect(cell.textContent).toBe("▲ 1.45%");
    expect(cell.style.color).toBe("var(--up)");
    expect(cell.getAttribute("aria-label")).toBe(`${words.up} 1.45%`);
  });

  it("renders a fall green with ▼", () => {
    renderWithIntl(<SignedChange value={-0.8} decimals={2} unit="%" />);
    const cell = screen.getByRole("img");
    expect(cell.textContent).toBe("▼ 0.80%");
    expect(cell.style.color).toBe("var(--down)");
    expect(cell.getAttribute("aria-label")).toBe(`${words.down} 0.80%`);
  });

  it("renders zero (and a value that rounds to zero) as flat, not red", () => {
    renderWithIntl(<SignedChange value={0.004} decimals={2} unit="%" />);
    const cell = screen.getByRole("img");
    expect(cell.textContent?.startsWith("–")).toBe(true);
    expect(cell.style.color).toBe("var(--text-muted)");
    expect(cell.getAttribute("aria-label")?.startsWith(words.flat)).toBe(true);
  });

  it("renders a missing value as a dash with no direction", () => {
    const { container } = renderWithIntl(<SignedChange value={null} />);
    expect(container.textContent).toBe("—");
    expect(screen.queryByRole("img")).toBeNull();
  });

  it("hides itself from assistive tech when the row already carries the label", () => {
    const { container } = renderWithIntl(<SignedChange value={2} labelled={false} />);
    const cell = container.querySelector("span");
    expect(cell?.getAttribute("aria-hidden")).toBe("true");
    expect(cell?.hasAttribute("aria-label")).toBe(false);
  });
});
