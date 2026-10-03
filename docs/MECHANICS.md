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
| MECH-GEAR-08 | Allowed main stats: weapon = flat ATK, helmet = flat HP, armor = flat DEF; necklace = Crit Chance, Crit Damage, ATK%, HP%, DEF%, flat ATK/HP/DEF; ring = Effectiveness, Effect Resistance, ATK%, HP%, DEF%, flat ATK/HP/DEF; boots = Speed, ATK%, HP%, DEF%, flat ATK/HP/DEF. | Fribbels `app/js/lib/dialog.js` main-stat selectors; MECH-GEAR-01 | `community` | Roster validation: error. |
| MECH-GEAR-09 | Weapons cannot have DEF/DEF% substats; armor cannot have ATK/ATK% substats. | Fribbels `modificationFilter.js` | `community` | Roster validation: warning (not verified in game). |
| MECH-GEAR-10 | Minimum number of substats by enhancement: +3 → 1, +6 → 2, +9 → 3, +12 → 4 (non-normal gear starts with ≥ 1 at +0). | Fribbels `itemAugmenter.js` (`fixProblemItem`) | `community` | Roster validation: warning. |
| MECH-HERO-01 | Max level = stars × 10 (6★ → Lv. 60). | Common knowledge | `community` | Roster validation: warning. |
