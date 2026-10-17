from __future__ import annotations

import json
import logging
import pickle
import signal
import sys
from pathlib import Path

import typer
from pydantic import ValidationError
from rich.console import Console
from rich.table import Table as RTable

from skysift import jsonutil
from skysift.config import Thresholds, load_settings, stop_request_path
from skysift.sources.base import load_registry
from skysift.store.blob import blob_from_url
from skysift.store.db import Store
from skysift.store.retention import DiskBudgetExceeded, Retention, RetentionPolicy

app = typer.Typer(help="SkySift: archival time-series search and vetting engine")


def _console(stderr: bool = False) -> Console:
    """On a real terminal rich uses the terminal width; piped (or under a test runner) it gets a wide fixed
    width, so wide tables are not wrapped or truncated. stderr=True is for progress and preamble lines, so
    stdout carries only the machine-readable result."""
    stream = sys.stderr if stderr else sys.stdout
    return Console(stderr=stderr) if stream.isatty() else Console(stderr=stderr, width=200)


def _plain(console: Console, text: str, **kw) -> None:
    """Print text verbatim: no rich markup, highlighting or wrapping (JSON holds brackets and long lines)."""
    console.print(text, markup=False, highlight=False, soft_wrap=True, **kw)


def _ctx(retention: str | None = None, workers_io: int | None = None, workers_cpu: int | None = None):
    if retention:
        try:
            RetentionPolicy.parse(retention)
        except ValueError as e:
            raise typer.BadParameter(str(e), param_hint="--retention") from e
    s = load_settings()
    if retention: s.retention = retention
    if workers_io: s.workers_io = workers_io
    if workers_cpu: s.workers_cpu = workers_cpu
    s.data_dir.mkdir(parents=True, exist_ok=True)
    store = Store(s.database_url); store.init_schema()
    return s, store, blob_from_url(s.blob_url)


def _period_window(s, min_period: float | None, max_period: float | None) -> None:
    """Apply --min-period/--max-period to the settings' thresholds; an impossible window is a usage error."""
    overrides = {k: v for k, v in (("min_period_days", min_period), ("max_period_days", max_period)) if v is not None}
    if not overrides: return
    try:
        s.thresholds = Thresholds(**{**s.thresholds.model_dump(), **overrides})
    except ValidationError as e:
        msg = "; ".join(err["msg"] for err in e.errors())
        raise typer.BadParameter(msg, param_hint="--min-period/--max-period") from e


def _calibration_warning(s, source: str) -> str | None:
    """One line when ``<data_dir>/calibration/<source>.jsonl`` is missing or holds lines of another search version
    than calibrate would write for this sweep's window and search settings (calibrate searches without the masked
    pass and the alias resolution, so its version is compared with that, not with the sweep's own); None when the
    file matches."""
    from skysift import calibrate as cal
    path = cal.default_path(s.data_dir, source)
    expected = cal.search_version(cal.calibration_detector(), s.thresholds)
    if not path.is_file():
        found = f"no calibration file {path}"
    else:
        versions = sorted({str(r.get("search_version")) for r in cal.read_rows(path)})
        if versions == [expected]: return None
        found = f"{path} holds search version {', '.join(versions) or 'none'}, this sweep needs {expected}"
    return f"warning: sde_min is not calibrated for this search version; run skysift calibrate ({found})"


def _source(name: str, store, blob):
    from skysift.sources.factory import make_source
    try:
        return make_source(name, store, blob)
    except ValueError as e:
        raise typer.BadParameter(str(e)) from e


