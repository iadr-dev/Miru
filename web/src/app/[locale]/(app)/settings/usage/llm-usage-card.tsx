"use client";

import { useQuery } from "@tanstack/react-query";
import { useLocale, useTranslations } from "next-intl";
import { type ReactNode, useState } from "react";
import {
  Bar,
  CartesianGrid,
  BarChart as RechartsBarChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { SettingsSection } from "@/components/settings/settings-section";
import { SettingsError, SettingsLoading } from "@/components/settings/settings-states";
import { ProviderBadge } from "@/components/usage/provider-badge";
import { getLlmUsage } from "@/lib/api/settings";
import { cn } from "@/lib/cn";
import { providerStyle } from "@/lib/provider-style";
import {
  formatCostMicros,
  formatEstimateMicros,
  formatTokens,
  shortModelName,
} from "@/lib/usage-format";
import {
  buildPoints,
  buildSeries,
  type CostKind,
  costByProvider,
  modelCost,
  OTHER_KEY,
  type Series,
  selectionTotals,
} from "@/lib/usage-series";
import type { DailyUsage, LlmUsageSummary, ModelTotals } from "@/types/usage";

const RANGES = [7, 30, 90] as const;
type Range = (typeof RANGES)[number];

/** Model usage: what the providers reported for the caller's own chats - tokens for every
 * provider, a charge only where the provider returns one on the response (OpenRouter). It is
 * our record of the calls, not the provider's bill, and the footnote says so. */
export function LlmUsageCard(): ReactNode {
  const t = useTranslations("settings.usage.llm");
  const tc = useTranslations("settings.common");
  const [days, setDays] = useState<Range>(30);
  const query = useQuery({
    queryKey: ["settings", "llm-usage", days],
    queryFn: () => getLlmUsage(days),
  });

  return (
    <SettingsSection title={t("title")} description={t("description")}>
      <div
        role="radiogroup"
        aria-label={t("range")}
        className="mb-4 inline-flex rounded-[var(--radius-button)] border border-[var(--border)] p-0.5"
      >
        {RANGES.map((value) => (
          <button
            key={value}
            type="button"
            role="radio"
            aria-checked={days === value}
            onClick={() => setDays(value)}
            className={cn(
              "min-h-9 rounded-[calc(var(--radius-button)-2px)] px-3 text-[13px] transition-colors",
              "!outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]",
              days === value
                ? "bg-[var(--surface-2)] font-medium text-[var(--text)]"
                : "text-[var(--text-muted)] hover:text-[var(--text)]",
            )}
          >
            {t("rangeDays", { count: value })}
          </button>
        ))}
      </div>

      {query.isLoading ? <SettingsLoading rows={3} /> : null}
      {query.isError ? (
        <SettingsError message={tc("errorLoad")} onRetry={() => void query.refetch()} />
      ) : null}
      {query.data ? <LlmUsageBody summary={query.data} /> : null}

      <p className="pt-4 text-[12px] text-[var(--text-muted)] leading-relaxed">{t("footnote")}</p>
    </SettingsSection>
  );
}

function LlmUsageBody({ summary }: { summary: LlmUsageSummary }): ReactNode {
  const t = useTranslations("settings.usage.llm");
  // Picking a model (legend chip or table row) narrows the figures to it and dims the rest of
  // the chart; picking it again clears. This is how cost is read per model / provider.
  const [selected, setSelected] = useState<string | null>(null);
  const toggle = (key: string): void => setSelected((cur) => (cur === key ? null : key));
  const shown = selectionTotals(summary.models, summary.totals, selected);
  const pickedModel =
    selected === null
      ? null
      : (summary.models.find((m) => `${m.provider}/${m.model}` === selected) ?? null);

  if (summary.totals.calls === 0) {
    return <p className="py-6 text-[13px] text-[var(--text-muted)]">{t("empty")}</p>;
  }

  return (
    <div className="flex flex-col gap-5">
      <dl className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Kpi label={t("kpi.calls")} value={formatTokens(shown.calls)} />
        <Kpi label={t("kpi.input")} value={formatTokens(shown.input_tokens)} />
        <Kpi label={t("kpi.output")} value={formatTokens(shown.output_tokens)} />
        <CostKpi models={summary.models} picked={pickedModel} />
      </dl>

      <DailyChart
        daily={summary.daily}
        models={summary.models}
        selected={selected}
        onToggle={toggle}
      />

      <div className="overflow-x-auto">
        <table className="w-full min-w-[420px] text-[13px]">
          <caption className="sr-only">{t("byModel")}</caption>
          <thead>
            <tr className="border-[var(--border)] border-b text-left text-[12px] text-[var(--text-muted)]">
              <th scope="col" className="py-2 pr-3 font-medium">
                {t("col.model")}
              </th>
              <th scope="col" className="px-3 py-2 text-right font-medium">
                {t("col.calls")}
              </th>
              <th scope="col" className="px-3 py-2 text-right font-medium">
                {t("col.input")}
              </th>
              <th scope="col" className="px-3 py-2 text-right font-medium">
                {t("col.output")}
              </th>
              <th scope="col" className="py-2 pl-3 text-right font-medium">
                {t("col.cost")}
              </th>
            </tr>
          </thead>
          <tbody>
            {summary.models.map((m) => {
              const key = `${m.provider}/${m.model}`;
              const active = selected === key;
              return (
                <tr
                  key={key}
                  className={cn(
                    "border-[var(--border)] border-b transition-colors",
                    active ? "bg-[var(--surface-2)]" : null,
                  )}
                >
                  <th scope="row" className="py-1.5 pr-3 text-left font-normal">
                    <button
                      type="button"
                      aria-pressed={active}
                      onClick={() => toggle(key)}
                      className={cn(
                        "flex min-h-9 items-center gap-2 rounded-[var(--radius-button)] px-1 text-left",
                        "!outline-none hover:text-[var(--accent-text)] focus-visible:ring-2 focus-visible:ring-[var(--accent)]",
                      )}
                    >
                      <span className="text-[var(--text)]">
                        {shortModelName(m.provider, m.model)}
                      </span>
                      <ProviderBadge provider={m.provider} />
                    </button>
                  </th>
                  <td className="px-3 py-2.5 text-right font-num tabular-nums">{m.calls}</td>
                  <td className="px-3 py-2.5 text-right font-num tabular-nums">
                    {formatTokens(m.input_tokens)}
                  </td>
                  <td className="px-3 py-2.5 text-right font-num tabular-nums">
                    {formatTokens(m.output_tokens)}
                  </td>
                  <td className="py-2.5 pl-3 text-right font-num tabular-nums">
                    <CostCell model={m} />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="text-[12px] text-[var(--text-muted)]">{t("pickHint")}</p>
    </div>
  );
}

/** One cost figure in the right form: the provider's own charge, a marked estimate, or "no
 * price" - never a bare dash, and an estimate always carries the approximately sign. */
function CostText({ kind, micros }: { kind: CostKind; micros: number }): ReactNode {
  const t = useTranslations("settings.usage.llm");
  if (kind === "reported") {
    return formatCostMicros(micros);
  }
  if (kind === "estimated") {
    return <span title={t("estimatedHint")}>{formatEstimateMicros(micros)}</span>;
  }
  return (
    <span className="font-sans text-[12px] text-[var(--text-muted)]" title={t("unpricedHint")}>
      {t("unpriced")}
    </span>
  );
}

/** Plain-text form of a cost (an estimate keeps its "≈"). */
function costString(kind: CostKind, micros: number): string {
  return kind === "estimated" ? formatEstimateMicros(micros) : formatCostMicros(micros);
}

function CostCell({ model }: { model: ModelTotals }): ReactNode {
  const cost = modelCost(model);
  return <CostText kind={cost.kind} micros={cost.micros} />;
}

/** The cost figure. With a model picked it is that model's; otherwise the total of what is
 * reported plus what is estimated (marked "≈" when any part is an estimate), with one line per
 * provider underneath showing which kind of number each one is. */
function CostKpi({
  models,
  picked,
}: {
  models: ModelTotals[];
  picked: ModelTotals | null;
}): ReactNode {
  const t = useTranslations("settings.usage.llm");
  if (picked !== null) {
    const cost = modelCost(picked);
    const value = cost.kind === "unpriced" ? "—" : costString(cost.kind, cost.micros);
    return (
      <Kpi
        label={t("kpi.cost")}
        value={value}
        note={
          cost.kind === "reported"
            ? undefined
            : t(cost.kind === "estimated" ? "estimatedHint" : "unpricedHint")
        }
      />
    );
  }
  const perProvider = costByProvider(models);
  const known = perProvider.filter((p) => p.kind !== "unpriced");
  const total = known.reduce((sum, p) => sum + p.costMicros, 0);
  const hasEstimate = known.some((p) => p.kind === "estimated");
  const value =
    known.length === 0 ? "—" : costString(hasEstimate ? "estimated" : "reported", total);
  return (
    <Kpi
      label={t("kpi.cost")}
      value={value}
      note={
        <ul className="m-0 flex list-none flex-col gap-0.5 p-0">
          {perProvider.map((p) => (
            <li key={p.provider} className="flex justify-between gap-2">
              <span>{providerStyle(p.provider).label}</span>
              <span className="font-num tabular-nums">
                <CostText kind={p.kind} micros={p.costMicros} />
              </span>
            </li>
          ))}
        </ul>
      }
    />
  );
}

function Kpi({
  label,
  value,
  note,
}: {
  label: string;
  value: string;
  note?: ReactNode;
}): ReactNode {
  return (
    <div className="rounded-[var(--radius-card)] border border-[var(--border)] px-3 py-2.5">
      <dt className="text-[12px] text-[var(--text-muted)]">{label}</dt>
      <dd className="mt-0.5 font-num text-[20px] text-[var(--text)] tabular-nums">{value}</dd>
      {note ? <div className="mt-1 text-[11px] text-[var(--text-muted)]">{note}</div> : null}
    </div>
  );
}

function seriesLabel(series: Series, otherLabel: string): string {
  return series.key === OTHER_KEY ? otherLabel : series.model;
}

function DailyChart({
  daily,
  models,
  selected,
  onToggle,
}: {
  daily: DailyUsage[];
  models: ModelTotals[];
  selected: string | null;
  onToggle: (key: string) => void;
}): ReactNode {
  const t = useTranslations("settings.usage.llm");
  const locale = useLocale();
  const dateFormat = new Intl.DateTimeFormat(locale, { month: "numeric", day: "numeric" });
  // One stacked segment per model, coloured by its provider, so a day spread over several
  // models (or providers) shows who served what instead of one lump.
  const series = buildSeries(models);
  const points = buildPoints(daily, series, (iso) =>
    dateFormat.format(new Date(`${iso}T00:00:00Z`)),
  );
  const otherLabel = t("other");

  return (
    <figure className="m-0">
      <figcaption className="mb-2 text-[12px] text-[var(--text-muted)]">
        {t("chartTitle")}
      </figcaption>
      <ul className="m-0 mb-3 flex list-none flex-wrap gap-x-4 gap-y-1.5 p-0 text-[12px]">
        {series.map((s) => {
          const pickable = s.key !== OTHER_KEY;
          const dimmed = selected !== null && selected !== s.key;
          const content = (
            <>
              <span aria-hidden className="size-2.5 rounded-sm" style={{ background: s.color }} />
              {seriesLabel(s, otherLabel)}
              {s.provider ? (
                <span className="text-[var(--text-muted)]">{providerStyle(s.provider).label}</span>
              ) : null}
            </>
          );
          return (
            <li key={s.key} className={cn("inline-flex", dimmed ? "opacity-50" : null)}>
              {pickable ? (
                <button
                  type="button"
                  aria-pressed={selected === s.key}
                  onClick={() => onToggle(s.key)}
                  className={cn(
                    "inline-flex min-h-8 items-center gap-1.5 rounded-[var(--radius-button)] px-1.5 text-[var(--text)]",
                    "!outline-none hover:bg-[var(--surface-2)] focus-visible:ring-2 focus-visible:ring-[var(--accent)]",
                    selected === s.key ? "bg-[var(--surface-2)]" : null,
                  )}
                >
                  {content}
                </button>
              ) : (
                <span className="inline-flex min-h-8 items-center gap-1.5 px-1.5 text-[var(--text)]">
                  {content}
                </span>
              )}
            </li>
          );
        })}
      </ul>
      <div className="h-56 w-full" role="img" aria-label={t("chartTitle")}>
        <ResponsiveContainer width="100%" height="100%">
          <RechartsBarChart data={points} margin={{ top: 4, right: 4, bottom: 0, left: 0 }}>
            <CartesianGrid vertical={false} stroke="var(--border)" />
            <XAxis
              dataKey="label"
              tickLine={false}
              axisLine={false}
              tick={{ fill: "var(--text-muted)", fontSize: 11 }}
              interval="preserveStartEnd"
              minTickGap={24}
            />
            <YAxis
              width={44}
              tickLine={false}
              axisLine={false}
              tick={{ fill: "var(--text-muted)", fontSize: 11 }}
              tickFormatter={(value: number) => formatTokens(value)}
            />
            <Tooltip
              cursor={<DashedCursor />}
              content={
                <UsageTooltip series={series} otherLabel={otherLabel} totalLabel={t("total")} />
              }
            />
            {series.map((s) => (
              <Bar
                key={s.key}
                dataKey={s.key}
                stackId="tokens"
                fill={s.color}
                fillOpacity={selected === null || selected === s.key ? 1 : 0.2}
                isAnimationActive={false}
              />
            ))}
          </RechartsBarChart>
        </ResponsiveContainer>
      </div>
    </figure>
  );
}

/** A dashed vertical guide through the hovered day (recharts hands a cursor its bar slot). */
function DashedCursor({
  x = 0,
  y = 0,
  width = 0,
  height = 0,
}: {
  x?: number;
  y?: number;
  width?: number;
  height?: number;
}): ReactNode {
  const centre = x + width / 2;
  return (
    <line
      x1={centre}
      x2={centre}
      y1={y}
      y2={y + height}
      stroke="var(--text-muted)"
      strokeDasharray="4 4"
    />
  );
}

type TooltipEntry = { dataKey?: string | number; value?: number | string };

/** Date, every model that day (largest first) with its provider, then the day's total. */
export function UsageTooltip({
  active,
  payload,
  label,
  series,
  otherLabel,
  totalLabel,
}: {
  active?: boolean;
  payload?: TooltipEntry[];
  label?: string | number;
  series: Series[];
  otherLabel: string;
  totalLabel: string;
}): ReactNode {
  if (!active || !payload || payload.length === 0) {
    return null;
  }
  const byKey = new Map(series.map((s) => [s.key, s]));
  const rows = payload
    .map((entry) => ({ entry, s: byKey.get(String(entry.dataKey)), value: Number(entry.value) }))
    .filter((row) => row.s !== undefined && row.value > 0)
    .sort((a, b) => b.value - a.value);
  const total = rows.reduce((sum, row) => sum + row.value, 0);
  return (
    <div className="min-w-44 rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3 py-2 text-[12px] shadow-lg">
      <p className="mb-1.5 font-medium text-[var(--text)]">{label}</p>
      <ul className="m-0 flex list-none flex-col gap-1 p-0">
        {rows.map(({ s, value }) =>
          s ? (
            <li key={s.key} className="flex items-center justify-between gap-4">
              <span className="inline-flex items-center gap-1.5 text-[var(--text)]">
                <span aria-hidden className="size-2 rounded-full" style={{ background: s.color }} />
                {s.provider ? `${providerStyle(s.provider).label} · ` : ""}
                {seriesLabel(s, otherLabel)}
              </span>
              <span className="font-num tabular-nums">{formatTokens(value)}</span>
            </li>
          ) : null,
        )}
      </ul>
      <p className="mt-1.5 flex justify-between border-[var(--border)] border-t pt-1.5 text-[var(--text-muted)]">
        <span>{totalLabel}</span>
        <span className="font-num tabular-nums">{formatTokens(total)}</span>
      </p>
    </div>
  );
}
