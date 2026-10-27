"""Win probability by simulating many battles (M11 part, SPEC D61): turn order and damage, nothing else yet.

Each battle: start CR 0-5% at random (MECH-CR-02), CR fills with speed and the first to 100% acts (MECH-CR-01); an
opponent's speed is drawn from Stove each battle. A unit uses S3 whenever its cooldown allows (epic7db; available
again N of the unit's own turns later, MECH-CD-01 `assumed`), else an active S2 whose cooldown allows, else S1
(MECH-AI-01 `assumed`; a skill that only resurrects waits for a dead ally); without a known cooldown, S3 on its first
action only (`assumed`); it attacks the living enemy it needs the fewest hits to defeat (focus fire,
`assumed`: the game's targeting is not known); a skill with more than one target hits every living enemy. Each hit
is critical with the attacker's crit chance (MECH-DMG-05). Skill effects from epic7db (SPEC D63, `predict/effects`):
attack/defense/speed changes, stun/sleep, combat readiness, extra turns, immortality, immunity, revives; debuffs land
by MECH-EFF-01. Survival (SPEC D68): heals (never above max Health), barriers (absorb damage before Health, the
stronger kept, MECH-BAR-01 `assumed`), resurrection, cleanse, dispel, invincibility; passive heals and barriers act at
the start of each of the caster's turns. Part 2 (SPEC D74): burn / bleed / poison tick at the start of the holder's
turn (MECH-DOT-01), provoke (S1 at the provoker) and silence (S1 only), counterattacks (S1 back, no chain). Hand kits
(SPEC D76): effects only when an enemy was defeated, health sacrifice, reflect, speed cap, no counterattacks, immune
debuffs, revive given to allies, effects when hit down to a Health share. SPEC D80: unable to counterattack / to be
buffed, buff durations cut, debuffs on every enemy, no Combat Readiness increase on one hero's turn, immortality on
lethal damage, effects only when a stun landed. SPEC D89: Offering (the holder takes 70% of the damage its allies
take, immortal once on lethal damage, an ally heal when it dies), Abundance (extra damage after attacking), a Combat
Readiness mirror, taunt (Ferocious Stand), Combat Readiness gained when hit, venom with its detonation, Effectiveness
buffs, fixed damage, Lingering Fragrance (a reflect for the whole team), an ally trigger (La Vie en Rose), Pilfer and
Spoils. Not modelled:
artifacts, combat set effects:
low confidence.
"""

from __future__ import annotations

import math
import random
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Final

from e7ac.domain.codes import Stat
from e7ac.predict.damage import CD_CAP, SkillPower, base_hit
from e7ac.predict.effects import DOT_ATK, POISON_MAX_HP, Effect, Kit
from e7ac.predict.turn_order import START_CR_MAX

RUNS: Final = 10_000
SEED: Final = 7
LAND_CAP: Final = 0.85
"""MECH-EFF-01: at most 85% for a debuff to land (15% minimum resist)."""
FEAR_DISPEL: Final = 0.4
"""MECH-FEAR-01 (`verified`, in-game text): a feared hero cannot act; at the start of its turn it has a 40% chance to
be dispelled."""
DEIFY_CRIT: Final = 0.5
"""Deify (`verified`, in-game text): "When attacking, increases Critical Hit Chance by 50%"."""
DEIFY_REDUCTION: Final = 0.5
"""Deify: "when attacked, grants 50% damage reduction" (applied to the damage of attacks, not to damage over time)."""
SPOILS_STEP: Final = 0.2
"""Spoils (Rinak, MECH-SPOIL-01, `verified` in the in-game text): Attack and Defense up by 20% per stack, up to
`SPOILS_CAP`."""
SPOILS_CAP: Final = 4
PILFER_STEP: Final = 0.2
"""Pilfer (`verified`): Attack and Defense of its holder down by 20%."""
MIN_COOLDOWN: Final = 2
"""MECH-CD-02 (`assumed`, NV-33): a 1-turn cooldown (New Moon Luna's S3 at +3) would, counted as MECH-CD-01 does, be
ready again at the very next own turn, i.e. no cooldown at all; it is read as "ready again two own turns later"."""
MAX_ACTIONS: Final = 200
"""A battle still going after this many actions is a draw (nobody can defeat anybody with the modelled skills)."""


@dataclass(frozen=True, slots=True)
class Combatant:
    label: str
    side: str
    stats: Mapping[Stat, float]
    speed: float | Callable[[random.Random], float]
    skills: Mapping[int, SkillPower]
    cooldowns: Mapping[int, int] = field(default_factory=dict)
    """Skill slot -> cooldown in turns (epic7db); missing: unknown."""
    kit: Kit = field(default_factory=Kit)
    """Skill effects (epic7db icons and text, SPEC D63)."""
    element: str = ""
    """Catalog element ("wind" = Earth): for what depends on the foremost ally (SPEC D79)."""


@dataclass(frozen=True, slots=True)
class HeroTotals:
    """What one hero did in an average simulated battle: the numbers of the game's Battle Results screen."""

    label: str
    side: str
    damage: float
    """Damage inflicted (attacks, counters, reflects, fixed and extra damage, detonations; not the damage over time)."""
    healed: float
    """Health restored to allies by its heals (never above max Health); the game may count more (NV-53)."""
    shielded: float
    """Barrier strength it granted."""


@dataclass(frozen=True, slots=True)
class BattleOdds:
    win: float
    loss: float
    draw: float
    mean_actions: float
    runs: int
    warnings: list[str] = field(default_factory=list)
    blind: tuple[str, ...] = ()
    """The heroes (label, with the skill slots) whose skills were not read: the simulation made them plain attackers, so
    this chance cannot be trusted (SPEC D87)."""
    heroes: tuple[HeroTotals, ...] = ()
    """Per hero, the average damage, healing and barriers of one battle (to compare with the Battle Results screen)."""
    low: float | None = None
    high: float | None = None
    """The chance against the worst and the best of the opponent builds simulated (SPEC D94); None: one build only."""
    raw: float | None = None
    """The simulation's own share of wins, when `win` was corrected for what it does not know (`predict.doubt`)."""

    @property
    def margin(self) -> float:
        """Half-width of the chance: the simulation's noise, or the spread over the opponent builds if that is wider."""
        noise = confidence_interval(self.win, self.runs)
        if self.low is None or self.high is None:
            return noise
        return max(noise, (self.high - self.low) / 2)


