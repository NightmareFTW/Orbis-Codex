# Orbis Codex

Part 1: **E7 Arena Companion** — a local, read-only roster tracker and Arena win-probability overlay for Epic Seven.

Status: Phase 1 in progress — see [`docs/ROADMAP.md`](docs/ROADMAP.md).

## Run it (Windows)
1. Download the ZIP of this branch (**Code → Download ZIP**) and extract it (right-click → *Extract All*).
   Running `OrbisCodex.cmd` from inside the ZIP does not work.
2. Double-click `OrbisCodex.cmd` in the extracted folder. The first start installs [uv](https://docs.astral.sh/uv/),
   Python 3.12 and every dependency automatically (a few minutes, only once), then shows the `e7 doctor` check
   (lines starting with `[OK  ]`) and waits for a key. Later starts are instant.

From a terminal opened in that folder (cmd or PowerShell), run commands as `.\OrbisCodex.cmd <command>`
(developers: `uv run e7 <command>`):

| Command | What it does |
|---|---|
| `e7 --version` | Show the version |
| `e7 doctor` | Check Python, OS, data folder and settings |
| `e7 paths` | Show where data is stored (`%LOCALAPPDATA%\OrbisCodex`) |
| `e7 config show` / `e7 config options` | Show settings / allowed values |
| `e7 config set world world_eu` | Change a setting (client, world, display_mode, resolution, game_language) |
| `e7 catalog sync` | Download the game catalog (heroes, skills, artifacts, sets) from Stove, Fribbels and e7calc (cached) |
| `e7 catalog show "Blood Blade Karin"` | Show one hero/artifact/set with the source and status of every value |
| `e7 roster add "Blood Blade Karin" --atk 4116 ... --cc 100 --cd 357` | Add one of your heroes (rates in percent) |
| `e7 roster edit 1 --spd 140` / `e7 roster history 1` | Change a hero (keeps every previous build) / see its history |
| `e7 roster list --sort speed --desc` | List your heroes (filters: `--element`, `--class`, `--arena`, `--search`) |
| `e7 roster export backup.json` / `e7 roster import backup.json` | Back up / restore (or merge) the whole roster |
| `e7 capture hero_info` | Save the game picture as PNG (read-only screen copy; `--hotkey ctrl+shift+s` to capture while you play) |
| `e7 capture --list-windows` | Show the open windows and which one looks like the game |

## Develop
```bash
uv sync                 # install (dev tools included)
uv run pytest           # tests
uv run ruff check . && uv run ruff format --check .
uv run mypy             # strict type checking
```

## Docs
- Spec & decisions: [`docs/SPEC.md`](docs/SPEC.md)
- Architecture, data model, risks: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)
- Data sources: [`docs/DATA_SOURCES.md`](docs/DATA_SOURCES.md)
- Game mechanics (with source + status): [`docs/MECHANICS.md`](docs/MECHANICS.md)

## Safety and data
The tool only reads pixels from the screen (capture + OCR). It never touches game memory, network traffic or inputs.
No game assets, screenshots or game data are stored in this repository.

## Licence
MIT — see [`LICENSE`](LICENSE). Epic Seven is a trademark of Smilegate; this project is unofficial.
