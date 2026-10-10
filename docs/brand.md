# Brand — Miru

> Blueprint-stage brand decision for the AI TW Stock/ETF analysis agent. Name pressure-tested
> against the live web + Taiwan fintech field (collision-checked, not fabricated). **Trademark
> (TIPO) and app-store / LINE-handle availability are NOT yet verified — see §6 gate.**

## 1. The decision

| | |
|---|---|
| **Name (all locales)** | **Miru** |
| **Wordmark** | Miru |
| **Mascot** | 藍鵲 — the Formosan blue magpie (a named sidekick *under* the brand, not the brand) |
| **Fallback name** | **Liran** (if "Miru" fails the TIPO/app-store gate — also collision-clean, weaker romanization) |

## 2. Why this name (positioning fit)

The product is a **non-trading, context-only analysis companion** — it explains the market and
shows its work; it never says buy/sell. The name encodes exactly that:

- **Miru** (見る, "to see") — perception, not prescription. It means *seeing*, never *acting*,
  which keeps it FSC-safe (金管會) while still warm and human. Two syllables, unambiguous to
  pronounce, one name for every locale, and it travels globally when the product expands beyond
  Taiwan.
- **Theme:** Miru (see) · 見微 (see the small) — the whole system is about *clarity/perception*,
  reinforcing the analysis-companion promise instead of a trading tip.

## 3. Brand architecture (brand > mascot > personality)

Deliberate three-layer structure — the brand carries credibility, the mascot carries warmth, the
personalities carry voice. Keeping them separate protects all three:

```
Miru                             ← the brand: trustworthy analyst engine (App Store, logo, "powered by")
  └─ 藍鵲 (blue magpie)          ← mascot: face of gamification / streaks / daily companion (cosmetic)
       └─ personality "modes"     ← the same engine's selectable voice, never separate advisors
```

- **Why brand-over-mascot:** if the mascot *were* the brand, the product reads as a cute toy and
  fights the professional, "shows-its-work" credibility retail investors + active traders need.
- **Why it protects the personality feature** ([modules/23](backend/modules/23-agent-personality.md)):
  personalities are *interchangeable voices of one trustworthy engine* — one brain, many faces.
  If the mascot were the brand, swapping personality would feel like swapping the product.
- **Why it future-proofs expansion:** a neutral analytical brand (Miru) travels to US/other
  markets; the 藍鵲 stays a beloved *local* sidekick without constraining the parent brand.
- Pets ([modules/21](backend/modules/21-pet-gamification.md)) remain **cosmetic-only, decoupled
  from tiers/analysis** — the 藍鵲 is the flagship mascot within that existing system, not a new one.

### 3a. Mascot naming (藍鵲 blue magpie)

- **Species/flagship pet id:** `blue_magpie` · display **藍鵲** / **Sisi**. "Sisi" = a soft,
  sayable pet name (echoes the magpie's bright call); the flagship free-pull pet on register.
- Other catalog pets keep the module-21 rarity system; 藍鵲 is the **hero mascot** (marketing +
  the default companion), not the only pet. Rarity/level change only cosmetic tone, never analysis.
- Role in copy: 藍鵲/Sisi is "the one who *sees* the market with you" — reinforces Miru (見/see),
  never gives advice (governance §1, module 21 hard-separation rule).

### 3b. Personality-mode names ([modules/23](backend/modules/23-agent-personality.md))

The four presets are **modes of one engine** — named as *voices*, never as advisors. `id`
matches module 23's registry (`steady`/`coach`/`concise`/`data`):

| id | Mode name (zh / en) | Voice |
|----|--------------------|-------|
| `steady` *(default)* | **穩健派** / Steady | Balanced, cautious, both-sides, risk-aware |
| `coach` | **白話派** / Coach | Plain-中文, explains jargon, encouraging, teaches |
| `concise` | **簡潔派** / Concise | Bullet-first, terse, leads with the takeaway |
| `data` | **數據派** / Data | Numbers-first, tables, minimal prose |

Presentation rule: users pick **「Miru 的語氣」/ "Miru's voice"** — one trustworthy brain, four
tones. Never framed as choosing a different analyst or advisor (protects the non-trading promise).

## 4. Voice & the FSC-safe line (brand-level)

- **Descriptive, never prescriptive.** The brand promise is "understand the market," never "beat
  the market." Marketing copy inherits the same non-trading guardrail as the product
  ([governance.md](backend/governance.md) §1): no 明牌/飆股/目標價/保證/穩賺/獲利, no buy-sell framing.
- **Bilingual, zh-TW-first.** One name, "Miru", in every locale; copy around it is zh-TW first.
- **Colour cue:** a single signature orange on warm ebony/cream neutrals (see `web/src/app/tokens.css`)
  — a maison feel: quiet, premium, one accent. The 藍鵲 keeps its own blue as the mascot's colour.

## 5. Names to keep OUT (regulated / off-brand)

Never in the brand, product names, tier names, or marketing: 投顧/投資顧問, 證券, 期貨, 基金,
財富管理, 理財, 操盤/代操, 明牌/報明牌, 飆股, 目標價, 漲停, 保證/穩賺/獲利/翻倍/必勝. Avoid the
root 盤 (看盤/解盤 → active-trading read); prefer 市 (market). Skip fintech clichés (Fin-, -vest,
Robo-, Quant-, Alpha-, Bull/Bear, -X, -ify).

## 6. Verification gate (before committing — NOT yet done)

Pressure-test status: ✅ = checked live; ⬜ = human-verify required.

- ✅ **TW fintech collision** — "Miru" no finance collision in the live TW field (富果/元大/籌碼K線/財報狗… swept); "Liran" no finance collision.
- ✅ **Latin-name product collision** — "Miru"/"Claria" domains are for-sale (no product); Clari/Akari/Lumo occupied (rejected).
- ⬜ **TIPO trademark** — search **classes 9 (app) / 35 / 36 (financial info) / 42 (SaaS)** for "Miru" and the fallback "Liran" (Miru is a common JP word → check carefully).
- ⬜ **Domains** — `miru.ai` / `.com` are **premium for-sale** (cost, not free); evaluate `getmiru`/`miruapp`/`.com.tw`. Register `miru` `.com.tw` for local trust.
- ⬜ **App Store + Google Play** (TW storefront, en + zh listings) — exact + near-match.
- ⬜ **LINE Official Account** name + `@id` (primary TW channel), IG / X / Threads / YouTube handles.
- ⬜ **經濟部商業司 (MOEA)** company-name check if incorporating under the brand.
- ⬜ **Native sound-check** — run Miru + 藍鵲 past 3–5 native speakers for unintended readings.

## 7. Open decisions

1. **Miru vs Liran** — Miru (cleaner, global, JP-rooted) is the pick; Liran is the pinyin-rooted
   fallback. Resolve once the TIPO/app-store gate (§6) runs.
2. **"Miru" reads Japanese, not Chinese** — a fine, positive signal in Taiwan (JP aesthetics carry
   weight), but a conscious choice: a JP-rooted Latin mark under a Chinese flagship.
3. Final palette, logotype, and the 藍鵲 mascot visual design — deferred to the frontend deliverable
   (the web and `docs/mobile/` apps); the `design-taste-frontend` skill can drive it.