def simulate(units: Sequence[Combatant], runs: int = RUNS, seed: int = SEED) -> BattleOdds:
    sides = {u.side for u in units}
    if sides != {"you", "them"}:
        raise ValueError("both sides need at least one hero")
    warnings = [f"{u.label}: no damaging skill known" for u in units if not u.skills]
    hits = {  # non-critical damage of each attacker's skills on each enemy, computed once
        (i, j, slot): base_hit(a.stats, skill, d.stats)
        for i, a in enumerate(units)
        for j, d in enumerate(units)
        if a.side != d.side
        for slot, skill in a.skills.items()
    }
    rng = random.Random(seed)
    wins = losses = actions_total = 0
    totals = [[0.0, 0.0, 0.0] for _ in units]
    for _ in range(runs):
        result, actions = _battle(units, hits, rng, totals)
        wins += result == "you"
        losses += result == "them"
        actions_total += actions
    heroes = tuple(
        HeroTotals(u.label, u.side, damage / runs, healed / runs, shielded / runs)
        for u, (damage, healed, shielded) in zip(units, totals, strict=True)
    )
    blind = tuple(f"{u.label} ({', '.join(f'S{s}' for s in u.kit.unread)})" for u in units if u.kit.unread)
    return BattleOdds(
        wins / runs, losses / runs, (runs - wins - losses) / runs, actions_total / runs, runs, warnings, blind, heroes
    )


def _battle(
    units: Sequence[Combatant],
    hits: Mapping[tuple[int, int, int], float],
    rng: random.Random,
    totals: list[list[float]] | None = None,
) -> tuple[str | None, int]:
    state = [_State() for _ in units]
    result = _play(units, hits, rng, state)
    if totals is not None:
        for row, s in zip(totals, state, strict=True):
            row[0] += s.dealt
            row[1] += s.healed
            row[2] += s.shielded
    return result


