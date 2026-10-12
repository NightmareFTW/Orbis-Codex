"""Spike: how well are the Equipment-tab stat rows read on real captures, under small image changes?

The user's watch run (2026-10-11/12) misread some rows on live frames that read fine here: the orange "▲" bonus sits
on a bright, busy background, so the OCR is at its limit and tiny pixel differences (another PC, another frame, a
re-encoding) flip the result. This harness measures a row reader over the user's Equipment captures and a set of
perturbations standing in for those differences (re-encoding, window scale, blur, noise, brightness, sub-pixel shift).

Usage:
    uv run python spikes/watch_ocr_eval.py                      # the current reader (baseline)
    uv run python spikes/watch_ocr_eval.py spikes.my_spike:read_rows --variants orig,webp75 --frames kawerik
    uv run python spikes/watch_ocr_eval.py ... --jobs 2         # processes (each loads its own OCR engine)

A reader is `read_rows(image, reader, base) -> {stat value: (final, bonus)}`: `image` BGR, `reader` a loaded
RapidOcrReader, `base` the catalog base per stat (final - bonus at Lv. 60, the arbiter the app has on this screen).
Ground truth: fixtures/screenshots/watch/truth.json and the captures (local, git-ignored; never committed).
Score: rows read exactly; "wrong" rows are those read with a wrong value (worse than a row left unread, unless the
reader also says so); "silent" rows are wrong rows that still pass the base check (accepted as right: the worst).
"""

from __future__ import annotations

import argparse
import importlib
import json
import sys
import time
from collections.abc import Callable, Mapping
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SHOTS = ROOT / "fixtures" / "screenshots"
TRUTH = SHOTS / "watch" / "truth.json"
FLAT = {"att", "def", "max_hp", "speed"}

Pair = tuple[float | None, float | None]
Reader = Callable[[Any, Any, Mapping[str, float]], Mapping[str, Pair]]


def _reencode(image: np.ndarray, ext: str, flag: int, quality: int) -> np.ndarray:
    ok, data = cv2.imencode(ext, image, [flag, quality])
    assert ok
    return cv2.imdecode(data, cv2.IMREAD_COLOR)


def _scale(image: np.ndarray, factor: float) -> np.ndarray:
    size = (round(image.shape[1] * factor), round(image.shape[0] * factor))
    return cv2.resize(image, size, interpolation=cv2.INTER_AREA if factor < 1 else cv2.INTER_CUBIC)


def _noise(image: np.ndarray) -> np.ndarray:
    rng = np.random.default_rng(7)
    return np.clip(image.astype(np.float32) + rng.normal(0, 5, image.shape), 0, 255).astype(np.uint8)


def _desaturate(image: np.ndarray) -> np.ndarray:
    """Colours 25% closer to grey (another monitor profile or a game brightness setting)."""
    grey = cv2.cvtColor(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY), cv2.COLOR_GRAY2BGR).astype(np.float32)
    return np.clip(grey + 0.75 * (image.astype(np.float32) - grey), 0, 255).astype(np.uint8)


def _shift(image: np.ndarray) -> np.ndarray:
    matrix = np.float32([[1, 0, 0.5], [0, 1, 0.5]])
    return cv2.warpAffine(image, matrix, (image.shape[1], image.shape[0]), flags=cv2.INTER_LINEAR)


