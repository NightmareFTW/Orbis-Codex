"""The skills read from the game as one text file the user can send (SPEC D91): the game's own words, per hero, then
the description of every effect panel seen. Plain text, readable by a person and by a program; kept out of the
repository on purpose (the game's text is the game's)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime

from e7ac.skills.store import SkillStore, StoredSkill


def export_text(
    store: SkillStore, names: Mapping[str, str], codes: Sequence[str] | None = None, now: datetime | None = None
) -> str:
    chosen = [c for c in (codes if codes is not None else store.heroes()) if store.load(c)]
    effects = store.effects()
    stamp = (now or datetime.now(UTC)).astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")
    skills = sum(len(store.load(c)) for c in chosen)
    lines = [f"# Orbis Codex: skills read from the game, {stamp}: {len(chosen)} heroes, {skills} skills"]
    for code in chosen:
        lines += ["", f"== {names.get(code, code)} ({code})"]
        for skill in store.load(code):
            lines += _skill(skill)
    lines += ["", f"== Effects ({len(effects)})"]
    for key in sorted(effects):
        entry = effects[key]
        marks = f" ({', '.join(entry.get('marks', []))})" if entry.get("marks") else ""
        lines.append(f"{entry['title']}{marks}: {entry['text']}")
    return "\n".join(lines) + "\n"


def _skill(skill: StoredSkill) -> list[str]:
    head = f"[{'+' + str(skill.level) if skill.level is not None else '+0'}] {skill.name}"
    facts = [f"{kind} {n}" for kind, n in skill.gains]
    if skill.cooldown is not None:
        facts.append(f"{skill.cooldown} turns")
    if skill.cooldown_note:
        facts.append(skill.cooldown_note)
    lines = [head + (" | " + ", ".join(facts) if facts else "")]
    lines.append(f"  {skill.text}")
    if skill.soulburn:
        cost = f" ({skill.soulburn_cost} Soul)" if skill.soulburn_cost else ""
        lines.append(f"  Soulburn{cost}: {skill.soulburn}")
    if skill.enhance:
        lines.append("  Enhance: " + "; ".join(f"+{n} {d}" for n, d in skill.enhance))
    lines += [f"  Effect {panel.title}: {panel.text}" for panel in skill.effects]
    return lines
