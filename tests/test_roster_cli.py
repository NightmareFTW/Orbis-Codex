from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner, Result

from e7ac.cli.app import app
from e7ac.paths import AppPaths

runner = CliRunner()

BBK_STATS = [
    "--atk",
    "4116",
    "--def",
    "829",
    "--hp",
    "12819",
    "--spd",
    "133",
    "--cc",
    "100",
    "--cd",
    "357",
    "--eff",
    "0",
    "--er",
    "21",
    "--dac",
    "3",
    "--cp",
    "141750",
]


FULL_BUILD: dict[str, Any] = {
    "stars": 6,
    "awakening": 6,
    "level": 60,
    "captured_at": "2026-10-03T12:00:00Z",
    "source": "manual",
    "gear": {
        "weapon": {
            "slot": "weapon",
            "set_code": "set_cri_dmg",
            "grade": "epic",
            "item_level": 90,
            "enhance": 15,
            "main": {"stat": "att", "value": 525},
            "substats": [
                {"stat": "cri", "value": 0.12},
                {"stat": "cri_dmg", "value": 0.2},
                {"stat": "speed", "value": 8},
                {"stat": "att_rate", "value": 0.15},
            ],
        }
    },
    "artifact": {"code": "efz01", "level": 30},
    "imprint": {"grade": "SSS", "stat": "att_rate", "value": 0.18},
    "exclusive_equipment": {"stat": "cri", "value": 0.12},
    "final_stats": {
        "atk": 4116,
        "defense": 829,
        "hp": 12819,
        "speed": 133,
        "crit_chance": 1.0,
        "crit_damage": 3.57,
        "effectiveness": 0.0,
        "effect_resistance": 0.21,
        "dual_attack": 0.03,
    },
}


def run(*args: str) -> Result:
    return runner.invoke(app, list(args))


def write_json(path: Path, data: object) -> Path:
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def shown(owned_id: int = 1) -> dict[str, Any]:
    result = run("roster", "show", str(owned_id), "--json")
    assert result.exit_code == 0, result.output
    build: dict[str, Any] = json.loads(result.stdout)["build"]
    return build


def test_add_by_code_without_catalog_and_by_name_with_catalog(isolated_home: AppPaths) -> None:
    no_catalog = runner.invoke(app, ["roster", "add", "Blood Blade Karin", *BBK_STATS])
    assert no_catalog.exit_code == 2 and "use a hero code" in no_catalog.stderr
    by_code = runner.invoke(app, ["roster", "add", "c2011", *BBK_STATS, "--arena"])
    assert by_code.exit_code == 0, by_code.output
    assert by_code.stdout.strip() == "Added #1 c2011"
    assert "assumed stars 6, awakening 6, level 60" in by_code.stderr  # defaults are said, not silent
    assert "no catalog yet" in by_code.stderr


def test_name_resolution_never_picks_a_near_miss(synthetic_catalog: list[str]) -> None:
    ok = runner.invoke(app, ["roster", "add", "blood blade karin", *BBK_STATS])
    assert ok.exit_code == 0, ok.output
    partial = runner.invoke(app, ["roster", "add", "blood blade", *BBK_STATS])
    assert partial.exit_code == 2 and "c2011" in partial.stderr and "not an exact hero name" in partial.stderr
    ambiguous = runner.invoke(app, ["roster", "add", "twin", *BBK_STATS])
    assert ambiguous.exit_code == 2 and "matches several heroes" in ambiguous.stderr


def test_partial_final_stats_are_refused_the_first_time(isolated_home: AppPaths) -> None:
    result = runner.invoke(app, ["roster", "add", "c2011", "--atk", "4116"])
    assert result.exit_code == 2 and "missing: defense" in result.stderr