def _play(
    units: Sequence[Combatant], hits: Mapping[tuple[int, int, int], float], rng: random.Random, state: list[_State]
) -> tuple[str | None, int]:
    n = len(units)
    speeds = [u.speed(rng) if callable(u.speed) else u.speed for u in units]
    cr = [rng.uniform(0, START_CR_MAX) for _ in units]
    hp = [u.stats[Stat.HP] for u in units]
    acted = [False] * n
    waiting: list[dict[int, int]] = [{} for _ in units]  # skill slot -> own turns until ready again
    for i, unit in enumerate(units):
        for effect in unit.kit.at_start:
            _use(effect, i, [], units, hp, cr, state, rng)
        for slot in unit.kit.start_wait:  # a skill that begins the battle with its full cooldown (Notos S3)
            waiting[i][slot] = unit.cooldowns.get(slot, 0)
        if unit.kit.eff_speed:  # Effectiveness from Speed ("30% of Speed", Rhianna and Luciella)
            state[i].eff_extra = unit.kit.eff_speed * speeds[i] / 100  # stays all battle: no dispel takes it
        if unit.kit.spirit_full:  # Fighting Spirit at the start of the battle (Young Senya S2)
            front = _foremost(units, hp, i)
            earth = front is not None and units[front].element == "wind"
            state[i].spirit = unit.kit.spirit_start + (unit.kit.spirit_bonus if earth else 0)
    for actions in range(1, MAX_ACTIONS + 1):
        alive = [i for i in range(n) if hp[i] > 0]
        caps = [_kit(units, state, i).speed_cap for i in range(n)]
        speed = [speeds[i] * (1.0 if caps[i] else state[i].factor("spd")) for i in range(n)]
        for c in alive:  # a speed cap (Harsetti): every other hero at most this share of the caster's speed
            if caps[c]:
                speed = [v if i == c else min(v, caps[c] * speed[c]) for i, v in enumerate(speed)]
        actor = min(alive, key=lambda i: (1 - cr[i]) / speed[i])
        elapsed = max(0.0, (1 - cr[actor]) / speed[actor])
        for i in alive:
            cr[i] += elapsed * speed[i]
        cr[actor] = 0.0
        unit, me = units[actor], state[actor]
        kit = _kit(units, state, actor)  # a sealed hero plays without its passives
        frozen = list(cr) if kit.cr_lock else None  # MECH-CRLOCK-01: Combat Readiness cannot go up on this turn
        for effect in kit.on_turn:  # passive heals / barriers at the start of the turn
            _use(effect, actor, [], units, hp, cr, state, rng)
        if me.hots:  # continuous healing at the start of the turn
            maximum = _max_hp(units, state, actor)
            for share, _ in me.hots:
                hp[actor] = min(maximum, hp[actor] + share * maximum)
            me.hots = [(share, turns - 1) for share, turns in me.hots if turns > 1]
        if me.erosions:  # erosion: a share of the Health lost so far, at the start of the turn (`verified`, popup text)
            lost = unit.stats[Stat.HP] - hp[actor]
            for share, _ in me.erosions:
                me.hurt(hp, actor, share * max(0.0, lost))
            me.erosions = [(share, turns - 1) for share, turns in me.erosions if turns > 1]
            if hp[actor] <= 0:
                _lethal(units, hp, cr, state, actor, rng)
                standing = {units[i].side for i in range(n) if hp[i] > 0}
                if len(standing) == 1:
                    return standing.pop(), actions
                continue
        if me.dots or me.venoms:  # damage over time at the start of the turn (MECH-DOT-01; venom: 10% of max Health)
            for damage, _ in me.dots:
                me.hurt(hp, actor, damage)
            for share, _ in me.venoms:
                me.hurt(hp, actor, share * unit.stats[Stat.HP])
            me.dots = [(damage, turns - 1) for damage, turns in me.dots if turns > 1]
            me.venoms = [(share, turns - 1) for share, turns in me.venoms if turns > 1]
            if hp[actor] <= 0:
                _lethal(units, hp, cr, state, actor, rng)
            if hp[actor] <= 0:
                standing = {units[i].side for i in range(n) if hp[i] > 0}
                if len(standing) == 1:
                    return standing.pop(), actions
                continue
        if me.timers.get("fear", 0) > 0 and rng.random() < FEAR_DISPEL:
            del me.timers["fear"]
        if _cannot_act(me):
            me.tick()
            continue
        for ready in waiting[actor]:
            waiting[actor][ready] = max(0, waiting[actor][ready] - 1 - me.cd_cut)
        me.cd_cut = 0
        me.low_wait = max(0, me.low_wait - 1)
        me.guard_wait = max(0, me.guard_wait - 1)
        me.ally_wait = max(0, me.ally_wait - 1)
        dead_ally = any(hp[j] <= 0 and units[j].side == unit.side for j in range(n))
        held = any(me.timers.get(kind, 0) > 0 for kind in ("silence", "provoke", "redirect_provoke"))
        slot = 1 if held else _choose(unit, waiting[actor], acted[actor], dead_ally)
        if slot != 1 and unit.cooldowns.get(slot):
            # counts down at the start of each own turn (MECH-CD-01, MECH-CD-02)
            waiting[actor][slot] = max(unit.cooldowns[slot], MIN_COOLDOWN)
        acted[actor] = True
        enemies = [j for j in alive if units[j].side != unit.side]
        skill = unit.skills.get(slot)
        if slot == 1 and kit.variant_slot in unit.skills and me.buffed:  # Nimble Sword: S1 with a buff
            skill = unit.skills[kit.variant_slot]
            for effect in unit.kit.variant_effects:
                _use(effect, actor, [], units, hp, cr, state, rng)
        targets: list[int] = []
        killed = False
        crit = False
        if skill is not None and enemies:
            if skill.targets == 2 and len(enemies) > 2:  # "attacks two enemies": two of them, at random (`assumed`)
                targets = rng.sample(enemies, 2)
            elif skill.targets > 1:
                targets = enemies
            else:  # a hero in front with Ferocious Stand is the only one a single-target skill can pick
                # a hero in stealth cannot be picked while it has an ally (MECH-STEALTH-01)
                open_to_it = [j for j in enemies if not _hidden(units, hp, state, j)] or enemies
                taunting = [j for j in open_to_it if units[j].kit.taunt and j == _front(units, hp, units[j].side)]
                targets = taunting[:1] or [min(open_to_it, key=lambda j: hp[j] / hits[(actor, j, slot)])]
            if me.timers.get("provoke", 0) > 0 and me.provoker in enemies:
                targets = [me.provoker]  # provoked: attacks its provoker
            if me.timers.get("redirect_provoke", 0) > 0:  # MECH-PROV-01 (verified in game): basic skill on the enemy
                targets = [max(enemies, key=lambda j: units[j].stats[Stat.HP])]  # with the highest max Health
            for effect in unit.kit.before_use.get(slot, []):  # "after inflicting seal ..., attacks": on the targets
                if effect.form < 0 or effect.form == me.form:
                    _use(effect, actor, targets, units, hp, cr, state, rng)
            crit = skill.can_crit and rng.random() < _crit_chance(units, state, actor)
            factor = min(unit.stats[Stat.CRIT_DAMAGE], CD_CAP) if crit else 1.0
            attack = {**unit.stats, Stat.ATK: unit.stats[Stat.ATK] * me.factor("atk")}
            for j in targets:
                guard = {**units[j].stats, Stat.DEF: units[j].stats[Stat.DEF] * state[j].factor("def")}
                used = skill
                top = unit.kit.pierce_top.get(slot)
                if top and j == max(enemies, key=lambda e: units[e].stats[Stat.HP]):
                    used = replace(skill, penetration=max(skill.penetration, top))
                resisted = max((_kit(units, state, m).pen_resist for m in _allies(units, hp, j)), default=0.0)
                if resisted and used.penetration:  # Penetration Resistance of the target's side (MECH-PENRES-01)
                    used = replace(used, penetration=used.penetration * (1 - min(resisted, 1.0)))
                lost, lethal = _strike(units, hp, cr, state, j, base_hit(attack, used, guard) * factor, rng, actor)
                if lethal:
                    killed = killed or hp[j] <= 0
                else:
                    _after_hit(j, actor, lost, slot not in unit.kit.uncounterable, units, hp, cr, state, hits, rng)
                if me.timers.get("abundance", 0) > 0 and hp[j] > 0:  # Abundance, MECH-ABUND-01
                    _strike(units, hp, cr, state, j, me.abundance, rng, actor)
                    killed = killed or hp[j] <= 0
        stunned = {j: state[j].timers.get("stun", 0) for j in targets}
        form = me.form  # the effects of a skill act in the form it was used in, even if the skill swaps
        proc = False
        proc_kill = False
        for effect in unit.kit.on_use.get(slot, []):
            if effect.form >= 0 and effect.form != form:
                continue
            if effect.condition == "kill" and not killed:
                continue
            if effect.condition == "stun" and not any(state[j].timers.get("stun", 0) > stunned[j] for j in targets):
                continue
            if effect.condition == "crit" and not crit:
                continue
            if effect.condition == "debuffed" and not any(state[j].debuffed for j in targets if hp[j] > 0):
                continue
            if effect.condition == "proc" and not proc:
                continue
            if effect.condition == "proc_kill" and not proc_kill:
                continue
            if effect.condition == "cd3" and waiting[actor].get(3, 0) <= 0:  # while its S3 is on cooldown
                continue
            if effect.condition == "half" and not any(0 < hp[j] <= 0.5 * units[j].stats[Stat.HP] for j in targets):
                continue
            if effect.kind == "swap":  # Rhianna <-> Luciella
                me.form = 1 - me.form
                continue
            if effect.kind == "extra_attack":  # an attack again, with S1 or the skill of `value` (Belian's Incursion)
                proc = rng.random() < effect.chance
                if proc:
                    proc_kill = _extra_attack(actor, targets, units, hp, cr, state, hits, rng, int(effect.value) or 1)
                continue
            if effect.kind == "dual_attack":
                _dual_attack(actor, targets, units, hp, cr, state, hits, rng, effect.receivers == "highest_atk")
                continue
            if effect.kind == "reset_cd":  # the cooldown of a skill is over at once (Arbiter Vildred's Dark Blade)
                waiting[actor][int(effect.value)] = 0
                continue
            _use(effect, actor, targets, units, hp, cr, state, rng)
        if frozen is not None:
            for i in range(n):
                cr[i] = min(cr[i], frozen[i])
        if me.beguiled and hp[actor] > 0:  # Beguile: the holder's allies suffer when its turn ends (Setsuka's S3)
            for m in _allies(units, hp, actor):
                if m != actor:
                    _strike(units, hp, cr, state, m, me.beguiled * units[m].stats[Stat.HP], rng)
            me.beguiled = 0.0
        me.tick()
        standing = {units[i].side for i in range(n) if hp[i] > 0}
        if len(standing) == 1:
            return standing.pop(), actions
    return None, MAX_ACTIONS


def _choose(unit: Combatant, waiting: Mapping[int, int], acted: bool, dead_ally: bool) -> int:
    """S3, else an active S2, when ready (MECH-AI-01 `assumed`); else S1."""
    for slot in (3, 2):
        effects = unit.kit.on_use.get(slot, [])
        if slot not in unit.skills and not effects:
            continue
        cooldown = unit.cooldowns.get(slot)
        if slot == 2 and not cooldown:
            continue  # a passive S2, or not known to be active
        if not (waiting.get(slot, 0) == 0 if cooldown is not None else not acted):
            continue
        if slot not in unit.skills and all(e.kind == "resurrect" for e in effects) and not dead_ally:
            continue  # nobody to bring back yet
        return slot
    return 1


