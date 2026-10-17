"""Skill effects read from the skill's text, for skills epic7db shows no effect icons for (SPEC D88).

epic7db has icons for the older heroes only; for a newer hero the kit reader found nothing and the simulation played it
as a plain attacker (SPEC D87). This reads the plain, unconditional sentences of an *active* skill's text: "has a 75%
chance to inflict fear for 1 turn", "dispelling all buffs before inflicting decreased Defense for 2 turns", "decreasing
Combat Readiness by 30%", "grants increased Attack to the caster for 2 turns and an extra turn", "dispels all debuffs
from all allies before increasing Combat Readiness by 25%". It builds the same `Effect`s the icon path builds (the
builder is passed in), so the chance, the duration and the receivers follow the same rules.

Conservative on purpose. A sentence that is conditional ("when", "after being attacked", "soulburn", ...), that has a
parenthesised form, or that acts on the caster itself ("dispelling all buffs from the caster") is left alone; so is
every effect the simulation has no kind for (bind, venom, erosion, offering, ...). Whatever effect word is left over
after the recognised phrases are taken out makes the skill still "not read": what this reads is simulated, and the
hero stays flagged for the rest. Source status: `community` (epic7db text, which can be out of date, NV-41).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from e7ac.predict.effects import Effect

type Build = Callable[[str, str], "Effect | None"]
"""(an effect name as epic7db's icons say it, the sentence it is in) -> the effect, or None when it is not modelled."""

_SENTENCES: Final = re.compile(r"(?<=[.!?])\s+")
_SKIP: Final = re.compile(
    r"\b(?:when|if|whenever|while|upon|soulburn|each time|every time|instead|unless|will|critical hit|defeated|"
    r"kills?|dies|death|target has|target is)\b|\(|"
    r"\bafter (?:being|an|a|every|attacking|the)\b|\bfrom the caster\b|\bthe caster'?s? buffs?\b",
    re.IGNORECASE,
)
_SCALING: Final = re.compile(r"damage dealt[^.]*", re.IGNORECASE)
_NAMES: Final = {
    "decreased defense": "decrease defense",
    "decreased attack": "decrease attack",
    "decreased speed": "decrease speed",
    "stun": "stun",
    "sleep": "sleep",
    "fear": "fear",
    "silence": "silence",
    "provoke": "provoke",
    "burn": "burn",
    "bleed": "bleed",
    "poison": "poison",
}
_INFLICT: Final = re.compile(
    r"(?:\d+% chance to )?inflict(?:s|ing)?\s+(?P<names>[a-z' ,]+?)(?=\s+for\s+\d+\s+turns?\b)", re.I
)
_LOWER: Final = re.compile(
    r"(?:decreas|reduc)(?:es|ing|e)\s+(?:the\s+)?(?P<what>attack|defense|speed|combat readiness)"
    r"(?:\s+by\s+\d+%)?(?:\s+for\s+\d+\s+turns?)?",
    re.IGNORECASE,
)
_RAISE_CR: Final = re.compile(
    r"increas(?:es|ing|e)\s+(?:the\s+)?combat readiness(?:\s+of the caster)?\s+by\s+\d+%", re.I
)
_BUFF: Final = re.compile(
    r"grant(?:s|ing)?\s+(?:an?\s+)?(?:increased|increase)\s+(?P<what>attack|defense|speed)"
    r"(?:\s+to\s+(?:the\s+caster|all\s+allies))?(?:\s+for\s+\d+\s+turns?)?",
    re.IGNORECASE,
)
_STATE: Final = re.compile(
    r"grant(?:s|ing)?\s+(?P<what>immunity|immortality|invincibility)(?:\s+to\s+(?:the\s+caster|all\s+allies))?"
    r"(?:\s+for\s+\d+\s+turns?)?",
    re.IGNORECASE,
)
_STATE_NAMES: Final = {"immunity": "immunity", "immortality": "immortal", "invincibility": "invincible"}
_EXTRA: Final = re.compile(r"\b(?:grant(?:s|ing)?|and)\s+(?:an\s+)?extra turn", re.IGNORECASE)
_FORMS: Final = re.compile(r"\([A-Z][a-z]+\)")
"""A skill that reads "(Rhianna) ... (Luciella) ...": two forms with a skill each, not one list of effects."""
_DISPEL: Final = re.compile(
    r"dispel(?:s|ling)?\s+(?:all|one|two|three)\s+(?P<what>buffs?|debuffs?)"
    r"(?:\s+from\s+(?P<who>all allies|all enemies))?",
    re.IGNORECASE,
)
_WORDS: Final = re.compile(
    r"\b(?:inflict\w*|dispel\w*|heal(?:s|ing|ed)?|recover\w*|barrier|stun\w*|fear\w*|provok\w*|silenc\w*|sleep\w*|venom\w*|"
    r"burn\w*|bleed\w*|poison\w*|grant\w*|immun\w*|immortal\w*|reviv\w*|resurrect\w*|steal\w*|invincib\w*|"
    r"extra turn|combat readiness|reflect\w*|decreas\w*|increas\w*|seal\w*|cleans\w*|restor\w*|absorb\w*|consum\w*)\b",
    re.IGNORECASE,
)


@dataclass(slots=True)
class TextRead:
    effects: list[Effect] = field(default_factory=list)
    unmodelled: list[str] = field(default_factory=list)
    """Recognised effects the simulation has no kind (or no amount) for."""
    leftover: bool = False
    """An effect word is still in the text after the recognised phrases: the skill is not fully read."""


def read_text(text: str, build: Build) -> TextRead:
    """What the plain sentences of an active skill's text say."""
    read = TextRead()
    if _FORMS.search(text):
        read.leftover = True
        return read
    for raw in _SENTENCES.split(_SCALING.sub(" ", text).strip()):
        sentence = raw.strip()
        if not sentence:
            continue
        if _SKIP.search(sentence):
            read.leftover = read.leftover or _WORDS.search(sentence) is not None
            continue
        rest = _consume(sentence, build, read)
        read.leftover = read.leftover or _WORDS.search(rest) is not None
    read.leftover = read.leftover or bool(read.unmodelled)  # an effect it names and cannot simulate is a gap too
    return read


def _consume(sentence: str, build: Build, read: TextRead) -> str:
    """The sentence with every recognised phrase taken out (the effects are added to `read`)."""

    placed: list[tuple[int, Effect]] = []

    def add(name: str, at: int) -> None:
        effect = build(name, sentence)
        if effect is None:
            read.unmodelled.append(name)
        else:
            placed.append((at, effect))

    def blank(found: re.Match[str]) -> str:
        return " " * len(found.group(0))  # same length: the positions of the other patterns stay valid

    def inflict(found: re.Match[str]) -> str:
        for part in re.split(r"\s*(?:,|\band\b)\s*", found.group("names").strip().lower()):
            if part in _NAMES:
                add(_NAMES[part], found.start())
            elif part:
                read.unmodelled.append(part)  # bind, erosion, ...: no kind for it
        return blank(found)

    def lower(found: re.Match[str]) -> str:
        what = found.group("what").lower()
        add("decrease combat readiness" if what == "combat readiness" else f"decrease {what}", found.start())
        return blank(found)

    def raise_cr(found: re.Match[str]) -> str:
        add("increase combat readiness", found.start())
        return blank(found)

    def buff(found: re.Match[str]) -> str:
        add(f"increase {found.group('what').lower()}", found.start())
        return blank(found)

    def state(found: re.Match[str]) -> str:
        add(_STATE_NAMES[found.group("what").lower()], found.start())
        return blank(found)

    def extra(found: re.Match[str]) -> str:
        add("extra turn", found.start())
        return blank(found)

    def dispel(found: re.Match[str]) -> str:
        if found.group("what").lower().startswith("buff"):
            add("dispel", found.start())
        elif found.group("who") == "all allies":
            add("cleanse", found.start())
        else:
            return found.group(0)  # debuffs of the enemy or of one ally: not what a dispel of ours means
        return blank(found)

    rest = sentence
    for pattern, handler in (
        (_INFLICT, inflict),
        (_DISPEL, dispel),
        (_LOWER, lower),
        (_RAISE_CR, raise_cr),
        (_BUFF, buff),
        (_STATE, state),
        (_EXTRA, extra),
    ):
        rest = pattern.sub(handler, rest)
    read.effects.extend(effect for _, effect in sorted(placed, key=lambda item: item[0]))
    return rest
