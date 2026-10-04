"""Gear set icons (M7): binary HTTP cache, Stove set-icon assets, `e7 catalog sync` / `e7 doctor` integration, and
the badge reader `vision.sets` (synthetic badges always; golden tests on the user's git-ignored captures)."""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from functools import cache
from itertools import pairwise
from pathlib import Path
from typing import Any

import cv2
import httpx
import numpy as np
import numpy.typing as npt
import pytest
from typer.testing import CliRunner

from e7ac.cli import catalog as catalog_cli
from e7ac.cli.app import app
from e7ac.paths import AppPaths
from e7ac.sources.assets import (
    SET_ICON_NAMESPACE,
    SET_ICON_URL,
    fetch_set_icons,
    load_set_icons,
    set_icon_url,
    validate_png,
)
from e7ac.sources.http import USER_AGENT, CachedHttp, FetchError, HttpConfig, read_cached_bytes
from e7ac.vision.ocr import Box, TextLine
from e7ac.vision.sets import ACTIVE_MAX, SetIconError, SetIconMatcher, SetMatch
from tests.catalog_data import make_handler
from tests.markers import FIXTURES_DIR

type Image = npt.NDArray[np.uint8]

runner = CliRunner()
ICON_HOST = "static-pubcomm.onstove.com"


def png(image: Image) -> bytes:
    ok, buffer = cv2.imencode(".png", image)
    assert ok
    return buffer.tobytes()


TINY_PNG = png(np.zeros((2, 2, 4), np.uint8))


# ====================================================================== binary HTTP cache (no network)


class FakeTime:
    def __init__(self) -> None:
        self.mono = 1000.0
        self.wall = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)
        self.sleeps: list[float] = []

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.mono += seconds

    def advance(self, delta: timedelta) -> None:
        self.wall += delta
        self.mono += delta.total_seconds()


def make_http(
    tmp_path: Path, handler: Callable[[httpx.Request], httpx.Response], *, offline: bool = False
) -> tuple[CachedHttp, FakeTime]:
    clock = FakeTime()
    http = CachedHttp(
        cache_dir=tmp_path / "cache",
        config=HttpConfig(min_interval=1.0, max_retries=1, backoff=2.0),
        offline=offline,
        transport=httpx.MockTransport(handler),
        clock=lambda: clock.mono,
        sleep=clock.sleep,
        now=lambda: clock.wall,
    )
    return http, clock


URL = "https://example.test/icon.png"


def test_get_bytes_caches_binary_bodies(tmp_path: Path) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=TINY_PNG)

    http, clock = make_http(tmp_path, handler)
    first = http.get_bytes("ns", URL, max_age=timedelta(days=1))
    clock.advance(timedelta(hours=1))
    second = http.get_bytes("ns", URL, max_age=timedelta(days=1))
    assert (first.content, first.from_cache) == (TINY_PNG, False)
    assert (second.content, second.from_cache, second.stale) == (TINY_PNG, True, False)
    assert len(seen) == 1
    assert seen[0].headers["User-Agent"] == USER_AGENT
    assert seen[0].headers["Accept"].startswith("image/png")
    assert first.sha256 == second.sha256
    assert read_cached_bytes(tmp_path / "cache", "ns", URL) == TINY_PNG


def test_binary_and_text_entries_of_one_url_do_not_clash(tmp_path: Path) -> None:
    http, _ = make_http(tmp_path, lambda r: httpx.Response(200, content=b"\x89PNG\r\n\x1a\nbody"))
    text = http.get_text("ns", URL, max_age=timedelta(days=1))
    binary = http.get_bytes("ns", URL, max_age=timedelta(days=1))
    assert not text.from_cache and not binary.from_cache  # separate entries, separate requests
    assert http.get_text("ns", URL, max_age=timedelta(days=1)).from_cache
    assert http.get_bytes("ns", URL, max_age=timedelta(days=1)).content == b"\x89PNG\r\n\x1a\nbody"


def test_invalid_binary_response_never_replaces_the_cached_copy(tmp_path: Path) -> None:
    bodies = iter([TINY_PNG, b"<html>maintenance</html>"])
    http, _ = make_http(tmp_path, lambda r: httpx.Response(200, content=next(bodies)))
    http.get_bytes("ns", URL, max_age=timedelta(0), validate=validate_png)
    bad = http.get_bytes("ns", URL, max_age=timedelta(0), validate=validate_png)
    assert (bad.content, bad.stale) == (TINY_PNG, True)
    assert bad.stale_reason == "invalid response from example.test: not a PNG image"
    assert read_cached_bytes(tmp_path / "cache", "ns", URL) == TINY_PNG


def test_invalid_binary_response_without_cache_raises_and_caches_nothing(tmp_path: Path) -> None:
    http, _ = make_http(tmp_path, lambda r: httpx.Response(200, content=b"<html>"))
    with pytest.raises(FetchError, match="not a PNG image"):
        http.get_bytes("ns", URL, max_age=timedelta(0), validate=validate_png)
    assert not list((tmp_path / "cache").rglob("*.json"))


