from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from e7ac.catalog.facts import EntityType
from e7ac.catalog.store import current_snapshot, load_entity
from e7ac.cli import catalog as catalog_cli
from e7ac.cli.app import app
from e7ac.domain.codes import DataStatus
from e7ac.paths import AppPaths
from e7ac.sources.http import CachedHttp, HttpConfig
from e7ac.storage.db import open_database, session_scope
from tests.catalog_data import make_handler

runner = CliRunner()


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[str]]:
    """Route the CLI's HTTP through the synthetic handler; record every request."""
    seen: list[str] = []
    state = {"fail": None}

    def factory(paths: AppPaths, offline: bool) -> CachedHttp:
        return CachedHttp(
            cache_dir=paths.cache_dir,
            config=HttpConfig(min_interval=0.0, max_retries=0),
            offline=offline,
            transport=httpx.MockTransport(make_handler(calls=seen, fail=state["fail"])),
            sleep=lambda s: None,
        )

    monkeypatch.setattr(catalog_cli, "http_factory", factory)
    yield seen


def test_show_without_a_catalog_explains_what_to_do() -> None:
    result = runner.invoke(app, ["catalog", "show", "c2011"])
    assert result.exit_code == 2
    assert "e7 catalog sync" in result.stderr


def test_sync_then_second_sync_is_served_from_cache(calls: list[str], isolated_home: AppPaths) -> None:
    first = runner.invoke(app, ["catalog", "sync"])
    assert first.exit_code == 0, first.output
    assert "Snapshot #1 (new)" in first.stdout
    assert "names shared by several hero codes" in first.stdout  # the two synthetic "Twin" heroes
    requests_after_first = len(calls)
    assert requests_after_first > 0

    second = runner.invoke(app, ["catalog", "sync"])
    assert second.exit_code == 0, second.output
    assert "Snapshot #1 (unchanged)" in second.stdout
    assert "0 network request(s)" in second.stdout
    assert len(calls) == requests_after_first

    engine = open_database(isolated_home.database)
    with session_scope(engine) as session:
        snapshot = current_snapshot(session)
        assert snapshot is not None
        bbk = load_entity(session, snapshot.id, EntityType.HERO, "c2011")
    assert bbk is not None
    for field, value in (("base.att", 1138), ("base.max_hp", 5871), ("base.def", 462), ("base.speed", 111)):
        assert bbk.value(field) == value
    assert bbk.status("name") is DataStatus.VERIFIED
    assert bbk.status("base.att") is DataStatus.COMMUNITY
    assert bbk.fields["base.att"].corroborated  # Fribbels + e7calc agree


def test_show_exact_partial_and_ambiguous(calls: list[str]) -> None:
    assert runner.invoke(app, ["catalog", "sync"]).exit_code == 0
    exact = runner.invoke(app, ["catalog", "show", "blood blade karin"])
    assert exact.exit_code == 0, exact.output
    assert "Blood Blade Karin (c2011)" in exact.stdout and "Skill S3" in exact.stdout
    partial = runner.invoke(app, ["catalog", "show", "blood"])
    assert partial.exit_code == 1 and "c2011" in partial.stderr and "No exact match" in partial.stderr
    ambiguous = runner.invoke(app, ["catalog", "show", "twin"])
    assert ambiguous.exit_code == 1 and "c9002" in ambiguous.stderr and "c9003" in ambiguous.stderr
    karin = runner.invoke(app, ["catalog", "show", "karin"])  # exact "Karin" is a different hero than BBK
    assert karin.exit_code == 0 and "Karin (c1011)" in karin.stdout
    as_json = json.loads(runner.invoke(app, ["catalog", "show", "c2011", "--json"]).stdout)
    assert as_json["entity"]["fields"]["base.att"]["status"] == "community"
    assert [s["entity_id"] for s in as_json["skills"]] == ["c2011:s1", "c2011:s3"]


def test_conflicts_coverage_and_snapshots(calls: list[str]) -> None:
    assert runner.invoke(app, ["catalog", "sync"]).exit_code == 0
    conflicts = runner.invoke(app, ["catalog", "conflicts"])
    assert conflicts.exit_code == 0
    assert "c9001" in conflicts.stdout and "base.att" in conflicts.stdout  # Fribbels 1000 vs e7calc 1300
    coverage = runner.invoke(app, ["catalog", "coverage", "--type", "artifact"])
    assert "efz09" in coverage.stdout and "missing: name" in coverage.stdout
    snapshots = runner.invoke(app, ["catalog", "snapshots"])
    assert snapshots.stdout.startswith("* #1")


def test_partial_failure_is_visible_and_nonzero(monkeypatch: pytest.MonkeyPatch) -> None:
    def factory(paths: AppPaths, offline: bool) -> CachedHttp:
        def fail(url: str) -> int | None:
            return 500 if url.endswith("heroes.ts") else None

        return CachedHttp(
            cache_dir=paths.cache_dir,
            config=HttpConfig(min_interval=0.0, max_retries=0),
            transport=httpx.MockTransport(make_handler(fail=fail)),
            sleep=lambda s: None,
        )

    monkeypatch.setattr(catalog_cli, "http_factory", factory)
    result = runner.invoke(app, ["catalog", "sync"])
    assert result.exit_code == 1
    assert "ERROR: e7calc" in result.stderr
    assert "Snapshot #1 (new)" in result.stdout


def test_offline_sync_without_cache_fails_cleanly(calls: list[str]) -> None:
    result = runner.invoke(app, ["catalog", "sync", "--offline"])
    assert result.exit_code == 2
    assert "no source produced data" in result.stderr
    assert calls == []


@pytest.mark.network
def test_live_sync_blood_blade_karin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Real sources (skipped unless E7AC_NETWORK_TESTS=1). Acceptance M2: c2011 = 1138/5871/462/111."""
    monkeypatch.setenv("E7AC_HOME", str(tmp_path / "live"))
    assert os.environ["E7AC_HOME"].endswith("live")
    result = runner.invoke(app, ["catalog", "sync"])
    assert result.exit_code in (0, 1), result.output
    shown = json.loads(runner.invoke(app, ["catalog", "show", "c2011", "--json"]).stdout)["entity"]["fields"]
    assert [shown[f"base.{s}"]["value"] for s in ("att", "max_hp", "def", "speed")] == [1138, 5871, 462, 111]
    assert shown["name"]["status"] == "verified"
