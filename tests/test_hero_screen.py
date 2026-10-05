"""Hero screen reading: synthetic OCR lines (always run) and the user's real captures (local fixtures only)."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from e7ac.catalog.facts import EntityType, Fact
from e7ac.catalog.resolve import ResolvedEntity, resolve
from e7ac.cli import roster as roster_cli
from e7ac.cli.app import app
from e7ac.domain.codes import DataStatus, SourceId, Stat
from e7ac.domain.roster import BuildSource, Imprint, ImprintGrade, ImprintMode
from e7ac.paths import AppPaths
from e7ac.roster.screen_import import ASSUMED, INFERRED_MODE, build_from_screen
from e7ac.settings import GameLanguage
from e7ac.vision.hero_screen import ScreenError, ScreenKind, parse_hero_screen
from e7ac.vision.image import ImageError, captured_at, expand_image_paths
from e7ac.vision.labels import match_label
from e7ac.vision.ocr import Box, TextLine, Word
from tests.markers import FIXTURES_DIR

runner = CliRunner()
ROW = 36.0  # row height of the synthetic layout (≈ the user's 1167-px captures)


def line(text: str, x0: float, y0: float, *, height: float = ROW, char: float = 14.0, score: float = 0.99) -> TextLine:
    """A detected line whose words are laid out left to right (space = one char wide)."""
    words, x = [], x0
    for token in text.split(" "):
        width = max(1, len(token)) * char
        words.append(Word(token, score, Box(x, y0, x + width, y0 + height)))
        x += width + char
    return TextLine(text, score, Box(x0, y0, x - char, y0 + height), tuple(words))


STAT_ROWS = [
    ("Attack", "1721", "751"),
    ("Defense", "1879", "1276"),
    ("Health", "13517", "8218"),
    ("Speed", "230", "108"),
    ("Critical Hit Chance", "36.0%", "9.0%"),
    ("Critical Hit Damage", "185.0%", "35.0%"),
    ("Effectiveness", "38.0%", "38.0%"),
    ("Effect Resistance", "69.0%", "69.0%"),
    ("Dual Attack Chance", "3.0%", None),
]


IMPRINT_LINES = [line("Effectiveness +", 280, 411), line("15%", 282, 441)]  # wrapped on two lines


def equipment_screen(
    *,
    rows: Sequence[tuple[str, str, str | None]] = STAT_ROWS,
    name: str = "Renoa",
    imprint: Sequence[TextLine] = IMPRINT_LINES,
) -> list[TextLine]:
    """Equipment tab like the user's capture, with every distraction that broke early versions."""
    lines = [
        line(name, 98, 48),  # top bar title
        line("460/360", 876, 50),  # stamina: looks like a level
        line("679,874,367", 1070, 50),  # gold: looks like a CP
        line("Dark Ranger Aquarius", 204, 150),
        line(f"{name} ☆", 158, 192, height=72),  # big name, a star read as a symbol
        line("AAAAA", 330, 200),  # star row read as letters
        line("Lv. Max/60", 162, 315, height=55),
        *imprint,
        line("123,120", 162, 598, height=52),
    ]
    y = 660.0
    for label, final, bonus in rows:
        lines.append(line(label, 160, y))
        lines.append(line(final, 445, y + 2))
        if bonus is not None:
            lines.append(line("A", 515, y + 4, char=10))  # the "▲" glyph read as a letter
            lines.append(line(bonus, 540, y + 2))
        lines.append(line("Ivana +15", 1700, y + 10))  # hero list on the right, same rows
        y += ROW
    lines += [line("Penetration Set", 216, 1000), line("Speed Set", 216, 1036), line("No set effect", 214, 1072)]
    return lines


