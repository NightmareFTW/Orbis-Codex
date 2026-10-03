# Game mechanics

Every formula or rule the code relies on, with source and status. **Never invent numbers**: anything
not backed by a source is `assumed`, surfaces as reduced confidence in the UI and appears in the
"Needs verification" list at the end. Code must reference mechanics by ID (e.g. `MECH-DMG-01`) in
docstrings so a change here can be traced to the code.

Status: `verified` (official / reproduced from an in-game observation) · `community` · `assumed` · `unknown`.
Sources abbreviations: **Stove** = official Strategy Guide API · **Fribbels** = Fribbels E7 Optimizer code/data (commit 4e2f6a0) ·
**e7calc** = tyopoyt/epic7-damage-calc (commit df2cdf6) · **Fixture** = user's BBK screenshots (values as given by the user,
pending confirmation) · **Game8** = game8.co Battle System page (last updated 2020-08-28).

Last reviewed: 2026-10-03.

## 1. Stats and stat composition

| ID | Rule | Source | Status | Notes |
|---|---|---|---|---|
| MECH-STAT-01 | Stats shown on Hero Info (ATK, DEF, HP, SPD, CC, CD, EFF, ER, DAC) are **final** values with gear, static sets, artifact, EE, imprint and awakening applied. They are the source of truth for combat. | User brief; Fixture | `verified` | Components are stored for validation/what-ifs only. |
| MECH-STAT-02 | Final ATK/HP/DEF = (base + base·Σ%(gear %, imprint %, static set %) + Σflat(gear, artifact)) · (1 + final multipliers). %-stats always apply to the **base** stat. Rates (CC, CD, EFF, ER, SPD) are additive. | Fribbels `StatCalculator.java` | `community` | Used only for the optional consistency check; mismatches are warnings. |
| MECH-STAT-03 | Base stats used are Lv60 6★ fully awakened. BBK = ATK 1138, HP 5871, DEF 462, SPD 111, CC 23%, CD 150%, DAC 3%. | Fribbels, e7calc, epic7db agree | `community` (3 sources) | |
| MECH-STAT-04 | 5★-origin hero base ATK/HP/DEF are determined by class × horoscope (table). | e7calc `stat-tables.ts`; identical values observed across heroes | `community` | Useful sanity check for catalog base stats. |
| MECH-STAT-05 | Crit Chance display is capped at 100%. | Fixture (100.0% shown with ≥100% of components) | `verified` (1 sample) | Excess is wasted. |
| MECH-STAT-06 | Crit Damage used in damage is capped at **350%** (some skill boosts are uncapped); the stat screen can show more (fixture 357%). | e7calc (`Math.min(…, 3.5)`), Fribbels (`min(350, chd)`) | `community` | |
| MECH-STAT-07 | Speed set +25%, Revenge +12% (+0.5% per 1% HP lost), Reversal +15%, Weakening +15% are % of **base** speed. | Stove text (magnitudes) + Fribbels (base-relative) | magnitudes `verified`, application `community` | |

## 2. Equipment

| ID | Rule | Source | Status | Notes |
|---|---|---|---|---|
| MECH-GEAR-01 | Weapon main = flat ATK, Helmet = flat HP, Armor = flat DEF; Necklace/Ring/Boots have variable main stats. | User brief; Fribbels data | `community` | |
| MECH-GEAR-02 | Lv90 +15 mains: Weapon ATK 525, Helmet HP 2835, Armor DEF 310, ATK% 65%, Crit Damage 70%. | Fixture; Fribbels sample (525, 2835) | `verified` for these values (pending fixture confirmation) | Full main-stat table by level/grade/enhance still needed. |
| MECH-GEAR-03 | Up to 4 substats; no duplicate substat types; no substat equal to the main stat type. | Common knowledge; Fribbels data model | `community` | Validation rule (error). |
| MECH-GEAR-04 | Substat roll ranges per item level; reforge (Lv85→Lv90) adds fixed amounts per roll; modification changes one substat. | Fribbels `reforge.js` tables | `community` | Used for plausibility warnings, not hard errors. |
| MECH-GEAR-05 | Set piece counts: 2-piece = Health, Defense, Critical, Hit, Resist, Unity, Immunity, Penetration, Torrent, Pursuit, Fervor; 4-piece = Attack, Speed, Destruction, Lifesteal, Counter, Rage, Revenge, Injury, Protection, Reversal, Riposte, Warfare, Weakening. | Fribbels `enums/Set.java` | `community` | Verify against in-game set tooltip (OCR fixture). |
| MECH-GEAR-06 | Set effects (official text, 2026-10-03): Attack +45% ATK · Health +20% HP · Defense +20% DEF · Speed +25% SPD · Critical +12% CC · Destruction +60% CD · Hit +20% EFF · Resist +20% ER · Unity +8% dual attack chance · Counter 30% chance to counterattack when attacked · Lifesteal absorbs 20% of damage dealt · Rage +30% damage vs debuffed targets · Immunity: immunity 1 turn at battle start · Penetration: single attacks penetrate 15% DEF · Torrent: −10% HP, +10% damage dealt · Injury: reduces target max HP by damage dealt up to 6% (12% single) · Protection: 12% max-HP barrier to all allies for 2 turns at battle start · Revenge: +12% SPD +0.5% per 1% HP lost · Reversal: +15% SPD, +50% CR on revive · Riposte: 70% counter after evading · Warfare: +20% HP, resets cooldowns at battle start · Pursuit: +20% additional damage · Fervor: +20% damage on the attack after an extra turn starts · Weakening: +15% SPD, +15% chance to inflict debuffs. | **Stove** `equip-list` | `verified` | Static (stat) vs in-combat effects are modelled separately. Note: Counter is 30% now; older community sources say 20%. |
| MECH-GEAR-07 | Static set bonuses stack per completed set (e.g. 3× Health 2-piece = +60% HP). | Fribbels; Stove combos like `max_hp,max_hp,max_hp` | `community` | |

