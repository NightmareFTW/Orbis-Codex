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


@pytest.fixture
def synthetic_catalog(monkeypatch: pytest.MonkeyPatch, isolated_home: AppPaths) -> list[str]:
    """Run `e7 catalog sync` against the synthetic sources so commands can resolve hero names."""
    import httpx
    from typer.testing import CliRunner

    from e7ac.cli import catalog as catalog_cli
    from e7ac.cli.app import app
    from e7ac.sources.http import CachedHttp, HttpConfig
    from tests.catalog_data import make_handler

    calls: list[str] = []

    def factory(paths: AppPaths, offline: bool) -> CachedHttp:
        return CachedHttp(
            cache_dir=paths.cache_dir,
            config=HttpConfig(min_interval=0.0, max_retries=0),
            offline=offline,
            transport=httpx.MockTransport(make_handler(calls=calls)),
            sleep=lambda s: None,
        )

    monkeypatch.setattr(catalog_cli, "http_factory", factory)
    result = CliRunner().invoke(app, ["catalog", "sync"])
    assert result.exit_code == 0, result.output
    return calls
