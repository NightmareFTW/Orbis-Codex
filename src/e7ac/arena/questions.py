"""What the watch asks the user (SPEC D83): "who is this?" for a face it could not name, with the picture and what is
known, so a window can show it. UI-free: the command line asks the same things on the terminal."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

type BgrImage = npt.NDArray[np.uint8]


@dataclass(frozen=True, slots=True, eq=False)
class FaceQuestion:
    kind: str
    """"slot" (the VS screen), "guide" (the Attack Guide) or "list" (the Opponents list)."""
    title: str
    """What is asked about: "Your team, slot 2", "Opponent hero 3"."""
    detail: str
    """What is known about it: "light knight" (its badge), or why it is asked."""
    crop: BgrImage | None
    """The picture of the hero (BGR), when there is one."""
    closest: str | None = None
    """The hero it looked most like, when it looked like one (a code)."""
    candidates: tuple[str, ...] = ()
    """The heroes it can be (codes); empty: any hero of the catalog."""
    suggestions: tuple[str, ...] = ()
    """The heroes it most likely is, best first (codes): what the window offers to pick with one click before it asks
    for a name."""
    key: tuple[str, int, int] | None = None
    """A face of a recorded list: (instant, row, face) of the sighting to correct once it is named."""
