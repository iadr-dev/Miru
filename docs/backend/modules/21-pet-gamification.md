# Module: Pet & gamification (`backend/src/pets/`)

A **cosmetic companion + engagement layer**: on register the user pulls a pet from a gacha,
the equipped pet levels up as they use the product, and it appears as a **reactive companion**
in the agent chat. The gacha + pity + duplicate→XP *pattern* is a well-worn engagement model; the
catalog, tuning, and mechanics below are **our own** (values are our config, not inherited defaults).

**Three product decisions (fixed, confirmed with the user):**
1. **Free guaranteed pull on register** — a welcome modal with an animated gacha bar; every new
   user gets exactly **one free pull** and the first pet auto-equips.
2. **Cosmetic + companion only** — pets grant **no data access, no quota, no analysis power**.
   They are **fully decoupled from billing, tiers, entitlements, and governance**. Rarity and
   level change only *tone/flavor*, never *what data or advice* the user gets.
3. **Reactive companion in chat** — the equipped pet's avatar reacts to chat events (thinking,
   level-up, a big move in a watched symbol) and lightly flavors the assistant's tone. It
   **never gives investment advice** and is **clearly separated** from the non-trading analysis.

> **Why decoupled from entitlements matters.** Coupling a pet to paid data or a "better answer"
> would turn a cosmetic into a pay-to-win investment tool — a governance and fairness problem.
> Keeping pets purely cosmetic means the whole system sits *outside* the §4 entitlement gate and
> the guardrails' analysis path; it can never affect a trading-relevant output. This is the
> single most important invariant in this module.

---

## 1. Catalog & rarity

Our pet catalog (`config`, code-defined — not a DB table) is a fixed set across **4 rarities** —
common / uncommon / rare / legendary. Each entry: `pet_id`, `name`, `name_zh`, `rarity`, bilingual
`description`. TW/finance flavor lives only in names/descriptions — the 藍鵲/Sisi flagship
([../../brand.md](../../brand.md) §3a) plus a 台灣特有種 line (石虎 / 黑面琵鷺 / 櫻花鉤吻鮭…). **No combat
stats** — the only per-owned-pet state is `level` + `xp`;
rarity is purely a drop-weight + a companion-persona tier.

## 2. Data model (relational — Postgres/SQLite, **not** the market-data store)

Pet/user state is app state → the app DB (module `db/`), scoped by `user_id` like everything
else. Two tables (full column list in [database-schema.md](../database-schema.md) §8):

- **`user_pets`** — one row per owned pet: `id, user_id, pet_id, is_active (equipped flag),
  level (default 1), xp, obtained_via (pull|admin), timestamps`. Index `(user_id)`,
  `(user_id, is_active)`.
- **`user_pet_stats`** — one row per user: `total_pulls, streak_days, last_login_date,
  chat_count, available_pulls (default 1), pity_counter`.

## 3. Gacha pull

`POST /pets/pull` — weighted random from **our** `pets.rarity_weights` config (common / uncommon /
rare / legendary — starting weights ≈ 50/30/15/5, tuned to our engagement cadence, adjustable without
code). Consumes **1 `available_pulls`** (atomic reserve-before-act, so a double-tap can't double-pull
— [modules/16](16-infrastructure.md) §10). Mechanics:

- **Pity system** — `pity_counter++` each pull; guarantees a rare/legendary at our configured
  thresholds (`pets.pity.rare_at` / `pets.pity.legendary_at` — starting ≈ 30 / 50, chosen for a
  reasonable pulls-to-legendary at our cadence). Equipping a higher-rarity pet lowers the thresholds
  slightly (a loyalty perk). Counter resets on a rare/legendary hit.
- **Duplicate** → the pull instead grants **+XP** to the already-owned pet (`pets.xp_per_duplicate`),
  so a dup is never wasted.
- **First-ever pet auto-equips.**

## 4. XP & leveling

- Max level + XP curve are **our** config (`pets.max_level`, `pets.xp_curve`); level-ups roll over
  surplus XP. Values are ours to tune, not inherited.
- **XP sources:** (a) **chatting** — each completed agent turn grants XP to the *equipped* pet;
  (b) **login streak** — consecutive-day logins; (c) **duplicate pulls**.
- **Milestone bonus pulls** — crossing configured levels (e.g. L5 → +1 pull, L10 → +2) and
  every N chats / every 7-day streak grant `available_pulls`. One-time per milestone.