VARIANTS: dict[str, Callable[[np.ndarray], np.ndarray]] = {
    "orig": lambda im: im,
    "webp75": lambda im: _reencode(im, ".webp", cv2.IMWRITE_WEBP_QUALITY, 75),
    "jpeg85": lambda im: _reencode(im, ".jpg", cv2.IMWRITE_JPEG_QUALITY, 85),
    "scale0.8": lambda im: _scale(im, 0.8),
    "scale1.2": lambda im: _scale(im, 1.2),
    "blur": lambda im: cv2.GaussianBlur(im, (0, 0), 0.7),
    "noise": _noise,
    "dark": lambda im: cv2.convertScaleAbs(im, alpha=0.85, beta=-5),
    "shift": _shift,
}
HOLDOUT: dict[str, Callable[[np.ndarray], np.ndarray]] = {
    "scale0.64": lambda im: _scale(im, 0.64),
    "scale1.5": lambda im: _scale(im, 1.5),
    "jpeg70": lambda im: _reencode(im, ".jpg", cv2.IMWRITE_JPEG_QUALITY, 70),
    "gamma": lambda im: cv2.LUT(im, np.array([255 * (i / 255) ** 0.8 for i in range(256)], dtype=np.uint8)),
    "desat": _desaturate,
    "crop": lambda im: im[im.shape[0] // 40 :, im.shape[1] // 60 :],
}
"""Variants never used while tuning a reader (added after the D57 comparison): its out-of-sample check."""
VARIANTS.update(HOLDOUT)


def baseline(image: Any, reader: Any, base: Mapping[str, float]) -> dict[str, Pair]:
    """The reader of the app today: full-frame OCR, then the text parser."""
    from e7ac.vision.hero_screen import parse_hero_screen

    reading = parse_hero_screen(reader.read(image))
    return {stat.value: (row.final, row.bonus) for stat, row in reading.stats.items()}


def app(image: Any, reader: Any, base: Mapping[str, float]) -> dict[str, Pair]:
    """The app's reader since SPEC D57: the full-frame reading, then the bonus cell of each row the base check does
    not confirm (`vision.bonus_cells`), the base used only to accept or reject what the cell read."""
    from e7ac.domain.codes import Stat
    from e7ac.vision.bonus_cells import reread_bonuses
    from e7ac.vision.hero_screen import parse_hero_screen

    lines = reader.read(image)
    reading = parse_hero_screen(lines)

    def agrees(stat: Stat, final: float | None, bonus: float | None) -> bool:
        tolerance = 0.5 if stat.value in FLAT else 0.0005
        return final is not None and abs(final - (bonus or 0.0) - base[stat.value]) <= tolerance

    unsettled = [s for s, r in reading.stats.items() if s.value in base and not agrees(s, r.final, r.bonus)]
    found = reread_bonuses(image, reading, lines, reader, unsettled, lambda s, b: agrees(s, reading.stats[s].final, b))
    return {s.value: (r.final, found[s].bonus if s in found else r.bonus) for s, r in reading.stats.items()}


def _same(a: float | None, b: float | None, flat: bool) -> bool:
    if a is None or b is None:
        return a is b
    return a == b if flat else abs(a - b) < 0.0005


def _load(spec: str) -> Reader:
    if spec in ("baseline", "app"):
        return {"baseline": baseline, "app": app}[spec]
    module, _, name = spec.partition(":")
    return getattr(importlib.import_module(module), name)  # type: ignore[no-any-return]


_READER: Any = None


def _job(args: tuple[str, str, str, dict[str, list[float | None]]]) -> dict[str, Any]:
    global _READER
    spec, frame, variant, truth = args
    if _READER is None:
        sys.path.insert(0, str(ROOT))
        from e7ac.vision.ocr import RapidOcrReader

        _READER = RapidOcrReader()
    image = VARIANTS[variant](cv2.imread(str(SHOTS / frame)))
    base = {stat: float(pair[0] or 0) - float(pair[1] or 0) for stat, pair in truth.items()}
    started = time.perf_counter()
    try:
        got = dict(_load(spec)(image, _READER, base))
    except Exception as exc:  # a reader crash is a result too
        got = {}
        print(f"  {frame} {variant}: reader failed: {exc!r}", file=sys.stderr)
    seconds = time.perf_counter() - started
    rows = []
    for stat, (final, bonus) in truth.items():
        flat = stat in FLAT
        read_final, read_bonus = got.get(stat, (None, None))
        right = _same(read_final, final, flat) and _same(read_bonus, bonus, flat)
        unread = read_final is None
        passes = read_final is not None and abs((read_final - (read_bonus or 0.0)) - base[stat]) <= (
            0.5 if flat else 0.0005
        )
        rows.append(
            {
                "stat": stat,
                "right": right,
                "unread": unread,
                "silent": not right and passes,
                "got": [read_final, read_bonus],
                "want": [final, bonus],
            }
        )
    return {"frame": frame, "variant": variant, "seconds": seconds, "rows": rows}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("reader", nargs="?", default="app", help="module:function, 'app' (default) or 'baseline'")
    parser.add_argument("--variants", default=",".join(VARIANTS))
    parser.add_argument("--frames", default="", help="comma-separated substrings of frame names")
    parser.add_argument("--jobs", type=int, default=1)
    parser.add_argument("--json", type=Path, help="also write the full results here")
    args = parser.parse_args()
    if not TRUTH.exists():
        sys.exit(f"missing {TRUTH} (local fixtures)")
    truth: dict[str, dict[str, list[float | None]]] = json.loads(TRUTH.read_text())["frames"]
    wanted = [f for f in args.frames.split(",") if f]
    frames = [f for f in truth if not wanted or any(w in f for w in wanted)]
    variants = [v for v in args.variants.split(",") if v]
    jobs = [(args.reader, frame, variant, truth[frame]) for frame in frames for variant in variants]
    with ProcessPoolExecutor(max_workers=args.jobs) as pool:
        results = list(pool.map(_job, jobs))
    rows = [row for result in results for row in result["rows"]]
    right = sum(r["right"] for r in rows)
    wrong = [r for r in rows if not r["right"] and not r["unread"]]
    silent = sum(r["silent"] for r in rows)
    print(
        f"{args.reader}: {right}/{len(rows)} rows right ({100 * right / len(rows):.1f}%), "
        f"{len(wrong)} wrong, {sum(r['unread'] for r in rows)} unread, {silent} SILENT (wrong but pass the base "
        f"check); {sum(r['seconds'] for r in results) / len(results):.2f} s per image"
    )
    for variant in variants:
        mine = [r for res in results if res["variant"] == variant for r in res["rows"]]
        print(f"  {variant:9} {sum(r['right'] for r in mine)}/{len(mine)}")
    for result in results:
        for row in result["rows"]:
            if not row["right"]:
                flag = " SILENT" if row["silent"] else ""
                print(
                    f"  x {result['frame']:24} {result['variant']:9} {row['stat']:8} got {row['got']} "
                    f"want {row['want']}{flag}"
                )
    if args.json:
        args.json.write_text(json.dumps(results, indent=1))


if __name__ == "__main__":
    main()