@app.command()
def sources():
    """List data sources with size, depth limit, prior yield and cost."""
    reg = load_registry()
    t = RTable(title="SkySift data sources")
    for col in ("name", "status", "agency", "light curves", "baseline d", "depth ppm", "searched", "yield/10k",
                "s/star", "GB/10k", "CPU h/10k"):
        t.add_column(col, no_wrap=col != "agency")
    for s in reg.values():
        t.add_row(s.name, s.status, s.agency, f"{s.n_lightcurves:,}", f"{s.baseline_days:.0f}", f"{s.depth_limit_ppm:.0f}",
                  f"{s.fraction_searched:.0%}", f"{s.est_yield_per_10k:.1f}", f"{s.seconds_per_star:.1f}",
                  f"{s.bytes_per_star * 1e4 / 1e9:.1f}", f"{s.cpu_hours_per_10k():.1f}")
    _console().print(t)


class InterruptOnce:
    """Ctrl+C handling for a sweep. The first SIGINT asks the engine for a graceful stop (finish the targets in
    flight, start no more) and says so; the second raises KeyboardInterrupt, the hard stop. ``check`` is the engine's
    stop poll: the Ctrl+C request or a stop file written by the dashboard."""

    def __init__(self, say, stop_file: Path):
        self.say, self.stop_file, self.requested = say, stop_file, False

    def __call__(self, signum, frame) -> None:
        if self.requested: raise KeyboardInterrupt
        self.requested = True
        self.say("stop requested: finishing in-flight targets, press Ctrl+C again to stop now")

    def check(self) -> bool:
        return self.requested or self.stop_file.exists()


