"use client";

import {
  type UseMutationResult,
  type UseQueryResult,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { toast } from "@/components/ui/toast";
import { fetchMe } from "@/lib/api/account";
import { getModelCatalog, getModelDefault, putModelDefault } from "@/lib/api/models";
import { queryKeys } from "@/lib/api/query-keys";
import { FRESHNESS } from "@/lib/freshness";
import type { ModelCatalog, ModelDefault } from "@/types/models";

/** True once `/me` says the caller runs on their own keys; `undefined` while that is unknown.
 * Everything model-related (the chat picker, the default-model setting) gates on this. */
export function useByokActive(): boolean | undefined {
  const me = useQuery({ queryKey: queryKeys.me, queryFn: ({ signal }) => fetchMe(signal) });
  return me.data?.entitlements.byok_active;
}

/** `GET /models`. Only fetched for a BYOK caller (`enabled`): the platform picks for everyone
 * else, so there is nothing to list. */
export function useModels(enabled: boolean): UseQueryResult<ModelCatalog> {
  return useQuery({
    queryKey: queryKeys.models,
    queryFn: ({ signal }) => getModelCatalog(signal),
    staleTime: FRESHNESS.reference,
    enabled,
  });
}

/** `GET /me/model-default` - the saved default model, or null when none is saved. */
export function useModelDefault(enabled: boolean): UseQueryResult<ModelDefault> {
  return useQuery({
    queryKey: queryKeys.modelDefault,
    queryFn: ({ signal }) => getModelDefault(signal),
    staleTime: FRESHNESS.reference,
    enabled,
  });
}

type DefaultSnapshot = { previous: ModelDefault | undefined };

/** `PUT /me/model-default` applied optimistically; a refusal snaps the selector back and toasts.
 * `null` clears the saved default. */
export function useSetModelDefault(): UseMutationResult<
  ModelDefault,
  Error,
  string | null,
  DefaultSnapshot
> {
  const t = useTranslations("settings.byok.defaultModel");
  const queryClient = useQueryClient();
  return useMutation<ModelDefault, Error, string | null, DefaultSnapshot>({
    mutationFn: (modelId) => putModelDefault(modelId),
    onMutate: async (modelId) => {
      await queryClient.cancelQueries({ queryKey: queryKeys.modelDefault });
      const previous = queryClient.getQueryData<ModelDefault>(queryKeys.modelDefault);
      queryClient.setQueryData<ModelDefault>(queryKeys.modelDefault, { default_model: modelId });
      return { previous };
    },
    onError: (_error, _modelId, context) => {
      if (context?.previous !== undefined) {
        queryClient.setQueryData(queryKeys.modelDefault, context.previous);
      }
      toast.error(t("saveFailed"));
    },
    onSettled: () => {
      // The catalog's `is_default` flag and `/me` both follow the saved default.
      void queryClient.invalidateQueries({ queryKey: queryKeys.modelDefault });
      void queryClient.invalidateQueries({ queryKey: queryKeys.models });
      void queryClient.invalidateQueries({ queryKey: queryKeys.me });
    },
  });
}
