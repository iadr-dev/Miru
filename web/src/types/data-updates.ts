/**
 * The `data.updated` cache-invalidation SSE frame — the ingest leader's signal that a
 * whole-market partition landed, relayed verbatim by `GET /updates/stream`
 * (`backend/src/api/routers/context.py::updates_stream`). Wire shape is the same
 * `id: <seq>\nevent: data.updated\ndata: <json>\n\n` framing as every other stream
 * (`backend/src/api/sse.py::format_sse_event`), so this reuses `sseEnvelopeSchema` for the
 * envelope and narrows `data` to the `_payload()` shape from `backend/src/events/data_updates.py`.
 *
 * `capability`/`market` are the raw enum *values* (`Capability`/`Market` from
 * `backend/src/schemas/enums.py`), not the frontend's narrower `Market` union in
 * `types/settings.ts` — the backend enum has members (`GB`/`JP`/`EU` etc. plus every non-price
 * capability) the frontend doesn't otherwise model, so this is intentionally a plain `string` at
 * this seam; a consumer narrows it against whatever query keys it actually owns.
 */
import { z } from "zod";
import { sseEnvelopeSchema } from "@/types/sse";

export const dataUpdatedPayloadSchema = z.object({
  capability: z.string(),
  market: z.string(),
  as_of: z.string(),
  scope: z.string(),
});
export type DataUpdatedPayload = z.infer<typeof dataUpdatedPayloadSchema>;

export const dataUpdatedEventSchema = sseEnvelopeSchema.extend({
  data: dataUpdatedPayloadSchema,
});
export type DataUpdatedEvent = z.infer<typeof dataUpdatedEventSchema>;
