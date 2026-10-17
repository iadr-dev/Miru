/**
 * MCP OAuth consent — `GET/POST /api/v1/oauth/consent/{request_id}` (`backend/src/api/routers/
 * oauth_consent.py`). The consent screen is where a third-party AI client (Claude, Gemini, Cursor,
 * …) asks the signed-in user to let it use Miru.
 */
import { z } from "zod";

export const consentResponseSchema = z.object({
  request_id: z.string(),
  client: z.object({
    name: z.string(),
    client_id: z.string(),
    kind: z.enum(["registered", "metadata_url"]),
    redirect_host: z.string(),
    verified: z.boolean(),
  }),
  redirect_uri: z.string(),
  scopes: z.array(z.object({ id: z.string(), description: z.string() })),
  resource: z.object({ url: z.string(), label: z.string() }),
  nonce: z.string(),
  expires_at: z.string(),
});
export type ConsentResponse = z.infer<typeof consentResponseSchema>;

export const consentDecisionResponseSchema = z.object({ redirect_to: z.string() });

export type ConsentDecision = "approve" | "deny";
