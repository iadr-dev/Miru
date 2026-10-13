/**
 * Type helpers over the generated OpenAPI types (`openapi.ts`, from `web/openapi.json` via
 * `pnpm gen:api`). Used only by `contract-checks.ts`; nothing here exists at runtime.
 */
import type { z } from "zod";
import type { paths } from "@/types/generated/openapi";

/** What actually goes over the wire. The spec marks a field with a default as optional, but the
 * backend serializes every field (no route uses `exclude_unset`/`exclude_none`), so a defaulted
 * field is always present — as its value or `null`. */
type Serialized<T> = T extends readonly (infer Item)[]
  ? Serialized<Item>[]
  : T extends object
    ? { [K in keyof T]-?: Serialized<Exclude<T[K], undefined>> }
    : T;

type JsonBody<R> = R extends { content: { "application/json": infer Body } }
  ? Serialized<Body>
  : never;

/** The JSON body the backend documents for an operation's success response (200, else 201). */
export type ResponseOf<P extends keyof paths, M extends keyof paths[P]> = paths[P][M] extends {
  responses: infer R;
}
  ? R extends { 200: infer Ok }
    ? JsonBody<Ok>
    : R extends { 201: infer Created }
      ? JsonBody<Created>
      : never
  : never;

/** Compiles only if everything the backend documents for `Backend` is accepted by `Schema`'s
 * parser — so a renamed, removed, retyped or newly nullable field is a `tsc` error naming the
 * field, while an added field (which the parser ignores) is fine. */
export type Accepts<Schema extends z.ZodTypeAny, Backend extends z.input<Schema>> = [
  Schema,
  Backend,
];
