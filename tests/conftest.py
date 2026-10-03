from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from e7ac.paths import HOME_ENV_VAR, AppPaths


@pytest.fixture(autouse=True)
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AppPaths:
    """Every test gets its own app home; nothing touches the real user folder."""
    home = tmp_path / "home"
    monkeypatch.setenv(HOME_ENV_VAR, str(home))
    return AppPaths(home=home)


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    skip_windows = pytest.mark.skip(reason="needs Windows")
    skip_network = pytest.mark.skip(reason="network tests disabled (set E7AC_NETWORK_TESTS=1)")
    for item in items:
        if "windows" in item.keywords and sys.platform != "win32":
            item.add_marker(skip_windows)
        if "network" in item.keywords and os.environ.get("E7AC_NETWORK_TESTS") != "1":
            item.add_marker(skip_network)
