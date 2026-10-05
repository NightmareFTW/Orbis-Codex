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
| MECH-STAT-02 | Final ATK/HP/DEF = (base + base·Σ%(gear %, imprint %, static set %) + Σflat(gear, artifact)) · (1 + final multipliers). %-stats always apply to the **base** stat. Rates (CC, CD, EFF, ER, SPD) are additive. | Fribbels `StatCalculator.java` | `community`; reproduces 36/36 displayed stats on 4 Hero Info captures (2026-10-04) | Used by the final-stat composition check (`roster/composition.py`, floor rule MECH-STAT-08); mismatches are warnings. |
| MECH-STAT-03 | Base stats used are Lv60 6★ fully awakened. BBK = ATK 1138, HP 5871, DEF 462, SPD 111, CC 23%, CD 150%, DAC 3%. | Fribbels, e7calc, epic7db agree | `community` (3 sources) | |
| MECH-STAT-04 | 5★-origin hero base ATK/HP/DEF are determined by class × horoscope (table). | e7calc `stat-tables.ts`; identical values observed across heroes | `community` | Useful sanity check for catalog base stats. |
| MECH-STAT-05 | Crit Chance display is capped at 100%. | Fixture (100.0% shown with ≥100% of components); Straze captures 2026-10-04 (composed 110%, shown 100.0%) | `verified` (2 heroes) | Excess is wasted. A capped value is drawn in red; on the Equipment tab its ▲ is cap − base (77.0% = 100 − 23), so MECH-STAT-06 holds on the capped value. |
| MECH-STAT-06 | On the hero **Equipment tab**, each stat shows its final value and an orange "▲" bonus: **final − ▲ = the Lv60 6★ awakened base stat** of the catalog (everything but the base is in ▲: gear, sets, artifact, imprint, EE). A stat whose bonus is 0 shows no ▲. | User captures 2026-10-04: Renoa c1193, Haru c1192, Straze c1034 — 27/27 stats equal Fribbels base stats exactly | `verified` (3 heroes) | Used by `e7 roster scan` as a cross-check of OCR and catalog; also confirms MECH-STAT-03 base stats for these heroes. |
| MECH-STAT-09 | Crit Damage used in damage is capped at **350%** (some skill boosts are uncapped); the stat screen can show more (fixture 357%). | e7calc (`Math.min(…, 3.5)`), Fribbels (`min(350, chd)`) | `community` | |
| MECH-STAT-07 | Speed set +25%, Revenge +12% (+0.5% per 1% HP lost), Reversal +15%, Weakening +15% are % of **base** speed. | Stove text (magnitudes) + Fribbels (base-relative); captures 2026-10-04 | magnitudes `verified`, application `verified` for the Speed set (2 captures) | Speed set = 25% of base reproduces Lady of the Scales (+29.25) and Ainz (+28.75); 25% of the total (≈ +54) is ruled out. |
| MECH-STAT-08 | Hero Info and the Equipment tab show a flat stat (ATK, DEF, HP, SPD) as the **floor** of its exact MECH-STAT-02 total (e.g. Haru DEF 1139.97 → 1139, Lady of the Scales HP 18116.50 → 18116). Rates show one decimal of percent and matched the exact sum on every capture. | User captures 2026-10-04: Haru c1192, Lady of the Scales c6005, Ainz c1155, Straze c1034 (`spikes/m7_stat_composition.py`): floor(total) fits 16/16 flat stats, round-half-up 10/16 (6 discriminating cases) | `verified` (4 captures) | Used by `roster/composition.py`. Floor once per Σ%-of-base term also fits: NV-24. |

## 2. Equipment

