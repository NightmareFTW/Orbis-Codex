"""The export read back (SPEC D104): `e7 skills import FILE` puts the heroes of a skills export (the file
`e7 skills export` writes, which the user sends from their own PC) into this PC's store, so the skills read on one PC
feed the kits on another. The inverse of `skills.export`: the same lines, parsed. Readings that are not skills
(`skills.clean`) are left out."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Final

from e7ac.skills.clean import why_junk
from e7ac.skills.merge import place
from e7ac.skills.store import SkillStore, StoredSkill
from e7ac.sources.epic7db import SkillInfo
from e7ac.vision.skill_popup import EffectPanel, SkillPopup

HERO: Final = re.compile(r"^== (.+) \((c\d{4})\)\s*$")
SKILL: Final = re.compile(r"^\[\+(\d+)\] (.*)$")
GAIN: Final = re.compile(r"^([A-Za-z]+) (\d+)$")
TURNS: Final = re.compile(r"^(\d+) turns?$")
SOULBURN: Final = re.compile(r"^Soulburn(?: \((\d+) Soul\))?: (.*)$")
STEP: Final = re.compile(r"\+(\d+) (.+?)(?=; \+\d+ |$)")


@dataclass(slots=True)
class Imported:
    heroes: int = 0
    skills: int = 0
    left_out: list[str] = field(default_factory=list)
    """The readings that are not skills, "Hero: name"."""


def parse(text: str) -> dict[tuple[str, str], list[SkillPopup]]:
    """Hero (name, code) -> its popups, in the order of the file."""
    heroes: dict[tuple[str, str], list[SkillPopup]] = {}
    key: tuple[str, str] | None = None
    current: dict[str, object] | None = None

    def close() -> None:
        if key is not None and current is not None:
            heroes[key].append(_popup(key[0], current))

    for raw in text.splitlines():
        if raw.startswith("== "):
            close()
            current = None
            found = HERO.match(raw)
            if not found:  # the "== Effects (N)" section ends the heroes
                break
            key = (found.group(1), found.group(2))
            heroes[key] = []
        elif key is not None and (head := SKILL.match(raw)):
            close()
            name, costs = _split_costs(head.group(2))
            current = {"level": int(head.group(1)), "name": name, "costs": costs, "text": "", "effects": []}
        elif current is not None and raw.startswith("  "):
            _line(current, raw.strip())
    close()
    return heroes


def _split_costs(head: str) -> tuple[str, str]:
    """The skill's name and its cost facts: the facts come after the last " | " (a junk reading can have one in its
    name, a real skill name never does)."""
    name, bar, costs = head.rpartition(" | ")
    return (name, costs) if bar else (head, "")


def _line(current: dict[str, object], line: str) -> None:
    effects = current["effects"]
    assert isinstance(effects, list)
    if line.startswith("Enhance: "):
        current["enhance"] = tuple((int(n), d.strip()) for n, d in STEP.findall(line[len("Enhance: ") :]))
    elif (burn := SOULBURN.match(line)) is not None:
        current["soulburn_cost"] = int(burn.group(1)) if burn.group(1) else None
        current["soulburn"] = burn.group(2)
    elif line.startswith("Effect ") and ": " in line:
        title, _, text = line[len("Effect ") :].partition(": ")
        effects.append((title, text))
    elif not current["text"]:
        current["text"] = line
    else:
        current["text"] = f"{current['text']} {line}"


def _popup(hero: str, c: dict[str, object]) -> SkillPopup:
    gains: list[tuple[str, int]] = []
    cooldown: int | None = None
    note = ""
    for token in str(c["costs"]).split(", ") if c["costs"] else []:
        if (turns := TURNS.match(token)) is not None:
            cooldown = int(turns.group(1))
        elif (gain := GAIN.match(token)) is not None:
            gains.append((gain.group(1).title(), int(gain.group(2))))
        else:
            note = token
    level = int(str(c["level"]))
    raw_effects = c["effects"]
    assert isinstance(raw_effects, list)
    return SkillPopup(
        hero=hero,
        name=str(c["name"]),
        level=level or None,
        gains=tuple(gains),
        cooldown=cooldown,
        cooldown_note=note,
        text=str(c["text"]),
        soulburn=str(c.get("soulburn", "")),
        soulburn_cost=c.get("soulburn_cost"),  # type: ignore[arg-type]
        enhance=tuple(c.get("enhance", ())),  # type: ignore[arg-type]
        effects=tuple(EffectPanel(title, (), text) for title, text in raw_effects),
        confidence=1.0,
    )


def import_export(
    store: SkillStore,
    text: str,
    kit_of: Callable[[str], Mapping[int, SkillInfo]] | None = None,
) -> Imported:
    """Store every skill of the export that is one; each is placed (slot, passive) by epic7db's names when `kit_of`
    gives them."""
    done = Imported()
    for (name, code), popups in parse(text).items():
        kit = kit_of(name) if kit_of is not None else {}
        stored = 0
        for popup in popups:
            candidate = StoredSkill(
                code, popup.name, popup.level, popup.gains, popup.cooldown, popup.cooldown_note, popup.text,
                popup.soulburn, popup.soulburn_cost, popup.enhance, popup.effects, popup.confidence, "",
            )  # fmt: skip
            if why_junk(candidate) is not None:
                done.left_out.append(f"{name}: {popup.name}")
                continue
            slot, passive = place(popup.name, kit)
            store.save(code, popup, slot=slot, passive=passive)
            stored += 1
        done.heroes += bool(stored)
        done.skills += stored
    return done