def test_equipment_tab_is_read_completely() -> None:
    reading = parse_hero_screen(equipment_screen())
    assert reading.kind is ScreenKind.EQUIPMENT
    assert (reading.name, reading.level, reading.level_cap, reading.cp) == ("Renoa", 60, 60, 123120)
    assert (reading.imprint_stat, reading.imprint_value) == (Stat.EFFECTIVENESS, 0.15)
    assert reading.set_names == ["Penetration Set", "Speed Set"]
    stats = {s: (r.final, r.bonus) for s, r in reading.stats.items()}
    assert stats == {
        Stat.ATK: (1721, 751),
        Stat.DEF: (1879, 1276),
        Stat.HP: (13517, 8218),
        Stat.SPEED: (230, 108),
        Stat.CRIT_CHANCE: (0.36, 0.09),
        Stat.CRIT_DAMAGE: (1.85, 0.35),
        Stat.EFFECTIVENESS: (0.38, 0.38),
        Stat.EFFECT_RESISTANCE: (0.69, 0.69),
        Stat.DUAL_ATTACK: (0.03, None),
    }
    assert reading.warnings == []


def test_hero_info_screen_has_no_bonus() -> None:
    rows = [(label, final, None) for label, final, _ in STAT_ROWS]
    reading = parse_hero_screen(equipment_screen(rows=rows))
    assert reading.kind is ScreenKind.HERO_INFO and reading.stats[Stat.ATK].bonus is None


def test_merged_label_and_value_lines_and_level_numbers() -> None:
    lines = [ln for ln in equipment_screen() if not ln.text.startswith(("Attack", "1721", "751", "Lv."))]
    lines.append(line("Attack 1721", 160, 660))
    lines.append(line("Lv. 52/60", 162, 315, height=55))
    reading = parse_hero_screen(lines)
    assert reading.stats[Stat.ATK].final == 1721 and (reading.level, reading.level_cap) == (52, 60)


def test_wrong_units_are_not_accepted_silently() -> None:
    rows = [("Speed", "23.0%", None) if label == "Speed" else (label, f, b) for label, f, b in STAT_ROWS]
    reading = parse_hero_screen(equipment_screen(rows=rows))
    assert reading.stats[Stat.SPEED].final is None and reading.stats[Stat.SPEED].confidence == 0.0
    rows = [
        ("Critical Hit Chance", "36%", None) if label.endswith("Chance") and "Hit" in label else (label, f, b)
        for label, f, b in STAT_ROWS
    ]
    assert parse_hero_screen(equipment_screen(rows=rows)).stats[Stat.CRIT_CHANCE].confidence == 0.6


@pytest.mark.parametrize(
    ("imprint", "expected"),
    [
        # Lady of the Scales (Hero Info, 2026-10-04): the label itself wraps
        (
            [line("Effect", 172, 404), line("Resistance +", 172, 433), line("15%", 172, 462)],
            (Stat.EFFECT_RESISTANCE, 0.15),
        ),
        # crop sent by the user: the label wraps before "+"
        ([line("Critical Hit", 172, 404), line("Chance +6%", 172, 434)], (Stat.CRIT_CHANCE, 0.06)),
        ([line("Health+15%", 172, 420)], (Stat.HP_PERCENT, 0.15)),
        ([line("Defense + 64", 172, 420)], (Stat.DEF, 64.0)),
    ],
)
def test_imprint_text_wrapped_on_several_lines(imprint: list[TextLine], expected: tuple[Stat, float]) -> None:
    reading = parse_hero_screen(equipment_screen(imprint=imprint))
    assert (reading.imprint_stat, reading.imprint_value) == expected
    assert not reading.imprint_locked and reading.warnings == []


def test_a_locked_imprint_means_none_and_a_missing_one_is_reported() -> None:
    locked = [line("Locked", 172, 400, height=44), line("Health %", 172, 440), line("Additional", 172, 470)]
    reading = parse_hero_screen(equipment_screen(imprint=[*locked, line("Effect", 172, 500)]))
    assert reading.imprint_locked and reading.imprint_stat is None and reading.warnings == []
    missing = parse_hero_screen(equipment_screen(imprint=[]))
    assert not missing.imprint_locked and missing.imprint_stat is None
    assert missing.warnings == ["imprint not found (neither a value nor 'Locked')"]


