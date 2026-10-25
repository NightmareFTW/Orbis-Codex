# CLAUDE.md — Orbis Codex / E7 Arena Companion

## How we work
- Talk to the user in **European Portuguese (pt-PT)**, never Brazilian Portuguese. Code, identifiers, comments,
  commit messages and docs in **English**.
- Work in small vertical slices. Every milestone ends with passing tests, a two-line "how to try it",
  a git commit and an updated `docs/ROADMAP.md`. Update this file and the docs at the end of every milestone.
- Batch questions; for non-blocking details pick a sensible default, record it in `docs/SPEC.md` (§9 decisions) and continue.
- Throwaway experiments go in `spikes/` (never imported by `src/`).

## Pending at the very end of the project: authorship cleanup (user request 2026-10-11)
When the user says everything is finished — not before — rewrite the repository so it shows only the user's work:
- every commit's author and committer -> `Diogo Borges <jdiogopessoa@hotmail.com>` (the identity of the user's own
  GitHub commits); strip the `Co-Authored-By: Claude …` and `Claude-Session: …` lines from every message;
- method chosen: rewrite in this same repository and force-push `main` (not a new repository). Check first that the
  environment allows the force-push (branch deletion was refused with HTTP 403 on 2026-10-05);
- scope to confirm with the user at that time: commits only, or also the content (`CLAUDE.md`, the few mentions of
  Claude/Anthropic in docs, comments and `.github/workflows/ci.yml`);
- tell the user what stays outside the history: PR #1 keeps showing the old commits (GitHub cannot delete PRs), the
  old CI runs keep their messages (the user deletes them), and the remote branch `claude/focused-ride-ollauq`, already
  merged into `main`, is the user's to delete. After the force-push their local clone must be reset to `origin/main`.
Until then, keep committing as now (attribution lines included).

## Pending at the very end of the project: interface in European Portuguese (user request 2026-10-11)
Translate the whole user interface (CLI messages, the watch badge, the future window/overlay) to pt-PT, not
Brazilian. Until then every user-facing text stays in English only.

## Planned for the week of 2026-10-19 (user request 2026-10-12)
Start the **manual refinement of hero kits** (skill effects read by hand, beyond the automatic epic7db tags of SPEC D63)
for the heroes the user meets most in Arena: ask the user for that list first, ~0.5–1% of the weekly limit per hero.

**Paused 2026-10-12** (user saving the weekly budget for other projects). On resume: (1) hand refinement of the most met
heroes (above): ready since D73 — run `e7 kit todo` on the user's PC (they paste the output), then for each hero
`e7 kit show HERO` and write `src/e7ac/predict/kits/<code>.toml` (format in `predict/overrides.py`); (2) effects part 2 done
(D74); six hand kits done 2026-10-17 (D76: c1162, c2011, c1153, c1104, c1131, c2049) — next: the user's `e7 kit todo`
list, one hero at a time, best from the user's in-game skill-screen screenshots (verified; epic7db can be stale, D77); model gaps: artifacts, combat set effects, lifesteal (BBK S1: no number in the game text); second batch done 2026-10-17 (D80: Harsetti, Yulha S2/S3, BBK S2, Maid Chloe S1/S3); third batch 2026-10-18 (D89: Lady of the Scales, Boss Arunka, Perfumer Byblis, Rinak, with the engine mechanics they need); still to read from the game: Maid Chloe S2, then the heroes in the user's `e7 kit todo` in `docs/SIM_GAPS.md` order (Spirit Eye Celine, Arbiter Vildred, Rhianna and Luciella, Notos, Belian, Setsuka ...; the user sends the skill screens hero by hero); (3) the main window: slice 1 done 2026-10-18 (D81: PySide6 confirmed by the user; shell, Home, Arena, Arena stats); next: roster page, kits page, settings page, then the Qt overlay.

