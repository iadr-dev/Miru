"use client";

import { useEffect } from "react";
import { enableTabKeyboard, syncRovingTabindex } from "@/lib/tab-strip";

const FADE_ATTR_START = "data-fade-start";
const FADE_ATTR_END = "data-fade-end";
const DRAG_THRESHOLD_PX = 5;

/** Mirrors a strip's scroll position into `data-fade-start/end` so globals.css can fade whichever
 * edge still has hidden tabs - the cue that the row scrolls sideways. */
function syncFades(strip: HTMLElement): void {
  const max = strip.scrollWidth - strip.clientWidth;
  const overflowing = max > 2;
  strip.toggleAttribute(FADE_ATTR_START, overflowing && strip.scrollLeft > 2);
  strip.toggleAttribute(FADE_ATTR_END, overflowing && strip.scrollLeft < max - 2);
}

function revealSelected(strip: HTMLElement): void {
  const selected = strip.querySelector<HTMLElement>('[role="tab"][aria-selected="true"]');
  if (selected && strip.scrollWidth > strip.clientWidth) {
    const left = selected.offsetLeft - (strip.clientWidth - selected.offsetWidth) / 2;
    strip.scrollTo({ left: Math.max(0, left), behavior: "smooth" });
  }
}

/** Mouse drag-to-scroll (touch and trackpads already scroll natively). A drag past the threshold
 * swallows the click that would otherwise select the tab under the cursor. */
function enableDrag(strip: HTMLElement): void {
  let startX = 0;
  let startLeft = 0;
  let dragged = false;
  strip.addEventListener("pointerdown", (event) => {
    if (event.pointerType !== "mouse" || strip.scrollWidth <= strip.clientWidth) return;
    startX = event.clientX;
    startLeft = strip.scrollLeft;
    dragged = false;
    const onMove = (move: PointerEvent): void => {
      const delta = move.clientX - startX;
      if (Math.abs(delta) > DRAG_THRESHOLD_PX) dragged = true;
      if (dragged) strip.scrollLeft = startLeft - delta;
    };
    const onUp = (): void => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
  });
  strip.addEventListener(
    "click",
    (event) => {
      if (dragged) {
        event.stopPropagation();
        event.preventDefault();
        dragged = false;
      }
    },
    true,
  );
}

/** Makes every `role="tablist"` in the app behave like one component: a scrollable strip (edge
 * fades while tabs are hidden, the selected tab scrolled into view, mouse drag) and a keyboard
 * strip (one tab stop on the selected tab, arrow/Home/End to move between tabs - `lib/tab-strip`).
 * Mounted once in the shell; a MutationObserver picks up strips as pages render and keeps the tab
 * stop on the selected tab. No per-strip wiring needed. */
export function TabStripBehavior(): null {
  useEffect(() => {
    const wired = new WeakSet<HTMLElement>();
    const resize = new ResizeObserver((entries) => {
      for (const entry of entries) syncFades(entry.target as HTMLElement);
    });

    const wire = (strip: HTMLElement): void => {
      if (wired.has(strip)) return;
      wired.add(strip);
      strip.addEventListener("scroll", () => syncFades(strip), { passive: true });
      enableDrag(strip);
      enableTabKeyboard(strip); // listener lives on the strip, so it goes with it
      resize.observe(strip);
      syncFades(strip);
      revealSelected(strip);
    };

    const scan = (): void => {
      for (const strip of document.querySelectorAll<HTMLElement>('[role="tablist"]')) {
        wire(strip);
        syncFades(strip);
        syncRovingTabindex(strip);
      }
    };

    // Keep the selected tab in view when the selection changes.
    const onClick = (event: Event): void => {
      const strip = (event.target as HTMLElement).closest<HTMLElement>('[role="tablist"]');
      if (strip) window.requestAnimationFrame(() => revealSelected(strip));
    };

    scan();
    const mutations = new MutationObserver(scan);
    mutations.observe(document.body, {
      childList: true,
      subtree: true,
      attributes: true,
      attributeFilter: ["aria-selected", "disabled", "aria-disabled", "aria-hidden"],
    });
    document.addEventListener("click", onClick);
    return () => {
      mutations.disconnect();
      resize.disconnect();
      document.removeEventListener("click", onClick);
    };
  }, []);

  return null;
}
