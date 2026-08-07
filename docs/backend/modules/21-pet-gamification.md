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

Our pet catalog (`config`, code-defined — not a DB table) is a fixed set of **15 pets** across
**4 rarities** — common / uncommon / rare / legendary. Each entry: `pet_id`, `name`, `name_zh`,
`rarity`, bilingual `description`. TW/finance flavor lives only in names/descriptions — the
藍鵲/Sisi flagship ([../../brand.md](../../brand.md) §3a) plus a wider 台灣特有種 line (石虎 / 黑面琵鷺 /
櫻花鉤吻鮭 / 台灣藍鵲 / 帝雉 / 台灣黑熊 / 水鹿 / 台灣獼猴 / 大冠鷲 / 台灣山羊 / 白面鼯鼠 / 台灣野豬 /
台灣長鬃山羊 / 穿山甲…). **No combat stats** — the only per-owned-pet state is `level` + `xp`;
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
  thresholds (`pets.config.PITY_RARE_AT` / `PITY_LEGENDARY_AT` = **30 / 50**, chosen for a
  reasonable pulls-to-legendary at our cadence). Counter resets on a rare/legendary hit
  (`SqlAlchemyPetStore._roll_rarity`). **Not yet built:** any loyalty perk that lowers the
  thresholds for equipping a higher-rarity pet — the current implementation applies the same
  fixed thresholds to every pull regardless of what is equipped.
- **Duplicate** → the pull instead grants **+XP** to the already-owned pet (`pets.xp_per_duplicate`),
  so a dup is never wasted.
- **First-ever pet auto-equips.**

## 4. XP & leveling

- Max level + XP curve are **our** config (`pets.config.MAX_LEVEL` = 30, `XP_PER_LEVEL` = 100
  flat); level-ups roll over surplus XP (`SqlAlchemyPetStore._apply_xp`). Values are ours to
  tune, not inherited.
- **XP sources (store methods exist; only the duplicate-pull path is actually wired):**
  (a) **chatting** — `PetStore.grant_chat_xp` grants XP to the *equipped* pet and bumps
  `chat_count`; (b) **login streak** — `PetStore.register_login` runs the consecutive-day streak
  logic; (c) **duplicate pulls** — `PetStore.pull` awards `XP_PER_DUPLICATE` on a dup, and this
  path IS wired (every `POST /pets/pull` call). **Not yet built:** nothing in the turn-lifecycle
  or auth/login path calls `grant_chat_xp` or `register_login` — no router, no
  `api/turn_driver.py` hook, no post-login handler invokes either method today, so a real chat
  turn or login currently grants no pet XP/streak in the running app even though the store logic
  is implemented and unit-tested (`tests/pets/test_store.py`).
- **Milestone bonus pulls** — crossing configured levels (`pets.config.LEVEL_MILESTONE_PULLS =
  {5: 1, 10: 2, 20: 3}`) and every `CHATS_PER_BONUS_PULL` (25) chats / every
  `STREAK_DAYS_PER_BONUS_PULL` (7) day streak grant `available_pulls`. One-time per milestone
  (`_apply_xp`'s crossing check makes this idempotent on retry — see its docstring). Reachable
  only once chat/login wiring above exists.
- All XP/pull mutations are **atomic upserts** on `user_pet_stats` / `user_pets` (concurrency
  §10) — parallel turns on multiple devices can't lose an update or double-grant.

## 5. Reactive companion in chat (design intent — tone modulation is real, SSE reactions are not)

- **Tone modulation — real, wired.** `pets/persona.py`'s `pet_hint_for(pet)` renders a small
  rarity/level-scaled **persona hint** string (e.g. common = a light encouraging closing line;
  legendary = warmer, more companion-like phrasing; level ≥10 adds a friendlier note). It changes
  *how warmly* the assistant phrases things — **never what data, score, or caveat it produces** —
  and the module has no import of `accounts`/entitlements/tools by design.
  `agent.prompts.PromptLayers.pet_hint` is the seam this string is meant to occupy, appended
  *after* the governance prompt **and the agent personality** ([modules/23](23-agent-personality.md)
  §1: governance → personality → skill → pet), so it can never override guardrails
  ([modules/05](05-agent-runtime.md)) — `guardrails.check_output` runs on the final text
  regardless. **However, `pet_hint_for` is not yet called from `orchestrator/orchestrator.py`** —
  today's `PromptLayers` are built with `personality` set but `pet_hint` always `None`
  (`_skill_prompt_layers`/`default_prompt_layers` never populate it), so the pet hint is proven
  correct by `tests/pets/test_persona.py` in isolation but does not yet reach a live turn's system
  prompt. Wiring it in is a small addition (call `pet_hint_for(active_pet)` and set the field), not
  a redesign.
- **Reaction events (SSE) — not yet built.** No `pet.reaction` / `pet.leveled` / `pet.pull_earned`
  stream-event kind exists in `conversation/models.py` or the event bus today, and no code emits
  one. The companion-avatar reaction UI (thinking / done / level-up celebration / pull-earned
  toast) described here is design intent for a later slice, not implemented.
- **Hard separation from advice (upheld by construction):** the pet mechanism has no path to emit
  analysis, a score, or a recommendation — `pets/` never imports `tools`/`orchestrator`'s
  analysis path, and the one real integration point (`pet_hint_for`) only ever returns a tone
  string that has no room for trading language by construction.

## 6. Onboarding & lifecycle flows

**New user (register) — real, reachable today:**
1. OAuth sign-up completes ([modules/12](12-multi-user-tiers.md)) → `user_pet_stats` seeded
   with `available_pulls = 1` (lazy `ensure_stats`/`_ensure_stats` on first pet-endpoint access,
   or on the first `pull` call if no stats row exists yet).
2. Client shows the **welcome gacha modal** with an animated pull bar → `POST /pets/pull`.
3. Pet revealed (rarity animation), **auto-equipped**, companion appears in chat (contingent on
   the pet-hint wiring gap in §5 above — the persona hint is not yet reaching the live prompt).

**Existing user (return) — store method exists, not yet called on login:**
`PetStore.register_login` implements the streak logic (yesterday → `streak_days++`; every
7-day streak → +1 pull; idempotent same-day), but **no login/auth code path calls it** — see the
gap noted in §4. Once wired, this is the intended flow: accrued XP/pulls from prior chats are
already applied; if `available_pulls > 0`, the client surfaces a "you have a pull" nudge.

**Ongoing loop (design intent, pending the §4/§5 wiring gaps):** chat → equipped pet gains XP →
level-ups + milestone pulls → pull new pets → equip favorite. `GET /pets/progress` (real, wired)
returns streak, chat_count, available_pulls, pity thresholds, next milestones, and collection
completion (N/total) from whatever state the stats row currently holds.

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
- **Guardrails supersede persona (by design, pending §5's wiring gap).** `pet_hint_for`'s output
  is designed to be appended after the governance prompt and subject to `guardrails.check_output`
  — it can never introduce buy/sell language, a target, or drop a caveat, and this is proven by
  `tests/pets/test_persona.py` today even though the hint isn't yet reaching a live prompt.
- **Non-advice separation.** The companion never speaks as an analyst — `pets/` has no path to
  emit analysis. The dedicated SSE reaction channel/UI region described in §5 is not yet built.
- **Per-user isolation + concurrency.** Pet state is `user_id`-scoped; all XP/pull/equip
  mutations use atomic upserts / reserve-before-act ([16](16-infrastructure.md) §10).
