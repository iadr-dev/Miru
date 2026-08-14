/** Strips Markdown syntax down to plain spoken-prose text, for `SpeakButton`
 * (`active-turn-view.tsx`) to pass into `speakText` instead of the raw `turn.answerText` markdown
 * string. Hand-rolled rather than a dependency (`remove-markdown` was evaluated — it leaves GFM
 * table pipes/rows completely untouched, which is exactly the "%%"/"|" literal-syntax-spoken-aloud
 * bug this fixes) — small, scoped, and unit-tested below/at the sibling `.test.ts`.
 *
 * Order matters: code fences are removed whole (their content isn't natural prose to read aloud)
 * before line-oriented rules run, then table rows are turned into comma-separated cell reads
 * before the generic pipe/emphasis/link stripping happens.
 */
export function markdownToSpeech(markdown: string): string {
  let text = markdown;

  // Fenced code blocks (```...```) — drop entirely; code isn't natural speech.
  text = text.replace(/```[\s\S]*?```/g, "");
  // Horizontal rules (---, ***, ___ on their own line) — before list-marker stripping, since
  // "---" would otherwise be read as a stray dash list item.
  text = text.replace(/^[ \t]*([-*_])(?:[ \t]*\1){2,}[ \t]*$/gm, "");

  const lines = text.split("\n");
  const outLines: string[] = [];
  for (const rawLine of lines) {
    let line = rawLine;

    // GFM table rows: pipe-delimited, optionally leading/trailing "|". Skip the
    // header-separator row (e.g. "|---|---|") entirely; turn other rows into
    // comma-separated cell text instead of reading raw "|" characters.
    const trimmed = line.trim();
    if (/^\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)+\|?$/.test(trimmed)) {
      continue;
    }
    if (trimmed.includes("|") && /^\|.*\|$/.test(trimmed)) {
      const cells = trimmed
        .slice(1, -1)
        .split("|")
        .map((cell) => cell.trim())
        .filter((cell) => cell.length > 0);
      line = cells.join(", ");
    }

    // Headers: leading "#".."######" markers.
    line = line.replace(/^\s{0,3}#{1,6}\s+/, "");
    // Blockquote markers: leading ">" (possibly nested "> >").
    line = line.replace(/^\s{0,3}(>\s?)+/, "");
    // List markers: "-"/"*"/"+" bullets or "1." ordered markers.
    line = line.replace(/^\s*([-*+]|\d+\.)\s+/, "");

    outLines.push(line);
  }
  text = outLines.join("\n");

  // Inline code spans: `code` -> code.
  text = text.replace(/`([^`]+)`/g, "$1");
  // Images: ![alt](url) -> alt.
  text = text.replace(/!\[([^\]]*)\]\([^)]*\)/g, "$1");
  // Links: [text](url) -> text.
  text = text.replace(/\[([^\]]*)\]\([^)]*\)/g, "$1");
  // Bold+italic (*** / ___), then bold (** / __), then italic (* / _).
  text = text.replace(/(\*{3}|_{3})([^*_]+?)\1/g, "$2");
  text = text.replace(/(\*{2}|_{2})([^*_]+?)\1/g, "$2");
  text = text.replace(/(\*|_)([^*_]+?)\1/g, "$2");
  // Strikethrough.
  text = text.replace(/~~([^~]+?)~~/g, "$1");

  // Collapse runs of 3+ blank lines down to a double newline, and trim.
  text = text.replace(/\n{3,}/g, "\n\n").trim();

  return text;
}