@dataclass(slots=True)
class _State:
    """Active effects of one unit: stat changes (kind -> (value, turns)), timers (stun, immortal, immunity,
    invincible) and a barrier."""

    buffs: dict[str, tuple[float, int]] = field(default_factory=dict)
    debuffs: dict[str, tuple[float, int]] = field(default_factory=dict)
    timers: dict[str, int] = field(default_factory=dict)
    revived: bool = False
    barrier: float = 0.0
    barrier_turns: int = 0
    dots: list[tuple[float, int]] = field(default_factory=list)
    """Damage over time: (damage per turn, turns left)."""
    provoker: int = -1
    revive_hp: float = 0.0
    """Health share restored by a revive status (kind "revive_buff")."""
    low_wait: int = 0
    """Own turns until the low-Health trigger works again."""
    guard_wait: int = 0
    """Own turns until the "ally attacked" effects work again."""
    hots: list[tuple[float, int]] = field(default_factory=list)
    """Continuous healing: (share of max Health at the start of each own turn, turns left); it stacks."""
    spirit: float = 0.0
    ally_wait: int = 0
    """Own turns until the "ally attacked" trigger (La Vie en Rose) works again."""
    venoms: list[tuple[float, int]] = field(default_factory=list)
    """Venom: (share of max Health per turn, turns left); it ticks at the start of the holder's turn."""
    offering: bool = False
    """This hero was granted offering: it takes `offering_share` of the damage its allies take."""
    offering_share: float = 0.0
    offering_turns: int = 0
    """Turns of immortality on lethal damage, once per battle."""
    offering_used: bool = False
    offering_by: int = -1
    """The hero that granted the offering."""
    abundance: float = 0.0
    """Additional damage after each attack while the "abundance" timer runs."""
    spoils: int = 0
    pilfer: bool = False
    pilfer_by: int = -1
    dealt: float = 0.0
    """Totals of this battle (HeroTotals): damage inflicted, Health restored to allies, barrier strength granted."""
    healed: float = 0.0
    shielded: float = 0.0
    cd_cut: int = 0
    """Turns every cooldown of this hero still has to lose for the deaths it saw (`Kit.death_cd`)."""
    beguiled: float = 0.0
    """Beguile: share of max Health that each other ally of the holder suffers when the holder's turn ends."""
    eff_extra: float = 0.0
    """Effectiveness that lasts the whole battle (`Kit.eff_speed`)."""
    form: int = 0
    """Which of its two forms the hero is in (Rhianna and Luciella swap at each S2)."""
    erosions: list[tuple[float, int]] = field(default_factory=list)
    """Erosion: (share of the holder's lost Health per turn, turns left); it ticks at the start of the holder's turn."""
    collapse: float = 0.0
    """Collapse: share of max Health the holder cannot have while the "collapse" timer runs."""

    @property
    def buffed(self) -> bool:
        """Whether the hero holds any buff (a stat buff, a status buff, a barrier, continuous healing, Spoils)."""
        return bool(
            self.buffs
            or self.hots
            or self.barrier > 0
            or self.spoils > 0
            or any(self.timers.get(kind, 0) > 0 for kind in _BUFF_TIMERS)
        )

    @property
    def debuffed(self) -> bool:
        """Whether the hero holds any debuff."""
        return bool(
            self.debuffs
            or self.dots
            or self.venoms
            or self.erosions
            or self.beguiled > 0
            or self.pilfer
            or any(self.timers.get(kind, 0) > 0 for kind in _DEBUFF_TIMERS)
        )

    def factor(self, kind: str) -> float:
        extra = (SPOILS_STEP * self.spoils - PILFER_STEP * self.pilfer) if kind in ("atk", "def") else 0.0
        buff = 0.0 if self.timers.get("block", 0) > 0 else self.buffs.get(kind, (0.0, 0))[0]  # block: no buff counts
        return max(0.0, 1 + buff + self.debuffs.get(kind, (0.0, 0))[0] + extra)

    def apply(self, effect: Effect) -> None:
        if effect.kind == "revive_buff":
            self.timers["revive_buff"] = max(self.timers.get("revive_buff", 0), effect.turns)
            self.revive_hp = effect.value
        elif effect.kind in ("atk", "def", "spd", "eff", "res"):
            table = self.debuffs if effect.debuff else self.buffs
            old = table.get(effect.kind, (0.0, 0))
            table[effect.kind] = (effect.value if abs(effect.value) >= abs(old[0]) else old[0], effect.turns)
        elif effect.kind in _TIMERS:
            self.timers[effect.kind] = max(self.timers.get(effect.kind, 0), effect.turns)
            if effect.kind == "abundance":
                self.abundance = effect.value

    def hurt(self, hp: list[float], j: int, damage: float) -> None:
        """Invincibility blocks the damage; a barrier absorbs it before Health (MECH-BAR-01)."""
        if self.timers.get("invincible", 0) > 0:
            return
        absorbed = min(self.barrier, damage)
        self.barrier -= absorbed
        hp[j] -= damage - absorbed

    def remove(self, debuffs: bool, count: int) -> None:
        """Cleanse (debuffs) or dispel (buffs): all when `count` is 0."""
        kinds = (
            [("timer", k) for k in _DEBUFF_TIMERS]
            + [("dot", "")] * len(self.dots)
            + [("venom", "")] * len(self.venoms)
            + [("erosion", "")] * len(self.erosions)
            + [("beguile", "")] * int(self.beguiled > 0)
            + [("pilfer", "")] * int(self.pilfer)
            + [("debuff", k) for k in self.debuffs]
            if debuffs
            else [("timer", k) for k in _BUFF_TIMERS]
            + [("hot", "")] * len(self.hots)
            + [("barrier", "")]
            + [("spoils", "")] * int(self.spoils > 0)
            + [("buff", k) for k in self.buffs]
        )
        removed = 0
        for table, kind in kinds:
            if count and removed >= count:
                return
            removed += self._drop(table, kind)

    def _drop(self, table: str, kind: str) -> bool:
        if table == "timer":
            return self.timers.pop(kind, 0) > 0
        if table == "debuff":
            return self.debuffs.pop(kind, None) is not None
        if table == "buff":
            return self.buffs.pop(kind, None) is not None
        if table == "dot":
            return bool(self.dots) and self.dots.pop(0) is not None
        if table == "hot":
            return bool(self.hots) and self.hots.pop(0) is not None
        if table == "venom":
            return bool(self.venoms) and self.venoms.pop(0) is not None
        if table == "beguile":
            had, self.beguiled = self.beguiled > 0, 0.0
            return had
        if table == "erosion":
            return bool(self.erosions) and self.erosions.pop(0) is not None
        if table == "pilfer":
            had, self.pilfer = self.pilfer, False
            return had
        if table == "spoils":
            had, self.spoils = self.spoils > 0, 0
            return had
        had = self.barrier > 0
        self.barrier, self.barrier_turns = 0.0, 0
        return had

    def shorten(self, turns: int) -> None:
        """Every buff lasts `turns` fewer own turns, the ones with none left are gone (Harsetti S3, MECH-BUFF-02)."""
        for kind, (value, left) in list(self.buffs.items()):
            if left <= turns:
                del self.buffs[kind]
            else:
                self.buffs[kind] = (value, left - turns)
        for kind in _BUFF_TIMERS:
            if kind in self.timers:
                if self.timers[kind] <= turns:
                    del self.timers[kind]
                else:
                    self.timers[kind] -= turns
        self.hots = [(share, left - turns) for share, left in self.hots if left > turns]
        if self.barrier_turns:
            self.barrier_turns -= turns
            if self.barrier_turns <= 0:
                self.barrier, self.barrier_turns = 0.0, 0

    def tick(self) -> None:
        """End of the unit's own turn: every duration goes down by one."""
        for table in (self.buffs, self.debuffs):
            for kind, (value, turns) in list(table.items()):
                if turns <= 1:
                    del table[kind]
                else:
                    table[kind] = (value, turns - 1)
        for kind in list(self.timers):
            self.timers[kind] -= 1
            if self.timers[kind] <= 0:
                del self.timers[kind]
        if self.barrier_turns:
            self.barrier_turns -= 1
            if self.barrier_turns <= 0:
                self.barrier = 0.0