## 3. Artifacts, Exclusive Equipment, Imprint

| ID | Rule | Source | Status | Notes |
|---|---|---|---|---|
| MECH-ART-01 | Artifacts give flat ATK and HP. +30 value = 13 × +0 value. | **Stove** (`ability_*` vs `enhance_ability_*`), Fribbels | `verified` (endpoints) | Stove's field `ability_defense` is actually HP. |
| MECH-ART-02 | Between +0 and +30 the flat stats scale linearly with level and keep one decimal (game shows integers). | Fribbels `artifact.js` | `community` | E.g. Hostess +18 → ATK 172.2, HP 262.4 (to verify with fixture). |
| MECH-ART-03 | Artifact effect values: Stove gives `lv01` and `lv_max` per placeholder. Intermediate steps (skill level per +3 enhancements) | Stove (endpoints) | endpoints `verified`, steps `assumed` | Needs per-level table (epic7db/in-game). |
| MECH-EE-01 | EE = hero-specific stat(s) + one chosen option modifying a given skill; Stove gives option code + target skill + usage share only. Stat **type** per hero from Fribbels `ex_equip` (BBK = Crit Chance, confirmed by the user). | Stove; Fribbels; user | structure `verified`, BBK stat type `verified` | Stat value range unknown: Fribbels lists `cri 0.06` for BBK while the user's EE shows 12% (NV-08). Option text from epic7db/in-game. |
| MECH-IMP-01 | Memory Imprint (self/"concentration") adds a stat by grade C…SSS; BBK = ATK% 6/9/12/14/16/18%. | Fribbels `self_devotion`; Fixture (SSS = 18%) | BBK `verified`, others `community` | Release imprint (team-wide) not modelled yet. |

## 4. Damage

| ID | Rule | Source | Status | Notes |
|---|---|---|---|---|
| MECH-DMG-01 | Offensive power = ((ATK_eff · rate + flatMod) · **1.871** + flatMod2) · pow · (1 + Σ skill-enhance) · elemental · damage-up mods. `flatMod` = HP/DEF/SPD scaling terms; `flatMod2` = artifact/extra flat. | e7calc `damage.service.ts`, Fribbels kernel | `community` (2 sources) | Constant 1.871 has no official source. |
| MECH-DMG-02 | Defensive factor = (1 − dmgReduction)(1 − dmgTransfer) / (1 + DEF_eff · (1 − penetration) / 300). | e7calc `target.ts`; Fribbels | `community` | |
| MECH-DMG-03 | Effective HP = HP · (1 + DEF/300). | Fribbels kernel; e7calc EHP tool | `community` | |
| MECH-DMG-04 | Penetration sources combine multiplicatively: Π(1 − p_i·(1 − penResist)); Penetration set 15% applies to single-target attacks only. | e7calc; Stove set text | `community` / set `verified` | |
| MECH-DMG-05 | Hit-type damage: critical × min(CD, 350%) (+uncapped boosts) · crushing × 1.3 · normal × 1.0 · miss × 0.75. | e7calc, Fribbels | `community` (2 sources) | |
| MECH-DMG-06 | ATK buffs: Attack Up +50%, Greater Attack Up +75%, Attack Down −50% (multiplicative on ATK); Defense Up +60%, Defense Break −70% on target DEF; "Target" debuff +15% damage taken. | e7calc `constants.ts` | `community` | Check vs in-game buff tooltips. |
| MECH-DMG-07 | Torrent +10%, Rage +30% vs debuffed, Fervor +20%, Pursuit +20% (additional damage) — additive damage-up mods. | Stove text; e7calc constants | magnitudes `verified`, stacking `community` | |