def test_not_a_hero_screen_or_unknown_language() -> None:
    with pytest.raises(ScreenError, match="stat rows"):
        parse_hero_screen([line("Attack", 160, 660), line("Defense", 160, 696), line("Arena", 400, 100)])
    with pytest.raises(ScreenError, match="language 'pt'"):
        parse_hero_screen(equipment_screen(), GameLanguage.PT)


def test_label_matching_never_takes_a_near_miss() -> None:
    labels = {"Critical Hit Chance": 1, "Critical Hit Damage": 2, "Attack": 3}
    assert match_label("critical hit chancc", labels) == "Critical Hit Chance"
    assert match_label("Critical Hit", labels) is None  # equally close to two labels
    assert match_label("Attack+21%", labels) is None


# ------------------------------------------------------------------------------------------------ screen -> build


def entity(code: str, **fields: object) -> ResolvedEntity:
    facts = [
        Fact(
            entity_type=EntityType.HERO,
            entity_id=code,
            field=name.replace("__", "."),
            value=value,  # type: ignore[arg-type]
            source=SourceId.FRIBBELS,
            status=DataStatus.COMMUNITY,
        )
        for name, value in fields.items()
    ]
    return resolve(facts)[0]


def _renoa_fields(**changes: object) -> dict[str, object]:
    fields: dict[str, object] = {
        "name": "Renoa",
        "base__att": 970,
        "base__def": 603,
        "base__max_hp": 5299,
        "base__speed": 122,
        "base__cri": 0.27,
        "base__cri_dmg": 1.5,
        "base__acc": 0,
        "base__res": 0,
        "base__coop": 0.03,
        "imprint__stat": "acc",
        "imprint__values": {"B": 0.07, "SSS": 0.15},
    }
    fields.update(changes)
    return {k: v for k, v in fields.items() if v is not None}


RENOA = entity("c1193", **_renoa_fields())
NOW = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)


def test_equipment_screen_build_is_cross_checked_with_the_catalog() -> None:
    result = build_from_screen(parse_hero_screen(equipment_screen()), {"c1193": RENOA}, captured_at=NOW)
    assert result.problems == [] and result.build is not None
    build = result.build
    summary = (build.hero_code, build.level, build.stars, build.cp, build.source)
    assert summary == ("c1193", 60, 6, 123120, BuildSource.OCR)
    assert all(check.ok for check in result.base_checks) and len(result.base_checks) == 9
    assert build.confidence["final_stats.atk"] == 1.0 and build.confidence["awakening"] == ASSUMED
    assert build.imprint == Imprint(grade=ImprintGrade.SSS, stat=Stat.EFFECTIVENESS, value=0.15, mode=ImprintMode.SELF)
    assert build.confidence["imprint.mode"] == build.confidence["imprint.grade"] == INFERRED_MODE


def test_the_imprint_mode_is_inferred_only_from_clear_catalog_evidence() -> None:
    def scan(hero: ResolvedEntity, imprint: list[TextLine] = IMPRINT_LINES) -> tuple[Imprint | None, list[str]]:
        result = build_from_screen(
            parse_hero_screen(equipment_screen(imprint=imprint)), {"c1193": hero}, captured_at=NOW
        )
        assert result.build is not None
        return result.build.imprint, result.notes

    team, notes = scan(entity("c1193", **_renoa_fields(imprint__stat="def_rate")))
    assert team == Imprint(grade=None, stat=Stat.EFFECTIVENESS, value=0.15, mode=ImprintMode.TEAM)
    assert any("so it is the team imprint" in n for n in notes)
    off_table, notes = scan(entity("c1193", **_renoa_fields(imprint__values={"B": 0.07, "SSS": 0.16})))
    assert off_table is not None and (off_table.mode, off_table.grade) == (None, None)
    assert any("self/team and the grade are unknown" in n for n in notes)
    no_table, notes = scan(entity("c1193", **_renoa_fields(imprint__stat=None, imprint__values=None)))
    assert no_table is not None and no_table.mode is None and any("no imprint table" in n for n in notes)


