import { shortModelName } from "@/lib/usage-format";
import type { ModelOption } from "@/types/models";

/** A provider's models, in the order the catalog lists them. */
export type ModelGroup = { provider: ModelOption["provider"]; models: ModelOption[] };

/** A model's label in the reader's language (`zh-TW` -> `label_zh`, everything else -> `label_en`). */
export function modelLabel(model: ModelOption, locale: string): string {
  return locale.toLowerCase().startsWith("zh") ? model.label_zh : model.label_en;
}

/** Groups the catalog by provider, keeping the order providers first appear in. */
export function groupByProvider(models: ModelOption[]): ModelGroup[] {
  const groups: ModelGroup[] = [];
  for (const model of models) {
    const group = groups.find((g) => g.provider === model.provider);
    if (group) {
      group.models.push(model);
    } else {
      groups.push({ provider: model.provider, models: [model] });
    }
  }
  return groups;
}

/** The model a chat actually runs on with no pick of its own: the catalog's `is_default` entry. */
export function defaultModel(models: ModelOption[]): ModelOption | null {
  return models.find((m) => m.is_default) ?? null;
}

/** How a picked model id reads on screen. A model the catalog no longer lists (a thread picked it
 * before it was retired) is named as the usage views do rather than by its raw id. */
export function labelForModelId(modelId: string, models: ModelOption[], locale: string): string {
  const match = models.find((m) => m.id === modelId);
  if (match) {
    return modelLabel(match, locale);
  }
  const [provider = "", ...rest] = modelId.split("/");
  return shortModelName(provider, rest.join("/") || provider);
}
