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
| D25 | 2026-10-03 | `e7 catalog sync` exit codes: 0 ok, 1 partial (a source failed, snapshot still saved but not made current — D28 — errors printed), 2 nothing synced | No silent failures | accepted |
| D26 | 2026-10-03 | Stove artifact stat fields are positional (two non-zero stats in ATK, DEF, HP order); a slot is `verified` only when Fribbels' +0 values confirm it, else stored as an `assumed` ATK/HP reading | M2 review RP-01: ATK+DEF / DEF+HP artifacts exist | accepted |
| D27 | 2026-10-03 | Artifact effect values: one entry per `@` placeholder in the text; `"0"`, `"0.0%"`, `{}` mean unknown → `null`, field `assumed` | M2 review RP-02; golden rule | accepted |
| D28 | 2026-10-03 | A sync is *complete* when every catalog source succeeded; only a complete sync (or the first one) moves the "current" snapshot | M2 review SRC-03 / STORE-01 | accepted |
| D29 | 2026-10-03 | HTTP: responses are validated before caching; a host that hit 429 or exhausted its retries is not contacted again in that run; stale copies carry a URL-free reason | M2 review HTTP-01..03 | accepted |
| D30 | 2026-10-03 | SQLite transactions are begun by SQLAlchemy (pysqlite's implicit BEGIN disabled) so savepoints never commit the outer transaction early | Found while testing DB-02: a crash after `save_snapshot` left a snapshot without facts | accepted |
| D31 | 2026-10-03 | Roster validation severity: **errors** (block a save unless `--force`) only for our data contract (units: a gear/imprint/EE rate ≥ 100%, a final rate > 1000%, a flat gear stat < 1; non-positive values). Every game rule that is not `verified` in MECHANICS (today all of them, incl. slot main stats MECH-GEAR-08 and substat rules MECH-GEAR-03) is a **warning**, always printed. Default chosen by us; easy to revert per rule once a rule is verified in game | Golden rule (never let an unverified rule reject real data); M3 review VALI-04/TEST-05 | accepted (default, user may change) |
| D32 | 2026-10-03 | Roster history: snapshots are immutable; at most one current snapshot per hero (partial unique index, migration 0003); an edit is a new snapshot dated now; NaN/∞ are refused everywhere and integers are bounded to 2³¹−1 (storage contract) | M3 review STOR-01/02/03/11 | accepted |
| D33 | 2026-10-03 | Backups: uid-based, idempotent merge. A hero new to the roster keeps the backup's current build; an existing hero only switches to an imported snapshot captured after its current one (reported). Any uid conflict or hero-code mismatch aborts the whole import. Export writes atomically, refuses to overwrite without `--force` and never targets the database | M3 review STOR-04/05/07, CLI-01/08 | accepted |
| D34 | 2026-10-03 | CLI input: rate options are percent (`--cc 100`), stored as fractions; omitted stars/awakening/level default to 6/6/60 and the CLI says so; a typed value drops that field's OCR confidence; `--from-json` for another hero is refused; JSON files may be UTF-8/16/32 (PowerShell) and may be `show --json` output | M3 review CLI-03..06, CLI-12 | accepted |
| D35 | 2026-10-03 | Gear rows are shared between snapshots when (content fingerprint, external id, score) are equal | M3 review STOR-06 (Fribbels rescoring) | accepted |
| D36 | 2026-10-04 | Roster import comes mainly from the **screen**: the app reads Hero Info (one capture per hero) and gear-detail screens itself, with no other program. The Fribbels save import becomes optional (M4 later). Network capture "like Fribbels" was investigated after the user allowed it if Fribbels does the same. Fribbels' importer is passive, but it sends the raw game traffic to Fribbels' closed server for decoding, which goes beyond "passive and local". Local decoding would need reverse engineering of the protocol (the EULA forbids it). It also needs Npcap, and Stove support is undocumented. Put to the user (Q9), who chose to add it as an optional extra → D38 | User request 2026-10-04; DATA_SOURCES §7 (verified research) | accepted |
| D37 | 2026-10-04 | Capture rules: <ul><li>find the game window by window class, title and executable name from the system process list; **never open a handle to the game process**;</li><li>never guess between several windows;</li><li>hotkeys only via RegisterHotKey;</li><li>backend: mss now, Windows Graphics Capture with the overlay (M9);</li><li>every frame checked for blank/scaled output;</li><li>captures stay local.</li></ul> A guard test forbids process/memory/input/hook/packet APIs in `src/` | Golden rule; research CAPTURE-*; anti-cheat software watches process handles | accepted |
| D38 | 2026-10-04 | **Optional network import approved by the user** (answer to Q9, after being told the traffic goes to a third-party server and needs Npcap): passive capture like Fribbels' importer, decoded by Fribbels' server. **Implementation paused**: the development session's safety system blocked building a tool that captures network traffic and uploads it to a third-party server. It resumes only if the user explicitly allows it in their tool permissions. Until then the CLAUDE.md rule stays strict (no network capture) and the screen import (D36) is the only import | User decision 2026-10-04; DATA_SOURCES §7 | approved, paused |
| D39 | 2026-10-04 | The game client is used in **English and sometimes Portuguese**, so OCR label tables (stat names, set names, screen titles) ship for both, selected by a `game_language` setting (default `en`). Windows version and HDR are unknown, so `e7 doctor` reports them | User answer to Q10 | accepted |

## 10. Open questions
Answered on 2026-10-03 (see D10–D19): Q1 client/region/display, Q3 Arena play style/league, Q4 hero priority,
Q5 dependencies, Q6 licence + Stove API, Q7 EE stat.

Still open:
- **Q2** The screenshots (`hero_info_bbk.png`, `hero_manage_bbk.png`, `stove_guide_lisette.png`) and the Fribbels save file are
  still not in this environment (git-ignored → not cloned). Needed from M5b/M6 (OCR) — now easy to take with
  `e7 capture` (Hero Info, Manage Equipment with a piece selected, a reforged and a modified piece in Equipment Details,
  the hero's EE, the Skill Enhance screen, and one capture at another window size); the save file only for the
  optional M4.
  Attach them in the chat (or share them through a private channel outside this repository). **Never push them to this
  repository**: it is public, GitHub has no private branches, and deleted branches stay reachable by commit SHA;
  `fixtures/` stays local and git-ignored.
- **Q8** Does the Arena opponent list show CP (and/or artifact/stars) per opponent team? (Determines Phase 2 calibration inputs.)
- **Q9** *(answered 2026-10-04: yes, as an optional extra next to the screen import → D38, implementation paused)*.
- **Q10** *(answered 2026-10-04: English, sometimes Portuguese; Windows/HDR unknown → D39)*.