def test_a_flat_percent_twin_is_not_taken_for_a_team_imprint() -> None:
    hero = entity("c1193", **_renoa_fields(imprint__stat="max_hp_rate", imprint__values={"B": 0.07, "SSS": 0.15}))
    lost_percent = [line("Health +", 172, 404), line("7", 172, 434)]  # "7%" with the "%" misread
    result = build_from_screen(
        parse_hero_screen(equipment_screen(imprint=lost_percent)), {"c1193": hero}, captured_at=NOW
    )
    assert result.build is not None and result.build.imprint == Imprint(grade=None, stat=Stat.HP, value=7.0)
    assert any("flat/percent twin" in n for n in result.notes)
    assert result.build.confidence["imprint.mode"] == ASSUMED


def test_a_rescan_keeps_a_known_imprint_mode_and_grade() -> None:
    no_table = entity("c1193", **_renoa_fields(imprint__stat=None, imprint__values=None))
    first = build_from_screen(parse_hero_screen(equipment_screen()), {"c1193": no_table}, captured_at=NOW)
    assert first.build is not None and first.build.imprint is not None
    known = first.build.imprint.model_copy(update={"mode": ImprintMode.TEAM, "grade": ImprintGrade.SSS})
    confidence = {k: v for k, v in first.build.confidence.items() if not k.startswith("imprint.")}  # typed by hand
    existing = first.build.model_copy(update={"imprint": known, "confidence": confidence})
    again = build_from_screen(
        parse_hero_screen(equipment_screen()), {"c1193": no_table}, captured_at=NOW, existing=existing
    )
    assert again.build is not None and again.build.imprint == known
    assert again.build.confidence["imprint.mode"] == again.build.confidence["imprint.grade"] == 1.0
    # a hand-typed mode beats the catalog inference, and the disagreement is reported
    self_known = existing.model_copy(update={"imprint": known.model_copy(update={"mode": ImprintMode.SELF})})
    team_hero = entity("c1193", **_renoa_fields(imprint__stat="def_rate"))
    kept = build_from_screen(
        parse_hero_screen(equipment_screen()), {"c1193": team_hero}, captured_at=NOW, existing=self_known
    )
    assert kept.build is not None and kept.build.imprint is not None and kept.build.imprint.mode is ImprintMode.SELF
    assert any("the catalog suggests team, the roster has self (kept" in n for n in kept.notes)


def test_locked_or_unread_imprints_are_never_silent() -> None:
    locked_lines = [line("Locked", 172, 400, height=44), line("Health %", 172, 440)]
    first = build_from_screen(parse_hero_screen(equipment_screen()), {"c1193": RENOA}, captured_at=NOW)
    assert first.build is not None and first.build.imprint is not None
    locked = build_from_screen(
        parse_hero_screen(equipment_screen(imprint=locked_lines)),
        {"c1193": RENOA},
        captured_at=NOW,
        existing=first.build,
    )
    assert locked.build is not None and locked.build.imprint is None and locked.build.confidence["imprint"] == 1.0
    assert not {"imprint.mode", "imprint.grade"} & set(locked.build.confidence)  # nothing left about a missing imprint
    assert any("shows 'Locked'" in n for n in locked.notes)
    kept = build_from_screen(
        parse_hero_screen(equipment_screen(imprint=[])), {"c1193": RENOA}, captured_at=NOW, existing=first.build
    )
    assert kept.build is not None and kept.build.imprint == first.build.imprint
    assert any("imprint not read: kept" in n for n in kept.notes)
    new = build_from_screen(parse_hero_screen(equipment_screen(imprint=[])), {"c1193": RENOA}, captured_at=NOW)
    assert new.build is not None and new.build.imprint is None and new.build.confidence["imprint"] == ASSUMED


def test_a_misread_value_lowers_confidence_and_is_reported() -> None:
    rows = [("Attack", "1727", "751") if label == "Attack" else row for row in STAT_ROWS for label in [row[0]]]
    result = build_from_screen(parse_hero_screen(equipment_screen(rows=rows)), {"c1193": RENOA}, captured_at=NOW)
    assert result.build is not None and result.build.confidence["final_stats.atk"] == 0.5
    assert any("att: the screen implies base 976 but the catalog says 970" in n for n in result.notes)


