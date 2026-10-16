# Hand-refined hero kits (SPEC D73)

One TOML file per hero, named by its code (`c2049.toml`). The format is in `predict/overrides.py`.

Workflow:
1. `e7 kit todo` lists the heroes met in Arena (most seen first) and what is not simulated for each.
2. `e7 kit show HERO` shows the kit read automatically (epic7db icons and text + Fribbels amounts) and any hand file.
3. Write `kits/<code>.toml` with only what is wrong or missing; give `source` and `status` for its numbers.
4. `e7 kit show HERO` again, then `e7 predict win ...` uses it ("kit refined by hand" note).

Never guess a number: unknown values are `status = "assumed"` and listed in docs/MECHANICS.md "Needs verification".
