"""Learned references: crops of heroes the user confirmed on Arena VS screens (M17a, SPEC D64).

No public source shows the game's battle models, so the app learns them from the user's own captures: each confirmed
hero's crop is kept as a PNG under `<home>/references/arena/<hero code>/` (local only, never in the repository) and
later crops are matched against it with ORB keypoints (shape and texture, robust to scale and position; the
opponent side faces the other way, so the mirrored reference is tried too). Acceptance is decided by
`arena.identify` with a margin rule; a hero never confirmed is never matched.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Final, cast

import cv2
import numpy as np
import numpy.typing as npt

from e7ac.domain.codes import is_hero_code
from e7ac.fileio import write_text_atomic

type BgrImage = npt.NDArray[np.uint8]

MAX_PER_HERO: Final = 12
"""Crops kept per hero (poses, animation frames, skins); the oldest goes first (`assumed` enough)."""
ORB_FEATURES: Final = 1500
RATIO: Final = 0.8
"""Lowe's ratio test."""
FOREGROUND_MIN: Final = 60
"""Grey level above the dark VS background (measured <= 60 on prep_1)."""
NORMAL_HEIGHT: Final = 280
"""Crops are resized to this height before matching (scale invariance from the badge size)."""


@dataclass(frozen=True, slots=True)
class Keypoints:
    points: npt.NDArray[np.float32]
    descriptors: npt.NDArray[np.uint8] | None
    height: int


@dataclass(frozen=True, slots=True)
class Match:
    inliers: int
    spread: float
    """Vertical extent of the matched points as a share of the reference height (a neighbour's leaked pixels match
    in a small area only)."""


def keypoints(crop: BgrImage) -> Keypoints:
    scale = NORMAL_HEIGHT / max(1, crop.shape[0])
    image = cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    grey = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    mask = (grey > FOREGROUND_MIN).astype(np.uint8) * 255
    orb = cv2.ORB.create(nfeatures=ORB_FEATURES)
    found, descriptors = cast(tuple[Any, Any], orb.detectAndCompute(grey, mask))  # descriptors: None without points
    points = np.array([k.pt for k in found], np.float32).reshape(-1, 2)
    return Keypoints(points, descriptors, grey.shape[0])


def match(reference: Keypoints, query: Keypoints) -> Match:
    if reference.descriptors is None or query.descriptors is None or len(reference.points) < 3 or len(query.points) < 3:
        return Match(0, 0.0)
    matcher = cv2.BFMatcher(cv2.NORM_HAMMING)
    good = [
        pair[0]
        for pair in matcher.knnMatch(query.descriptors, reference.descriptors, k=2)
        if len(pair) == 2 and pair[0].distance < RATIO * pair[1].distance
    ]
    if len(good) < 4:
        return Match(0, 0.0)
    source = np.array([query.points[g.queryIdx] for g in good], np.float32)
    target = np.array([reference.points[g.trainIdx] for g in good], np.float32)
    _, inlier_mask = cast(
        Any, cv2.estimateAffinePartial2D(source, target, method=cv2.RANSAC, ransacReprojThreshold=6.0)
    )  # None when no transform is found
    if inlier_mask is None:
        return Match(0, 0.0)
    chosen = target[inlier_mask.ravel().astype(bool)]
    if len(chosen) == 0:
        return Match(0, 0.0)
    spread = float(chosen[:, 1].max() - chosen[:, 1].min()) / reference.height
    return Match(len(chosen), spread)


class ReferenceStore:
    """`root` = `<home>/references/arena`."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self._cache: dict[str, list[Keypoints]] = {}

    def codes(self) -> set[str]:
        if not self.root.is_dir():
            return set()
        return {p.name for p in self.root.iterdir() if p.is_dir() and is_hero_code(p.name) and any(p.glob("*.png"))}

    def best(self, code: str, query: BgrImage) -> Match:
        """Best match of `query` (and its mirror) against every stored crop of `code`."""
        found = Match(0, 0.0)
        references = self._references(code)
        if not references:
            return found
        sides = [keypoints(query), keypoints(np.asarray(cv2.flip(query, 1), np.uint8))]
        for reference in references:
            for side in sides:
                result = match(reference, side)
                if result.inliers > found.inliers:
                    found = result
        return found

    def add(self, code: str, crop: BgrImage, when: datetime) -> Path:
        if not is_hero_code(code):
            raise ValueError(f"not a hero code: {code!r}")
        ok, encoded = cv2.imencode(".png", crop)
        if not ok:
            raise ValueError("crop could not be encoded")
        data = encoded.tobytes()
        folder = self.root / code
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{when.strftime('%Y%m%d-%H%M%S')}-{hashlib.sha256(data).hexdigest()[:8]}.png"
        temporary = path.with_suffix(".tmp")
        temporary.write_bytes(data)
        temporary.replace(path)
        for old in sorted(folder.glob("*.png"))[:-MAX_PER_HERO]:
            old.unlink(missing_ok=True)
        self._cache.pop(code, None)
        return path

    def crops(self) -> dict[str, list[BgrImage]]:
        """Every stored crop, per hero code (damaged files left out)."""
        found: dict[str, list[BgrImage]] = {}
        for code in sorted(self.codes()):
            for path in sorted((self.root / code).glob("*.png")):
                image = cv2.imread(str(path), cv2.IMREAD_COLOR)
                if image is not None:
                    found.setdefault(code, []).append(np.asarray(image, np.uint8))
        return found

    def last_team(self) -> list[str]:
        try:
            data: Any = json.loads((self.root / "last_team.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        codes = data.get("you") if isinstance(data, dict) else None
        return [c for c in codes if isinstance(c, str) and is_hero_code(c)] if isinstance(codes, list) else []

    def save_last_team(self, codes: list[str]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        write_text_atomic(self.root / "last_team.json", json.dumps({"you": codes}))

    def _references(self, code: str) -> list[Keypoints]:
        if code not in self._cache:
            loaded = []
            for path in sorted((self.root / code).glob("*.png")) if is_hero_code(code) else []:
                image = cv2.imread(str(path), cv2.IMREAD_COLOR)
                if image is not None:  # a damaged file counts as missing
                    loaded.append(keypoints(np.asarray(image, np.uint8)))
            self._cache[code] = loaded
        return self._cache[code]