@app.command()
def run(source: str = typer.Option(...), gmag_max: float = 19.0,
        background_only: bool = typer.Option(True, "--background-only/--all"), psffrac_min: float = 0.5,
        limit: int | None = typer.Option(None, help="Process the N highest-priority targets not yet done."),
        detector: str = "box_dip", workers_io: int | None = None,
        workers_cpu: int | None = None, retention: str | None = None,
        min_period: float | None = typer.Option(None, help="Override the minimum search period (days)."),
        max_period: float | None = typer.Option(None, help="Override the maximum search period (days)."),
        periodogram: str | None = typer.Option(None, "--periodogram",
                                               help="Store the SDE envelope per target: all (default), signals, off."),
        verbose: bool = False):
    """Sweep a source: fetch, detect, vet, store, release files."""
    logging.basicConfig(level=logging.INFO if verbose else logging.WARNING,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    console = _console(stderr=True)
    s, store, blob = _ctx(retention, workers_io, workers_cpu)
    _period_window(s, min_period, max_period)
    if periodogram is not None:
        if periodogram not in ("all", "signals", "off"):
            raise typer.BadParameter("one of all, signals, off", param_hint="--periodogram")
        s.store_periodogram = periodogram
    from skysift import pipeline
    src = _source(source, store, blob)
    _plain(console, f"period window: {s.thresholds.min_period_days:g} to {s.thresholds.max_period_days:g} days")
    if warning := _calibration_warning(s, source): _plain(console, warning)
    interrupt = InterruptOnce(lambda text: _plain(console, f"\n{text}"), stop_request_path(s.data_dir))
    try:
        previous = signal.signal(signal.SIGINT, interrupt)
    except ValueError:                                  # not the main thread (embedding): Ctrl+C keeps its default
        previous = None
    try:
        summary = pipeline.run(src, store, blob, s, filter={"gmag_max": gmag_max, "background_only": background_only,
                                                           "psffrac_min": psffrac_min},
                               detector_name=detector, max_targets=limit,
                               progress=lambda sm: _plain(
                                   console, f"processed {sm['processed']} signals {sm['signals']} outcomes {sm['outcomes']}",
                                   end="\r"),
                               stop_requested=interrupt.check)
    except DiskBudgetExceeded as e:
        # an operator condition, not a crash: the work done so far on stdout, a plain message, exit code 3
        if getattr(e, "summary", None) is not None: _plain(_console(), jsonutil.dumps(e.summary, indent=1))
        _plain(console, f"\nstopped: {e}")
        raise typer.Exit(3) from None
    finally:
        if previous is not None: signal.signal(signal.SIGINT, previous)
    if summary.get("stopped"):
        _plain(console, "\nstopped early: the same command resumes with the targets not yet done")
    _plain(_console(), jsonutil.dumps(summary, indent=1))


@app.command()
def calibrate(source: str = typer.Option(...),
              limit: int = typer.Option(1000, min=1, help="Search the N highest-priority targets (the order of run)."),
              nulls: str = typer.Option("scrambled,inverted", help="Null curves to search besides the real flux."),
              workers_cpu: int | None = None,
              out: str | None = typer.Option(None, help="Output file (default <data_dir>/calibration/<source>.jsonl)."),
              min_period: float | None = typer.Option(None, help="Override the minimum search period (days); "
                                                                 "calibrate at the window you sweep with."),
              max_period: float | None = typer.Option(None, help="Override the maximum search period (days)."),
              gmag_max: float = 19.0, background_only: bool = typer.Option(True, "--background-only/--all"),
              psffrac_min: float = 0.5, retention: str | None = None, verbose: bool = False):
    """Null calibration of sde_min: search each target's real, inverted and quarter-scrambled flux and append the top
    SDEs to a JSON-lines file. Resumable: targets already in the file are skipped."""
    from skysift import calibrate as cal
    from skysift.sources import PARSERS
    kinds = tuple(k.strip() for k in nulls.split(",") if k.strip())
    if bad := [k for k in kinds if k not in cal.NULLS]:
        raise typer.BadParameter(f"unknown null {', '.join(bad)}; choose from {', '.join(cal.NULLS)}",
                                 param_hint="--nulls")
    logging.basicConfig(level=logging.INFO if verbose else logging.WARNING,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    console = _console(stderr=True)
    s, store, blob = _ctx(retention, workers_cpu=workers_cpu)
    _period_window(s, min_period, max_period)
    src = _source(source, store, blob)
    path = Path(out) if out else cal.default_path(s.data_dir, source)
    _plain(console, f"period window: {s.thresholds.min_period_days:g} to {s.thresholds.max_period_days:g} days; "
                    f"writing {path}")
    try:
        summary = cal.run(src, store, blob, s,
                          filter={"gmag_max": gmag_max, "background_only": background_only, "psffrac_min": psffrac_min},
                          limit=limit, out=path, nulls=kinds, parse=PARSERS.get(source),
                          progress=lambda sm: _plain(console, f"written {sm['written']} of {sm['targets']}"
                                                              f" (skipped {sm['skipped']}, errors {sm['errors']})",
                                                     end="\r"))
    except cal.VersionMismatch as e:
        raise typer.BadParameter(str(e), param_hint="--out") from e
    except DiskBudgetExceeded as e:
        _plain(console, f"\nstopped: {e}; the same command resumes")
        raise typer.Exit(3) from None
    except KeyboardInterrupt:
        _plain(console, "\ninterrupted: every finished target is in the file; the same command resumes")
        raise typer.Exit(130) from None
    _plain(console, "")
    _plain(_console(), jsonutil.dumps(summary, indent=1))


@app.command("calibrate-report")
def calibrate_report(file: str = typer.Argument(..., help="A calibrate output file."),
                     all_versions: bool = typer.Option(False, "--all-versions",
                                                       help="Pool lines written by different search versions.")):
    """Per kind (real, inverted, scrambled): curves, median and maximum top SDE, counts at or above 7 to 15, and how
    many real curves clear sde_min."""
    from skysift import calibrate as cal
    if not Path(file).is_file():
        raise typer.BadParameter(f"{file} is not a file", param_hint="FILE")
    s = load_settings()
    try:
        summary = cal.summarise(cal.read_rows(Path(file)), s.thresholds.sde_min, all_versions=all_versions)
    except ValueError as e:
        raise typer.BadParameter(str(e), param_hint="--all-versions") from e
    _plain(_console(), cal.format_table(summary))


@app.command()
def status():
    """Counts, targets per state, outcomes, rung verdicts, throughput, cache usage, recent errors."""
    from skysift.ui import queries
    s, store, blob = _ctx()
    ret = Retention(RetentionPolicy.parse(s.retention), store, blob)
    _plain(_console(), jsonutil.dumps(queries.summary(store, ret, s), indent=1, default=str))


@app.command()
def vet(source: str = typer.Option(...), target_id: str = typer.Option(...), detector: str = "box_dip",
        spectrum: bool = typer.Option(False, "--spectrum", help="Also write the raw BLS spectrum (.npz, about 2 MB).")):
    """Run detection and the full ladder on one target and print every metric and the periodogram summary (files
    are kept)."""
    console = _console(stderr=True)
    s, store, blob = _ctx(retention="keep")
    src = _source(source, store, blob)
    from skysift.pipeline import DEFAULT_LADDER_VERSION, _cpu_work
    target = next((t for t in src.list_targets({"gmag_max": 99, "background_only": False, "psffrac_min": 0})
                   if t.target_id == target_id), None)
    if target is None:
        raise typer.BadParameter(f"target {target_id} not found in source {source}")
    lc, sha, url = src.fetch_lightcurve(target)
    store.pin_target_artifacts(target.target_id)      # the kept file must survive a later budget sweep
    _plain(console, f"fetched {url} sha256={sha}")
    vet_dir = s.data_dir / "vet"
    vet_dir.mkdir(parents=True, exist_ok=True)
    spectrum_path = str(vet_dir / f"{target_id}_spectrum.npz") if spectrum else None
    out = _cpu_work(pickle.dumps(lc), target.model_dump_json(), s.thresholds.model_dump_json(), detector,
                    DEFAULT_LADDER_VERSION, True, store_periodogram="all", spectrum_path=spectrum_path)
    data = json.loads(out)
    for q in data.get("queries", []):                 # provenance: every catalogue request the rungs made
        store.log_query(q["service"], q["url"], q.get("params") or {}, q.get("status"), q.get("nbytes") or 0,
                        q.get("sha256"), error=q.get("error") or "", target_id=target.target_id)
    text = jsonutil.dumps(data, indent=1)
    path = vet_dir / f"{target_id}.json"
    path.write_text(text, encoding="utf-8")
    _plain(_console(), text)
    _plain(console, f"wrote {path}")
    if spectrum_path:
        _plain(console, f"wrote {spectrum_path}")


@app.command()
def ui(host: str = "127.0.0.1", port: int = 8765, no_browser: bool = typer.Option(False, "--no-browser")):
    """Start the local dashboard (loopback only) and open it in the browser."""
    import os
    import threading
    import webbrowser

    import uvicorn

    from skysift.ui.app import LOOPBACK, create_app
    from skysift.ui.assets import ensure_plotly
    from skysift.ui.sweeps import SweepLauncher

    if host not in LOOPBACK and not (host == "0.0.0.0" and os.environ.get("SKYSIFT_UI_ALLOW_CONTAINER") == "1"):
        raise typer.BadParameter(f"{host} is not a loopback address; the dashboard has no authentication. "
                                 "Reach it remotely through an SSH tunnel: ssh -L 8765:127.0.0.1:8765 <host>",
                                 param_hint="--host")
    s, store, blob = _ctx()
    (s.data_dir / "ui").mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s",
                        handlers=[logging.FileHandler(s.data_dir / "ui" / "dashboard.log", encoding="utf-8")])
    plotly = ensure_plotly(s.data_dir)
    application = create_app(s, store=store, blob=blob, launcher=SweepLauncher(s.data_dir), plotly_path=plotly)
    shown = "127.0.0.1" if host == "0.0.0.0" else host
    url = f"http://[{shown}]:{port}/" if ":" in shown else f"http://{shown}:{port}/"
    _plain(_console(stderr=True), f"SkySift dashboard at {url}  (Ctrl+C to stop)")
    if not no_browser:
        threading.Timer(1.5, webbrowser.open, args=(url,)).start()
    uvicorn.run(application, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    app()
