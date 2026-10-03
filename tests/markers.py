"""Skip logic for the custom markers declared in pyproject.toml (kept importable so it can be tested).

- ``@pytest.mark.windows``: skipped unless running on Windows.
- ``@pytest.mark.network``: skipped unless ``E7AC_NETWORK_TESTS=1``.
- ``@pytest.mark.fixtures("screenshots/hero_info_bbk.png", ...)``: skipped when any listed path (relative to the
  git-ignored ``fixtures/`` folder) is missing; with no arguments, requires ``fixtures/screenshots``.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterable, Mapping
from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures"


def missing_fixtures(paths: Iterable[object], fixtures_dir: Path = FIXTURES_DIR) -> list[str]:
    required = [str(p) for p in paths] or ["screenshots"]
    return [p for p in required if not (fixtures_dir / p).exists()]


def apply_skips(
    items: Iterable[pytest.Item],
    *,
    fixtures_dir: Path = FIXTURES_DIR,
    platform: str = sys.platform,
    environ: Mapping[str, str] = os.environ,
) -> None:
    for item in items:
        if item.get_closest_marker("windows") is not None and platform != "win32":
            item.add_marker(pytest.mark.skip(reason="needs Windows"))
        if item.get_closest_marker("network") is not None and environ.get("E7AC_NETWORK_TESTS") != "1":
            item.add_marker(pytest.mark.skip(reason="network tests disabled (set E7AC_NETWORK_TESTS=1)"))
        marker = item.get_closest_marker("fixtures")
        if marker is not None:
            missing = missing_fixtures(marker.args, fixtures_dir)
            if missing:
                item.add_marker(pytest.mark.skip(reason=f"git-ignored fixtures missing: {', '.join(missing)}"))
