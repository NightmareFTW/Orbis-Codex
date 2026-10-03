from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

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


def run(*args: str) -> object:
    return runner.invoke(app, list(args))


def test_add_by_code_without_catalog_and_by_name_with_catalog(isolated_home: AppPaths) -> None:
    no_catalog = runner.invoke(app, ["roster", "add", "Blood Blade Karin", *BBK_STATS])
    assert no_catalog.exit_code == 2 and "use a hero code" in no_catalog.stderr
    by_code = runner.invoke(app, ["roster", "add", "c2011", *BBK_STATS, "--arena"])
    assert by_code.exit_code == 0, by_code.output
    assert by_code.stdout.strip() == "Added #1 c2011"


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
    assert runner.invoke(app, ["roster", "add", "c2011", *BBK_STATS, "--arena"]).exit_code == 0
    assert (
        runner.invoke(
            app,
            [
                "roster",
                "add",
                "c1011",
                "--atk",
                "3000",
                "--def",
                "700",
                "--hp",
                "9000",
                "--spd",
                "150",
                "--cc",
                "60",
                "--cd",
                "200",
                "--eff",
                "0",
                "--er",
                "0",
                "--dac",
                "5",
            ],
        ).exit_code
        == 0
    )
    edited = runner.invoke(app, ["roster", "edit", "1", "--spd", "140"])
    assert edited.exit_code == 0 and "2 in history" in edited.stdout
    assert runner.invoke(app, ["roster", "edit", "1", "--spd", "140"]).stdout.strip() == "Nothing changed."
    history = runner.invoke(app, ["roster", "history", "1"]).stdout.splitlines()
    assert len(history) == 2 and history[1].startswith("*") and "SPD 140" in history[1]

    by_speed = runner.invoke(app, ["roster", "list", "--sort", "speed", "--desc"]).stdout.splitlines()
    assert "Karin" in by_speed[1] and "Blood Blade Karin" in by_speed[2]
    dark = runner.invoke(app, ["roster", "list", "--element", "dark"]).stdout
    assert "Blood Blade Karin" in dark and "1 hero(es)" in dark
    arena_only = runner.invoke(app, ["roster", "list", "--arena"]).stdout
    assert "1 hero(es)" in arena_only
    assert runner.invoke(app, ["roster", "arena", "1", "--off"]).exit_code == 0
    assert "0 hero(es)" in runner.invoke(app, ["roster", "list", "--arena"]).stdout
    bad_sort = runner.invoke(app, ["roster", "list", "--sort", "luck"])
    assert bad_sort.exit_code == 2

    shown = json.loads(runner.invoke(app, ["roster", "show", "1", "--json"]).stdout)
    assert shown["build"]["final_stats"]["speed"] == 140
    assert shown["build"]["final_stats"]["crit_damage"] == pytest.approx(3.57)
    assert runner.invoke(app, ["roster", "show", "99"]).exit_code == 2


def test_validation_blocks_errors_unless_forced(tmp_path: Path, isolated_home: AppPaths) -> None:
    build = {
        "stars": 6,
        "awakening": 6,
        "level": 60,
        "captured_at": "2026-10-03T12:00:00Z",
        "source": "manual",
        "gear": {
            "weapon": {
                "slot": "weapon",
                "set_code": "set_cri",
                "grade": "epic",
                "item_level": 90,
                "enhance": 15,
                "main": {"stat": "max_hp", "value": 2835},
                "substats": [],
            }
        },
    }
    path = tmp_path / "build.json"
    path.write_text(json.dumps(build), encoding="utf-8")
    refused = runner.invoke(app, ["roster", "add", "c2011", "--from-json", str(path)])
    assert refused.exit_code == 2 and "MECH-GEAR-08" in refused.stderr and "Not saved" in refused.stderr
    forced = runner.invoke(app, ["roster", "add", "c2011", "--from-json", str(path), "--force"])
    assert forced.exit_code == 0
    validated = runner.invoke(app, ["roster", "validate", "1"])
    assert validated.exit_code == 1 and "MECH-GEAR-08" in validated.stderr


def test_export_import_round_trip(tmp_path: Path, isolated_home: AppPaths, monkeypatch: pytest.MonkeyPatch) -> None:
    assert runner.invoke(app, ["roster", "add", "c2011", *BBK_STATS]).exit_code == 0
    assert runner.invoke(app, ["roster", "edit", "1", "--cp", "150000"]).exit_code == 0
    backup = tmp_path / "backup.json"
    exported = runner.invoke(app, ["roster", "export", str(backup)])
    assert exported.exit_code == 0 and "1 hero(es), 2 snapshot(s)" in exported.stdout

    monkeypatch.setenv("E7AC_HOME", str(tmp_path / "other-home"))
    imported = runner.invoke(app, ["roster", "import", str(backup)])
    assert imported.exit_code == 0 and "Imported 1 hero(es), 2 snapshot(s)" in imported.stdout
    again = runner.invoke(app, ["roster", "import", str(backup)])
    assert "0 snapshot(s); 2 already present" in again.stdout
    assert json.loads(runner.invoke(app, ["roster", "show", "1", "--json"]).stdout)["build"]["cp"] == 150000

    broken = tmp_path / "broken.json"
    broken.write_text('{"format": "something-else"}', encoding="utf-8")
    assert runner.invoke(app, ["roster", "import", str(broken)]).exit_code == 2