| ID | Rule | Source | Status | Notes |
|---|---|---|---|---|
| MECH-GEAR-01 | Weapon main = flat ATK, Helmet = flat HP, Armor = flat DEF; Necklace/Ring/Boots have variable main stats. | User brief; Fribbels data | `community` | |
| MECH-GEAR-02 | Lv90 +15 mains: Weapon ATK 525, Helmet HP 2835, Armor DEF 310, ATK% 65%, Crit Damage 70%. | Fixture; Fribbels sample (525, 2835) | `verified` for these values (pending fixture confirmation) | Full main-stat table by level/grade/enhance still needed. |
| MECH-GEAR-03 | Up to 4 substats; no duplicate substat types; no substat equal to the main stat type. | Common knowledge; Fribbels data model | `community` | Roster validation: warning (SPEC D31: community rules never block a save). |
| MECH-GEAR-04 | Substat roll ranges per item level; reforge (Lv85→Lv90) adds fixed amounts per roll; modification changes one substat. | Fribbels `reforge.js` tables | `community` | Used for plausibility warnings, not hard errors. |
| MECH-GEAR-05 | Set piece counts: 2-piece = Health, Defense, Critical, Hit, Resist, Unity, Immunity, Penetration, Torrent, Pursuit, Fervor; 4-piece = Attack, Speed, Destruction, Lifesteal, Counter, Rage, Revenge, Injury, Protection, Reversal, Riposte, Warfare, Weakening. | Fribbels `enums/Set.java`; Hero Info captures 2026-10-04 | 2-piece `verified` for Health, Immunity, Hit, Critical, Pursuit, Penetration (2 pieces gave one active-set icon each); others `community` | 4 pieces gave exactly one icon for Destruction, Speed, Rage: consistent with 4-piece, not proof (NV-30). |
| MECH-GEAR-06 | Set effects (official text, 2026-10-03): Attack +45% ATK · Health +20% HP · Defense +20% DEF · Speed +25% SPD · Critical +12% CC · Destruction +60% CD · Hit +20% EFF · Resist +20% ER · Unity +8% dual attack chance · Counter 30% chance to counterattack when attacked · Lifesteal absorbs 20% of damage dealt · Rage +30% damage vs debuffed targets · Immunity: immunity 1 turn at battle start · Penetration: single attacks penetrate 15% DEF · Torrent: −10% HP, +10% damage dealt · Injury: reduces target max HP by damage dealt up to 6% (12% single) · Protection: 12% max-HP barrier to all allies for 2 turns at battle start · Revenge: +12% SPD +0.5% per 1% HP lost · Reversal: +15% SPD, +50% CR on revive · Riposte: 70% counter after evading · Warfare: +20% HP, resets cooldowns at battle start · Pursuit: +20% additional damage · Fervor: +20% damage on the attack after an extra turn starts · Weakening: +15% SPD, +15% chance to inflict debuffs. | **Stove** `equip-list` | `verified` | Static (stat) vs in-combat effects are modelled separately. Note: Counter is 30% now; older community sources say 20%. |
| MECH-GEAR-07 | Static set bonuses stack per completed set (e.g. 3× Health 2-piece = +60% HP). | Fribbels; Stove combos like `max_hp,max_hp,max_hp` | `community` | |
| MECH-GEAR-08 | Allowed main stats: weapon = flat ATK, helmet = flat HP, armor = flat DEF; necklace = Crit Chance, Crit Damage, ATK%, HP%, DEF%, flat ATK/HP/DEF; ring = Effectiveness, Effect Resistance, ATK%, HP%, DEF%, flat ATK/HP/DEF; boots = Speed, ATK%, HP%, DEF%, flat ATK/HP/DEF. | Fribbels `app/js/lib/dialog.js` main-stat selectors; MECH-GEAR-01 | `community` | Roster validation: warning (SPEC D31). |
| MECH-GEAR-09 | Weapons cannot have DEF/DEF% substats; armor cannot have ATK/ATK% substats. | Fribbels `modificationFilter.js` | `community` | Roster validation: warning (not verified in game). |
| MECH-GEAR-11 | Hero Info gear panel: every gear value (main stat, substats) and the EE value has a stat icon on its left with the same artwork as the nine stat-label icons (Attack … Dual Attack Chance). Substat icons are semi-transparent grey (0.85–0.95× a label icon), main-stat icons opaque white (0.95–1.0×), the EE icon ≈ 0.65×. A percent value on ATK/DEF/HP is the percent stat. An empty substat slot shows no icon. | User captures 2026-10-04 (5 Hero Info captures × scales 0.64/1.0/1.28) | `verified` (5 captures, Stove PC, English) | Read by `vision/stat_icons.py` (templates cut from the same capture, never stored): 149/149, 0 wrong. Heart (HP) and shield (DEF) are the closest pair. |
| MECH-GEAR-12 | Gear frame: a red background with a gold corner ornament, or a purple background without it. Community: red = Epic, purple = Heroic. At +0 the red pieces showed 4 substats and the purple one 3. | Captures 2026-10-04 (30 pieces); community | colours and ornament `verified`; grade mapping `assumed` | `vision/gear_panel.py` reports the colour; both cues must agree, else REVIEW (Politis helmet: mixed red/purple, NV-26). |
| MECH-GEAR-13 | "Average Equipment Score: N" = floor(sum of the 6 piece scores / 6). | 5 of 5 captures | `assumed` | Warning only; the piece score formula is unknown (NV-25). |
| MECH-GEAR-14 | Gear item icon: item level at its top-left, a red "+N" pill at its top-right on every enhanced piece (none at +0), the piece score under it, the set badge at its bottom-right. | Captures 2026-10-04 (78 enhanced, 12 at +0) | `verified` (5 captures) | +0 is taken only when no "+N" is read and no pill is seen. |
| MECH-GEAR-15 | Hero Info sets: each piece shows its set as a badge with the official Stove set-icon artwork (shield, gold rim, red or blue fill); the CP row shows one round icon per completed set. A hero without gear shows neither (nor the average score line). | Captures 2026-10-04 (9 sets seen); Stove `wearingStatus` set icons | `verified` for the 9 sets seen; artwork `assumed` for the other 15 (NV-30) | Read by `vision/sets.py` against the Stove icons cached by `e7 catalog sync`. |
| MECH-GEAR-10 | Minimum number of substats by enhancement: +3 → 1, +6 → 2, +9 → 3, +12 → 4 (non-normal gear starts with ≥ 1 at +0). | Fribbels `itemAugmenter.js` (`fixProblemItem`) | `community` | Roster validation: warning. |
| MECH-HERO-01 | Max level = stars × 10 (6★ → Lv. 60). | Common knowledge; 9/9 user captures (Lv. Max/60 with 6★, Lv. Max/50 and Lv.5/50 with 5★) | `community` | Roster validation: warning. |
| MECH-HERO-02 | Awakening level does not exceed the star count. | Our assumption (not checked against the game) | `assumed` | Roster validation: warning. NV-18. |
| MECH-HERO-03 | The star row next to the hero name shows the awakening: an awakened star has a **darker centre**, and the hero is awakened up to the last such star. | User 2026-10-04; captures: Renoa, Haru, Straze (6★, all six stars dark-centred) and Closer Charles (5★, not awakened, all stars solid yellow) | all-or-nothing `verified` (8 captures); partial awakening `verified` once (Uncharted Pioneer Politis 5★, only the first star awakened); filling from the left `assumed` (1 sample) | Read by `vision/star_row.py`: an awakened star has a yellow-orange top, a magenta-pink lower half and a dark centre; a plain star is uniform yellow (NV-20). |

