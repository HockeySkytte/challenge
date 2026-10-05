"""Work out a possession value for each part of the rink.

The question this answers: when a team starts a possession in a given part of
the rink, what does it get out of the next 15 seconds?

Every possession start already carries a 15-second window - the columns
``CF_15``, ``FF_15`` and the rest, counted in step 10 of the cleaning.  This
module adds up the difference between the two teams' columns for the windows
that began in each part of the rink, and puts the answer on a map.

Three things keep the map honest about how little data there is:

* Only the international games at 5v5, and only possession starts.
* **The rink is mirrored across the centre line.**  The ice is symmetric left to
  right, so a box and its twin on the other side are counted as one region,
  roughly doubling the data behind each part of the map.  A box straddling the
  centre line is its own twin.
* **Thin regions are merged into their neighbours.**  A region with fewer than
  ``MIN_WINDOWS`` windows behind it is folded into whichever region is nearest,
  and that repeats until every region has enough to say something.  The corners
  end up as larger blobs; the busy areas keep their detail.
"""

from __future__ import annotations

import pandas as pd

import rink_geometry

#: The two columns each metric takes the difference of.
METRICS = {
    "Corsi": ("CF_15", "CA_15"),
    "Fenwick": ("FF_15", "FA_15"),
    "Shots": ("SF_15", "SA_15"),
    "Goals": ("GF_15", "GA_15"),
    "xG_All_Shots": ("xGF_15", "xGA_15"),
}

#: How many decimals each metric needs to say anything.  A goal per possession
#: is a small number, so two decimals would round most regions to zero.
METRIC_DECIMALS = {
    "Corsi": 2,
    "Fenwick": 2,
    "Shots": 2,
    "Goals": 3,
    "xG_All_Shots": 3,
}

#: The events that count as a possession starting, in the order the slicer
#: offers them.
POSSESSION_STARTS = ["Puck Recovery", "Takeaway", "Reception"]

#: How many windows a region needs behind it before it is shown on its own.
MIN_WINDOWS = 100

#: Two boxes are twins when their edges line up this closely.
TOLERANCE = 0.01

#: The two college teams.  Everything else in the file is an international game.
NCAA_TEAMS = ["St. Lawrence Saints", "Clarkson Golden Knights"]


def international_five_on_five(frame: pd.DataFrame) -> pd.DataFrame:
    """International games at 5v5.

    The situations both value pages look at.  The college games are a different
    level of hockey and the special teams are a different game altogether, so
    neither belongs in a per-situation value.
    """
    college = frame["home_team"].isin(NCAA_TEAMS) | frame["away_team"].isin(NCAA_TEAMS)
    return frame[~college & (frame["strength_state"] == "5v5")]


def population(frame: pd.DataFrame) -> pd.DataFrame:
    """The rows the Possession Values page is about.

    International games at 5v5, and only the events where a player's possession
    starts.
    """
    situations = international_five_on_five(frame)
    return situations[situations["possession_start"] == 1]


def mirror_partners(zones) -> dict[str, str]:
    """Each box paired with the box that mirrors it across the centre line.

    Two boxes are twins when they cover the same stretch of x and their y ranges
    are the same but flipped: a box running from -30 to -10 has its twin running
    from 10 to 30.
    """
    partners = {}
    for zone in zones:
        x0, x1, y0, y1 = zone["bounds"]
        for other in zones:
            ox0, ox1, oy0, oy1 = other["bounds"]
            if (
                abs(ox0 - x0) < TOLERANCE
                and abs(ox1 - x1) < TOLERANCE
                and abs(oy0 + y1) < TOLERANCE
                and abs(oy1 + y0) < TOLERANCE
            ):
                partners[zone["id"]] = other["id"]
                break
    return partners


def mirrored_regions(zones) -> list[list[str]]:
    """The boxes grouped into mirrored regions, each region listed once."""
    partners = mirror_partners(zones)
    seen = set()
    regions = []
    for zone in zones:
        pair = tuple(sorted({zone["id"], partners[zone["id"]]}))
        if pair in seen:
            continue
        seen.add(pair)
        regions.append(list(pair))
    return regions


def _centre(boxes: list[str], centres: dict) -> tuple[float, float]:
    """The middle of a region, for placing its label and measuring distances."""
    xs = [centres[box][0] for box in boxes]
    ys = [centres[box][1] for box in boxes]
    return sum(xs) / len(xs), sum(ys) / len(ys)