## Golden rules
- **Never invent game numbers** (multipliers, set bonuses, base stats, formulas, AI behaviour). Every datum carries
  `source` + `status` (`verified` / `community` / `assumed` / `unknown`). Unverifiable → `assumed`, lower confidence,
  add to "Needs verification" in `docs/MECHANICS.md`. Reference mechanics by ID (e.g. `MECH-DMG-01`) in docstrings.
- **Read-only towards the game**: screen capture + OCR/CV only. Never read/write game memory, inject, intercept network
  traffic, or automate inputs (no clicks/keys). Hotkeys via `RegisterHotKey`, no keyboard hooks.
- **Stable IDs** (hero `c2011`, artifact `efa22`, set `set_cri_dmg`, EE `ek_c201101_01`); never key by display name;
  fuzzy matching must never silently pick a near-miss (margin rule → review).
- **No silent failures**: parsed fields and predictions carry confidence; validation mismatches are warnings, never silent fixes.
- Game assets, screenshots, save files and real API responses are **git-ignored**; tests needing them skip cleanly.
  Unit tests use synthetic data.
- Polite scraping: cache, ≤ 1 req/s, honest User-Agent, robots/ToS. Ask before adding big dependencies.

## Commands
```bash
uv sync                      # install (dev group included)
uv run e7 --help             # CLI: --version, doctor, paths, config, catalog, roster, opponent, predict, arena, kit, skills, capture, app
uv run e7 app                # the main window (Orbis.cmd on Windows); Qt tests run offscreen (QT_QPA_PLATFORM=offscreen)
uv run pytest                # tests (fixture tests skip if fixtures are missing; network tests need E7AC_NETWORK_TESTS=1)
uv run ruff check . && uv run ruff format --check .
uv run mypy                  # strict, src + tests
.\OrbisCodex.cmd doctor      # Windows end-user launcher (installs uv -> Python 3.12 -> deps; double-click = doctor + pause)
python spikes/stove_probe.py c2011 --world world_global   # Stove API probe (spike)
actionlint .github/workflows/ci.yml   # validate workflow edits before pushing (a broken file runs 0 jobs)
```
Tests isolate the app home via `E7AC_HOME` (autouse fixture in `tests/conftest.py`). Markers (logic in `tests/markers.py`):
`windows`, `network`, `fixtures("screenshots/x.png", …)` — skipped automatically when not applicable.

## Architecture summary (see docs/ARCHITECTURE.md)
Python 3.12 + uv · pydantic v2 domain · SQLite via SQLAlchemy 2.0 + Alembic · httpx · Typer CLI `e7` ·
OpenCV-headless + OCR engine chosen by benchmark · PySide6 main window + overlay.
Package `src/e7ac/`: `domain`, `sources` (stove/fribbels/e7calc/epic7db), `catalog` (fact store → versioned snapshots),
`roster`, `vision`, `opponents`, `predict`, `sim`, `learning`, `storage`, `ui`, `cli`. Core is UI-free.

Key data facts (details in docs/DATA_SOURCES.md):
- Stove JSON API: `https://api.onstove.com/pub-meta/v1.0/epic7/guide/hunt/hero-detail-for-game?world_code=…&stage=1&lang_code=en&hero_code=…&strategy_type=0&boss_type=1`
  → 10-bin histograms per stat (edges inferred, `assumed`), top-3 set combos / EE options / artifacts; weekly (Thu 03:00 UTC).
- Fribbels `data/cache/herodata.json` / `artifactdata.json`: base stats, imprint, skill multipliers.
- e7calc (`tyopoyt/epic7-damage-calc`): damage formula, constants, enhancement steps (TypeScript).

