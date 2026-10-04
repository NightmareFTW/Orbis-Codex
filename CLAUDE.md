# CLAUDE.md — Orbis Codex / E7 Arena Companion

## How we work
- Talk to the user in **European Portuguese (pt-PT)**, never Brazilian Portuguese. Code, identifiers, comments,
  commit messages and docs in **English**.
- Work in small vertical slices. Every milestone ends with passing tests, a two-line "how to try it",
  a git commit and an updated `docs/ROADMAP.md`. Update this file and the docs at the end of every milestone.
- Batch questions; for non-blocking details pick a sensible default, record it in `docs/SPEC.md` (§9 decisions) and continue.
- Throwaway experiments go in `spikes/` (never imported by `src/`).

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
uv run e7 --help             # CLI: --version, doctor, paths, config, catalog, roster, capture
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
- Next: **M7** — Hero Info gear (substat icons via templates from the same capture), imprint icon (mode, grade,
  positions), awakened stars (MECH-HERO-03), artifact and EE. Waiting for the 4 Hero Info captures as files.
- Game client language: English, sometimes Portuguese → `game_language` setting (D39). Windows version shown by doctor.
- Network import "like Fribbels" (D38): approved by the user, **paused**. This session's safety system blocked building a
  traffic-capture + third-party-upload tool. Resume only after the user explicitly allows it; until then the golden
  rule above (no network capture) applies unchanged.
- Still missing: the user's screen captures (SPEC Q2) — needed for M5b/M6; a Fribbels save only for the optional M4.
  The repo is **public**: never commit captures or save files.
