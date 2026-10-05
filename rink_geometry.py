"""Rink geometry for the event and heat maps.

Two coordinate systems have to line up:

* the event data is centred - ``x`` runs -100 to 100 and ``y`` runs -42.5 to
  42.5, with positive x pointing at the goal the eventing team is attacking;
* the bundled ``HockeyRinkZones.geojson`` uses that same frame, so its zone
  polygons can be drawn straight on top.

Both land on a 200x85 SVG, with y flipped because y grows upwards on ice but
downwards in SVG::

    svg_x = x + 100
    svg_y = 42.5 - y

``static/hockeyrink.png`` covers exactly that 200x85 box, so it is drawn as the
background of the same viewBox and everything lines up without adjustment.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

#: The rink in feet, which is also the SVG viewBox.
RINK_WIDTH = 200.0
RINK_HEIGHT = 85.0

#: Half of each, so the middle of the rink lands in the middle of the SVG.
X_CENTRE = RINK_WIDTH / 2
Y_CENTRE = RINK_HEIGHT / 2


def to_svg(x: float, y: float) -> tuple[float, float]:
    """Event coordinates -> SVG coordinates."""
    return round(float(x) + X_CENTRE, 2), round(Y_CENTRE - float(y), 2)


def _centroid(points: list[tuple[float, float]]) -> tuple[float, float]:
    """The middle of a polygon, for putting its label in.

    Area weighted, so a long thin zone puts its label in the right place rather
    than wherever its corners happen to average out.
    """
    ring = points[:-1] if len(points) > 1 and points[0] == points[-1] else points
    area = 0.0
    cx = 0.0
    cy = 0.0
    for i, (x0, y0) in enumerate(ring):
        x1, y1 = ring[(i + 1) % len(ring)]
        cross = x0 * y1 - x1 * y0
        area += cross
        cx += (x0 + x1) * cross
        cy += (y0 + y1) * cross

    area *= 0.5
    if abs(area) < 1e-10:
        # A degenerate ring: fall back to the average of its corners.
        return sum(p[0] for p in ring) / len(ring), sum(p[1] for p in ring) / len(ring)

    return cx / (6 * area), cy / (6 * area)


@lru_cache(maxsize=4)
def zones(geojson_path: str) -> tuple[dict, ...]:
    """The rink zones as SVG paths with a label position, read once and cached.

    Each entry is ``{"id", "zone", "d", "cx", "cy"}``.  ``d`` is an SVG path in
    the 200x85 viewBox and ``cx``/``cy`` are where to put the count.
    """
    data = json.loads(Path(geojson_path).read_text(encoding="utf-8"))
    out = []
    for feature in data["features"]:
        geometry = feature["geometry"]
        rings = (
            [geometry["coordinates"]]
            if geometry["type"] == "Polygon"
            else geometry["coordinates"]
        )
        for ring in rings:
            corners = ring[0]
            xs = [point[0] for point in corners]
            ys = [point[1] for point in corners]
            points = [to_svg(gx, gy) for gx, gy in corners]
            if len(points) < 3:
                continue
            path = "M" + " L".join(f"{px},{py}" for px, py in points) + " Z"
            cx, cy = _centroid(points)
            out.append(
                {
                    "id": feature["properties"]["id"],
                    "zone": feature["properties"]["ZONE"],
                    "d": path,
                    "cx": round(cx, 1),
                    "cy": round(cy, 1),
                    # The box's extent in event coordinates, which is what the
                    # mirroring in possession_values.py works from.
                    "bounds": (
                        round(min(xs), 2),
                        round(max(xs), 2),
                        round(min(ys), 2),
                        round(max(ys), 2),
                    ),
                }
            )
    return tuple(out)


#: The heat map runs from white for an empty box to this blue for the busiest.
HEAT_LOW = (255, 255, 255)
HEAT_HIGH = (30, 80, 200)
HEAT_ALPHA = 0.62


def heat_colour(count: int, busiest: int) -> str:
    """White for an empty box, deeper blue the busier the box."""
    if not count or not busiest:
        return f"rgba(255,255,255,{HEAT_ALPHA})"
    strength = min(1.0, count / busiest)
    channels = (
        round(low - strength * (low - high))
        for low, high in zip(HEAT_LOW, HEAT_HIGH)
    )
    return "rgba({},{},{},{})".format(*channels, HEAT_ALPHA)


def heatmap_zones(zones_in, counts: dict) -> list[dict]:
    """The zones with their count and fill worked out, ready to draw."""
    busiest = max(counts.values()) if counts else 0
    return [
        {
            **zone,
            "count": counts.get(zone["id"], 0),
            "fill": heat_colour(counts.get(zone["id"], 0), busiest),
        }
        for zone in zones_in
    ]


#: A possession value can be positive or negative, so it needs two colours
#: rather than one: blue for a positive value, red for a negative one.
VALUE_POSITIVE = (30, 80, 200)
VALUE_NEGATIVE = (200, 16, 46)
VALUE_MIN_ALPHA = 0.18
VALUE_MAX_ALPHA = 0.80


def value_colour(value: float, extent: float) -> str:
    """A diverging fill for a value that runs either side of zero.

    The further from zero, the stronger the colour.  A value near zero is almost
    transparent, so the rink shows through and "nothing much happened here" does
    not look like a finding.
    """
    if not extent:
        return f"rgba(255,255,255,{VALUE_MIN_ALPHA})"
    strength = min(1.0, abs(value) / extent)
    alpha = VALUE_MIN_ALPHA + (VALUE_MAX_ALPHA - VALUE_MIN_ALPHA) * strength
    channels = VALUE_POSITIVE if value >= 0 else VALUE_NEGATIVE
    return "rgba({},{},{},{:.2f})".format(*channels, alpha)
