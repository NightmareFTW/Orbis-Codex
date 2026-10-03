# Orbis Codex

Part 1: **E7 Arena Companion** — a local, read-only roster tracker and Arena win-probability overlay for Epic Seven.

Status: Phase 0 (research & plan) — see [`docs/ROADMAP.md`](docs/ROADMAP.md).

- Spec & decisions: [`docs/SPEC.md`](docs/SPEC.md)
- Architecture, data model, risks: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)
- Data sources: [`docs/DATA_SOURCES.md`](docs/DATA_SOURCES.md)
- Game mechanics (with source + status): [`docs/MECHANICS.md`](docs/MECHANICS.md)

The tool only reads pixels from the screen (capture + OCR). It never touches game memory, network traffic or inputs.
No game assets, screenshots or game data are stored in this repository.
