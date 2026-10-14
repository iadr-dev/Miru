# Miru promo — design spec (video-shotcraft, autonomous creation)

The production baseline for the independent final review. Everything here was decided by the
agent after the user said "all decide by you"; the user's explicit constraints are marked **[user]**.

## Mode

Co-creation was chosen first; the user then handed over all decisions, so the run switched to
**autonomous creation** (`references/pipeline.md`), recorded here.

## Product brief

| Item | Decision | Evidence |
|---|---|---|
| Positioning | AI research assistant for Taiwan & US stocks/ETFs: ask in plain language, get data-backed context. Context, never trading advice. | `AGENTS.md` governance; tagline `hero.tagline` |
| Audience | Retail investors (zh-TW first), plus US-market followers | default locale `zh-TW` |
| Use | Showcase promo for the repo README (GitHub `user-attachments` video) **[user]** | user message |
| Approach | **Concept-led, not a feature tour [user]**: explain the idea with designed scenes | user message |
| Data | **Mock data only [user]**. Invented figures; no real tickers or company names | user message |
| Languages | **Two versions: zh-TW and en [user]**, one timeline, `lang` input prop | user message |
| Length / format | ~39 s, 1920×1080, 30 fps | promo + README embed |
| Audio | SFX only (no BGM) | see "Audio" below |
| Compliance | No trading language (明牌/飆股/目標價/買賣/進場/停損, buy/sell/target/pick); the answer keeps its non-trading caveat; no celebratory or magnitude-scaled motion on a price | `web/OVERVIEW.md` §4, `docs/brand.md` §5 (also no 漲停, hot tips, the 盤 root or buy/sell framing such as 買超 / net buying) |

## Requirement → execution decisions

| Requirement | Evidence | Affects | Decision |
|---|---|---|---|
| Easy to understand | user | story | One metaphor, "Noise → Clarity": market noise, one question, sources converge, one calm answer, the noise focuses into the aperture logo |
| Concept, not features | user | assets | All scenes hand-built (non-replica) at publication quality (Q1/Q10); no page screenshots, so no dev-server capture |
| Two languages | user | copy, layout | Copy table keyed by `lang`; CJK and Latin get their own type sizes; same frame timeline |
| Mock data | user | copy | Sector-level example ("AI server stocks"), invented numbers, a caveat line |
| Non-trading | governance | copy, motion | Copy says understand / context / sources; no price celebration; red/green only on signed numbers with ▲▼ |
| README showcase | user | output | MP4, H.264, < 100 MB, plus a poster PNG |

## Visual direction (chosen: "Ebène")

Built from `web/src/app/tokens.css` and `fonts.ts` — the product's own dark theme. Styleframe
comparison skipped: the product has a strict, documented token system (tokens.css is the single
source), which the skill lists as a valid reason to skip; still frames are verified per shot instead.

- Surfaces: `--bg #100d0b`, `--surface #1d1916`, `--surface-2 #27211d`, `--border #342d27`
- Text: `--text #f4ede3` (cream, never white), `--text-muted #a99d8e`
- Accent (chrome only): `--accent #f2873a`; the logo orange `#f37021`
- Signed numbers only: `--up #f0483e` (漲), `--down #1fb574` (跌), always with ▲/▼ and a sign
- Fonts: Source Serif 4 (display, Latin headings/figures), Noto Sans TC (body, CJK), IBM Plex Mono
  (every number, tabular)
- Radius: card 16 · input 10 · pill 999
- Materials: warm ebony with a soft amber glow, no blue-grey, no pure black/white

Motion tokens — preset "professional / premium" (fintech, low energy, serious): main durations
~21–48 f, entrances `bezier(0,0,0.2,1)`-class out-curves, no bounce except where a card's tuned
parameters require a landing overshoot. Three words: **calm, precise, warm**.

## Feature → shot mapping

| Beat | Shot card · style | Demo source | Why |
|---|---|---|---|
| Market noise | `flying-words` | `demos/typography/flying-words/FlyingWords.tsx` | A tunnel of headlines/tickers reads as "too much information" without a word cloud |
| Ask | `glass-pill-dictation-typing` | `demos/interaction/glass-pill-dictation-typing/GlassPillDictationTyping.tsx` | The quiet beat: the input glows, then gives its light to the question |
| Sources converge | `bezier-source-converge-merge` | `demos/ui-entrance/bezier-source-converge-merge/BezierSourceConvergeMerge.tsx` | "Miru pulls price, flows, news and earnings together" as a physical process |
| Answer | `ai-stream-response` | `demos/interaction/ai-stream-response/StreamResponse.tsx` | Conclusion first, then evidence, then done — exactly how Miru answers |
| Breath title | `blur-slide` | `demos/typography/blur-slide/BlurSlide.tsx` | Default low-energy title, gives way to the heavier shots |
| Coverage | `pill-slot-cycle` | `demos/typography/pill-slot-cycle/PillSlotCycle.tsx` | One sentence, the slot rotates through what you can ask about |
| Brand | `ui-to-brand-morph · input-morph-assemble` | `demos/outro/ui-to-brand-morph/InputMorphsIntoLogo.tsx` | The input you've watched all film becomes the aperture logo: "the thing you ask is Miru" |

Deviation from `promo-energy-arc` (recorded): the open is the noise tunnel (mid-high) rather than a
low brand open, because the story needs the "before" state first; the outro is a morph lockup
rather than the group-photo peak, matching the quiet maison brand. Both wordmark holds ≥1 s kept.

## Storyboard (30 fps, 1177 f ≈ 39.2 s; frame ranges are `src/timeline.ts`)

