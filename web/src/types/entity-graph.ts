/**
 * Entity-graph contract — mirrors `GET /symbols/{symbol}/entity-graph`: the company in focus,
 * the entities (technologies, materials, applications, products) it is mentioned with, and the
 * other companies that share those entities. Link `kind` says how two nodes are connected:
 * `supply` is a verified supplier-to-customer relationship; `mention` links a company to an entity
 * it mentions; `cooccurrence` only says two names appear together and is never a business
 * relationship.
 */
import { z } from "zod";

export const ENTITY_TYPES = [
  "tw_company",
  "intl_company",
  "technology",
  "material",
  "application",
  "product",
] as const;
export const entityTypeSchema = z.enum(ENTITY_TYPES);
export type EntityType = z.infer<typeof entityTypeSchema>;

export const ENTITY_LINK_KINDS = ["mention", "cooccurrence", "supply"] as const;
export const entityLinkKindSchema = z.enum(ENTITY_LINK_KINDS);
export type EntityLinkKind = z.infer<typeof entityLinkKindSchema>;

export const entityNodeSchema = z.object({
  id: z.string(),
  label: z.string(),
  type: entityTypeSchema,
  symbol: z.string().nullable(),
  market: z.enum(["TW", "US"]).nullable(),
  weight: z.number(),
  focus: z.boolean(),
});
export type EntityNode = z.infer<typeof entityNodeSchema>;

export const entityLinkSchema = z.object({
  source: z.string(),
  target: z.string(),
  weight: z.number(),
  kind: entityLinkKindSchema,
});
export type EntityLink = z.infer<typeof entityLinkSchema>;

export const entityGraphResponseSchema = z.object({
  symbol: z.string(),
  market: z.string(),
  nodes: z.array(entityNodeSchema).default([]),
  links: z.array(entityLinkSchema).default([]),
  /** Additive: true when the server withheld part of this answer for the caller's plan. */
  locked: z.boolean().default(false),
  /** Additive: how many items were withheld (0 when nothing was). */
  hidden_count: z.number().default(0),
});
export type EntityGraphResponse = z.infer<typeof entityGraphResponseSchema>;
