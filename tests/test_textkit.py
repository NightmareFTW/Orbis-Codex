"""Skill effects read from a skill's text when epic7db has no icons for it (SPEC D88): the plain, unconditional
sentences only; what it cannot read keeps the skill flagged "not read"."""

from __future__ import annotations

from e7ac.predict.effects import Effect, kit_from
from e7ac.predict.textkit import read_text
from e7ac.sources.epic7db import SkillInfo

# epic7db's texts as they are (newer heroes: no icons)
LADY_S1 = (
    "Attacks the enemy with divine lightning and has a 75% chance to inflict fear for 1 turn, before recovering Health "
    "of all allies proportional to the target's max Health ."
)
LADY_S3 = (
    "With the Goddess's authority, dispels all debuffs from all allies before increasing Combat Readiness by 25%. "
    "Consumes 20% of the current Health of the ally granted offering , granting abundance to all other allies for 2 "
    "turns."
)
ARUNKA_S1 = (
    "Attacks the enemy with a road sign and has a 60% chance to inflict decreased Defense for 1 turn. Damage dealt "
    "increases proportional to the caster's Defense ."
)
ARUNKA_S3 = (
    "Attacks the enemy at an incredible velocity, dispelling all buffs before inflicting decreased Defense for 2 "
    "turns. Grants a barrier to the caster for 2 turns. Damage dealt and barrier strength increase proportional to "
    "the caster's Defense ."
)
RINAK_S2 = (
    "Swiftly attacks the enemy, dispelling all buffs before decreasing Combat Readiness by 30% and inflicting "
    "pilfer . Grants increased Attack to the caster for 2 turns and an extra turn ."
)
RINAK_S3 = (
    "Nimbly attacks the enemy. Penetrates the target's Defense, and cannot trigger a critical hit . After attacking, "
    "activates a security sensor by a mistake, dispelling all buffs from the caster and inflicting stun for 1 turn, "
    "and deals 7000 fixed damage to all enemies. Ignores Effect Resistance ."
)
RHIANNA_S2 = (
    "(Rhianna) Attacks the enemy, dispelling two buffs before inflicting bind and decreased Defense for 2 turns. "
    "Swaps with Luciella and grants an extra turn . (Luciella) After granting increased Attack to the caster for 3 "
    "turns, attacks the enemy. Swaps with Rhianna and grants an extra turn ."
)
GENESIS_S2 = (
    "After inflicting seal and collapse on the enemy for 2 turns, attacks the enemy. A critical hit will grand an "
    "extra turn. Damage dealt increases proportional to the caster's max Health."
)
SETSUKA_S3 = (
    "Dispel all buffs from all enemies before inflicting beguile and decreased Hit Chance for 2 turns. Ignore Effect "
    "Resistance of targets with less max Health than the caster."
)
BELIAN_S1 = (
    "Attacks all enemies with a ray, with a 65% chance to dispel one buff . When used on the caster's turn, has a 35% "
    "chance to activate Incursion as an extra attack ."
)
PLAIN = "Attacks the enemy with scented paper."
NOTOS_S1 = (
    "Attacks the enemy with divinity and has a 75% chance to inflict decreased Defense for 1 turn. Damage dealt "
    "increases proportional to the caster's max Health ."
)


def _effect(name: str, sentence: str) -> Effect | None:
    from e7ac.predict.effects import _effect as build

    return build(name, None, sentence, {})


def read(text: str) -> tuple[list[tuple[str, bool, float, int, float]], list[str], bool]:
    found = read_text(text, _effect)
    effects = [(e.kind, e.debuff, e.value, e.turns, e.chance) for e in found.effects]
    return effects, found.unmodelled, found.leftover


def test_a_plain_effect_sentence_is_read_with_its_chance_and_duration() -> None:
    assert read(ARUNKA_S1) == ([("def", True, -0.7, 1, 0.6)], [], False)  # 60% chance, 1 turn; 70%: MECH-DMG-06
    assert read(NOTOS_S1) == ([("def", True, -0.7, 1, 0.75)], [], False)
    effects, unmodelled, leftover = read(LADY_S1)
    assert effects == [("fear", True, 0.0, 1, 0.75)] and unmodelled == []
    assert leftover  # ... and the heal of all allies is not read: the skill stays flagged


def test_several_effects_in_a_sentence_and_in_a_skill() -> None:
    effects, _, leftover = read(RINAK_S2)
    assert effects == [
        ("dispel", True, 0.0, 0, 1.0),
        ("cr", True, -0.3, 2, 1.0),
        ("atk", False, 0.5, 2, 1.0),
        ("extra_turn", False, 0.0, 2, 1.0),
    ]
    assert leftover  # "inflicting pilfer": no kind for it
    effects, _, leftover = read(LADY_S3)
    assert [e[0] for e in effects] == ["cleanse", "cr"] and effects[1][2] == 0.25
    assert leftover  # abundance, and the Health consumed for the offering


def test_a_barrier_without_an_amount_is_never_guessed() -> None:
    effects, _, leftover = read(ARUNKA_S3)
    assert [e[0] for e in effects] == ["dispel", "def"] and leftover  # the barrier has no amount in the data


def test_what_is_conditional_or_acts_on_the_caster_is_left_alone() -> None:
    assert read(RINAK_S3) == ([], [], True)  # "from the caster", "after attacking, ... by a mistake"
    # the plain first sentence is read (with its 65%); "When used on the caster's turn ..." is not
    assert read(BELIAN_S1)[0] == [("dispel", True, 0.0, 0, 0.65)]
    assert read(GENESIS_S2)[0] == []  # "A critical hit will grant an extra turn" is not an unconditional extra turn
    assert read(RHIANNA_S2) == ([], [], True)  # two forms, a skill each: nothing is read


def test_an_effect_named_and_not_simulated_is_listed_and_flags_the_skill() -> None:
    effects, unmodelled, leftover = read(SETSUKA_S3)
    assert [e[0] for e in effects] == ["dispel"] and unmodelled == ["beguile", "decreased hit chance"] and leftover


def test_a_skill_that_only_hits_has_nothing_to_read_and_nothing_left() -> None:
    assert read(PLAIN) == ([], [], False)
    assert read("") == ([], [], False)


def test_the_kit_takes_the_text_read_for_active_skills_only_and_clears_the_flag_when_all_is_read() -> None:
    kit = kit_from({1: SkillInfo(1, 0, {}, ARUNKA_S1), 3: SkillInfo(3, 3, {}, ARUNKA_S3)})
    assert [e.kind for e in kit.on_use[1]] == ["def"] and kit.unread == [3]  # S1 fully read, S3 has a barrier left
    assert [e.kind for e in kit.on_use[3]] == ["dispel", "def"]
    passive = "At the start of battle, grants a barrier and increases Combat Readiness by 20%."
    kit = kit_from({2: SkillInfo(2, None, {}, passive)})
    assert kit.unread == [2] and 2 not in kit.on_use  # a passive is never read from its words here
    icons = kit_from({1: SkillInfo(1, 0, {"Decrease Defense": 0.7}, ARUNKA_S1)})
    assert [e.kind for e in icons.on_use[1]] == ["def"]  # with icons, the icons are read, not the text (no doubles)


def test_health_is_not_a_heal() -> None:
    from e7ac.predict.effects import describes_effects

    assert not describes_effects("Attacks the enemy. Ignores the target's max Health.")
    assert describes_effects("Heals all allies.") and describes_effects("Recovers Health of all allies.")
