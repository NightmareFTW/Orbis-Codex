"""What the Arena watch predicted and what happened (SPEC D93): the log that turns every later fix of the simulation
into a measured one (NV-51).

Two kinds of line in `<home>/arena/results.jsonl` (local only): a **prediction** (when the VS screen of a match was
read: both teams as hero codes, the chance to win, the profile of the opponent build, the heroes whose skills were not
read) and an **outcome** (when the Victory / Defeat screen was seen). An outcome belongs to the last prediction made
before it, if that was no more than `WINDOW` earlier (a fight lasts minutes; a prediction left unplayed is not an
outcome's). Only hero codes are kept, never a player name. A line that cannot be read is counted, never dropped
silently."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final

from e7ac.fileio import write_text_atomic

FILE_NAME: Final = "results.jsonl"
WINDOW: Final = timedelta(minutes=20)
RESULTS: Final = ("victory", "defeat")


def results_path(home: Path) -> Path:
    return home / "arena" / FILE_NAME


@dataclass(frozen=True, slots=True)
class Prediction:
    when: datetime
    team: tuple[str, ...]
    theirs: tuple[str, ...]
    win: float
    loss: float
    profile: str
    blind: tuple[str, ...] = ()
    """The heroes (with skill slots) whose skills were not read when the chance was computed."""


@dataclass(frozen=True, slots=True)
class Outcome:
    when: datetime
    result: str
    """"victory" or "defeat"."""


@dataclass(frozen=True, slots=True)
class Fight:
    prediction: Prediction
    outcome: Outcome

    @property
    def won(self) -> bool:
        return self.outcome.result == "victory"


class ResultStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def add(self, item: Prediction | Outcome) -> None:
        line = json.dumps(_to_json(item), separators=(",", ":"))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(line + "\n")

    def read(self) -> tuple[list[Prediction], list[Outcome], int]:
        """(predictions, outcomes, lines that could not be read), in the order written."""
        predictions: list[Prediction] = []
        outcomes: list[Outcome] = []
        skipped = 0
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return predictions, outcomes, 0
        for line in lines:
            if not line.strip():
                continue
            try:
                data = json.loads(line)
                when = datetime.fromisoformat(data["when"])
                if data["kind"] == "prediction":
                    predictions.append(
                        Prediction(
                            when,
                            tuple(data["team"]),
                            tuple(data["theirs"]),
                            float(data["win"]),
                            float(data["loss"]),
                            str(data["profile"]),
                            tuple(data.get("blind", [])),
                        )
                    )
                elif data["kind"] == "outcome" and data["result"] in RESULTS:
                    outcomes.append(Outcome(when, data["result"]))
                else:
                    skipped += 1
            except (ValueError, KeyError, TypeError):
                skipped += 1
        return predictions, outcomes, skipped

    def remove_last(self) -> bool:
        """Drop the last line (a wrong outcome noticed by the user)."""
        try:
            lines = [line for line in self.path.read_text(encoding="utf-8").splitlines() if line.strip()]
        except OSError:
            return False
        if not lines:
            return False
        write_text_atomic(self.path, "\n".join(lines[:-1]) + ("\n" if len(lines) > 1 else ""))
        return True


def _to_json(item: Prediction | Outcome) -> dict[str, object]:
    when = item.when.astimezone(UTC).isoformat(timespec="seconds")
    if isinstance(item, Outcome):
        return {"kind": "outcome", "when": when, "result": item.result}
    return {
        "kind": "prediction",
        "when": when,
        "team": list(item.team),
        "theirs": list(item.theirs),
        "win": round(item.win, 4),
        "loss": round(item.loss, 4),
        "profile": item.profile,
        "blind": list(item.blind),
    }


def pair(predictions: Sequence[Prediction], outcomes: Sequence[Outcome], window: timedelta = WINDOW) -> list[Fight]:
    """Each outcome with the last prediction made before it (within `window`); a prediction is used once."""
    fights: list[Fight] = []
    used: set[int] = set()
    for outcome in sorted(outcomes, key=lambda o: o.when):
        before = [
            (i, p)
            for i, p in enumerate(predictions)
            if i not in used and timedelta(0) <= outcome.when - p.when <= window
        ]
        if before:
            index, prediction = max(before, key=lambda ip: ip[1].when)
            used.add(index)
            fights.append(Fight(prediction, outcome))
    return fights


@dataclass(frozen=True, slots=True)
class Calibration:
    fights: int
    wins: int
    expected: float
    """The wins the chances predicted (their sum)."""
    brier: float
    """The mean squared error of the chances (0 perfect; 0.25 is a coin toss)."""
    sure_but_lost: int
    """Fights lost with a chance of 85% or more."""


def calibrate(fights: Sequence[Fight]) -> Calibration:
    if not fights:
        return Calibration(0, 0, 0.0, 0.0, 0)
    wins = sum(f.won for f in fights)
    return Calibration(
        len(fights),
        wins,
        sum(f.prediction.win for f in fights),
        sum((f.prediction.win - (1.0 if f.won else 0.0)) ** 2 for f in fights) / len(fights),
        sum(not f.won and f.prediction.win >= 0.85 for f in fights),
    )
