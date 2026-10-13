// @vitest-environment jsdom
import { afterEach, describe, expect, it } from "vitest";
import { enabledTabs, enableTabKeyboard, syncRovingTabindex, tabForKey } from "@/lib/tab-strip";

afterEach(() => {
  document.body.innerHTML = "";
});

interface TabSpec {
  label: string;
  selected?: boolean;
  disabled?: "attr" | "aria";
}

function strip(tabs: TabSpec[], attrs = ""): HTMLElement {
  document.body.innerHTML = `<div role="tablist" ${attrs}>${tabs
    .map(
      (t) =>
        `<button type="button" role="tab" aria-selected="${t.selected ? "true" : "false"}"${
          t.disabled === "attr" ? " disabled" : ""
        }${t.disabled === "aria" ? ' aria-disabled="true"' : ""}>${t.label}</button>`,
    )
    .join("")}</div>`;
  return document.querySelector<HTMLElement>('[role="tablist"]') as HTMLElement;
}

function press(target: HTMLElement, key: string, init: KeyboardEventInit = {}): KeyboardEvent {
  const event = new KeyboardEvent("keydown", { key, bubbles: true, cancelable: true, ...init });
  target.dispatchEvent(event);
  return event;
}

const labels = (tabs: HTMLElement[]): string[] => tabs.map((t) => t.textContent ?? "");

describe("enabledTabs", () => {
  it("skips disabled tabs, by attribute and by aria-disabled", () => {
    const s = strip([
      { label: "a" },
      { label: "b", disabled: "attr" },
      { label: "c", disabled: "aria" },
      { label: "d" },
    ]);
    expect(labels(enabledTabs(s))).toEqual(["a", "d"]);
  });
});

describe("tabForKey", () => {
  const tabs = ["a", "b", "c"].map((label) => {
    const el = document.createElement("button");
    el.textContent = label;
    return el;
  });

  it("moves forward and backward, wrapping at the ends", () => {
    expect(tabForKey("ArrowRight", tabs, 0, false)?.textContent).toBe("b");
    expect(tabForKey("ArrowRight", tabs, 2, false)?.textContent).toBe("a");
    expect(tabForKey("ArrowLeft", tabs, 0, false)?.textContent).toBe("c");
    expect(tabForKey("ArrowLeft", tabs, 2, false)?.textContent).toBe("b");
  });

  it("flips the arrows in a right-to-left strip", () => {
    expect(tabForKey("ArrowRight", tabs, 1, true)?.textContent).toBe("a");
    expect(tabForKey("ArrowLeft", tabs, 1, true)?.textContent).toBe("c");
  });

  it("jumps to the ends with Home/End and ignores other keys", () => {
    expect(tabForKey("Home", tabs, 2, false)?.textContent).toBe("a");
    expect(tabForKey("End", tabs, 0, false)?.textContent).toBe("c");
    expect(tabForKey("ArrowDown", tabs, 0, false)).toBeNull();
    expect(tabForKey("Enter", tabs, 0, false)).toBeNull();
  });

  it("does nothing without tabs or from an unknown origin", () => {
    expect(tabForKey("ArrowRight", [], 0, false)).toBeNull();
    expect(tabForKey("ArrowRight", tabs, -1, false)).toBeNull();
  });
});

describe("syncRovingTabindex", () => {
  it("makes the selected tab the only tab stop", () => {
    const s = strip([{ label: "a" }, { label: "b", selected: true }, { label: "c" }]);
    syncRovingTabindex(s);
    expect([...s.children].map((c) => c.getAttribute("tabindex"))).toEqual(["-1", "0", "-1"]);
  });

  it("falls back to the first tab when none is selected", () => {
    const s = strip([{ label: "a" }, { label: "b" }]);
    syncRovingTabindex(s);
    expect([...s.children].map((c) => c.getAttribute("tabindex"))).toEqual(["0", "-1"]);
  });

  it("follows the selection when it changes", () => {
    const s = strip([{ label: "a", selected: true }, { label: "b" }]);
    syncRovingTabindex(s);
    s.children[0]?.setAttribute("aria-selected", "false");
    s.children[1]?.setAttribute("aria-selected", "true");
    syncRovingTabindex(s);
    expect([...s.children].map((c) => c.getAttribute("tabindex"))).toEqual(["-1", "0"]);
  });

  it("never picks a disabled tab as the stop", () => {
    const s = strip([{ label: "a", selected: true, disabled: "attr" }, { label: "b" }]);
    syncRovingTabindex(s);
    expect([...s.children].map((c) => c.getAttribute("tabindex"))).toEqual([null, "0"]);
  });

  it("leaves a hidden strip alone", () => {
    const s = strip([{ label: "a" }, { label: "b" }], 'aria-hidden="true"');
    syncRovingTabindex(s);
    expect([...s.children].map((c) => c.getAttribute("tabindex"))).toEqual([null, null]);
  });
});

