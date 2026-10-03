# Orbis Codex — E7 Arena Companion: specification

Living document: the original brief, refined as decisions are made. Decisions are logged in §9;
open questions in §10. Companion docs: `ARCHITECTURE.md`, `DATA_SOURCES.md`, `MECHANICS.md`, `ROADMAP.md`.

## 1. Goal
A local Windows desktop app + in-game overlay for **Epic Seven** (Smilegate) that:
1. stores the user's whole roster with complete build data (stats, gear, sets, artifact, EE, imprint…), with history;
2. in **Arena** (normal asynchronous PvP, not RTA) estimates the win probability of the chosen attack team against each
   opponent on the list, e.g. `Team 1 — 45% | Team 2 — 12% | Team 3 — 89%`, with uncertainty, confidence and reasons.

Opponents' heroes are visible in Arena; their stats and gear are not → assumed builds from the official Stove Strategy Guide
(Lv60, 6★ awakened, fully equipped heroes, per region).

## 2. User context
- Plays on the **Stove PC client** today; must also support the upcoming **Steam** release and stay open to Google Play Games
  and emulators → the game client is a configurable *client profile* (window title/process, capture hints), never hard-coded.
- Server **Global** (`world_global`) for now; region must be switchable in settings.
- Game in English, **borderless windowed fullscreen**; must work at any resolution/aspect ratio (no fixed-pixel assumptions).
- Arena: plays mostly on **auto**; league/rank varies a lot → the model must work for **all leagues** (no fixed rank prior).
- Owns ~90% of the roster and uses many heroes → no hero priority list; kit coverage must be broad (generic kits + priority
  by observed frequency).
- Machine: Windows laptop, i7-12700H, 16 GB RAM, RTX 3050 Ti.
- Roster: hundreds of heroes → incremental import and an "arena-relevant" flag.

## 3. Hard constraints
- Windows, local-first: offline after syncing data; no cloud, no paid APIs, no accounts.
- Towards the game the tool is **read-only and user-triggered**: screen capture + OCR/CV only. Never read/write game memory,
  inject, intercept/modify network traffic, or automate inputs. Overlay is a separate topmost window; hotkeys use
  `RegisterHotKey` (no keyboard hooks).
- Scraping: polite (cache, rate limit ≤ 1 req/s, honest User-Agent, robots/ToS respected), personal use only.
- Game assets, screenshots, save files and real API responses are git-ignored; tests that need them skip cleanly.
  The code may be published on GitHub.
- Lightweight dependencies; ask before adding anything huge (PyTorch etc.).
- **Golden rule for game data**: never invent numbers. Unverifiable → `assumed`, shown as reduced confidence, listed in
  `MECHANICS.md` → "Needs verification".

## 4. Domain primer (verified details live in MECHANICS.md)
Hero (element, class, horoscope, stars/awakening, level ≤ 60, skill enhancements, Memory Imprint, EE, artifact, 6 gear pieces);
gear (fixed mains for weapon/helmet/armor, variable for necklace/ring/boots; set, grade, item level, +0…+15, ≤ 4 substats,
reforge/modify, score); static set bonuses vs in-combat set effects modelled separately; displayed hero stats are FINAL values
and the combat source of truth; combat = CR gauge, skills/cooldowns, hit types, elements, EFF vs ER, buffs/debuffs, CR push/pull,
extra turns, counters, dual attacks, revives, soulburn; the defence is played by the game AI.
**Stable IDs**: hero codes (`c2011` = Blood Blade Karin), artifact codes (`efa22`), set codes (`set_cri_dmg`), EE option codes
(`ek_c201101_01`). Display names are never keys; fuzzy matching never silently picks a near-miss.

## 5. Features by phase (refined)

### Phase 1 — Roster storage (first feature)
- Model with history: `HeroCatalog`, `OwnedHero` + immutable `HeroSnapshot` (stars, awakening, level, skill enhancements, imprint,
  EE choice, artifact, 6 gear slots, displayed final stats, CP, captured_at, source, per-field confidence), `Gear`, `Artifact`,
  `ExclusiveEquipment`, `SetCatalog` (pieces required, static bonus vs combat effect). ERD in `ARCHITECTURE.md` §6.