_TIMERS: Final = (
    "stun",
    "immortal",
    "immunity",
    "invincible",
    "provoke",
    "silence",
    "counter",
    "revive_buff",
    "redirect_provoke",
    "fear",
    "unable_counter",
    "unable_buff",
    "abundance",
    "seal",
    "collapse",
    "deify",
    "nullifier",
    "sleep",
    "restrict",
    "stealth",
    "block",
)
_BUFF_TIMERS: Final = (
    "immortal",
    "invincible",
    "immunity",
    "counter",
    "revive_buff",
    "abundance",
    "deify",
    "nullifier",
    "stealth",
)
_DEBUFF_TIMERS: Final = (
    "stun",
    "fear",
    "provoke",
    "silence",
    "redirect_provoke",
    "unable_counter",
    "unable_buff",
    "seal",
    "collapse",
    "sleep",
    "restrict",
    "block",
)
_BUFFS: Final = frozenset(
    {
        "atk",
        "def",
        "spd",
        "eff",
        "res",
        "abundance",
        "deify",
        "nullifier",
        "stealth",
        "immortal",
        "immunity",
        "invincible",
        "counter",
        "revive_buff",
        "hot",
        "barrier",
    }
)
"""What "unable to be buffed" blocks (MECH-BUFF-01 `assumed`): stat buffs, status buffs, continuous healing, barriers;
not heals, Combat Readiness or cleanses."""


def _kit(units: Sequence[Combatant], state: Sequence[_State], j: int) -> Kit:
    """The kit of `j` as it works now: a sealed hero has lost its passives (MECH-SEAL-01)."""
    return units[j].kit.sealed() if state[j].timers.get("seal", 0) > 0 else units[j].kit


def _hidden(units: Sequence[Combatant], hp: Sequence[float], state: Sequence[_State], j: int) -> bool:
    """Stealth: "when another ally is present, prevents enemies from selecting the bearer as a skill target"."""
    return state[j].timers.get("stealth", 0) > 0 and len(_allies(units, hp, j)) > 1


def _cannot_act(state: _State) -> bool:
    """Stunned, feared or asleep: no turn and no counterattack."""
    return any(state.timers.get(kind, 0) > 0 for kind in ("stun", "fear", "sleep"))


def _crit_chance(units: Sequence[Combatant], state: Sequence[_State], i: int) -> float:
    """Critical Hit Chance of `i` now: Deify adds 50 points while it attacks (MECH-DEIFY-01)."""
    bonus = DEIFY_CRIT if state[i].timers.get("deify", 0) > 0 else 0.0
    return min(units[i].stats[Stat.CRIT_CHANCE] + bonus, 1.0)


def _max_hp(units: Sequence[Combatant], state: Sequence[_State], j: int) -> float:
    """Max Health of `j` now: a collapsed hero has lost a share of it (MECH-COLLAPSE-01)."""
    full = units[j].stats[Stat.HP]
    return full * (1 - state[j].collapse) if state[j].timers.get("collapse", 0) > 0 else full


def _allies(units: Sequence[Combatant], hp: Sequence[float], i: int) -> list[int]:
    return [j for j in range(len(units)) if units[j].side == units[i].side and hp[j] > 0]


def _front(units: Sequence[Combatant], hp: Sequence[float], side: str) -> int | None:
    """The first living hero of a side in the order the team was given (the front row, `assumed`, NV-43)."""
    return next((m for m in range(len(units)) if units[m].side == side and hp[m] > 0), None)


def _strike(
    units: Sequence[Combatant],
    hp: list[float],
    cr: list[float],
    state: list[_State],
    j: int,
    damage: float,
    rng: random.Random,
    by: int = -1,
) -> tuple[float, bool]:
    """Direct damage to `j` (a hit, a counter, a reflect, fixed damage): the ally holding its side's offering takes its
    share instead (MECH-OFFER-01, `verified` in the in-game text), then barrier and Health, then the lethal rules.
    Returns the Health `j` itself lost and whether the damage was lethal to it (before immortality or a revive)."""
    if by >= 0 and state[j].timers.get("deify", 0) > 0:  # an attack on a hero holding Deify: 50% less (MECH-DEIFY-01)
        damage *= 1 - DEIFY_REDUCTION
    cap = _kit(units, state, j).hit_cap
    if cap:  # damage suffered in one attack never exceeds a share of max Health (Sixth Sense, `verified`)
        damage = min(damage, cap * units[j].stats[Stat.HP])
    before = hp[j]
    if by >= 0:
        state[by].dealt += damage
    holder = next((h for h in _allies(units, hp, j) if h != j and state[h].offering), None)
    if holder is not None and damage > 0:
        shared = state[holder].offering_share * damage
        state[holder].hurt(hp, holder, shared)
        damage -= shared
        if hp[holder] <= 0:
            _lethal(units, hp, cr, state, holder, rng)
    state[j].hurt(hp, j, damage)
    if by >= 0 and damage > 0:  # an attack wakes a sleeper (`assumed`, NV-72) and ends stealth (`verified`)
        state[j].timers.pop("sleep", None)
        state[j].timers.pop("stealth", None)
    lethal = hp[j] <= 0
    if lethal:
        _lethal(units, hp, cr, state, j, rng)
    return before - hp[j], lethal


