# Roadmap

Every milestone ends with: passing tests, a two-line "how to try it", a git commit and this file updated.
Checkboxes: `[x]` done · `[ ]` pending. Milestones are small vertical slices; order may change after approval.

## Phase 0 — Research & plan
- [x] **M0 Research & plan** — `DATA_SOURCES.md`, `MECHANICS.md`, `ARCHITECTURE.md` (diagram, stack, ERD, risks), `SPEC.md`, this roadmap, `CLAUDE.md`, `spikes/stove_probe.py`.
  - Acceptance: user approves the plan and answers the batched questions (SPEC §10). ✅ Approved 2026-10-03 (D11).

## Phase 1 — Roster storage
- [x] **M1 Skeleton & tooling** — uv project (`orbis-codex`, package `e7ac`), ruff, mypy (strict for core), pytest + hypothesis, Typer CLI `e7`, `platformdirs` data dir, settings (client profile, region, display), Windows launcher `OrbisCodex.cmd` (installs uv → Python 3.12 → deps on first run), MIT licence, GitHub Actions (Windows + Ubuntu).
  - Acceptance: `uv run e7 --version` works; `e7 config show|set` persists settings; `e7 doctor` reports environment; `uv run pytest`, `uv run ruff check`, `uv run mypy` clean on CI for both OSes; `OrbisCodex.cmd --version` works on a clean Windows user account.
  - ✅ Done 2026-10-03. Clean-machine criterion: verified by the CI launcher job (uv absent, uv-managed Python only) on a
    hosted runner; **pending the user's first run on their own PC**.
  - **How to try:** double-click `OrbisCodex.cmd` (first start installs everything, then shows `e7 doctor` and waits for a
    key); from a terminal in that folder: `.\OrbisCodex.cmd config set resolution 2560x1440` then `.\OrbisCodex.cmd config show`.
- [x] **M2 Catalog v1** — Stove client (cache, rate limit, strict models), Fribbels herodata/artifactdata parsers, e7calc cross-check for base stats/multipliers, fact store + resolver, versioned snapshot in SQLite; `e7 catalog sync`, `e7 catalog show c2011`, `e7 catalog conflicts`.
  - Acceptance: synthetic-response tests; BBK resolves to `c2011` with base stats 1138/5871/462/111 and statuses; coverage report lists heroes missing any field; second sync is served from cache.
  - ✅ Done 2026-10-03. Live sync verified on Global (BBK = 1138/5871/462/111, name `verified`, base stats `community`
    and corroborated). A multi-agent adversarial review was run and its confirmed findings fixed (SPEC D26–D30).
  - **How to try:** `.\OrbisCodex.cmd catalog sync` (≈20 s the first time, then cached), then
    `.\OrbisCodex.cmd catalog show "Blood Blade Karin"`, `... catalog conflicts --type skill`, `... catalog coverage`.
- [x] **M3 Roster core** — domain models, DB schema + first Alembic migration, validation rules (slot ↔ main, substats, ranges, caps), immutable snapshots + history, JSON export/import; `e7 roster add|show|list|history|export|import`.
  - Acceptance: property tests (hypothesis) for stat maths/validation; round-trip export → import is lossless; editing creates a new snapshot.
  - ✅ Done 2026-10-03, followed by a multi-agent adversarial review: 39 confirmed findings fixed (SPEC D31–D35). Every
    reported test gap is covered, and 13 deliberate mutations of the code are all caught by the tests.
    Deferred: the consistency check between final stats and their components moves to M4, because it needs the
    Fribbels save to settle NV-10.
  - **How to try:** `.\OrbisCodex.cmd roster add "Blood Blade Karin" --atk 4116 --def 829 --hp 12819 --spd 133 --cc 100
    --cd 357 --eff 0 --er 21 --dac 3 --cp 141750` (after `catalog sync`), then `... roster edit 1 --spd 140`,
    `... roster history 1`, `... roster export roster-backup.json`.
> **Order changed 2026-10-04 (SPEC D36):** the user wants the app itself to extract the roster from the screen, with no
> other program. Screen extraction therefore comes first (M5a → M5b → M6 → M7 → M9); the Fribbels save import (M4)
> becomes optional and comes later, for users who already have a Fribbels file.

