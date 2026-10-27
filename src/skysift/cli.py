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


def _read_target_list(path: Path) -> list[str]:
    """Target ids from a text file, one per line; blank lines and # comments skipped, duplicates dropped, order kept."""
    if not path.is_file():
        raise typer.BadParameter(f"{path} is not a file", param_hint="--targets")
    seen: dict[str, None] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        tid = line.split("#", 1)[0].strip()
        if tid: seen.setdefault(tid, None)
    return list(seen)


@app.command()
def run(source: str = typer.Option(...), gmag_max: float = 19.0,
        background_only: bool = typer.Option(True, "--background-only/--all", help="Skip stars Kepler itself targeted (catalogue kic equal to tpforg); --all includes them."), psffrac_min: float = 0.5,
        limit: int | None = typer.Option(None, help="Process the N highest-priority targets not yet done."),
        targets: str | None = typer.Option(None, "--targets", help="Sweep only the target ids listed in this file "
                                           "(one per line, # comments), in the file's order; the registry filters still apply."),
        detector: str = "box_dip", workers_io: int | None = None,
        workers_cpu: int | None = None, retention: str | None = None,
        min_period: float | None = typer.Option(None, help="Override the minimum search period (days)."),
        max_period: float | None = typer.Option(None, help="Override the maximum search period (days)."),
        periodogram: str | None = typer.Option(None, "--periodogram",
                                               help="Store the SDE envelope per target: all (default), signals, off."),
        no_recheck: bool = typer.Option(False, "--no-recheck",
                                        help="Skip the stage-2 recheck of open signals after the sweep."),
        no_pixels: bool = typer.Option(False, "--no-pixels",
                                       help="Recheck after the sweep without the pixel stage (no pixel files)."),
        verbose: bool = False):
    """Sweep a source: fetch, detect, vet, store, release files; then recheck the open signals (stage 2, all four
    steps, the pixel stage included) unless --no-recheck; --no-pixels leaves the pixel stage out."""
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
    flt: dict = {"gmag_max": gmag_max, "background_only": background_only, "psffrac_min": psffrac_min}
    if targets is not None:
        flt["target_ids"] = _read_target_list(Path(targets))
        _plain(console, f"targets: {len(flt['target_ids'])} ids from {targets}")
    _plain(console, f"period window: {s.thresholds.min_period_days:g} to {s.thresholds.max_period_days:g} days")
    if warning := _calibration_warning(s, source): _plain(console, warning)
    interrupt = InterruptOnce(lambda text: _plain(console, f"\n{text}"), stop_request_path(s.data_dir))
    try:
        previous = signal.signal(signal.SIGINT, interrupt)
    except ValueError:                                  # not the main thread (embedding): Ctrl+C keeps its default
        previous = None
    try:
        summary = pipeline.run(src, store, blob, s, filter=flt, detector_name=detector, max_targets=limit,
                               progress=lambda sm: _plain(
                                   console, f"processed {sm['processed']} signals {sm['signals']} outcomes {sm['outcomes']}",
                                   end="\r"),
                               stop_requested=interrupt.check, recheck=not no_recheck, pixels=not no_pixels)
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
    if summary.get("recheck") is not None:
        _plain(console, f"recheck: {_recheck_line(summary['recheck'])}")


def _recheck_line(report: dict) -> str:
    """One line for a recheck report: signals considered, the steps that ran, the outcomes that occurred and the
    decided signals whose pixel rerun disagrees with the decision (``decided_pixel_changed``, when any)."""
    if "error" in report:
        return f"{'stopped' if report.get('stopped') else 'failed'}, {report['error']}"
    steps = ", ".join(f"{k} {v}" for k, v in report.get("steps", {}).items() if v) or "no step ran"
    outcomes = ", ".join(f"{k} {v}" for k, v in report.get("outcomes", {}).items() if v) or "none"
    stopped = "; stopped early, the next recheck takes the rest" if report.get("stopped") else ""
    changed = report.get("decided_pixel_changed") or 0
    flagged = (f"; decided pixel verdicts changed: {changed} (each dossier flags it for a reviewer)"
               if changed else "")
    return f"considered {report.get('considered', 0)}; steps: {steps}; outcomes: {outcomes}{flagged}{stopped}"


