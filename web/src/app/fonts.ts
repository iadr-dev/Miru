/** Self-hosted fonts via next/font — never a Google Fonts <link> (web/CLAUDE.md §1).
 * Noto Sans TC carries the Traditional-Chinese body face; IBM Plex Mono is the tabular numeric
 * face for every price/%/score. Exposed as CSS variables consumed by globals.css @theme. */
import { IBM_Plex_Mono, Noto_Sans_TC, Source_Serif_4 } from "next/font/google";

export const notoSansTC = Noto_Sans_TC({
  subsets: ["latin"],
  weight: ["400", "500", "600", "700"],
  variable: "--font-noto-sans-tc",
  display: "swap",
});

export const plexMono = IBM_Plex_Mono({
  subsets: ["latin"],
  weight: ["400", "500", "600"],
  variable: "--font-plex-mono",
  display: "swap",
});

/** The display serif: large figures and Latin headings. A refined, high-legibility text serif with
 * lining numerals - it gives prices and titles an editorial voice while the Traditional-Chinese
 * body stays in the clean sans above (a serif CJK face at small sizes renders poorly on most
 * systems). */
export const sourceSerif = Source_Serif_4({
  subsets: ["latin"],
  weight: ["300", "400", "500", "600"],
  variable: "--font-source-serif",
  display: "swap",
});