def test_unknown_or_fuzzy_names_are_never_silent() -> None:
    unknown = build_from_screen(parse_hero_screen(equipment_screen(name="Zzyzx")), {"c1193": RENOA}, captured_at=NOW)
    assert unknown.build is None and "no catalog hero is called 'Zzyzx'" in unknown.problems[0]
    fuzzy = build_from_screen(parse_hero_screen(equipment_screen(name="Renca")), {"c1193": RENOA}, captured_at=NOW)
    assert fuzzy.build is None  # 0.8 similarity: below the threshold, not guessed
    near = build_from_screen(parse_hero_screen(equipment_screen(name="Renoaa")), {"c1193": RENOA}, captured_at=NOW)
    assert near.hero_code == "c1193" and any("matched to 'Renoa'" in n for n in near.notes)


def test_a_rescan_keeps_what_the_screen_does_not_show() -> None:
    first = build_from_screen(parse_hero_screen(equipment_screen()), {"c1193": RENOA}, captured_at=NOW)
    assert first.build is not None
    confidence = {**first.build.confidence, "awakening": 1.0}
    existing = first.build.model_copy(update={"awakening": 5, "confidence": confidence})
    again = build_from_screen(
        parse_hero_screen(equipment_screen()), {"c1193": RENOA}, captured_at=NOW, existing=existing
    )
    assert again.build is not None and again.build.awakening == 5 and again.build.confidence["awakening"] == 1.0
    other = existing.model_copy(update={"hero_code": "c2011"})
    assert build_from_screen(
        parse_hero_screen(equipment_screen()), {"c1193": RENOA}, captured_at=NOW, existing=other
    ).problems


def test_capture_time_comes_from_the_file_name(tmp_path: Path) -> None:
    named = tmp_path / "20261004-103000-hero_info.png"
    named.write_bytes(b"x")
    assert captured_at(named) == datetime(2026, 10, 4, 10, 30, tzinfo=UTC)
    other = tmp_path / "shot.png"
    other.write_bytes(b"x")
    assert captured_at(other).tzinfo is UTC


def test_image_arguments_are_expanded_like_a_shell_would(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    folder = tmp_path / "caps [1]"
    folder.mkdir()
    for name in ("20261004-120000-b.png", "20261004-110000-a.webp", "notes.txt"):
        (folder / name).write_bytes(b"x")
    ordered = [folder / "20261004-110000-a.webp", folder / "20261004-120000-b.png"]
    assert expand_image_paths([folder]) == ordered  # a folder: its images, oldest capture first
    monkeypatch.setenv("E7AC_TEST_CAPTURES", str(tmp_path))
    pattern = f"${{E7AC_TEST_CAPTURES}}/{folder.name}/*"
    with pytest.raises(ImageError, match="no image matches"):
        expand_image_paths([pattern])  # "[1]" is a glob class here, so nothing matches: an error, not a silent skip
    escaped = "${E7AC_TEST_CAPTURES}/caps [[]1]/*.png"
    assert expand_image_paths([escaped, folder / "20261004-120000-b.png"]) == [ordered[1]]  # deduplicated
    literal = folder / "20261004-110000-a.webp"
    assert expand_image_paths([str(literal)]) == [literal]
    missing = tmp_path / "missing.png"
    assert expand_image_paths([missing]) == [missing]  # reported when loaded
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(ImageError, match="no images"):
        expand_image_paths([empty])


def test_image_arguments_run_oldest_first_and_odd_arguments_are_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os

    newer, older = tmp_path / "Screenshot (10).png", tmp_path / "Screenshot (9).png"
    for path, stamp in ((older, 1_000_000), (newer, 2_000_000)):
        path.write_bytes(b"x")
        os.utime(path, (stamp, stamp))
    # already-expanded files in name order (as a Windows shell or glob gives them) still run oldest first
    assert expand_image_paths([newer, older]) == [older, newer]
    assert expand_image_paths([str(tmp_path / "*.png")]) == [older, newer]
    with pytest.raises(ImageError, match="empty image argument"):
        expand_image_paths([""])
    monkeypatch.chdir(tmp_path)
    tilde = tmp_path / "~draft.png"
    tilde.write_bytes(b"x")
    assert expand_image_paths(["~draft.png"]) == [Path("~draft.png")]  # an existing path is taken literally


def test_typer_never_expands_arguments_before_e7_does(monkeypatch: pytest.MonkeyPatch) -> None:
    from e7ac.cli import app as app_module

    calls: list[dict[str, object]] = []
    monkeypatch.setattr(app_module, "app", lambda **kwargs: calls.append(kwargs))
    app_module.main()
    assert calls == [{"windows_expand_args": False}]


@pytest.mark.windows
def test_windows_percent_variables_are_expanded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "shot.png").write_bytes(b"x")
    monkeypatch.setenv("E7AC_TEST_CAPTURES", str(tmp_path))
    assert expand_image_paths(["%E7AC_TEST_CAPTURES%\\*.png"]) == [tmp_path / "shot.png"]


