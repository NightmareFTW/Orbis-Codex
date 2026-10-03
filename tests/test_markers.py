from __future__ import annotations

from pathlib import Path

import pytest

from tests.markers import missing_fixtures

PLUGIN = """
import os
from pathlib import Path
from tests.markers import apply_skips

def pytest_collection_modifyitems(config, items):
    apply_skips(items, fixtures_dir=Path(os.environ["T_FIXTURES"]), platform=os.environ["T_PLATFORM"],
                environ={"E7AC_NETWORK_TESTS": os.environ.get("T_NETWORK", "")})
"""

TESTS = """
import pytest

@pytest.mark.windows
def test_win(): pass

@pytest.mark.network
def test_net(): pass

@pytest.mark.fixtures("present.txt")
def test_fixture_present(): pass

@pytest.mark.fixtures("present.txt", "absent.png")
def test_fixture_absent(): pass

@pytest.mark.fixtures
def test_fixture_default(): pass

def test_plain(): pass
"""


def _run(pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch, platform: str, network: str) -> pytest.RunResult:
    fixtures = pytester.path / "fx"
    fixtures.mkdir()
    (fixtures / "present.txt").write_text("x", encoding="utf-8")
    monkeypatch.setenv("T_FIXTURES", str(fixtures))
    monkeypatch.setenv("T_PLATFORM", platform)
    monkeypatch.setenv("T_NETWORK", network)
    pytester.makeconftest(PLUGIN)
    pytester.makepyfile(TESTS)
    return pytester.runpytest(
        "-p", "no:cacheprovider", "-rs", "-o", "markers=windows\nnetwork\nfixtures", "--strict-markers"
    )


def test_skips_off_windows_without_network(pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch) -> None:
    result = _run(pytester, monkeypatch, platform="linux", network="")
    result.assert_outcomes(passed=2, skipped=4)
    result.stdout.fnmatch_lines(
        [
            "*needs Windows*",
            "*network tests disabled*",
            "*fixtures missing: absent.png*",
            "*fixtures missing: screenshots*",
        ]
    )


def test_runs_on_windows_with_network(pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch) -> None:
    result = _run(pytester, monkeypatch, platform="win32", network="1")
    result.assert_outcomes(passed=4, skipped=2)


def test_missing_fixtures_helper(tmp_path: Path) -> None:
    (tmp_path / "screenshots").mkdir()
    assert missing_fixtures([], tmp_path) == []
    assert missing_fixtures(["screenshots", "saves/a.json"], tmp_path) == ["saves/a.json"]
