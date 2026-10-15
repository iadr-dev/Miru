"use client";

import { shortModelName } from "@/lib/usage-format";

/** A model catalog entry offered by the model-selector dropdown. */
export type ModelOption = {
  id: string;
  label: string;
};

/** A provider category group in the model-selector dropdown, keyed to the providers this
 * backend's `src/llm/registry.py` has real `LLMClient` implementations for. */
type ModelCategory = {
  key: "anthropic" | "openai" | "gemini" | "openrouter" | "grok" | "deepseek" | "ollama";
  models: ModelOption[];
};

// Hardcoded UI catalog — not fetched from anywhere. Every `id` is a full `"provider/model"`
// string matching `backend/src/llm/registry.py`'s own format (the same shape its
// `AITW_MODEL_ROLE_<ROLE>` env-var overrides use), since selecting an item here sends this exact
// string as `Thread.model_override` via `PATCH /threads/{id}` — see `ModelMenu` below. Every
// provider/model id was live-verified against its provider's real model-list endpoint + a real
// chat() call through this backend (see session notes) — entries that didn't resolve to a real,
// chat-callable model (a fabricated "Claude Sonnet 4.8", a non-chat "gpt-5.4-pro" completions-only
// model, "gpt-oss-20b" which OpenAI's own API doesn't serve, a bare "gemini-3.1" missing its real
// "-pro-preview" suffix, and OpenRouter's DeepSeek/Grok ids that didn't match any real listing)
// were corrected to ids that actually work.
export const MODEL_CATEGORIES: ModelCategory[] = [
  {
    key: "anthropic",
    models: [
      { id: "anthropic/claude-haiku-5-5", label: "Claude Haiku 5.5" },
      { id: "anthropic/claude-sonnet-5", label: "Claude Sonnet 5" },
      { id: "anthropic/claude-sonnet-5-5", label: "Claude Sonnet 5.5" },
      { id: "anthropic/claude-opus-4-6", label: "Claude Opus 4.6" },
      { id: "anthropic/claude-opus-4-8", label: "Claude Opus 4.8" },
      { id: "anthropic/claude-opus-5-5", label: "Claude Opus 5.5" },
    ],
  },
  {
    key: "openai",
    models: [
      { id: "openai/gpt-4o-mini", label: "GPT-4o mini" },
      { id: "openai/gpt-5.4", label: "GPT-5.4" },
      { id: "openai/gpt-5.4-mini", label: "GPT-5.4 mini" },
      // "gpt-5.6" bare doesn't exist — only the "-sol"/"-terra"/"-luna" variants do (live-verified);
      // luna was the fastest of the three in a head-to-head latency check with near-identical
      // reasoning depth, so it represents "GPT-5.6" here.
      { id: "openai/gpt-5.6-luna", label: "GPT-5.6" },
    ],
  },
  {
    key: "gemini",
    models: [
      { id: "gemini/gemini-3.1-pro-preview", label: "Gemini 3.1 Pro" },
      { id: "gemini/gemini-3.5-flash-lite", label: "Gemini 3.5 Flash-Lite" },
      { id: "gemini/gemini-3.5-flash", label: "Gemini 3.5 Flash" },
      { id: "gemini/gemini-3.6-flash", label: "Gemini 3.6 Flash" },
    ],
  },
  {
    key: "openrouter",
    models: [
      { id: "openrouter/openrouter/auto", label: "openrouter/auto" },
      { id: "openrouter/meta-llama/llama-3.3-70b-instruct", label: "Llama 3.3 70B" },
    ],
  },
  // Grok and DeepSeek are reached through OpenRouter too (ids are `openrouter/<vendor>/<model>`);
  // they get their own groups because each vendor now has enough models to crowd the one list.
  {
    key: "grok",
    models: [
      { id: "openrouter/~x-ai/grok-latest", label: "Grok latest" },
      { id: "openrouter/x-ai/grok-4.7", label: "Grok 4.7" },
      { id: "openrouter/x-ai/grok-4.6", label: "Grok 4.6" },
      { id: "openrouter/x-ai/grok-4.5", label: "Grok 4.5" },
      { id: "openrouter/x-ai/grok-4.20", label: "Grok 4.20" },
    ],
  },
  {
    key: "deepseek",
    models: [
      { id: "openrouter/deepseek/deepseek-chat-v3.1", label: "DeepSeek V3.1" },
      { id: "openrouter/deepseek/deepseek-v4-flash", label: "DeepSeek V4 Flash" },
      { id: "openrouter/deepseek/deepseek-v4-pro", label: "DeepSeek V4 Pro" },
    ],
  },
  {
    key: "ollama",
    models: [{ id: "ollama/qwen3.6:latest", label: "Qwen 3.6:latest" }],
  },
];

/** Resolves a `model_override` string (as stored on `Thread`) to its catalog entry, or `null`
 * when unset — the caller renders `null` as the translated "server default" label rather than a
 * hardcoded English one, and never as "Auto" (that string names a real, distinct, selectable
 * OpenRouter catalog entry — `openrouter/auto` — so it can't also mean "no override chosen"
 * without the two being confusable in the same menu). A thread overridden to a model this build's
 * catalog doesn't (yet) list still shows a real trigger label via the raw id rather than
 * silently falling back to the default, which would misrepresent what the thread is actually
 * configured to use. */
/** The provider half of a catalog id ("openrouter/x-ai/grok-4.7" -> "openrouter"). */
export function modelProvider(modelId: string): string {
  return modelId.split("/")[0] ?? "";
}

/** The provider the backend uses when a thread has picked no model (its AGENT role default). */
export const DEFAULT_MODEL_PROVIDER = "anthropic";

/** The first catalog model served by any of `providers` - the model to switch to when the current
 * one belongs to a provider the caller has no key for. */
export function firstModelFor(providers: string[]): string | null {
  for (const category of MODEL_CATEGORIES) {
    const match = category.models.find((m) => providers.includes(modelProvider(m.id)));
    if (match) {
      return match.id;
    }
  }
  return null;
}

export function modelOptionFor(modelOverride: string | null | undefined): ModelOption | null {
  if (!modelOverride) {
    return null;
  }
  for (const category of MODEL_CATEGORIES) {
    const match = category.models.find((model) => model.id === modelOverride);
    if (match) {
      return match;
    }
  }
  // Not in the catalog (a model picked before it was retired from the list): name it as the usage
  // views do, rather than showing the raw id.
  const [provider = "", ...rest] = modelOverride.split("/");
  return { id: modelOverride, label: shortModelName(provider, rest.join("/") || provider) };
}
