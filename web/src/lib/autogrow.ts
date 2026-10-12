/** Fraction of the viewport a composer may grow to before its own scrollbar takes over. */
const AUTOGROW_VIEWPORT_CAP = 0.45;

/**
 * Grow a textarea with its content up to ~45% of the viewport height (the Claude-style composer:
 * the box itself expands with no inner scrollbar until it is genuinely too tall to keep growing;
 * only past that cap does the textarea's own overflow kick in). Shared by both composers.
 */
export function autogrowTextarea(el: HTMLTextAreaElement): void {
  const cap = Math.round(window.innerHeight * AUTOGROW_VIEWPORT_CAP);
  el.style.height = "auto";
  el.style.height = `${Math.min(el.scrollHeight, cap)}px`;
  el.style.overflowY = el.scrollHeight > cap ? "auto" : "hidden";
}
