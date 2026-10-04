# Fixtures

Game screenshots, save files and Stove responses are **git-ignored** (game assets / personal data).
Tests that need them must skip cleanly when they are missing.

Expected local layout (not committed):

| Path | Content |
|---|---|
| `fixtures/screenshots/equip_{renoa,haru,straze}.webp` | The user's Equipment-tab captures (2026-10-04) — golden OCR fixtures (`tests/test_hero_screen.py`) |
| `fixtures/screenshots/heroinfo_charles.webp` | Hero Info without gear (Closer Charles, 5★ not awakened, team imprint) — golden OCR fixture |
| `fixtures/screenshots/heroinfo_{haru,lots,ainz,straze}.webp` | Hero Info with gear (expected for M7: substats, sets, artifact, EE) |
| `fixtures/screenshots/hero_info_bbk.png` | In-game Hero Info screen (Blood Blade Karin) — golden OCR fixture |
| `fixtures/screenshots/hero_manage_bbk.png` | Hero management screen (sets + gear stat contribution) |
| `fixtures/screenshots/stove_guide_lisette.png` | Stove Strategy Guide page (reference only) |
| `fixtures/saves/*.json` | Fribbels E7 Optimizer save file(s) |
| `fixtures/stove/**.json` | Saved real Stove API responses (integration tests) |

Unit tests use small **synthetic** responses committed under `tests/` that mimic the schemas
documented in `docs/DATA_SOURCES.md` — never real game data.
