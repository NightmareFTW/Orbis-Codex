# Hand-refined hero kits (SPEC D73)

One TOML file per hero, named by its code (`c2049.toml`). The format is in `predict/overrides.py`.

Workflow:
1. `e7 kit todo` lists the heroes met in Arena (most seen first) and what is not simulated for each.
2. `e7 kit show HERO` shows the kit read automatically (epic7db icons and text + Fribbels amounts) and any hand file.
3. Write `kits/<code>.toml` with only what is wrong or missing; give `source` and `status` for its numbers.
4. `e7 kit show HERO` again, then `e7 predict win ...` uses it ("kit refined by hand" note).

A kit that still leaves a whole skill out (its effects matter and are not simulated yet) says so with
`unread = [3]`: the hero then stays flagged "skills not read" for that slot, and every chance with it keeps its ~.

Never guess a number: unknown values are `status = "assumed"` and listed in docs/MECHANICS.md "Needs verification".

Best source: the in-game skill screen (a screenshot of the hero's skill description at its current enhancement) —
the numbers are then `verified`. epic7db can be out of date (SPEC D77, NV-41).

Hand kits so far (screens read by hand): Young Senya, Mort, Harsetti, Blood Blade Karin, Maid Chloe, Yulha, then (SPEC
D89) Lady of the Scales, Boss Arunka, Perfumer Byblis and Rinak: Offering, Abundance, taunt, venom and its detonation,
Pilfer and Spoils and the rest of their mechanics are in `predict/overrides.py` (kinds, passives) and
docs/MECHANICS.md (MECH-OFFER-01 ... MECH-SPOIL-01).
