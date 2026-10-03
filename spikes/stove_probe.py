"""Throwaway probe of the Stove Strategy Guide JSON API (Phase 0 spike).

Reproduces the findings in docs/DATA_SOURCES.md §1: fetches one hero's statistics for a region
and decodes the histograms with the *assumed* bin edges. Standard library only; not imported by src/.

Usage:
    python spikes/stove_probe.py c2011 --world world_global
    python spikes/stove_probe.py --search Karin

Be polite: one request per run per hero, no loops over the whole hero list.
"""

from __future__ import annotations

import argparse
import json
import urllib.parse
import urllib.request

API = "https://api.onstove.com/pub-meta/v1.0/epic7/guide"
USER_AGENT = "OrbisCodex-spike/0.0 (personal, non-commercial research)"

# Front-end constants from the HeroDetailStat component (label min/max per stat).
# Equal-width bins with open-ended edge bins are an ASSUMPTION (see DATA_SOURCES.md).
STATS = [
    ("attack_stats", "ATK", 1200, 5200),
    ("defense_stats", "DEF", 800, 2400),
    ("vitality_statistics", "HP", 9000, 25000),
    ("speed_statistics", "SPD", 110, 270),
    ("critical_statistics", "CC%", 15, 100),
    ("critical_hit_statistics", "CD%", 150, 350),
    ("effective_statistics", "EFF%", 0, 180),
    ("effect_resistance_statistics", "ER%", 0, 225),
]


def get(path: str, **params: str) -> dict:
    url = f"{API}/{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        payload = json.load(resp)
    if payload.get("code") != 0:
        raise RuntimeError(f"Stove error: {payload}")
    return payload["value"]


def percentile(counts: list[int], lo: float, hi: float, p: float) -> tuple[float, bool]:
    """Linear interpolation inside bins; flag True when the result falls in an open edge bin."""
    width = (hi - lo) / len(counts)
    target = p * sum(counts)
    acc = 0
    for i, c in enumerate(counts):
        if c and acc + c >= target:
            open_edge = i in (0, len(counts) - 1)
            return lo + width * i + width * (target - acc) / c, open_edge
        acc += c
    return hi, True


def show_hero(hero_code: str, world: str) -> None:
    v = get(
        "hunt/hero-detail-for-game",
        world_code=world, stage="1", lang_code="en", hero_code=hero_code, strategy_type="0", boss_type="1",
    )
    if not v.get("seq"):
        print("No data for this hero/region.")
        return
    print(f"{v['hero_name']} ({v['hero_code']}) {v['attribute_code']}/{v['job_code']} {v['grade']}★ — {world}, reg_date {v['reg_date']}")
    for key, name, lo, hi in STATS:
        counts = [int(x) for x in v[key].split(",")]
        parts = []
        for p in (0.5, 0.75, 0.9):
            value, open_edge = percentile(counts, lo, hi, p)
            parts.append(f"P{int(p * 100)}={value:.1f}{'*' if open_edge else ''}")
        print(f"  {name:5} n={sum(counts):>6}  {'  '.join(parts)}   counts={counts}")
    print("  (* = falls in an open-ended edge bin: low confidence)")
    print("  sets:", [(e["rank_sets"], e["eqip_rank_share"]) for e in v["hero_eqips"]])
    print("  EE:  ", [(e.get("rank_skill"), e.get("enhancemen_skill"), e["skill_rank_share"]) for e in v["hero_dedicated_eqip"]])
    print("  arti:", [(a["rank_arti"], a["rank_arti_name"], a["arti_rank_share"]) for a in v["hero_arti"]])


def search(keyword: str, world: str) -> None:
    v = get("wearing-status/hero-list", world_code=world, lang_code="en", current_page="1", is_paging="N", keyword=keyword)
    for h in v["guide_list"]:
        print(f"{h['hero_code']}  {h['hero_name']}  {h['attribute_code']}/{h['job_code']} {h['grade']}★")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("hero_code", nargs="?")
    ap.add_argument("--world", default="world_global",
                    choices=["world_global", "world_eu", "world_asia", "world_kor", "world_jpn"])
    ap.add_argument("--search")
    args = ap.parse_args()
    if args.search:
        search(args.search, args.world)
    elif args.hero_code:
        show_hero(args.hero_code, args.world)
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