## Docs map
- `docs/SPEC.md` — refined spec, decisions log, open questions
- `docs/ROADMAP.md` — milestones with acceptance criteria
- `docs/DATA_SOURCES.md` — sources, endpoints, freshness, licence/ToS, last verified
- `docs/MECHANICS.md` — formulas/rules with source + status, needs-verification list
- `docs/ARCHITECTURE.md` — diagram, stack, ERD, risks
- `docs/SKILL_SCREENS.md` — who to open in the game and which skills to hold, in order, plus the few fights worth recording
- `docs/SIM_GAPS.md` — what the simulation does not know yet (the heroes met most, the opponent's build, real results), ordered by effect, and what settles each
- `fixtures/README.md` — expected (git-ignored) fixture files

## Current status
- Phase 0 approved 2026-10-03 (SPEC D11; user answers recorded as D10–D19).
- **M1 Skeleton & tooling — done**: package `e7ac` (`paths`, `settings`, `doctor`, `domain.world`, `cli.app`),
  launcher `OrbisCodex.cmd` (+ `scripts/install-uv.ps1`), GitHub Actions (Ubuntu + Windows + clean-machine launcher job), MIT.
  Reviewed by a multi-agent adversarial review (22 confirmed findings fixed).
- **M2 Catalog v1 — done**:
  - `sources/{http,stove,fribbels,e7calc}`: polite cached HTTP, validate-before-cache, per-run host breaker.
  - `catalog/{facts,resolve,names,sets,coverage,store,sync}`: facts with provenance → resolver → versioned snapshots;
    partial syncs never become current.
  - `storage` (SQLAlchemy + Alembic, SQLAlchemy-emitted BEGIN) and `e7 catalog sync|show|conflicts|coverage|snapshots`.
  - Real-data quirks are in DATA_SOURCES (positional artifact stats, `0.0%` = unknown effect values, duplicate names/codes).
- **M3 Roster core — done**:
  - `domain/roster.py`: builds, gear, final stats; NaN/∞ refused; codes matched exactly.
  - `roster/{validation,store,backup}.py`, with migrations 0002 and 0003.
  - `e7 roster add|edit|list|show|history|validate|arena|export|import`.
  - Severity policy (SPEC D31): only data-contract rules (units, positivity) are errors; every non-verified game rule
    is a warning.
  - Reviewed by a multi-agent adversarial review (39 findings fixed; the tests catch 13 deliberate code mutations).
- **Order changed 2026-10-04 (SPEC D36):** screen extraction first, Fribbels import optional/later.
- **M5a Capture tooling — done**: `vision/{window,_win32,capture}.py`, `e7 capture` (`--list-windows`, `--hwnd`, `--delay`,
  `--hotkey`), doctor check; guard test `tests/test_vision.py::test_no_source_file_can_touch_the_game`.
- **M5b + M6 Hero screen OCR — done**:
  - `vision/{ocr,labels,hero_screen,image}.py`: RapidOCR with English labels, anchored on the stat labels;
  - `roster/screen_import.py`: base check final − ▲ = catalog base (MECH-STAT-06), merge with the current build;
  - `e7 roster scan`;
  - golden tests on the user's captures (local fixtures `fixtures/screenshots/equip_*.webp`, never committed).
- **M6.1**: `roster scan` expands patterns/folders/`%VAR%` (D43); imprint mode self/team + "Locked" (MECH-IMP-02,
  D42, migration 0004).
- **M7 Hero Info gear — done** (SPEC D46–D51): `vision/{stat_icons,sets,gear_panel,imprint_icon,star_row,hero_info}.py`,
  `sources/assets.py` (Stove set icons cached by `catalog sync`), `roster/{composition,screen_gear}.py`; golden tests on
  local captures (`fixtures/screenshots/heroinfo_*.webp`, `fixtures/stove/set_icons/`, never committed). Reviewed by a
  multi-agent adversarial review (11 findings fixed).
- **M4 Fribbels save import — done** (SPEC D52): `roster/{fribbels_import,pieces}.py`, `e7 roster import-fribbels FILE
  [--dry-run] [--trust-save] [--force]`; reads only the save file made with Fribbels' own tool; gear as worn in the game
  (`ingameEquippedId`, never Fribbels' optimizer plans); exact names only; Fribbels' estimates at lower confidence;
  level/awakening/displayed stats kept from the roster; a save file older than the roster build never replaces it,
  screen-read or hand-entered pieces only replaced with `--trust-save`. Reviewed by two rounds of multi-agent
  adversarial review (26 + 13 findings fixed).
  Checked on the user's real export (local fixture `fixtures/saves/fribbels.json`).
- **M4.1 Fribbels importer data — done** (SPEC D53): the same command reads `gear.txt` (Fribbels' importer output,
  preferred): every hero by its own code and game id (`owned_hero.game_id`, migration 0005; copies kept apart), stars,
  awakening, worn gear incl. sets Fribbels does not know. Fields `d`/`s` not used yet (NV-31/32). Real files are local
  fixtures (`fixtures/saves/{fribbels.json,gear.txt}`), never committed.
- **M9a Passive watch — done** (SPEC D54): `vision/watch.py` (settled/new screen detection), `e7 roster watch` (shares `_store_screen` with `roster scan`; background reader; settle time) and its badge `ui/watch_signal.py` (D55: hold still / captured / result; excluded from capture). Threshold and settle time to tune on the user's PC.
  Doubtful readings (D56): stored, amber on the badge, capture kept in `captures/watch/`; base check only at Lv. 60.
  Live frames (D57): unread bonus = "unchecked" (not doubtful); split digits glued back ("speed 2"); top-bar name
  cross-check; CP icon stripped; badge lines change together; missed bonuses re-read from their cell
  (`vision/bonus_cells.py`, accepted only when they agree with the catalog base: 100% of rows on the captures and
  hold-out variants, was 82%). Harness `spikes/watch_ocr_eval.py` (truth and captures local in
  `fixtures/screenshots/watch/`).
- **Order chosen 2026-10-12:** Phase 2/3 (opponent builds, turn order, Arena win probability) before any UI; one UI
  (roster + predictions + overlay) at the end. Token budget is tight: no multi-agent reviews unless the user asks,
  small slices, the user's game tests as the check.
- **M10 slice 1 — done** (SPEC D58): `sources/stove_usage.py` (hero-detail fetch, weekly refresh), `opponents/
  {histogram,assumed}.py`, `e7 opponent show HERO --profile typical|strong|top`.
- **M11 slice 1 — done** (SPEC D59): `predict/turn_order.py`, `e7 predict order --team … --vs …` (first-turn odds,
  opponent speed drawn from Stove); slice 2 (D60): `predict/damage.py`, `e7 predict hits` (S1/S3 hits to defeat).
  Slice 3 (D61): `predict/battle.py`, `e7 predict win` (simulated battles, low confidence). Slice 4 (D62): S3
  cooldowns from epic7db (`sources/epic7db.py`). Slice 5 (D63): skill effects read automatically from epic7db icons
  and text (`predict/effects.py`). Slice 6 (D68): survival effects (heals/barriers with Fribbels amounts,
  resurrection, cleanse, dispel, invincibility, active S2); part 2 (D74): DoTs, counter, provoke, silence. User chose precision before UI. Next: hand refinement (week of 2026-10-19).
- **M17a slice 1 — done** (SPEC D64): `vision/arena_badges.py`, `arena/{identify,references}.py`, `e7 arena scan CAPTURE
  [--confirm/--no-confirm] [--win]`: Arena VS screen → badges (element, class) → candidates → asked once, then learned
  (`<home>/references/arena/`). Slice 2 (D65): `e7 arena watch` + double-click `Arena.cmd` (watches the game, reads
  each VS screen once, asks only unknown heroes, runs predict win). Slice 3 (D66): `vision/arena_guide.py` recognises the
  opponent from the Attack Guide faces (magnifier) against the Stove face icons; the badge says what to do and shows the odds; D67: its "Check again" button re-reads both screens
  (another opponent or team; the opponent is never carried over). D69: unknown faces never block the watch (named
  by elimination or typed while it goes on) and are learned; `e7 arena forget HERO` undoes a wrong name. D70: an unclear face is decided among the heroes the
  VS badges allow. D72: "Save picture" badge button (game + result, `captures/arena/shared/`); the Opponents-list
  magnifier works too. D73: hand kits (`predict/kits/*.toml`, `e7 kit show|todo`). D75: the Opponents list is read
  directly (best of the three opponents on the badge). Local fixtures `fixtures/screenshots/arena/`, `fixtures/stove/class_icons/` (never committed).
- **M8 slice 1 — done** (SPEC D81): PySide6 main window `e7 app` / `Orbis.cmd`: `ui/{shell,context,theme,tasks,watch_controller,app}.py`, pages registry `ui/pages/` (Home, Arena, Arena stats = dev tool). `cli/arena.py` `run_watch(options, badge, answers, retry, share, stop)` is what the window drives; every Opponents-list refresh is recorded on `<home>/arena/sightings.jsonl` (`arena/sightings.py`, `popularity()`), `e7 kit todo` counts it; `predict/worklist.py` and `appdata.py` are the UI-free shared pieces. Seen offscreen only, not yet on the user's Windows PC (NV-46). D82 (2026-10-18, after the user's first run): the Opponents list is read with its badges (`vision/arena_list.py`, `arena/list_read.py`: element + class per face, faces checked against them, shapes recorded), "Check again" re-reads the list, the badge over the game has a size (`badge_size`, DPI-aware, default large), Dev tools is an openable sidebar folder, and the window uses the game's palette with round hero portraits from the cached face icons (`ui/theme.py`, `ui/art.py`, `ui/widgets.py`; never committed). D83 (2026-10-18, after the user's second run): the list is read through a fixed layout with jitter-tolerant stillness (`vision/arena_guide.fit_rows`, `cli/arena._list_frame`), collecting ignores the VS screen and Attack Guide, every question goes to the window as a `FaceQuestion` (`arena/questions.py`, `ui/questions.py`: suggested heroes to click, the name last) and unknown list faces can be taught while the watch goes on (`WatchOptions.choose/ask_face/unknown/taught`). D84 (2026-10-18, after the third run): every frame is read at 740 px high (`vision/arena_badges.normalise` now enlarges small frames too: the circle detection depends on absolute size), `vision/arena_list.find_rows` completes the list's rows from the level squares, the pages show "The watch sees: ..." and a "Keep picture" button (`<home>/captures/arena/debug/`), and a list not found 6 s after "Check again" keeps its picture by itself. D85 (2026-10-18, after the fourth run): the Arena page compares the three opponents of the Opponents list (`arena/compare.py`, `ui/opponents.py`, `compare_runs` setting) and names the best; crash evidence under `<home>/logs/` (`crashlog.py`: fatal-error dump, `app.log`, unclean-run marker) plus the Tk-garbage and `Orbis.cmd` exit-code fixes (cause of the user's crash unconfirmed, NV-49); a Settings page after Dev tools (`ui/pages/settings.py`: readiness check moved from Home, game and Arena options, folders, diagnostics, icon choice); the program icon is Celine's cached portrait or the user's own `<home>/icon.png` (`ui/icon.py`, never game art in the repository). D86 (2026-10-18, after the fifth run): `Orbis.cmd` starts the window with `pythonw` (`python -m e7ac.ui`, `ui/launch.py`) and its console closes; `Orbis.cmd --console` keeps the old console start; with no console the streams go to `<home>/logs/console.log` and a failure to start is shown in a message box; Settings can make a desktop shortcut (`shortcut.py`) and the icon is also written as `<home>/orbis.ico`; why the taskbar icon did not show on the user's PC is unknown (NV-50). D87 (2026-10-18, after a "100%" prediction that was lost): a chance that involves heroes whose skills were not read (`Kit.unread`, `BattleOdds.blind`) is shown with a ~, amber, "skills of N heroes not read", never as 100%/0%, and the opponent comparison prefers the better known team among equal chances; `e7 kit todo` lists the unread skills; the calculation itself is unchanged (NV-51: calibrate with real results). D88: `predict/textkit.py` reads the plain unconditional sentences of an active skill's text when epic7db has no icons for it (fear, Defense/Attack changes, dispel, cleanse, Combat Readiness, extra turn, immunity ...); conditional sentences, two-form skills and effects without a kind stay unread and keep the ~ (NV-52). D89 (2026-10-18, after the user's skill screens of the four opponents that decided the lost fight, and "the game never shows an opponent's equipment": the idea of reading the opponent's build from the screen is closed): hand kits `c6005 c2124 c5154 c1168` and the mechanics Offering / Abundance / Combat Readiness mirror / taunt / venom + detonate / Effectiveness buff / fixed damage / Lingering Fragrance / La Vie en Rose / Pilfer + Spoils / self-inflicted effects / speed-scaled damage (all damage goes through `battle._strike`); `e7 predict win` prints damage, Health restored and barrier granted per hero (`BattleOdds.heroes`, to compare with Battle Results). **The lost fight is still predicted as a win (99.6%, 89% with the "top" build)**: the skills were not what decided it (NV-53 what "Recovery" counts, NV-54 the assumptions of the four kits, NV-55 why still a win): the next levers are the results of more fights and the heroes still unread.
- **D90/D91 (2026-10-18, the user asked for a full survey and the lightest possible work for them)**: `e7 kit audit` (all 391 heroes; epic7db's enhancement steps, max-enhancement cooldowns, Fribbels hero-library block, RTA tier order) and the **Skill reader** (Dev tools page, `e7 skills watch|read|show|todo|export`, `vision/skill_popup.py`, `skills/`): the program reads the skill popup of the game while the user holds each skill icon, stores it (`<home>/skills/`, never committed), feeds the kits at once and exports one text file; epic7db's numbers are base for some heroes and max for others (NV-56), so what the game shows wins. `docs/SKILL_SCREENS.md` is the list of what to read, in order. D92: setting `opponent_profile` / `--profile` (typical, strong, top, **library** = the Fribbels hero library average from epic7db) for the opponent build; default `typical` until results choose. D93: the **results log** (`arena/results.py`, `vision/arena_result.py`, `e7 arena results [--undo]`): the Arena watch logs each chance and each Victory / Defeat it sees (`<home>/arena/results.jsonl`, local), pairs them and gives the Brier score, so every later change of the simulation is judged by real fights (NV-51); the Battle Results numbers (damage / recovery per hero) still come from the user. D94 (2026-10-25, after a "100%" lost again): the chance is read with doubt (`predict/doubt.py`): three opponent builds (profile `mixed`, now the default) with a range, and Laplace's rule with 20 "fights" so a sure win shows 95.5%; `BattleOdds.low/high/raw/margin`; the results log keeps the raw share; constants `assumed` (NV-59). Next (agreed plan): log the Battle Results numbers per fight and compare simulated and real per hero, then calibrate with the log, then artifacts / combat set effects / lifesteal on the user's side.
- Game client language: English, sometimes Portuguese → `game_language` setting (D39). Windows version shown by doctor.
- Roster import paths (D45), picked by the user: A = Fribbels save file (M4, done), B = passive screen
  watch (M9a). Network import inside our app (M5c, D38): **not possible here** — blocked twice by Anthropic's safety
  checks, even after the user changed the permission mode; do not attempt it again.
- Network import "like Fribbels" (D38): approved by the user, **paused**. This session's safety system blocked building a
  traffic-capture + third-party-upload tool. Resume only after the user explicitly allows it; until then the golden
  rule above (no network capture) applies unchanged.
- Received: the user's screen captures and Fribbels files (local fixtures only).
  The repo is **public**: never commit captures or save files.
