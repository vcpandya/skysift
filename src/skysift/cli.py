from __future__ import annotations

import json
import logging
import pickle
import sys

import typer
from rich.console import Console
from rich.table import Table as RTable
from sqlalchemy import select

from skysift.config import load_settings
from skysift.sources.base import load_registry
from skysift.store.blob import blob_from_url
from skysift.store.db import RunRow, SignalRow, Store
from skysift.store.retention import Retention, RetentionPolicy

app = typer.Typer(help="SkySift: archival time-series search and vetting engine")


def _console() -> Console:
    """On a real terminal rich uses the terminal width; piped (or under a test runner) it gets a wide fixed
    width, so wide tables are not wrapped or truncated."""
    return Console() if sys.stdout.isatty() else Console(width=200)


def _plain(console: Console, text: str, **kw) -> None:
    """Print text verbatim: no rich markup, highlighting or wrapping (JSON holds brackets and long lines)."""
    console.print(text, markup=False, highlight=False, soft_wrap=True, **kw)


def _ctx(retention: str | None = None, workers_io: int | None = None, workers_cpu: int | None = None):
    s = load_settings()
    if retention: s.retention = retention
    if workers_io: s.workers_io = workers_io
    if workers_cpu: s.workers_cpu = workers_cpu
    s.data_dir.mkdir(parents=True, exist_ok=True)
    store = Store(s.database_url); store.init_schema()
    return s, store, blob_from_url(s.blob_url)


def _source(name: str, store, blob):
    if name == "kbonus_bkg":
        from skysift.sources.kbonus_bkg import KBonusBkg
        return KBonusBkg(blob, store)
    raise typer.BadParameter(f"source {name} is not live yet; see `skysift sources`")


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


@app.command()
def run(source: str = typer.Option(...), gmag_max: float = 19.0,
        background_only: bool = typer.Option(True, "--background-only/--all"), psffrac_min: float = 0.5,
        limit: int | None = None, detector: str = "box_dip", workers_io: int | None = None,
        workers_cpu: int | None = None, retention: str | None = None,
        min_period: float | None = typer.Option(None, help="Override the minimum search period (days)."),
        max_period: float | None = typer.Option(None, help="Override the maximum search period (days)."),
        verbose: bool = False):
    """Sweep a source: fetch, detect, vet, store, release files."""
    logging.basicConfig(level=logging.INFO if verbose else logging.WARNING,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    console = _console()
    s, store, blob = _ctx(retention, workers_io, workers_cpu)
    if min_period is not None: s.thresholds.min_period_days = min_period
    if max_period is not None: s.thresholds.max_period_days = max_period
    from skysift import pipeline
    src = _source(source, store, blob)
    _plain(console, f"period window: {s.thresholds.min_period_days:g} to {s.thresholds.max_period_days:g} days")
    summary = pipeline.run(src, store, blob, s, filter={"gmag_max": gmag_max, "background_only": background_only,
                                                       "psffrac_min": psffrac_min, "limit": limit},
                           detector_name=detector, max_targets=limit,
                           progress=lambda sm: _plain(
                               console, f"processed {sm['processed']} signals {sm['signals']} outcomes {sm['outcomes']}", end="\r"))
    _plain(console, json.dumps(summary, indent=1))


@app.command()
def status():
    """Counts, outcomes, throughput, cache usage, recent errors."""
    s, store, blob = _ctx()
    c = store.counts()
    policy = RetentionPolicy.parse(s.retention)
    ret = Retention(policy, store, blob)
    with store.session() as ses:
        errors = ses.scalars(select(RunRow).where(RunRow.status == "error").order_by(RunRow.id.desc()).limit(5)).all()
        done = ses.scalars(select(RunRow).where(RunRow.status == "done")).all()
        outcomes: dict[str, int] = {}
        for row in ses.scalars(select(SignalRow)): outcomes[row.final_status] = outcomes.get(row.final_status, 0) + 1
        seconds = sum((r.finished - r.started).total_seconds() for r in done if r.finished)
        hours = max(1e-9, seconds / 3600)
        out = {"counts": c, "signal_outcomes": outcomes, "targets_per_hour": round(len(done) / hours, 1),
               "mean_seconds_per_run": round(seconds / len(done), 2) if done else None,
               "cache_bytes": ret.cached_bytes(), "retention": s.retention,
               "cache_budget_bytes": policy.budget_bytes if policy.mode == "budget" else None,
               "last_errors": [{"target": e.target_id, "error": e.error[:200]} for e in errors]}
    _plain(_console(), json.dumps(out, indent=1, default=str))


@app.command()
def vet(source: str = typer.Option(...), target_id: str = typer.Option(...), detector: str = "box_dip"):
    """Run detection and the full ladder on one target and print every metric (files are kept)."""
    console = _console()
    s, store, blob = _ctx(retention="keep")
    src = _source(source, store, blob)
    from skysift.pipeline import DEFAULT_LADDER_VERSION, _cpu_work
    target = next((t for t in src.list_targets({"gmag_max": 99, "background_only": False, "psffrac_min": 0})
                   if t.target_id == target_id), None)
    if target is None:
        raise typer.BadParameter(f"target {target_id} not found in source {source}")
    lc, sha, url = src.fetch_lightcurve(target)
    _plain(console, f"fetched {url} sha256={sha}")
    out = _cpu_work(pickle.dumps(lc), target.model_dump_json(), s.thresholds.model_dump_json(), detector,
                    DEFAULT_LADDER_VERSION, True)
    text = json.dumps(json.loads(out), indent=1)
    path = s.data_dir / "vet" / f"{target_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    _plain(console, text)
    _plain(console, f"wrote {path}")


if __name__ == "__main__":
    app()
