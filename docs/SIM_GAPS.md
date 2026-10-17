# What the simulation does not know yet, and what would settle it

Why this file: the first real comparison of the win chance with a fight was a "100%" and a defeat (SPEC D87, NV-51). The
chance is only as good as what the simulation was told, so this is the list of what it was not told, **ordered by how
much each item moves the result**, with the one thing that settles each. Update it whenever one is closed.

Status words as everywhere: `verified` (read in the game), `community` (public data), `assumed` (a stand-in, listed in
`docs/MECHANICS.md` "Needs verification").

## 1. The skills of the heroes met most (biggest gap)

The simulation plays a hero as a plain attacker unless its kit was read: from epic7db's effect icons (older heroes
only: newer ones have none) or by hand (`src/e7ac/predict/kits/<code>.toml`, from the in-game skill screens, `verified`).
`e7 kit todo` (or the Arena stats page: "Read kit status") lists the heroes met in Arena, most seen first, with what is
**NOT READ** for each. Seen counts of 2026-10-18 (teams read from the Opponents list); done = a hand kit exists.

| Seen | Hero | State | Named effects whose in-game text is needed (beyond S1-S3) |
|---:|---|---|---|
| 16 | Lady of the Scales (c6005) | done (D89) | Soulburn only |
| 15 | Boss Arunka (c2124) | done (D89) | Soulburn only |
| 10 | Rinak (c1168) | done (D89) | Soulburn only; the Health part of Pilfer / Spoils |
| 9 | Spirit Eye Celine (c2103) | not read | Possession; Nimble Sword life steal amount; Souls |
| 8 | Harsetti (c1153) | done | Focus maximum and bonus, En Passant multiplier (NV-44) |
| 8 | Arbiter Vildred (c2007) | not read | Focus, Dark Blade, the regeneration on lethal damage |
| 8 | Rhianna and Luciella (c2185) | not read | Erosion, Waning/Half Moon Slash, Butterfly's Afterdream, damage sharing, the swap |
| 7 | Notos (c2181) | not read | God of Battle, Sanctuary of Battle |
| 6 | Belian (c1117) | partly read | Incursion (extra attack), the random buff of S2, Provoke |
| 6 | Setsuka (c1176) | not read | Fighting Spirit, Demon Blade Unleashed, Blade Art: Bewitching Blossom, beguile |
| 5 | Genesis Ras (c2001) | not read | Sacred Covenant, seal and collapse |
| 5 | Empyrean Ilynav (c2113) | not read | Injuries, Indomitable |
| 4 | Perfumer Byblis (c5154) | done (D89) | Soulburn; the 5% Health of Lingering Fragrance; injuries |
| 4 | Zio (c1133), New Moon Luna (c2066), Witch of the Mere Tenebria (c6050) | not read | Deify; skill effect nullifier; rupture, restrict, block |
| 4 | Maid Chloe (c2049) | S1, S3 done | S2 |
| 2-3 | Mort, Young Senya, Celine, Frieren, School Nurse Yulha, Tactical Archetype Coli, Afternoon Soak Flan, ... | Mort, Young Senya done | the rest, in this order |

Since SPEC D88 the plain unconditional sentences of an active skill's text are read for heroes without icons (fear, Defense / Attack changes, dispel, Combat Readiness, extra turn ...); what stays "not read" is what needs an amount or a mechanic the text does not give (heals, barriers, venom, offering, possession, focus, ...), so the table below is what is still missing after that.

What settles it: the in-game skill screens, S1 / S2 / S3 at the enhancement the hero is assumed to have (opponents:
fully enhanced), plus the description of each named effect (tap the highlighted word). Cost: three screens per hero.
A partly written kit keeps the hero flagged for the skills it leaves out (`unread = [3]` in the file).

## 2. The opponent's real build

The opponent's stats are Stove's "typical" build: the median of each stat taken alone, from a population that also holds
PvE builds (NV-01, NV-02). Arena defence teams are probably better built than the median. Their sets and artifacts
are not in the simulation at all (NV-07, NV-17).

**The game never shows an opponent's equipment** (the user, 2026-10-18), so the build cannot be read from the screen:
the idea is closed. What is left is results (section 3) to choose between the profiles. Since SPEC D92 there are
four: Stove typical / strong / top and **library** (the Fribbels hero library average of each hero, from epic7db's page:
final stats with the sets already in them, speed included; `e7 predict win --profile library`, or the setting
`opponent_profile`). On the lost fight the chance goes 99.4% (typical) -> 96.0% (library): still a win.

## 3. The real result of each fight

Nothing checks the chance against what happened. The Battle Results screen (Damage & Recovery tab) gives, per hero,
the damage and the recovery: the first one (the lost fight) is kept locally and summarised in NV-51; it shows the
size of the gap (the opponents recovered 1.44 million).

Since SPEC D89 `e7 predict win` prints each hero's damage, Health restored and barrier granted in an average battle, to
compare with those numbers hero by hero. With the four opponents' kits read, the lost fight is **still** predicted as a
win (99.6%; 89% with the "top" build), in about 22 actions, with a fraction of the real damage and healing (Byblis:
31 thousand damage and 12 thousand restored against 249 thousand and 717 thousand; NV-53, NV-55). The damage numbers
say the real fight lasted 15 to 40 rounds per hero; what the Recovery column counts is itself open (NV-53). So the
skills read were not what decided that fight, and no other cause can be told apart from this one fight.

What settles it: the Battle Results and Victory / Defeat screen of each fight, with the chance the program showed (a
screenshot of both is enough; a results log that reads them from the screen is proposed, not built), the tabs of the
same screen that were not sent yet (Damage Taken, the other Recovery tab), and how many turns the fight lasted.

## 4. How the game plays the fight

Assumed, not seen: which skill the AI uses when several are ready (S3 whenever it can, MECH-AI-01, NV-36), whom it
targets (the enemy it needs the fewest hits to defeat, NV-05), how cooldowns count (NV-33), the starting Combat
Readiness (NV-04), ties (NV-13), what provoke / silence forbid (NV-37).

What settles it: a recorded fight in which one can read the turn order and the skills used (a screen recording or the
battle log is enough: the first six turns of one fight).

## 5. Gear in combat

Not simulated: artifacts (NV-07, NV-17), combat set effects (Counter, Immunity, Revenge, Rage, Torrent, ...), skill
enhancement levels of the opponents (assumed complete), imprints in battle (NV-22), exclusive equipment effects (NV-08).

What settles it: for sets and artifacts, community data is enough to model them once (no screenshots), but their
effect on the result needs the fights of section 3; for the user's own heroes it is read from the screens already.

## Order of work

1. The heroes at the top of the table (each one that is read removes a "~" from a lot of predictions): next Spirit Eye
   Celine, Arbiter Vildred, Rhianna and Luciella, Notos, Belian, Setsuka.
2. The results log (section 3): turns every later fix into a measured one; the lost fight is the first case to explain.
3. The opponent build (section 2): only from results, since the game never shows it.
4. The rest, as the fights say which of them matter.