@app.command()
def calibrate(source: str = typer.Option(...),
              limit: int = typer.Option(1000, min=1, help="Search the N highest-priority targets (the order of run)."),
              nulls: str = typer.Option("scrambled,inverted", help="Null curves to search besides the real flux."),
              workers_cpu: int | None = None,
              out: str | None = typer.Option(None, help="Output file (default <data_dir>/calibration/<source>.jsonl)."),
              min_period: float | None = typer.Option(None, help="Override the minimum search period (days); "
                                                                 "calibrate at the window you sweep with."),
              max_period: float | None = typer.Option(None, help="Override the maximum search period (days)."),
              gmag_max: float = 19.0, background_only: bool = typer.Option(True, "--background-only/--all", help="Skip stars Kepler itself targeted (catalogue kic equal to tpforg); --all includes them."),
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
def dossier(target: str = typer.Option(..., "--target", help="The target id, e.g. kic-006146676."),
            signal: int | None = typer.Option(None, "--signal", help="A signal id of the target (default: its best "
                                              "signal, stage 2 then approved then stage 1, then SDE)."),
            export: str | None = typer.Option(None, "--export", help="Also write the standalone HTML page here."),
            force: bool = typer.Option(False, "--force", help="Regenerate even when the dossier is current."),
            further: bool = typer.Option(False, "--further", help="Store this signal's dossier as the target's "
                                         "second candidate, beside the target's own, not in its place (needs "
                                         "--signal).")):
    """Generate the target's dossier_data.json and its dossiers row (a no-op when the tests, status and decision are
    unchanged); with --export, the standalone page too. With --further, the dossier of the target's second candidate
    (a signal named by --signal) is written beside the target's own. Prints the report: a table in a terminal, JSON
    when piped."""
    from skysift import dossier as ds
    from skysift import net
    console = _console(stderr=True)
    if further and export:
        _plain(console, "--export writes the target's own page; a further dossier has none yet")
        raise typer.Exit(2)
    s, store, blob = _ctx()
    try:
        rep = ds.generate(store, blob, s, target, signal_id=signal, force=force, fetch=net.fetch,
                          log=lambda text: _plain(console, text), further=further)
    except ds.NoSignal as e:
        _plain(console, f"{e}: nothing to build a dossier from")
        raise typer.Exit(1) from None
    except (ds.LightCurveReleased, ds.PriorClaimsError, ds.NoExtinction, ds.ExtinctionError,
            ds.FurtherRefused) as e:
        _plain(console, str(e))
        raise typer.Exit(2) from None
    report = rep.as_dict()
    if export:
        report["export_path"] = str(ds.write_export(store, rep, Path(export)))
    if sys.stdout.isatty():
        t = RTable(title=f"Dossier {target}", show_header=False)
        t.add_column("field"); t.add_column("value")
        for key, value in report.items():
            t.add_row(key, value if isinstance(value, str) else jsonutil.dumps(value))
        _console().print(t)
    else:
        _plain(_console(), jsonutil.dumps(report, indent=1))
    if rep.gaia_errors:             # the dossier is written, but its star or neighbours are incomplete
        for err in rep.gaia_errors if not rep.generated else ():      # a fresh run printed them as they came
            _plain(console, f"Gaia query failed: {err}")
        raise typer.Exit(3)


@app.command("pack")
def pack_command(target: str = typer.Option(..., "--target", help="The target id, e.g. kic-006146676."),
                 out: str | None = typer.Option(None, "--out", help="The pack folder (default <data_dir>/packs/"
                                                "<target>/<UTC yyyymmdd>); the zip is written inside it."),
                 tic: str | None = typer.Option(None, "--tic", help="The star's TIC id, named in the ExoFOP "
                                                "upload note."),
                 tag: str = typer.Option("skysift", "--tag", help="The word that starts the description of the "
                                         "suggested ExoFOP Data Tag."),
                 tag_seq: int = typer.Option(1, "--tag-seq", help="The Data Tag's five-digit sequence number (1 "
                                             "for the first upload of the day)."),
                 zenodo_draft: bool = typer.Option(False, "--zenodo", help="Also make a Zenodo draft (sandbox) with "
                                                   "the token in SKYSIFT_ZENODO_TOKEN; it is never published."),
                 zenodo_live: bool = typer.Option(False, "--zenodo-live", help="Make the Zenodo draft on zenodo.org "
                                                  "instead of the sandbox (implies --zenodo); refused before a "
                                                  "reviewer approves the candidate, while the false-positive "
                                                  "estimate is provisional, and while the stored dossier's "
                                                  "ephemeris or transit shape carries no error."),
                 final: bool = typer.Option(False, "--final", help="Refuse empty author fields and any placeholder "
                                            "DOI (needs --zenodo-live).")):
    """Build the submission pack from the target's stored dossier: the README's five steps, the Research Note draft,
    the data files, figures, provenance and the ExoFOP copies, and a zip. With --zenodo, also a Zenodo draft
    deposition holding the zip, made after every file is written; publishing it is your click on Zenodo. Prints the
    report: a table in a terminal, JSON when piped."""
    from skysift import dossier as ds
    from skysift import fpp
    from skysift import pack as pk
    from skysift import zenodo as zn
    console = _console(stderr=True)
    if final and not zenodo_live:               # spec 11.6: only a live draft reserves the record's DOI
        _plain(console, pk.FINAL_NEEDS_LIVE)
        raise typer.Exit(2)
    if zenodo_live and not fpp.SIGNED_OFF:       # the provisional FPP must not reach a live record, token or not
        _plain(console, "--zenodo-live is refused while the false-positive estimate is provisional (spec 4.2's factor "
                        "tables are not signed off); use --zenodo for a sandbox rehearsal")
        raise typer.Exit(2)
    s, store, blob = _ctx()
    want_zenodo = zenodo_draft or zenodo_live
    token = zn.token_from_env() if want_zenodo else None
    client = zn.ZenodoClient(token, sandbox=not zenodo_live, session=zn.new_session()) if token else None
    try:
        rep = pk.build_pack(store, blob, s, target, out_dir=Path(out) if out else None, tic=tic, observer_tag=tag,
                            tag_seq=tag_seq, zenodo=client, final=final,
                            log=lambda text: _plain(console, text))
    except (pk.NoDossier, ds.NoSignal) as e:
        _plain(console, str(e))
        raise typer.Exit(1) from None
    except (ds.LightCurveReleased, pk.PackError) as e:
        _plain(console, str(e))            # a refused live draft names every problem (pack.gate_problems)
        raise typer.Exit(2) from None
    except zn.ZenodoError as e:
        _plain(console, str(e))            # names the built pack and any unfinished draft
        raise typer.Exit(1) from None
    report = rep.as_dict()
    if sys.stdout.isatty():
        t = RTable(title=f"Submission pack {target}", show_header=False)
        t.add_column("field"); t.add_column("value")
        for key, value in report.items():
            t.add_row(key, value if isinstance(value, str) else jsonutil.dumps(value))
        _console().print(t)
    else:
        _plain(_console(), jsonutil.dumps(report, indent=1))
    if rep.zenodo:
        where = "sandbox draft" if rep.zenodo["sandbox"] else "draft"
        _plain(console, f"Zenodo {where}, not published: {rep.zenodo['draft_url']}. Read it there and press Publish "
                        "when it is right.")
    if want_zenodo and client is None:
        _plain(console, f"{zn.TOKEN_ENV} is not set; the pack was built without a Zenodo draft")
        raise typer.Exit(2)


@app.command("crosscheck")
def crosscheck_command(target: str = typer.Option(..., "--target", help="The target id, e.g. kic-006146676."),
                       export: str | None = typer.Option(None, "--export", help="Write the TRICERATOPS inputs from "
                                                         "the stored dossier into this folder."),
                       result: str | None = typer.Option(None, "--import", help="Store this result file of "
                                                         "tools/triceratops_check.py on the dossier."),
                       signal: int | None = typer.Option(None, "--signal", help="The signal id of a further dossier "
                                                         "(skysift dossier --further); default the target's own "
                                                         "dossier.")):
    """The TRICERATOPS false-positive cross-check. --export writes inputs.json, lightcurve.csv and the pixel files
    for tools/triceratops_check.py, which runs under .venv-triceratops; --import checks its result file and stores it
    on the dossier with whether it was made for the stored dossier's inputs. --signal works on the further dossier of
    that signal (skysift dossier --further) instead of the target's own. Prints JSON. Exits 1 without a dossier,
    2 for a bad result file, an export that found no pixel file or a dossier below the current version."""
    from skysift import crosscheck as cc
    from skysift import dossier as ds
    console = _console(stderr=True)
    if (export is None) == (result is None):
        _plain(console, "give one of --export or --import")
        raise typer.Exit(2)
    s, store, blob = _ctx()
    try:
        if export is not None:
            out = cc.export_inputs(store, blob, s, target, Path(export), signal_id=signal)
            inputs = json.loads((out / "inputs.json").read_text(encoding="utf-8"))
            report = {"target_id": target, "inputs": str(out), "fingerprint": inputs["fingerprint"],
                      "quarters": inputs["quarters"], "lightcurve_points": inputs["lightcurve"]["n"]}
        else:
            block = cc.import_result(store, target, Path(result), signal_id=signal)
            report = {"target_id": target,
                      **{k: block[k] for m in ("fpp", "fpp_no_stp", "nfpp") for k in (m, f"{m}_p16", f"{m}_p84")},
                      "n_runs": block["n_runs"], "n_degenerate": block["n_degenerate"], "class": block["class"],
                      "current": block["current"], "blocks_live_gate": cc.verdict(block)[0],
                      "live_gate_reason": cc.verdict(block)[1]}
        if signal is not None:
            report["signal_id"] = signal
    except cc.NoDossier as e:
        _plain(console, str(e))
        raise typer.Exit(1) from None
    except (cc.BadResult, cc.NoPixelFiles, cc.OldDossier, ds.LightCurveReleased) as e:
        _plain(console, str(e))
        raise typer.Exit(2) from None
    _plain(_console(), jsonutil.dumps(report, indent=1))


_SIGNAL_IDS = typer.Option(None, "--signal-id", help="Recheck only this signal (repeat for several).")
BELOW_NORMAL_PRIORITY_CLASS = 0x00004000          # Windows priority class of a background job
RECHECK_NICE = 10                                 # the POSIX stand-in


def _lower_own_priority(platform: str = sys.platform) -> bool:
    """Run this process below normal priority (Windows) or at nice ``RECHECK_NICE`` (elsewhere), so a standalone
    recheck yields the CPU to a sweep running beside it, as the dashboard's background jobs do. A refusal is logged
    and the process keeps its priority. Returns whether the priority was lowered."""
    try:
        if platform == "win32":
            import ctypes
            kernel32 = ctypes.windll.kernel32
            return bool(kernel32.SetPriorityClass(kernel32.GetCurrentProcess(), BELOW_NORMAL_PRIORITY_CLASS))
        import os
        os.nice(RECHECK_NICE)
        return True
    except (OSError, AttributeError) as e:
        logging.getLogger(__name__).warning("could not lower the recheck's priority: %s", e)
        return False


@app.command("recheck")
def recheck_command(signal_id: list[int] | None = _SIGNAL_IDS,
                    no_pixels: bool = typer.Option(False, "--no-pixels", help="Leave out the pixel stage."),
                    retention: str | None = typer.Option(None, help="keep, stream or budget:<GB> (default: the "
                                                         "configured one); stream releases a signal's pixel files "
                                                         "once its pixel step is written, keep keeps them."),
                    verbose: bool = False):
    """Stage 2: recheck the undetermined signals and stage-1 candidates of the current search version that no reviewer
    has decided on (ladder re-vet, half-period re-vet, catalogue retry, pixel stage), each under the search window it
    was found with, at below-normal priority. Prints the report as JSON."""
    from skysift import net
    from skysift import recheck as stage2
    logging.basicConfig(level=logging.INFO if verbose else logging.WARNING,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    console = _console(stderr=True)
    s, store, blob = _ctx(retention)
    _lower_own_priority()
    rep = stage2.recheck(store, blob, s, fetch=net.fetch, signal_ids=signal_id or None, pixels=not no_pixels,
                         log=lambda text: _plain(console, text),
                         retention=Retention(RetentionPolicy.parse(s.retention), store, blob))
    _plain(_console(), jsonutil.dumps(rep.as_dict(), indent=1))


@app.command()
def inject(source: str = typer.Option(...),
           limit: int = typer.Option(30, min=1, help="Inject into the N highest-priority targets (the order of run)."),
           per_star: int = typer.Option(4, min=1, help="Injections per target, cycling through the --snr values."),
           snr: str = typer.Option("7,10,15,25", help="Nominal signal-to-noise of the injected transits, comma-separated."),
           workers_cpu: int | None = None,
           out: str | None = typer.Option(None, help="Output file (default <data_dir>/injections/<source>.jsonl)."),
           min_period: float | None = typer.Option(None, help="Override the minimum search period (days)."),
           max_period: float | None = typer.Option(None, help="Override the maximum search period (days)."),
           gmag_max: float = 19.0, background_only: bool = typer.Option(True, "--background-only/--all", help="Skip stars Kepler itself targeted (catalogue kic equal to tpforg); --all includes them."),
           psffrac_min: float = 0.5, seed: int = typer.Option(1, help="Seed of the injection draws."),
           retention: str | None = None, verbose: bool = False):
    """Injection and recovery: inject box transits into each target's cached raw light curve, run the production search
    and rungs 1 to 3, and append one line per injection (found, ranked first, ladder outcome) to a JSON-lines file.
    Resumable: targets already in the file are skipped."""
    from skysift import inject as inj
    from skysift.sources import PARSERS
    try:
        snr_targets = tuple(float(x) for x in snr.split(",") if x.strip())
    except ValueError as e:
        raise typer.BadParameter(f"not a comma-separated list of numbers: {snr!r}", param_hint="--snr") from e
    if not snr_targets or any(x <= 0 for x in snr_targets):
        raise typer.BadParameter("give at least one positive value", param_hint="--snr")
    logging.basicConfig(level=logging.INFO if verbose else logging.WARNING,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    console = _console(stderr=True)
    s, store, blob = _ctx(retention, workers_cpu=workers_cpu)
    _period_window(s, min_period, max_period)
    src = _source(source, store, blob)
    path = Path(out) if out else inj.default_path(s.data_dir, source)
    _plain(console, f"period window: {s.thresholds.min_period_days:g} to {s.thresholds.max_period_days:g} days; "
                    f"writing {path}")
    try:
        summary = inj.run(src, store, blob, s,
                          filter={"gmag_max": gmag_max, "background_only": background_only, "psffrac_min": psffrac_min},
                          limit=limit, per_star=per_star, snr_targets=snr_targets, out=path, workers_cpu=s.workers_cpu,
                          seed=seed, parse=PARSERS.get(source),
                          progress=lambda sm: _plain(
                              console, f"{sm['written']} injections from {sm['stars']} of {sm['targets']} targets"
                                       f" (skipped {sm['skipped']}, errors {sm['errors']})", end="\r"))
    except inj.VersionMismatch as e:
        raise typer.BadParameter(str(e), param_hint="--out") from e
    except DiskBudgetExceeded as e:
        _plain(console, f"\nstopped: {e}; the same command resumes")
        raise typer.Exit(3) from None
    except KeyboardInterrupt:
        _plain(console, "\ninterrupted: every finished target is in the file; the same command resumes")
        raise typer.Exit(130) from None
    _plain(console, "")
    _plain(_console(), jsonutil.dumps(summary, indent=1))


@app.command("inject-report")
def inject_report(file: str = typer.Argument(..., help="An inject output file."),
                  all_versions: bool = typer.Option(False, "--all-versions",
                                                    help="Pool lines written by different search or ladder versions.")):
    """Completeness against nominal and red-noise signal-to-noise and against period: injections found, ranked first and
    passing rungs 1 to 3, with the median recovered-to-injected depth ratio."""
    from skysift import inject as inj
    if not Path(file).is_file():
        raise typer.BadParameter(f"{file} is not a file", param_hint="FILE")
    try:
        summary = inj.summarise(inj.read_rows(Path(file)), all_versions=all_versions)
    except ValueError as e:
        raise typer.BadParameter(str(e), param_hint="--all-versions") from e
    _plain(_console(), inj.format_table(summary))


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
    # filename=, not handlers=[FileHandler(...)]: basicConfig opens the file only when it installs the handler, so a
    # root logger that already has one (pytest's capture, any embedding) leaves no unclosed file behind
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s",
                        filename=s.data_dir / "ui" / "dashboard.log", encoding="utf-8")
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