def _lethal(
    units: Sequence[Combatant], hp: list[float], cr: list[float], state: list[_State], j: int, rng: random.Random
) -> None:
    """Lethal damage: immortality keeps 1 HP; the offering holder is immortal once (MECH-OFFER-01); a revive passive
    works once (MECH via SPEC D63, `assumed`)."""
    kit = _kit(units, state, j)
    if state[j].timers.get("immortal", 0) > 0:
        hp[j] = 1.0
    elif state[j].offering and not state[j].offering_used:
        state[j].offering_used = True
        hp[j] = 1.0
        state[j].timers["immortal"] = max(state[j].offering_turns, 1)
    elif state[j].timers.pop("revive_buff", 0) > 0:  # a revive status given to an ally (Maid Chloe)
        hp[j] = units[j].stats[Stat.HP] * state[j].revive_hp
    elif kit.lethal_immortal and not state[j].revived:  # immortal for a few turns, once (Blood Blade Karin S2)
        state[j].revived = True
        hp[j] = 1.0
        state[j].timers["immortal"] = kit.lethal_immortal
    elif kit.revive is not None and not state[j].revived:
        state[j].revived = True
        hp[j] = units[j].stats[Stat.HP] * kit.revive
        for effect in kit.revive_effects:
            if effect.kind == "cr":
                cr[j] = min(1.0, cr[j] + effect.value)
            else:
                state[j].apply(effect)
    if hp[j] <= 0:  # a hero died: the heroes with Sixth Sense lose a turn of cooldown
        for m in range(len(units)):
            if hp[m] > 0 and (cut := _kit(units, state, m).death_cd):
                state[m].cd_cut += cut
    if hp[j] <= 0 and state[j].offering:  # the ally granted offering died: the granter's heal
        state[j].offering = False
        owner = state[j].offering_by
        if owner >= 0:
            for effect in units[owner].kit.offering_death_effects:
                _use(effect, owner, [], units, hp, cr, state, rng)


def _raise_cr(
    units: Sequence[Combatant], hp: Sequence[float], cr: list[float], state: Sequence[_State], i: int, amount: float
) -> None:
    """Combat Readiness of `i` goes up by `amount` (never above 100%); every living enemy with a CR mirror gains its
    share of what was added (MECH-CRMIRROR-01, `verified`: Lady of the Scales S2)."""
    if units[i].kit.cr_immune or state[i].timers.get("restrict", 0) > 0:  # Notos; Restrict (MECH-RESTRICT-01)
        return
    gained = max(0.0, min(1.0, cr[i] + amount) - cr[i])
    cr[i] += gained
    if gained <= 0:
        return
    for m in range(len(units)):
        if units[m].side != units[i].side and hp[m] > 0 and (mirror := _kit(units, state, m).cr_mirror):
            cr[m] = min(1.0, cr[m] + mirror * gained)


def _lift_pilfers(state: Sequence[_State], j: int) -> None:
    """The Spoils of `j` were dispelled: the Pilfer it inflicted goes with them (`verified`, Spoils text)."""
    for other in state:
        if other.pilfer and other.pilfer_by == j:
            other.pilfer = False


def _receivers(
    effect: Effect, actor: int, units: Sequence[Combatant], hp: Sequence[float], state: Sequence[_State]
) -> list[int]:
    allies = _allies(units, hp, actor)
    if effect.receivers == "all" or (not effect.receivers and effect.all_allies):
        return allies
    if effect.receivers == "others":
        return [j for j in allies if j != actor]
    if effect.receivers == "not_offering":  # "all other allies" of the one granted offering (Abundance)
        return [j for j in allies if not state[j].offering]
    if effect.receivers.startswith("lowest:"):
        count = int(effect.receivers.split(":")[1])
        return sorted(allies, key=lambda j: hp[j] / units[j].stats[Stat.HP])[:count]
    return [actor]