## 5. Hit determination and elements

| ID | Rule | Source | Status | Notes |
|---|---|---|---|---|
| MECH-ELEM-01 | Fire > Earth > Ice > Fire; Light ↔ Dark mutually advantaged. (API codes: `fire`, `wind` = Earth, `ice`, `light`, `dark`.) | User brief; Stove codes | `community` | |
| MECH-ELEM-02 | Elemental advantage: +10% damage, +15% crit chance. | e7calc (1.1); community guides | damage `community` (2), crit `community` (1) | |
| MECH-ELEM-03 | Elemental disadvantage: 50% chance of a **Miss**; a miss cannot crit/crush and cannot inflict EFF-based debuffs. | Community guides (gamepress, gamesadda) | `community` | Some skills ignore elemental disadvantage. |
| MECH-HIT-01 | Crushing hit chance in neutral/advantage. | — | **`unknown`** | Community claims conflict (e.g. "75% on advantage"). Parameter must be `assumed` until measured. |
| MECH-HIT-02 | Order of resolution (miss → crit → crushing → normal). | — | `assumed` | |

## 6. Debuffs

| ID | Rule | Source | Status | Notes |
|---|---|---|---|---|
| MECH-EFF-01 | P(land) = P(not miss) · skill_chance · clamp(1 + EFF − ER, 0, **0.85**) (EFF/ER as fractions). The 85% cap = "15% minimum resist". | e7calc effectiveness checker; multiple guides | `community` | Effects that "cannot be resisted"/"ignore ER" bypass it. |
| MECH-EFF-02 | Weakening set "+15% chance to inflict debuffs": how it combines with MECH-EFF-01. | Stove text | magnitude `verified`, formula `assumed` | |
| MECH-EFF-03 | Each debuff in a multi-debuff skill rolls independently. | Community guides | `community` | |

## 7. Turn order (Combat Readiness)

| ID | Rule | Source | Status | Notes |
|---|---|---|---|---|
| MECH-CR-01 | Each unit has a CR gauge 0–100%; it fills at a rate proportional to its current Speed; the first to reach 100% acts; after acting CR resets to 0. CR push/pull add/subtract percentage points. | Community standard; e7calc speed tools | `community` | |
| MECH-CR-02 | Starting CR = random uniform 0–5% per unit (so +5% speed guarantees order). | e7calc speed tuner/solver | `community` | One web summary claims ±5% in PvP → conflict; keep as parameter. |
| MECH-CR-03 | Tie-breaking when two units reach 100% simultaneously. | — | `unknown` | |
| MECH-CR-04 | Extra turn: the unit acts again immediately. | Skill text | `community` | Interaction with CR display to verify. |

## 8. Extra actions

| ID | Rule | Source | Status | Notes |
|---|---|---|---|---|
| MECH-DUAL-01 | Dual Attack: when a hero attacks with its basic skill (S1), with probability = DAC a random ally also attacks the same target with its S1. Base DAC 3–5% by hero; Unity set +8%. | Fribbels data (DAC); Stove (Unity); community | trigger rules `assumed`, magnitudes `verified`/`community` | Low impact; verify damage modifier and ally selection. |
| MECH-CNT-01 | Counterattack uses S1; Counter set 30% when attacked; Riposte 70% after evading. | Stove (set text) | `verified` (chances); details `assumed` | Can counters be countered/dual-attacked: `unknown`. |

## 9. Souls

| ID | Rule | Source | Status | Notes |
|---|---|---|---|---|
| MECH-SOUL-01 | Skills generate souls (e.g. "+1 souls"); soulburn spends souls for an enhanced effect. | epic7db skill text | `community` | Soul gauge size/costs per skill to catalog. |
| MECH-SOUL-02 | In Arena the defence AI never uses soulburn. | Game8 (2020) | `community` (dated) | Attacker (me) can soulburn: `community`. |

## 10. Combat Power

