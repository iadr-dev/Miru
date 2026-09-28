/**
 * Shared open/closed state for `SearchPalette` (Cmd/Ctrl+K command palette). Previously local
 * `useState` inside `TopBar`, which meant nothing outside the top bar could open it — the
 * `/stock` empty state needs to trigger the same palette its own "搜尋股票" button opens, without
 * a prop channel between two unrelated routes. Zustand per web/CLAUDE.md §3, same
 * cross-surface-signal role as `new-thread-store.ts`.
 */
import { create } from "zustand";

type SearchPaletteState = {
  open: boolean;
  setOpen: (open: boolean) => void;
};

export const useSearchPaletteStore = create<SearchPaletteState>((set) => ({
  open: false,
  setOpen: (open) => set({ open }),
}));