def _use(
    effect: Effect,
    actor: int,
    targets: Sequence[int],
    units: Sequence[Combatant],
    hp: list[float],
    cr: list[float],
    state: list[_State],
    rng: random.Random,
) -> None:
    if hp[actor] <= 0:
        return
    caster = units[actor].stats
    enemies = [j for j in range(len(units)) if units[j].side != units[actor].side and hp[j] > 0]
    if effect.kind == "random":  # one of several effects, picked for each target (Tenebria's random debuff)
        for j in enemies if effect.receivers == "enemies" else [t for t in targets if hp[t] > 0]:
            picked = rng.choice(effect.options)
            _use(replace(picked, chance=picked.chance * effect.chance), actor, [j], units, hp, cr, state, rng)
        return
    if effect.kind == "sacrifice":  # health paid by the caster, never to death
        hp[actor] = max(1.0, hp[actor] * (1 - effect.value))
        return
    if effect.kind == "spirit":  # Fighting Spirit gained by the caster
        state[actor].spirit += effect.value
        return
    if effect.kind == "shorten":  # buff durations cut: a guaranteed effect, not an effect chance (MECH-BUFF-02)
        for j in targets:
            if hp[j] > 0:
                state[j].shorten(effect.turns)
        return
    if effect.kind == "offering":  # granted to the foremost ally (`assumed`: the first other hero, NV-43)
        front = _foremost(units, hp, actor)
        if front is not None:
            holder = state[front]
            holder.offering, holder.offering_share = True, effect.value
            holder.offering_turns, holder.offering_by = effect.turns, actor
        return
    if effect.kind == "offering_cost":  # Health the holder pays (never to death)
        for h in _allies(units, hp, actor):
            if state[h].offering and state[h].offering_by == actor:
                hp[h] = max(1.0, hp[h] * (1 - effect.value))
        return
    if effect.kind in ("fixed_damage", "detonate"):  # damage that ignores Defense and always lands
        for j in enemies if effect.receivers == "enemies" else [t for t in targets if hp[t] > 0]:
            damage = effect.value
            if effect.kind == "detonate":  # every venom at once: share x turns left, for each stack (MECH-VENOM-01)
                damage = sum(share * units[j].stats[Stat.HP] * left for share, left in state[j].venoms)
                state[j].venoms = []
                lost = max(0.0, units[j].stats[Stat.HP] - hp[j])  # and every erosion: its share of the lost Health
                damage += sum(share * lost * left for share, left in state[j].erosions)
                state[j].erosions = []
            if damage > 0:
                _strike(units, hp, cr, state, j, damage, rng, actor)
        return
    health = units[targets[0]].stats[Stat.HP] if effect.of_target and targets else caster[Stat.HP]
    amount = effect.scaling[0] * health + effect.scaling[1] * caster[Stat.ATK] + effect.scaling[2] * caster[Stat.DEF]
    if effect.kind == "resurrect":
        dead = [j for j in range(len(units)) if units[j].side == units[actor].side and hp[j] <= 0]
        for j in dead if effect.receivers == "all" else dead[:1]:
            revived = state[j].revived
            hp[j], cr[j], state[j] = units[j].stats[Stat.HP] * effect.value, 0.0, _State(revived=revived)
        return
    if not effect.debuff:
        for r in _receivers(effect, actor, units, hp, state):
            if effect.kind in _BUFFS and any(state[r].timers.get(k, 0) > 0 for k in ("unable_buff", "block")):
                continue  # MECH-BUFF-01 (a blocked hero cannot be buffed either)
            if effect.kind == "extra_turn":
                cr[r] = 1.0
            elif effect.kind == "cr":
                _raise_cr(units, hp, cr, state, r, effect.value)
            elif effect.kind == "heal":
                restored = min(_max_hp(units, state, r), hp[r] + amount)
                state[actor].healed += max(0.0, restored - hp[r])
                hp[r] = restored
            elif effect.kind == "hot":
                state[r].hots.append((effect.value, effect.turns))
            elif effect.kind == "barrier":
                state[actor].shielded += amount
                state[r].barrier, state[r].barrier_turns = max(state[r].barrier, amount), effect.turns
            elif effect.kind == "cleanse":
                state[r].remove(debuffs=True, count=effect.count)
            else:
                state[r].apply(effect)
        return
    effectiveness = (
        caster.get(Stat.EFFECTIVENESS, 0.0) + state[actor].buffs.get("eff", (0.0, 0))[0] + state[actor].eff_extra
    )
    own = effect.receivers == "self"  # inflicted on the caster itself (a mistake of its own skill): always lands
    hit = enemies if effect.receivers == "enemies" else [actor] if own else list(targets)
    for j in hit:
        if hp[j] <= 0 or (not own and (state[j].timers.get("immunity", 0) > 0 or effect.kind in units[j].kit.immune)):
            continue
        resistance = units[j].stats.get(Stat.EFFECT_RESISTANCE, 0.0) + state[j].buffs.get("res", (0.0, 0))[0]
        if units[actor].kit.ignore_resist and units[j].stats[Stat.HP] < caster[Stat.HP]:
            resistance = 0.0  # ignores the Effect Resistance of weaker (less max Health) targets
        land = 1.0 if own else effect.chance * min(max(1 + effectiveness - resistance, 0.0), LAND_CAP)  # MECH-EFF-01
        for _ in range(max(1, effect.count) if effect.kind == "venom" else 1):  # each venom stack has its own chance
            if rng.random() >= land:
                continue
            if not own and state[j].timers.pop("nullifier", 0) > 0:  # an enemy skill's effect nullified (MECH-NULL-01)
                continue
            if effect.kind == "cr":
                if not units[j].kit.cr_immune:
                    cr[j] = max(0.0, cr[j] + effect.value)
            elif effect.kind == "dispel":
                spoils = state[j].spoils
                state[j].remove(debuffs=False, count=effect.count)
                if spoils and not state[j].spoils:
                    _lift_pilfers(state, j)
            elif effect.kind in DOT_ATK or effect.kind == "poison":
                per_turn = (
                    POISON_MAX_HP * units[j].stats[Stat.HP]
                    if effect.kind == "poison"
                    else DOT_ATK[effect.kind] * caster[Stat.ATK]
                )
                state[j].dots.append((per_turn, effect.turns))
            elif effect.kind == "venom":
                state[j].venoms.append((effect.value, effect.turns))
            elif effect.kind == "erosion":
                state[j].erosions.append((effect.value, effect.turns))
            elif (
                effect.kind == "collapse"
            ):  # max Health down: the Health above the new maximum is lost (MECH-COLLAPSE-01)
                state[j].collapse = max(
                    state[j].collapse if state[j].timers.get("collapse", 0) > 0 else 0.0, effect.value
                )
                state[j].timers["collapse"] = max(state[j].timers.get("collapse", 0), effect.turns)
                hp[j] = min(hp[j], _max_hp(units, state, j))
            elif effect.kind == "beguile":
                state[j].beguiled = effect.value
            elif effect.kind == "pilfer":
                if not state[j].pilfer:  # a new Pilfer gives the caster a Spoils stack (`assumed`: not a repeated one)
                    state[j].pilfer, state[j].pilfer_by = True, actor
                    state[actor].spoils = min(SPOILS_CAP, state[actor].spoils + 1)
            elif effect.kind == "provoke":
                state[j].apply(effect)
                state[j].provoker = actor
            else:
                state[j].apply(effect)


