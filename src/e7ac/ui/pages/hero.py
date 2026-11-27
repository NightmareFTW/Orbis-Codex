"""The Hero page (SPEC D133, D134): everything about one hero, opened by a click on its portrait (Home's team). It shows
only the hero that was clicked; from the sidebar, with none chosen, it says how to choose one.

Laid out like the game's hero screen: a header (portrait, name, stars, element, class), the hero's stats as tiles and
its gear as a grid with the set icons, the three skills as cards with their own icons (cut from the popups the Skill
reader read), and what the simulation and the Arena know. The art is the game's, kept in this PC's own cache (never
in the repository); what is not cached is a quiet placeholder."""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage, QPixmap, QResizeEvent
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from e7ac.domain.roster import Gear, HeroBuild
from e7ac.skills.icons import find_icon
from e7ac.skills.store import StoredSkill
from e7ac.sources.assets import load_set_icons
from e7ac.ui.context import AppContext
from e7ac.ui.hero_sheet import Section, Sheet, build_sheet, set_name, stat_text
from e7ac.ui.pages.base import Page, card, heading
from e7ac.ui.theme import TOKENS

ELEMENTS = {"fire": "#e0663c", "ice": "#4aa8e8", "earth": "#58b868", "light": "#f0d060", "dark": "#a36ad8"}
"""Chip colours of the five elements (the game's own: red, blue, green, yellow, purple)."""
GEAR_ORDER = ("weapon", "helmet", "armor", "necklace", "ring", "boots")
SKILL_ICON = 72
SET_ICON = 40


