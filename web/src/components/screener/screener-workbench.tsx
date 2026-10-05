"use client";

import { useTranslations } from "next-intl";
import { type ReactNode, useMemo, useState } from "react";
import { ConditionBuilder } from "@/components/screener/condition-builder";
import { FunnelMatchRate } from "@/components/screener/funnel-match-rate";
import { FunnelMeter } from "@/components/screener/funnel-meter";
import { GateBar } from "@/components/screener/gate-bar";
import { MarketTabs, type ScreenerMode } from "@/components/screener/market-tabs";
import { PresetLibrary } from "@/components/screener/preset-library";
import { SavedScreens } from "@/components/screener/saved-screens";
import { ScreenerEmptyState } from "@/components/screener/screener-empty-state";
import { ScreenerError } from "@/components/screener/screener-error";
import { ScreenerLoading } from "@/components/screener/screener-loading";
import { SCREENER_MARKET_CONFIGS } from "@/components/screener/screener-market-config";
import { ScreenerTable } from "@/components/screener/screener-table";
import { ThemeCards } from "@/components/screener/theme-cards";
import { useFactorCatalog, useRunScreener, useScreenerPresets } from "@/hooks/use-screener";
import type {
  ScreenerGateOverrides,
  ScreenerMarket,
  ScreenerRunCondition,
  ScreenerScreen,
} from "@/types/screener";
import { isFunnelUniverseKnown } from "@/types/screener";

const DEFAULT_GATES: ScreenerGateOverrides = {
  exclude_disposition: true,
  exclude_attention: true,
  exclude_full_cash_delivery: true,
};

/** The screener funnel workbench: market tabs -> FunnelMeter ->
 * GateBar -> builder rail + results table. Client component — the whole surface is interactive
 * (live re-probe on every condition/gate edit), so unlike a typical RSC-first page this pushes
 * `'use client'` at the workbench root rather than a deep leaf (justified: nearly every child here
 * is stateful, an RSC shell around it would buy nothing). */
export function ScreenerWorkbench(): ReactNode {
  const t = useTranslations("screener");
  const [market, setMarket] = useState<ScreenerMarket>("TW");
  const [mode, setMode] = useState<ScreenerMode>("screen");
  const [conditions, setConditions] = useState<ScreenerRunCondition[]>([]);
  const [selectedTags, setSelectedTags] = useState<string[]>([]);
  const [match, setMatch] = useState<"AND" | "OR">("AND");
  const [gateOverrides, setGateOverrides] = useState<ScreenerGateOverrides>(DEFAULT_GATES);
  const [activePresetKey, setActivePresetKey] = useState<string | null>(null);

  const catalogQuery = useFactorCatalog(market);
  const presetsQuery = useScreenerPresets(market);

  const allConditions: ScreenerRunCondition[] = useMemo(
    () => [...selectedTags.map((tag) => ({ factor: tag, op: "eq" as const })), ...conditions],
    [selectedTags, conditions],
  );

  const runQuery = useRunScreener({
    market,
    conditions: allConditions,
    match,
    gateOverrides,
  });

  function handleMarketChange(next: ScreenerMarket): void {
    setMarket(next);
    setConditions([]);
    setSelectedTags([]);
    setActivePresetKey(null);
  }

  function handleRelaxGates(): void {
    setGateOverrides({
      exclude_disposition: false,
      exclude_attention: false,
      exclude_full_cash_delivery: false,
    });
  }

  function loadScreen(screen: ScreenerScreen): void {
    const catalog = catalogQuery.data;
    const knownTags = new Set((catalog?.signals ?? []).map((signal) => signal.key));
    const loadedConditions = (screen.conditions as ScreenerRunCondition[]).filter(
      (condition) => !knownTags.has(condition.factor),
    );
    const loadedTags = (screen.conditions as ScreenerRunCondition[])
      .filter((condition) => knownTags.has(condition.factor))
      .map((condition) => condition.factor);
    setConditions(loadedConditions);
    setSelectedTags(loadedTags);
    setMatch(screen.match);
    if (screen.gate_overrides) {
      setGateOverrides(screen.gate_overrides as ScreenerGateOverrides);
    }
    setActivePresetKey(null);
  }

  const columns = SCREENER_MARKET_CONFIGS[market].columns;

  return (
    <div className="flex min-h-full flex-col">
      <MarketTabs
        market={market}
        onMarketChange={handleMarketChange}
        mode={mode}
        onModeChange={setMode}
      />

      {mode === "themes" ? (
        <ThemeCards />
      ) : (
        <>
          <FunnelMeter funnel={runQuery.data?.funnel} />
          <GateBar
            overrides={gateOverrides}
            onChange={setGateOverrides}
            passedCount={
              runQuery.data && isFunnelUniverseKnown(runQuery.data.funnel)
                ? runQuery.data.funnel.gated
                : undefined
            }
            market={market}
          />
          <div className="flex flex-1 flex-col lg:flex-row">
            <div className="flex flex-col lg:h-full lg:w-[264px]">
              <ConditionBuilder
                catalog={catalogQuery.data}
                market={market}
                conditions={conditions}
                onConditionsChange={setConditions}
                selectedTags={selectedTags}
                onSelectedTagsChange={setSelectedTags}
                match={match}
                onMatchChange={setMatch}
                liveCount={runQuery.data?.funnel.setup}
              />
              <div className="mt-auto flex flex-col gap-3">
                <SavedScreens
                  market={market}
                  catalog={catalogQuery.data}
                  currentConditions={allConditions}
                  currentMatch={match}
                  currentGateOverrides={gateOverrides}
                  onLoad={loadScreen}
                />
                {runQuery.data && isFunnelUniverseKnown(runQuery.data.funnel) ? (
                  <FunnelMatchRate funnel={runQuery.data.funnel} />
                ) : null}
              </div>
            </div>
            <div className="flex-1">
              <PresetLibrary
                presets={presetsQuery.data ?? []}
                activeKey={activePresetKey}
                onSelect={(preset) => {
                  setConditions(
                    preset.conditions.map((c) => ({
                      factor: c.factor,
                      op: c.op,
                      value: c.value ?? undefined,
                      value2: c.value2 ?? undefined,
                    })),
                  );
                  setSelectedTags([]);
                  setMatch(preset.match);
                  setActivePresetKey(preset.key);
                }}
              />

              <p className="border-[var(--border)] border-y bg-[color-mix(in_srgb,var(--warn)_12%,transparent)] px-4 py-2 text-[12px] text-[var(--text-muted)]">
                ⚠ {t("caveat")}
              </p>

              {runQuery.isLoading ? <ScreenerLoading /> : null}
              {runQuery.isError ? <ScreenerError onRetry={() => runQuery.refetch()} /> : null}
              {runQuery.data && runQuery.data.survivors.length === 0 ? (
                <ScreenerEmptyState onRelaxGates={handleRelaxGates} />
              ) : null}
              {runQuery.data && runQuery.data.survivors.length > 0 ? (
                <ScreenerTable
                  survivors={runQuery.data.survivors}
                  columns={columns}
                  market={market}
                  catalog={catalogQuery.data}
                />
              ) : null}
            </div>
          </div>
        </>
      )}
    </div>
  );
}
