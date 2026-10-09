# Big Data Cup Explorer

A password-protected Flask web app for exploring the Stathletes Big Data Cup
2021 event dataset - 24,002 tracked events from the 2018 Olympic women's
tournament and associated fixtures.

The repo is a single self-contained project: its own app, its own
`requirements.txt`, its own deploy config and its own copy of the raw data.
Clone it, run it, deploy it.

> **Status - working app.** `clean_data.py` builds `data/cleaned/events.csv`,
> and the app reads it and draws it on four analysis pages. See
> [The pages](#the-pages) and [Cleaning the data](#cleaning-the-data).

---

## What it does

- **Cleans the raw Stathletes export** into one analysis-ready CSV, in ten
  numbered steps you can read.
- **Draws it on four pages** - an Event Map of passes, shots and everything
  else, a Heat Map that counts events into the rink's 58 boxes and two value maps that put a number on what a possession or a zone entry is worth.
- **Is password-gated.** The pages are unlinked and require a shared password, so
  they can be shared privately before they are made public.
- **Ships its own data**, so there is nothing to configure to get it running.

---

## Quick start

Requires Python 3.11 or newer.

```bash
git clone <this-repo> big-data-cup
cd big-data-cup

python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

pip install -r requirements.txt

export APP_PASSWORD=your-password    # Windows: set APP_PASSWORD=...
export SECRET_KEY=$(python -c "import secrets; print(secrets.token_hex(32))")

python app.py
```

Open <http://127.0.0.1:5002> and enter the password.

| Variable | Required | Notes |
| --- | --- | --- |
| `APP_PASSWORD` | yes | Leave unset and the page stays **locked**, not open. |
| `SECRET_KEY` | recommended | Signs the unlock cookie. If unset, a random key is generated at boot and unlock cookies stop working after a restart. |
| `PORT` | no | Defaults to `5002` locally. Railway injects its own. |
| `DATA_DIR` | no | Defaults to `./data`. |
| `RAW_CSV` | no | The untouched source file. Defaults to `<data dir>/olympic_womens_dataset.csv`. |
| `EVENTS_CSV` | no | What the app reads. Defaults to `<data dir>/cleaned/events.csv`. |
| `ZONES_GEOJSON` | no | The rink zone geometry. Defaults to `<data dir>/HockeyRinkZones.geojson`. |
| `MODELS_JSON` | no | The xG model record. Defaults to `<data dir>/cleaned/xg_models.json`. |

`/healthz` returns `{"status":"ok"}` and is what the Railway healthcheck hits.

---

## Project layout

```
.
├── app.py                 Flask app: routes and the gate
├── auth.py                Shared-password gate (signed cookie, fails closed)
├── config.py              Branding, access and data settings
├── data_source.py         Reads the cleaned CSV and answers what the pages ask
├── rink_geometry.py       Rink coordinates, zone shapes and heat-map colours
├── possession_values.py   Possession value by rink region, with the mirroring
├── zone_entries.py        Filing zone entries by type, and valuing them
├── clean_data.py          The cleaning script - one numbered step per function
├── templates/
│   ├── base.html          App shell - sidebar, tabs, content area
│   ├── _slicers.html      The side panel slicers, shared by both rink pages
│   ├── event_map.html     Event Map
│   ├── heatmap.html       Heat Map
│   ├── possession.html    Possession Values
│   ├── zone_entries.html  Zone Entries
│   ├── cleaning.html      Data Cleaning
│   ├── xg_models.html     xG Models
│   ├── gate.html          Password gate
│   └── error.html         "Dataset unavailable"
├── static/
│   ├── styles.css         The app's stylesheet
│   ├── hockeyrink.png     The rink picture both maps are drawn on
│   ├── flames-dark.svg    Logo for the dark sidebar
│   └── flames-light.svg   Logo for the favicon
├── data/
│   ├── olympic_womens_dataset.csv    The raw dataset (4.5 MB)
│   ├── Dataset Description.pdf       The data dictionary
│   ├── HockeyRinkZones.geojson       Rink zone geometry
│   ├── hockeyrink.png
│   └── cleaned/
│       ├── events.csv                Written by clean_data.py
│       └── xg_models.json            What the xG models were trained on and scored
├── requirements.txt
├── railway.toml           Railway config
├── Procfile               Same start command, for Render/Heroku-style hosts
└── .env.example
```

---

## How it works

The app is deliberately small and has no database. The request flow is:

```
request → app.py  ──→ auth.py           (is this visitor unlocked?)
                  └─→ data_source.py    (read the CSV, apply the slicers)
                          ↓
                   rink_geometry.py     (turn coordinates into SVG)
                          ↓
                   templates/*.html     (render)
```

**`data_source.py` is the only module the app uses to read data.** It reads the
CSV with pandas, caches the frame until the file changes and answers what the pages ask of it. Swapping the data file is a change to this one module and
nothing else. `clean_data.py` is separate - it *writes* the cleaned CSV.

There is no database and no ORM. With a static dataset there is nothing for one
to do, and it keeps the deploy to a single service.

### The pages

Six tabs, all behind the same gate.

**Event Map** draws the matching events on a rink, from the eventing team's point
of view, so the goal being attacked is always on the right:

| event | drawn as |
| --- | --- |
| `Play` | a blue arrow, from where the puck was let go to where it was aimed |
| `Incomplete Play` | a red arrow, the same way |
| `Shot on Net`, `Missed Shot`, `Blocked Shot`, `Goal` | blue markers, a different shape for each |
| everything else | a black dot |

It draws at most 2,500 events at once and says so when it stops, because a few
thousand arrows on one rink is a lot to ask of a browser.

**Heat Map** counts the matching events into the rink's 58 boxes and colours each
one, white for empty and deep blue for the busiest, with the count written in the
middle of the box. The **From / To** slicer decides what is being counted:

- **From** counts where each event happened.
- **To** counts where a pass arrived, so `Play` and `Incomplete Play` are counted
  in the box the puck was aimed at. Every other event has only the one location,
  so it is still counted where it happened.

**Possession Values** asks a different question: when a team wins the puck in a
given part of the rink, what does it get out of the next 15 seconds? The
population is fixed - international games only, at 5v5, and only events where a
possession starts - and two slicers pick the **Possession Start** (`Puck
Recovery`, `Takeaway`, `Reception` or all three) and the **Metric** (`Corsi`,
`Fenwick`, `Shots`, `Goals`, `xG_All_Shots`). Each metric is the for column minus
the against column, added up over every 15-second window that began in that
region and then **divided by the number of possessions**, so `Corsi` reads as
`(CF_15 - CA_15)` per possession.

The division is what makes the map comparable across the rink. Without it the
busiest parts would look the most extreme simply because more possessions started
there, and the map would show where play happens rather than what a possession is
worth. The window-weighted average of the region values equals the overall rate
exactly, for every metric.

There is not much data behind it, so the map is made readable two ways. The rink
is **mirrored across the centre line** - the ice is symmetric left to right, so a
box and its twin count as one region, which roughly doubles the data behind each
part of the map. Any region still holding fewer than **100 windows** is then
**merged into its nearest neighbour**, repeating until every region has enough to
say something. 58 boxes become 36 mirrored regions and then 28 drawn regions; the
seven that are now built from more than two boxes are all in the quiet corners.

**Every box is labelled**, including the ones inside a merged or mirrored region,
so each carries its region's value and the map has no unlabelled holes.

**Zone Entries** applies the same idea to a narrower question: when a team
carries, plays or dumps the puck into the offensive zone, what does it get out of
the next 15 seconds? Same population rule and same metric list, with an extra
slicer for the **Zone Entry Type** (`Dumped`, `Carried`, `Played` or all).

The entries are **filed by type rather than by coordinate**. A zone entry is
about crossing the blue line, but the coordinate it is logged at is not where the
entry happened - a carry or a play is recorded where the puck crossed the line,
and a dump where the puck was *released*, well before it. Read literally, the same
kind of entry scatters across the neutral zone and the offensive zone. So carries
and plays go in the first offensive column (`O24`, `O25`, `O26`) and dumps go in
the last neutral column (`N01`, `N02`, `N03`), keeping only the third of the ice
the entry was made from.

**Nothing is mirrored and nothing is merged here**, which is the one place this
page departs from Possession Values. Because entries are filed by type, each box
already means something on its own, and folding the left and right walls together
would hide the comparison the page exists to make. The rest of the rink is left
unfilled: those boxes hold no entries by construction, so they are not empty
regions, they are simply not where the question applies.

**Data Cleaning** walks through all ten cleaning steps in plain language - what
each one changes and why - with no code on the page. It is written for someone
who wants to know what happened to the data without reading the script. The side
panel offers both the raw and the cleaned file for download, behind the gate.

**xG Models** explains how the two expected-goal models are built, what they were
trained on, how they scored under cross-validation and what weights they came out
with. It opens by saying plainly that they rest on very little data - 944 shots
and 30 goals - so the numbers should not be leaned on too hard. The figures come
from `data/cleaned/xg_models.json`, which `clean_data.py` writes when it fits the
models, so the page shows the models that actually produced the xG columns rather
than a second set trained just for display.

Both rink pages share the same side panel: **Competition, Team, Player, Strength
State, Score State** and **Event**, plus **From / To** on the Heat Map only. The
Event slicer opens on `Play`; every other slicer opens on "all". Changing any of
them reloads the page with the new filters, and each value is checked against the
values actually in the data, so a hand-edited URL falls back to "all" rather than
erroring.

The first three narrow as you go: **Competition** decides which **Teams** are
offered, and the chosen team decides which **Players** are offered. Pick the NCAA
and the team list drops to the two college teams; pick Canada and the player list
drops to Canada's 23. Switching competition resets a team that no longer
applies. Choosing the Olympics while a college team is selected clears the team
rather than filtering the page down to nothing.

`score_state` is the goal difference from the eventing team's point of view, so
the slicer reads `Up 1` / `Tied` / `Down 1` rather than raw numbers.

### The password gate

`auth.py` implements a single shared password, with no user accounts:

| Behaviour | How |
| --- | --- |
| One shared password | `APP_PASSWORD` |
| Unlock survives restarts | HMAC-SHA256 signed cookie, keyed by `SECRET_KEY` |
| Rotating the password logs everyone out | the password is part of the signed label |
| **Fails closed** | an unset password denies everything, including a correctly-signed cookie for the empty password |
| No timing leak | `hmac.compare_digest` on the submitted password |
| No open redirect | `next` is only honoured if it is a same-site absolute path |
| Not discoverable | the page is linked from nowhere, and a 404 renders the gate rather than a "not found" page |

An unset `SECRET_KEY` is not an error - a random one is generated at boot. That
is deliberate: a hardcoded fallback secret would be forgeable.

To share the app with a reviewer, send them the URL and the password.

---

## The dataset

`data/olympic_womens_dataset.csv` - Stathletes-tracked events, described in
`data/Dataset Description.pdf`. Event types are shots, goals, plays, incomplete
plays, takeaways, puck recoveries, dump ins/outs, zone entries, faceoffs and
penalties.

Observations from reading the raw file, recorded so the cleaning decisions can
be made against them rather than re-derived. This table describes the **raw**
file; see [Cleaning the data](#cleaning-the-data) for what the script changes.

| Observation | Evidence |
| --- | --- |
| **Coordinates are attack-normalized.** x=200 is always the net the eventing team attacks, whichever team it is. | The data dictionary says coordinates are "from the perspective of the eventing team". Goals cluster at x≈155-188 and faceoff dots sit at x=31/100/169. |
| **Goal lines are at x=11 and x=189; blue lines at x=75 and x=125.** | The bundled `HockeyRinkZones.geojson` splits its D/N/O regions at ±25 from center ice, i.e. x=75/125. Tracked `Zone Entry` events (coordinate = where the puck crossed the line) have a median x of 124. |
| **`Clock` counts *down* from 20:00 and resets each period.** | Every period's maximum clock is exactly 20:00. It needs converting to elapsed seconds before any "what happened next" question. |
| **Score columns are the score *at* the event, before it.** | A game's first goal is logged at 0-0. |
| **The file contains one exact duplicate row.** | `df.duplicated().sum() == 1`; it is in the 2018-02-14 game. |
| **`Detail 1`-`Detail 4` mean different things per event type.** | Shot → type/destination/traffic/one-timer; Play → pass type; Dump → possession outcome; Zone Entry → entry type; Faceoff → backhand/forehand; Penalty → infraction. `Detail 3`/`4` are the strings `t`/`f`, not booleans. |
| **There is no game id column.** | Fixtures have to be identified from `game_date` + home + away. |
| **Strength state is derivable, not given.** | `Home Team Skaters`/`Away Team Skaters` range 3-6. |
| **The file is 13 games, not only the Olympics.** | 5 Olympic games (February 2018), 2 NCAA games (October 2018), 3 Rivalry Series games (February 2019) and 3 World Championship games (April 2019). Dates are used exactly as shipped. |

The one judgement already encoded is which events count as a shot attempt: the
dataset splits them across `Shot` (unsuccessful - block, miss, save) and `Goal`
(successful), so both are counted. That is a reading of the data dictionary, not
a transformation, and it is named in one place (`SHOT_ATTEMPT_EVENTS`).

---

## Cleaning the data

```bash
python clean_data.py
```

Reads `data/olympic_womens_dataset.csv`, writes `data/cleaned/events.csv`. The
raw file is never modified.

All the cleaning lives in **one script**, `clean_data.py`, as numbered steps
applied in order. Each step is one function that explains what it does and why
and reports what it changed, so running the script prints a readable record of
what happened to the data:

```
Step 1  Clean the teams
          "Home Team"  ->  "home_team"
          "Away Team"  ->  "away_team"
          "Team"       ->  "team"
          "Olympic (Women) - Canada"  ->  "Canada"   (9,757 events)
          "St. Lawrence Saints" / "Clarkson Golden Knights"  ->  unchanged

Step 2  Rename Period to period, and replace Clock with time_elapsed
          "Period"  ->  "period"
          "Clock"   ->  "time_elapsed"   (seconds since the start of the game)
          runs from 0 to 4,799 seconds across the file

Step 3  Add venue, strength_state and score_state, and drop the columns they replace
          added "venue"           Home 12,160 / Away 11,842 / blank 0
          added "strength_state"  3v4, 3v5, 4v3, 4v4, 4v5, 4v6, 5v3, 5v4, 5v5, 5v6, 6v4, 6v5
          added "score_state"     -5 to 5
          removed "Home Team Skaters", "Away Team Skaters", "Home Team Goals", "Away Team Goals"

Step 4  Give shots their outcome, add a Reception under every Play and rename the rest
          shots renamed    "Shot on Net" 727 / "Missed Shot" 449 / "Blocked Shot" 439
          receptions added 7,424 new rows, one underneath every Play
          columns renamed  "Player"  ->  "player"
          ...

Step 5  Rename the coordinate columns to x1, y1, x2, y2, and centre them on 0
          renamed  "X Coordinate"  ->  "x1"
          ...
          centred  x1   -100.0 to   100.0
          centred  y1    -42.5 to    42.5
          centred  x2   -100.0 to   100.0
          centred  y2    -42.5 to    42.5

Step 6  Add boxid and boxid_2 - which rink zone each coordinate falls in
          added "boxid"    58 of 58 boxes used, 144 rows snapped to the nearest box, 0 blank
          added "boxid_2"  58 of 58 boxes used, 25 rows snapped to the nearest box, 21,321 blank

Step 7  Add possession_start - does this event begin a player's possession?
          added "possession_start"  1 for 15,269 rows: Reception 7,424 / Puck Recovery 6,960 / Takeaway 885
                                    0 for the other 16,157 rows

Step 8  Add xg_all_shots and xg_on_net, from two logistic regressions
          shot geometry    distance 1.1 to 74.1 ft, angle 0.0 to 142.3 degrees
          all-shots model  944 shots, 30 goals   shot_distance -1.22 / shot_angle +0.32 / traffic +0.17 / one_timer +0.29
                           cross-validated AUC 0.858
          on-net model     417 shots, 30 goals   shot_distance -0.96 / shot_angle +0.41 / traffic +0.36 / one_timer +0.32
                           cross-validated AUC 0.826
          added "xg_all_shots"  scored on 944 rows
          added "xg_on_net"     scored on 417 rows

Step 9  Add competition - which tournament each game belongs to
          Olympic              5 games, 12,258 rows
          NCAA                 2 games, 4,486 rows
          Rivalry Series       3 games, 7,241 rows
          World Championship   3 games, 7,441 rows

Step 10  Count what happens in the 15 seconds after a possession starts or a zone entry
          windows          17,069 openings (15,269 possession starts + 1,800 zone entries)
          cut short        353 ended on a goal, 165 ended at a period end
          totals           CF 7,928 / CA 1,731 / SF 3,702 / SA 862 / GF 274 / GA 79
                           xGF 152.3 / xGA 31.8
          added            CF_15, CA_15, FF_15, FA_15, SF_15, SA_15, GF_15, GA_15, xGF_15, xGA_15
```

| Step | What it does |
| --- | --- |
| 1 | Cleans the teams. Renames `Home Team`, `Away Team` and `Team` to `home_team`, `away_team` and `team`, then shortens Olympic team names to the nation - `Olympic (Women) - Canada` → `Canada`, `Olympic (Women) - Olympic Athletes from Russia` → `Russia`. The two NCAA teams keep their full names. |
| 2 | Renames `Period` to `period`, and replaces `Clock` with `time_elapsed`: seconds since the start of the game, `(period - 1) × 1200 + seconds played in the period`. The raw clock counts *down* and restarts every period, so it cannot be used to ask what happened next. |
| 3 | Adds three columns written from the **eventing team's** point of view, then drops the four home/away columns they replace. `venue` is `Home` or `Away`. `strength_state` is skaters for and against - so `5v4` is a power play for whichever team took the event. `score_state` is the goal difference - so `+1` is winning by one. |
| 4 | Puts the outcome into the shot's name (`Shot on Net`, `Missed Shot`, `Blocked Shot`) instead of leaving every shot called `Shot`. Adds a `Reception` row underneath every `Play`, so the player who *received* a pass gets a row - they are invisible in the raw file, which only credits the passer. Renames the last of the raw columns to `player`, `player_2`, `event` and `detail_1`-`detail_4`. |
| 5 | Renames the coordinates to `x1`, `y1`, `x2`, `y2` - a pass has two locations, where it was let go and where it arrived. Shifts them so the middle of the rink is `0`: `x` runs -100 (own goal line) to +100 (target goal line) and `y` runs -42.5 to +42.5. Positive `x` is the offensive end. |
| 6 | Adds `boxid` and `boxid_2`: the rink box that `(x1, y1)` and `(x2, y2)` fall in, looked up from the bundled `HockeyRinkZones.geojson`. Events exactly on a blue line count as the end zone, not the middle, and events outside every box are snapped to the nearest one - so nothing is left without a box. `boxid_2` is blank only where there is no second location. |
| 7 | Adds `possession_start`: `1` when the event begins a player's possession of the puck - a `Puck Recovery`, `Reception` or `Takeaway` - and `0` otherwise. |
| 8 | Works out `shot_distance` and `shot_angle`, builds two logistic-regression xG models with scikit-learn, and adds `xg_all_shots` and `xg_on_net`. |
| 9 | Adds `competition` - `Olympic`, `NCAA`, `Rivalry Series` or `World Championship` - from the date the game was played. |
| 10 | For every possession start and `Zone Entry`, counts the following 15 seconds as `CF_15`/`CA_15`, `FF_15`/`FA_15`, `SF_15`/`SA_15`, `GF_15`/`GA_15` and `xGF_15`/`xGA_15`. A goal or the end of the period closes the window early. |

Three columns describe the same idea - the situation as the team that took the
event saw it - which is what makes `5v4` mean a power play and `+1` mean leading,
regardless of which team it was.

`score_state` holds the score as it stood when the event was logged, so at a
`Goal` it is the score *before* that goal. The first goal of every game reads `0`.

A `Reception` copies its `Play`'s period, `time_elapsed`, team, situation and
details, and carries one player and one location: the receiver, and where they
took the puck in. `player_2` and the second coordinate pair are left blank.

Because a Reception is added under every one of the 7,424 Plays, the cleaned file
has **31,426 rows** where the raw file has 24,002.

Centring the coordinates puts the events in the same frame as the bundled
`HockeyRinkZones.geojson`, so events and rink zones line up without adjustment.
The numbers confirm it: the blue lines land on ±25, matching the geojson's
defensive/neutral/offensive split, and tracked `Zone Entry` events have a median
`x1` of 24 - right where the puck crosses the offensive blue line.

`boxid` and `boxid_2` come from that same file, which splits the rink into 58
boxes - 26 in each end and 6 across the middle. Because coordinates are written
from the eventing team's point of view, `O` is always the end that team is
attacking. The zone letter always agrees with the x position: `D` at or past
-25, `N` between, `O` at or past +25.

Two points on a line need a rule, because either side could be argued for:

- **An event exactly on a blue line counts as the end zone, not the middle.**
  The blue line is where the end zone begins. 523 events sit on `x1 = +25` and
  57 on `x1 = -25`, and none of them are left in the neutral zone.
- **An event outside every box is snapped to the nearest one.** Events are
  logged right on the boards and out in the rounded corners, where a point can
  sit just outside the drawn rink. A puck is never off the ice, so the closest
  box is the right answer - and for an event on the boards, that is the box
  along the boards. 144 events and 25 pass targets were snapped this way.

The result is that **every event has a `boxid`**, and `boxid_2` is blank only
where there is no second location at all - 21,321 of 31,426 rows.

`competition` is worked out from the date the game was played. Each competition
sits in its own month, so the date separates them cleanly with no guessing:

| competition | month | games |
| --- | --- | ---: |
| Olympic | February 2018 | 5 |
| NCAA | October 2018 | 2 |
| Rivalry Series | February 2019 | 3 |
| World Championship | April 2019 | 3 |

The three February 2019 games are Canada vs United States only, which is the
Rivalry Series. The three April 2019 games include Finland and run to overtime,
which is the World Championship.

### The xG models

Two plain logistic regressions, built with scikit-learn in step 8. Both are
trained only on the international games at 5v5, which is the situation they
are meant to describe: 944 shot attempts and 30 goals.

| column | trained on | rows | goals |
| --- | --- | ---: | ---: |
| `xg_all_shots` | every shot attempt - on net, missed, blocked and goals | 944 | 30 |
| `xg_on_net` | shots that reached the net - saved shots and goals | 417 | 30 |

Both take the same four inputs: `shot_distance`, `shot_angle`, whether there was
traffic in front (`detail_3`) and whether it was a one-timer (`detail_4`). The
inputs are standardised before fitting, so the model's penalty falls evenly
across them rather than hardest on whichever happens to be written in the
largest numbers.

`shot_angle` is the angle between the two goal posts as seen from the shot. It
is measured with `atan2` of the cross and dot products of the two lines running
to the posts, which keeps it between 0° and 180° and positive on both sides of
the ice - a shot from the left and a shot from the right, otherwise alike, get
the same angle.

A shot taken from the goal line itself, or from behind it, is given **0**: from
there the net is edge-on or behind the shooter, so none of the goal mouth is on
show. 27 shots in the file fall into that group - 9 on the goal line and 18
behind it - and none of them were goals.

The weights come out with the expected signs - distance negative, angle
positive, traffic and one-timer small and positive - and the models are well
calibrated: across the training rows the xG values sum to **30.03** against 30
actual goals.

**Both columns are blank outside the training population.** A college game or a
power play gets no xG value: the model was built for international 5v5 play, and
saying nothing about anything else is more honest than guessing. Each model is
scored on the rows it was trained on, so these are in-sample values.

With 30 goals spread across four inputs the sample is small. The cross-validated
AUC - 0.858 for all shots, 0.826 for shots on net - is best read as "the model
has learned something real" rather than as a precise measure of quality.

### The 15-second windows

Step 10 looks forward from every event that opens a passage of play: a
possession starting (`Puck Recovery`, `Reception`, `Takeaway`) or a
`Zone Entry`. It counts the next 15 seconds.

The window closes early in exactly two cases:

- **A goal, by either team.** The goal still counts, since it happened inside
  the window; then the window shuts, because play restarts from centre ice.
- **The end of the period.** A window never runs into the next period.

Everything is counted twice, once per team. `F` is the team that opened the
window and `A` is the other one, so `CF_15` is what that team generated and
`CA_15` is what it gave up:

| column | counts |
| --- | --- |
| `CF_15` / `CA_15` | corsi - every shot attempt: on net, missed, blocked, goals |
| `FF_15` / `FA_15` | fenwick - shot attempts that were not blocked |
| `SF_15` / `SA_15` | shots that reached the net, goals included |
| `GF_15` / `GA_15` | goals |
| `xGF_15` / `xGA_15` | expected goals, summed over those attempts |

A goal counts towards corsi, fenwick and shots as well as goals, because a goal
is a shot attempt that went in. So the four nest inside each other, and the data
confirms it: `CF ≥ FF ≥ SF ≥ GF` and `CA ≥ FA ≥ SA ≥ GA` on every one of the
17,069 windows.

Only an opening row has a window, so all ten columns are blank everywhere else.

**Two things to read carefully.** First, the windows overlap heavily - 1,671 shot
attempts appear across 9,659 window-slots, about 5.8 each - so these totals are
not counts of unique events. Second, the split is lopsided, `CF` 7,928 against
`CA` 1,731. That is not a bug: an opening is by definition a moment when a team
*gains* the puck, so the team that opened the window usually still has it 15
seconds later.

`xG` is only worked out for international 5v5 shots, so a window in a college
game or on a power play adds up the xG it has and treats the rest as 0 - as
agreed, a missing xG counts as nothing rather than blanking the window.

---

## Deploying to Railway

1. Push this repo to GitHub.
2. In Railway: **New Project → Deploy from GitHub repo**.
3. Under **Variables**, set:
   - `APP_PASSWORD` - the shared password
   - `SECRET_KEY` - `python -c "import secrets; print(secrets.token_hex(32))"`
4. Deploy. Railway injects `PORT`; `railway.toml` supplies the start command and
   the `/healthz` healthcheck.
5. Open the generated URL - it should show the gate.

### Any other host

`Procfile` carries the same command:

```
web: gunicorn app:app --bind 0.0.0.0:$PORT --workers 2 --timeout 60
```

---

## Roadmap

1. **More pages.** The 15-second window columns (`CF_15`, `xGF_15` and the
   rest) are in the data but not yet on a page.
2. **More slicers.** Date, period and venue are all in the data and easy to add.
3. **Compare players.** Every window metric supports ranking players, which is
   the obvious next thing a coach would ask for.

---

## Configuration reference

Branding lives in `config.py` rather than in the templates, so the app can be
re-skinned without touching markup - the accent colour drives the sidebar
gradient, the active tab underline and the table bars:

```python
app_title_top: str = "Calgary"
app_title_bottom: str = "Flames"
accent: str = "#c8102e"
logo_url: str = "/static/flames-dark.svg"
```
