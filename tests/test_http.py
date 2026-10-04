from __future__ import annotations

import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from e7ac.sources.http import USER_AGENT, CachedHttp, FetchError, HttpConfig

URL = "https://example.test/data.json"


class FakeTime:
    def __init__(self) -> None:
        self.mono = 1000.0
        self.wall = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)
        self.sleeps: list[float] = []

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.mono += seconds

    def advance(self, delta: timedelta) -> None:
        self.wall += delta
        self.mono += delta.total_seconds()


def make(
    tmp_path: Path, handler: Callable[[httpx.Request], httpx.Response], *, offline: bool = False
) -> tuple[CachedHttp, FakeTime]:
    clock = FakeTime()
    http = CachedHttp(
        cache_dir=tmp_path / "cache",
        config=HttpConfig(min_interval=1.0, max_retries=2, backoff=2.0),
        offline=offline,
        transport=httpx.MockTransport(handler),
        clock=lambda: clock.mono,
        sleep=clock.sleep,
        now=lambda: clock.wall,
    )
    return http, clock


def test_fresh_cache_hit_makes_no_request(tmp_path: Path) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, text="v1")

    http, clock = make(tmp_path, handler)
    first = http.get_text("ns", URL, {"b": "2", "a": "1"}, max_age=timedelta(hours=1))
    clock.advance(timedelta(minutes=30))
    second = http.get_text("ns", URL, {"a": "1", "b": "2"}, max_age=timedelta(hours=1))
    assert (first.text, first.from_cache) == ("v1", False)
    assert (second.text, second.from_cache, second.stale) == ("v1", True, False)
    assert len(seen) == 1
    assert seen[0].headers["User-Agent"] == USER_AGENT
    assert http.requests_made == 1


