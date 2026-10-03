# Orbis Codex

Part 1: **E7 Arena Companion** — a local, read-only roster tracker and Arena win-probability overlay for Epic Seven.

Status: Phase 1 in progress — see [`docs/ROADMAP.md`](docs/ROADMAP.md).

## Run it (Windows)
1. Download or clone this repository.
2. Double-click / run `OrbisCodex.cmd` (or `OrbisCodex.cmd doctor` from a terminal).
   The first start installs [uv](https://docs.astral.sh/uv/), Python 3.12 and every dependency automatically
   (a few minutes, once). Later starts are instant.

Useful commands (via `OrbisCodex.cmd <command>` or `uv run e7 <command>`):

| Command | What it does |
|---|---|
| `e7 --version` | Show the version |
| `e7 doctor` | Check Python, OS, data folder and settings |
| `e7 paths` | Show where data is stored (`%LOCALAPPDATA%\OrbisCodex`) |
| `e7 config show` / `e7 config options` | Show settings / allowed values |
| `e7 config set world world_eu` | Change a setting (client, world, display_mode, resolution) |

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
