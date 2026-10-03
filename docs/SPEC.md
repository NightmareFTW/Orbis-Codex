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
- Plays on **[CLIENT — open question Q1]**, server **[REGION — Q1]**, game in English, **[RESOLUTION / windowed|borderless — Q1]**.
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
     **Scan mode**: passive watching while the user browses heroes; auto-capture each new Hero Info screen; dedupe by hero + CP.
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

## 9. Decisions log
| # | Date | Decision | Rationale | Status |
|---|---|---|---|---|
| D1 | 2026-10-03 | Stove data via its public JSON API (`api.onstove.com/pub-meta/v1.0/epic7/guide/…`) with httpx; no Playwright | Page is a Nuxt SPA fetching plain JSON; no auth | proposed |
| D2 | 2026-10-03 | Use `hero-detail-for-game` with `strategy_type=0` (general equipment statistics) for builds | Same call the in-game guide link uses; has histograms + top combos | proposed |
| D3 | 2026-10-03 | Histogram edges = equal-width between UI label bounds, edge bins open-ended; configurable; status `assumed` | Inferred from front-end constants + equal totals | proposed |
| D4 | 2026-10-03 | Catalog = fact store with per-field provenance merged from Stove (official) + Fribbels + e7calc (+ epic7db for text) | No single complete source | proposed |
| D5 | 2026-10-03 | SQLAlchemy 2.0 typed ORM + Alembic, pydantic for domain (not SQLModel) | mypy strict, history tables | proposed |
| D6 | 2026-10-03 | opencv-python-**headless** | Avoid Qt plugin clash with PySide6 | proposed |
| D7 | 2026-10-03 | OCR engine chosen by benchmark on the fixtures (M5) | Requirement | proposed |
| D8 | 2026-10-03 | Default opponent profile: typical (P50), overridable; CP calibration when CP is visible | Neutral default; Arena-specific data unavailable | proposed |
| D9 | 2026-10-03 | Real Stove responses git-ignored; unit tests use synthetic responses mimicking the schema | ToS / redistribution caution | proposed |
| D10 | 2026-10-03 | Region default `world_global` until the user answers Q1 | Placeholder | proposed |

## 10. Open questions (batched; see the Phase 0 summary)
- **Q1** Client (Stove PC / Google Play Games / emulator), server region, resolution and windowed/borderless?
- **Q2** The screenshots are not in this environment (git-ignored, so not cloned). How do you want to provide them and the
  Fribbels save file? (attach to the chat, or a private branch/gist that I delete after copying locally).
- **Q3** Do you play Arena attacks manually or on auto? Typical Arena rank/league? Does your Arena list include NPC teams?
  Is there a turn limit / sudden death you have noticed?
- **Q4** Which heroes do you use most in Arena (attack and defence)? (Prioritises kits and OCR testing.)
- **Q5** OK with these dependency sizes: PySide6 (~100 MB), opencv-headless (~40 MB), possibly RapidOCR + onnxruntime (~30 MB)?
  Tesseract would need a separate install — acceptable or to be avoided?
- **Q6** Licence for the repo when published (MIT suggested)? Any objection to calling Stove's undocumented JSON API weekly?
- **Q7** EE "Blood Blade, 12%": which stat is the 12%? (Fribbels lists BBK's EE stat as crit chance.)