- All XP/pull mutations are **atomic upserts** on `user_pet_stats` / `user_pets` (concurrency
  §10) — parallel turns on multiple devices can't lose an update or double-grant.

## 5. Reactive companion in chat (the standout integration)

The equipped pet is wired into the agent loop as a **tone modulator + reaction emitter** —
**not** a second speaker and **not** part of the analysis:

- **Tone modulation:** `pets/persona.py` injects a small rarity/level-scaled **persona hint**
  into the assistant's system prompt (e.g. common = a light encouraging closing line; rare =
  slightly richer phrasing; a high level adds a friendlier tone). It changes *how warmly* the
  assistant phrases things — **never what data, score, or caveat it produces**. The pet hint is
  the **lowest** styling layer — appended *after* the governance prompt **and the agent
  personality** ([modules/23](23-agent-personality.md) §1: governance → personality → skill → pet)
  — and **cannot override guardrails** ([modules/05](05-agent-runtime.md)):
  `guardrails.check_output` runs on the final text regardless, so no persona can introduce trading
  language or drop a caveat. (Personality = the base analyst voice; the pet is a lighter nudge on top.)
- **Reaction events (SSE):** the pet avatar reacts via dedicated stream events, rendered in a
  **separate companion UI region**, visually distinct from the analysis:
  - `pet.reaction` — avatar animation cue (thinking / done / a watched symbol moved).
  - `pet.leveled` — level-up celebration mid-stream.
  - `pet.pull_earned` — a milestone granted a pull.
  These are additive frames on the existing envelope ([modules/07](07-conversation-streaming.md),
  [10](10-events-hooks.md)) — the client can ignore them entirely with zero effect on the answer.
- **Hard separation from advice:** the pet **never** emits analysis, a score, or a
  recommendation. Its copy is flavor only ("我在幫你看盤面～" as a spinner line), and it is rendered
  outside the answer/caveat area so a user can't mistake mascot chatter for analysis (§ governance).

## 6. Onboarding & lifecycle flows

**New user (register):**
1. OAuth sign-up completes ([modules/12](12-multi-user-tiers.md)) → `user_pet_stats` seeded
   with `available_pulls = 1` (lazy `_ensure_stats` on first pet-endpoint access).
2. Client shows the **welcome gacha modal** with an animated pull bar → `POST /pets/pull`.
3. Pet revealed (rarity animation), **auto-equipped**, companion appears in chat.

**Existing user (return):** on login, the streak logic runs (yesterday → `streak_days++`;
every 7-day streak → +1 pull); accrued XP/pulls from prior chats are already applied. If
`available_pulls > 0`, the client surfaces a "you have a pull" nudge.

**Ongoing loop:** chat → equipped pet gains XP → level-ups (`pet.leveled`) + milestone pulls
(`pet.pull_earned`) → pull new pets → equip favorite. `GET /pets/progress` returns streak,
chat_count, available_pulls, pity thresholds, next milestones, and collection completion (N/total).

## 7. Endpoints

```
GET  /pets/catalog          full pet catalog (rarity, names, descriptions)
GET  /pets                  my owned pets
GET  /pets/active           my equipped pet
PUT  /pets/{record_id}/equip   equip (atomic: unequip others in one tx)
GET  /pets/progress         streak, chat_count, pulls, pity, milestones, completion
POST /pets/pull             gacha pull (reserve-before-act on available_pulls)
```
All scoped by `user_id`; standard session auth. No tier check anywhere in this module
(cosmetic-only invariant, §governance).

## Governance obligations
- **Fully outside the analysis + entitlement path.** Pets grant no data access, no quota, no
  analytical capability; rarity/level affect only cosmetic tone. Not gated by tier; not part of
  `data_class`; not billed. A pet can never change a trading-relevant output.
- **Guardrails supersede persona.** The persona hint is appended after the governance prompt
  and is subject to `guardrails.check_output` — it can never introduce buy/sell language, a
  target, or drop a caveat. Pet reaction copy is flavor only, rendered outside the analysis area.
- **Non-advice separation.** The companion never speaks as an analyst; its SSE events are in a
  distinct channel/UI region so mascot chatter is never mistakable for market analysis.
- **Per-user isolation + concurrency.** Pet state is `user_id`-scoped; all XP/pull/equip
  mutations use atomic upserts / reserve-before-act ([16](16-infrastructure.md) §10).