def merge_thin_regions(
    regions: list[list[str]], windows: dict[str, int], centres: dict, minimum: int
) -> list[dict]:
    """Fold thin regions into their nearest neighbour until none is thin.

    The thinnest region goes first, into whichever region's middle is closest.
    Repeating that leaves the busy parts of the rink as they were and grows the
    quiet parts into blobs big enough to mean something.
    """
    working = [
        {"boxes": sorted(boxes), "windows": sum(windows.get(b, 0) for b in boxes)}
        for boxes in regions
    ]

    while len(working) > 1:
        thinnest = min(working, key=lambda region: region["windows"])
        if thinnest["windows"] >= minimum:
            break

        others = [region for region in working if region is not thinnest]
        here = _centre(thinnest["boxes"], centres)
        nearest = min(
            others,
            key=lambda region: _gap(here, _centre(region["boxes"], centres)),
        )
        nearest["boxes"] = sorted(nearest["boxes"] + thinnest["boxes"])
        nearest["windows"] += thinnest["windows"]
        working.remove(thinnest)

    for region in working:
        region["id"] = "+".join(region["boxes"])
        region["cx"], region["cy"] = _centre(region["boxes"], centres)
    return working


def _gap(a: tuple[float, float], b: tuple[float, float]) -> float:
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5


def region_values(frame: pd.DataFrame, regions: list[dict], metric: str) -> dict[str, float]:
    """The value **per possession** for each region.

    The for column minus the against column, added up over every window that
    opened in the region, then divided by how many windows there were.  The
    division is the point: without it a busy part of the rink would look extreme
    simply because more possessions started there, and the map would be a picture
    of where play happens rather than of what a possession is worth.

    A window with no shots adds nothing to either side, so it pulls the average
    towards zero - which is right, because most possessions do not produce a
    shot.
    """
    for_column, against_column = METRICS[metric]
    totals = frame.groupby("boxid")[[for_column, against_column]].sum()

    values = {}
    for region in regions:
        present = [box for box in region["boxes"] if box in totals.index]
        if not present or not region["windows"]:
            values[region["id"]] = 0.0
            continue
        rows = totals.loc[present]
        difference = float(rows[for_column].sum() - rows[against_column].sum())
        values[region["id"]] = difference / region["windows"]
    return values


def plain_regions(zones, windows: dict[str, int]) -> list[dict]:
    """Every box on its own - no mirroring and no merging."""
    return [
        {
            "boxes": [zone["id"]],
            "windows": int(windows.get(zone["id"], 0)),
            "id": zone["id"],
            "cx": zone["cx"],
            "cy": zone["cy"],
        }
        for zone in zones
    ]


def build(
    frame: pd.DataFrame,
    zones,
    metric: str,
    minimum: int = MIN_WINDOWS,
    combine: bool = True,
) -> dict:
    """Everything the page needs to draw the map.

    Returns the zones with their fill worked out, the regions with their value
    and label position, and how far the values run from zero.

    With ``combine`` on, the rink is mirrored across the centre line and any
    region still holding fewer than ``minimum`` windows is merged into its
    nearest neighbour.  Turning it off leaves every box on its own, which is
    what the Zone Entries page wants: its entries are filed by type rather than
    by coordinate, so each box already means something on its own and combining
    them would blur the comparison the page is for.
    """
    centres = {zone["id"]: (zone["cx"], zone["cy"]) for zone in zones}
    windows = frame["boxid"].value_counts().to_dict()

    if combine:
        regions = merge_thin_regions(
            mirrored_regions(zones), windows, centres, minimum
        )
    else:
        regions = plain_regions(zones, windows)

    values = region_values(frame, regions, metric)
    extent = max((abs(value) for value in values.values()), default=0.0)

    box_region = {}
    for region in regions:
        region["value"] = values[region["id"]]
        # A signed label, so the direction reads off the map as well as the
        # colour does.
        decimals = METRIC_DECIMALS[metric]
        region["label"] = f"{region['value']:+,.{decimals}f}"
        for box in region["boxes"]:
            box_region[box] = region["id"]

    fills = {
        region["id"]: rink_geometry.value_colour(region["value"], extent)
        for region in regions
    }
    # Every box is labelled, including the ones inside a merged or mirrored
    # region: each carries its region's value, so a merged region reads as one
    # block and the map has no unlabelled holes in it.
    by_region = {region["id"]: region for region in regions}
    drawn = []
    for zone in zones:
        region = by_region[box_region[zone["id"]]]
        drawn.append(
            {
                **zone,
                "fill": fills[region["id"]],
                "label": region["label"],
                "region": region["id"],
                "region_windows": region["windows"],
            }
        )

    return {
        "zones": drawn,
        "regions": regions,
        "extent": extent,
        "decimals": METRIC_DECIMALS[metric],
        "merged": sum(1 for region in regions if len(region["boxes"]) > 2),
    }