def test_expired_cache_revalidates_with_etag(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.headers.get("If-None-Match") == '"v1"':
            return httpx.Response(304)
        return httpx.Response(200, text="body", headers={"ETag": '"v1"'})

    http, clock = make(tmp_path, handler)
    http.get_text("ns", URL, max_age=timedelta(hours=1))
    clock.advance(timedelta(hours=2))
    again = http.get_text("ns", URL, max_age=timedelta(hours=1))
    assert (again.text, again.stale) == ("body", False)
    assert again.fetched_at == clock.wall
    assert http.requests_made == 2
    clock.advance(timedelta(minutes=10))
    assert http.get_text("ns", URL, max_age=timedelta(hours=1)).from_cache  # refreshed timestamp counts
    assert http.requests_made == 2  # ...so no third request


def test_refresh_bypasses_fresh_cache(tmp_path: Path) -> None:
    bodies = iter(["v1", "v2"])
    http, _ = make(tmp_path, lambda r: httpx.Response(200, text=next(bodies)))
    http.get_text("ns", URL, max_age=timedelta(days=1))
    assert http.get_text("ns", URL, max_age=timedelta(days=1), refresh=True).text == "v2"


def test_throttles_requests_to_the_same_host(tmp_path: Path) -> None:
    http, clock = make(tmp_path, lambda r: httpx.Response(200, text="x"))
    http.get_text("ns", URL + "?1", max_age=timedelta(0))
    http.get_text("ns", URL + "?2", max_age=timedelta(0))
    assert clock.sleeps and clock.sleeps[-1] == pytest.approx(1.0)


def test_retries_transient_errors_with_backoff(tmp_path: Path) -> None:
    statuses = iter([503, 502, 200])
    http, clock = make(tmp_path, lambda r: httpx.Response(next(statuses), text="ok"))
    assert http.get_text("ns", URL, max_age=timedelta(0)).text == "ok"
    assert 2.0 in clock.sleeps and 4.0 in clock.sleeps


def test_client_errors_are_not_retried(tmp_path: Path) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(404)

    http, _ = make(tmp_path, handler)
    with pytest.raises(FetchError, match="HTTP 404"):
        http.get_text("ns", URL, max_age=timedelta(0))
    assert len(calls) == 1


def test_network_failure_serves_stale_cache(tmp_path: Path) -> None:
    responses: list[httpx.Response | Exception] = [httpx.Response(200, text="cached")]

    def handler(request: httpx.Request) -> httpx.Response:
        item = responses.pop(0) if responses else httpx.ConnectError("offline")
        if isinstance(item, Exception):
            raise item
        return item

    http, clock = make(tmp_path, handler)
    http.get_text("ns", URL, max_age=timedelta(hours=1))
    clock.advance(timedelta(days=2))
    result = http.get_text("ns", URL, max_age=timedelta(hours=1))
    assert (result.text, result.from_cache, result.stale) == ("cached", True, True)
    assert result.stale_reason == "example.test unreachable after 3 attempt(s) (ConnectError: offline)"


def test_network_failure_without_cache_raises(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline")

    http, _ = make(tmp_path, handler)
    with pytest.raises(FetchError, match="giving up"):
        http.get_text("ns", URL, max_age=timedelta(hours=1))


def test_offline_mode_never_touches_the_network(tmp_path: Path) -> None:
    http, clock = make(tmp_path, lambda r: httpx.Response(200, text="v1"))
    http.get_text("ns", URL, max_age=timedelta(hours=1))
    clock.advance(timedelta(days=30))

    def forbidden(request: httpx.Request) -> httpx.Response:
        raise AssertionError("network used in offline mode")

    offline, _ = make(tmp_path, forbidden, offline=True)
    offline.now = lambda: clock.wall
    result = offline.get_text("ns", URL, max_age=timedelta(hours=1))
    assert (result.text, result.stale) == ("v1", True)
    assert result.stale_reason is not None and result.stale_reason.startswith("offline mode")
    with pytest.raises(FetchError, match="offline and not cached"):
        offline.get_text("ns", URL + "?other", max_age=timedelta(hours=1))


def test_corrupt_cache_entry_is_a_miss(tmp_path: Path) -> None:
    http, _ = make(tmp_path, lambda r: httpx.Response(200, text="fresh"))
    http.get_text("ns", URL, max_age=timedelta(hours=1))
    entry = next((tmp_path / "cache" / "ns").glob("*.json"))
    entry.write_text("{broken", encoding="utf-8")
    assert http.get_text("ns", URL, max_age=timedelta(hours=1)).from_cache is False


def test_http_error_with_cache_serves_stale_copy_with_its_reason(tmp_path: Path) -> None:
    statuses = iter([200, 404])
    http, clock = make(tmp_path, lambda r: httpx.Response(next(statuses), text="v1"))
    http.get_text("ns", URL, max_age=timedelta(hours=1))
    clock.advance(timedelta(hours=2))
    result = http.get_text("ns", URL, max_age=timedelta(hours=1))
    assert (result.text, result.stale, result.stale_reason) == ("v1", True, "HTTP 404")


def _must_be_json(text: str) -> None:
    if not text.startswith("{"):
        raise ValueError("not a JSON object")


def test_invalid_response_never_overwrites_the_cache(tmp_path: Path) -> None:
    bodies = iter(['{"good": 1}', "<html>maintenance</html>", '{"good": 2}'])
    http, _ = make(tmp_path, lambda r: httpx.Response(200, text=next(bodies)))
    http.get_text("ns", URL, max_age=timedelta(0), validate=_must_be_json)
    bad = http.get_text("ns", URL, max_age=timedelta(0), validate=_must_be_json)
    assert (bad.text, bad.stale) == ('{"good": 1}', True)
    assert bad.stale_reason == "invalid response from example.test: not a JSON object"
    entry = next((tmp_path / "cache" / "ns").glob("*.json"))
    assert "maintenance" not in entry.read_text(encoding="utf-8")
    assert http.get_text("ns", URL, max_age=timedelta(0), validate=_must_be_json).text == '{"good": 2}'


def test_invalid_response_without_cache_raises(tmp_path: Path) -> None:
    http, _ = make(tmp_path, lambda r: httpx.Response(200, text="<html>"))
    with pytest.raises(FetchError, match=re.escape(f"invalid response from {URL}")):
        http.get_text("ns", URL, max_age=timedelta(0), validate=_must_be_json)
    assert not list((tmp_path / "cache").rglob("*.json"))


def test_dead_host_is_not_contacted_again_in_the_same_run(tmp_path: Path) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if request.url.host == "example.test":
            raise httpx.ConnectError("down")
        return httpx.Response(200, text="other host is fine")

    http, _ = make(tmp_path, handler)
    with pytest.raises(FetchError, match="giving up"):
        http.get_text("ns", URL + "?page=1", max_age=timedelta(0))
    with pytest.raises(FetchError, match="not contacted again this run"):
        http.get_text("ns", URL + "?page=2", max_age=timedelta(0))
    assert len(calls) == 3  # max_retries + 1 attempts in total for the whole run, not per URL
    assert http.get_text("ns", "https://other.test/x", max_age=timedelta(0)).text == "other host is fine"


def test_rate_limit_stops_contacting_the_host(tmp_path: Path) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(429)

    http, _ = make(tmp_path, handler)
    with pytest.raises(FetchError, match="429"):
        http.get_text("ns", URL + "?page=1", max_age=timedelta(0))
    with pytest.raises(FetchError, match=r"not contacted again this run \(HTTP 429 rate limited\)"):
        http.get_text("ns", URL + "?page=2", max_age=timedelta(0))
    assert len(calls) == 1  # 429 is never retried
