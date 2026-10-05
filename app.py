"""Flask app for exploring the Stathletes Big Data Cup 2021 event data.

The pages read one cleaned CSV and draw it on a rink.  Two rules shape the file:

* **The pages are unlinked.** They render without site navigation and are
  reachable only by knowing the URL.
* **Access fails closed.** A single shared password (``APP_PASSWORD``, never
  committed) unlocks a signed cookie.  An unset password denies everything
  rather than opening the pages.

Run locally::

    APP_PASSWORD=<your-password> python app.py
"""

from __future__ import annotations

import hmac
import logging

from flask import Flask, abort, redirect, render_template, request, send_file, url_for
from jinja2 import StrictUndefined

import auth
import data_source
import possession_values
import rink_geometry
import zone_entries
from config import Config, load_config

log = logging.getLogger("app")


def _safe_next(raw: str | None) -> str:
    """Only ever redirect to a path on this site.

    A ``next`` value arrives from the query string and the login form, so it is
    attacker-controlled. Anything that is not a same-site absolute path is
    discarded, which is what stops the gate being used as an open redirect.
    """
    if not raw or not raw.startswith("/") or raw.startswith("//"):
        return url_for("event_map")
    return raw


def _file_size(path) -> str:
    """A file's size, for the download links."""
    return f"{path.stat().st_size / 1048576:.1f} MB" if path.exists() else "not found"