- [x] **M4 Fribbels save import** *(SPEC D44/D45/D52: path A of the two import paths, bulk first load of ~400 heroes)* —
  `roster/fribbels_import.py` reads the save file the user made with Fribbels' own importer ("Save all optimizer
  data"); `e7 roster import-fribbels <file> [--dry-run] [--trust-save] [--force]`.
  - Acceptance: a report per hero (new/updated/unchanged/skipped with the reason, every note and warning shown); exact
    name matching only; gear as worn in the game (never Fribbels' optimizer plans), with rolls and game ids; Fribbels'
    estimates (+N below +15, stars, hand-edited pieces) at a lower confidence; level/awakening/displayed stats kept
    from the roster; a save file modified before the roster build never replaces it, and gear read on screen only
    with `--trust-save`; synthetic tests always run, the golden test skips cleanly without `fixtures/saves/fribbels.json`.
  - ✅ Done 2026-10-05 on synthetic saves built from Fribbels' code, then two rounds of multi-agent adversarial review:
    26 + 13 confirmed findings fixed (D52); the tests catch 25 deliberate mutations of the key rules (one more is
    equivalent). **Pending:** a check against the
    user's real save (the format is `community` until then).
  - **How to try:** in Fribbels, import your account from the game and use "Save all optimizer data"; then
    `.\OrbisCodex.cmd roster import-fribbels "$HOME\Documents\FribbelsOptimizerSaves\<file>.json" --dry-run`, and again
    without `--dry-run` to store it (`... roster list` shows the heroes).
- [x] **M4.1 Fribbels importer data** *(SPEC D53)* — `e7 roster import-fribbels` also reads `gear.txt` (auto-detected),
  the file Fribbels' importer writes when it reads the game: every hero by its own code and game id (copies kept apart,
  `owned_hero.game_id`, migration 0005), stars, awakening, the gear it wears (sets Fribbels does not know included).
  - Acceptance: the user's real files import (378 heroes from gear.txt; a second run reports them unchanged; the export
    on top agrees on 231 heroes); golden tests on both files skip cleanly without them.
  - ✅ Done 2026-10-05.
  - **How to try:** after Fribbels' import from the game,
    `.\OrbisCodex.cmd roster import-fribbels "$HOME\Documents\FribbelsOptimizerSaves\gear.txt" --dry-run`, then again
    without `--dry-run`; `... roster list` shows every hero with its stars and awakening.
- [x] **M5a Capture tooling** — read-only game-window locator (window title/class + system process list, never a handle to the game), `mss` capture of the window's client area, blank-frame check, lossless PNG, `e7 capture` (one shot, `--delay`, `--list-windows`, `--hwnd`) and a `--hotkey` scan mode (RegisterHotKey, no keyboard hook); `e7 doctor` reports the game window; a guard test forbids process handles, memory access, input injection, keyboard hooks and packet capture anywhere in the code.
  - Acceptance: tests with fake windows/backends on every OS, real window listing and screen grab on the Windows CI runner; the user captures the screens listed in SPEC Q2 with it.
  - ✅ Done 2026-10-04 (in-game check pending: the window identifiers of the Stove client are community-sourced).
  - **How to try:** with the game open on the Hero Info screen: `.\OrbisCodex.cmd capture --list-windows`, then
    `.\OrbisCodex.cmd capture hero_info` (or `.\OrbisCodex.cmd capture --hotkey ctrl+shift+s` and press the keys in game).
- [x] **M5b OCR engine** — RapidOCR on the user's real captures; engine decision D41.
  - ✅ Done 2026-10-04:
    - 3 captures at 3 scales, every field correct;
    - Windows OCR and template digits were not needed;
    - `e7 doctor` checks that the engine loads.
- [x] **M6 Hero screen OCR v1 (stats panel)** — anchored on the stat labels:
  - reads hero name → catalog code, level, CP, imprint, active sets, and the 9 final stats (+ "▲" bonus on the Equipment
    tab);
  - every field carries a confidence;
  - cross-check final − ▲ = catalog base (MECH-STAT-06);
  - `e7 roster scan <images>` stores builds (D40).
  - Acceptance: golden captures pass for all non-icon fields; works on rescaled copies.
  - ✅ Done 2026-10-04:
    - golden tests on Renoa, Haru and Straze at 0.64×, 1× and 1.28× (local fixtures; skipped in CI);
    - 27/27 base-stat checks agree.

    Gear details on Hero Info (substat values with icon types) come with M7.
  - **How to try:** `.\OrbisCodex.cmd capture --hotkey ctrl+shift+s`, press it on each hero's Equipment tab or Hero Info,
    then `.\OrbisCodex.cmd roster scan "%LOCALAPPDATA%\OrbisCodex\captures\*.png"` and `... roster list`.
  - Follow-up 2026-10-04 (M6.1):
    - `roster scan` expands patterns, folders and `%VAR%` itself (Windows shells do not; D43);
    - imprint text wrapped on three lines, "Locked" = no imprint;
    - imprint mode self/team stored (MECH-IMP-02, D42; migration 0004);
    - Hero Info golden test (Closer Charles);
    - adversarial review: 7 confirmed findings fixed (Typer's Windows argument expansion, flat/% twin imprints,
      keeping a known imprint mode, migration downgrade keeping gear links, empty and `~` arguments).
