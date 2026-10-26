"""The skills read from the game (SPEC D91), local only: `<home>/skills/<hero code>.json` holds, per hero, the popups
read (one per skill, the latest wins), and `<home>/skills/effects.json` the description of every effect panel ever seen
(Fear, Offering, Venom, ...), so a mechanic is described once and for all. Nothing here leaves the PC unless the user
exports it (`e7 skills export`): the game's text stays out of the repository."""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from e7ac.catalog.names import normalise_name
from e7ac.domain.codes import is_hero_code
from e7ac.fileio import write_text_atomic
from e7ac.vision.skill_popup import EffectPanel, SkillPopup

FOLDER: Final = "skills"
EFFECTS_FILE: Final = "effects.json"


def skills_dir(home: Path) -> Path:
    return home / FOLDER


@dataclass(frozen=True, slots=True)
class StoredSkill:
    """One popup as it was read, with when."""

    hero: str
    name: str
    level: int | None
    gains: tuple[tuple[str, int], ...]
    cooldown: int | None
    cooldown_note: str
    text: str
    soulburn: str
    soulburn_cost: int | None
    enhance: tuple[tuple[int, str], ...]
    effects: tuple[EffectPanel, ...]
    confidence: float
    captured_at: str
    """ISO time (UTC)."""
    slot: int | None = None
    """Which of the hero's three skills it is (1, 2, 3), by its name among epic7db's skills; None: not placed."""
    passive: bool = False
    """The slot-2 skill that has no cooldown: a passive (the rule the kit readers use)."""

    @property
    def key(self) -> str:
        return normalise_name(self.name)

    def to_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "level": self.level,
            "gains": [list(g) for g in self.gains],
            "cooldown": self.cooldown,
            "cooldown_note": self.cooldown_note,
            "text": self.text,
            "soulburn": self.soulburn,
            "soulburn_cost": self.soulburn_cost,
            "enhance": [list(s) for s in self.enhance],
            "effects": [{"title": e.title, "marks": list(e.marks), "text": e.text} for e in self.effects],
            "confidence": round(self.confidence, 3),
            "captured_at": self.captured_at,
            "slot": self.slot,
            "passive": self.passive,
        }

    @staticmethod
    def from_json(hero: str, data: dict[str, Any]) -> StoredSkill:
        return StoredSkill(
            hero=hero,
            name=str(data["name"]),
            level=data.get("level"),
            gains=tuple((str(k), int(n)) for k, n in data.get("gains", [])),
            cooldown=data.get("cooldown"),
            cooldown_note=str(data.get("cooldown_note", "")),
            text=str(data.get("text", "")),
            soulburn=str(data.get("soulburn", "")),
            soulburn_cost=data.get("soulburn_cost"),
            enhance=tuple((int(n), str(d)) for n, d in data.get("enhance", [])),
            effects=tuple(
                EffectPanel(str(e["title"]), tuple(e.get("marks", [])), str(e.get("text", "")))
                for e in data.get("effects", [])
            ),
            confidence=float(data.get("confidence", 0.0)),
            captured_at=str(data.get("captured_at", "")),
            slot=data.get("slot") if data.get("slot") in (1, 2, 3) else None,
            passive=bool(data.get("passive", False)),
        )


@dataclass(frozen=True, slots=True)
class Saved:
    """What storing a popup did."""

    stored: StoredSkill
    new: bool
    """The skill had not been read before."""
    changed: bool
    """It had, and the game's text or numbers differ now (an update, or another enhancement level)."""
    new_effects: tuple[str, ...] = field(default_factory=tuple)
    """Effect panels never seen before (or whose description changed)."""


class SkillStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def _file(self, code: str) -> Path:
        return self.root / f"{code}.json"

    def heroes(self) -> list[str]:
        """The codes of the heroes with something read."""
        return sorted(p.stem for p in self.root.glob("c*.json") if is_hero_code(p.stem)) if self.root.is_dir() else []

    def load(self, code: str) -> list[StoredSkill]:
        """The skills read for a hero, in the order first read (a damaged file counts as nothing read)."""
        path = self._file(code)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return [StoredSkill.from_json(code, item) for item in data["skills"]]
        except (OSError, ValueError, KeyError, TypeError):
            return []

    def save(
        self, code: str, popup: SkillPopup, now: datetime | None = None, slot: int | None = None, passive: bool = False
    ) -> Saved:
        """Keep a popup: it replaces the one read before for the same skill (the same name). The effect panels of the
        read before stay when the text is the same and this read missed some (a panel the OCR did not catch)."""
        when = (now or datetime.now(UTC)).astimezone(UTC).isoformat(timespec="seconds")
        stored = StoredSkill(
            code,
            popup.name,
            popup.level,
            popup.gains,
            popup.cooldown,
            popup.cooldown_note,
            popup.text,
            popup.soulburn,
            popup.soulburn_cost,
            popup.enhance,
            popup.effects,
            popup.confidence,
            when,
            slot,
            passive,
        )
        skills = self.load(code)
        index = next((i for i, s in enumerate(skills) if s.key == stored.key), None)
        before = skills[index] if index is not None else None
        if before is not None and before.text == stored.text:
            seen = {e.title.lower() for e in stored.effects}
            stored = replace(
                stored, effects=(*stored.effects, *(e for e in before.effects if e.title.lower() not in seen))
            )
        if index is None:
            skills.append(stored)
        else:
            skills[index] = stored
        self.write(code, skills)
        changed = before is not None and (before.text, before.level, before.enhance) != (
            stored.text,
            stored.level,
            stored.enhance,
        )
        return Saved(stored, before is None, changed, self._learn(code, popup.effects, when))

    def write(self, code: str, skills: list[StoredSkill]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        write_text_atomic(
            self._file(code),
            json.dumps({"hero": code, "skills": [s.to_json() for s in skills]}, indent=1, ensure_ascii=False),
        )

    def effects(self) -> dict[str, dict[str, Any]]:
        """Every effect described so far, by lower-case title."""
        try:
            return dict(json.loads((self.root / EFFECTS_FILE).read_text(encoding="utf-8")))
        except (OSError, ValueError, TypeError):
            return {}

    def _learn(self, code: str, panels: Iterable[EffectPanel], when: str) -> tuple[str, ...]:
        known = self.effects()
        fresh: list[str] = []
        for panel in panels:
            key = panel.title.lower()
            entry = known.get(key)
            if entry is None or entry.get("text") != panel.text:
                fresh.append(panel.title)
                entry = {"title": panel.title, "marks": list(panel.marks), "text": panel.text, "first_seen": when}
            entry["heroes"] = sorted({*entry.get("heroes", []), code})
            known[key] = entry
        if fresh or panels:
            self.root.mkdir(parents=True, exist_ok=True)
            write_text_atomic(self.root / EFFECTS_FILE, json.dumps(known, indent=1, ensure_ascii=False, sort_keys=True))
        return tuple(fresh)
