"""Runtime configuration for the app.

Branding lives here rather than in the templates so the app can be re-skinned
without touching markup: swap ``ACCENT``/``logo_url`` and the whole UI (sidebar
gradient, active tab underline, map colours) follows.

Nothing has a usable default secret.  An unset ``APP_PASSWORD`` locks the
app rather than opening it, and an unset ``SECRET_KEY`` gets a random value at
boot, so unlock cookies stop working after a restart instead of being forgeable.
"""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

#: Calgary Flames red.  Drives every accent in the UI.
ACCENT = "#c8102e"


@dataclass(frozen=True)
class Config:
    # ── Branding ──
    app_title_top: str = "Calgary"
    app_title_bottom: str = "Flames"
    accent: str = ACCENT
    #: The white-outlined mark is the one built for dark backgrounds, which is
    #: what the sidebar gradient is.
    logo_url: str = "/static/flames-dark.svg"
    favicon_url: str = "/static/flames-light.svg"

    # ── Access ──
    password: str = ""
    secret_key: str = ""
    cookie_name: str = "app_access"
    cookie_max_age: int = 60 * 60 * 24 * 7

    # ── Data ──
    data_dir: Path = field(default_factory=lambda: BASE_DIR / "data")
    #: The untouched source file, offered as a download next to the cleaned one.
    raw_csv: Path = field(
        default_factory=lambda: BASE_DIR / "data" / "olympic_womens_dataset.csv"
    )
    #: What the app reads.  Written by clean_data.py from the raw file.
    events_csv: Path = field(
        default_factory=lambda: BASE_DIR / "data" / "cleaned" / "events.csv"
    )
    zones_geojson: Path = field(
        default_factory=lambda: BASE_DIR / "data" / "HockeyRinkZones.geojson"
    )
    #: The record of the xG models, written next to the cleaned file.
    models_json: Path = field(
        default_factory=lambda: BASE_DIR / "data" / "cleaned" / "xg_models.json"
    )

    @property
    def password_configured(self) -> bool:
        return bool(self.password.strip())

    @property
    def secret_is_ephemeral(self) -> bool:
        return not os.environ.get("SECRET_KEY", "").strip()


def load_config() -> Config:
    """Read configuration from the environment, falling back to repo defaults."""
    data_dir = Path(os.environ.get("DATA_DIR", BASE_DIR / "data"))
    return Config(
        password=os.environ.get("APP_PASSWORD", ""),
        secret_key=os.environ.get("SECRET_KEY", "").strip() or secrets.token_hex(32),
        data_dir=data_dir,
        raw_csv=Path(
            os.environ.get("RAW_CSV", data_dir / "olympic_womens_dataset.csv")
        ),
        events_csv=Path(
            os.environ.get("EVENTS_CSV", data_dir / "cleaned" / "events.csv")
        ),
        zones_geojson=Path(
            os.environ.get("ZONES_GEOJSON", data_dir / "HockeyRinkZones.geojson")
        ),
        models_json=Path(
            os.environ.get("MODELS_JSON", data_dir / "cleaned" / "xg_models.json")
        ),
    )