class _Body(QWidget):
    """The scrolled content. Wrapped text needs the height for its width, which a scroll area does not ask for: the
    body asks the layout and keeps that as its minimum height (otherwise the cards squeeze their text)."""

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 (Qt's name)
        super().resizeEvent(event)
        layout = self.layout()
        if layout is not None and self.width() > 0:
            wanted = layout.totalHeightForWidth(self.width())
            if wanted > 0 and wanted != self.minimumHeight():
                self.setMinimumHeight(wanted)


ROLES = {"manauser": "Soul Weaver", "soul-weaver": "Soul Weaver"}
"""Catalog class names that are not the game's."""


class HeroPage(Page):
    def __init__(self, context: AppContext) -> None:
        super().__init__(context)
        self.code: str | None = None
        """The hero shown now."""
        self.sheet: Sheet | None = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 20, 24, 12)
        outer.addWidget(
            heading("Hero", "Everything the app has about one hero. Click a portrait of your team on Home.")
        )
        self._hint = QLabel("No hero chosen: click one of the portraits of your team on Home.")
        self._hint.setObjectName("muted")
        outer.addWidget(self._hint)
        self._body = _Body()
        self._column = QVBoxLayout(self._body)
        self._column.setContentsMargins(0, 0, 0, 0)
        self._column.setSpacing(14)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setWidget(self._body)
        outer.addWidget(scroll, 1)

    def show_hero(self, code: str) -> None:
        """Show this hero (and only this one)."""
        self.code = code
        sheet = self.sheet = build_sheet(self.context.paths, self.context.heroes(), code)
        self._hint.hide()
        while self._column.count():
            item = self._column.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.deleteLater()
        self._column.addWidget(self._header(sheet))
        for build_number, build in enumerate(sheet.builds, 1):
            self._column.addWidget(self._build(build, build_number if len(sheet.builds) > 1 else 0))
        if not sheet.builds:
            self._column.addWidget(_section_card(sheet.sections[0]))
        self._column.addWidget(self._skills(sheet))
        lower = QHBoxLayout()
        lower.setSpacing(14)
        for section in sheet.sections[2:]:
            lower.addWidget(_section_card(section), 1)
        holder = QWidget()
        holder.setLayout(lower)
        self._column.addWidget(holder)
        self._column.addStretch()

    def shown(self) -> None:
        if self.code is not None:
            self.show_hero(self.code)  # the roster or the skills may have changed since

    # --- the pieces ---------------------------------------------------------------------------------------------

    def _header(self, sheet: Sheet) -> QWidget:
        frame, layout = card()
        row = QHBoxLayout()
        row.setSpacing(18)
        portrait = QLabel()
        portrait.setPixmap(self.context.portraits.pixmap(sheet.code, 120))
        portrait.setFixedSize(120, 120)
        row.addWidget(portrait)
        names = QVBoxLayout()
        names.setSpacing(4)
        title = QLabel(sheet.name)
        title.setObjectName("title")
        names.addWidget(title)
        stars = max((b.stars for b in sheet.builds), default=0)
        names.addWidget(_stars(stars) if stars else _muted(_base_stars(sheet)))
        chips = QHBoxLayout()
        chips.setSpacing(8)
        for tag in sheet.tags:
            if not tag.endswith("base"):
                chips.addWidget(
                    _chip(ROLES.get(tag.lower(), tag.title()), ELEMENTS.get(tag.lower(), TOKENS["gold_dim"]))
                )
        chips.addWidget(_muted(sheet.code))
        chips.addStretch()
        names.addLayout(chips)
        names.addStretch()
        row.addLayout(names, 1)
        layout.addLayout(row)
        return frame

    def _build(self, build: HeroBuild, copy: int) -> QWidget:
        frame, layout = card("Your hero" + (f" (copy {copy})" if copy else ""))
        facts = QHBoxLayout()
        facts.setSpacing(8)
        facts.addWidget(_stars(build.stars))
        facts.addWidget(_chip(f"Awakening {build.awakening}", TOKENS["gold_dim"]))
        facts.addWidget(_chip(f"Lv. {build.level}", TOKENS["panel_alt"]))
        s = build.skills
        facts.addWidget(_chip(f"S1 +{s.s1}  S2 +{s.s2}  S3 +{s.s3}", TOKENS["panel_alt"]))
        if build.cp is not None:
            facts.addWidget(_chip(f"CP {build.cp:,}", TOKENS["blue_top"]))
        facts.addStretch()
        layout.addLayout(facts)
        if build.final_stats is not None:
            f = build.final_stats
            tiles = QGridLayout()
            tiles.setSpacing(8)
            values = (
                ("Attack", f"{f.atk}"),
                ("Defense", f"{f.defense}"),
                ("Health", f"{f.hp:,}"),
                ("Speed", f"{f.speed}"),
                ("Crit Chance", _pct(f.crit_chance)),
                ("Crit Damage", _pct(f.crit_damage)),
                ("Effectiveness", _pct(f.effectiveness)),
                ("Effect Res.", _pct(f.effect_resistance)),
                ("Dual Attack", _pct(f.dual_attack)),
            )
            for index, (label, value) in enumerate(values):
                tiles.addWidget(_tile(label, value), index // 5, index % 5)
            layout.addLayout(tiles)
        extras = QHBoxLayout()
        extras.setSpacing(8)
        if build.imprint is not None:
            grade = build.imprint.grade.value if build.imprint.grade is not None else "?"
            extras.addWidget(_tile("Memory imprint", f"{grade} · {stat_text(build.imprint.stat, build.imprint.value)}"))
        if build.exclusive_equipment is not None:
            ee = build.exclusive_equipment
            text = ee.option_text or (stat_text(ee.stat, ee.value) if ee.stat and ee.value is not None else "?")
            extras.addWidget(_tile("Exclusive equipment", text))
        if build.artifact is not None:
            extras.addWidget(_tile("Artifact", f"{build.artifact.code} · Lv. {build.artifact.level}"))
        extras.addStretch()
        layout.addLayout(extras)
        layout.addWidget(_muted(f"Read {build.captured_at:%Y-%m-%d} from {build.source.value}"))
        grid = QGridLayout()
        grid.setSpacing(8)
        gear = {slot.value: item for slot, item in build.gear.items()}
        sets = load_set_icons(self.context.paths.cache_dir, {g.set_code for g in gear.values()})
        for index, slot in enumerate(GEAR_ORDER):
            item = gear.get(slot)
            if item is not None:
                grid.addWidget(_gear(slot, item, sets.get(item.set_code)), index // 3, index % 3)
        layout.addLayout(grid)
        return frame

    def _skills(self, sheet: Sheet) -> QWidget:
        frame, layout = card("Skills as read from the game")
        if not sheet.skills:
            layout.addWidget(_muted("Not read yet: use the Skill reader (Dev tools)."))
            return frame
        for skill in sheet.skills:
            path = find_icon(self.context.paths.home, skill.hero, skill.name)
            layout.addWidget(_skill(skill, QImage(str(path)) if path is not None else QImage()))
        return frame


# --- widgets ---------------------------------------------------------------------------------------------------


def _pct(value: float) -> str:
    return f"{value * 100:.0f}%"


def _muted(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("muted")
    label.setWordWrap(True)
    return label


def _chip(text: str, colour: str) -> QLabel:
    """A small rounded tag."""
    label = QLabel(text)
    dark = QColor(colour).lightness() < 140
    label.setStyleSheet(
        f"background: {colour}; color: {'#f2ecdc' if dark else '#14161d'}; border-radius: 9px; padding: 2px 10px;"
        "font-weight: 600;"
    )
    label.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
    return label


def _stars(count: int) -> QLabel:
    label = QLabel("★" * count + "☆" * (6 - count))
    label.setStyleSheet(f"color: {TOKENS['gold_bright']}; font-size: 16pt; letter-spacing: 2px;")
    return label


def _base_stars(sheet: Sheet) -> str:
    return next((t for t in sheet.tags if t.endswith("base")), "")


def _tile(label: str, value: str) -> QFrame:
    """A stat: the label small above the value."""
    frame = QFrame()
    frame.setStyleSheet(f"background: {TOKENS['panel_alt']}; border: 1px solid {TOKENS['border']}; border-radius: 8px;")
    box = QVBoxLayout(frame)
    box.setContentsMargins(12, 6, 12, 8)
    box.setSpacing(0)
    name = QLabel(label)
    name.setStyleSheet(f"color: {TOKENS['muted']}; font-size: 8pt; border: none; background: transparent;")
    number = QLabel(value)
    number.setStyleSheet("font-size: 12pt; font-weight: 600; border: none; background: transparent;")
    number.setWordWrap(True)
    box.addWidget(name)
    box.addWidget(number)
    return frame


def _picture(image: QImage, size: int, placeholder: str = "") -> QLabel:
    label = QLabel()
    label.setFixedSize(size, size)
    label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    if image.isNull():
        label.setText(placeholder)
        label.setStyleSheet(
            f"background: {TOKENS['panel']}; border: 1px dashed {TOKENS['border']}; border-radius: 10px;"
            f"color: {TOKENS['muted']};"
        )
    else:
        label.setPixmap(
            QPixmap.fromImage(image).scaled(
                size, size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
            )
        )
    return label


def _gear(slot: str, gear: Gear, icon: bytes | None) -> QFrame:
    """One piece: its set icon, the slot, the set and the level, the main stat and the substats."""
    frame = QFrame()
    frame.setStyleSheet(f"background: {TOKENS['panel_alt']}; border: 1px solid {TOKENS['border']}; border-radius: 8px;")
    row = QHBoxLayout(frame)
    row.setContentsMargins(10, 8, 10, 8)
    row.setSpacing(10)
    row.addWidget(_picture(QImage.fromData(icon) if icon else QImage(), SET_ICON, "?"), 0, Qt.AlignmentFlag.AlignTop)
    text = QVBoxLayout()
    text.setSpacing(1)
    head = QLabel(f"{slot.title()} · {set_name(gear.set_code)} +{gear.enhance}")
    head.setStyleSheet(f"color: {TOKENS['gold']}; font-weight: 600; border: none; background: transparent;")
    main = QLabel(stat_text(gear.main.stat, gear.main.value))
    main.setStyleSheet("font-size: 11pt; border: none; background: transparent;")
    text.addWidget(head)
    text.addWidget(main)
    for sub in gear.substats:
        line = QLabel(stat_text(sub.stat, sub.value) + (f"  ({sub.rolls} rolls)" if sub.rolls else ""))
        line.setStyleSheet(f"color: {TOKENS['muted']}; font-size: 9pt; border: none; background: transparent;")
        text.addWidget(line)
    row.addLayout(text, 1)
    return frame


def _skill(skill: StoredSkill, icon: QImage) -> QFrame:
    """One skill as the game shows it: its icon, name and level, its cost, the text, the Soulburn and the steps."""
    frame = QFrame()
    frame.setStyleSheet(
        f"background: {TOKENS['panel_alt']}; border: 1px solid {TOKENS['border']}; border-radius: 10px;"
    )
    row = QHBoxLayout(frame)
    row.setContentsMargins(12, 10, 12, 10)
    row.setSpacing(14)
    row.addWidget(_picture(icon, SKILL_ICON, f"S{skill.slot or '?'}"), 0, Qt.AlignmentFlag.AlignTop)
    body = QVBoxLayout()
    body.setSpacing(5)
    top = QHBoxLayout()
    top.setSpacing(8)
    name = QLabel(f"S{skill.slot or '?'}  {skill.name}")
    name.setStyleSheet("font-size: 12pt; font-weight: 600; border: none; background: transparent;")
    top.addWidget(name)
    if skill.level is not None:
        top.addWidget(_chip(f"+{skill.level}", TOKENS["gold_dim"]))
    if skill.passive:
        top.addWidget(_chip("Passive", TOKENS["blue_top"]))
    elif skill.cooldown:
        top.addWidget(_chip(f"{skill.cooldown} turns", TOKENS["panel"]))
    for kind, amount in skill.gains:
        top.addWidget(_chip(f"+{amount} {kind}", TOKENS["green_top"]))
    top.addStretch()
    body.addLayout(top)
    body.addWidget(_plain(skill.text))
    if skill.soulburn:
        burn = _plain(f"Soulburn ({skill.soulburn_cost or '?'} Soul): {skill.soulburn}")
        burn.setStyleSheet("color: #7fb0ff; border: none; background: transparent;")
        body.addWidget(burn)
    if skill.enhance:
        steps = _plain("   ".join(f"+{n} {t}" for n, t in skill.enhance))
        steps.setStyleSheet(f"color: {TOKENS['muted']}; font-size: 9pt; border: none; background: transparent;")
        body.addWidget(steps)
    for effect in skill.effects:
        if "Exclusive Equipment" in effect.text or effect.title.strip() == "Equipment":
            continue  # the game's hint about exclusive equipment, read as if it were an effect of the skill
        line = _plain(f"{effect.title}: {effect.text}")
        line.setStyleSheet(f"color: {TOKENS['gold']}; font-size: 9pt; border: none; background: transparent;")
        body.addWidget(line)
    row.addLayout(body, 1)
    return frame


def _plain(text: str) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    label.setStyleSheet("border: none; background: transparent;")
    return label


def _section_card(section: Section) -> QWidget:
    """A plain card of rows and paragraphs (the simulation, the Arena, a hero not in the roster)."""
    frame, layout = card(section.title)
    if not section.rows and not section.paragraphs:
        layout.addWidget(_muted(section.empty))
        layout.addStretch()
        return frame
    for label, value in section.rows:
        row = QHBoxLayout()
        name = _muted(label)
        name.setFixedWidth(150)
        name.setAlignment(Qt.AlignmentFlag.AlignTop)
        row.addWidget(name)
        row.addWidget(_plain(value), 1)
        layout.addLayout(row)
    for paragraph in section.paragraphs:
        layout.addWidget(_muted(paragraph))
    layout.addStretch()
    return frame


__all__: Sequence[str] = ("HeroPage",)
