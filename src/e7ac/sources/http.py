"""Polite HTTP client with an on-disk cache (docs/DATA_SOURCES.md "Access policy").

- Honest User-Agent, at most one request per `min_interval` seconds per host, retries with exponential back-off.
- Every response body is cached on disk with its fetch time; a fresh cache hit makes no request. Text bodies
  (`get_text`) and binary bodies (`get_bytes`, e.g. icons) use separate entry files, each written atomically.
- Conditional requests (ETag / Last-Modified) avoid re-downloading unchanged files.
- Offline mode serves the cache only; if the network fails, a stale cached copy is returned and flagged.
"""

from __future__ import annotations

import base64
import binascii
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
_ACCEPT_TEXT: Final = "application/json, text/plain, */*"
_ACCEPT_BINARY: Final = "image/png, image/*;q=0.8, */*;q=0.5"
_TEXT_SUFFIX: Final = ".json"
_BINARY_SUFFIX: Final = ".bin.json"
"""Binary entries: JSON metadata with the body in base64 and its SHA-256 (checked on every read)."""


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


@dataclass(frozen=True, slots=True)
class BytesResult:
    """A binary resource (`CachedHttp.get_bytes`); same freshness flags as `FetchResult`."""

    url: str
    content: bytes
    fetched_at: datetime
    from_cache: bool
    stale: bool
    stale_reason: str | None = None

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.content).hexdigest()


@dataclass(frozen=True, slots=True)
class _Codec[T]:
    """How one kind of body is read from a response and stored in / read back from a cache entry."""

    suffix: str
    accept: str
    from_response: Callable[[httpx.Response], T]
    to_entry: Callable[[T], dict[str, Any]]
    from_entry: Callable[[dict[str, Any]], T | None]
    """None = the entry is corrupt (a cache miss)."""


@dataclass(frozen=True, slots=True)
class _Fetched[T]:
    url: str
    body: T
    fetched_at: datetime
    from_cache: bool
    stale: bool
    stale_reason: str | None = None


def _text_from_entry(entry: dict[str, Any]) -> str | None:
    text = entry.get("text")
    return text if isinstance(text, str) else None


def _bytes_to_entry(content: bytes) -> dict[str, Any]:
    return {"body_b64": base64.b64encode(content).decode("ascii"), "sha256": hashlib.sha256(content).hexdigest()}


def _bytes_from_entry(entry: dict[str, Any]) -> bytes | None:
    encoded, digest = entry.get("body_b64"), entry.get("sha256")
    if not isinstance(encoded, str) or not isinstance(digest, str):
        return None
    try:
        content = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        return None
    return content if hashlib.sha256(content).hexdigest() == digest else None


_TEXT: Final = _Codec[str](_TEXT_SUFFIX, _ACCEPT_TEXT, lambda r: r.text, lambda t: {"text": t}, _text_from_entry)
_BYTES: Final = _Codec[bytes](_BINARY_SUFFIX, _ACCEPT_BINARY, lambda r: r.content, _bytes_to_entry, _bytes_from_entry)


@dataclass(slots=True)
class HttpConfig:
    min_interval: float = 1.0
    timeout: float = 30.0
    max_retries: int = 3
    backoff: float = 2.0
    user_agent: str = USER_AGENT