def _after_hit(
    j: int,
    attacker: int,
    lost: float,
    counterable: bool,
    units: Sequence[Combatant],
    hp: list[float],
    cr: list[float],
    state: list[_State],
    hits: Mapping[tuple[int, int, int], float],
    rng: random.Random,
) -> None:
    """What a hero does after a hit it survives (SPEC D76): reflect part of the Health lost, trigger its low-Health
    effects, counterattack."""
    kit = _kit(units, state, j)
    if kit.hit_cr:  # Combat Readiness gained after being attacked (Boss Arunka S2)
        _raise_cr(units, hp, cr, state, j, kit.hit_cr)
    if kit.spirit_full:  # Fighting Spirit: Help! Help! when full (Young Senya S2)
        state[j].spirit += kit.spirit_hit
        if state[j].spirit >= kit.spirit_full:
            state[j].spirit -= kit.spirit_full
            for effect in kit.spirit_effects:
                _use(effect, j, [], units, hp, cr, state, rng)
            front = _foremost(units, hp, j)
            extra = kit.spirit_share * (
                units[j].stats[Stat.HP] + (units[front].stats[Stat.HP] if front is not None else 0)
            )
            for e in range(len(units)):
                if units[e].side != units[j].side and hp[e] > 0:
                    _strike(units, hp, cr, state, e, extra, rng, j)
    fragrance = max(
        (_kit(units, state, m).fragrance for m in _allies(units, hp, j)), default=0.0
    )  # Lingering Fragrance
    for share in (kit.reflect, fragrance):
        if share and lost > 0 and hp[attacker] > 0:
            _strike(units, hp, cr, state, attacker, min(share * lost, units[j].stats[Stat.HP]), rng, j)
    if kit.low_hp and hp[j] <= kit.low_hp * units[j].stats[Stat.HP] and state[j].low_wait == 0:
        state[j].low_wait = kit.low_hp_cooldown
        for effect in kit.low_hp_effects:
            _use(effect, j, [], units, hp, cr, state, rng)
    for m in _allies(units, hp, j):
        if m != j and (gain := _kit(units, state, m).ally_hit_cr):  # Dragon's Pride: CR after an ally's attack
            _raise_cr(units, hp, cr, state, m, gain)
    for m in _allies(units, hp, j):  # an ally was left low: La Vie en Rose (Perfumer Byblis S2)
        helper = _kit(units, state, m)
        if helper.ally_low and hp[j] <= helper.ally_low * units[j].stats[Stat.HP] and state[m].ally_wait == 0:
            state[m].ally_wait = helper.ally_low_cooldown
            for effect in helper.ally_low_effects:
                _use(effect, m, [], units, hp, cr, state, rng)
    if not counterable:  # a skill that cannot trigger a counterattack (BBK S3)
        return
    for m in range(len(units)):  # an ally was attacked: a guard (Mort) may counterattack for it
        mate, guarding = units[m], _kit(units, state, m)
        if m == j or mate.side != units[j].side or hp[m] <= 0 or not guarding.guard or 1 not in mate.skills:
            continue
        blocked = _cannot_act(state[m]) or state[m].timers.get("unable_counter", 0) > 0
        if hp[attacker] > 0 and not blocked and rng.random() < guarding.guard:
            _counterattack(m, attacker, units, hp, cr, state, hits, rng)
            if state[m].guard_wait == 0:
                state[m].guard_wait = guarding.guard_cooldown
                for effect in guarding.guard_effects:
                    _use(effect, m, [], units, hp, cr, state, rng)
    if hp[attacker] > 0 and _counters(units, hp, j, state, rng):
        _counterattack(j, attacker, units, hp, cr, state, hits, rng)


def _foremost(units: Sequence[Combatant], hp: Sequence[float], i: int) -> int | None:
    """The foremost ally except `i`: the first other living hero of its side in the order the team was given (the
    convention: front first; `assumed`, NV-43)."""
    return next((m for m in range(len(units)) if m != i and units[m].side == units[i].side and hp[m] > 0), None)


def _dual_attack(
    actor: int,
    targets: Sequence[int],
    units: Sequence[Combatant],
    hp: list[float],
    cr: list[float],
    state: list[_State],
    hits: Mapping[tuple[int, int, int], float],
    rng: random.Random,
    strongest: bool = False,
) -> None:
    """Dual Attack: a random ally able to act (the one with the highest Attack when `strongest`) attacks the same target
    with a basic attack (never by counterattacks)."""
    target = next((j for j in targets if hp[j] > 0), None)
    mates = [
        m
        for m in range(len(units))
        if m != actor
        and units[m].side == units[actor].side
        and hp[m] > 0
        and 1 in units[m].skills
        and not _cannot_act(state[m])
    ]
    if target is None or not mates:
        return
    m = max(mates, key=lambda a: units[a].stats[Stat.ATK] * state[a].factor("atk")) if strongest else rng.choice(mates)
    crit = units[m].skills[1].can_crit and rng.random() < _crit_chance(units, state, m)
    factor = min(units[m].stats[Stat.CRIT_DAMAGE], CD_CAP) if crit else 1.0
    _strike(units, hp, cr, state, target, hits[(m, target, 1)] * state[m].factor("atk") * factor, rng, m)


def _extra_attack(
    actor: int,
    targets: Sequence[int],
    units: Sequence[Combatant],
    hp: list[float],
    cr: list[float],
    state: list[_State],
    hits: Mapping[tuple[int, int, int], float],
    rng: random.Random,
    slot: int = 1,
) -> bool:
    """The caster attacks again with the skill of `slot` (S1: Rhianna's Waning Moon Slash; Belian's Incursion is her
    "S2" data; Zio's Disappear): the first living target, or every target when that skill hits all enemies. It triggers
    nothing else. Returns whether it defeated an enemy."""
    unit = units[actor]
    skill = unit.skills.get(slot)
    living = [j for j in targets if hp[j] > 0]
    if skill is None or not living:
        return False
    crit = skill.can_crit and rng.random() < _crit_chance(units, state, actor)
    factor = min(unit.stats[Stat.CRIT_DAMAGE], CD_CAP) if crit else 1.0
    killed = False
    for target in living if skill.targets > 1 else living[:1]:
        _strike(
            units, hp, cr, state, target, hits[(actor, target, slot)] * state[actor].factor("atk") * factor, rng, actor
        )
        killed = killed or hp[target] <= 0
    return killed


def _counters(
    units: Sequence[Combatant], hp: Sequence[float], j: int, state: Sequence[_State], rng: random.Random
) -> bool:
    """A counterattack (S1 back at the attacker): always with the counterattack buff, else the passive chance; never
    while stunned, nor while another hero's "nobody else counterattacks" holds (`assumed`)."""
    unit, me = units[j], state[j]
    if _cannot_act(me) or me.timers.get("unable_counter", 0) > 0 or 1 not in unit.skills:
        return False
    if any(_kit(units, state, i).no_counter and hp[i] > 0 and i != j for i in range(len(units))):
        return False
    chance = 1.0 if me.timers.get("counter", 0) > 0 else _kit(units, state, j).counter
    return chance > 0 and rng.random() < chance


def _counterattack(
    j: int,
    attacker: int,
    units: Sequence[Combatant],
    hp: list[float],
    cr: list[float],
    state: list[_State],
    hits: Mapping[tuple[int, int, int], float],
    rng: random.Random,
) -> None:
    unit, skill = units[j], units[j].skills[1]
    crit = skill.can_crit and rng.random() < _crit_chance(units, state, j)
    factor = min(unit.stats[Stat.CRIT_DAMAGE], CD_CAP) if crit else 1.0
    _strike(units, hp, cr, state, attacker, hits[(j, attacker, 1)] * state[j].factor("atk") * factor, rng, j)


def confidence_interval(p: float, runs: int) -> float:
    """95% half-width of a simulated share (the simulation's own noise, not the model's error)."""
    return 1.96 * math.sqrt(max(p * (1 - p), 1e-12) / runs)