def test_edit_history_list_show(synthetic_catalog: list[str]) -> None:
    assert run("roster", "add", "c2011", *BBK_STATS, "--arena").exit_code == 0
    karin = ["--atk", "3000", "--def", "700", "--hp", "9000", "--spd", "150", "--cc", "60", "--cd", "200"]
    assert run("roster", "add", "c1011", *karin, "--eff", "0", "--er", "0", "--dac", "5").exit_code == 0
    assert run("roster", "add", "c9001").exit_code == 0  # no final stats yet
    edited = run("roster", "edit", "1", "--spd", "140")
    assert edited.exit_code == 0 and "2 in history" in edited.stdout
    assert run("roster", "edit", "1", "--spd", "140").stdout.strip() == "Nothing changed."
    history = run("roster", "history", "1").stdout.splitlines()
    assert len(history) == 2 and history[1].startswith("*") and "SPD 140" in history[1]

    def codes(*args: str) -> list[str]:
        return [line.split()[1] for line in run("roster", "list", *args).stdout.splitlines()[1:-1]]

    # speed order (Karin 150 > BBK 140) differs from id order; heroes without stats sort first ascending
    assert codes("--sort", "speed") == ["c9001", "c2011", "c1011"]
    assert codes("--sort", "speed", "--desc") == ["c1011", "c2011", "c9001"]
    assert codes("--sort", "name") == ["c2011", "c1011", "c9001"]  # Blood Blade Karin, Karin, Test Hero
    dark = run("roster", "list", "--element", "dark").stdout
    assert "Blood Blade Karin" in dark and "1 hero(es)" in dark
    assert "1 hero(es)" in run("roster", "list", "--arena").stdout
    assert run("roster", "arena", "1", "--off").exit_code == 0
    assert "0 hero(es)" in run("roster", "list", "--arena").stdout
    assert run("roster", "list", "--sort", "luck").exit_code == 2

    build = shown(1)
    assert build["final_stats"]["speed"] == 140
    assert build["final_stats"]["crit_damage"] == pytest.approx(3.57)
    assert run("roster", "show", "99").exit_code == 2


def test_every_percent_option_is_converted(isolated_home: AppPaths) -> None:
    assert run("roster", "add", "c2011", *BBK_STATS).exit_code == 0
    stats = shown()["final_stats"]
    assert stats == {
        "atk": 4116,
        "defense": 829,
        "hp": 12819,
        "speed": 133,
        "crit_chance": pytest.approx(1.0),
        "crit_damage": pytest.approx(3.57),
        "effectiveness": pytest.approx(0.0),
        "effect_resistance": pytest.approx(0.21),
        "dual_attack": pytest.approx(0.03),
    }


def test_errors_block_unless_forced_and_game_rules_only_warn(tmp_path: Path, isolated_home: AppPaths) -> None:
    hp_weapon = {
        **FULL_BUILD,
        "gear": {"weapon": {**FULL_BUILD["gear"]["weapon"], "main": {"stat": "max_hp", "value": 2835}}},
    }
    saved = run("roster", "add", "c2011", "--from-json", str(write_json(tmp_path / "w.json", hp_weapon)))
    assert saved.exit_code == 0 and "WARNING MECH-GEAR-08" in saved.stderr  # community rule: reported, not blocking
    typed = dict(FULL_BUILD["gear"]["weapon"], substats=[{"stat": "cri", "value": 12}])
    percent = write_json(tmp_path / "p.json", {**FULL_BUILD, "gear": {"weapon": typed}})
    refused = run("roster", "add", "c2011", "--from-json", str(percent))
    assert refused.exit_code == 2 and "UNIT-RATE" in refused.stderr and "not saved" in refused.stderr
    forced = run("roster", "add", "c2011", "--from-json", str(percent), "--force")
    assert forced.exit_code == 0
    validated = run("roster", "validate", "2")
    assert validated.exit_code == 1 and "UNIT-RATE" in validated.stderr


