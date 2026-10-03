"""Polite HTTP client with an on-disk cache (docs/DATA_SOURCES.md "Access policy").

- Honest User-Agent, at most one request per `min_interval` seconds per host, retries with exponential back-off.
- Every response body is cached on disk with its fetch time; a fresh cache hit makes no request.
- Conditional requests (ETag / Last-Modified) avoid re-downloading unchanged files.
- Offline mode serves the cache only; if the network fails, a stale cached copy is returned and flagged.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final
from urllib.parse import urlsplit

import httpx

from e7ac import __version__

USER_AGENT: Final = f"OrbisCodex/{__version__} (personal, non-commercial; +https://github.com/NightmareFTW/Orbis-Codex)"
_RETRY_STATUS: Final = frozenset({500, 502, 503, 504})


class FetchError(Exception):
    """The resource could not be fetched and no cached copy exists.

    `reason` is the URL-free cause (e.g. "HTTP 404"), used as the stale reason so one outage gives one warning."""

    def __init__(self, message: str, reason: str | None = None) -> None:
        super().__init__(message)
        self.reason = reason or message


@dataclass(frozen=True, slots=True)
class FetchResult:
    url: str
    text: str
    fetched_at: datetime
    from_cache: bool
    stale: bool
    """True when an expired (or unvalidated-replacement) cached copy was served instead of fresh data."""
    stale_reason: str | None = None
    """Why the stale copy was served: offline mode, network error, HTTP status, invalid response..."""

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()

    def json(self) -> Any:
        return json.loads(self.text)


@dataclass(slots=True)
class HttpConfig:
    min_interval: float = 1.0
    timeout: float = 30.0
    max_retries: int = 3
    backoff: float = 2.0
    user_agent: str = USER_AGENT


@dataclass(slots=True)
class CachedHttp:
    """Fetch text resources through a disk cache. One instance per sync run."""

    cache_dir: Path
    config: HttpConfig = field(default_factory=HttpConfig)
    offline: bool = False
    transport: httpx.BaseTransport | None = None
    clock: Callable[[], float] = time.monotonic
    sleep: Callable[[float], None] = time.sleep
    now: Callable[[], datetime] = lambda: datetime.now(UTC)
    requests_made: int = 0
    _last_request: dict[str, float] = field(default_factory=dict)
    _failed_hosts: dict[str, str] = field(default_factory=dict)
    """Hosts that failed (retries exhausted or rate-limited) during this run: not contacted again."""
    _client: httpx.Client | None = None

    def get_text(
        self,
        namespace: str,
        url: str,
        params: Mapping[str, str] | None = None,
        *,
        max_age: timedelta,
        refresh: bool = False,
        validate: Callable[[str], None] | None = None,
    ) -> FetchResult:
        """Return the resource text, from cache when younger than `max_age` (unless `refresh`).

        `validate` (raises ValueError) is applied to fresh responses *before* they are cached, so an error page
        served with HTTP 200 never replaces the last good copy."""
        full_url = _full_url(url, params)
        entry_path = self._entry_path(namespace, full_url)
        cached = _read_entry(entry_path)

        def cached_result(stale: bool, reason: str | None = None) -> FetchResult:
            assert cached is not None
            fetched = datetime.fromisoformat(cached["fetched_at"])
            return FetchResult(full_url, cached["text"], fetched, from_cache=True, stale=stale, stale_reason=reason)

        if cached is not None and not refresh and self.now() - datetime.fromisoformat(cached["fetched_at"]) < max_age:
            return cached_result(stale=False)
        if self.offline:
            if cached is None:
                raise FetchError(f"offline and not cached: {full_url}")
            return cached_result(stale=True, reason="offline mode: cached copy older than its refresh interval")
        host = urlsplit(full_url).netloc
        if host in self._failed_hosts:
            reason = f"{host} not contacted again this run ({self._failed_hosts[host]})"
            if cached is None:
                raise FetchError(f"{reason}: {full_url}", reason)
            return cached_result(stale=True, reason=reason)

        try:
            response = self._request(full_url, cached)
        except FetchError as exc:
            if cached is None:
                raise
            return cached_result(stale=True, reason=exc.reason)

        now = self.now()
        if response.status_code == 304 and cached is not None:
            cached["fetched_at"] = now.isoformat()
            _write_entry(entry_path, cached)
            return FetchResult(full_url, cached["text"], now, from_cache=True, stale=False)

        text = response.text
        if validate is not None:
            try:
                validate(text)
            except ValueError as exc:
                reason = f"invalid response from {host}: {exc}"
                if cached is None:
                    raise FetchError(f"invalid response from {full_url}: {exc}", reason) from exc
                return cached_result(stale=True, reason=reason)
        _write_entry(
            entry_path,
            {
                "url": full_url,
                "fetched_at": now.isoformat(),
                "etag": response.headers.get("etag"),
                "last_modified": response.headers.get("last-modified"),
                "text": text,
            },
        )
        return FetchResult(full_url, text, now, from_cache=False, stale=False)

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def __enter__(self) -> CachedHttp:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    # ------------------------------------------------------------------ internals

    def _request(self, full_url: str, cached: dict[str, Any] | None) -> httpx.Response:
        headers = {"User-Agent": self.config.user_agent, "Accept": "application/json, text/plain, */*"}
        if cached is not None:
            if cached.get("etag"):
                headers["If-None-Match"] = cached["etag"]
            if cached.get("last_modified"):
                headers["If-Modified-Since"] = cached["last_modified"]
        host = urlsplit(full_url).netloc
        last_error = ""
        for attempt in range(self.config.max_retries + 1):
            if attempt:
                self.sleep(self.config.backoff**attempt)
            self._throttle(host)
            try:
                response = self._http().get(full_url, headers=headers)
            except httpx.HTTPError as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                continue
            if response.status_code == 429:
                # rate limited: stop talking to this host for the rest of the run (polite scraping)
                self._failed_hosts[host] = "HTTP 429 rate limited"
                raise FetchError(f"HTTP 429 (rate limited) for {full_url}", f"HTTP 429 (rate limited) from {host}")
            if response.status_code in _RETRY_STATUS:
                last_error = f"HTTP {response.status_code}"
                continue
            if response.status_code == 304 or response.is_success:
                return response
            raise FetchError(f"HTTP {response.status_code} for {full_url}", f"HTTP {response.status_code}")
        self._failed_hosts[host] = last_error or "unreachable"
        attempts = self.config.max_retries + 1
        raise FetchError(
            f"giving up on {full_url} after {attempts} attempt(s) ({last_error})",
            f"{host} unreachable after {attempts} attempt(s) ({last_error})",
        )

    def _throttle(self, host: str) -> None:
        last = self._last_request.get(host)
        if last is not None:
            wait = self.config.min_interval - (self.clock() - last)
            if wait > 0:
                self.sleep(wait)
        self._last_request[host] = self.clock()
        self.requests_made += 1

    def _http(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=self.config.timeout, transport=self.transport, follow_redirects=True)
        return self._client

    def _entry_path(self, namespace: str, full_url: str) -> Path:
        key = hashlib.sha256(full_url.encode("utf-8")).hexdigest()
        return self.cache_dir / namespace / f"{key}.json"


def _full_url(url: str, params: Mapping[str, str] | None) -> str:
    """URL with its own query merged with `params`, sorted so the cache key does not depend on order."""
    base = httpx.URL(url)
    merged = dict(base.params.multi_items())
    merged.update(params or {})
    if not merged:
        return str(base)
    return str(base.copy_with(params=sorted(merged.items())))


def _read_entry(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None  # missing or corrupt cache entry: treated as a cache miss (the source is re-fetched)
    if not isinstance(data, dict) or not isinstance(data.get("text"), str) or "fetched_at" not in data:
        return None
    return data


def _write_entry(path: Path, entry: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(entry, handle, ensure_ascii=False)
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)