@pytest.mark.parametrize("damage", ["sha", "base64", "json"])
def test_damaged_binary_entry_is_a_cache_miss(tmp_path: Path, damage: str) -> None:
    http, _ = make_http(tmp_path, lambda r: httpx.Response(200, content=TINY_PNG))
    http.get_bytes("ns", URL, max_age=timedelta(days=1))
    entry = next((tmp_path / "cache" / "ns").glob("*.bin.json"))
    text = entry.read_text(encoding="utf-8")
    if damage == "sha":
        text = re.sub(r'"sha256": "[0-9a-f]+"', '"sha256": "' + "0" * 64 + '"', text)
    elif damage == "base64":
        text = re.sub(r'"body_b64": "[^"]+"', '"body_b64": "@@not base64@@"', text)
    else:
        text = "{broken"
    entry.write_text(text, encoding="utf-8")
    assert read_cached_bytes(tmp_path / "cache", "ns", URL) is None
    again = http.get_bytes("ns", URL, max_age=timedelta(days=1))
    assert (again.content, again.from_cache) == (TINY_PNG, False)


def test_binary_offline_mode_and_revalidation(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.headers.get("If-None-Match") == '"v1"':
            return httpx.Response(304)
        return httpx.Response(200, content=TINY_PNG, headers={"ETag": '"v1"'})

    http, clock = make_http(tmp_path, handler)
    http.get_bytes("ns", URL, max_age=timedelta(days=1))
    clock.advance(timedelta(days=2))
    revalidated = http.get_bytes("ns", URL, max_age=timedelta(days=1))
    assert (revalidated.content, revalidated.from_cache, revalidated.stale) == (TINY_PNG, True, False)
    assert http.requests_made == 2

    def forbidden(request: httpx.Request) -> httpx.Response:
        raise AssertionError("network used in offline mode")

    offline, _ = make_http(tmp_path, forbidden, offline=True)
    offline.now = lambda: clock.wall + timedelta(days=60)
    old = offline.get_bytes("ns", URL, max_age=timedelta(days=1))
    assert (old.content, old.stale) == (TINY_PNG, True)
    with pytest.raises(FetchError, match="offline and not cached"):
        offline.get_bytes("ns", URL + "?other", max_age=timedelta(days=1))


# ====================================================================== Stove set-icon assets


def icon_handler(
    calls: list[str], *, status: Callable[[str], int] | None = None, body: bytes = TINY_PNG
) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        calls.append(url)
        code = status(url) if status is not None else 200
        return httpx.Response(code, content=body if code == 200 else b"")

    return handler


def test_set_icon_url_only_accepts_set_codes() -> None:
    assert set_icon_url("set_cri_dmg") == SET_ICON_URL.format(code="set_cri_dmg")
    assert set_icon_url("set_cri_dmg").endswith("/wearingStatus/images/sets/set_cri_dmg.png")
    for bad in ("../secret", "set_cri\n", "SET_CRI", "set_1", ""):
        with pytest.raises(ValueError, match="not a set code"):
            set_icon_url(bad)


@pytest.mark.parametrize(
    ("data", "reason"),
    [
        (b"GIF89a....", "not a PNG image"),
        (b"\x89PNG\r\n\x1a\n\x00\x00", "truncated PNG image"),
        (b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\x0dIHDR" + (5000).to_bytes(4, "big") * 2, "unexpected PNG header"),
        (b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\x0dIDAT" + (10).to_bytes(4, "big") * 2, "unexpected PNG header"),
    ],
)
def test_validate_png_rejects_what_is_not_a_small_png(data: bytes, reason: str) -> None:
    with pytest.raises(ValueError, match=reason):
        validate_png(data)
    validate_png(TINY_PNG)


def test_fetch_set_icons_is_polite_and_cached(tmp_path: Path) -> None:
    calls: list[str] = []
    http, clock = make_http(tmp_path, icon_handler(calls))
    report = fetch_set_icons(http, ["set_speed", "set_cri", "set_speed"])
    assert (report.fetched, report.cached, dict(report.failed)) == (("set_cri", "set_speed"), (), {})
    assert calls == [set_icon_url("set_cri"), set_icon_url("set_speed")]  # one request per code, deduplicated
    assert clock.sleeps == [pytest.approx(1.0)]  # one second between two requests to the same host
    again = fetch_set_icons(http, ["set_cri", "set_speed"])
    assert (again.fetched, again.cached, again.available) == ((), ("set_cri", "set_speed"), ("set_cri", "set_speed"))
    assert len(calls) == 2
    fetch_set_icons(http, ["set_cri"], refresh=True)
    assert len(calls) == 3


def test_fetch_set_icons_reports_failures_without_urls(tmp_path: Path) -> None:
    calls: list[str] = []

    def status(url: str) -> int:
        return 404 if url.endswith("set_def.png") else 200

    http, _ = make_http(tmp_path, icon_handler(calls, status=status))
    report = fetch_set_icons(http, ["set_def", "set_res", "../evil"])
    assert report.available == ("set_res",)
    assert dict(report.failed) == {"../evil": "not a set code", "set_def": "HTTP 404"}
    assert all("http" not in reason for reason in report.failed.values())

    offline, _ = make_http(tmp_path, icon_handler(calls), offline=True)
    offline_report = fetch_set_icons(offline, ["set_res", "set_def"])
    assert offline_report.cached == ("set_res",)
    assert dict(offline_report.failed) == {"set_def": "offline and not cached"}


def test_fetch_set_icons_stops_contacting_a_dead_host(tmp_path: Path) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        raise httpx.ConnectError("down")

    http, _ = make_http(tmp_path, handler)
    report = fetch_set_icons(http, ["set_att", "set_cri", "set_def"])
    assert report.available == ()
    assert len(calls) == 2  # max_retries + 1 attempts for the whole run, then the host breaker
    assert report.failed["set_att"].startswith(f"{ICON_HOST} unreachable after 2 attempt(s)")
    assert report.failed["set_def"].startswith(f"{ICON_HOST} not contacted again this run")


def test_fetch_set_icons_keeps_a_stale_copy_and_says_why(tmp_path: Path) -> None:
    replies = iter([TINY_PNG, b"<html>"])
    http, clock = make_http(tmp_path, lambda r: httpx.Response(200, content=next(replies)))
    fetch_set_icons(http, ["set_cri"])
    clock.advance(timedelta(days=40))
    report = fetch_set_icons(http, ["set_cri"])
    assert report.cached == ("set_cri",)
    assert dict(report.stale) == {"set_cri": f"invalid response from {ICON_HOST}: not a PNG image"}


def test_load_set_icons_reads_the_cache_only(tmp_path: Path) -> None:
    calls: list[str] = []
    http, _ = make_http(tmp_path, icon_handler(calls))
    fetch_set_icons(http, ["set_cri", "set_def"])
    cache_dir = tmp_path / "cache"
    damaged = next(p for p in (cache_dir / SET_ICON_NAMESPACE).glob("*.bin.json"))
    damaged.write_text("{broken", encoding="utf-8")
    icons = load_set_icons(cache_dir, ["set_cri", "set_def", "set_speed", "not a code"])
    assert len(icons) == 1 and set(icons) <= {"set_cri", "set_def"}  # damaged and uncached ones are left out
    assert all(data == TINY_PNG for data in icons.values())
    assert len(calls) == 2


# ====================================================================== e7 catalog sync / e7 doctor


class IconCalls(list[str]):
    icons_status: int = 200


@pytest.fixture
def sync_calls(monkeypatch: pytest.MonkeyPatch) -> Iterator[IconCalls]:
    """The synthetic catalog sources plus a set-icon host whose answer the test controls."""
    seen = IconCalls()
    catalog = make_handler(calls=seen)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host != ICON_HOST:
            return catalog(request)
        seen.append(str(request.url))
        if seen.icons_status != 200:
            return httpx.Response(seen.icons_status)
        return httpx.Response(200, content=TINY_PNG, headers={"Content-Type": "image/png"})

    def factory(paths: AppPaths, offline: bool) -> CachedHttp:
        return CachedHttp(
            cache_dir=paths.cache_dir,
            config=HttpConfig(min_interval=0.0, max_retries=0),
            offline=offline,
            transport=httpx.MockTransport(handler),
            sleep=lambda s: None,
        )

    monkeypatch.setattr(catalog_cli, "http_factory", factory)
    yield seen


def _icon_calls(calls: list[str]) -> list[str]:
    return [url for url in calls if ICON_HOST in url]


def _set_icon_line(output: str) -> str:
    return next(line for line in output.splitlines() if line.startswith("Set icons:"))


def test_sync_fetches_set_icons_once(sync_calls: IconCalls) -> None:
    first = runner.invoke(app, ["catalog", "sync"])
    assert first.exit_code == 0, first.output
    count = len(_icon_calls(sync_calls))
    assert count > 0
    assert _set_icon_line(first.stdout) == f"Set icons: {count} cached ({count} new)"
    second = runner.invoke(app, ["catalog", "sync"])
    assert second.exit_code == 0, second.output
    assert _set_icon_line(second.stdout) == f"Set icons: {count} cached (0 new)"
    assert "0 network request(s)" in second.stdout
    assert len(_icon_calls(sync_calls)) == count

    offline = runner.invoke(app, ["catalog", "sync", "--offline"])
    assert offline.exit_code == 0, offline.output
    assert _set_icon_line(offline.stdout) == f"Set icons: {count} cached (0 new)"
    assert len(_icon_calls(sync_calls)) == count


def test_set_icon_failures_are_warnings_never_errors(sync_calls: IconCalls) -> None:
    sync_calls.icons_status = 404
    result = runner.invoke(app, ["catalog", "sync"])
    assert result.exit_code == 0, result.output
    line = _set_icon_line(result.stdout)
    assert line.startswith("Set icons: 0 cached (0 new), ") and "missing" in line
    warnings = [w for w in result.stdout.splitlines() if "warning: set icons missing (HTTP 404): " in w]
    assert len(warnings) == 1 and "set_cri_dmg" in warnings[0]  # one line per reason, not one per set
    assert ICON_HOST not in result.stdout


def test_offline_sync_without_cached_icons_says_so(sync_calls: IconCalls, isolated_home: AppPaths) -> None:
    assert runner.invoke(app, ["catalog", "sync", "--source", "stove"]).exit_code == 0
    for path in (isolated_home.cache_dir / SET_ICON_NAMESPACE).glob("*"):
        path.unlink()
    result = runner.invoke(app, ["catalog", "sync", "--source", "stove", "--offline"])
    assert result.exit_code == 0, result.output
    assert "warning: set icons missing (offline and not cached)" in result.stdout


def _doctor_line(output: str) -> str:
    return next(line for line in output.splitlines() if " set icons " in line)


def test_doctor_reports_set_icons(sync_calls: IconCalls, isolated_home: AppPaths) -> None:
    before = runner.invoke(app, ["doctor"])
    assert before.exit_code == 0, before.output
    assert _doctor_line(before.stdout) == "[WARN] set icons no catalog yet: run e7 catalog sync"

    assert runner.invoke(app, ["catalog", "sync"]).exit_code == 0
    total = len(_icon_calls(sync_calls))
    assert _doctor_line(runner.invoke(app, ["doctor"]).stdout) == f"[OK  ] set icons {total}/{total} cached"

    entry = next((isolated_home.cache_dir / SET_ICON_NAMESPACE).glob("*.bin.json"))
    entry.unlink()
    line = _doctor_line(runner.invoke(app, ["doctor"]).stdout)
    assert line.startswith(f"[WARN] set icons {total - 1}/{total} cached, missing set_") and "e7 catalog sync" in line


def test_doctor_survives_an_unreadable_catalog(isolated_home: AppPaths) -> None:
    isolated_home.home.mkdir(parents=True)
    isolated_home.database.write_bytes(b"not a database")
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0, result.output
    assert _doctor_line(result.stdout).startswith("[WARN] set icons cannot read the catalog")


# ====================================================================== badge reader: synthetic badges

GOLD = (60, 175, 220)
FILLS = {"red": (35, 30, 165), "blue": (165, 70, 35)}
ICON_SIZE = (113, 119)  # (width, height) of the real Stove icons


def shield_polygon(width: int, height: int) -> npt.NDArray[np.int32]:
    points = [(0.06, 0.04), (0.94, 0.04), (0.94, 0.58), (0.5, 0.96), (0.06, 0.58)]
    return np.array([(round(x * (width - 1)), round(y * (height - 1))) for x, y in points], np.int32)


def _glyph(canvas: Image, kind: str) -> None:
    w, h = canvas.shape[1], canvas.shape[0]
    cx, cy, r = w // 2, int(h * 0.42), int(w * 0.22)
    t = max(2, w // 14)
    if kind == "circle":
        cv2.circle(canvas, (cx, cy), r, GOLD, -1)
    elif kind == "ring":
        cv2.circle(canvas, (cx, cy), r, GOLD, t)
    elif kind == "cross":
        cv2.line(canvas, (cx - r, cy), (cx + r, cy), GOLD, t)
        cv2.line(canvas, (cx, cy - r), (cx, cy + r), GOLD, t)
    elif kind == "x":
        cv2.line(canvas, (cx - r, cy - r), (cx + r, cy + r), GOLD, t)
        cv2.line(canvas, (cx - r, cy + r), (cx + r, cy - r), GOLD, t)
    elif kind == "vbar":
        cv2.rectangle(canvas, (cx - t, cy - r), (cx + t, cy + r), GOLD, -1)
    elif kind == "bars":
        for k in (-1, 0, 1):
            cv2.rectangle(canvas, (cx - r, cy + k * 2 * t - t // 2), (cx + r, cy + k * 2 * t + t // 2), GOLD, -1)
    elif kind == "square":
        cv2.rectangle(canvas, (cx - r, cy - r), (cx + r, cy + r), GOLD, t)
    elif kind == "dots":
        for dx, dy in ((-1, -1), (1, -1), (-1, 1), (1, 1)):
            cv2.circle(canvas, (cx + dx * r // 2, cy + dy * r // 2), t, GOLD, -1)
    else:
        raise AssertionError(kind)


def synthetic_icon(glyph: str, fill: str, size: tuple[int, int] = ICON_SIZE) -> Image:
    """BGRA shield like the Stove icons: gold rim, gold glyph, red or blue fill, transparent outside."""
    width, height = size
    icon = np.zeros((height, width, 4), np.uint8)
    polygon = shield_polygon(width, height)
    colour = icon[..., :3].copy()
    cv2.fillPoly(colour, [polygon], FILLS.get(fill, (128, 128, 128)))
    cv2.polylines(colour, [polygon], True, GOLD, max(3, width // 12))
    _glyph(colour, glyph)
    icon[..., :3] = colour
    alpha = np.zeros((height, width), np.uint8)
    cv2.fillPoly(alpha, [polygon], 255)
    cv2.polylines(alpha, [polygon], True, 255, max(3, width // 12))
    icon[..., 3] = alpha
    return icon


SYNTHETIC_SETS: dict[str, tuple[str, str]] = {
    "set_aaa": ("circle", "red"),
    "set_bbb": ("cross", "red"),
    "set_ccc": ("vbar", "red"),
    "set_ddd": ("bars", "red"),
    "set_eee": ("ring", "blue"),
    "set_fff": ("x", "blue"),
    "set_ggg": ("square", "blue"),
    "set_hhh": ("dots", "blue"),
}


@cache
def synthetic_icons() -> dict[str, Image]:
    return {code: synthetic_icon(glyph, fill) for code, (glyph, fill) in SYNTHETIC_SETS.items()}


@cache
def synthetic_matcher(without: frozenset[str] = frozenset()) -> SetIconMatcher:
    return SetIconMatcher.from_png({c: png(icon) for c, icon in synthetic_icons().items() if c not in without})


def paste(scene: Image, icon: Image, x: int, y: int, height: int) -> Box:
    """Alpha-blend `icon` scaled to `height` with its top-left at (x, y); returns the pasted box."""
    width = round(icon.shape[1] * height / icon.shape[0])
    scaled = cv2.resize(icon, (width, height), interpolation=cv2.INTER_AREA)
    alpha = scaled[..., 3:4].astype(np.float32) / 255
    region = scene[y : y + height, x : x + width].astype(np.float32)
    scene[y : y + height, x : x + width] = (scaled[..., :3] * alpha + region * (1 - alpha)).astype(np.uint8)
    return Box(x, y, x + width, y + height)


def background(height: int, width: int, seed: int = 3) -> Image:
    rng = np.random.default_rng(seed)
    base = np.full((height, width, 3), (40, 25, 50), np.float32)
    return np.clip(base + rng.normal(0, 12, base.shape), 0, 255).astype(np.uint8)


def item_card(scene: Image, x: int, y: int, side: int, seed: int) -> Box:
    """A textured item artwork square with a dark frame (no badge yet); returns the item icon box."""
    rng = np.random.default_rng(seed)
    art = np.full((side, side, 3), (30, 30, 120), np.float32) + rng.normal(0, 25, (side, side, 3))
    cv2.circle(art, (side // 3, side // 3), side // 4, (200, 200, 220), -1)  # some "item" art
    scene[y : y + side, x : x + side] = np.clip(art, 0, 255).astype(np.uint8)
    cv2.rectangle(scene, (x - 3, y - 3), (x + side + 3, y + side + 3), (60, 60, 60), 3)
    return Box(x, y, x + side, y + side)


BADGE_PER_SIDE = 0.44  # measured badge height / item icon side on the user's captures
BADGE_OFFSET = (0.73, 0.63)  # measured badge top-left inside the item icon box, in sides


def piece_scene(code: str | None, badge_height: int, *, icon: Image | None = None) -> tuple[Image, Box, Box | None]:
    side = round(badge_height / BADGE_PER_SIDE)
    scene = background(3 * side, 3 * side)
    box = item_card(scene, side, side // 2, side, seed=badge_height)
    badge = None
    if code is not None or icon is not None:
        art = icon if icon is not None else synthetic_icons()[str(code)]
        x = round(box.x0 + BADGE_OFFSET[0] * side)
        y = round(box.y0 + BADGE_OFFSET[1] * side)
        badge = paste(scene, art, x, y, badge_height)
    return scene, box, badge


def overlap(a: Box, b: Box) -> float:
    ix = max(0.0, min(a.x1, b.x1) - max(a.x0, b.x0))
    iy = max(0.0, min(a.y1, b.y1) - max(a.y0, b.y0))
    union = (a.x1 - a.x0) * (a.y1 - a.y0) + (b.x1 - b.x0) * (b.y1 - b.y0) - ix * iy
    return ix * iy / union


@pytest.mark.parametrize("badge_height", [25, 46, 75])
@pytest.mark.parametrize("code", sorted(SYNTHETIC_SETS))
def test_piece_badge_is_read_at_three_sizes(code: str, badge_height: int) -> None:
    scene, box, badge = piece_scene(code, badge_height)
    match = synthetic_matcher().piece_set(scene, box)
    assert match.set_code == code, match
    assert match.fill == SYNTHETIC_SETS[code][1]
    assert match.box is not None and badge is not None and overlap(match.box, badge) > 0.7
    assert match.margin >= 0.12 and 0 < match.confidence <= 1


@pytest.mark.parametrize(("dx", "dy", "grow"), [(0.2, 0, 1), (-0.2, 0, 1), (0, 0.2, 1), (0, -0.2, 1), (0, 0, 1.3)])
def test_piece_badge_tolerates_an_imprecise_item_box(dx: float, dy: float, grow: float) -> None:
    scene, box, _ = piece_scene("set_fff", 46)
    side = box.x1 - box.x0
    cx, cy, half = (box.x0 + box.x1) / 2 + dx * side, (box.y0 + box.y1) / 2 + dy * side, grow * side / 2
    assert synthetic_matcher().piece_set(scene, Box(cx - half, cy - half, cx + half, cy + half)).set_code == "set_fff"


def test_no_badge_is_review_not_a_set() -> None:
    scene, box, _ = piece_scene(None, 46)
    match = synthetic_matcher().piece_set(scene, box)
    assert match == SetMatch(None, "", 0.0, "", 0.0, None)
    assert match.confidence == 0.0


def test_unknown_set_is_never_read_as_a_look_alike() -> None:
    """Negative control: the true set has no reference icon -> REVIEW (score or margin rule), never another set."""
    for code in SYNTHETIC_SETS:
        scene, box, _ = piece_scene(code, 46)
        match = synthetic_matcher(frozenset({code})).piece_set(scene, box)
        assert match.set_code is None, (code, match)


def test_colour_gate_rejects_a_badge_with_the_wrong_fill() -> None:
    """A red-set glyph on a blue fill: the grey glyph matches, the fill does not -> REVIEW."""
    recoloured = synthetic_icon("vbar", "blue")
    scene, box, _ = piece_scene(None, 46, icon=recoloured)
    match = synthetic_matcher().piece_set(scene, box)
    assert (match.best, match.fill, match.set_code) == ("set_ccc", "blue", None)
    assert match.second != ""  # the overall runner-up is reported when the gate fails


def test_badge_without_fill_colour_is_review() -> None:
    grey = synthetic_icon("cross", "grey")
    scene, box, _ = piece_scene(None, 46, icon=grey)
    match = synthetic_matcher().piece_set(scene, box)
    assert (match.fill, match.set_code) == ("unknown", None)


def test_same_fill_look_alikes_go_to_review() -> None:
    """Two references of the same fill whose glyphs differ by a few pixels: the margin rule abstains."""
    icons = {code: png(icon) for code, icon in synthetic_icons().items()}
    twin = synthetic_icon("circle", "red")
    cv2.circle(twin, (ICON_SIZE[0] // 2, int(ICON_SIZE[1] * 0.42)), 2, (35, 30, 165), -1)
    icons["set_twin"] = png(twin)
    matcher = SetIconMatcher.from_png(icons)
    scene, box, _ = piece_scene("set_aaa", 46)
    match = matcher.piece_set(scene, box)
    assert match.set_code is None and {match.best, match.second} == {"set_aaa", "set_twin"}
    assert match.margin < 0.12


def test_same_fill_margin_ignores_the_other_fill() -> None:
    """Identical glyph in both fills: the colour gate decides, the margin is taken among same-fill sets only."""
    icons = {code: png(icon) for code, icon in synthetic_icons().items()}
    icons["set_bluecircle"] = png(synthetic_icon("circle", "blue"))
    matcher = SetIconMatcher.from_png(icons)
    scene, box, _ = piece_scene("set_aaa", 46)
    match = matcher.piece_set(scene, box)
    assert match.set_code in ("set_aaa", None)  # never the blue twin
    if match.set_code is not None:
        assert matcher.reference_fill(match.second) == "red"


def cp_scene(codes: list[str], cp_height: int) -> tuple[Image, Box]:
    """A CP number (white digits) with the active-set icons right of it, as on Hero Info."""
    h = cp_height
    scene = background(4 * h, 12 * h, seed=h)
    origin = (h, 2 * h)
    scale = h / 30
    (text_w, _), _ = cv2.getTextSize("124,427", cv2.FONT_HERSHEY_SIMPLEX, scale, 2)
    cv2.putText(scene, "124,427", origin, cv2.FONT_HERSHEY_SIMPLEX, scale, (235, 235, 235), 2, cv2.LINE_AA)
    cp = Box(origin[0], origin[1] - h * 0.85, origin[0] + text_w, origin[1] + h * 0.15)
    icons = synthetic_icons()
    for k, code in enumerate(codes):
        paste(scene, icons[code], round(cp.x1 + (1.6 + 0.85 * k) * h), round(cp.y0 + 0.15 * h), round(0.7 * h))
    return scene, cp


@pytest.mark.parametrize("cp_height", [36, 56, 72])
@pytest.mark.parametrize("codes", [["set_eee", "set_aaa"], ["set_hhh", "set_bbb", "set_fff"], ["set_ddd"]])
def test_active_sets_in_screen_order(codes: list[str], cp_height: int) -> None:
    scene, cp = cp_scene(codes, cp_height)
    found = synthetic_matcher().active_sets(scene, cp)
    assert [m.set_code for m in found] == codes
    assert [m.box.x0 for m in found if m.box] == sorted(m.box.x0 for m in found if m.box)


@pytest.mark.parametrize("cp_height", [36, 56, 72])
def test_no_active_set_icon_gives_an_empty_list(cp_height: int) -> None:
    scene, cp = cp_scene([], cp_height)
    assert synthetic_matcher().active_sets(scene, cp) == []


def test_at_most_three_active_sets_are_read() -> None:
    scene, cp = cp_scene(["set_aaa", "set_bbb", "set_ccc", "set_ddd"], 56)
    assert len(synthetic_matcher().active_sets(scene, cp)) == ACTIVE_MAX


def test_regions_outside_the_image_find_nothing() -> None:
    scene = background(100, 100)
    matcher = synthetic_matcher()
    assert matcher.piece_set(scene, Box(500, 500, 600, 600)).box is None
    assert matcher.active_sets(scene, Box(900, 10, 990, 40)) == []


def test_matcher_needs_two_png_icons_with_transparency() -> None:
    icon = synthetic_icon("circle", "red")
    with pytest.raises(SetIconError, match="at least two"):
        SetIconMatcher.from_png({"set_aaa": png(icon)})
    with pytest.raises(SetIconError, match="set_bbb: not a PNG with an alpha channel"):
        SetIconMatcher.from_png({"set_aaa": png(icon), "set_bbb": png(icon[..., :3].copy())})
    with pytest.raises(SetIconError, match="set_bbb: not a PNG"):
        SetIconMatcher.from_png({"set_aaa": png(icon), "set_bbb": b"<html>"})
    with pytest.raises(SetIconError, match="fully transparent"):
        SetIconMatcher.from_png({"set_aaa": png(icon), "set_bbb": png(np.zeros((9, 9, 4), np.uint8))})
    matcher = synthetic_matcher()
    assert matcher.codes == frozenset(SYNTHETIC_SETS)
    assert {code: matcher.reference_fill(code) for code in SYNTHETIC_SETS} == {
        code: fill for code, (_, fill) in SYNTHETIC_SETS.items()
    }


# ====================================================================== golden: the user's Hero Info captures

ICON_DIR_ENV = "E7AC_TEST_SET_ICONS"
"""Folder with the Stove set icons (<code>.png); default fixtures/stove/set_icons (git-ignored game assets)."""
SLOTS = ("weapon", "helmet", "armor", "necklace", "ring", "boots")
S = "set_speed"
# Set per piece in slot order, read by eye from the captures and cross-checked against the active-set icons and the
# Equipment tab's set names (equip_haru: "Health Set", "Destruction Set"; equip_straze: "Rage Set", "Critical Set").
GOLDEN_SETS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "heroinfo_haru": (("set_cri_dmg",) * 4 + ("set_max_hp",) * 2, ("set_max_hp", "set_cri_dmg")),
    "heroinfo_lots": ((S, S, "set_immune", S, "set_immune", S), ("set_immune", S)),
    "heroinfo_ainz": ((S, S, "set_acc", S, S, "set_acc"), ("set_acc", S)),
    "heroinfo_straze": (("set_rage",) * 3 + ("set_cri", "set_cri", "set_rage"), ("set_rage", "set_cri")),
    "heroinfo_politis": ((S, "set_chase", S, S, S, "set_chase"), (S, "set_chase")),
    "heroinfo_charles": ((), ()),  # no gear: no badge, no active-set icon
}
SCALES = (0.64, 1.0, 1.28)  # 1.28 x the 2000-px copies = the user's 2560-px window


def icon_dir() -> Path:
    return Path(os.environ.get(ICON_DIR_ENV) or FIXTURES_DIR / "stove" / "set_icons")


def read_bgra(path: Path) -> Image:
    return np.asarray(cv2.imdecode(np.fromfile(path, np.uint8), cv2.IMREAD_UNCHANGED), dtype=np.uint8)


def like_a_capture(scene: Image) -> Image:
    """Mild blur and a lossy WebP round trip, like the game's rendering plus a saved capture."""
    blurred = cv2.GaussianBlur(scene, (0, 0), 0.6)
    encoded = cv2.imencode(".webp", blurred, [cv2.IMWRITE_WEBP_QUALITY, 75])[1]
    return np.asarray(cv2.imdecode(encoded, cv2.IMREAD_COLOR), dtype=np.uint8)


def icon_paths() -> list[Path]:
    return sorted(icon_dir().glob("set_*.png"))


@cache
def stove_matcher() -> SetIconMatcher:
    icons = {path.stem: path.read_bytes() for path in icon_paths()}
    if len(icons) < 2:
        pytest.skip(f"Stove set icons missing in {icon_dir()} (game assets, never committed)")
    return SetIconMatcher.from_png(icons)


@cache
def capture(name: str, scale: float) -> tuple[Image, tuple[TextLine, ...]]:
    """The capture at `scale` and its OCR lines (OCR runs once per capture and scale for the whole module)."""
    from e7ac.vision.image import load_image
    from e7ac.vision.ocr import RapidOcrReader

    image = load_image(FIXTURES_DIR / "screenshots" / f"{name}.webp")
    if scale != 1.0:
        interpolation = cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC
        image = cv2.resize(image, None, fx=scale, fy=scale, interpolation=interpolation)
    return image, tuple(RapidOcrReader().read(image))


_VALUE = re.compile(r"^\D{0,2}\d{1,3}(?:,\d{3})*%?$")
_SCORE = re.compile(r"^\d{1,3}$")
ICON_SIDE_PER_PITCH = 0.53  # item artwork side / vertical piece pitch (105 / 199 px on the 2000-px copies)
ICON_GAP = 0.12  # gap between the artwork's bottom and the score text, in sides


def item_icon_boxes(lines: tuple[TextLine, ...]) -> dict[str, Box]:
    """Stand-in for the gear-panel reader (written separately): the item artwork boxes found from the OCR'd piece
    scores, which are centred under each artwork; slots from the 2 x 3 grid (left column weapon/helmet/armor)."""
    anchor = next(line for line in lines if "equipment score" in line.text.lower())
    h = anchor.box.height
    below = [line for line in lines if line.box.y0 > anchor.box.y1 and line.box.x0 > anchor.box.x0 - 2 * h]
    columns = _value_column_rights(below, h)
    scores = [ln for ln in below if _SCORE.match(ln.text.strip()) and all(abs(ln.box.x1 - c) > h for c in columns)]
    grid: list[list[TextLine]] = []
    for index, right in enumerate(columns):
        left = columns[index - 1] if index else -1.0
        grid.append(_score_column([ln for ln in scores if left < ln.box.x1 < right], h))
    pitch = float(np.mean([b.box.y0 - a.box.y0 for column in grid for a, b in pairwise(column)]))
    side = ICON_SIDE_PER_PITCH * pitch
    boxes = {}
    for slot, line in zip(SLOTS, grid[0] + grid[1], strict=True):
        cx, bottom = (line.box.x0 + line.box.x1) / 2, line.box.y0 - ICON_GAP * side
        boxes[slot] = Box(cx - side / 2, bottom - side, cx + side / 2, bottom)
    return boxes


def _value_column_rights(lines: list[TextLine], h: float) -> list[float]:
    """The two right-aligned stat value columns (many value lines share a right edge)."""
    rights = sorted(line.box.x1 for line in lines if _VALUE.match(line.text.strip()))
    clusters: list[list[float]] = []
    for x in rights:
        if clusters and x - clusters[-1][-1] < 0.35 * h:
            clusters[-1].append(x)
        else:
            clusters.append([x])
    columns = sorted(float(np.median(c)) for c in clusters if len(c) >= 6)
    assert len(columns) == 2, columns
    return columns


def _score_column(candidates: list[TextLine], h: float) -> list[TextLine]:
    """The three piece scores of one column: integers sharing a centre x, below the item levels (same x group)."""
    groups: list[list[TextLine]] = []
    for line in sorted(candidates, key=lambda ln: ln.box.x0 + ln.box.x1):
        centre = (line.box.x0 + line.box.x1) / 2
        if groups and centre - (groups[-1][-1].box.x0 + groups[-1][-1].box.x1) / 2 < 0.4 * h:
            groups[-1].append(line)
        else:
            groups.append([line])
    threes = [sorted(g, key=lambda ln: ln.box.y0) for g in groups if len(g) == 3]
    assert threes, [line.text for line in candidates]
    return max(threes, key=lambda g: float(np.mean([ln.box.y0 for ln in g])))


def _golden_params(geared: bool) -> list[Any]:
    names = [n for n, (pieces, _) in GOLDEN_SETS.items() if bool(pieces) == geared]
    return [
        pytest.param(name, scale, marks=pytest.mark.fixtures(f"screenshots/{name}.webp"), id=f"{name}-{scale}")
        for name in names
        for scale in SCALES
    ]


@pytest.mark.parametrize(("name", "scale"), _golden_params(geared=True))
def test_golden_piece_sets(name: str, scale: float) -> None:
    matcher = stove_matcher()
    image, lines = capture(name, scale)
    boxes = item_icon_boxes(lines)
    read = {slot: matcher.piece_set(image, boxes[slot]) for slot in SLOTS}
    assert tuple(read[slot].set_code for slot in SLOTS) == GOLDEN_SETS[name][0], read
    assert all(m.score >= 0.80 and m.margin >= 0.20 for m in read.values()), read  # measured >= 0.83 / >= 0.24


@pytest.mark.parametrize(("name", "scale"), _golden_params(geared=True) + _golden_params(geared=False))
def test_golden_active_sets(name: str, scale: float) -> None:
    from e7ac.vision.hero_screen import parse_hero_screen

    matcher = stove_matcher()
    image, lines = capture(name, scale)
    cp = parse_hero_screen(list(lines)).anchors.cp
    assert cp is not None
    found = matcher.active_sets(image, cp)
    assert tuple(m.set_code for m in found) == GOLDEN_SETS[name][1], found
    pieces = set(GOLDEN_SETS[name][0])
    assert {m.set_code for m in found} <= pieces  # an active set needs pieces of that set (MECH-GEAR-05)


@pytest.mark.parametrize("scale", SCALES)
@pytest.mark.fixtures("screenshots/heroinfo_charles.webp", "screenshots/heroinfo_haru.webp")
def test_golden_empty_gear_slots_are_never_a_set(scale: float) -> None:
    """Negative control: Charles wears no gear; the slot boxes of Haru's capture (same layout, same width) land on
    or near Charles's empty slot silhouettes."""
    matcher = stove_matcher()
    image, _ = capture("heroinfo_charles", scale)
    boxes = item_icon_boxes(capture("heroinfo_haru", scale)[1])
    for slot in SLOTS:
        assert matcher.piece_set(image, boxes[slot]).set_code is None, slot


@pytest.mark.fixtures(*(f"screenshots/{name}.webp" for name, (pieces, _) in GOLDEN_SETS.items() if pieces))
def test_golden_every_stove_icon_painted_over_real_badges() -> None:
    """The 15 sets never seen in the captures: each Stove icon painted over each real weapon badge (scale 1.0, mildly
    blurred and re-encoded like a capture) must be read as itself."""
    matcher = stove_matcher()
    icons = {path.stem: read_bgra(path) for path in icon_paths()}
    wrong: list[str] = []
    for name, (pieces, _) in GOLDEN_SETS.items():
        if not pieces:
            continue
        image, lines = capture(name, 1.0)
        box = item_icon_boxes(lines)["weapon"]
        badge = matcher.piece_set(image, box).box
        assert badge is not None
        for code, icon in icons.items():
            scene = image.copy()
            paste(scene, icon, round(badge.x0), round(badge.y0), round(badge.y1 - badge.y0))
            match = matcher.piece_set(like_a_capture(scene), box)
            if match.set_code != code:
                wrong.append(f"{name} {code}: {match}")
    assert wrong == []
