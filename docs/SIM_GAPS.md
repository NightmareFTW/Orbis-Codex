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
| 16 | Lady of the Scales (c6005) | not read | Offering, Abundance; S1 heal amount (a share of the *target's* max Health) |
| 15 | Boss Arunka (c2124) | not read | Ferocious Stand; S3 barrier amount |
| 10 | Rinak (c1168) | not read | Pilfer; S1 damage grows with Speed; S3 7000 fixed damage and its self-stun |
| 9 | Spirit Eye Celine (c2103) | not read | Possession; Nimble Sword life steal amount; Souls |
| 8 | Harsetti (c1153) | done | Focus maximum and bonus, En Passant multiplier (NV-44) |
| 8 | Arbiter Vildred (c2007) | not read | Focus, Dark Blade, the regeneration on lethal damage |
| 8 | Rhianna and Luciella (c2185) | not read | Erosion, Waning/Half Moon Slash, Butterfly's Afterdream, damage sharing, the swap |
| 7 | Notos (c2181) | not read | God of Battle, Sanctuary of Battle |
| 6 | Belian (c1117) | partly read | Incursion (extra attack), the random buff of S2, Provoke |
| 6 | Setsuka (c1176) | not read | Fighting Spirit, Demon Blade Unleashed, Blade Art: Bewitching Blossom, beguile |
| 5 | Genesis Ras (c2001) | not read | Sacred Covenant, seal and collapse |
| 5 | Empyrean Ilynav (c2113) | not read | Injuries, Indomitable |
| 4 | Zio (c1133), New Moon Luna (c2066), Perfumer Byblis (c5154), Witch of the Mere Tenebria (c6050) | not read | Deify; skill effect nullifier; venom (damage per venom and its detonation), lingering fragrance, La Vie en Rose; rupture, restrict, block |
| 4 | Maid Chloe (c2049) | S1, S3 done | S2 |
| 2-3 | Mort, Young Senya, Celine, Frieren, School Nurse Yulha, Tactical Archetype Coli, Afternoon Soak Flan, ... | Mort, Young Senya done | the rest, in this order |

What settles it: the in-game skill screens, S1 / S2 / S3 at the enhancement the hero is assumed to have (opponents:
fully enhanced), plus the description of each named effect (tap the highlighted word). Cost: three screens per hero.
A partly written kit keeps the hero flagged for the skills it leaves out (`unread = [3]` in the file).

## 2. The opponent's real build

The opponent's stats are Stove's "typical" build: the median of each stat taken alone, from a population that also holds
PvE builds (NV-01, NV-02). Arena defence teams are probably better built than the median. Their sets and artifacts
are not in the simulation at all (NV-07, NV-17).

What settles it: (a) whether the game shows an Arena opponent's hero stats and gear (a hero's info screen before the
fight): if it does, one capture of it, and the program can read the real build the way it reads the user's own heroes;
(b) otherwise, results (section 3) to choose between the typical, strong and top profiles.

## 3. The real result of each fight

Nothing checks the chance against what happened. The Battle Results screen (Damage & Recovery tab) gives, per hero,
the damage and the recovery: the first one (the lost fight) is kept locally and summarised in NV-51; it shows the
size of the gap (the opponents recovered 1.44 million).

What settles it: the Battle Results and Victory / Defeat screen of each fight, with the chance the program showed. A
results log that reads them from the screen is proposed (not built); until then, a screenshot of both is enough. The
simulation could print each hero's damage and recovery, to compare with those numbers hero by hero.

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

1. The heroes at the top of the table (each one that is read removes a "~" from a lot of predictions).
2. The opponent build (section 2): the largest single lever on the stats once the kits are in.
3. The results log (section 3): turns every later fix into a measured one.
4. The rest, as the fights say which of them matter.
