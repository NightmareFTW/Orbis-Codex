"""What the reader must not mistake for the game (SPEC D104): the program's own window and badge.

The game is read from the screen, so whatever is on top of it is read too: the Orbis window put over the game, or the
badge over the game saying `Read: Notos` and the name of the skill. Their text has the shape of a popup (a title and a
line or two) and was stored as skills ("S3 Sword of Requiem |", "Read: Notos -"). A frame that shows the window is not
read at all, and the badge's own lines are taken out of what is."""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Final

from e7ac.vision.ocr import TextLine

WINDOW_MARKERS: Final = (
    "orbis codex",
    "arena companion",
    "hero of the day",
    "hide complete heroes",
    "start reading",
    "skill reader",
)
"""Words of the window that no game text has."""
BADGE_LINE: Final = re.compile(r"^(read|hero|skills)\s*:", re.IGNORECASE)
"""The first line of the badge while the skill reader runs: "Read: <hero>", "Hero: <hero>", "Skills: ..."."""


def shows_window(lines: Sequence[TextLine]) -> bool:
    """Whether the Orbis window is in the picture (put over the game)."""
    return any(marker in line.text.lower() for line in lines for marker in WINDOW_MARKERS)


def without_badge(lines: Sequence[TextLine]) -> list[TextLine]:
    """The lines without the badge's own: a line that starts like its first line and the line just under it."""
    heads = [line for line in lines if BADGE_LINE.match(line.text.strip())]
    if not heads:
        return list(lines)

    def of_badge(line: TextLine) -> bool:
        return any(
            line is head
            or (
                head.box.y1 - 2 <= line.box.y0 <= head.box.y1 + 1.8 * head.box.height
                and abs(line.box.x0 - head.box.x0) <= 70
            )
            for head in heads
        )

    return [line for line in lines if not of_badge(line)]
