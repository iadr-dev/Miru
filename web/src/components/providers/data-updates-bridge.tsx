"use client";

import type { ReactNode } from "react";
import { useDataUpdates } from "@/hooks/use-data-updates";

/** Keeps market data on screen current for the whole signed-in app: one `data.updated`
 * subscription per tab (`hooks/use-data-updates.ts`). Renders nothing. */
export function DataUpdatesBridge(): ReactNode {
  useDataUpdates();
  return null;
}
