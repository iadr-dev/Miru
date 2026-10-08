import type { Memory } from "@/types/settings";

/** Pure helpers for the memory settings: how a memory reads, how one is built from typed text, and
 * where it came from. A memory the user writes (or the agent proposes) is `{ text }`; older rows may
 * hold any JSON object, so reading falls back to showing its values rather than hiding them. */

const KEY_MAX_LENGTH = 40;

/** The memory as a person reads it: its `text`, else the string/number values of its content. */
export function memoryText(memory: Pick<Memory, "content">): string {
  const text = memory.content.text;
  if (typeof text === "string") {
    return text;
  }
  const parts = Object.entries(memory.content)
    .filter(([, value]) => typeof value === "string" || typeof value === "number")
    .map(([key, value]) => `${key}: ${String(value)}`);
  return parts.join("\n");
}

/** What is written back when a memory is edited. */
export function contentFromText(text: string): Record<string, unknown> {
  return { text: text.trim() };
}

/** A short name for a memory, taken from the start of its text (the user is never asked for one). */
export function keyFromText(text: string): string {
  const flat = text.replace(/\s+/g, " ").trim();
  return flat.length <= KEY_MAX_LENGTH ? flat : `${flat.slice(0, KEY_MAX_LENGTH)}…`;
}

/** The chat a memory was saved from (`thread:<id>`), or null for one the user typed in. */
export function sourceThreadId(memory: Pick<Memory, "source_ref">): string | null {
  const ref = memory.source_ref;
  return ref?.startsWith("thread:") ? ref.slice("thread:".length) || null : null;
}