# ------------------------------------------------------------------------------------------------ CLI


class FakeReader:
    def __init__(self, lines: list[TextLine]) -> None:
        self.lines = lines

    def read(self, image: object) -> list[TextLine]:
        return self.lines


BBK_ROWS = [
    ("Attack", "4116", "2978"),
    ("Defense", "829", "367"),
    ("Health", "12819", "6948"),
    ("Speed", "133", "22"),
    ("Critical Hit Chance", "100.0%", "77.0%"),
    ("Critical Hit Damage", "357.0%", "207.0%"),
    ("Effectiveness", "0.0%", None),
    ("Effect Resistance", "21.0%", "21.0%"),
    ("Dual Attack Chance", "3.0%", None),
]


@pytest.fixture
def scan_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    import cv2
    import numpy as np

    image = tmp_path / "20261004-101500-hero.png"
    cv2.imwrite(str(image), np.zeros((20, 30, 3), dtype=np.uint8))
    lines = equipment_screen(rows=BBK_ROWS, name="Blood Blade Karin")
    monkeypatch.setattr(roster_cli, "reader_factory", lambda: FakeReader(lines))
    yield image


def test_scan_saves_then_reports_unchanged(scan_files: Path, synthetic_catalog: list[str]) -> None:
    first = runner.invoke(app, ["roster", "scan", str(scan_files)])
    assert first.exit_code == 0, first.output
    assert "Blood Blade Karin (c2011)" in first.stdout and "9/9 agree" in first.stdout
    assert "Saved as new roster hero #1" in first.stdout
    again = runner.invoke(app, ["roster", "scan", str(scan_files)])
    assert "#1: unchanged" in again.stdout
    dry = runner.invoke(app, ["roster", "scan", str(scan_files), "--dry-run"])
    assert "dry run" in dry.stdout
    assert "1 hero(es)" in runner.invoke(app, ["roster", "list"]).stdout


def test_scan_takes_folders_and_patterns(scan_files: Path, synthetic_catalog: list[str]) -> None:
    by_pattern = runner.invoke(app, ["roster", "scan", str(scan_files.parent / "*.png"), "--dry-run"])
    assert by_pattern.exit_code == 0, by_pattern.output
    assert f"{scan_files.name}:" in by_pattern.stdout
    nothing = runner.invoke(app, ["roster", "scan", str(scan_files.parent / "*.jpg")])
    assert nothing.exit_code == 2 and "no image matches" in nothing.stderr


def test_scan_needs_a_catalog(scan_files: Path, isolated_home: AppPaths) -> None:
    no_catalog = runner.invoke(app, ["roster", "scan", str(scan_files)])
    assert no_catalog.exit_code == 2 and "needs the catalog" in no_catalog.stderr  # checked before any slow OCR


