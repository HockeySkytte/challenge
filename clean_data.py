"""
Clean the Big Data Cup event data.

    python clean_data.py

Reads:   data/olympic_womens_dataset.csv   the raw file - never modified
Writes:  data/cleaned/events.csv           the cleaned file

The cleaning happens in numbered steps, applied in order.  Each step is one
function below, and each one explains what it does and why.  Run the script and
it prints what every step changed, so you can follow along.

    Step 1   Clean the teams.
    Step 2   Rename Period, and replace Clock with time_elapsed.
    Step 3   Add venue, strength_state and score_state.
    Step 4   Give shots their outcome, add a Reception under every Play, and
             rename the remaining columns.
    Step 5   Rename the coordinates, and centre them on 0.
    Step 6   Add boxid and boxid_2.
    Step 7   Mark where a player's possession starts.
    Step 8   Build the xG models, and score the shots.
    Step 9   Add the competition.
    Step 10  Count what happens in the 15 seconds after an opening.

Steps are added here as we go, and the list of steps in main() is what decides
the order they run in.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import StratifiedKFold, cross_val_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

# ---------------------------------------------------------------------------
# Where the files live, relative to this script.
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent
RAW_CSV = BASE_DIR / "data" / "olympic_womens_dataset.csv"
CLEAN_CSV = BASE_DIR / "data" / "cleaned" / "events.csv"

#: A record of the two xG models - what they were trained on, how they scored and
#: what weights they came out with.  Written so the app can show the models
#: without training them again.
MODELS_JSON = BASE_DIR / "data" / "cleaned" / "xg_models.json"
ZONES_GEOJSON = BASE_DIR / "data" / "HockeyRinkZones.geojson"

#: One period is 20 minutes long, which is 1,200 seconds.
PERIOD_SECONDS = 20 * 60


# ===========================================================================
# STEP 1 - Clean the teams
# ===========================================================================

#: The raw file's team column names, and the lowercase name to use instead.
TEAM_COLUMN_RENAMES = {
    "Home Team": "home_team",
    "Away Team": "away_team",
    "Team": "team",
}

#: The Olympic teams in the file, and the nation name to use for each one.
#: "Olympic Athletes from Russia" was the official name for the Russian team at
#: those Games, so it does not shorten to a nation on its own.
OLYMPIC_TEAM_NAMES = {
    "Olympic (Women) - Canada": "Canada",
    "Olympic (Women) - United States": "United States",
    "Olympic (Women) - Finland": "Finland",
    "Olympic (Women) - Olympic Athletes from Russia": "Russia",
}


def step_1_clean_teams(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """Clean the teams.

    Two things happen here, and both are about team names.

    1. The three team columns are renamed to lowercase.  The raw file uses
       spaces and capitals, so code has to be written df["Home Team"].  That is
       easy to mistype, and df["home_team"] is the Python convention.

           "Home Team"  ->  "home_team"
           "Away Team"  ->  "away_team"
           "Team"       ->  "team"

    2. Olympic team names are shortened to just the nation.  "Olympic (Women)"
       is the same for every Olympic team, so it tells you nothing, and it
       makes a table or a chart harder to read.

           "Olympic (Women) - Canada"                        ->  "Canada"
           "Olympic (Women) - United States"                 ->  "United States"
           "Olympic (Women) - Finland"                       ->  "Finland"
           "Olympic (Women) - Olympic Athletes from Russia"  ->  "Russia"

       The two NCAA teams keep their full names, because that is the only name
       they have: "St. Lawrence Saints" and "Clarkson Golden Knights".

    All three team columns are updated, so a nation is spelled the same way
    whether it appears as the home team, the away team or the event team.
    """
    df = df.rename(columns=TEAM_COLUMN_RENAMES)

    notes = [f'"{old}"  ->  "{new}"' for old, new in TEAM_COLUMN_RENAMES.items()]

    # How many events each team is credited with, for the run output.
    events_per_team = df["team"].value_counts()

    for column in TEAM_COLUMN_RENAMES.values():
        df[column] = df[column].replace(OLYMPIC_TEAM_NAMES)

    for raw_name, nation in OLYMPIC_TEAM_NAMES.items():
        notes.append(
            f'"{raw_name}"  ->  "{nation}"   ({events_per_team[raw_name]:,} events)'
        )

    notes.append('"St. Lawrence Saints" / "Clarkson Golden Knights"  ->  unchanged')

    return df, notes


# ===========================================================================
# STEP 2 - Rename Period, and replace Clock with time_elapsed
# ===========================================================================

def _clock_to_seconds(clock: str) -> int:
    """Turn a "mm:ss" clock reading into a number of seconds.

    The file writes the clock as text, and it counts DOWN, so "19:34" means
    1,174 seconds are left on the period clock.
    """
    minutes, seconds = clock.split(":")
    return int(minutes) * 60 + int(seconds)


def step_2_period_and_time_elapsed(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """Rename Period to period, and replace Clock with time_elapsed.

    Why: "Period" has a capital for no good reason, and "Clock" counts DOWN from
    20:00 and then starts again from 20:00 at the next period.  A clock that
    counts down and resets cannot be used to ask what happened next, because a
    time in period 1 cannot be compared with a time in period 3.

    time_elapsed is the number of seconds since the game started, so it only
    ever goes up:

        time_elapsed = (period - 1) x 1,200  +  seconds played in the period

    1,200 is 20 minutes - the length of one period.  So every period adds 1,200
    to the number, and nothing resets until the next game.

    The worked example below is a game whose periods each run 20:00 down to
    0:00, which is what this file contains:

        Period 1, Clock 20:00  ->  period 1, time_elapsed     0
        Period 1, Clock  0:00  ->  period 1, time_elapsed 1,200
        Period 2, Clock 20:00  ->  period 2, time_elapsed 1,200
        Period 2, Clock 10:00  ->  period 2, time_elapsed 1,800
        Period 3, Clock  5:00  ->  period 3, time_elapsed 2,700

    Because Clock counts down, "seconds played in the period" is 1,200 minus
    the clock reading.  The Clock column is dropped afterwards: time_elapsed
    replaces it, and keeping both would just be two ways of saying the same
    thing.
    """
    df = df.rename(columns={"Period": "period"})

    seconds_left = df["Clock"].map(_clock_to_seconds)
    seconds_played = PERIOD_SECONDS - seconds_left
    df["time_elapsed"] = (df["period"] - 1) * PERIOD_SECONDS + seconds_played

    df = df.drop(columns=["Clock"])

    # Put time_elapsed where Clock used to sit, so the columns keep a sensible
    # order instead of the new column being tacked on the end.
    columns = [name for name in df.columns if name != "time_elapsed"]
    columns.insert(columns.index("period") + 1, "time_elapsed")
    df = df[columns]

    notes = [
        '"Period"  ->  "period"',
        '"Clock"   ->  "time_elapsed"   (seconds since the start of the game)',
        f"runs from {int(df['time_elapsed'].min()):,} to "
        f"{int(df['time_elapsed'].max()):,} seconds across the file",
    ]

    return df, notes


# ===========================================================================
# STEP 3 - Add venue, strength_state and score_state
# ===========================================================================

#: The four raw columns that only make sense from the home team's point of view.
#: Step 3 replaces them with three columns written from the eventing team's
#: point of view, and then drops them.
HOME_ONLY_COLUMNS = [
    "Home Team Skaters",
    "Away Team Skaters",
    "Home Team Goals",
    "Away Team Goals",
]


def step_3_venue_strength_and_score(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """Add venue, strength_state and score_state, and drop the columns they replace.

    Every event is taken by one team, and the file says which one in the "team"
    column.  The three new columns describe the situation from that team's point
    of view, so a single value means the same thing for both teams.  Without
    them you have to check which team you are looking at before you can read the
    numbers, and "5v4" could mean either a power play or a penalty kill.

    1. venue - did the home team or the away team take the event?

           team is the home team  ->  "Home"
           team is the away team  ->  "Away"
           otherwise              ->  ""

    2. strength_state - how many skaters each side had, eventing team first.

           venue "Home"  ->  "Home Team Skaters" v "Away Team Skaters"
           venue "Away"  ->  "Away Team Skaters" v "Home Team Skaters"
           otherwise     ->  ""

       So "5v4" always means the team that took the event had five skaters and
       the other team had four - a power play for the eventing team, whether
       that team is the home side or the away side.

    3. score_state - the goal difference from the eventing team's point of view.

           venue "Home"  ->  Home Team Goals - Away Team Goals
           venue "Away"  ->  Away Team Goals - Home Team Goals
           otherwise     ->  blank

       So +1 always means the team that took the event was winning by one, and
       -1 always means it was losing by one.

    One detail about score_state: the raw goal columns hold the score as it
    stood when the event was logged, so at a Goal they are the score BEFORE that
    goal.  The first goal of every game therefore reads 0, not 1.

    The four raw columns these are built from are then dropped, because on their
    own they only read correctly for the home team:

        "Home Team Skaters", "Away Team Skaters",
        "Home Team Goals",  "Away Team Goals"
    """
    is_home = df["team"] == df["home_team"]
    is_away = df["team"] == df["away_team"]

    # 1. venue
    venue = pd.Series("", index=df.index, dtype="object")
    venue[is_home] = "Home"
    venue[is_away] = "Away"
    df["venue"] = venue

    # 2. strength_state - the eventing team's skaters always come first.
    strength = pd.Series("", index=df.index, dtype="object")
    strength[is_home] = (
        df.loc[is_home, "Home Team Skaters"].astype(str)
        + "v"
        + df.loc[is_home, "Away Team Skaters"].astype(str)
    )
    strength[is_away] = (
        df.loc[is_away, "Away Team Skaters"].astype(str)
        + "v"
        + df.loc[is_away, "Home Team Skaters"].astype(str)
    )
    df["strength_state"] = strength

    # 3. score_state - the eventing team's goal difference.
    #    Int64 rather than the usual int, so the "otherwise" case is a real blank
    #    and not a zero, which would read as an even score.
    score = pd.Series(pd.NA, index=df.index, dtype="Int64")
    score[is_home] = df.loc[is_home, "Home Team Goals"] - df.loc[is_home, "Away Team Goals"]
    score[is_away] = df.loc[is_away, "Away Team Goals"] - df.loc[is_away, "Home Team Goals"]
    df["score_state"] = score

    df = df.drop(columns=HOME_ONLY_COLUMNS)

    # Keep the three new columns together, directly after "team".
    new_columns = ["venue", "strength_state", "score_state"]
    columns = [name for name in df.columns if name not in new_columns]
    columns[columns.index("team") + 1 : columns.index("team") + 1] = new_columns
    df = df[columns]

    removed = ", ".join(f'"{name}"' for name in HOME_ONLY_COLUMNS)
    strengths = ", ".join(sorted(df["strength_state"].unique()))

    notes = [
        f'added "venue"           Home {int(is_home.sum()):,} / '
        f"Away {int(is_away.sum()):,} / blank {int((~is_home & ~is_away).sum()):,}",
        f'added "strength_state"  {strengths}',
        f'added "score_state"     {int(df["score_state"].min())} to '
        f'{int(df["score_state"].max())}',
        f"removed {removed}",
    ]

    return df, notes


# ===========================================================================
# STEP 4 - Shot outcomes, receptions and the remaining column names
# ===========================================================================

#: What a "Shot" event should be called, based on its "Detail 2" value.
SHOT_OUTCOME_NAMES = {
    "On Net": "Shot on Net",
    "Missed": "Missed Shot",
    "Blocked": "Blocked Shot",
}

#: The remaining raw column names, and the lowercase name to use instead.
EVENT_COLUMN_RENAMES = {
    "Player": "player",
    "Player 2": "player_2",
    "Event": "event",
    "Detail 1": "detail_1",
    "Detail 2": "detail_2",
    "Detail 3": "detail_3",
    "Detail 4": "detail_4",
}


def step_4_shot_outcomes_receptions_and_names(
    df: pd.DataFrame,
) -> tuple[pd.DataFrame, list[str]]:
    """Give shots their outcome, add a Reception under every Play and rename the rest.

    Three things happen here.

    1. A shot says what happened to it.
       Every shot is logged with the same event name, "Shot", and the outcome
       sits over in "Detail 2".  "Shot" on its own tells you nothing about
       whether the puck hit the net, missed the net or was blocked, so the
       outcome goes into the event name:

           "Detail 2" On Net   ->  "Shot on Net"
           "Detail 2" Missed   ->  "Missed Shot"
           "Detail 2" Blocked  ->  "Blocked Shot"

       Goals keep the name "Goal".  A goal is logged as "On Net" as well, but a
       goal is not a shot that failed to go in, so it keeps its own name.

    2. Every successful pass gets a Reception underneath it.
       A "Play" is a pass that reached its target.  The file records the passer
       in "Player" and the receiver in "Player 2", but only the passer gets a
       row - so the receiver is invisible.  You cannot count how many passes a
       player received, or where they received them.

       For each Play, a new "Reception" row is added directly underneath it.  It
       copies the Play's period, time_elapsed, team, venue, strength_state,
       score_state and details, and changes only:

           "Event"           ->  "Reception"
           "Player"          ->  the Play's "Player 2"       (the receiver)
           "Player 2"        ->  blank
           "X Coordinate"    ->  the Play's "X Coordinate 2" (where it arrived)
           "Y Coordinate"    ->  the Play's "Y Coordinate 2"
           "X Coordinate 2"  ->  blank
           "Y Coordinate 2"  ->  blank

       So a Reception carries one player and one location: the receiver, and
       where they took the puck in.  That mirrors a Play, which carries the
       passer and where they let it go.

    3. The remaining column names are made lowercase.
       "Player", "Player 2", "Event" and "Detail 1" to "Detail 4" become
       "player", "player_2", "event" and "detail_1" to "detail_4", matching the
       team columns from step 1.

    The coordinate columns keep their names for now; they are renamed later.
    """
    # --- 1. shots say what happened to them ---------------------------------
    is_shot = df["Event"] == "Shot"
    for detail, name in SHOT_OUTCOME_NAMES.items():
        df.loc[is_shot & (df["Detail 2"] == detail), "Event"] = name

    # --- 2. a Reception underneath every Play -------------------------------
    # A throwaway column remembering each row's position, so the new rows can be
    # slotted in underneath their Play and everything stays in order.
    df = df.copy()
    df["_order"] = range(len(df))

    receptions = df.loc[df["Event"] == "Play"].copy()
    receptions["Event"] = "Reception"
    receptions["Player"] = receptions["Player 2"]
    receptions["Player 2"] = ""
    receptions["X Coordinate"] = receptions["X Coordinate 2"]
    receptions["Y Coordinate"] = receptions["Y Coordinate 2"]
    # float("nan") rather than pd.NA, so the column stays a number column with a
    # blank in it instead of turning into a column of mixed types.
    receptions["X Coordinate 2"] = float("nan")
    receptions["Y Coordinate 2"] = float("nan")
    # Half a place later, so it lands between the Play and whatever follows it.
    receptions["_order"] = receptions["_order"] + 0.5

    df = (
        pd.concat([df, receptions], ignore_index=True)
        .sort_values("_order", kind="stable")
        .drop(columns=["_order"])
        .reset_index(drop=True)
    )

    # --- 3. the remaining column names --------------------------------------
    df = df.rename(columns=EVENT_COLUMN_RENAMES)

    shot_counts = " / ".join(
        f'"{name}" {int((df["event"] == name).sum()):,}'
        for name in SHOT_OUTCOME_NAMES.values()
    )
    notes = [
        f"shots renamed    {shot_counts}",
        f"receptions added {len(receptions):,} new rows, one underneath every Play",
    ]
    notes += [
        f'columns renamed  "{old}"  ->  "{new}"'
        for old, new in EVENT_COLUMN_RENAMES.items()
    ]

    return df, notes


# ===========================================================================
# STEP 5 - Rename the coordinates, and centre them on 0
# ===========================================================================

#: The four coordinate columns, and the name each one gets.
COORDINATE_RENAMES = {
    "X Coordinate": "x1",
    "Y Coordinate": "y1",
    "X Coordinate 2": "x2",
    "Y Coordinate 2": "y2",
}

#: How far to shift each axis so the middle of the rink becomes 0.
#: The rink is 200 feet long and 85 feet wide, so half of each is 100 and 42.5.
X_CENTRE = 100.0
Y_CENTRE = 42.5


def step_5_centre_coordinates(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """Rename the coordinate columns to x1, y1, x2, y2, and centre them on 0.

    Two things happen here.

    1. The coordinate columns are renamed.

           "X Coordinate"    ->  "x1"      where the event happened
           "Y Coordinate"    ->  "y1"
           "X Coordinate 2"  ->  "x2"      where a pass was aimed
           "Y Coordinate 2"  ->  "y2"

       A pass has two locations: where it was let go (x1, y1) and where it
       arrived (x2, y2).  Every other event has only the first, and leaves the
       second blank.

    2. The coordinates are moved so the middle of the rink is 0.
       The raw file measures from one end of the rink, so x runs 0 to 200 and y
       runs 0 to 85.  Taking away half of each puts the centre line at x = 0 and
       the middle of the ice at y = 0:

           x1 = "X Coordinate" - 100     -100 (own goal line) to +100 (target goal line)
           y1 = "Y Coordinate" - 42.5    -42.5 (one side)     to +42.5 (other side)

       The same shift is applied to x2 and y2.

       This is the frame the bundled HockeyRinkZones.geojson already uses, so
       events and rink zones line up without any further adjustment.

       Positive x is the offensive end.  The raw file already writes every
       coordinate from the point of view of the team that took the event, so a
       shot has a positive x whichever team took it.
    """
    df = df.rename(columns=COORDINATE_RENAMES)

    df["x1"] = df["x1"] - X_CENTRE
    df["y1"] = df["y1"] - Y_CENTRE
    df["x2"] = df["x2"] - X_CENTRE
    df["y2"] = df["y2"] - Y_CENTRE

    notes = [f'renamed  "{old}"  ->  "{new}"' for old, new in COORDINATE_RENAMES.items()]
    for column in COORDINATE_RENAMES.values():
        notes.append(
            f"centred  {column}  {df[column].min():>7.1f} to {df[column].max():>7.1f}"
        )

    return df, notes


# ===========================================================================
# STEP 6 - Add boxid and boxid_2
# ===========================================================================

#: The blue lines, where the neutral zone meets each end zone.
BLUE_LINE_X = 25.0

#: A hair's width.  Used to put a point that sits exactly on a line onto one
#: side of it, so the answer does not depend on which way a test happens to lean.
HAIR = 1e-6


def _load_rink_zones(path: Path) -> list[tuple[str, np.ndarray]]:
    """Read the rink zone polygons, as a list of (zone id, corner points)."""
    data = json.loads(path.read_text(encoding="utf-8"))
    zones = []
    for feature in data["features"]:
        corners = feature["geometry"]["coordinates"][0]
        zones.append((feature["properties"]["id"], np.array(corners, dtype=float)))
    return zones


def _points_in_polygon(px: np.ndarray, py: np.ndarray, corners: np.ndarray) -> np.ndarray:
    """Which of the points (px, py) fall inside this polygon?

    The usual ray casting test: draw a line out to the right from each point and
    count how many edges of the polygon it crosses.  An odd number means the
    point is inside.  Doing it with numpy means the whole column is tested at
    once, rather than looping over 31,000 rows.
    """
    inside = np.zeros(len(px), dtype=bool)
    x = corners[:, 0]
    y = corners[:, 1]
    j = len(corners) - 1

    with np.errstate(divide="ignore", invalid="ignore"):
        for i in range(len(corners)):
            # An edge only counts if it spans the point's height.
            spans = (y[i] > py) != (y[j] > py)
            # ...and if the point sits to the left of where the edge crosses.
            left_of = px < (x[j] - x[i]) * (py - y[i]) / (y[j] - y[i]) + x[i]
            inside ^= spans & left_of
            j = i

    return inside


def _distance_to_polygon(px: np.ndarray, py: np.ndarray, corners: np.ndarray) -> np.ndarray:
    """How far each point is from the outline of this polygon.

    The distance to a shape is the shortest distance to any of its edges, so
    this walks the edges and keeps the smallest.  Only used for the handful of
    points that fall in no box at all.
    """
    best = np.full(len(px), np.inf)
    x = corners[:, 0]
    y = corners[:, 1]
    j = len(corners) - 1

    for i in range(len(corners)):
        edge_x = x[i] - x[j]
        edge_y = y[i] - y[j]
        length_sq = edge_x * edge_x + edge_y * edge_y
        if length_sq == 0:
            distance = np.hypot(px - x[j], py - y[j])
        else:
            # Where along the edge the point is closest to, kept between the
            # two ends so it measures the edge and not an endless line.
            along = np.clip(
                ((px - x[j]) * edge_x + (py - y[j]) * edge_y) / length_sq, 0.0, 1.0
            )
            distance = np.hypot(px - (x[j] + along * edge_x), py - (y[j] + along * edge_y))
        best = np.minimum(best, distance)
        j = i

    return best


def _box_ids(
    px: np.ndarray, py: np.ndarray, zones: list[tuple[str, np.ndarray]]
) -> tuple[np.ndarray, int]:
    """The zone id each point falls in, plus how many had to be snapped.

    A blank coordinate (a pass's second location on a non-pass event) matches
    nothing, so it comes back blank.
    """
    # A point sitting exactly on a blue line belongs to the end zone, not the
    # middle: the blue line is where the end zone starts.  Nudging it a hair
    # further from centre ice puts it on the right side for the lookup below.
    on_blue_line = np.abs(px) == BLUE_LINE_X
    px = np.where(on_blue_line, np.sign(px) * (BLUE_LINE_X + HAIR), px)

    ids = np.full(len(px), "", dtype=object)
    in_a_box = np.zeros(len(px), dtype=bool)
    for zone_id, corners in zones:
        inside = _points_in_polygon(px, py, corners)
        ids[inside] = zone_id
        in_a_box |= inside

    # Anything in no box gets the nearest one.  Events are logged right on the
    # boards and out in the rounded corners, where a point can land just outside
    # the drawn rink.  A puck is never off the ice, so the closest box is the
    # right answer - and on the boards that is the box along the boards.
    loose = ~in_a_box & np.isfinite(px) & np.isfinite(py)
    if loose.any():
        loose_x, loose_y = px[loose], py[loose]
        nearest = np.full(len(loose_x), "", dtype=object)
        shortest = np.full(len(loose_x), np.inf)
        for zone_id, corners in zones:
            distance = _distance_to_polygon(loose_x, loose_y, corners)
            closer = distance < shortest
            shortest[closer] = distance[closer]
            nearest[closer] = zone_id
        ids[loose] = nearest

    return ids, int(loose.sum())


def step_6_box_ids(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """Add boxid and boxid_2 - which rink zone each coordinate falls in.

    The file that came with the dataset, HockeyRinkZones.geojson, divides the
    rink into 58 boxes: 26 in each end and 6 across the middle.  Each one has a
    name, and the name says which part of the rink it covers:

        D01 to D26   the defensive end
        N01 to N06   the neutral zone, between the blue lines
        O01 to O26   the offensive end

    Step 5 put the coordinates into the same frame as those boxes, so finding
    the box is a matter of asking which one each point falls inside.

        boxid     the box (x1, y1) falls in - where the event happened
        boxid_2   the box (x2, y2) falls in - where a pass arrived

    Only passes have a second location, so boxid_2 is blank on every other
    event.

    Two points on the edges need a rule, because a point on a line could
    reasonably go either side:

    * An event exactly on a blue line counts as being in the end zone, not the
      neutral zone.  The blue line is where the end zone begins.

    * An event that lands outside every box is snapped to the nearest one.
      Events are logged right on the boards and out in the rounded corners,
      where a point can sit just outside the drawn rink.  A puck is never off
      the ice, so the closest box is the right answer - and for an event on the
      boards, that is the box along the boards.

    Because the coordinates are written from the eventing team's point of view,
    "O" always means the end that team is attacking, whichever team it is.
    """
    zones = _load_rink_zones(ZONES_GEOJSON)

    df["boxid"], snapped = _box_ids(df["x1"].to_numpy(), df["y1"].to_numpy(), zones)
    df["boxid_2"], snapped_2 = _box_ids(df["x2"].to_numpy(), df["y2"].to_numpy(), zones)

    # Keep each box next to the coordinates it was worked out from.
    columns = [name for name in df.columns if name not in ("boxid", "boxid_2")]
    columns.insert(columns.index("y1") + 1, "boxid")
    columns.insert(columns.index("y2") + 1, "boxid_2")
    df = df[columns]

    used = df.loc[df["boxid"] != "", "boxid"].nunique()
    used_2 = df.loc[df["boxid_2"] != "", "boxid_2"].nunique()
    blank = int((df["boxid"] == "").sum())
    blank_2 = int((df["boxid_2"] == "").sum())

    notes = [
        f'added "boxid"    {used} of {len(zones)} boxes used, '
        f"{snapped} rows snapped to the nearest box, {blank} blank",
        f'added "boxid_2"  {used_2} of {len(zones)} boxes used, '
        f"{snapped_2} rows snapped to the nearest box, {blank_2:,} blank "
        "(no second coordinate)",
    ]

    return df, notes


# ===========================================================================
# STEP 7 - Mark where a player's possession starts
# ===========================================================================

#: The events that begin a player's possession of the puck.
POSSESSION_START_EVENTS = ["Puck Recovery", "Reception", "Takeaway"]


def step_7_possession_start(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """Add possession_start - does this event begin a player's possession?

    Three events are the moment a player takes control of the puck:

        Puck Recovery   picking up a loose puck
        Reception       taking in a pass
        Takeaway        winning the puck off an opponent

    Everything else happens to a puck that somebody already has, so it is not
    the start of a possession.  Marking the three gives a simple way to count
    possessions, or to find where on the rink they begin:

        possession_start  1  this event begins a possession
                          0  it does not

    A Reception counts as one, which is only possible because step 4 added a row
    for the player who received each pass - in the raw file the receiver has no
    row of their own.
    """
    df["possession_start"] = df["event"].isin(POSSESSION_START_EVENTS).astype(int)

    # Keep it next to the event it was worked out from.
    columns = [name for name in df.columns if name != "possession_start"]
    columns.insert(columns.index("event") + 1, "possession_start")
    df = df[columns]

    starts = df.loc[df["possession_start"] == 1, "event"].value_counts()
    breakdown = " / ".join(f"{name} {count:,}" for name, count in starts.items())
    notes = [
        f'added "possession_start"  1 for {int(starts.sum()):,} rows: {breakdown}',
        f"                          0 for the other "
        f"{int((df['possession_start'] == 0).sum()):,} rows",
    ]

    return df, notes


# ===========================================================================
# STEP 8 - Build the xG models, and score the shots
# ===========================================================================

#: Where the net is, in the centred coordinates: on the goal line, at the
#: middle of the ice.
NET_X = 89.0
NET_Y = 0.0

#: The goal mouth is 6 feet wide, so its two posts sit 3 feet either side of
#: the middle of the net.
POST_OFFSET = 3.0

#: The two college teams.  Every other fixture in the file is an international
#: game, and those are the games the xG models are built on.
NCAA_TEAMS = ["St. Lawrence Saints", "Clarkson Golden Knights"]

#: Every event that is a shot attempt, and the ones that got as far as the net.
SHOT_ATTEMPT_EVENTS = ["Shot on Net", "Missed Shot", "Blocked Shot", "Goal"]
ON_NET_EVENTS = ["Shot on Net", "Goal"]

#: The only four things the models are allowed to look at.
XG_FEATURES = ["shot_distance", "shot_angle", "traffic", "one_timer"]


def _fit_xg(features: pd.DataFrame, goals: pd.Series):
    """Fit one logistic regression, putting the inputs on the same footing first.

    The four inputs are measured in very different units - feet, degrees, and
    two yes/no flags - so they are standardised before fitting.  Without that,
    the model's built-in penalty would fall hardest on whichever input happens
    to be written in the largest numbers, which is distance.
    """
    model = make_pipeline(StandardScaler(), LogisticRegression(max_iter=1000))
    model.fit(features, goals)
    return model


def _xg_weights(model) -> str:
    """The fitted weights, for the run output."""
    weights = model.named_steps["logisticregression"].coef_[0]
    return " / ".join(f"{name} {weight:+.2f}" for name, weight in zip(XG_FEATURES, weights))


def _model_record(key, column, trained_on, model, features, goals, auc) -> dict:
    """Everything worth keeping about one fitted model."""
    weights = model.named_steps["logisticregression"].coef_[0]
    return {
        "key": key,
        "column": column,
        "trained_on": trained_on,
        "rows": int(len(features)),
        "goals": int(goals.sum()),
        "auc": round(float(auc), 3),
        "weights": [
            {"feature": name, "weight": round(float(weight), 3)}
            for name, weight in zip(XG_FEATURES, weights)
        ],
    }


def step_8_xg_models(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """Add xg_all_shots and xg_on_net, from two logistic regressions.

    Three things happen here.

    1. Work out where each shot was taken from.
       The models never see the raw coordinates.  They see two numbers that
       describe the shooting position:

           shot_distance   how far the puck was from the net, in feet
           shot_angle      how much of the goal mouth the shooter could see,
                           in degrees

       The net sits on the goal line at (89, 0) and the goal mouth is 6 feet
       wide, so the angle is measured between the two posts.  It is always
       positive: a shot from the left of the ice and a shot from the right,
       otherwise alike, get the same angle.  A shot from the goal line itself,
       or from behind it, is given 0 - from there the net is edge-on or behind
       the shooter, so none of the goal mouth is on show.

    2. Build the two models, with scikit-learn.
       Both are plain logistic regressions, and both are trained only on
       international games at 5v5, because that is the situation they are meant
       to describe:

           xg_all_shots   trained on every shot attempt - on net, missed, blocked and goals
           xg_on_net      trained only on shots that reached the net - saved
                          shots and goals

       A goal is the thing being predicted, so goals are in both.

       The four inputs are shot_distance, shot_angle, whether there was traffic
       in front (detail_3) and whether it was a one-timer (detail_4).

    3. Score the shots.
       Each model is applied to the rows it was trained on and nothing else, so
       a shot from a college game or from a power play has no xG value.  That
       is deliberate: the model was built for international 5v5 play, and saying
       nothing about anything else is more honest than guessing.
    """
    # --- 1. where each shot was taken from ----------------------------------
    is_attempt = df["event"].isin(SHOT_ATTEMPT_EVENTS)

    toward_net_x = NET_X - df["x1"]
    toward_net_y = NET_Y - df["y1"]
    distance = np.hypot(toward_net_x, toward_net_y)

    # The angle between the two posts as seen from the shot, worked out from the
    # two lines that run from the shot to each post.  Using atan2 of their cross
    # and dot products keeps the answer between 0 and 180 degrees and positive
    # on both sides of the ice - a shot from the left and a shot from the right,
    # otherwise alike, get the same angle.
    to_near_post_y = (NET_Y - POST_OFFSET) - df["y1"]
    to_far_post_y = (NET_Y + POST_OFFSET) - df["y1"]

    dot = toward_net_x * toward_net_x + to_near_post_y * to_far_post_y
    cross = toward_net_x * to_far_post_y - to_near_post_y * toward_net_x
    angle = np.degrees(np.abs(np.arctan2(cross, dot)))

    # A shot taken from the goal line or from behind it sees none of the goal
    # mouth - the net is edge-on, or the shot is past it - so it gets an angle
    # of 0 rather than a number worked out from a line of sight that is not
    # really there.
    angle = np.where(df["x1"] >= NET_X, 0.0, angle)

    df["shot_distance"] = np.where(is_attempt, distance, np.nan)
    df["shot_angle"] = np.where(is_attempt, angle, np.nan)

    # --- 2. build the two models --------------------------------------------
    international = ~df["home_team"].isin(NCAA_TEAMS) & ~df["away_team"].isin(NCAA_TEAMS)
    at_five_on_five = df["strength_state"] == "5v5"

    features = pd.DataFrame(
        {
            "shot_distance": df["shot_distance"],
            "shot_angle": df["shot_angle"],
            # detail_3 and detail_4 hold the strings "t" and "f".
            "traffic": (df["detail_3"] == "t").astype(int),
            "one_timer": (df["detail_4"] == "t").astype(int),
        }
    )
    is_goal = df["event"] == "Goal"
    reached_the_net = df["event"].isin(ON_NET_EVENTS)

    trained_on_all = is_attempt & international & at_five_on_five
    trained_on_net = trained_on_all & reached_the_net

    all_shots_model = _fit_xg(features[trained_on_all], is_goal[trained_on_all])
    on_net_model = _fit_xg(features[trained_on_net], is_goal[trained_on_net])

    # --- 3. score the shots the models were built for ------------------------
    df["xg_all_shots"] = np.nan
    df["xg_on_net"] = np.nan
    df.loc[trained_on_all, "xg_all_shots"] = all_shots_model.predict_proba(
        features[trained_on_all]
    )[:, 1]
    df.loc[trained_on_net, "xg_on_net"] = on_net_model.predict_proba(
        features[trained_on_net]
    )[:, 1]

    # Keep the two new position columns next to the coordinates they came from.
    columns = [name for name in df.columns if name not in ("shot_distance", "shot_angle")]
    columns.insert(columns.index("boxid") + 1, "shot_angle")
    columns.insert(columns.index("boxid") + 1, "shot_distance")
    df = df[columns]

    # Cross-validated AUC, so the score is not just the model marking its own
    # homework.  With this few goals it is a rough number, not a verdict.
    folds = StratifiedKFold(n_splits=5, shuffle=True, random_state=0)
    auc_all = cross_val_score(
        all_shots_model, features[trained_on_all], is_goal[trained_on_all],
        cv=folds, scoring="roc_auc",
    ).mean()
    auc_net = cross_val_score(
        on_net_model, features[trained_on_net], is_goal[trained_on_net],
        cv=folds, scoring="roc_auc",
    ).mean()

    # Keep a record of what was built, so the app can show the models - and be
    # honest about how little data went into them - without training them again.
    MODELS_JSON.parent.mkdir(parents=True, exist_ok=True)
    MODELS_JSON.write_text(
        json.dumps(
            {
                "features": XG_FEATURES,
                "models": [
                    _model_record(
                        "all_shots",
                        "xg_all_shots",
                        "Every shot attempt: on net, missed, blocked and goals",
                        all_shots_model,
                        features[trained_on_all],
                        is_goal[trained_on_all],
                        auc_all,
                    ),
                    _model_record(
                        "on_net",
                        "xg_on_net",
                        "Only shots that reached the net: saved shots and goals",
                        on_net_model,
                        features[trained_on_net],
                        is_goal[trained_on_net],
                        auc_net,
                    ),
                ],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    notes = [
        f"shot geometry    distance {df['shot_distance'].min():.1f} to "
        f"{df['shot_distance'].max():.1f} ft, angle {df['shot_angle'].min():.1f} to "
        f"{df['shot_angle'].max():.1f} degrees",
        f"all-shots model  {int(trained_on_all.sum()):,} shots, "
        f"{int(is_goal[trained_on_all].sum())} goals   {_xg_weights(all_shots_model)}",
        f"                 cross-validated AUC {auc_all:.3f}",
        f"on-net model     {int(trained_on_net.sum()):,} shots, "
        f"{int(is_goal[trained_on_net].sum())} goals   {_xg_weights(on_net_model)}",
        f"                 cross-validated AUC {auc_net:.3f}",
        f'added "xg_all_shots"  scored on {int(df["xg_all_shots"].notna().sum()):,} rows',
        f'added "xg_on_net"     scored on {int(df["xg_on_net"].notna().sum()):,} rows',
    ]
    return df, notes


# ===========================================================================
# STEP 9 - Add the competition
# ===========================================================================

#: The competitions in the file, matched on the start of the game date.  Each
#: one sits in its own month, so the date a game was played tells you which it
#: was without any guessing.
COMPETITION_BY_DATE_PREFIX = {
    "2018-02": "Olympic",
    "2018-10": "NCAA",
    "2019-02": "Rivalry Series",
    "2019-04": "World Championship",
}


def step_9_competition(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """Add competition - which tournament each game belongs to.

    The file holds four competitions, and each one sits in its own month:

        Olympic             February 2018   the Olympic tournament
        NCAA                October 2018    the two college games
        Rivalry Series      February 2019   Canada vs United States
        World Championship  April 2019      the World Championship

    A competition belongs to the game, not to the event, so every event in a
    game gets the same value.  The dates are used exactly as the file gives
    them.
    """

    def competition_of(game_date: str) -> str:
        for prefix, name in COMPETITION_BY_DATE_PREFIX.items():
            if game_date.startswith(prefix):
                return name
        return ""

    df["competition"] = df["game_date"].astype(str).map(competition_of)

    # Keep it with the other columns that describe the game rather than the event.
    columns = [name for name in df.columns if name != "competition"]
    columns.insert(columns.index("game_date") + 1, "competition")
    df = df[columns]

    fixtures = df.drop_duplicates(["game_date", "home_team", "away_team"])
    games = fixtures.groupby("competition").size()
    events = df.groupby("competition").size()

    notes = [
        f"{name:<20} {int(games[name])} games, {int(events[name]):,} rows"
        for name in COMPETITION_BY_DATE_PREFIX.values()
    ]

    return df, notes


# ===========================================================================
# STEP 10 - Count what happens in the 15 seconds after an opening
# ===========================================================================

#: How far ahead each window looks, in seconds.
WINDOW_SECONDS = 15

#: Events that open a window, on top of a possession starting.
WINDOW_OPENING_EVENTS = ["Zone Entry"]

#: Shot attempt types, from the widest to the narrowest.
CORSI_EVENTS = ["Shot on Net", "Missed Shot", "Blocked Shot", "Goal"]
FENWICK_EVENTS = ["Shot on Net", "Missed Shot", "Goal"]
ON_NET_EVENTS = ["Shot on Net", "Goal"]

#: The ten columns this step adds, in the order they are written out.
WINDOW_COLUMNS = [
    "CF_15", "CA_15",     # corsi - every shot attempt
    "FF_15", "FA_15",     # fenwick - shot attempts that were not blocked
    "SF_15", "SA_15",     # shots on net
    "GF_15", "GA_15",     # goals
    "xGF_15", "xGA_15",   # expected goals
]


def step_10_possession_windows(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """Count what happens in the 15 seconds after a possession starts or a zone entry.

    Every event that opens a passage of play gets a window: a possession
    starting - a Puck Recovery, a Reception or a Takeaway - or a Zone Entry.

    The window runs 15 seconds, but it stops early in two cases:

    * **A goal, by either team.**  The goal itself still counts, because it
      happened inside the window, and then the window closes: play restarts
      from centre ice, so the passage is over.
    * **The end of the period.**  A window never runs into the next period.

    For each opening, everything is counted twice - once for the team that
    opened it and once for the other team:

        CF_15 / CA_15    corsi     every shot attempt: on net, missed, blocked, goals
        FF_15 / FA_15    fenwick   shot attempts that were not blocked
        SF_15 / SA_15    shots     attempts that reached the net, goals included
        GF_15 / GA_15    goals
        xGF_15 / xGA_15  expected goals, summed over those shot attempts

    "F" is the team that opened the window and "A" is the other one, so CF_15 is
    what that team generated in the next 15 seconds and CA_15 is what it gave
    up.  A goal counts towards corsi, fenwick and shots as well as goals, since
    a goal is a shot attempt that went in.

    Only an opening row has a window, so these columns are blank everywhere
    else.

    xG is only worked out for international 5v5 shots, so a window in a college
    game or on a power play adds up the xG it has and treats the rest as 0.
    """
    event = df["event"].to_numpy()
    team = df["team"].to_numpy()
    period = df["period"].to_numpy()
    clock = df["time_elapsed"].to_numpy()
    # A shot with no xG adds nothing rather than poisoning the whole window.
    expected_goals = df["xg_all_shots"].fillna(0.0).to_numpy()

    is_corsi = np.isin(event, CORSI_EVENTS)
    is_fenwick = np.isin(event, FENWICK_EVENTS)
    is_shot = np.isin(event, ON_NET_EVENTS)
    is_goal = event == "Goal"

    possession_start = df["possession_start"].to_numpy() == 1
    zone_entry = np.isin(event, WINDOW_OPENING_EVENTS)
    opens = possession_start | zone_entry

    size = len(df)
    counts = {name: np.zeros(size) for name in WINDOW_COLUMNS}
    ended_on_goal = 0
    ended_on_period = 0

    # Each game is a run of rows in time order, so a window is found by walking
    # forward from its opening row and stopping at the first edge.
    game_codes, _ = pd.factorize(
        df["game_date"].astype(str) + "|" + df["home_team"] + "|" + df["away_team"]
    )
    starts = np.flatnonzero(np.r_[True, game_codes[1:] != game_codes[:-1]])
    ends = np.r_[starts[1:], size]

    for start, end in zip(starts, ends):
        for i in range(start, end):
            if not opens[i]:
                continue

            limit = clock[i] + WINDOW_SECONDS
            opening_team = team[i]
            opening_period = period[i]

            for j in range(i + 1, end):
                if period[j] != opening_period:
                    ended_on_period += 1
                    break
                if clock[j] > limit:
                    break

                # "F" is the team that opened the window, "A" is the other one.
                side = "F" if team[j] == opening_team else "A"

                if is_corsi[j]:
                    counts["C" + side + "_15"][i] += 1
                    counts["xG" + side + "_15"][i] += expected_goals[j]
                if is_fenwick[j]:
                    counts["F" + side + "_15"][i] += 1
                if is_shot[j]:
                    counts["S" + side + "_15"][i] += 1
                if is_goal[j]:
                    counts["G" + side + "_15"][i] += 1
                    ended_on_goal += 1
                    break

    # Blank on everything that did not open a window.
    for name in WINDOW_COLUMNS:
        counts[name][~opens] = np.nan

    for name in WINDOW_COLUMNS:
        if name.startswith("xG"):
            df[name] = counts[name]
        else:
            # Counts are whole numbers, with a real blank rather than a 0 on the
            # rows that have no window.
            df[name] = pd.array(counts[name], dtype="Int64")

    notes = [
        f"windows          {int(opens.sum()):,} openings "
        f"({int(possession_start.sum()):,} possession starts + "
        f"{int(zone_entry.sum()):,} zone entries)",
        f"cut short        {ended_on_goal:,} ended on a goal, "
        f"{ended_on_period:,} ended at a period end",
        f"totals           CF {int(np.nansum(counts['CF_15'])):,} / "
        f"CA {int(np.nansum(counts['CA_15'])):,} / "
        f"SF {int(np.nansum(counts['SF_15'])):,} / "
        f"SA {int(np.nansum(counts['SA_15'])):,} / "
        f"GF {int(np.nansum(counts['GF_15'])):,} / "
        f"GA {int(np.nansum(counts['GA_15'])):,}",
        f"                 xGF {np.nansum(counts['xGF_15']):.1f} / "
        f"xGA {np.nansum(counts['xGA_15']):.1f}",
        f"added            {', '.join(WINDOW_COLUMNS)}",
    ]

    return df, notes


# ===========================================================================
# Running the steps
# ===========================================================================

#: Every cleaning step, in the order it is applied.
STEPS = [
    step_1_clean_teams,
    step_2_period_and_time_elapsed,
    step_3_venue_strength_and_score,
    step_4_shot_outcomes_receptions_and_names,
    step_5_centre_coordinates,
    step_6_box_ids,
    step_7_possession_start,
    step_8_xg_models,
    step_9_competition,
    step_10_possession_windows,
]


def _step_title(step) -> str:
    """The first line of a step's docstring, used as its heading in the output."""
    return step.__doc__.strip().splitlines()[0].rstrip(".")


def main() -> int:
    if not RAW_CSV.exists():
        print(f"ERROR: raw file not found at {RAW_CSV}", file=sys.stderr)
        return 1

    print(f"Reading  {RAW_CSV.relative_to(BASE_DIR)}")
    df = pd.read_csv(RAW_CSV, low_memory=False)
    print(f"         {len(df):,} rows, {len(df.columns)} columns")
    print()

    for number, step in enumerate(STEPS, start=1):
        print(f"Step {number}  {_step_title(step)}")
        df, notes = step(df)
        for note in notes:
            print(f"          {note}")
        print()

    CLEAN_CSV.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(CLEAN_CSV, index=False)
    print(f"Wrote    {CLEAN_CSV.relative_to(BASE_DIR)}")
    print(f"         {len(df):,} rows, {len(df.columns)} columns")
    print()

    print("Teams in the cleaned file:")
    width = max(len(team) for team in df["team"].unique())
    for team, events in df["team"].value_counts().items():
        print(f"    {team:<{width}}  {events:>6,} events")

    return 0


if __name__ == "__main__":
    sys.exit(main())