def test_edit_keeps_every_component_and_is_validated(tmp_path: Path, isolated_home: AppPaths) -> None:
    assert run("roster", "add", "c2011", "--from-json", str(write_json(tmp_path / "b.json", FULL_BUILD))).exit_code == 0
    before = shown()
    assert run("roster", "edit", "1", "--spd", "140").exit_code == 0
    after = shown()
    for part in ("gear", "artifact", "imprint", "exclusive_equipment", "skills"):
        assert after[part] == before[part], part
    assert after["final_stats"] == {**before["final_stats"], "speed": 140}
    refused = run("roster", "edit", "1", "--cc", "1200")
    assert refused.exit_code == 2 and "UNIT-RATE" in refused.stderr
    assert len(run("roster", "history", "1").stdout.splitlines()) == 2


def test_catalog_checks_are_wired_and_only_warn(tmp_path: Path, synthetic_catalog: list[str]) -> None:
    build = {
        **FULL_BUILD,
        "artifact": {"code": "efz03", "level": 30},  # warrior-only artifact on an assassin
        "imprint": {"grade": "SSS", "stat": "max_hp_rate", "value": 0.18},  # catalog: att_rate
        "exclusive_equipment": {"stat": "speed", "value": 4},  # catalog: cri
        "final_stats": {**FULL_BUILD["final_stats"], "crit_damage": 0.0357},  # 3.57 typed as a fraction
    }
    result = run("roster", "add", "c2011", "--from-json", str(write_json(tmp_path / "b.json", build)))
    assert result.exit_code == 0, result.output
    for rule in ("MECH-ART-04", "MECH-IMP-01", "MECH-EE-01", "MECH-STAT-03"):
        assert f"WARNING {rule}" in result.stderr, rule
    assert "(catalog class lock: verified)" in result.stderr
    assert "no catalog yet" not in result.stderr