def create_app(config: Config | None = None) -> Flask:
    config = config or load_config()
    app = Flask(__name__)
    app.config["SETTINGS"] = config
    app.secret_key = config.secret_key
    # A template variable that was never passed should be an error, not a blank
    # space.  Jinja renders nothing by default, which hides mistakes like an
    # include that forgot its argument.
    app.jinja_env.undefined = StrictUndefined

    if not config.password_configured:
        log.warning(
            "APP_PASSWORD is not set - the pages are locked. "
            "Set it in the environment to enable access."
        )
    if config.secret_is_ephemeral:
        log.warning(
            "SECRET_KEY is not set - a random one was generated for this process, "
            "so unlock cookies will not survive a restart."
        )

    @app.context_processor
    def inject_branding():
        return {
            "config": config,
            "page_title": f"{config.app_title_top} {config.app_title_bottom}",
            "rink": {
                "width": rink_geometry.RINK_WIDTH,
                "height": rink_geometry.RINK_HEIGHT,
            },
        }

    # ── Gate ────────────────────────────────────────────────

    def render_gate(error: str = "", next_url: str | None = None):
        return render_template(
            "gate.html",
            error=error,
            next_url=_safe_next(next_url),
            locked_out=not config.password_configured,
        )

    #: The files a visitor can download, and where each one lives.
    DOWNLOADS = {
        "raw": ("Raw file", "olympic_womens_dataset.csv"),
        "cleaned": ("Cleaned file", "events.csv"),
    }

    def download_list() -> list[dict]:
        """The download links for the side panel, with the size of each file."""
        paths = {"raw": config.raw_csv, "cleaned": config.events_csv}
        return [
            {
                "name": key,
                "label": label,
                "filename": filename,
                "size": _file_size(paths[key]),
            }
            for key, (label, filename) in DOWNLOADS.items()
        ]

    def page_data():
        """Load the data and apply the slicers.

        Returns the dataset, the values each slicer can offer, the slicers as
        chosen, and the rows that survived them.
        """
        dataset = data_source.load(config)
        selected, available = data_source.resolve(dataset, request.args)
        return dataset, available, selected, data_source.select(dataset, selected)

    # ── Pages ───────────────────────────────────────────────

    @app.get("/")
    def event_map():
        if not auth.is_unlocked():
            return render_gate(next_url=request.full_path)
        try:
            dataset, available, selected, frame = page_data()
        except data_source.DatasetMissing as exc:
            return render_template("error.html", message=str(exc), active_tab=""), 500

        events, total = data_source.event_map(frame)
        return render_template(
            "event_map.html",
            active_tab="event_map",
            dataset=dataset,
            available=available,
            selected=selected,
            matched=len(frame),
            events=events,
            drawn=len(events),
            total=total,
            limit=data_source.EVENT_MAP_LIMIT,
            # From / To belongs to the Heat Map only.
            shows_direction=False,
        )

    @app.get("/heatmap")
    def heat_map():
        if not auth.is_unlocked():
            return render_gate(next_url=request.full_path)
        try:
            dataset, available, selected, frame = page_data()
        except data_source.DatasetMissing as exc:
            return render_template("error.html", message=str(exc), active_tab=""), 500

        # From / To only means anything for a pass.  Play and Incomplete Play are
        # the only events carrying a second location, so the slicer is hidden for
        # everything else, and a direction left over in the URL is ignored rather
        # than counting the events by a place they never had.
        shows_direction = selected["event"] in data_source.PASS_EVENTS
        direction = (
            "to"
            if shows_direction and request.args.get("direction") == "to"
            else "from"
        )

        counts = data_source.heat_counts(frame, direction)
        return render_template(
            "heatmap.html",
            active_tab="heat_map",
            dataset=dataset,
            available=available,
            selected=selected,
            matched=len(frame),
            direction=direction,
            shows_direction=shows_direction,
            zones=rink_geometry.heatmap_zones(
                rink_geometry.zones(str(config.zones_geojson)), counts
            ),
        )

    @app.get("/possession")
    def possession():
        if not auth.is_unlocked():
            return render_gate(next_url=request.full_path)
        try:
            dataset = data_source.load(config)
        except data_source.DatasetMissing as exc:
            return render_template("error.html", message=str(exc), active_tab=""), 500

        # An empty start means all three; an unknown one falls back to that too.
        start = request.args.get("start", "")
        if start not in possession_values.POSSESSION_STARTS:
            start = ""
        metric = request.args.get("metric", "")
        if metric not in possession_values.METRICS:
            metric = "Corsi"

        base = possession_values.population(dataset.frame)
        frame = base if not start else base[base["event"] == start]
        built = possession_values.build(
            frame, rink_geometry.zones(str(config.zones_geojson)), metric
        )

        return render_template(
            "possession.html",
            active_tab="possession",
            dataset=dataset,
            starts=possession_values.POSSESSION_STARTS,
            metrics=list(possession_values.METRICS),
            minimum=possession_values.MIN_WINDOWS,
            selected_start=start,
            metric=metric,
            windows=len(frame),
            map_label="Possession value by rink region",
            **built,
        )

    @app.get("/zone-entries")
    def zone_entries_page():
        if not auth.is_unlocked():
            return render_gate(next_url=request.full_path)
        try:
            dataset = data_source.load(config)
        except data_source.DatasetMissing as exc:
            return render_template("error.html", message=str(exc), active_tab=""), 500

        entry = request.args.get("entry", "")
        if entry not in zone_entries.ENTRY_TYPES:
            entry = ""
        metric = request.args.get("metric", "")
        if metric not in possession_values.METRICS:
            metric = "Corsi"

        base = zone_entries.population(dataset.frame)
        frame = base if not entry else base[base["detail_1"] == entry]
        built = zone_entries.build(
            zone_entries.filed(frame),
            rink_geometry.zones(str(config.zones_geojson)),
            metric,
        )

        return render_template(
            "zone_entries.html",
            active_tab="zone_entries",
            dataset=dataset,
            entry_types=zone_entries.ENTRY_TYPES,
            metrics=list(possession_values.METRICS),
            selected_entry=entry,
            metric=metric,
            entries=len(frame),
            thinnest=min((region["windows"] for region in built["regions"]), default=0),
            map_label="Zone entry value by rink region",
            **built,
        )

    @app.get("/xg")
    def xg_models():
        if not auth.is_unlocked():
            return render_gate(next_url=request.full_path)
        try:
            dataset = data_source.load(config)
            models = data_source.load_models(config)
        except data_source.DatasetMissing as exc:
            return render_template("error.html", message=str(exc), active_tab=""), 500
        return render_template(
            "xg_models.html",
            active_tab="xg",
            dataset=dataset,
            models=models,
        )

    @app.get("/cleaning")
    def cleaning():
        if not auth.is_unlocked():
            return render_gate(next_url=request.full_path)
        try:
            dataset = data_source.load(config)
        except data_source.DatasetMissing as exc:
            return render_template("error.html", message=str(exc), active_tab=""), 500
        return render_template(
            "cleaning.html",
            active_tab="cleaning",
            dataset=dataset,
            columns=len(dataset.frame.columns),
            receptions=int((dataset.frame["event"] == "Reception").sum()),
            downloads=download_list(),
        )

    @app.get("/download/<name>")
    def download(name: str):
        """Hand over one of the data files.  Behind the gate like everything else."""
        if not auth.is_unlocked():
            return render_gate(next_url=request.full_path)
        if name not in DOWNLOADS:
            abort(404)

        _, filename = DOWNLOADS[name]
        path = {"raw": config.raw_csv, "cleaned": config.events_csv}[name]
        if not path.exists():
            return render_template("error.html", message=f"{filename} is not on disk.", active_tab=""), 500
        # Say the type rather than letting the host guess: on Windows a .csv is
        # registered as an Excel file, which is not what this is.
        return send_file(
            path, as_attachment=True, download_name=filename, mimetype="text/csv"
        )

    @app.post("/unlock")
    def unlock():
        next_url = _safe_next(request.form.get("next"))
        expected = config.password.strip()

        # Fail closed: with no password configured there is nothing to match.
        if not expected:
            return render_gate(next_url=next_url)

        supplied = request.form.get("password", "")
        # Constant-time compare so the gate does not leak the password by timing.
        if not hmac.compare_digest(supplied, expected):
            log.warning("rejected an unlock attempt from %s", request.remote_addr)
            return render_gate(error="Incorrect password.", next_url=next_url)

        return auth.issue_unlock(redirect(next_url))

    @app.post("/lock")
    def lock():
        return auth.clear_unlock(redirect(url_for("event_map")))

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}, 200

    @app.errorhandler(404)
    def not_found(_error):
        # Deliberately does not confirm what does or does not exist in the app.
        return render_gate(next_url=request.path), 404

    return app


app = create_app()


if __name__ == "__main__":
    import os

    app.run(
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", 5002)),
        debug=os.environ.get("FLASK_DEBUG", "") == "1",
    )