## 3. Artifacts, Exclusive Equipment, Imprint

| ID | Rule | Source | Status | Notes |
|---|---|---|---|---|
| MECH-ART-01 | Artifacts give two flat stats out of ATK, DEF, HP (most are ATK + HP; some ATK + DEF or DEF + HP). +30 value = 13 × +0 value. | **Stove** (`ability_*` vs `enhance_ability_*`, positional), Fribbels `{attack, defense, health}` | `verified` (endpoints) | Stove's two fields are positional (SPEC D26, DATA_SOURCES §1). |
| MECH-ART-02 | Between +0 and +30 the flat stats scale linearly with level and keep one decimal (game shows integers). | Fribbels `artifact.js`; captures 2026-10-04 | `community`; consistent within ±1 at +4, +15 and +30 (4 artifacts) | E.g. Hostess +18 → ATK 172.2, HP 262.4. |
| MECH-ART-03 | Artifact effect values: Stove gives `lv01` and `lv_max` per placeholder. Intermediate steps (skill level per +3 enhancements) | Stove (endpoints) | endpoints `verified`, steps `assumed` | Needs per-level table (epic7db/in-game). |
| MECH-ART-04 | An artifact with a class lock (Stove `job_code` ≠ `NN`) can only be equipped by heroes of that class. | Stove `job_code`; in-game artifact text | `community` (rule); lock data `verified` from Stove | Roster validation: warning, message names the lock fact's status. |
| MECH-ART-05 | Artifact "Lv.X/Y" vs "+N": X = 1 + floor(N/3), "Max" when X = Y (seen: Max/6 +15, Max/11 +30, 2/10 +4, 1/6 +0). The "+N" pill is red (+15, +30) or orange (+4), absent at +0. | Community rule; user captures 2026-10-04 | `community` | `vision/gear_panel.py` only lowers confidence on a mismatch (NV-27). |
| MECH-EE-01 | EE = hero-specific stat(s) + one chosen option modifying a given skill; Stove gives option code + target skill + usage share only. Stat **type** per hero from Fribbels `ex_equip` (BBK = Crit Chance, confirmed by the user). | Stove; Fribbels; user | structure `verified`, BBK stat type `verified` | Stat value range unknown: Fribbels lists `cri 0.06` for BBK while the user's EE shows 12% (NV-08). Option text from epic7db/in-game. Hero Info shows the EE value with a small stat icon and the EE name left of the artifact: Straze c1034 "Star Extinction" 12%, Crit Hit Chance icon (2026-10-04); with its sets confirmed, its CC without the EE is 98% < 100% shown, so the EE adds ≥ 1.95 pp CC (supports `ee.stat = cri`). |
| MECH-IMP-01 | Memory Imprint (self/"concentration") adds a stat by grade C…SSS; BBK = ATK% 6/9/12/14/16/18%. | Fribbels `self_devotion`; Fixture (SSS = 18%) | BBK `verified`, others `community` | The team imprint has its own stat and values, not in our sources yet (NV-21). |
| MECH-IMP-02 | A hero uses its imprint either on **itself** or on the **team**, and the hero screens (Equipment tab, Hero Info) show the active one: <ul><li>self: crosshair icon;</li><li>team: four-square icon in a diamond (top, left, right, bottom); **only the heroes in the lit positions receive the bonus** (user, 2026-10-04). Seen: all four lit (Renoa, Haru, Politis…), left + bottom (Closer Charles), right + bottom ("Health +15%" crop). Unlit squares are the same colour at about 30% brightness;</li><li>no imprint: "Locked" with a padlock and grey squares;</li><li>the grade letter (B…SSS) is drawn on the icon; icon, letters and imprint text take the grade's colour (B blue, SSS red seen; NV-28);</li><li>geometry (13 icons): team width/height 1.18–1.31, self 0.97–1.02.</li></ul> A self imprint always gives the hero's own imprint stat. | User 2026-10-04; captures: Straze (crosshair, ATK +21% = its own SSS value), Renoa, Haru, Closer Charles (squares, stats that are not their own imprint) and 4 icon crops | `verified` (7 captures) | Mode stored on the build (SPEC D42); read by `vision/imprint_icon.py` (grade only from the observed colour × letter-count pairs, never OCR; the OCR text "Locked" decides, the icon is a consistency check; NV-29). Community names: Imprint Concentration (self), Imprint Release (team). |
| MECH-IMP-03 | A team imprint is not part of the hero's own displayed stats. | Captures: Closer Charles (team EFF +6%, no gear, EFF 0.0%); the composition check of Haru (team HP +4%) and Lady of the Scales (team ER +15%) fits only without it | `verified` (3 captures) | Whether it applies to the hero itself in battle: NV-22. |

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
| NV-03 | Crushing hit chance and hit-resolution order (MECH-HIT-01/02) | Damage variance | Count hit types in recorded battles |
| NV-04 | Starting CR randomness range in PvP (MECH-CR-02) | Turn-order probability | Speed-tuning observations from recorded battles |
| NV-05 | Defence AI targeting & skill policy, auto-battle attacker policy (D15), turn limit / NPC teams (MECH-ARENA-02..06) | Simulator accuracy | Log battles (Phase 5/6) |
| NV-06 | CP enhancement factor (MECH-CP-02) | CP→percentile calibration | Many heroes from the Fribbels save + Hero Info OCR |
| NV-07 | Artifact effect per-level steps (MECH-ART-03) | Artifact effects in sim | epic7db / in-game tooltip |
| NV-08 | EE stat value range (BBK: Fribbels 0.06 vs user's 12% Crit Chance) and option texts (MECH-EE-01) | Roster validation, sim | In-game EE tooltip / epic7db / Fribbels save |
| NV-09 | Dual attack/counter details (MECH-DUAL-01, MECH-CNT-01) | Minor damage | Observation |
| NV-10 | Fixture residuals: with Fribbels base stats, final − "gear contribution" leaves ATK +35, DEF +15, HP +100, CD +4%, ER +4%, SPD 0, CC 0 for BBK, while imprint (+18% ≈ +205 ATK) and artifact (+18 ≈ +172 ATK / +262 HP) are expected on top | Consistency check design | Inspect `hero_manage_bbk.png` (what "gear contribution" includes) **Partly answered by MECH-STAT-06** (▲ = final − base); still open: the split of ▲ between gear, imprint, artifact and EE. |
| NV-11 | Gear set piece counts (MECH-GEAR-05) and full main-stat table | Validation | In-game tooltips / OCR fixtures |
| NV-12 | How the Weakening set's "+15% chance to inflict debuffs" combines with EFF/ER (MECH-EFF-02) | Debuff landing | Observation / official notes |
| NV-13 | CR tie-break when units reach 100% together (MECH-CR-03) | Turn order | Recorded battles |
| NV-14 | Skill multipliers where Fribbels and e7calc disagree (~40 fields, `e7 catalog conflicts --type skill`) | Damage maths | Third source (epic7db skill pages) / in-game skill text |
| NV-15 | Base stats where Fribbels and e7calc disagree (~24 heroes; e7calc values are damage-calc tuned) | Consistency check | epic7db base stats / in-game Hero Info at known gear |
| NV-16 | Which stats the two Stove artifact fields hold for artifacts Fribbels cannot confirm (`ef506`, `ef427`: stored as an assumed ATK/HP reading, D26) | Final stats | In-game artifact screen / OCR |
| NV-17 | Artifact effect values Stove leaves unknown (84 artifacts have a `null` in `effect_levels`, D27) | Predictor (artifact effects) | e7calc `artifacts.ts` (damage-relevant ones) / in-game artifact text at +0 and +30 |
| NV-18 | Whether awakening can exceed the star count, and how awakening steps are gated by stars (MECH-HERO-02) | Roster validation | In-game awakening screen |
| NV-19 | **Answered 2026-10-04 (MECH-IMP-02): the active one, self or team, told by the icon.** Which imprint the hero screens show: on 2 of 3 captures (Renoa "Effectiveness +15%", Haru "Health +4%") it is not the hero's own imprint in Fribbels' table, so it is probably Imprint Release (the bonus given to allies) | Roster (imprint field), stat composition | A capture of the imprint screen in both modes |
| NV-20 | Awakening: read by the star classifier (M7); partial awakening seen once (Politis 1/5, first star). Still open: another partly awakened hero to confirm left-to-right filling | Roster | A capture of a partly awakened 6★ hero |
| NV-21 | Team imprint per hero: stat, values per grade and positions. Fribbels' current `herodata.json` has only `self_devotion`; its app's sample hero shows the upstream format `devotion {type, grades, slots 1–4}`. Also: which square is which formation position (the diamond's top/left/right/bottom vs the team slots 1–4) | Roster (team imprint grade), sim (ally bonuses) | Another catalog source (EpicSevenDB / epic7db) or the screen (value + lit squares) |
| NV-22 | Whether a team imprint also applies to the hero itself in battle (MECH-IMP-03) | Sim | In-battle stats / official notes |
| NV-23 | Stat icons and anchors on other clients, languages and window sizes (only English Stove PC captures seen; Portuguese labels unknown) | Gear import (M7) | Captures with the user's other settings |
| NV-24 | Whether the flat-stat floor (MECH-STAT-08) applies once to the total or separately per term | Composition check (a false mismatch when fractional parts add up to ≥ 1) | A capture where they do |
| NV-25 | Gear piece score formula and the average rule (MECH-GEAR-13) | Validation of the OCR'd scores | More captures / Fribbels save scores |
| NV-26 | Frame colour vs grade (MECH-GEAR-12) and the mixed red/purple frame (Politis helmet) | Gear grade field | The Equipment Details popup (grade as text) |
| NV-27 | Artifact level per enhancement (MECH-ART-05) | Artifact effect level | In-game artifact screen at several +N |
| NV-28 | Imprint grade letters and colours other than B (blue, 1 letter) and SSS (red, 3 letters) | Imprint icon reader (grade unknown until seen) | Captures of heroes with grades A, S, SS, C, D |
| NV-29 | Locked imprint icon (one zoomed sample); whether a self-mode Locked exists | Imprint icon reader (the OCR text decides) | More Locked captures |
| NV-30 | Set badges of the 15 sets not seen in game (artwork assumed = Stove icon); whether a duplicated 2-piece set shows one CP icon per completed set; the CP-row icon order; the Equipment tab's "No set effect" line | Set recognition, pieces vs icons check | Captures wearing those sets / 3× Health |
| NV-31 | Fribbels importer data `d` per unit: 5 on the four heroes whose imprint the captures show as SSS, 1 on the two with B, 1–9 overall; maybe the imprint (devotion) level on a scale that depends on the hero | Imprint grade from importer data (not used) | More heroes with a known imprint grade (Hero Info captures) |
| NV-32 | Fribbels importer data `s` per unit: three numbers (e.g. 5/5/5, 6/3/6, 8/–/7) that look like skill enhancement counts | Skill enhancements from importer data (not used) | Skill Enhance screens of 2–3 heroes |
