/**
 * Keyboard behaviour shared by every `role="tablist"` in the app (WAI-ARIA tabs pattern):
 *
 * - **Roving tab stop** — only the selected tab (or the first, when none is selected) is in the Tab
 *   sequence, so a keyboard user passes one stop per strip instead of one per tab.
 * - **Arrow keys** — Left/Right (flipped in RTL) move focus to the previous/next enabled tab,
 *   wrapping at the ends; Home/End jump to the first/last.
 * - **Manual activation** — moving focus does not select. Enter/Space (native on the buttons) do.
 *   Most strips fetch on select, so arrowing across them must not fire a request per tab.
 *
 * Plain DOM so one global enhancer (`components/shell/tab-strip-behavior.tsx`) covers every strip
 * without per-component wiring, and so it is testable without React.
 */

const TAB = '[role="tab"]';

/** A hidden strip (e.g. an `aria-hidden` placeholder) is not for keyboard users. */
function isHidden(strip: HTMLElement): boolean {
  return strip.closest('[aria-hidden="true"]') !== null;
}

function isDisabled(tab: HTMLElement): boolean {
  return tab.hasAttribute("disabled") || tab.getAttribute("aria-disabled") === "true";
}

/** The strip's tabs that can take focus, in DOM order. */
export function enabledTabs(strip: HTMLElement): HTMLElement[] {
  return [...strip.querySelectorAll<HTMLElement>(TAB)].filter(
    (tab) => tab.closest('[role="tablist"]') === strip && !isDisabled(tab),
  );
}

/** Which tab `key` moves focus to from `current`, or null when `key` is not a tab-navigation key. */
export function tabForKey(
  key: string,
  tabs: readonly HTMLElement[],
  current: number,
  rtl: boolean,
): HTMLElement | null {
  const count = tabs.length;
  if (count === 0 || current < 0) return null;
  const forward = rtl ? "ArrowLeft" : "ArrowRight";
  const backward = rtl ? "ArrowRight" : "ArrowLeft";
  if (key === forward) return tabs[(current + 1) % count] ?? null;
  if (key === backward) return tabs[(current - 1 + count) % count] ?? null;
  if (key === "Home") return tabs[0] ?? null;
  if (key === "End") return tabs[count - 1] ?? null;
  return null;
}

/** Makes the selected tab (else the first) the strip's one tab stop. */
export function syncRovingTabindex(strip: HTMLElement): void {
  if (isHidden(strip)) return;
  const tabs = enabledTabs(strip);
  const stop = tabs.find((tab) => tab.getAttribute("aria-selected") === "true") ?? tabs[0];
  for (const tab of tabs) {
    tab.setAttribute("tabindex", tab === stop ? "0" : "-1");
  }
}

/** Arrow/Home/End navigation. Returns the cleanup that removes the listener. */
export function enableTabKeyboard(strip: HTMLElement): () => void {
  const onKeyDown = (event: KeyboardEvent): void => {
    if (event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
    const origin = (event.target as HTMLElement).closest<HTMLElement>(TAB);
    if (!origin || origin.closest('[role="tablist"]') !== strip || isHidden(strip)) return;
    const tabs = enabledTabs(strip);
    const rtl = getComputedStyle(strip).direction === "rtl";
    const next = tabForKey(event.key, tabs, tabs.indexOf(origin), rtl);
    if (!next) return;
    event.preventDefault();
    next.focus();
  };
  strip.addEventListener("keydown", onKeyDown);
  return () => strip.removeEventListener("keydown", onKeyDown);
}
