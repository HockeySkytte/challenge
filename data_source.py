"""Read the cleaned dataset and answer what the pages ask of it.

The app reads ``data/cleaned/events.csv``, written by ``clean_data.py``.  This
module is the only place the app touches data.

Everything the pages need comes from four functions:

    load()      read the file once and keep it in memory
    resolve()   work out the slicers and what each one can offer
    select()    apply the slicers
    event_map() / heat_counts() / load_models()   answer what a page asks

The slicer values are checked against the values actually in the data, so a
hand-edited URL degrades to "all" rather than erroring or reaching the query.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import pandas as pd

import rink_geometry

#: The slicers in the side panel, and the column each one filters on.
FILTER_COLUMNS = {
    "competition": "competition",
    "team": "team",
    "player": "player",
    "strength_state": "strength_state",
    "score_state": "score_state",
    "event": "event",
}

#: What the event slicer opens on.
DEFAULT_EVENT = "Play"

#: Passes, drawn as arrows from where the puck was let go to where it went.
PASS_EVENTS = ["Play", "Incomplete Play"]

#: Shot attempts, drawn as markers.  A goal is a shot attempt that went in.
SHOT_EVENTS = ["Goal", "Shot on Net", "Missed Shot", "Blocked Shot"]

#: How many events the event map draws before it stops.  Drawing every play in
#: the file would be 7,000 arrows, which no browser enjoys.
EVENT_MAP_LIMIT = 2500


class DatasetMissing(RuntimeError):
    """Raised when the cleaned CSV has not been built yet."""


@dataclass(frozen=True)
class Dataset:
    frame: pd.DataFrame
    path: Path
    modified: str

    @property
    def rows(self) -> int:
        return int(len(self.frame))


@lru_cache(maxsize=2)
def _read(path_str: str, mtime: float, size: int) -> pd.DataFrame:
    """Read the CSV, cached until the file changes."""
    del mtime, size  # only used to make the cache key change when the file does
    return pd.read_csv(path_str, low_memory=False)


def load(config) -> Dataset:
    """Load the cleaned dataset, or say clearly that it has not been built."""
    path = Path(config.events_csv)
    if not path.exists():
        raise DatasetMissing(
            f"{path} not found. Run `python clean_data.py` to build it from the "
            "raw file in data/."
        )
    stat = path.stat()
    frame = _read(str(path), stat.st_mtime, stat.st_size)
    return Dataset(
        frame=frame,
        path=path,
        modified=pd.Timestamp(stat.st_mtime, unit="s").strftime("%Y-%m-%d %H:%M"),
    )


@lru_cache(maxsize=2)
def _read_models(path_str: str, mtime: float, size: int) -> dict:
    """Read the model record, cached until the file changes."""
    del mtime, size
    return json.loads(Path(path_str).read_text(encoding="utf-8"))


def load_models(config) -> dict:
    """What clean_data.py recorded about the two xG models.

    Read from a file rather than retrained here, so the page shows the models
    that actually produced the xG columns in the data.
    """
    path = Path(config.models_json)
    if not path.exists():
        raise DatasetMissing(
            f"{path} not found. Run `python clean_data.py` to build the models."
        )
    stat = path.stat()
    return _read_models(str(path), stat.st_mtime, stat.st_size)


def _values(frame: pd.DataFrame, column: str) -> list[str]:
    """Every distinct value of a column, sorted."""
    return sorted(frame[column].dropna().unique().tolist())


def _choose(args, name: str, values: list[str], default: str = "") -> str:
    """One slicer's value, kept only if it is a value that actually exists.

    An empty string means "all".  A slicer missing from the URL opens on its
    default, which is Play for the event slicer and "all" for the rest.
    """
    if name not in args:
        return default
    chosen = (args.get(name) or "").strip()
    return chosen if chosen in values else ""


def resolve(dataset: Dataset, args) -> tuple[dict[str, str], dict[str, list[str]]]:
    """Work out the slicers and what each one can offer.

    Team narrows to the chosen competition, and Player narrows to the chosen
    team, so the three are worked out in that order - a list of teams is only
    useful once you know which competition you are looking at.

    A leftover choice from a wider selection is dropped rather than applied.
    Pick the Olympics while a college team is selected and the team resets to
    "all", because that team played in no Olympic game and filtering on it would
    leave an empty page.
    """
    frame = dataset.frame

    competitions = _values(frame, "competition")
    competition = _choose(args, "competition", competitions)

    in_competition = (
        frame if not competition else frame[frame["competition"] == competition]
    )
    teams = _values(in_competition, "team")
    team = _choose(args, "team", teams)

    in_team = (
        in_competition if not team else in_competition[in_competition["team"] == team]
    )
    players = _values(in_team, "player")
    player = _choose(args, "player", players)

    # These three are not narrowed by anything, so they offer the whole file.
    strengths = _values(frame, "strength_state")
    # score_state is the goal difference, so it sorts as a number: -2 before 1.
    scores = [str(score) for score in sorted(frame["score_state"].dropna().unique().tolist())]
    # Most common first, so Play sits at the top of the list.
    events = frame["event"].value_counts().index.tolist()

    selected = {
        "competition": competition,
        "team": team,
        "player": player,
        "strength_state": _choose(args, "strength_state", strengths),
        "score_state": _choose(args, "score_state", scores),
        "event": _choose(args, "event", events, DEFAULT_EVENT),
    }
    available = {
        "competition": competitions,
        "team": teams,
        "player": players,
        "strength_state": strengths,
        "score_state": scores,
        "event": events,
    }
    return selected, available


def select(dataset: Dataset, filters: dict[str, str]) -> pd.DataFrame:
    """Apply the filters, one at a time."""
    frame = dataset.frame
    for name, chosen in filters.items():
        if not chosen:
            continue
        values = frame[FILTER_COLUMNS[name]]
        if name == "score_state":
            # The only slicer whose column is a number rather than text.
            values = values.astype("string")
        frame = frame[values == chosen]
    return frame


def event_kind(event: str) -> str:
    """How an event should be drawn on the event map."""
    if event == "Play":
        return "pass"
    if event == "Incomplete Play":
        return "incomplete"
    if event in SHOT_EVENTS:
        return "shot"
    return "other"


def event_map(frame: pd.DataFrame, limit: int = EVENT_MAP_LIMIT) -> tuple[list[dict], int]:
    """The events to draw, in rink coordinates, plus how many there were.

    Passes carry a second point - where the puck was aimed - so they come back
    with both ends.  Everything else has one location.
    """
    total = int(len(frame))
    drawn = []
    for row in frame.head(limit).itertuples(index=False):
        x, y = rink_geometry.to_svg(row.x1, row.y1)
        item = {
            "x": x,
            "y": y,
            "kind": event_kind(row.event),
            "event": row.event,
            "team": row.team,
            "player": row.player,
            "period": int(row.period),
            "time": int(row.time_elapsed),
        }
        if row.event in PASS_EVENTS and pd.notna(row.x2):
            item["x2"], item["y2"] = rink_geometry.to_svg(row.x2, row.y2)
        drawn.append(item)
    return drawn, total


def heat_counts(frame: pd.DataFrame, direction: str) -> dict[str, int]:
    """How many events land in each rink box.

    "from" counts where the event happened.  "to" counts where a pass was aimed
    instead, so Plays and Incomplete Plays use boxid_2 - the box the puck
    arrived in - while everything else has only the one location and still uses
    boxid.
    """
    if direction == "to":
        aimed = frame["event"].isin(PASS_EVENTS)
        boxes = frame["boxid"].where(~aimed, frame["boxid_2"])
    else:
        boxes = frame["boxid"]
    return {str(box): int(count) for box, count in boxes.dropna().value_counts().items()}

