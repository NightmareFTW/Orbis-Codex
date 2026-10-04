"""Exact (normalised) name -> code index. Ambiguous names are never resolved (golden rule: no silent near-miss).

Real example: the Stove hero list has two different heroes named "Mercedes" (c0001 and c1005).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field


def normalise_name(text: str) -> str:
    """Lower-case alphanumerics only: "Archdemon's Shadow" == "archdemon_shadow" (curly quotes are dropped too)."""
    return "".join(ch for ch in text.casefold() if ch.isalnum())


@dataclass(slots=True)
class NameIndex:
    _codes: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))

    def add(self, name: str, code: str) -> None:
        key = normalise_name(name)
        if key:
            self._codes[key].add(code)

    def lookup(self, name: str) -> str | None:
        """The code for this exact normalised name, or None when unknown or ambiguous."""
        codes = self._codes.get(normalise_name(name))
        if codes is None or len(codes) != 1:
            return None
        return next(iter(codes))

    def is_ambiguous(self, name: str) -> bool:
        return len(self._codes.get(normalise_name(name), ())) > 1

    def ambiguous(self) -> dict[str, list[str]]:
        return {key: sorted(codes) for key, codes in sorted(self._codes.items()) if len(codes) > 1}

    def __len__(self) -> int:
        return len(self._codes)