def test_unknown_codes_and_missing_catalog_are_explained(
    isolated_home: AppPaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert run("roster", "add", "c2021", *BBK_STATS).exit_code == 0  # typo of c2011, no catalog to tell
    validated = run("roster", "validate", "1")
    assert validated.exit_code == 0 and "catalog checks" in validated.stderr and "e7 catalog sync" in validated.stderr
    no_catalog = run("roster", "list", "--element", "dark")
    assert no_catalog.exit_code == 2 and "need the catalog" in no_catalog.stderr


def test_unknown_codes_warn_once_a_catalog_exists(synthetic_catalog: list[str]) -> None:
    added = run("roster", "add", "c2021", *BBK_STATS)
    assert added.exit_code == 0 and "WARNING CATALOG-UNKNOWN" in added.stderr and "'c2021'" in added.stderr
    assert "CATALOG-UNKNOWN" in run("roster", "validate", "1").stderr
    listed = run("roster", "list", "--element", "dark")
    assert "0 hero(es)" in listed.stdout and "1 hero(es) not in the catalog" in listed.stderr


def test_from_json_for_another_hero_is_refused(tmp_path: Path, isolated_home: AppPaths) -> None:
    karin = write_json(tmp_path / "k.json", {**FULL_BUILD, "hero_code": "c1011"})
    refused = run("roster", "add", "c2011", "--from-json", str(karin))
    assert refused.exit_code == 2 and "is a build of 'c1011', not 'c2011'" in refused.stderr
    assert run("roster", "add", "c2011", *BBK_STATS).exit_code == 0
    assert run("roster", "edit", "1", "--from-json", str(karin)).exit_code == 2
    assert len(run("roster", "history", "1").stdout.splitlines()) == 1


@pytest.mark.parametrize(
    ("name", "content"),
    [
        ("missing.json", None),
        ("broken.json", b"{not json"),
        ("list.json", b"[1, 2]"),
        ("nan.json", json.dumps(FULL_BUILD).replace('"value": 0.12}', '"value": NaN}', 1).encode()),
        ("inf.json", json.dumps(FULL_BUILD).replace('"speed": 133', '"speed": Infinity', 1).encode()),
        ("huge.json", json.dumps(FULL_BUILD).replace('"atk": 4116', '"atk": 99999999999999999999', 1).encode()),
    ],
)
@pytest.mark.parametrize("command", ["add", "edit"])
def test_bad_json_input_fails_cleanly(
    tmp_path: Path, isolated_home: AppPaths, name: str, content: bytes | None, command: str
) -> None:
    path = tmp_path / name
    if content is not None:
        path.write_bytes(content)
    if command == "edit":
        assert run("roster", "add", "c2011", *BBK_STATS).exit_code == 0
    target = ["c2011"] if command == "add" else ["1"]
    result = run("roster", command, *target, "--from-json", str(path))
    assert result.exit_code == 2, result.output
    assert "Error:" in result.stderr and isinstance(result.exception, SystemExit)  # a clean exit, no traceback
    expected_heroes = "0 hero(es)" if command == "add" else "1 hero(es)"
    assert expected_heroes in run("roster", "list").stdout
    if command == "edit":
        assert len(run("roster", "history", "1").stdout.splitlines()) == 1


def test_show_json_in_utf16_round_trips_into_edit(tmp_path: Path, isolated_home: AppPaths) -> None:
    """PowerShell 5 `> file` writes UTF-16 with a BOM; `show --json` wraps the build: both are accepted."""
    assert run("roster", "add", "c2011", "--from-json", str(write_json(tmp_path / "b.json", FULL_BUILD))).exit_code == 0
    dumped = tmp_path / "dump.json"
    dumped.write_text(run("roster", "show", "1", "--json").stdout, encoding="utf-16")
    again = run("roster", "edit", "1", "--from-json", str(dumped))
    assert again.exit_code == 0 and again.stdout.strip() == "Nothing changed."


def test_options_override_from_json_and_replace_stale_confidence(tmp_path: Path, isolated_home: AppPaths) -> None:
    ocr = {**FULL_BUILD, "source": "ocr", "confidence": {"final_stats.speed": 0.35, "atk": 0.5}}
    path = write_json(tmp_path / "ocr.json", ocr)
    added = run("roster", "add", "c2011", "--from-json", str(path), "--stars", "5", "--awakening", "3", "--level", "50")
    assert added.exit_code == 0, added.output
    build = shown()
    assert (build["stars"], build["awakening"], build["level"], build["source"]) == (5, 3, 50, "ocr")
    assert "assumed" not in added.stderr
    assert run("roster", "edit", "1", "--spd", "135").exit_code == 0
    edited = shown()
    assert edited["source"] == "manual"
    assert edited["confidence"] == {"atk": 0.5}  # the typed speed no longer carries the OCR doubt


def test_percent_options_explain_small_values(isolated_home: AppPaths) -> None:
    stats = [a if a != "21" else "0.21" for a in BBK_STATS]  # ER typed as a fraction
    result = run("roster", "add", "c2011", *stats)
    assert result.exit_code == 0 and "note: --er 0.21 means 0.21%" in result.stderr


def test_crit_damage_typed_as_fraction_is_flagged_with_a_catalog(synthetic_catalog: list[str]) -> None:
    stats = [a if a != "357" else "3.57" for a in BBK_STATS]
    result = run("roster", "add", "c2011", *stats)
    assert (
        result.exit_code == 0 and "WARNING MECH-STAT-03" in result.stderr and "below this hero's base" in result.stderr
    )


def test_export_never_clobbers_silently(tmp_path: Path, isolated_home: AppPaths) -> None:
    assert run("roster", "add", "c2011", *BBK_STATS).exit_code == 0
    backup = tmp_path / "backup.json"
    backup.write_text("my only backup", encoding="utf-8")
    refused = run("roster", "export", str(backup))
    assert refused.exit_code == 2 and "--force" in refused.stderr
    assert backup.read_text(encoding="utf-8") == "my only backup"
    assert run("roster", "export", str(backup), "--force").exit_code == 0
    assert json.loads(backup.read_text(encoding="utf-8"))["format"] == "orbis-codex-roster"
    database = run("roster", "export", str(isolated_home.database), "--force")
    assert database.exit_code == 2 and "roster database" in database.stderr
    nowhere = run("roster", "export", str(tmp_path / "no" / "such" / "dir.json"))
    assert nowhere.exit_code == 2 and "cannot write" in nowhere.stderr
    folder = run("roster", "export", str(tmp_path))
    assert folder.exit_code == 2 and "is a folder" in folder.stderr


def test_export_import_round_trip(tmp_path: Path, isolated_home: AppPaths, monkeypatch: pytest.MonkeyPatch) -> None:
    assert run("roster", "add", "c2011", *BBK_STATS).exit_code == 0
    assert run("roster", "edit", "1", "--cp", "150000").exit_code == 0
    backup = tmp_path / "backup.json"
    exported = run("roster", "export", str(backup))
    assert exported.exit_code == 0 and "1 hero(es), 2 snapshot(s)" in exported.stdout

    monkeypatch.setenv("E7AC_HOME", str(tmp_path / "other-home"))
    imported = run("roster", "import", str(backup))
    assert imported.exit_code == 0 and "Imported 1 hero(es), 2 snapshot(s)" in imported.stdout
    again = run("roster", "import", str(backup))
    assert "0 snapshot(s); 2 already present" in again.stdout
    assert shown()["cp"] == 150000

    broken = tmp_path / "broken.json"
    broken.write_text('{"format": "something-else"}', encoding="utf-8")
    assert run("roster", "import", str(broken)).exit_code == 2


def _two_snapshot_backup(tmp_path: Path) -> dict[str, Any]:
    assert run("roster", "add", "c2011", *BBK_STATS).exit_code == 0
    assert run("roster", "edit", "1", "--cp", "150000").exit_code == 0
    path = tmp_path / "good.json"
    assert run("roster", "export", str(path)).exit_code == 0
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


@pytest.mark.parametrize(
    "corrupt",
    [
        lambda d: d["heroes"][0]["snapshots"][0].update(is_current=True),  # two current snapshots
        lambda d: d["heroes"][0]["snapshots"][0]["build"].update(hero_code="c1011"),
        lambda d: d["heroes"][0]["snapshots"][1].update(uid=d["heroes"][0]["snapshots"][0]["uid"]),
        lambda d: d["heroes"][0].update(hero_code="Blood Blade Karin"),
        lambda d: d.update(exported_at="2026-10-03T12:00:00"),
    ],
)
def test_corrupted_backups_import_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, isolated_home: AppPaths, corrupt: Callable[[dict[str, Any]], None]
) -> None:
    data = _two_snapshot_backup(tmp_path)
    corrupt(data)
    monkeypatch.setenv("E7AC_HOME", str(tmp_path / "fresh"))
    result = run("roster", "import", str(write_json(tmp_path / "bad.json", data)))
    assert result.exit_code == 2 and "cannot read backup" in result.stderr
    assert "0 hero(es)" in run("roster", "list").stdout