- Import paths, in priority order:
  1. **OCR of the Hero Info screen** — resolution-independent (window + anchors + normalised regions); substat type icons →
     classifier (templates bootstrapped from screenshots + labelling tool) + "%" detection; set from set icon; grade from frame
     colour; slot from position (left: weapon, helmet, armor; right: necklace, ring, boots); hero name via OCR + catalog match;
     per-field confidence; low confidence → review screen (crop next to parsed value).
     **Scan via overlay**: a small overlay with a **"Scan hero"** button (one capture per click) and an optional passive
     watch mode that auto-captures each new Hero Info screen while the user browses heroes; dedupe by hero + CP.
     The same overlay later gets **"Scan Arena teams"** (Phase 5). Clicking our overlay is input to *our* window only — nothing
     is ever sent to the game.
     **Batch import** from a folder of screenshots. Incremental; "arena-relevant" flag.
  2. **Fribbels save file import** (schema derived from the user's real file).
  3. **Manual create/edit form** with validation.
- Validation: slot ↔ main stat; substat count/duplicates/sub ≠ main; plausible item level/enhancement/value ranges; caps
  (CC 100%). Optional consistency check: recompute final stats from components when base stats are known; mismatches are
  warnings, never silent fixes.
- UI: roster list (search, filter by element/class, sort by any stat), hero page mirroring the in-game layout, edit, history,
  JSON export/import backup.
- Acceptance: golden fixture `hero_info_bbk.png` (values in §7), frozen only after the user confirms the transcription table.

### Phase 2 — Opponent model (Stove)
- Stove client with local cache (timestamp + region), weekly or on-demand refresh; tests on saved/synthetic responses.
- `AssumedBuild` per opponent hero: deterministic profiles **typical (P50)**, **strong (P75)**, **top (P90)** (default configurable);
  sampling mode for Monte Carlo (stats from histograms, sets/artifact/EE by usage %). Stove gives marginals only → independence
  approximation, documented and swappable.
- Use what the Arena screen shows (stars, level, artifact, CP…) to override/calibrate; e.g. choose the percentile that best
  reproduces visible CP (MECH-CP-01/02, uncertainty from the enhancement factor).

### Phase 3 — Predictor v1 (heuristic, explainable) + overlay
- Input: my team (4 owned heroes) + N opponent teams (default N = 3, configurable); v1 manual entry with fast fuzzy autocomplete.
- Features: turn-order advantage (incl. random starting CR); damage per turn vs effective HP → time-to-kill both ways with
  elemental modifiers; debuff landing probabilities both ways; mechanic tags (cleanse, dispel, immunity, revive, counter,
  CR push/pull, AoE, stealth, invincibility, skill nullifier…) and their counters.
- Logistic score with documented hand-set initial weights; later fitted on logged results.
- Output per opponent: win %, uncertainty interval, confidence level (kit coverage, share of `assumed` data), top 3 factors
  for/against in plain language, warnings.
- Overlay: small, transparent, always on top, draggable, click-through toggle, remembers position, DPI-aware, windowed/borderless;
  global hotkey; colour-coded; instant heuristic, refined when the simulator finishes. Main window for roster, data, settings.

### Phase 4 — Battle simulator (Monte Carlo)
- Deterministic, seedable, event-driven engine: CR timeline, skills, status effects (duration/stacking/priority), hooks.
- Kits as data (YAML DSL) + Python escape hatch; each kit cites sources and has unit tests.
- Pluggable AI policies (defence AI approximation; my side auto-battle-like by default).
- Coverage-aware generic kits; start with my most-used heroes and most-met defences.
- Adaptive number of simulations (stop when 95% CI half-width < 3 pp; cap configurable). Target < 5 s for 3 matchups.
- Final probability = blend(simulator, heuristic) weighted by kit coverage, then calibration.

### Phase 5 — Screen automation
- Capture only the game window (benchmark WGC / dxcam / mss); screen-type classifier (Hero Info, Arena list, battle result).
- Arena list: detect N opponents and their 4 heroes (portrait recognition vs catalog portraits; OCR where names are visible);
  one-click confirmation/correction → predict.
- Battle result: detect win/loss and log it (with confirmation).

### Phase 6 — Learning & calibration
- Log every prediction (inputs, model version, data snapshot ids, seed) and its outcome.
- Dashboard: Brier, log loss, reliability curve; from ~50 results Platt/isotonic calibration and refit of heuristic weights;
  optional per-defence Beta memory.

### Later (stretch)
Team recommender; what-ifs (swap gear, reach X speed).

## 6. Quality bar
Unit tests, golden OCR fixtures, property tests for stat maths, deterministic seeded simulator tests, regression tests per kit.
No silent failures: every parsed field and prediction carries a confidence; unknowns visible. Reproducible predictions,
versioned migrations, `mypy --strict` in core packages, ruff clean. Docs + `CLAUDE.md` updated at the end of every milestone.

## 7. Golden fixture (as given by the user — pending image check)
- Blood Blade Karin · Dark · Thief · Scorpio · 6★ · Lv. 60 · Imprint Attack +18% (SSS) · CP 141,750
- ATK 4116 · DEF 829 · HP 12819 · SPD 133 · CC 100.0% · CD 357.0% · EFF 0.0% · ER 21.0% · Dual Attack 3.0%
- Gear, all Lv. 90 +15: Weapon ATK 525 (score 84) · Helmet HP 2,835 (76) · Armor DEF 310 (90) · Necklace CD 70% (94) ·
  Ring ATK 65% (72) · Boots ATK 65% (93) · average score 84
- Sets Destruction + Critical; gear contribution (from `hero_manage_bbk.png`): ATK +2943, DEF +352, HP +6848, SPD +22, CC +77%,
  CD +203%, ER +17%
- Artifact Hostess of the Banquet (+18) · EE "Blood Blade", 12%
- To transcribe from the image and confirm with the user before freezing: gear substats, EE stat, artifact level.

Cross-checks done without the images (details in MECHANICS.md):
- Catalog agrees: BBK = `c2011`, Dark (`dark`), Thief (`assassin`), Scorpio (`scorpion`), base SPD 111 → 111 + 22 = 133 ✔,
  base CC 23% + 77% ≥ 100% → displayed 100.0% ✔, imprint SSS = 18% ✔, Lv90 weapon/helmet mains 525/2835 ✔.
- CP: gear-only formula gives 109,033; 141,750 / 109,033 = 1.300 → consistent with "+skill enhancements" (MECH-CP-02, `assumed`).
- Open: final − gear contribution leaves ATK +35, DEF +15, HP +100, CD +4%, ER +4% over base stats, while imprint and artifact
  should add more ATK/HP (NV-10). The screenshots are needed to settle what "gear contribution" includes.

## 8. Non-functional defaults
- Data dir: `%LOCALAPPDATA%\OrbisCodex\` (DB `e7ac.sqlite3`, `cache/`, `captures/`, `logs/`).
- Python package `e7ac` (E7 Arena Companion), distribution `orbis-codex`, CLI command `e7`.
- Stove refresh: weekly after Thu 03:00 UTC, lazy per hero, on demand; ≤ 1 req/s.
- Logging: structured, local only; no telemetry.
- Settings file: `%LOCALAPPDATA%\OrbisCodex\settings.json` (client, world/region, display mode, resolution; opponent
  profile added in M10). Missing file = defaults; corrupt file = explicit error, never a silent reset.
- Install/run: the Windows launcher `OrbisCodex.cmd` finds uv (or installs it via `scripts/install-uv.ps1`, the official
  installer), then runs `uv run --frozen --no-dev --project <repo> e7 <args>`: uv downloads Python 3.12 and the locked
  runtime dependencies on first start. Arguments reach `e7` exactly as typed (no PowerShell in between). Double-click (no
  arguments) runs `e7 doctor` and waits for a key (will open the GUI once it exists). No manual dependency installs;
  no dependency that needs a separate system installer (→ Tesseract excluded).

## 9. Decisions log
| # | Date | Decision | Rationale | Status |
|---|---|---|---|---|
| D1 | 2026-10-03 | Stove data via its public JSON API (`api.onstove.com/pub-meta/v1.0/epic7/guide/…`) with httpx; no Playwright | Page is a Nuxt SPA fetching plain JSON; no auth | accepted |
| D2 | 2026-10-03 | Use `hero-detail-for-game` with `strategy_type=0` (general equipment statistics) for builds | Same call the in-game guide link uses; has histograms + top combos | accepted |
| D3 | 2026-10-03 | Histogram edges = equal-width between UI label bounds, edge bins open-ended; configurable; status `assumed` | Inferred from front-end constants + equal totals | accepted |
| D4 | 2026-10-03 | Catalog = fact store with per-field provenance merged from Stove (official) + Fribbels + e7calc (+ epic7db for text) | No single complete source | accepted |
| D5 | 2026-10-03 | SQLAlchemy 2.0 typed ORM + Alembic, pydantic for domain (not SQLModel) | mypy strict, history tables | accepted |
| D6 | 2026-10-03 | opencv-python-**headless** | Avoid Qt plugin clash with PySide6 | accepted |
| D7 | 2026-10-03 | OCR engine chosen by benchmark on the fixtures (M5) | Requirement | accepted |
| D8 | 2026-10-03 | Default opponent profile: typical (P50), overridable; CP calibration when CP is visible | Neutral default; Arena-specific data unavailable | accepted |
| D9 | 2026-10-03 | Real Stove responses git-ignored; unit tests use synthetic responses mimicking the schema | ToS / redistribution caution | accepted |
| D10 | 2026-10-03 | Region `world_global` by default, switchable in settings | User answer Q1 | accepted |
| D11 | 2026-10-03 | Phase 0 plan approved (user answered Q1–Q7) | User | accepted |
| D12 | 2026-10-03 | Game client = configurable client profile (Stove PC default; Steam, Google Play Games, emulator) | User answer Q1 | accepted |
| D13 | 2026-10-03 | Display assumption: borderless windowed fullscreen by default, any resolution; vision must be resolution-independent | User answer Q1 | accepted |
| D14 | 2026-10-03 | Hero/team capture is user-triggered from an overlay "Scan" button, plus optional passive watch; overlay shell moves into Phase 1 (M9) | User answer Q2 | accepted |
| D15 | 2026-10-03 | Attacker policy default = auto-battle AI approximation (user plays on auto); no league prior, CP-based calibration for all leagues | User answer Q3 | accepted |
| D16 | 2026-10-03 | No hero priority list; kit work ordered by observed frequency (battle log, then Stove usage) | User answer Q4 | accepted |
| D17 | 2026-10-03 | Any dependency size is fine if installation is automatic; uv-based bootstrap launcher; avoid deps needing a separate installer (Tesseract out) | User answer Q5 | accepted |
| D18 | 2026-10-03 | Repo licence MIT; weekly Stove API calls approved by the user | User answer Q6 | accepted |
| D19 | 2026-10-03 | BBK EE stat type = Crit Chance as in Fribbels (user-confirmed); the 12% on the user's copy vs Fribbels' 0.06 still to explain (NV-08) | User answer Q7 | accepted |
| D20 | 2026-10-03 | Launcher calls uv directly from `OrbisCodex.cmd` (args verbatim); PowerShell only installs uv; runtime deps only (`--no-dev`) | M1 review LAUNCH-1..3, TC-1 | accepted |
| D21 | 2026-10-03 | Catalog persisted as versioned snapshots: facts table + one resolved JSON document per entity (see ARCHITECTURE §6 note) | Small, read-mostly, versioned as a whole | accepted |
| D22 | 2026-10-03 | e7calc base ATK/HP/DEF are `assumed` facts (corroboration only) | Real data: tuned for its damage maths, include passive-like factors | accepted |
| D23 | 2026-10-03 | Names map to codes only by exact normalised match; ambiguous names are refused; e7calc needs an explicit hand-checked alias table, validated by element/class | Golden rule; real duplicate names ("Mercedes" ×3) | accepted |
| D24 | 2026-10-03 | Within one source, entries sharing a code: fields on which they disagree are dropped with a warning | Fribbels has 6 duplicated artifact codes | accepted |
| D25 | 2026-10-03 | `e7 catalog sync` exit codes: 0 ok, 1 partial (a source failed, snapshot still saved, errors printed), 2 nothing synced | No silent failures | accepted |

## 10. Open questions
Answered on 2026-10-03 (see D10–D19): Q1 client/region/display, Q3 Arena play style/league, Q4 hero priority,
Q5 dependencies, Q6 licence + Stove API, Q7 EE stat.

Still open:
- **Q2** The screenshots (`hero_info_bbk.png`, `hero_manage_bbk.png`, `stove_guide_lisette.png`) and the Fribbels save file are
  still not in this environment (git-ignored → not cloned). Needed from M4 (save import) and M6 (OCR golden fixture).
  Attach them in the chat (or share them through a private channel outside this repository). **Never push them to this
  repository**: it is public, GitHub has no private branches, and deleted branches stay reachable by commit SHA;
  `fixtures/` stays local and git-ignored.
- **Q8** Does the Arena opponent list show CP (and/or artifact/stars) per opponent team? (Determines Phase 2 calibration inputs.)