| ID | Rule | Source | Status | Notes |
|---|---|---|---|---|
| MECH-CP-01 | CP_gear = ((ATK·1.6 + ATK·1.6·CC·CD)·(1 + (SPD − 45)·0.02) + HP + DEF·9.3) · (1 + (ER + EFF)/4) (CC, CD, ER, EFF as fractions). Excludes skill enhancements. | Fribbels (README: "doesn't take skill enhances into account") | `community` | |
| MECH-CP-02 | In-game CP = CP_gear · k(skill enhancements, …). Fixture BBK: CP_gear = 109,033 vs in-game 141,750 → k = 1.3001 for a (presumably) fully enhanced hero. | Fixture + MECH-CP-01 | `assumed` (1 sample) | Need more samples (Fribbels save will give many). Basis for CP→percentile calibration in Phase 2. |

## 11. Arena (normal, asynchronous)

| ID | Rule | Source | Status | Notes |
|---|---|---|---|---|
| MECH-ARENA-01 | The defence is played by the game AI; the attacker plays manually or on auto. The user plays mostly on **auto**, so both sides are AI-driven by default. | User brief / answers | `verified` | Whether auto-battle uses the same policy as the defence AI: `unknown`. |
| MECH-ARENA-02 | AI targeting: prefers targets it has elemental advantage over (except invulnerable units); units whose skill ignores elemental disadvantage pick a random target. | Game8 (2020) | `community` (dated, low confidence) | |
| MECH-ARENA-03 | AI targeting also prefers low-HP targets. | GameFAQs anecdote | `assumed` | |
| MECH-ARENA-04 | AI skill choice (uses S3/S2 when off cooldown?). | — | `unknown` | Core uncertainty; learn from logged battles (Phase 6). |
| MECH-ARENA-05 | Turn limit / sudden death / NPC opponents in the list. | — | `unknown` | Observe in captured battles. |
| MECH-ARENA-06 | Auto-battle policy for the attacker (skill choice/targeting). | — | `unknown` | Default attacker policy = same approximation as the defence AI until data says otherwise (D15). |

## 12. Not yet researched (Phase 4)
Buff/debuff durations and "turn" semantics (start vs end of turn decrement), stacking/priority rules,
immunity vs cleanse ordering, revive rules, barrier stacking, stealth/invincibility/skill-nullifier
interactions, damage share, damage caps per boss/hero passives, Injury reduction cap, CR push from
skills vs speed buffs (Speed Up = +30% speed per e7calc `spdUp 1.3`, `community`).

---

## Needs verification (live list)

| # | Item | Why it matters | How to verify |
|---|---|---|---|
| NV-01 | Stove histogram bin edges (MECH via DATA_SOURCES §1) | All opponent stat percentiles | Compare with the user's own heroes' stats / percentile plausibility; contact Stove? |
| NV-02 | Which population `hero-detail` vs `wearing-status` rankings use | Opponent set/artifact priors | Compare shares across weeks/regions; ask community |
| NV-03 | Crushing hit chance (MECH-HIT-01) | Damage variance | Count hit types in recorded battles |
| NV-04 | Starting CR randomness range in PvP (MECH-CR-02) | Turn-order probability | Speed-tuning observations from recorded battles |
| NV-05 | Defence AI targeting & skill policy (MECH-ARENA-02..04) | Simulator accuracy | Log battles (Phase 5/6) |
| NV-06 | CP enhancement factor (MECH-CP-02) | CP→percentile calibration | Many heroes from the Fribbels save + Hero Info OCR |
| NV-07 | Artifact effect per-level steps (MECH-ART-03) | Artifact effects in sim | epic7db / in-game tooltip |
| NV-08 | EE stat value range (BBK: Fribbels 0.06 vs user's 12% Crit Chance) and option texts (MECH-EE-01) | Roster validation, sim | In-game EE tooltip / epic7db / Fribbels save |
| NV-09 | Dual attack/counter details (MECH-DUAL-01, MECH-CNT-01) | Minor damage | Observation |
| NV-10 | Fixture residuals: with Fribbels base stats, final − "gear contribution" leaves ATK +35, DEF +15, HP +100, CD +4%, ER +4%, SPD 0, CC 0 for BBK, while imprint (+18% ≈ +205 ATK) and artifact (+18 ≈ +172 ATK / +262 HP) are expected on top | Consistency check design | Inspect `hero_manage_bbk.png` (what "gear contribution" includes) |
| NV-11 | Gear set piece counts (MECH-GEAR-05) and full main-stat table | Validation | In-game tooltips / OCR fixtures |