- [x] **M7 Hero Info gear + icon classifiers** — substat icons (templates taken from the stat-label icons of the same Hero Info capture), "%" detection, set icons (bootstrapped from Stove icons + screenshots), imprint icon (self/team, lit positions, grade letter; MECH-IMP-02), awakened stars (MECH-HERO-03), grade from frame colour, constraint-based disambiguation; then Hero Info gear: item level, +enhance, score, main stat and 4 substats per piece, artifact name/level and EE.
  - ✅ Done 2026-10-05 (prototypes measured first, then 5 production modules; SPEC D46–D51):
    - `vision/stat_icons.py` (149/149 icons at 3 scales), `vision/sets.py` + `sources/assets.py` (Stove set icons,
      cached by `catalog sync`), `vision/gear_panel.py` (slots, values, item level, +N, score, frame colour,
      artifact, EE), `vision/imprint_icon.py`, `vision/star_row.py`, `roster/composition.py` (final-stat check);
    - `vision/hero_info.py` + `roster/screen_gear.py` wire them into `e7 roster scan`;
    - end to end on the user's 6 Hero Info captures: every piece right; the final-stat check gives 9/9 on the 4
      Lv60 heroes; Politis' ambiguous helmet frame is not stored (note).
    - adversarial review (4 reviewers, findings reproduced by running code): 11 defects fixed — values cut at the
      image edge sent to review, artifact +N above 30 no longer crashes the scan, a rescan keeps the rolls/reforge
      flags/source id of the same piece, a name read differently no longer creates a second copy, reader warnings
      (missed row) and data-contract errors block only their piece, damaged set icons no longer switch off every set,
      truncated PNGs are never cached, plus 3 missing notes; 2 claims did not reproduce;
    - Not done: the labelling tool (no review UI yet, M8); scan speed ≈ 15 s per capture (second OCR passes) to be
      optimised before the passive watch mode (M9a).
  - **How to try:** `.\OrbisCodex.cmd catalog sync` (also caches the set icons), then on Hero Info captures
    `.\OrbisCodex.cmd roster scan "$env:LOCALAPPDATA\OrbisCodex\captures" --dry-run` and without `--dry-run` to save.
  - Acceptance: golden BBK fixture passes 100% including substats, EE stat, artifact level; ambiguous crops produce review items instead of guesses.
- [ ] **M8 Roster UI** — PySide6 main window: roster list (search, element/class filters, sort by any stat), hero page mirroring the game layout, edit form with validation, history view, review queue (crop + value), JSON backup.
  - Acceptance: pytest-qt smoke tests; manual checklist for the user on Windows.
- [ ] **M9a Passive watch mode** *(brought forward after M4 — SPEC D45 path B)*: `e7 roster watch` captures the Hero Info screen by itself when the shown hero changes and reads it (no key presses, no input to the game).
- [ ] **M9 Overlay scan + batch import** — client profiles (Stove PC, Steam, Google Play Games, emulator: window title/process + capture hints), window locator, minimal overlay shell (topmost, draggable, non-activating) with a **"Scan hero"** button and an optional passive watch mode, Hero Info detection, dedupe by hero + CP, incremental folder import, arena-relevant flag.
  - Acceptance: replaying a folder of screenshots imports each hero once; the user verifies in game that "Scan hero" captures the current hero and nothing is ever sent to the game.

## Phase 2 — Opponent model
- [ ] **M10 AssumedBuild** — histogram model (configurable edges), P50/P75/P90 profiles, Monte Carlo sampler (independent marginals, documented), set/EE/artifact priors, weekly refresh + data-age warning, CP-based percentile calibration; `e7 opponent show c2011 --profile strong`.
  - Acceptance: deterministic profile tests on synthetic histograms; sampler reproduces marginals (property test); calibration recovers a known percentile on synthetic data.

## Phase 3 — Predictor v1 + overlay
- [ ] **M11 Heuristic predictor** — features (turn order, TTK both ways, debuff landing, mechanic tags), logistic score with documented weights, CI, confidence, top-3 factors, warnings; `e7 predict --team … --vs …`.
  - Acceptance: explainable output for 3 manual opponent teams; reproducible with seed; each weight documented.
- [ ] **M12 Overlay predictions** — extends the M9 overlay shell: results panel, click-through toggle, position memory, DPI-aware, global hotkey (RegisterHotKey), colour-coded results, fuzzy autocomplete entry.
  - Acceptance: user checklist on Windows (windowed + borderless).

## Phase 4 — Simulator
- [ ] **M13 Engine core** — CR timeline, skills/cooldowns, status effects, hooks, seedable RNG, generic kits from tags/multipliers.
- [ ] **M14 Kits DSL + first kits** — YAML schema, Python escape hatch, kits for the user's top Arena heroes + most-met defences, kit tests citing sources.
- [ ] **M15 AI policies + Monte Carlo runner + blending** — defence AI approximation, auto-like attacker, adaptive stopping, worker pool, < 5 s for 3 matchups, coverage-weighted blend.

## Phase 5 — Screen automation
- [ ] **M16 Capture benchmark + screen classifier** (WGC / dxcam / mss on the user's client).
- [ ] **M17 Arena list detection** — overlay **"Scan Arena teams"** button; portraits (perceptual hash/embeddings vs Stove portraits) + OCR; confirmation UI.
- [ ] **M18 Battle result logging** — win/loss detection with confirmation.

## Phase 6 — Learning & calibration
- [ ] **M19 Prediction log + dashboard** — Brier, log loss, reliability curve; Platt/isotonic from ~50 results; weight refit; per-defence Beta memory.

## Later
- [ ] Team recommender · [ ] What-ifs (gear swaps, speed targets)
