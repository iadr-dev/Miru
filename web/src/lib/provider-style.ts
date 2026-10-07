/** How each LLM provider is drawn (badge + chart series). Colours are the categorical chart
 * tokens, never a literal hex, and each known provider keeps the same one on every surface. */

export type ProviderStyle = { label: string; color: string };

const KNOWN: Record<string, ProviderStyle> = {
  anthropic: { label: "Anthropic", color: "var(--chart-1)" },
  openai: { label: "OpenAI", color: "var(--chart-2)" },
  gemini: { label: "Gemini", color: "var(--chart-3)" },
  openrouter: { label: "OpenRouter", color: "var(--chart-4)" },
  groq: { label: "Groq", color: "var(--chart-5)" },
  cerebras: { label: "Cerebras", color: "var(--chart-6)" },
  ollama: { label: "Ollama", color: "var(--text-muted)" },
};

const FALLBACK_COLORS = [
  "var(--chart-1)",
  "var(--chart-2)",
  "var(--chart-3)",
  "var(--chart-4)",
  "var(--chart-5)",
  "var(--chart-6)",
];

/** A stable (same input, same colour) pick for a provider this table has not heard of. */
function fallbackColor(provider: string): string {
  let hash = 0;
  for (const char of provider) {
    hash = (hash * 31 + char.charCodeAt(0)) % 997;
  }
  return FALLBACK_COLORS[hash % FALLBACK_COLORS.length] ?? "var(--chart-1)";
}

export function providerStyle(provider: string): ProviderStyle {
  const known = KNOWN[provider.toLowerCase()];
  if (known) {
    return known;
  }
  return { label: provider || "—", color: fallbackColor(provider) };
}
