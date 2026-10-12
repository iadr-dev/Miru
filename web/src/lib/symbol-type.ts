/**
 * Client-side mirror of `backend/src/analytics/symbol_universe.py::classify_symbol_type` — a
 * pure code-shape rule (no fetch), so the stock/ETF detail page can pick which tab-bar to render
 * synchronously from the `symbol` route param alone. Keep this in lockstep with the backend rule:
 * a plain equity is a 4-digit-or-shorter numeric code; a TW ETF is `00`-prefixed, up to 6 chars,
 * with a `L`/`R` suffix marking leveraged/inverse products (TWSE's own documented convention).
 */
import type { SymbolType } from "@/types/market/shared";

const MAX_ETF_LENGTH = 6;
const ETF_PREFIX = "00";
const LEVERAGED_SUFFIX = "L";
const INVERSE_SUFFIX = "R";

export function classifySymbolType(symbol: string): SymbolType {
  if (symbol.startsWith(ETF_PREFIX) && symbol.length <= MAX_ETF_LENGTH) {
    if (symbol.endsWith(LEVERAGED_SUFFIX)) return "etf_leveraged";
    if (symbol.endsWith(INVERSE_SUFFIX)) return "etf_inverse";
    return "etf";
  }
  return "stock";
}

export function isEtfSymbolType(symbolType: SymbolType): boolean {
  return symbolType !== "stock";
}
