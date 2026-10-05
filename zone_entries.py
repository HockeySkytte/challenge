"""Value a zone entry by the part of the rink it was made from.

The question: when a team carries, plays or dumps the puck into the offensive
zone, what does it get out of the next 15 seconds?

**The coordinate a zone entry is logged at is not where the entry happened.**
A carry or a play is recorded where the puck crossed the blue line, and a dump
where the puck was released - which is well before the line.  Read literally,
the same kind of entry scatters across the neutral zone and the offensive zone,
and the map is hard to read.

So each entry is filed by its **type** instead:

    Carried, Played   ->  the first offensive column, O24 / O25 / O26
    Dumped            ->  the last neutral column, N01 / N02 / N03

Only the third of the ice it was made from is kept, so a carry up the left wall
and a carry up the right wall still land in different boxes.  Everything else
follows the Possession Values page: the rink is mirrored across the centre line,
thin regions are merged into their neighbours, and the value is per entry.
"""

from __future__ import annotations

import pandas as pd

import possession_values

#: Which third of the ice each entry is filed in, by entry type.
ENTRY_PLACEMENT = {
    "Carried": {"low": "O24", "middle": "O25", "high": "O26"},
    "Played": {"low": "O24", "middle": "O25", "high": "O26"},
    "Dumped": {"low": "N01", "middle": "N02", "high": "N03"},
}

#: The neutral zone's own bands, which are the three equal strips the rink's
#: zone file uses across the middle of the ice.
BAND_EDGE = 17.5

#: The entry types the slicer offers, in order.
ENTRY_TYPES = ["Dumped", "Carried", "Played"]


def y_band(y: float) -> str:
    """Which third of the ice a point is in: low, middle or high."""
    if y < -BAND_EDGE:
        return "low"
    if y > BAND_EDGE:
        return "high"
    return "middle"


def population(frame: pd.DataFrame) -> pd.DataFrame:
    """International games, at 5v5, and only zone entries."""
    situations = possession_values.international_five_on_five(frame)
    return situations[situations["event"] == "Zone Entry"]


def filed(frame: pd.DataFrame) -> pd.DataFrame:
    """The entries with each one moved into the box its type belongs in."""
    filed = frame.copy()
    filed["boxid"] = [
        ENTRY_PLACEMENT[row.detail_1][y_band(row.y1)]
        for row in frame.itertuples(index=False)
    ]
    return filed


#: How a box that no entry can be filed into is drawn.  It is not an empty
#: region, it is simply not where this question applies.
OUTSIDE_FILL = "rgba(255,255,255,0.06)"


def build(frame: pd.DataFrame, zones, metric: str) -> dict:
    """Everything the page needs to draw the map.

    Only the boxes the entries were actually filed into take part.  Leaving the
    rest of the rink in would be wrong twice over: those boxes hold no entries by
    construction, so they would read as thin regions and get merged into the
    real ones, dragging the whole map into one blob.

    Nothing is mirrored and nothing is merged.  The entries are filed by type
    rather than by coordinate, so each box already means something on its own -
    a carry up the left wall and a carry up the right wall are different things
    here, and folding them together would hide exactly the comparison the page
    exists to make.
    """
    used = sorted(set(frame["boxid"]))
    subset = [zone for zone in zones if zone["id"] in used]
    built = possession_values.build(frame, subset, metric, combine=False)

    drawn = {zone["id"]: zone for zone in built["zones"]}
    return {
        **built,
        "zones": [
            drawn.get(
                zone["id"],
                {**zone, "fill": OUTSIDE_FILL, "label": None, "region": ""},
            )
            for zone in zones
        ],
    }
