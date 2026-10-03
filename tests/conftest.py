from __future__ import annotations

from pathlib import Path

import pytest

from e7ac.paths import HOME_ENV_VAR, AppPaths
from tests.markers import apply_skips

pytest_plugins = ["pytester"]


@pytest.fixture(autouse=True)
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AppPaths:
    """Every test gets its own app home; nothing touches the real user folder."""
    home = tmp_path / "home"
    monkeypatch.setenv(HOME_ENV_VAR, str(home))
    return AppPaths(home=home)


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    apply_skips(items)
