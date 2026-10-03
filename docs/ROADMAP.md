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
- [ ] **M3 Roster core** — domain models, DB schema + first Alembic migration, validation rules (slot ↔ main, substats, ranges, caps), immutable snapshots + history, JSON export/import; `e7 roster add|show|list|history|export|import`.
  - Acceptance: property tests (hypothesis) for stat maths/validation; round-trip export → import is lossless; editing creates a new snapshot.
- [ ] **M4 Fribbels save import** — schema derived from the user's real save file; strict models + raw passthrough; gear dedupe by id/fingerprint; mapping Fribbels names → hero codes with margin rule; `e7 import fribbels <file>`.
  - Acceptance: user's file imports with a report (imported/skipped/ambiguous); unknown fields preserved; test skips cleanly without the file; CP samples feed NV-06.
- [ ] **M5 OCR spike & benchmark** (`spikes/ocr_bench/`) — template digits vs RapidOCR vs Windows OCR on the fixtures (+ synthetic rescales); Tesseract excluded (separate installer, D17).
  - Acceptance: table of per-field accuracy/latency/install cost in `ARCHITECTURE.md`; engine decision logged in SPEC.
- [ ] **M6 Hero Info OCR v1** — anchors + normalised regions, numeric fields, hero name → catalog match, per-field confidence; `e7 import screenshot <png>`.
  - Acceptance: golden BBK fixture (after the user confirms the transcription table) passes for all non-icon fields; works on 0.75×/1.25× rescaled copies.
- [ ] **M7 Icon classifiers + labelling tool** — substat icons, "%" detection, set icons (bootstrapped from Stove icons + screenshots), grade from frame colour, constraint-based disambiguation.
  - Acceptance: golden BBK fixture passes 100% including substats, EE stat, artifact level; ambiguous crops produce review items instead of guesses.
- [ ] **M8 Roster UI** — PySide6 main window: roster list (search, element/class filters, sort by any stat), hero page mirroring the game layout, edit form with validation, history view, review queue (crop + value), JSON backup.
  - Acceptance: pytest-qt smoke tests; manual checklist for the user on Windows.
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