def test_conflicting_backup_is_refused_as_a_whole(tmp_path: Path, isolated_home: AppPaths) -> None:
    data = _two_snapshot_backup(tmp_path)
    data["heroes"][0]["uid"] = "someone-else"  # same snapshot uids, different owner
    data["heroes"][0]["hero_code"] = "c1011"
    for snap in data["heroes"][0]["snapshots"]:
        snap["build"]["hero_code"] = "c1011"
    result = run("roster", "import", str(write_json(tmp_path / "clash.json", data)))
    assert result.exit_code == 2 and "conflicts with the roster" in result.stderr
    assert "1 hero(es)" in run("roster", "list").stdout


def test_import_reports_problems_in_imported_builds(
    tmp_path: Path, isolated_home: AppPaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    typed = dict(FULL_BUILD["gear"]["weapon"], substats=[{"stat": "cri", "value": 12}])
    forced = write_json(tmp_path / "p.json", {**FULL_BUILD, "gear": {"weapon": typed}})
    assert run("roster", "add", "c2011", "--from-json", str(forced), "--force").exit_code == 0
    assert run("roster", "export", str(tmp_path / "b.json")).exit_code == 0
    monkeypatch.setenv("E7AC_HOME", str(tmp_path / "fresh"))
    result = run("roster", "import", str(tmp_path / "b.json"))
    assert result.exit_code == 0 and "1 error(s) (e7 roster validate 1)" in result.stderr
