"""Docs guard rails: every mechanic / needs-verification ID cited anywhere must be defined in docs/MECHANICS.md."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ID_RE = re.compile(r"\b(MECH-[A-Z]+-\d{2}|NV-\d{2})\b")
DEFINITION_RE = re.compile(r"^\| (MECH-[A-Z]+-\d{2}|NV-\d{2}) \|", re.MULTILINE)


def _sources() -> list[Path]:
    files = [*ROOT.glob("src/**/*.py"), *ROOT.glob("tests/**/*.py"), *ROOT.glob("docs/*.md"), ROOT / "CLAUDE.md"]
    return [f for f in files if f.is_file()]


def test_every_cited_mechanic_id_is_defined() -> None:
    mechanics = (ROOT / "docs" / "MECHANICS.md").read_text(encoding="utf-8")
    defined = set(DEFINITION_RE.findall(mechanics))
    missing: dict[str, list[str]] = {}
    for path in _sources():
        for cited in set(ID_RE.findall(path.read_text(encoding="utf-8"))) - defined:
            missing.setdefault(cited, []).append(str(path.relative_to(ROOT)))
    assert not missing, f"IDs cited but not defined in docs/MECHANICS.md: {missing}"


def test_mechanics_keeps_its_sections() -> None:
    mechanics = (ROOT / "docs" / "MECHANICS.md").read_text(encoding="utf-8")
    for heading in ("## 4. Damage", "## 11. Arena", "## Needs verification"):
        assert heading in mechanics
