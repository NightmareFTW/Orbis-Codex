"""Re-reading the "▲" bonus cell of Equipment-tab rows (SPEC D57): synthetic geometry, a scripted recogniser, and the
user's live captures (local fixtures)."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from e7ac.domain.codes import Stat
from e7ac.roster.screen_import import build_from_screen
from e7ac.vision.bonus_cells import CELL_IMAGES, green_minus_blue, parse_value, reread_bonuses
from e7ac.vision.hero_screen import parse_hero_screen
from e7ac.vision.ocr import CellReader, TextLine
from tests.markers import FIXTURES_DIR
from tests.test_hero_screen import RENOA, STAT_ROWS, equipment_screen

NOW = datetime(2026, 10, 12, 8, 0, tzinfo=UTC)
SCREEN = np.zeros((1200, 2000, 3), dtype=np.uint8)
MAP = np.zeros((1020, 1020, 3), dtype=np.uint8)
"""Each pixel tells where it was, to 4 px (5 at the far edge): blue = x / 4, green = y / 4; red stays 0 (the margin
is white)."""
MAP[:, :, 0] = np.arange(1020)[None, :] // 4
MAP[:, :, 1] = np.arange(1020)[:, None] // 4


class ScriptedCells:
    """A recogniser that answers each batch from a script (one text per cell, in order) and records the cells."""

    def __init__(self, *batches: Sequence[str]) -> None:
        self.batches = list(batches)
        self.calls: list[list[Any]] = []

    def recognise(self, images: Sequence[Any]) -> list[tuple[str, float]]:
        self.calls.append(list(images))
        texts = self.batches.pop(0) if self.batches else [""] * len(images)
        return [(text, 0.9) for text in texts]


def _without_bonus(label: str) -> list[tuple[str, str, str | None]]:
    return [(lb, final, None if lb == label else bonus) for lb, final, bonus in STAT_ROWS]


@pytest.mark.parametrize(
    ("token", "stat", "value"),
    [
        ("2,374", Stat.ATK, 2374),
        ("23.0", Stat.ATK, None),  # a flat stat never has a decimal or a "%"
        ("111.0%", Stat.CRIT_DAMAGE, 1.11),
        ("147.0", Stat.CRIT_DAMAGE, 1.47),  # the thin "%" lost on a lossy frame
        ("6,0%", Stat.CRIT_CHANCE, 0.06),  # the "." read as ","
        ("1470", Stat.CRIT_DAMAGE, None),  # bare digits are never re-cut into a percentage
    ],
)
def test_a_cell_value_is_read_in_its_row_format(token: str, stat: Stat, value: float | None) -> None:
    assert parse_value(token, stat) == value


def test_green_minus_blue_keeps_orange_and_drops_pink_art_and_white_text() -> None:
    cell = np.zeros((10, 30, 3), dtype=np.uint8)
    cell[:, :10] = (60, 150, 240)  # orange glyph (BGR)
    cell[:, 10:20] = (170, 110, 230)  # pink art
    cell[:, 20:] = (240, 240, 240)  # white text
    grey = green_minus_blue(cell)
    assert grey[:, :10].min() == 255 and grey[:, 10:].max() == 0


def test_only_unsettled_rows_are_re_read_and_only_accepted_values_kept() -> None:
    reading = parse_hero_screen(equipment_screen())
    cells = ScriptedCells(["8218", "9.9%"], ["35.0%"])  # 1st image: HP right, CD wrong; 2nd image: CD right
    seen: list[tuple[Stat, float | None]] = []

    def accept(stat: Stat, bonus: float | None) -> bool:
        seen.append((stat, bonus))
        return {Stat.HP: 8218.0, Stat.CRIT_DAMAGE: 0.35}[stat] == bonus

    found = reread_bonuses(SCREEN, reading, equipment_screen(), cells, [Stat.HP, Stat.CRIT_DAMAGE], accept)
    assert {s: (r.bonus, r.image) for s, r in found.items()} == {
        Stat.HP: (8218.0, CELL_IMAGES[0][0]),
        Stat.CRIT_DAMAGE: (0.35, CELL_IMAGES[1][0]),
    }
    assert [len(batch) for batch in cells.calls] == [2, 1]  # the second image only for the row still unsettled
    assert seen == [(Stat.HP, 8218.0), (Stat.CRIT_DAMAGE, 0.099), (Stat.CRIT_DAMAGE, 0.35)]  # only what was read


def test_a_cell_without_a_number_is_offered_as_no_bonus() -> None:
    reading = parse_hero_screen(equipment_screen())
    offered: list[float | None] = []

    def accept(stat: Stat, bonus: float | None) -> bool:
        offered.append(bonus)
        return False

    reread_bonuses(SCREEN, reading, equipment_screen(), ScriptedCells([""]), [Stat.DUAL_ATTACK], accept)
    assert offered[:1] == [None]


def test_the_bonus_column_comes_from_the_bonus_words_read_on_other_rows() -> None:
    lines = equipment_screen()
    cells = ScriptedCells(["8218"])
    reread_bonuses(MAP, parse_hero_screen(lines), lines, cells, [Stat.HP], lambda s, b: True)
    (cell,) = cells.calls[0]
    inner = cell[cell[:, :, 2] == 0]  # the margin is white
    xs, ys = inner[:, 0].astype(int) * 4, inner[:, 1].astype(int) * 4
    health = next(ln for ln in lines if ln.text == "Health").box
    row = health.height  # every synthetic label has this height
    bonus_left = 540  # where the synthetic bonuses start (equipment_screen)
    assert abs(xs.min() - (bonus_left - 0.25 * row)) <= 5 and abs(xs.max() - (bonus_left + 3.0 * row)) <= 5
    assert abs(ys.min() - (health.cy - 0.4 * row)) <= 5 and abs(ys.max() - (health.cy + 0.4 * row)) <= 5


def test_without_any_bonus_word_the_column_is_placed_after_the_finals() -> None:
    lines = equipment_screen(rows=[(label, final, None) for label, final, _ in STAT_ROWS])
    cells = ScriptedCells(["751"])
    found = reread_bonuses(SCREEN, parse_hero_screen(lines), lines, cells, [Stat.ATK], lambda s, b: b == 751)
    assert found[Stat.ATK].bonus == 751 and len(cells.calls) == 1


def test_build_from_screen_settles_an_unread_bonus_with_the_re_read() -> None:
    reading = parse_hero_screen(equipment_screen(rows=_without_bonus("Health")))
    asked: list[list[Stat]] = []

    def reread(stats: Sequence[Stat], accept: Any) -> dict[Stat, Any]:
        asked.append(list(stats))
        return reread_bonuses(SCREEN, reading, equipment_screen(), ScriptedCells(["8218"]), stats, accept)

    result = build_from_screen(reading, {"c1193": RENOA}, captured_at=NOW, reread=reread)
    assert asked == [[Stat.HP]]  # every other row already agrees with the catalog base
    assert all(c.verdict == "ok" for c in result.base_checks) and not result.doubtful
    assert result.build is not None and result.build.confidence["final_stats.hp"] == 1.0
    assert "bonus re-read from its cell for max_hp: agrees with the catalog base" in result.notes


def test_a_re_read_that_disagrees_with_the_base_changes_nothing() -> None:
    reading = parse_hero_screen(equipment_screen(rows=_without_bonus("Health")))

    def reread(stats: Sequence[Stat], accept: Any) -> dict[Stat, Any]:
        return reread_bonuses(
            SCREEN, reading, equipment_screen(), ScriptedCells(["8213"], ["8212"], ["8211"]), stats, accept
        )

    result = build_from_screen(reading, {"c1193": RENOA}, captured_at=NOW, reread=reread)
    assert [(c.stat, c.verdict) for c in result.base_checks if not c.ok] == [(Stat.HP, "unchecked")]
    assert not any("re-read" in note for note in result.notes)


def test_the_app_reader_can_re_read_cells() -> None:
    from e7ac.vision.ocr import RapidOcrReader

    assert isinstance(RapidOcrReader(), CellReader)


# ------------------------------------------------------------------------------------------------ real captures

TRUTH = "screenshots/watch/truth.json"
LIVE = ["watch/renoa.webp", "watch/estelle.webp", "watch/fumyr.webp", "watch/kawerik.webp", "watch/mighty_scout.webp"]


@pytest.mark.parametrize("scale", [1.0, 0.64])
@pytest.mark.parametrize(
    "frame", [pytest.param(f, marks=pytest.mark.fixtures(TRUTH, f"screenshots/{f}")) for f in LIVE]
)
def test_every_bonus_of_the_live_captures_is_settled(frame: str, scale: float) -> None:
    """The kept watch frames (bright hero art behind the bonuses): with the cell re-read every row agrees with its
    base (base = final - bonus by eye, truth.json: local, never committed)."""
    import cv2

    from e7ac.vision.image import load_image
    from e7ac.vision.ocr import RapidOcrReader

    truth = json.loads((FIXTURES_DIR / TRUTH).read_text())["frames"][frame]
    image = load_image(FIXTURES_DIR / "screenshots" / frame)
    if scale != 1.0:
        image = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    reader = RapidOcrReader()
    lines: list[TextLine] = reader.read(image)
    reading = parse_hero_screen(lines)
    base = {Stat(k): (v[0] or 0) - (v[1] or 0) for k, v in truth.items()}
    tolerance = {stat: 0.5 if stat in (Stat.ATK, Stat.DEF, Stat.HP, Stat.SPEED) else 0.0005 for stat in base}

    def agrees(stat: Stat, final: float | None, bonus: float | None) -> bool:
        return final is not None and abs(final - (bonus or 0) - base[stat]) <= tolerance[stat]

    unsettled = [s for s, r in reading.stats.items() if not agrees(s, r.final, r.bonus)]
    found = reread_bonuses(image, reading, lines, reader, unsettled, lambda s, b: agrees(s, reading.stats[s].final, b))
    assert sorted(found, key=str) == sorted(unsettled, key=str), {s: r.text for s, r in found.items()}
    for stat, row in reading.stats.items():  # and nothing settled on a wrong pair: finals are right, bonuses true
        bonus = found[stat].bonus if stat in found else row.bonus
        want_final, want_bonus = truth[stat.value]
        assert row.final == pytest.approx(want_final, abs=1e-6), stat
        assert (bonus is None) == (want_bonus is None) and (bonus is None or bonus == pytest.approx(want_bonus)), stat


class CellFakeReader(ScriptedCells):
    """The scan's OCR: canned full-frame lines, and cells read from a script."""

    def __init__(self, lines: list[TextLine], *batches: Sequence[str]) -> None:
        super().__init__(*batches)
        self.lines = lines

    def read(self, image: object) -> list[TextLine]:
        return self.lines


def test_scan_re_reads_an_unread_bonus(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, synthetic_catalog: list[str]
) -> None:
    import cv2
    from typer.testing import CliRunner

    from e7ac.cli import roster as roster_cli
    from e7ac.cli.app import app
    from tests.test_hero_screen import BBK_ROWS

    capture = tmp_path / "20261012-080000-hero.png"
    cv2.imwrite(str(capture), SCREEN)
    rows = [(label, final, None if label == "Health" else bonus) for label, final, bonus in BBK_ROWS]
    lines = equipment_screen(rows=rows, name="Blood Blade Karin")
    monkeypatch.setattr(roster_cli, "reader_factory", lambda: CellFakeReader(lines, ["6948"]))
    result = CliRunner().invoke(app, ["roster", "scan", str(capture), "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "max_hp 12819 ok" in result.stdout and "9/9 agree" in result.stdout
    assert "bonus re-read from its cell for max_hp: agrees with the catalog base" in result.stderr