@dataclass(slots=True)
class CachedHttp:
    """Fetch text (`get_text`) and binary (`get_bytes`) resources through a disk cache. One instance per sync run."""

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
        got = self._fetch(_TEXT, namespace, url, params, max_age=max_age, refresh=refresh, validate=validate)
        return FetchResult(got.url, got.body, got.fetched_at, got.from_cache, got.stale, got.stale_reason)

    def get_bytes(
        self,
        namespace: str,
        url: str,
        params: Mapping[str, str] | None = None,
        *,
        max_age: timedelta,
        refresh: bool = False,
        validate: Callable[[bytes], None] | None = None,
    ) -> BytesResult:
        """Binary twin of `get_text` (same cache, throttling, retries, host breaker and validate-before-cache rules);
        the cached body is checked against its SHA-256 on every read, a mismatch is a cache miss."""
        got = self._fetch(_BYTES, namespace, url, params, max_age=max_age, refresh=refresh, validate=validate)
        return BytesResult(got.url, got.body, got.fetched_at, got.from_cache, got.stale, got.stale_reason)

    def _fetch[T](
        self,
        codec: _Codec[T],
        namespace: str,
        url: str,
        params: Mapping[str, str] | None,
        *,
        max_age: timedelta,
        refresh: bool,
        validate: Callable[[T], None] | None,
    ) -> _Fetched[T]:
        full_url = _full_url(url, params)
        entry_path = _entry_path(self.cache_dir, namespace, full_url, codec.suffix)
        cached = _read_entry(entry_path)
        cached_body = codec.from_entry(cached) if cached is not None else None
        if cached_body is None:
            cached = None

        def cached_result(stale: bool, reason: str | None = None) -> _Fetched[T]:
            assert cached is not None and cached_body is not None
            fetched = _fetched_at(cached)
            return _Fetched(full_url, cached_body, fetched, from_cache=True, stale=stale, stale_reason=reason)

        if cached is not None and not refresh and self.now() - _fetched_at(cached) < max_age:
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
            response = self._request(full_url, cached, codec.accept)
        except FetchError as exc:
            if cached is None:
                raise
            return cached_result(stale=True, reason=exc.reason)

        now = self.now()
        if response.status_code == 304 and cached is not None and cached_body is not None:
            cached["fetched_at"] = now.isoformat()
            _write_entry(entry_path, cached)
            return _Fetched(full_url, cached_body, now, from_cache=True, stale=False)

        body = codec.from_response(response)
        if validate is not None:
            try:
                validate(body)
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
                **codec.to_entry(body),
            },
        )
        return _Fetched(full_url, body, now, from_cache=False, stale=False)

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def __enter__(self) -> CachedHttp:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    # ------------------------------------------------------------------ internals

    def _request(self, full_url: str, cached: dict[str, Any] | None, accept: str) -> httpx.Response:
        headers = {"User-Agent": self.config.user_agent, "Accept": accept}
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


def read_cached_bytes(
    cache_dir: Path, namespace: str, url: str, params: Mapping[str, str] | None = None
) -> bytes | None:
    """The cached body of a `get_bytes` resource, whatever its age; None when absent or corrupt. Never uses the
    network (offline readers such as icon loading)."""
    entry = _read_entry(_entry_path(cache_dir, namespace, _full_url(url, params), _BINARY_SUFFIX))
    return _bytes_from_entry(entry) if entry is not None else None


def _entry_path(cache_dir: Path, namespace: str, full_url: str, suffix: str) -> Path:
    key = hashlib.sha256(full_url.encode("utf-8")).hexdigest()
    return cache_dir / namespace / f"{key}{suffix}"


def _full_url(url: str, params: Mapping[str, str] | None) -> str:
    """URL with its own query merged with `params`, sorted so the cache key does not depend on order."""
    base = httpx.URL(url)
    merged = dict(base.params.multi_items())
    merged.update(params or {})
    if not merged:
        return str(base)
    return str(base.copy_with(params=sorted(merged.items())))


def _read_entry(path: Path) -> dict[str, Any] | None:
    """The entry's metadata (its body is checked by the codec); None for a missing or corrupt entry (a cache miss:
    the source is re-fetched)."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("fetched_at"), str):
        return None
    try:
        datetime.fromisoformat(data["fetched_at"])
    except ValueError:
        return None
    return data


def _fetched_at(entry: Mapping[str, Any]) -> datetime:
    """The entry's fetch time; a time without a timezone (hand-edited or older entry) is taken as UTC instead of
    crashing the comparison with an aware clock (M7 review)."""
    fetched = datetime.fromisoformat(entry["fetched_at"])
    return fetched if fetched.tzinfo is not None else fetched.replace(tzinfo=UTC)


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
