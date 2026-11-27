"""A badge that keeps what matters on screen long enough to be read (SPEC D136).

The Skill reader's loop looks at the game many times a second and used to say "hold a skill icon" on every frame
without a popup, which wiped the line that said what had just been read before anyone could see it. This wrapper sits
between the watch and the badge: a message sent with `hold=` (a result, a problem) stays for that many seconds, and the
plain messages that arrive meanwhile (the idle hint, "Hold still...") wait and the last of them is shown when the hold
ends. A message with `urgent=True` (a new reading started) breaks the hold at once. Pure: it needs a clock only."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

HOLD: float = 6.0
"""Seconds a result stays on the badge."""


class StickyBadge:
    def __init__(self, inner: Any, clock: Callable[[], float]) -> None:
        self._inner = inner
        self._clock = clock
        self._until = 0.0
        self._waiting: tuple[str, str, str] | None = None

    # --- what is said ---------------------------------------------------------------------------------------------

    def say(self, top: str, bottom: str, colour: str, *, hold: float = 0.0, urgent: bool = False) -> None:
        now = self._clock()
        if hold > 0:
            self._until, self._waiting = now + hold, None
            self._inner.say(top, bottom, colour)
        elif urgent or now >= self._until:
            self._until, self._waiting = 0.0, None
            self._inner.say(top, bottom, colour)
        else:
            self._waiting = (top, bottom, colour)  # shown when the hold ends

    def settling(self) -> None:
        if self._clock() >= self._until:
            self._inner.settling()

    def tick(self) -> None:
        """Call on every look at the game: shows the message that waited, once the hold is over."""
        if self._waiting is not None and self._clock() >= self._until:
            top, bottom, colour = self._waiting
            self._waiting, self._until = None, 0.0
            self._inner.say(top, bottom, colour)

    # --- everything else goes straight through --------------------------------------------------------------------

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)
