// Miru's own design tokens (web/src/app/tokens.css, dark "Ebène" theme) and fonts
// (web/src/app/fonts.ts). The whole film reuses these; nothing here is a promo-only palette.
import { loadFont as loadMono } from '@remotion/google-fonts/IBMPlexMono';
import { loadFont as loadSans } from '@remotion/google-fonts/NotoSansTC';
import { loadFont as loadSerif } from '@remotion/google-fonts/SourceSerif4';

export const C = {
  bg: '#100d0b',
  surface: '#1d1916',
  surface2: '#27211d',
  surface3: '#312b26',
  border: '#342d27',
  borderStrong: '#4c4238',
  text: '#f4ede3',
  muted: '#a99d8e',
  accent: '#f2873a',
  accentWeak: '#3b2314',
  logo: '#f37021', // --hx-orange, the logo's own orange
  up: '#f0483e', // 漲 — signed numbers only, always with ▲ and a sign
  down: '#1fb574', // 跌 — signed numbers only, always with ▼ and a sign
  chart1: '#6aa0f0',
  chart2: '#b58ae0',
  chart3: '#e5b53d',
  chart4: '#4fb5a5',
} as const;

// The accent as an RGB triple, for rgba() glows (cards keep one ACCENT_RGB variable).
export const ACCENT_RGB = '242,135,58';

const sans = loadSans('normal', {
  weights: ['400', '500', '600', '700'],
  subsets: ['latin', 'chinese-traditional'],
});
const serif = loadSerif('normal', { weights: ['400', '500', '600'], subsets: ['latin'] });
const mono = loadMono('normal', { weights: ['400', '500', '600'], subsets: ['latin'] });

export const F = {
  sans: `${sans.fontFamily}, "Noto Sans TC", sans-serif`,
  serif: `${serif.fontFamily}, ${sans.fontFamily}, serif`,
  mono: `${mono.fontFamily}, ui-monospace, monospace`,
} as const;
