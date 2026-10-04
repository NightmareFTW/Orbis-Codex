"""Game artwork used as image references by the screen readers: the official Stove set icons (M7).

The Hero Info screen shows a gear piece's set only as a badge (`vision.sets`), drawn with the same artwork as the
Stove Strategy Guide's "wearingStatus" set icons (seen in game for 9 sets on the user's captures, 2026-10-04;
assumed for the other 15). Facts (docs/DATA_SOURCES.md):
- URL `SET_ICON_URL`, one PNG per set, named by the catalog set code (`set_cri_dmg.png`): HTTP 200 for all 24 catalog
  set codes on 2026-10-04 (source `stove`, status `verified`), 113x119 RGBA each;
- the icons are game assets: cached under the app home only (never in the repository), fetched politely through
  `CachedHttp` (one request per second per host, honest User-Agent, retries, host breaker), refreshed monthly.

`fetch_set_icons` downloads (during `e7 catalog sync`); `load_set_icons` only reads the cache, so screen reading
works offline.
"""

from __future__ import annotations

import struct
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Final

from e7ac.domain.codes import is_set_code
from e7ac.sources.http import CachedHttp, FetchError, read_cached_bytes

SET_ICON_URL: Final = "https://static-pubcomm.onstove.com/event/live/epic7/guide/wearingStatus/images/sets/{code}.png"
SET_ICON_NAMESPACE: Final = "assets/set_icons"
SET_ICON_MAX_AGE: Final = timedelta(days=30)
"""Artwork changes rarely; a monthly revalidation (ETag / Last-Modified) is polite enough."""

PNG_SIGNATURE: Final = b"\x89PNG\r\n\x1a\n"
MAX_ICON_SIDE: Final = 1024
"""A set icon larger than this is not the expected small icon (measured 113x119): rejected before caching."""
_IHDR: Final = struct.Struct(">I4sII")
"""Length, chunk type, width, height: the first chunk of every PNG."""


@dataclass(frozen=True, slots=True)
class AssetReport:
    """Outcome of one `fetch_set_icons` run, per set code. Reasons never contain URLs."""

    fetched: tuple[str, ...] = ()
    """Downloaded (new or changed) in this run."""
    cached: tuple[str, ...] = ()
    """Served from the cache (fresh, revalidated, or stale: see `stale`)."""
    failed: Mapping[str, str] = field(default_factory=dict)
    """No usable copy: code -> reason."""
    stale: Mapping[str, str] = field(default_factory=dict)
    """Cached copy kept because a refresh failed: code -> reason (also listed in `cached`)."""

    @property
    def available(self) -> tuple[str, ...]:
        return self.fetched + self.cached


def set_icon_url(code: str) -> str:
    """URL of one set icon; only stable set codes are accepted (the code becomes part of the URL)."""
    if not is_set_code(code):
        raise ValueError(f"not a set code: {code!r}")
    return SET_ICON_URL.format(code=code)


def validate_png(data: bytes) -> None:
    """Raise ValueError unless `data` starts like a small PNG image (signature + IHDR with plausible size)."""
    if not data.startswith(PNG_SIGNATURE):
        raise ValueError("not a PNG image")
    header = data[len(PNG_SIGNATURE) : len(PNG_SIGNATURE) + _IHDR.size]
    if len(header) < _IHDR.size:
        raise ValueError("truncated PNG image")
    _, chunk, width, height = _IHDR.unpack(header)
    if chunk != b"IHDR" or not (0 < width <= MAX_ICON_SIDE and 0 < height <= MAX_ICON_SIDE):
        raise ValueError("unexpected PNG header")


def fetch_set_icons(http: CachedHttp, codes: Iterable[str], *, refresh: bool = False) -> AssetReport:
    """Download (or revalidate) the icon of every set code; a failure is reported per code, never raised."""
    fetched: list[str] = []
    cached: list[str] = []
    failed: dict[str, str] = {}
    stale: dict[str, str] = {}
    for code in sorted(set(codes)):
        if not is_set_code(code):
            failed[code] = "not a set code"
            continue
        url = set_icon_url(code)
        try:
            result = http.get_bytes(
                SET_ICON_NAMESPACE, url, max_age=SET_ICON_MAX_AGE, refresh=refresh, validate=validate_png
            )
        except FetchError as exc:
            failed[code] = _without_url(exc.reason, url)
            continue
        if result.from_cache:
            cached.append(code)
        else:
            fetched.append(code)
        if result.stale:
            stale[code] = _without_url(result.stale_reason or "stale cached copy", url)
    return AssetReport(tuple(fetched), tuple(cached), failed, stale)


def load_set_icons(cache_dir: Path, codes: Iterable[str]) -> dict[str, bytes]:
    """PNG bytes of the cached icons among `codes` (cache only, never the network); missing ones are left out, so
    callers compare the keys with `codes` and report the gap."""
    icons: dict[str, bytes] = {}
    for code in sorted(set(codes)):
        if not is_set_code(code):
            continue
        data = read_cached_bytes(cache_dir, SET_ICON_NAMESPACE, set_icon_url(code))
        if data is None:
            continue
        try:
            validate_png(data)
        except ValueError:
            continue  # a damaged cache file counts as missing (reported by the caller, re-fetched by the next sync)
        icons[code] = data
    return icons


def _without_url(reason: str, url: str) -> str:
    """Reasons are shown per set code: the URL is noise there (and the same for every code)."""
    return reason.replace(f": {url}", "").replace(url, "the icon URL")