describe("enableTabKeyboard", () => {
  it("moves focus with the arrow keys and stops the page from scrolling", () => {
    const s = strip([{ label: "a" }, { label: "b" }, { label: "c" }]);
    enableTabKeyboard(s);
    const [a, b, c] = [...s.children] as HTMLElement[];
    a?.focus();
    const event = press(a as HTMLElement, "ArrowRight");
    expect(document.activeElement).toBe(b);
    expect(event.defaultPrevented).toBe(true);
    press(b as HTMLElement, "End");
    expect(document.activeElement).toBe(c);
    press(c as HTMLElement, "ArrowRight");
    expect(document.activeElement).toBe(a);
  });

  it("moves focus without selecting (activation stays on Enter/Space)", () => {
    const s = strip([{ label: "a", selected: true }, { label: "b" }]);
    enableTabKeyboard(s);
    const [a, b] = [...s.children] as HTMLElement[];
    a?.focus();
    press(a as HTMLElement, "ArrowRight");
    expect(document.activeElement).toBe(b);
    expect(a?.getAttribute("aria-selected")).toBe("true");
    expect(b?.getAttribute("aria-selected")).toBe("false");
  });

  it("skips disabled tabs", () => {
    const s = strip([{ label: "a" }, { label: "b", disabled: "attr" }, { label: "c" }]);
    enableTabKeyboard(s);
    const [a, , c] = [...s.children] as HTMLElement[];
    a?.focus();
    press(a as HTMLElement, "ArrowRight");
    expect(document.activeElement).toBe(c);
  });

  it("ignores keys with modifiers and non-navigation keys", () => {
    const s = strip([{ label: "a" }, { label: "b" }]);
    enableTabKeyboard(s);
    const [a] = [...s.children] as HTMLElement[];
    a?.focus();
    expect(press(a as HTMLElement, "ArrowRight", { altKey: true }).defaultPrevented).toBe(false);
    expect(press(a as HTMLElement, "ArrowRight", { shiftKey: true }).defaultPrevented).toBe(false);
    expect(press(a as HTMLElement, "ArrowDown").defaultPrevented).toBe(false);
    expect(document.activeElement).toBe(a);
  });

  it("ignores events from a hidden strip and from non-tab targets", () => {
    const hidden = strip([{ label: "a" }, { label: "b" }], 'aria-hidden="true"');
    enableTabKeyboard(hidden);
    const [a] = [...hidden.children] as HTMLElement[];
    a?.focus();
    expect(press(a as HTMLElement, "ArrowRight").defaultPrevented).toBe(false);

    const s = strip([{ label: "a" }, { label: "b" }]);
    enableTabKeyboard(s);
    expect(press(s, "ArrowRight").defaultPrevented).toBe(false);
  });

  it("stops listening after cleanup", () => {
    const s = strip([{ label: "a" }, { label: "b" }]);
    const cleanup = enableTabKeyboard(s);
    cleanup();
    const [a] = [...s.children] as HTMLElement[];
    a?.focus();
    expect(press(a as HTMLElement, "ArrowRight").defaultPrevented).toBe(false);
  });

  it("does not take over a nested strip's keys", () => {
    document.body.innerHTML = `<div role="tablist" id="outer"><button role="tab" aria-selected="true">o1</button><div role="tablist" id="inner"><button role="tab" aria-selected="true">i1</button><button role="tab" aria-selected="false">i2</button></div></div>`;
    const outer = document.getElementById("outer") as HTMLElement;
    const inner = document.getElementById("inner") as HTMLElement;
    enableTabKeyboard(outer);
    enableTabKeyboard(inner);
    expect(labels(enabledTabs(outer))).toEqual(["o1"]);
    const i1 = inner.children[0] as HTMLElement;
    i1.focus();
    press(i1, "ArrowRight");
    expect(document.activeElement).toBe(inner.children[1]);
  });
});
