/**
 * `ChartHint` — mirrors `backend/src/schemas/chart_hint.py` field-for-field. A minimal
 * Vega-Lite-style `mark` + channel-encoding grammar an `output_model` may optionally carry
 * alongside its data, read by `chat/cards/generic-chart.tsx` to pick a chart shape independent
 * of which tool produced the data.
 *
 * Carries no color/hex value anywhere — `color`/`theta`/`x`/`y` are channel-to-field-NAME
 * mappings only; the renderer resolves actual colors from design tokens (`--chart-1..10`/
 * `--up`/`--down`), never from this schema. `diverging: true` routes to `--up`/`--down` instead
 * of a categorical token, mirroring `DivergingBarChart`'s existing convention.
 */
import { z } from "zod";

export const FIELD_TYPES = ["quantitative", "nominal", "ordinal", "temporal"] as const;
export type FieldType = (typeof FIELD_TYPES)[number];

export const MARKS = ["line", "bar", "area", "point", "arc"] as const;
export type Mark = (typeof MARKS)[number];

const _TEMPORAL_OR_ORDINAL_MARKS: readonly Mark[] = ["line", "area"];
const _DIVERGING_ALLOWED_MARKS: readonly Mark[] = ["bar", "line"];

export const fieldDefSchema = z.object({
  field: z.string().min(1),
  type: z.enum(FIELD_TYPES),
  aggregate: z.enum(["sum", "mean", "count"]).nullable().optional(),
});
export type FieldDef = z.infer<typeof fieldDefSchema>;

export const chartHintSchema = z
  .object({
    mark: z.enum(MARKS),
    title: z.string().nullable().optional(),
    data_key: z.string().nullable().optional(),
    x: fieldDefSchema.nullable().optional(),
    y: fieldDefSchema.nullable().optional(),
    y2: fieldDefSchema.nullable().optional(),
    color: fieldDefSchema.nullable().optional(),
    theta: fieldDefSchema.nullable().optional(),
    size: fieldDefSchema.nullable().optional(),
    diverging: z.boolean().default(false),
  })
  .refine(
    (hint) =>
      !_TEMPORAL_OR_ORDINAL_MARKS.includes(hint.mark) ||
      (hint.x != null && (hint.x.type === "temporal" || hint.x.type === "ordinal")),
    { message: "line/area require x.type to be temporal or ordinal" },
  )
  .refine((hint) => hint.mark !== "arc" || (hint.x == null && hint.y == null), {
    message: "mark=arc has no axes — x/y must not be set",
  })
  .refine((hint) => hint.mark !== "arc" || (hint.theta != null && hint.color != null), {
    message: "mark=arc requires both theta and color",
  })
  .refine((hint) => hint.mark === "arc" || hint.theta == null, {
    message: "theta is only valid for mark=arc",
  })
  .refine((hint) => hint.mark === "bar" || hint.y2 == null, {
    message: "y2 is only valid for mark=bar",
  })
  .refine((hint) => hint.mark === "point" || hint.size == null, {
    message: "size is only valid for mark=point",
  })
  .refine((hint) => !hint.diverging || _DIVERGING_ALLOWED_MARKS.includes(hint.mark), {
    message: "diverging=true is only valid for mark in [bar, line]",
  })
  .refine((hint) => !hint.diverging || hint.color == null, {
    message: "diverging=true and color are mutually exclusive",
  });
export type ChartHint = z.infer<typeof chartHintSchema>;
