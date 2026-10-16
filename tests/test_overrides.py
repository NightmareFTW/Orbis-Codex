"""Hand-refined kits (SPEC D73): TOML parsing, validation, merging with the automatic kit, `e7 kit todo`."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest
from typer.testing import CliRunner

from e7ac.predict.effects import Effect, Kit
from e7ac.predict.overrides import KitError, apply_override, load_overrides, parse_override

GOOD = """
hero = "c2049"
source = "in-game skill text, test"
status = "verified"
replace = [3]
handles = ["Revive"]
cooldowns = { 3 = 4 }

[[effect]]
slot = 3
kind = "resurrect"
value = 0.2
receivers = "all"

[[effect]]
slot = 2
when = "turn"
kind = "heal"
scaling = [0.1, 0, 0]
receivers = "lowest:2"
"""


def test_a_hand_kit_replaces_slots_and_clears_what_it_handles() -> None:
    hand = parse_override(GOOD)
    auto = Kit(on_use={1: [Effect("stun", True, 0.0, 1, 0.4)], 3: [Effect("atk", False, 0.5, 3, 1.0)]})
    auto.unmodelled = ["Revive", "Fury"]
    kit, cooldowns = apply_override(auto, {1: 0, 3: 6}, hand)
    assert kit.on_use[1] == auto.on_use[1]  # untouched slot kept
    assert [e.kind for e in kit.on_use[3]] == ["resurrect"]  # replaced
    assert kit.on_turn[0].scaling == (0.1, 0.0, 0.0) and kit.on_turn[0].receivers == "lowest:2"
    assert kit.unmodelled == ["Fury"] and cooldowns == {1: 0, 3: 4}


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ('hero = "Maid Chloe"\nsource = "x"\nstatus = "verified"', "hero code"),
        ('hero = "c2049"\nstatus = "verified"', "'source'"),
        ('hero = "c2049"\nsource = "x"\nstatus = "sure"', "'status'"),
        ('hero = "c2049"\nsource = "x"\nstatus = "assumed"\n[[effect]]\nslot = 3\nkind = "nuke"', "unknown kind"),
        ('hero = "c2049"\nsource = "x"\nstatus = "assumed"\n[[effect]]\nkind = "heal"', "'slot'"),
        ('hero = "c2049"\nsource = "x"\nstatus = "assumed"\n[[effect]]\nslot = 1\nkind = "stun"\nchance = 2', "chance"),
        ("hero = ", "c"),
    ],
)
def test_a_malformed_hand_kit_is_an_error_naming_the_problem(text: str, message: str) -> None:
    with pytest.raises(KitError, match=message):
        parse_override(text, "c2049.toml")


def test_hand_kits_are_keyed_by_their_file_name(tmp_path: Path) -> None:
    (tmp_path / "c2049.toml").write_text(GOOD, encoding="utf-8")
    assert set(load_overrides(tmp_path)) == {"c2049"}
    (tmp_path / "c1001.toml").write_text(GOOD, encoding="utf-8")
    with pytest.raises(KitError, match="file name"):
        load_overrides(tmp_path)


def test_the_shipped_hand_kits_are_valid() -> None:
    load_overrides()  # every file in predict/kits parses and is named by its hero code


def test_kit_todo_lists_the_heroes_met_most_first(
    synthetic_catalog: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from e7ac.arena.references import ReferenceStore
    from e7ac.cli import kit as kit_cli
    from e7ac.cli.app import app
    from e7ac.paths import default_paths

    root = default_paths().home / "references"
    when = datetime(2026, 10, 16, tzinfo=UTC)
    crop = np.zeros((20, 20, 3), np.uint8)
    for i in range(3):
        crop[0, 0, 0] = i
        ReferenceStore(root / "faces").add("c9001", crop.copy(), when)
    ReferenceStore(root / "arena").add("c2011", crop, when)
    monkeypatch.setattr(kit_cli, "fetch_kit", lambda http, name: {})
    result = CliRunner().invoke(app, ["kit", "todo", "--offline"])
    assert result.exit_code == 0, result.output
    lines = [line for line in result.output.splitlines() if "x  " in line]
    assert "Test Hero" in lines[0] and "3x" in lines[0] and "Blood Blade Karin" in lines[1]
    assert "(no epic7db page)" in lines[0]