def test_scan_reports_bad_images(scan_files: Path, synthetic_catalog: list[str], tmp_path: Path) -> None:
    broken = tmp_path / "broken.png"
    broken.write_bytes(b"not an image")
    bad = runner.invoke(app, ["roster", "scan", str(broken)])
    assert bad.exit_code == 1 and "is not an image" in bad.stderr


def test_scan_never_guesses_between_copies(scan_files: Path, synthetic_catalog: list[str]) -> None:
    for _ in range(2):
        assert runner.invoke(app, ["roster", "add", "c2011"]).exit_code == 0
    refused = runner.invoke(app, ["roster", "scan", str(scan_files)])
    assert refused.exit_code == 2 and "several copies" in refused.stderr
    chosen = runner.invoke(app, ["roster", "scan", str(scan_files), "--id", "2"])
    assert chosen.exit_code == 0 and "#2: new snapshot" in chosen.stdout


# ------------------------------------------------------------------------------------------------ real captures


# Transcribed from the user's captures (2026-10-04). Equipment tabs: every stat agrees with the catalog base stats.
# kind, name, level, CP, imprint stat and value, final ATK DEF HP SPD CC CD EFF ER DAC
EQUIP, INFO = ScreenKind.EQUIPMENT, ScreenKind.HERO_INFO
GOLDEN = {
    "screenshots/equip_renoa.webp": (
        EQUIP,
        "Renoa",
        60,
        123120,
        Stat.EFFECTIVENESS,
        0.15,
        [1721, 1879, 13517, 230, 0.36, 1.85, 0.38, 0.69, 0.03],
    ),
    "screenshots/equip_haru.webp": (
        EQUIP,
        "Haru",
        60,
        124427,
        Stat.HP_PERCENT,
        0.04,
        [1800, 1139, 22370, 189, 0.75, 3.25, 0.0, 0.30, 0.03],
    ),
    "screenshots/equip_straze.webp": (
        EQUIP,
        "Straze",
        60,
        139544,
        Stat.ATK_PERCENT,
        0.21,
        [5063, 863, 9321, 124, 1.0, 3.30, 0.23, 0.09, 0.03],
    ),
    "screenshots/heroinfo_charles.webp": (  # no gear, 5 stars, not awakened, "Lv. Max/50"
        INFO,
        "Closer Charles",
        50,
        14628,
        Stat.EFFECTIVENESS,
        0.06,
        [793, 381, 4267, 113, 0.15, 1.5, 0.0, 0.0, 0.03],
    ),
}


@pytest.mark.parametrize("scale", [1.0, 0.64, 1.28])  # 1.28 x the 2000-px copies = the user's 2560x1494 window
@pytest.mark.parametrize("fixture", [pytest.param(f, marks=pytest.mark.fixtures(f)) for f in sorted(GOLDEN)])
def test_real_capture(fixture: str, scale: float) -> None:
    import cv2

    from e7ac.vision.image import load_image
    from e7ac.vision.ocr import RapidOcrReader

    kind, name, level, cp, imprint_stat, imprint_value, finals = GOLDEN[fixture]
    image = load_image(FIXTURES_DIR / fixture)
    if scale != 1.0:
        interpolation = cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC
        image = cv2.resize(image, None, fx=scale, fy=scale, interpolation=interpolation)
    reading = parse_hero_screen(RapidOcrReader().read(image))
    assert (reading.kind, reading.name, reading.level, reading.cp) == (kind, name, level, cp)
    assert reading.warnings == []
    assert (reading.imprint_stat, reading.imprint_value) == (imprint_stat, imprint_value)
    assert [
        reading.stats[s].final
        for s in (
            Stat.ATK,
            Stat.DEF,
            Stat.HP,
            Stat.SPEED,
            Stat.CRIT_CHANCE,
            Stat.CRIT_DAMAGE,
            Stat.EFFECTIVENESS,
            Stat.EFFECT_RESISTANCE,
            Stat.DUAL_ATTACK,
        )
    ] == pytest.approx(finals)