| # | Frames | Shot | Main action | Copy (zh-TW / en) |
|---|---|---|---|---|
| 1 | 0–165 | flying-words | Headlines, tickers and % moves fly out of the dark at the camera | 「市場每天都在說話。」 / "The market never stops talking." → 「但哪些才是重點？」 / "But what actually matters?" |
| 2 | 165–315 | glass-pill | Noise dims and blurs; the composer pill drops in glowing orange; the question types and the glow goes out | 「AI 伺服器族群為什麼在漲？」 / "Why are AI server stocks rising?" |
| 3 | 315–515 | bezier-converge | Four sources (價量 Price · 籌碼 Flows · 新聞 News · 財報 Earnings) draw in and slide into the Miru mark; after the merge the frame glides the mark to centre (one motivated move, added in QA because the card layout left the end frame lopsided) | 「Miru 一次整理所有資料」 / "Miru pulls every source together." |
| 4 | 515–695 | ai-stream-response | Answer panel: summary first, then five evidence rows, completion, caveat | see copy table in `src/copy.ts` |
| 5 | 695–785 | blur-slide | Title in on ebony | 「看懂市場，不再靠猜。」/ "Understand the market. Stop guessing." + subtitle |
| 6 | 785–971 | pill-slot-cycle | 「問 Miru」 + slot: 台股 · 美股 · ETF · 產業輪動 · 法人籌碼 → 「任何市場問題。」 | "Ask Miru about" + Taiwan stocks · US stocks · ETFs · Sector rotation · Fund flows → "the whole market." |
| 7 | 971–1177 | input-morph-assemble | Composer sends; the box springs into the disc; three pairs of aperture gaps slice in; the centre opens; the vector crossfades into the exact logo.png (knocked out for ebony); vertical lockup, the wordmark settling letter by letter, tagline; hold ≥1 s | 「Miru」「你的股票與 ETF AI 助理」 / "Miru" "Your AI assistant for stocks & ETFs" |

## Audio

SFX only. Every bundled BGM is strong-beat (tech-house / hip-hop), which clashes with the quiet
premium brand and would need beat analysis; the Ink Press template ships SFX-only as well. A BGM
version can be added later through the `bgm` prop if wanted.

## Data compliance

All numbers invented; "AI server stocks" is a sector, not a company; the answer ends with
「僅供研究參考，非投資建議」/ "Context for research, not investment advice"

## Implementation notes (recorded during build)

- **Seams** (shot-transitions): C focus relay (blur 0→8 / 8→0 px, 12 f exchange, 2 f stagger) on
  2→3, 3→4, 5→6, 6→7; D dark title card on 4→5; 1→2 is built into shot 1 (the tunnel recedes
  behind the composer).
- **Logo**: the real `web/public/logo.png` is used wherever the mark is shown whole (shot 3 badge,
  shot 4 header, shot 7 lockup), knocked out for the dark background with a colour matrix and
  rendered through Remotion `<Img>` (load-tracked). A vector rebuild (`src/MiruMark.tsx`) exists
  only to assemble the mark in shot 7 and crossfades into the real logo.
- **Readability (Q11)**: narration captions 58 px; shot 4 summary 42 px, rows 32 px,
  label/meta/caveat 28–32 px, the caveat on its own line so it stays readable in both languages.
- **Sound**: SFX only, pins in `src/SoundTrack.tsx`, all relative to `SHOTS`. Film vocabulary plus
  real-object foley (camera shutters for the aperture, light switches for rows and pills).
  Riser → impact → sparkle at the end; the impact is the loudest beat and its tail fades with the
  film. Every file has a traceable Mixkit source (`AUDIO-CREDITS.md`); the library's untraceable
  keyboard / riser-cine / sparkle / pop were swapped out. Master gain ×1.5 after measuring the
  first mix (peak −6.1 dBFS, no clipping).
- **Copy compliance**: the noise words originally included 目標價 / "Price target?" and 當沖;
  replaced, since 目標價 is on the frontend governance banned list even as background noise.
  The independent review then flagged "Hot tip?", 漲停, 外資賣超 and "net buying" / 買超 (docs/brand.md
  §5); all replaced (Buzz, 量能, 外資動向, "net inflows" / 淨流入).

## Card deviations (recorded after the independent review)

| Card | Card default | This film | Why |
|---|---|---|---|
| glass-pill | 50 f | 150 f | the real question is 2–3× the placeholder; typing stays at a human pace in en too |
| bezier-converge | motion = shot | motion 168 f inside a 200 f shot | the last 32 f freeze the end frame so the caption is still before the seam (review #1) |
| ai-stream-response | 6–8 rows | 5 rows | Q11 type sizes (rows 32 px) fit five rows on the panel |
| blur-slide | 114 f | 90 f | a breath between the two heavy shots; title + subtitle still finish ≥20 f before the seam |
| pill-slot-cycle | — | 186 f | the finale holds ≥45 f (review #7) |
| input-morph-assemble | pieces fly in | gaps slice in place | the aperture's gaps are its shape; the final lockup holds ≥30 f (shot 206 f, review B1); the vector sits underneath and the real logo fades in on top over 4 f, so nothing dims (review #3) |
| flying-words | 15–26 px | en 18–32 px | Latin words are wider-set and need the size to read at the same density |
| shot 7 composer hint | repeats the shot-2 question | 「這週 ETF 資金流向哪裡？」 / "Where are ETF flows heading this week?" | shot 6 ends on "the whole market"; a new question avoids saying the same sentence twice (review #8) |

Sound after review #4: the impact sample has a ~14 f lead-in, so it is pinned 14 f before the settle
so the hit lands on it; the riser runs up to the hit and fades over 8 f instead of being cut while
still loud; typing is three quieter pins over the full ~100 f typing window; soft fills at f0 and at
the start of shot 7 remove the two near-silent stretches.
