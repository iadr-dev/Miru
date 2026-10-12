"use client";

import {
  type UseMutationResult,
  type UseQueryResult,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { useDebouncedValue } from "@/hooks/use-debounced-value";
import {
  deleteScreenerScreen,
  getFactorCatalog,
  getScreenerPresets,
  listScreenerScreens,
  runScreener,
  type SaveScreenerScreenParams,
  saveScreenerScreen,
} from "@/lib/api/screener";
import { FRESHNESS } from "@/lib/freshness";
import type {
  FactorCatalog,
  RunScreenerResult,
  ScreenerGateOverrides,
  ScreenerMarket,
  ScreenerPreset,
  ScreenerRunCondition,
  ScreenerScreen,
} from "@/types/screener";

const DEBOUNCE_MS = 400;

/** Query keys mirror the backend's own key shape (`web/OVERVIEW.md` §3) — `(market, conditions,
 * gateOverrides)` for a run, `market` alone for factors/presets/screens. */
function factorsKey(market: ScreenerMarket) {
  return ["screener-factors", market] as const;
}
function presetsKey(market: ScreenerMarket) {
  return ["screener-presets", market] as const;
}
function screensKey(market: ScreenerMarket) {
  return ["screener-screens", market] as const;
}
function runKey(
  market: ScreenerMarket,
  conditions: ScreenerRunCondition[],
  match: "AND" | "OR",
  gateOverrides: ScreenerGateOverrides | undefined,
  countOnly: boolean,
) {
  return ["screener-run", market, conditions, match, gateOverrides ?? null, countOnly] as const;
}

/** The per-market factor/signal catalog the condition builder discovers from. Rarely changes —
 * a long `staleTime` avoids refetching on every tab switch. */
export function useFactorCatalog(market: ScreenerMarket): UseQueryResult<FactorCatalog> {
  return useQuery({
    queryKey: factorsKey(market),
    queryFn: () => getFactorCatalog(market),
    staleTime: FRESHNESS.reference,
  });
}

/** Named presets the `PresetLibrary` loads into the builder. */
export function useScreenerPresets(market: ScreenerMarket): UseQueryResult<ScreenerPreset[]> {
  return useQuery({
    queryKey: presetsKey(market),
    queryFn: () => getScreenerPresets(market),
    staleTime: FRESHNESS.reference,
  });
}

export interface UseRunScreenerParams {
  market: ScreenerMarket;
  conditions: ScreenerRunCondition[];
  match?: "AND" | "OR";
  gateOverrides?: ScreenerGateOverrides;
  countOnly?: boolean;
  enabled?: boolean;
}

/** Runs the gates->setup funnel, debounced against condition/gate churn (README.md §4's live
 * survivor-count probe fires on every builder edit — this hook is what keeps that from firing on
 * every keystroke). Empty `conditions` still runs (gates-only, matching the funnel's own "gates
 * apply even with zero setup conditions" semantics) unless `enabled` is explicitly false. */
export function useRunScreener(params: UseRunScreenerParams): UseQueryResult<RunScreenerResult> {
  const debouncedConditions = useDebouncedValue(params.conditions, DEBOUNCE_MS);
  const debouncedGateOverrides = useDebouncedValue(params.gateOverrides, DEBOUNCE_MS);
  const match = params.match ?? "AND";
  const countOnly = params.countOnly ?? false;

  return useQuery({
    queryKey: runKey(params.market, debouncedConditions, match, debouncedGateOverrides, countOnly),
    queryFn: () =>
      runScreener({
        market: params.market,
        conditions: debouncedConditions,
        match,
        gateOverrides: debouncedGateOverrides,
        countOnly,
      }),
    enabled: params.enabled ?? true,
  });
}

// ---------------------------------------------------------------------------
// Saved screens (README.md §6) — optimistic save/update/delete, same shape as
// `use-watchlists.ts`'s mutations.
// ---------------------------------------------------------------------------

/** What an optimistic delete snapshots so `onError` can roll it back. */
interface ScreensSnapshot {
  previous: ScreenerScreen[] | undefined;
}

export function useScreenerScreens(market: ScreenerMarket): UseQueryResult<ScreenerScreen[]> {
  return useQuery({
    queryKey: screensKey(market),
    queryFn: () => listScreenerScreens(market),
  });
}

function invalidateScreens(queryClient: ReturnType<typeof useQueryClient>, market: ScreenerMarket) {
  queryClient.invalidateQueries({ queryKey: screensKey(market) });
}

export function useSaveScreenerScreen(
  market: ScreenerMarket,
): UseMutationResult<ScreenerScreen, Error, SaveScreenerScreenParams> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (params: SaveScreenerScreenParams) => saveScreenerScreen(params),
    onSuccess: () => invalidateScreens(queryClient, market),
  });
}

export function useDeleteScreenerScreen(
  market: ScreenerMarket,
): UseMutationResult<void, Error, string, ScreensSnapshot> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (screenId: string) => deleteScreenerScreen(screenId),
    onMutate: async (screenId) => {
      await queryClient.cancelQueries({ queryKey: screensKey(market) });
      const previous = queryClient.getQueryData<ScreenerScreen[]>(screensKey(market));
      queryClient.setQueryData<ScreenerScreen[]>(screensKey(market), (current) =>
        (current ?? []).filter((screen) => screen.id !== screenId),
      );
      return { previous };
    },
    onError: (_error, _vars, context) => {
      if (context?.previous) {
        queryClient.setQueryData(screensKey(market), context.previous);
      }
    },
    onSettled: () => invalidateScreens(queryClient, market),
  });
}
