"""Dossier generation (dossiers spec 5.4): one ``dossier_data.json`` per target, built from stored rows and files plus
the four Gaia queries, and the ``dossiers`` row that says which signal, versions and test rows it was built from.

``generate`` picks the target's best signal (``STATUS_ORDER`` then SDE), compares the fingerprint of its latest test
rows, status and decision with the stored row (a match is a no-op unless forced), reloads the light curve through the
engine's loader and whitens it as the sweep did, thins the plot arrays to the sizes of the hand-built KIC 6146676 page
(``docs/candidates/kic-006146676/prep_data.py``), reads rung 5's ``pixels.npz`` artifact when present, asks Gaia for
the star and its neighbours (``stars``), and computes the two probabilities (``calibrate.false_alarm``,
``fpp.compute``). It never changes a signal's status, and it reaches the network only through the injected ``fetch``.
``export_html`` renders the JSON as one standalone file with its own level toggle and no external URL.

Every sentence here is built from stored numbers by a rule (spec 5.7); the hand-written narrative of the KIC 6146676
page stays in ``docs/candidates`` as the record."""
from __future__ import annotations

import base64
import copy
import dataclasses
import hashlib
import html
import io
import json
import logging
import math
import os
import re
import subprocess
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path, PureWindowsPath

import numpy as np
from sqlalchemy import select

from skysift import calibrate as cal
from skysift import jsonutil, net, stars
from skysift.calibrate import FalseAlarm
from skysift.detectors.base import get_detector
from skysift.detectors.periodogram import PeriodogramSummary
from skysift.fpp import FPP_MODEL_VERSION, Fpp, FppInputs, Neighbour, compute
from skysift.lc.loader import TARGET_FIELDS, LightCurveLoader
from skysift.lc.ops import event_depths, event_quality, fold_phase, season_of
from skysift.lc.transit_model import uniform_disc_flux
from skysift.models import LightCurve, Target
from skysift.net import ServiceUnavailable
from skysift.pipeline import search_version
from skysift.priors import NO_PIXEL_CONFUSION_ARCSEC, NO_PIXEL_FALLOFF_ARCSEC, PIXEL_ARCSEC
from skysift.store.db import (
    PERIODOGRAM_URL,
    PIXELS_URL,
    ArtifactRow,
    CandidateRow,
    DecisionRow,
    QueryRow,
    RecheckRow,
    RunRow,
    SignalRow,
    Store,
    TargetRow,
    utcnow,
)
from skysift.ui import explain
from skysift.ui.planet_svg import star_word
from skysift.ui.plots import whitened_fnorm
from skysift.vetting.rung1_detection import SYSTEMATIC_PERIODS

log = logging.getLogger(__name__)

DOSSIER_VERSION = "2"           # 2: the FPP model version joins the fingerprint (21 October 2026)
STATUS_ORDER = ("candidate_stage2", "approved", "candidate_stage1")
CLOSE_SIGMA = 3.5               # a voting season nearer the 3 sigma decision than this gets the Monte Carlo caveat
MIN_VOTING_SEASONS = 2          # rung 5 passes on two voting seasons at least; below that the FPP keeps the no-pixel rule
CLOSE_CALL = ("The centroid error comes from 200 Monte Carlo draws, so a season this close to the 3 sigma threshold "
              "can move by about 5 percent with the seed.")
SERIES_BIN_DAYS = 0.25
GALLERY_HALF_HOURS = 8.0
SPECTRUM_BINS = 1500
FOLD_POINTS = 4000              # the fold scatter, strided; the bins use every point
FOLD_WINDOW_DURATIONS = 4.0     # the fold, odd/even and secondary windows: +-4 durations, as lc_analysis.py
MODEL_POINTS = 400
ALIAS_FACTORS = (0.5, 2.0, 3.0)
STARS_SEED = 20261021           # the seed of stars.lookup, so a dossier reproduces the lookup's draws
SIG_FIGS = 8                    # every float in the JSON outside the plot arrays (which carry their own rounding)
BKJD_OFFSET = 2454833.0
SUN_ANGULAR_DEG = 0.533         # the Sun's angular diameter seen from Earth
RSUN_AU = 0.00465047
STATIC_DIR = Path(__file__).parent / "ui" / "static"
DOSSIER_URL = "skysift://dossier/{target_id}"
EXPORT_URL = "skysift://dossier-export/{target_id}"

# printed under each number, verbatim from spec 4.1 and 4.2
FAP_NOT_COVERED = "This is about noise. A real dip on a neighbouring star or an eclipsing binary passes this test."
FPP_NOT_COVERED = ("This counts the common impostors with simple priors. It is not a vespa or a Kepler DR25 "
                   "false-positive probability, and it has no radial-velocity or imaging input.")
SECTIONS = ("The signal", "Does it repeat", "The checks", "Which star is dimming", "The star and the planet",
            "How it might look", "What is not yet known", "How to reproduce")
GAIA_STAR, GAIA_CONE = "star row", "neighbour cone"      # the two Gaia query groups, as error prefixes
_HTTP_4XX = re.compile(r"HTTP (4\d\d)\b")
RELEASED_REMEDY = "re-run the sweep for this target with --retention keep"
# what a broken section raises: a missing or unreadable file, a bad archive, a missing array, a mis-shaped array
SECTION_ERRORS = (OSError, ValueError, KeyError, IndexError, zipfile.BadZipFile)


class NoSignal(KeyError):
    """The target has no signal (or not the one asked for): there is nothing to build a dossier from."""

    def __str__(self) -> str:
        return str(self.args[0]) if self.args else "no signal"


class LightCurveReleased(RuntimeError):
    """The signal's raw light curve is no longer in the blob. A dossier is built offline except for the Gaia
    queries (spec 5.8), so it is not fetched again."""


@dataclass
class DossierReport:
    target_id: str
    signal_id: int
    data_path: Path
    generated: bool                 # False when the fingerprint matched and not force
    fap: FalseAlarm | None          # as stored in the dossier (n = 0, not calibrated, without a calibration file)
    fpp: Fpp
    stale_reason: str | None        # why the stored dossier was regenerated; None when nothing was written
    comparison: dict = field(default_factory=dict)      # filled by Task 7's comparison tool
    gaia_errors: list[str] = field(default_factory=list)   # the Gaia queries that failed (the CLI then exits 3)

    def as_dict(self) -> dict:
        fap = None if self.fap is None else {"words": self.fap.words(), "fap": self.fap.fap, "k": self.fap.k,
                                             "n": self.fap.n, "calibrated": self.fap.calibrated}
        return jsonutil.sanitize({
            "target_id": self.target_id, "signal_id": self.signal_id, "data_path": str(self.data_path),
            "generated": self.generated, "stale_reason": self.stale_reason, "fap": fap,
            "fpp": {"value": self.fpp.reported_value, "words": self.fpp.words(), "label": self.fpp.label(),
                    "pixels": self.fpp.pixels, "neighbours_known": self.fpp.neighbours_known},
            "comparison": self.comparison, "gaia_errors": list(self.gaia_errors)})


# ---------------------------------------------------------------------------------------------------- small helpers

def _r(x, nd: int):
    """``x`` rounded to ``nd`` decimals, None when missing or not finite."""
    if x is None:
        return None
    x = float(x)
    return round(x, nd) if math.isfinite(x) else None


def _sig(x: float) -> float:
    if x == 0 or not math.isfinite(x):
        return x
    return round(x, SIG_FIGS - 1 - math.floor(math.log10(abs(x))))


def _tidy(obj):
    """Strict-JSON-ready copy with every float at ``SIG_FIGS`` significant figures (no float noise in the file)."""
    obj = jsonutil.sanitize(obj)
    if isinstance(obj, dict):
        return {k: _tidy(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_tidy(v) for v in obj]
    if isinstance(obj, float):
        return _sig(obj)
    return obj


def _iso(when) -> str | None:
    return when.isoformat() if hasattr(when, "isoformat") else (str(when) if when else None)


def _num(m: dict, key: str) -> float | None:
    v = m.get(key)
    if isinstance(v, bool) or v is None:
        return None
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _rung(tests: list[dict], rung: str) -> dict | None:
    return next((t for t in tests if t.get("rung") == rung), None)


def _metrics(tests: list[dict], rung: str) -> dict:
    return (_rung(tests, rung) or {}).get("metrics") or {}


def _git_commit() -> str:
    """The short commit of the code that built the dossier, with "-dirty" when the tree has uncommitted changes to
    tracked files (``git describe --always --dirty``); "unknown" outside a checkout."""
    try:
        out = subprocess.run(["git", "describe", "--always", "--dirty", "--abbrev=7", "--match=NO-TAGS"],
                             capture_output=True, text=True, timeout=10, cwd=Path(__file__).resolve().parents[2],
                             check=False).stdout.strip()
        return out or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def target_name(target: dict) -> str:
    if target.get("kic"):
        return f"KIC {int(target['kic'])}"
    if target.get("gaia_id"):
        return f"Gaia DR3 {target['gaia_id']}"
    return str(target.get("target_id"))


# ---------------------------------------------------------------------------------------------------- the signal

def rank_key(signal: dict, decision: str | None) -> tuple:
    """The sort key of a target's signals, best first, shared by ``pick_signal`` and the Dossiers tiles: a signal a
    reviewer rejected ranks below every other whatever its engine status (the store keeps the engine status on a
    rejection); the rest by ``STATUS_ORDER`` (an approved signal ranks as approved even when its engine status is
    stage 1, and a stage-2 signal stays stage 2 when approved), then SDE, then id. ``signal`` holds ``id``,
    ``final_status``, ``sde`` and ``candidate_status``; ``decision`` is the latest reviewer decision or None."""
    cand = signal.get("candidate_status")
    rejected = cand == "rejected" or decision == "reject"
    approved = decision == "approve" or cand in ("approved", "published")
    statuses = {signal["final_status"]} | ({"approved"} if approved else set())
    order = min((STATUS_ORDER.index(s) for s in statuses if s in STATUS_ORDER), default=len(STATUS_ORDER))
    return rejected, order, -float(signal.get("sde") or 0.0), signal["id"]


def pick_signal(store: Store, target_id: str, signal_id: int | None = None) -> dict:
    """The signal row (a dict of its columns plus ``candidate_status`` and the latest ``decision``) the dossier is
    built from: ``signal_id`` when given (it must belong to the target), else the target's best signal: not rejected
    by a reviewer, then by ``STATUS_ORDER`` (an approved signal ranks as approved even when its engine status is
    stage 1), then SDE. Raises NoSignal when there is none."""
    with store.session() as s:
        q = select(SignalRow).where(SignalRow.target_id == target_id)
        if signal_id is not None:
            q = q.where(SignalRow.id == signal_id)
        sigs = s.scalars(q).all()
        if not sigs:
            raise NoSignal(f"no signal {signal_id} for target {target_id}" if signal_id is not None
                           else f"no signal for target {target_id}")
        ids = [r.id for r in sigs]
        cand = {c.signal_id: c.status for c in s.scalars(select(CandidateRow).where(CandidateRow.signal_id.in_(ids)))}
        latest: dict[int, str] = {}
        for d in s.scalars(select(DecisionRow).where(DecisionRow.signal_id.in_(ids)).order_by(DecisionRow.id)):
            latest[d.signal_id] = d.decision
        rows = [{**{c.name: getattr(r, c.name) for c in SignalRow.__table__.columns},
                 "candidate_status": cand.get(r.id), "decision": latest.get(r.id)} for r in sigs]
    return min(rows, key=lambda r: rank_key(r, r["decision"]))


def fingerprint(latest_tests: list[dict], final_status: str, decision: str | None, calibration_rows: int = 0) -> str:
    """sha256 of the sorted test row ids, the final status, the latest decision, the number of rows in the source's
    null calibration (``calibration_rows``) and the FPP model version: a dossier is stale when any of them changed
    since it was written, so one written while the calibration was short goes stale as the null searches arrive, and
    one written under an older false-positive model goes stale with the model."""
    ids = sorted(int(t["id"]) for t in latest_tests)
    text = json.dumps({"tests": ids, "status": final_status, "decision": decision,
                       "calibration_rows": int(calibration_rows), "fpp_model": FPP_MODEL_VERSION}, sort_keys=True)
    return hashlib.sha256(text.encode()).hexdigest()


_ROW_COUNTS: dict[str, tuple[int, int, int]] = {}      # path -> (mtime_ns, size, rows)


def calibration_rows(data_dir, source: str | None) -> int:
    """The number of non-empty lines (one target's two null searches each) in ``source``'s null calibration file under
    ``data_dir``, 0 when there is none; counted once per change of the file's size or time."""
    path = cal.default_path(Path(data_dir), source or "kbonus_bkg")
    try:
        st = path.stat()
    except OSError:
        return 0
    key = str(path)
    hit = _ROW_COUNTS.get(key)
    if hit is not None and hit[:2] == (st.st_mtime_ns, st.st_size):
        return hit[2]
    try:
        with path.open("rb") as fh:
            n = sum(1 for line in fh if line.strip())
    except OSError:
        return 0
    _ROW_COUNTS[key] = (st.st_mtime_ns, st.st_size, n)
    return n


def data_dir_of(data_path) -> Path:
    """The data folder a dossier's ``<data_dir>/dossiers/<target_id>/dossier_data.json`` lies in."""
    return Path(data_path).resolve().parents[2]


# ---------------------------------------------------------------------------------------------------- plot arrays

def _bins(x: np.ndarray, y: np.ndarray, width: float, lo: float, hi: float) -> list[list]:
    """[centre, mean, standard error, n] of ``y`` in bins of ``width`` from ``lo`` to ``hi``, bins with at least three
    points only (as ``lc_analysis.binned``)."""
    n_bins = max(1, math.ceil((hi - lo) / width))
    keep = (x >= lo) & (x < lo + n_bins * width) & np.isfinite(y)
    idx = np.floor((x[keep] - lo) / width).astype(int)
    yk = y[keep]
    n = np.bincount(idx, minlength=n_bins)
    s1 = np.bincount(idx, weights=yk, minlength=n_bins)
    s2 = np.bincount(idx, weights=yk * yk, minlength=n_bins)
    out = []
    for i in np.flatnonzero(n >= 3):
        mean = s1[i] / n[i]
        sd = math.sqrt(max(0.0, s2[i] / n[i] - mean * mean))
        out.append([lo + (i + 0.5) * width, mean, sd / math.sqrt(n[i]), int(n[i])])
    return out


def _hours_bins(x_days, y, width_days, win) -> list[list]:
    return [[_r(c * 24, 3), _r(m, 1), _r(e, 1), k] for c, m, e, k in _bins(x_days, y, width_days, -win, win)]


def _spectrum(periodogram: PeriodogramSummary | None) -> list[list]:
    """The stored SDE envelope thinned to at most ``SPECTRUM_BINS`` points: the highest bin of each group."""
    if periodogram is None:
        return []
    periods = periodogram.bin_periods()
    sde = np.array([np.nan if v is None else v for v in periodogram.envelope], float)
    group = max(1, math.ceil(sde.size / SPECTRUM_BINS))
    out = []
    for start in range(0, sde.size, group):
        chunk = sde[start:start + group]
        if np.isfinite(chunk).any():
            j = start + int(np.nanargmax(chunk))
            out.append([_r(periods[j], 4), _r(sde[j], 2)])
    return out


def plot_arrays(lc: LightCurve, fnorm: np.ndarray, signal: dict, tests: list[dict], *,
                periodogram: PeriodogramSummary | None = None) -> dict:
    """The arrays sections 01 and 02 draw, in ppm of the whitened flux and hours from mid-transit, thinned as the
    hand-built page was: the full curve in ``SERIES_BIN_DAYS`` bins, the fold with its bins and model, odd and even,
    the phase 0.5 window, every event with its depth and quality and its points within ``GALLERY_HALF_HOURS``, the
    periodogram envelope (up to ``SPECTRUM_BINS``) and a line per quarter."""
    period, t0, dur = float(signal["period"]), float(signal["t0"]), float(signal["duration"])
    time = np.asarray(lc.time, float)
    fnorm = np.asarray(fnorm, float)
    good = np.isfinite(fnorm) & np.isfinite(time)
    t, y, q = time[good], (fnorm[good] - 1.0) * 1e6, np.asarray(lc.quarter)[good]
    # full series
    series = []
    if t.size:
        start = math.floor(t.min())
        idx = np.floor((t - start) / SERIES_BIN_DAYS).astype(int)
        n = np.bincount(idx)
        st, sy = np.bincount(idx, weights=t), np.bincount(idx, weights=y)
        series = [[_r(st[i] / n[i], 3), _r(sy[i] / n[i], 0), int(n[i])] for i in np.flatnonzero(n >= 3)]
    # fold, model, odd/even and the phase 0.5 window
    win = min(FOLD_WINDOW_DURATIONS * dur, 0.5 * period)
    ph = fold_phase(t, period, t0)
    m = np.abs(ph) < win
    stride = max(1, math.ceil(int(m.sum()) / FOLD_POINTS))
    fold_points = [[_r(a * 24, 3), _r(b, 0)] for a, b in zip(ph[m][::stride], y[m][::stride], strict=True)]
    shape = _metrics(tests, "rung2_shape")
    k, b, a_r = (_num(shape, x) for x in ("fit_k", "fit_b", "fit_a_over_r"))
    if shape.get("fit_ok") is True and None not in (k, b, a_r) and a_r > 0:
        model_t = np.linspace(-win, win, MODEL_POINTS)
        flux = uniform_disc_flux(model_t, period, k, b, a_r)
        model = [[_r(a * 24, 3), _r((f - 1.0) * 1e6, 1)] for a, f in zip(model_t, flux, strict=True)]
        model_kind = "fit"
    else:
        depth = -float(signal["depth_ppm"])
        half_h, win_h = dur * 12.0, win * 24.0
        model = [[_r(-win_h, 3), 0.0], [_r(-half_h, 3), 0.0], [_r(-half_h, 3), _r(depth, 1)],
                 [_r(half_h, 3), _r(depth, 1)], [_r(half_h, 3), 0.0], [_r(win_h, 3), 0.0]]
        model_kind = "box"
    cyc = np.round((t - t0) / period).astype(int)
    odd, even = m & (cyc % 2 == 1), m & (cyc % 2 == 0)
    sec_ph = fold_phase(t, period, t0 + 0.5 * period)
    sm = np.abs(sec_ph) < win
    # events: depth per event, coverage and single-event statistic per expected epoch, the gallery points
    ed = event_depths(time, fnorm, np.asarray(lc.quarter), period, t0, dur)
    eq = event_quality(time, fnorm, period, t0, dur)
    quality = {int(e): (c, s) for e, c, s in zip(eq.epochs, eq.coverage, eq.ses, strict=True)}
    events, gallery = [], []
    half_days = GALLERY_HALF_HOURS / 24.0
    for e, quarter, d, err in zip(ed.epochs, ed.quarters, ed.depths, ed.errors, strict=True):
        t_mid = t0 + int(e) * period
        cov, ses = quality.get(int(e), (None, None))
        events.append({"epoch": int(e), "t_mid": _r(t_mid, 3), "quarter": int(quarter), "depth_ppm": _r(d, 0),
                       "err_ppm": _r(err, 0), "coverage": _r(cov, 2), "ses": _r(ses, 2)})
        w = np.abs(t - t_mid) < half_days
        gallery.append({"epoch": int(e), "t_mid": _r(t_mid, 3), "quarter": int(quarter), "depth_ppm": _r(d, 0),
                        "err_ppm": _r(err, 0),
                        "pts": [[_r((a - t_mid) * 24, 2), _r(v, 0)] for a, v in zip(t[w], y[w], strict=True)]})
    quarters = []
    for qq in sorted({int(v) for v in q}):
        mq = q == qq
        fq = fnorm[good][mq]
        quarters.append({"q": qq, "season": int(season_of(np.array([qq]))[0]), "t_start": _r(t[mq].min(), 2),
                         "t_end": _r(t[mq].max(), 2), "n": int(mq.sum()),
                         "rms_ppm": _r(1.4826 * np.median(np.abs(fq - np.median(fq))) * 1e6, 0)})
    lines = {"aliases": [round(period * a, 6) for a in ALIAS_FACTORS], "systematic_periods": list(SYSTEMATIC_PERIODS),
             "peaks": list(periodogram.peaks) if periodogram is not None else []}
    return {"series_binned": series, "series_bin_days": SERIES_BIN_DAYS, "window_h": _r(win * 24, 3),
            "fold_points": fold_points, "fold_bins": _hours_bins(ph[m], y[m], dur / 8, win),
            "model": model, "model_kind": model_kind,
            "odd_bins": _hours_bins(ph[odd], y[odd], dur / 4, win),
            "even_bins": _hours_bins(ph[even], y[even], dur / 4, win),
            "secondary_bins": _hours_bins(sec_ph[sm], y[sm], dur / 4, win),
            "event_gallery": gallery, "gallery_half_hours": GALLERY_HALF_HOURS, "events": events,
            "spectrum": _spectrum(periodogram), "spectrum_lines": lines, "quarters": quarters}


# ---------------------------------------------------------------------------------------------------- pixels

def _pixel_seasons(metrics: dict) -> list[dict]:
    return [s for s in metrics.get("seasons") or [] if isinstance(s, dict)]


@dataclass(frozen=True)
class CentroidEstimate:
    """The season-averaged difference centroid of rung 5's voting seasons, in arcsec. ``offset`` is the
    inverse-variance weighted mean of the seasons' scalar distances from the target (``dist_target_pix``; a distance,
    not a 2-D vector, because each season's frame is rolled against the others). ``sigma`` is the larger of
    ``sigma_stat`` (the error of that weighted mean) and ``sigma_scatter`` (the seasons' RMS scatter about it divided
    by sqrt(n)), since the statistical error alone leaves out the systematics (star positions, WCS, the PRF of a
    bright neighbour) that show up as season-to-season scatter; ``source`` names the one that won."""
    offset: float
    sigma: float
    sigma_stat: float
    sigma_scatter: float
    source: str                     # "statistical" | "scatter"
    n_seasons: int


def season_votes(season: dict) -> bool:
    """Whether a rung 5 season voted: its ``voting`` flag; a row written before rung 5 stored the flag votes when
    its ``nearer`` names a star."""
    if "voting" in season:
        return season["voting"] is True
    return season.get("nearer") in ("target", "neighbour")


def centroid_estimate(rung5_metrics: dict) -> CentroidEstimate | None:
    """The ``CentroidEstimate`` of the seasons that voted (``season_votes``) with a distance and a positive error, at
    ``PIXEL_ARCSEC`` per pixel; None when no season voted, so the FPP falls back to the no-pixel rule."""
    rows = [(d, e) for s in _pixel_seasons(rung5_metrics)
            if season_votes(s) and (d := _num(s, "dist_target_pix")) is not None
            and (e := _num(s, "centroid_sigma_pix")) is not None and e > 0]
    if not rows:
        return None
    w = np.array([1.0 / e ** 2 for _d, e in rows])
    d = np.array([d for d, _e in rows])
    mean = float(np.sum(w * d) / np.sum(w))
    stat = float(1.0 / math.sqrt(np.sum(w)))
    # sample scatter (ddof 1) about the weighted mean, over sqrt(n); a single season has no scatter to judge by
    n = len(rows)
    scatter = float(math.sqrt(np.sum((d - mean) ** 2) / (n - 1)) / math.sqrt(n)) if n > 1 else 0.0
    source = "scatter" if scatter > stat else "statistical"
    return CentroidEstimate(offset=mean * PIXEL_ARCSEC, sigma=max(stat, scatter) * PIXEL_ARCSEC,
                            sigma_stat=stat * PIXEL_ARCSEC, sigma_scatter=scatter * PIXEL_ARCSEC, source=source,
                            n_seasons=n)


def centroid_offset(rung5_metrics: dict) -> tuple[float, float] | None:
    """(offset, error) in arcsec of ``centroid_estimate``, or None without a voting season."""
    est = centroid_estimate(rung5_metrics)
    return None if est is None else (est.offset, est.sigma)


def _grid(a: np.ndarray, valid: np.ndarray, nd: int) -> list[list]:
    return [[_r(x, nd) if ok else None for x, ok in zip(row, vrow, strict=True)]
            for row, vrow in zip(a.tolist(), valid.tolist(), strict=True)]


PIXEL_KEYS = ("diff", "var", "direct", "valid", "pos_target", "pos_neighbour")


def _season_arrays(z, season: int) -> dict:
    """One season's arrays from the npz: ``diff_<s>``, ``var_<s>``, ``direct_<s>`` and ``valid_<s>`` on the season's
    own frame (seasons are union grids of different shapes, so they are never stacked) and ``pos_target_<s>``,
    ``pos_neighbour_<s>`` as season-frame (col, row). A missing key raises KeyError, a mis-shaped array ValueError."""
    a = {k: np.asarray(z[f"{k}_{season}"]) for k in PIXEL_KEYS}
    shape = a["diff"].shape
    if len(shape) != 2 or any(a[k].shape != shape for k in ("var", "direct", "valid")):
        raise ValueError(f"season {season}: the diff, var, direct and valid frames differ in shape or are not 2-D")
    for k in ("pos_target", "pos_neighbour"):
        if a[k].size != 2:
            raise ValueError(f"season {season}: {k} is not a (col, row) pair")
    return a


def pixels_section(store: Store, blob, signal_id: int, rung5_metrics: dict) -> list[dict] | None:
    """Section 04's per-season arrays from rung 5's ``pixels.npz`` artifact (``seasons``, an int array, and per season
    ``s`` the keys of ``_season_arrays``) and its metrics, or None when the signal has no such artifact. The artifact
    is the one the latest rung 5 row names (``metrics["pixels_sha256"]``), never merely the newest file at the URL: a
    step whose row did not land (a reviewer decided meanwhile, or the commit failed) may have stored arrays the rows
    do not describe. Each season
    keeps its own frame shape. Seasons are the Kepler archive SEASON (0 to 3; Q1 and Q5 are season 3) as rung 5 wrote
    them, from the npz ``seasons`` key, and a season's quarters are the ones its rung 5 metrics list; they are never
    worked out again from the quarter numbers (an empty list when the metrics do not name them). The S/N map is
    ``diff / sqrt(var)`` on valid pixels with a positive variance, None elsewhere. An artifact that cannot be read, or lacks a key, raises one of ``SECTION_ERRORS``."""
    sha = rung5_metrics.get("pixels_sha256")
    if not isinstance(sha, str) or not sha:
        return None
    with store.session() as s:
        row = s.scalars(select(ArtifactRow).where(ArtifactRow.sha256 == sha,
                                                  ArtifactRow.url == PIXELS_URL.format(signal_id=signal_id),
                                                  ArtifactRow.deleted_at.is_(None))).first()
        loc = None if row is None else row.local_path
    if loc is None:
        return None
    with np.load(io.BytesIO(blob.get(loc))) as z:
        seasons = [int(v) for v in np.asarray(z["seasons"]).ravel()]
        frames = {season: _season_arrays(z, season) for season in seasons}
    by_season = {int(s["season"]): s for s in _pixel_seasons(rung5_metrics) if s.get("season") is not None}
    out = []
    for season in seasons:
        a = frames[season]
        diff, var = a["diff"].astype(float), a["var"].astype(float)
        direct, valid = a["direct"].astype(float), a["valid"].astype(bool)
        ok = valid & (var > 0) & np.isfinite(diff)
        snr = np.where(ok, diff / np.sqrt(np.where(var > 0, var, 1.0)), np.nan)
        m = by_season.get(season, {})
        quarters = m.get("quarters") or []
        out.append({"season": season, "quarters": [int(x) for x in quarters], "shape": list(diff.shape),
                    "snr": _grid(snr, ok, 2), "direct": _grid(direct, valid, 1), "valid": valid.tolist(),
                    "pos_target": a["pos_target"].astype(float).ravel().tolist(),
                    "pos_neighbour": a["pos_neighbour"].astype(float).ravel().tolist(),
                    "centroid": m.get("centroid_colrow"), "centroid_sigma": m.get("centroid_sigma_pix"),
                    "nearer": m.get("nearer"), "dist_target_pix": m.get("dist_target_pix"),
                    "dist_neighbour_pix": m.get("dist_neighbour_pix"),
                    "depth_target_ppm": m.get("depth_target_ppm"), "depth_neighbour_ppm": m.get("depth_neighbour_ppm")})
    return out


# ---------------------------------------------------------------------------------------------------- star and FPP

def look_numbers(star: stars.StarParams, planet: stars.PlanetParams) -> dict | None:
    """The inputs of section 06 (the illustrated panel), or None when the star could not be characterized."""
    if star.route == "none" or star.teff is None or star.radius is None:
        return None
    teff, radius = star.teff.median, star.radius.median
    a = planet.a_au.median if planet.a_au is not None else None
    angular = math.degrees(2 * math.atan(radius * RSUN_AU / a)) if a else None
    return {"teff_k": teff, "radius_rsun": radius, "mass_msun": star.mass.median if star.mass else None,
            "luminosity_lsun": radius ** 2 * (teff / stars.TSUN) ** 4, "spectral_type": star.spectral_type,
            "star_colour": star_word(teff), "a_au": a,
            "rp_rearth": planet.rp_rearth.median if planet.rp_rearth else None,
            "teq_k": planet.teq_k.median if planet.teq_k else None,
            "insolation_earth": planet.insolation_earth.median if planet.insolation_earth else None,
            "hz_inner_au": planet.hz_inner_au, "hz_outer_au": planet.hz_outer_au,
            "hz_optimistic_inner_au": planet.hz_optimistic_inner_au,
            "hz_optimistic_outer_au": planet.hz_optimistic_outer_au,
            "in_habitable_zone": planet.in_habitable_zone,
            "in_optimistic_habitable_zone": planet.in_optimistic_habitable_zone,
            "angular_size_deg": angular, "angular_size_sun": angular / SUN_ANGULAR_DEG if angular else None}


def _depth_fraction(signal: dict, shape: dict) -> float:
    for v in (_num(shape, "depth_ppm"), _num(signal, "depth_ppm")):
        if v is not None and v > 0:
            return v / 1e6
    return float(signal["depth_ppm"]) / 1e6


def gaia_refused(error: str) -> str | None:
    """The HTTP status of a Gaia error the archive refused (4xx: the query itself is wrong, a retry cannot help), or
    None for an outage (5xx, a timeout, an empty answer)."""
    m = _HTTP_4XX.search(error)
    return m.group(1) if m else None


def _lookup(target: Target, signal: dict, tests: list[dict], *, fetch, store: Store):
    """``stars.lookup`` with each query group caught: a failed query on the star's own row gives route "none", one on
    the neighbour cone gives ``None`` for the neighbours (unknown, not empty); each is logged at warning level and
    returned in the error list as "<group>: <error>" (``GAIA_STAR`` or ``GAIA_CONE``)."""
    shape = _metrics(tests, "rung2_shape")
    depth = _depth_fraction(signal, shape)
    fit_k = _num(shape, "fit_k") if shape.get("fit_ok") is True else None
    errors: list[str] = []
    try:
        gaia = stars.query_gaia(target, fetch=fetch, store=store)
    except ServiceUnavailable as e:
        gaia = None
        errors.append(f"{GAIA_STAR}: {e}")
        log.warning("Gaia query for the star of %s failed: %s", target.target_id, e)
    cone_target = target
    if target.gmag is None and gaia is not None and gaia.gmag is not None:
        cone_target = target.model_copy(update={"gmag": gaia.gmag})
    try:
        neighbours = stars.query_neighbours(cone_target, depth, fetch=fetch, store=store)
    except ServiceUnavailable as e:
        neighbours = None
        errors.append(f"{GAIA_CONE}: {e}")
        log.warning("Gaia neighbour cone for %s failed: %s", target.target_id, e)
    star, planet = stars.derive(gaia, float(signal["period"]), fit_k, depth, np.random.default_rng(STARS_SEED))
    return star, planet, neighbours, gaia, errors


def fpp_inputs(signal: dict, tests: list[dict], star: stars.StarParams, planet: stars.PlanetParams,
               neighbours: list[Neighbour] | None, gmag: float | None) -> FppInputs:
    """The FPP inputs from the signal, rung 2, the star and, when a rung 5 row has at least ``MIN_VOTING_SEASONS``
    voting seasons (the rung's own pass rule; one season decides nothing), the centroid (``centroid_estimate``): each Gaia neighbour's ``centroid_sep_arcsec`` is then its separation less the centroid's
    offset from the target (the nearest the centroid can sit to it, since the cone gives no position angle), floored
    at zero, and the centroid error with the two numbers it was chosen from goes into the inputs record. The
    neighbour list counts as known only when the cone answered (``neighbours`` is not None) and the target has a G;
    otherwise the FPP is not computed."""
    shape = _metrics(tests, "rung2_shape")
    fit_ok = shape.get("fit_ok") is True
    odd, even, parity = _num(shape, "odd_ppm"), _num(shape, "even_ppm"), _num(shape, "parity_sigma")
    odd_even_err = abs(odd - even) / parity if odd is not None and even is not None and parity else None
    centroid = centroid_estimate(_metrics(tests, "rung5_pixels")) if _rung(tests, "rung5_pixels") else None
    if centroid is not None and centroid.n_seasons < MIN_VOTING_SEASONS:
        centroid = None             # one voting season decides nothing for rung 5 either: the no-pixel rule applies
    known = neighbours is not None and gmag is not None
    neighbours = list(neighbours or [])
    if centroid is not None:
        neighbours = [dataclasses.replace(n, centroid_sep_arcsec=max(0.0, n.sep_arcsec - centroid.offset))
                      for n in neighbours]
    if gmag is None:
        neighbours = []         # no target G: no neighbour can be judged bright enough
    return FppInputs(
        period=float(signal["period"]), depth=_depth_fraction(signal, shape),
        gmag=float(gmag) if gmag is not None else float("nan"),
        teff=star.teff.median if star.teff else None, radius=star.radius.median if star.radius else None,
        mass=star.mass.median if star.mass else None,
        rp_rearth=planet.rp_rearth.median if planet.rp_rearth else None,
        fit_k=_num(shape, "fit_k") if fit_ok else None, fit_b=_num(shape, "fit_b") if fit_ok else None,
        fit_a_over_r=_num(shape, "fit_a_over_r") if fit_ok else None,
        secondary_ppm=_num(shape, "secondary_ppm"), secondary_err_ppm=_num(shape, "control_rms_ppm"),
        odd_ppm=odd, even_ppm=even, odd_even_err_ppm=odd_even_err, neighbours=tuple(neighbours),
        centroid_sigma_arcsec=centroid.sigma if centroid is not None else None, neighbours_known=known,
        sigma_c_stat_arcsec=centroid.sigma_stat if centroid is not None else None,
        sigma_c_scatter_arcsec=centroid.sigma_scatter if centroid is not None else None,
        sigma_c_source=centroid.source if centroid is not None else None)


def fpp_from_inputs(inputs: dict) -> Fpp:
    """Recompute the FPP from a dossier's stored ``fpp.inputs``: ``compute(FppInputs(**inputs))`` (``FppInputs``
    rebuilds the neighbour dicts), with a target G stored as null (no G known) read back as NaN."""
    values = dict(inputs)
    if values.get("gmag") is None:
        values["gmag"] = float("nan")
    return compute(FppInputs(**values))


# ---------------------------------------------------------------------------------------------------- caveats

def close_call(tests: list[dict]) -> bool:
    """Whether a season that voted (``voting`` stored and true; a row without the key never counts) has a centroid
    separation within ``CLOSE_SIGMA`` of the target, where the Monte Carlo error can tip its 3 sigma vote."""
    for s in _pixel_seasons(_metrics(tests, "rung5_pixels")):
        sep = _num(s, "separation_sigma")
        if s.get("voting") is True and sep is not None and abs(sep) < CLOSE_SIGMA:
            return True
    return False


def caveats(data: dict) -> list[dict]:
    """The section 07 list, [{"title", "text"}], by the rules of the plan in their order, each sentence built from
    the stored numbers."""
    tests = data.get("tests") or []
    fpp, fap, star = data.get("fpp") or {}, data.get("fap") or {}, data.get("star") or {}
    source = (data.get("target") or {}).get("source") or "this source"
    out: list[dict] = []
    pixels = _rung(tests, "rung5_pixels")
    if pixels is None:
        out.append({"title": "Which star is dimming has not been tested with pixel data",
                    "text": "The pixel check has not run for this signal, so the dip could still come from a "
                            "neighbouring star whose light falls in the same photometry."})
    elif pixels.get("status") == "undetermined":
        reason = pixels.get("reason") or ""
        missing, nxt = explain.reason_in_words(reason)
        out.append({"title": missing, "text": f"The pixel check gave no verdict ({reason or 'no reason stored'}). "
                                              f"What happens next: {nxt}."})
    for name, err in (data.get("section_errors") or {}).items():
        out.append({"title": f"The {name} section could not be built",
                    "text": f"The {name} section could not be built: {err.get('error')}"})
    if close_call(tests):
        out.append({"key": "close_call", "title": "A season sits close to the 3 sigma line", "text": CLOSE_CALL})
    if fpp.get("pixels") is False:
        out.append({"title": "The false-positive estimate has no pixel exclusion",
                    "text": f"Without a difference-image centroid every neighbour within two Kepler pixels "
                            f"({NO_PIXEL_CONFUSION_ARCSEC:g} arcsec) counts in full towards the background binary "
                            f"term, with a fall-off of one pixel ({NO_PIXEL_FALLOFF_ARCSEC:g} arcsec) beyond, so the "
                            "estimate is labelled \"no pixel data yet\"."})
    if fpp.get("neighbours_known") is False:
        out.append({"key": "no_neighbours", "title": "The false-positive probability is not computed",
                    "text": "Without the Gaia neighbour list the background binary term cannot be judged, and leaving "
                            "it out would make the candidate look cleaner than it is, so no number is given. It comes "
                            "back when the neighbour cone answers and the star has a Gaia G magnitude."})
    if not fap.get("calibrated"):
        out.append({"title": "The false-alarm probability is not yet calibrated for this source",
                    "text": f"The false-alarm probability is not yet calibrated: the null calibration for {source} "
                            f"holds {fap.get('n', 0):,} null searches and at least {cal.MIN_NULLS} are needed. The "
                            "pilot calibration is still running."})
    shape = next((t.get("metrics") or {} for t in tests if t.get("rung") == "rung2_shape"), {})
    fit_b = _num(shape, "fit_b")
    if shape.get("fit_ok") is False or (fit_b is not None and fit_b >= 0.9):
        text = ("The transit fit did not converge, so the shape alone cannot tell a planet from a grazing eclipse."
                if shape.get("fit_ok") is False else
                f"The transit fit gives an impact parameter of {fit_b:.2f}, where a grazing eclipse fits as well as a "
                "planet.")
        out.append({"title": "A grazing or diluted eclipsing binary is not fully excluded by photometry alone",
                    "text": text})
    for err in star.get("gaia_errors") or []:
        refused = gaia_refused(err)
        if refused is None:
            out.append({"title": "Gaia could not be reached",
                        "text": f"Gaia could not be reached: {err}. Generate the dossier again when the archive "
                                "answers."})
            continue
        what = ("the neighbours are left out of the false-positive estimate" if err.startswith(GAIA_CONE) else
                "the star block comes from the fallback route")
        out.append({"key": "gaia_refused", "title": "A Gaia query was refused",
                    "text": f"A Gaia query was refused (HTTP {refused}): {what}. The archive rejected the query "
                            f"itself, so asking again gives the same answer ({err})."})
    route = star.get("route")
    if route == "none":
        why = "; ".join(star.get("caveats") or []) or "no Gaia row for this star"
        out.append({"title": "The star cannot be characterized from Gaia photometry",
                    "text": f"{why[:1].upper()}{why[1:]}. Without the star's radius the planet radius, orbit and "
                            "temperature are not given."})
    teff = (star.get("teff") or {}).get("median")
    if route == "mamajek" and teff is not None and teff < 4000:
        out.append({"title": "The radius comes from the colour table because the 2MASS Ks magnitude is missing",
                    "text": f"Below 4,000 K (this star: {teff:,.0f} K) the Mann relations need the absolute Ks "
                            "magnitude; the Mamajek dwarf table at the observed colour stands in, with its scatter "
                            "doubled."})
    note = star.get("reddening_note") or ""
    alt = (star.get("alt_radius_e02") or {}).get("median")
    text = "The radius ratio comes from the light curve; turning it into Earth radii needs the star's radius."
    text += f" {note}" if note else " The star's radius is not known here."
    if alt is not None and not note:
        text += f" At E(BP-RP) = 0.2 the stellar radius would be {alt:.3f} Rsun."
    out.append({"title": "No spectrum: the planet radius depends on the stellar radius from Gaia colour and parallax",
                "text": text})
    stab = next((t.get("metrics") or {} for t in tests if t.get("rung") == "rung3_stability"), None)
    if stab is not None:
        n_seasons = stab.get("n_seasons")
        n_seasons = len(stab.get("season_depths") or {}) if n_seasons is None else int(n_seasons)
        if n_seasons < 2:
            out.append({"title": "The depth is measured in one season only",
                        "text": f"Rung 3 found data in {n_seasons} season{'' if n_seasons == 1 else 's'}, so a depth "
                                "that changes with the detector cannot be tested."})
    out.append({"title": "This is a candidate until independent follow-up confirms it",
                "text": "Follow-up would need ground-based transit photometry at a predicted time, a spectrum of the "
                        "host and high-resolution imaging."})
    return out


# ---------------------------------------------------------------------------------------------------- generation

def _target_dict(store: Store, target_id: str) -> dict:
    with store.session() as s:
        t = s.get(TargetRow, target_id)
        if t is None:
            raise KeyError(f"no target {target_id}")
        return {c.name: getattr(t, c.name) for c in TargetRow.__table__.columns}


def _periodogram(store: Store, blob, target_id: str) -> PeriodogramSummary | None:
    with store.session() as s:
        row = s.scalars(select(ArtifactRow).where(ArtifactRow.url == PERIODOGRAM_URL.format(target_id=target_id),
                                                  ArtifactRow.deleted_at.is_(None))
                        .order_by(ArtifactRow.created.desc())).first()
        loc = None if row is None else row.local_path
    if loc is None:
        return None
    return PeriodogramSummary.from_npz(blob.get(loc))       # unreadable or gone: one of SECTION_ERRORS


def _offline_factory(target_id: str, signal_id: int):
    """The loader's adapter factory for a dossier: a raw file released by retention is not fetched again (spec 5.8,
    offline except Gaia), so asking for an adapter means the light curve is gone."""
    def factory(name: str, store, blob):
        raise LightCurveReleased(f"the raw light curve of {target_id} (signal {signal_id}) is no longer stored and a "
                                 f"dossier does not download it; {RELEASED_REMEDY}")
    return factory


def ladder_versions(store: Store, signal: dict, tests: list[dict]) -> tuple[str | None, dict[str, str | None]]:
    """(the effective ladder version, the version per rung). A test row written by a recheck carries the rechecks
    row's ladder version; any other row is the sweep's, the signal row's ``ladder_version`` or, when that is empty
    (signals vetted before migration 002, and every signal the recheck brought up to date, which records its version
    in ``rechecks`` only), the run's. The effective version is the latest rechecks row's, else the sweep's."""
    with store.session() as s:
        rechecks = {r.id: r.ladder_version for r in
                    s.scalars(select(RecheckRow).where(RecheckRow.signal_id == signal["id"]).order_by(RecheckRow.id))}
        sweep = signal.get("ladder_version")
        if sweep is None and signal.get("run_id") is not None:
            run = s.get(RunRow, signal["run_id"])
            sweep = run.ladder_version if run is not None else None
    per_rung = {t["rung"]: rechecks.get(t.get("recheck_id"), sweep) if t.get("recheck_id") is not None else sweep
                for t in tests}
    effective = rechecks[max(rechecks)] if rechecks else sweep
    return effective, per_rung


def _relative(path: Path, data_dir) -> str:
    """``path`` relative to ``data_dir`` when it lies inside it (as the pack names it), else as it is."""
    try:
        return Path(path).resolve().relative_to(Path(data_dir).resolve()).as_posix()
    except ValueError:
        return str(path)


def _provenance(store: Store, signal: dict, target: dict, calibration: Path, data_dir) -> dict:
    tid = target["target_id"]
    ladder, per_rung = ladder_versions(store, signal, store.latest_tests(signal["id"]))
    with store.session() as s:
        queries = [{"service": q.service, "url": q.url, "params": _params(q.params), "status": q.status,
                    "nbytes": q.nbytes, "sha256": q.sha256, "error": q.error, "utc": _iso(q.utc),
                    "target_id": q.target_id}
                   for q in s.scalars(select(QueryRow).where(QueryRow.target_id == tid).order_by(QueryRow.id))]
        files = [{"url": a.url, "sha256": a.sha256, "kind": a.kind, "nbytes": a.nbytes}
                 for a in s.scalars(select(ArtifactRow).where(ArtifactRow.target_id == tid,
                                                              ArtifactRow.deleted_at.is_(None),
                                                              ArtifactRow.kind != "dossier")
                                    .order_by(ArtifactRow.created))]
    source = target.get("source") or "kbonus_bkg"
    commands = [f"skysift vet --source {source} --target-id {tid}",
                f"skysift calibrate --source {source}",
                f"skysift dossier --target {tid} --signal {signal['id']} --force"]
    return {"search_version": signal.get("detector_version"), "ladder_version": ladder,
            "ladder_versions": per_rung, "git_commit": _git_commit(), "queries": queries, "files": files,
            "calibration_file": _relative(calibration, data_dir) if calibration.is_file() else None,
            "commands": commands}


def _params(text: str):
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return text


def _record_artifact(store: Store, url: str, path: Path, text: str, target_id: str) -> None:
    """The file as a pinned artifact of kind ``dossier``; earlier versions at the same url are marked deleted (the
    file they described has been replaced)."""
    data = text.encode("utf-8")
    sha = hashlib.sha256(data).hexdigest()
    store.add_artifact(sha, len(data), url, str(path), target_id, pinned=True, kind="dossier")
    with store.session() as s:
        for old in s.scalars(select(ArtifactRow).where(ArtifactRow.url == url, ArtifactRow.sha256 != sha,
                                                       ArtifactRow.deleted_at.is_(None))):
            old.deleted_at = utcnow()


def _stored_report(data_path: Path, signal_id: int, target_id: str) -> DossierReport:
    """The report of an up-to-date dossier, from its stored JSON: the FAP as it was written and the FPP recomputed
    from the stored inputs."""
    data = json.loads(data_path.read_text(encoding="utf-8"))
    f = data.get("fap") or {}
    fap = (FalseAlarm(k=f["k"], n=f["n"], fap=f["fap"], calibrated=f["calibrated"],
                      version_matches=f["version_matches"], versions=tuple(f["versions"]),
                      k_by_kind=dict(f.get("k_by_kind") or {}), n_by_kind=dict(f.get("n_by_kind") or {}))
           if {"k", "n", "fap", "calibrated", "version_matches", "versions"} <= set(f) else None)
    return DossierReport(target_id, signal_id, data_path, False, fap, fpp_from_inputs(data["fpp"]["inputs"]), None,
                         gaia_errors=list((data.get("star") or {}).get("gaia_errors") or []))


def _stale_reason(old: dict | None, signal: dict, force: bool, fp: str) -> str:
    if old is None:
        return "no dossier yet"
    if old["signal_id"] != signal["id"]:
        return "another signal is now the target's best"
    if old["dossier_version"] != DOSSIER_VERSION:
        return f"the dossier format changed ({old['dossier_version']} to {DOSSIER_VERSION})"
    if old["tests_fingerprint"] == fp:
        return "forced" if force else "the data file is missing"
    if old["status_at_generation"] != signal["final_status"]:
        return "the signal's status changed"
    return "the test rows, the reviewer decision or the null calibration changed"


def calibration_version_for(signal: dict, thresholds) -> str:
    """The calibration search version this signal's FAP must be read against. Calibration searches with the
    masked pass and the alias resolution off, so its version never equals the sweep's: when the signal was found by
    today's sweep (its ``detector_version`` is today's sweep version) the matching calibration is today's
    calibration version; otherwise the signal came from another search, and only rows written under its own
    ``detector_version`` describe it. Rows under any other version read "calibration is for an older search"."""
    try:
        sweep_now = search_version(get_detector(signal["detector"]), thresholds)
    except KeyError:                    # a detector no longer registered: no translation is possible
        return str(signal["detector_version"])
    if signal["detector_version"] == sweep_now:
        return cal.search_version(cal.calibration_detector(), thresholds)
    return str(signal["detector_version"])


def _section(name: str, errors: dict, build: Callable[[], object]):
    """``build()``, or None with ``errors[name] = {"error": message}`` when it raises one of ``SECTION_ERRORS``."""
    try:
        return build()
    except SECTION_ERRORS as e:
        log.warning("the %s section could not be built", name, exc_info=True)
        errors[name] = {"error": str(e) or type(e).__name__}
        return None


def generate(store: Store, blob, settings, target_id: str, *, signal_id: int | None = None, force: bool = False,
             fetch=net.fetch, log: Callable[[str], None] | None = None) -> DossierReport:
    """Write ``<data_dir>/dossiers/<target_id>/dossier_data.json`` and its ``dossiers`` row (spec 5.4). A no-op
    (``generated=False``, the file untouched) when the stored fingerprint and dossier version match and ``force`` is
    False. Raises NoSignal when the target has no signal (or not ``signal_id``) and LightCurveReleased when the raw
    light curve is no longer stored (it is not downloaded again: offline except Gaia, spec 5.8)."""
    say = log or (lambda _text: None)
    signal = pick_signal(store, target_id, signal_id)
    sid = signal["id"]
    tests = store.latest_tests(sid)
    decisions = store.decisions(sid)
    decision = decisions[0]["decision"] if decisions else None
    source = _target_dict(store, target_id).get("source") or "kbonus_bkg"
    fp = fingerprint(tests, signal["final_status"], decision, calibration_rows(settings.data_dir, source))
    data_path = Path(settings.data_dir) / "dossiers" / target_id / "dossier_data.json"
    old = store.dossier(target_id)
    if (old is not None and not force and old["tests_fingerprint"] == fp and old["signal_id"] == sid
            and old["dossier_version"] == DOSSIER_VERSION and data_path.is_file()):
        say(f"dossier for {target_id} is current (signal {sid}); nothing to do")
        return _stored_report(data_path, sid, target_id)
    stale = _stale_reason(old, signal, force, fp)
    target = _target_dict(store, target_id)
    say(f"signal {sid} ({signal['final_status']}, SDE {signal['sde']:.1f}); loading the light curve")
    lc = LightCurveLoader(store, blob, source_factory=_offline_factory(target_id, sid)).load(target, pin=True)
    fnorm = whitened(lc, signal)
    errors: dict[str, dict] = {}
    periodogram = _section("periodogram", errors, lambda: _periodogram(store, blob, target_id))
    plots = plot_arrays(lc, fnorm, signal, tests, periodogram=periodogram)
    pixels = _section("pixels", errors, lambda: pixels_section(store, blob, sid, _metrics(tests, "rung5_pixels")))
    say("asking Gaia for the star and its neighbours")
    tgt = Target(**{k: target[k] for k in TARGET_FIELDS if target.get(k) is not None}, meta=target.get("meta") or {})
    star, planet, neighbours, gaia, gaia_errors = _lookup(tgt, signal, tests, fetch=fetch, store=store)
    for err in gaia_errors:
        say(f"Gaia query failed: {err}")
    gmag = target.get("gmag") if target.get("gmag") is not None else (gaia.gmag if gaia is not None else None)
    inputs = fpp_inputs(signal, tests, star, planet, neighbours, gmag)
    fpp = compute(inputs)
    calibration = cal.default_path(settings.data_dir, target.get("source") or "kbonus_bkg")
    rows = cal.read_rows(calibration) if calibration.is_file() else []
    version = calibration_version_for(signal, settings.thresholds)
    fap = cal.false_alarm(rows, float(signal["sde"]), version)
    data = _assemble(store, signal, target, tests, decisions, plots, pixels, star, planet, gaia_errors, fap, fpp,
                     inputs, calibration, version, settings.data_dir)
    data["section_errors"] = errors
    data["caveats"] = caveats(data)
    data = _tidy(data)
    text = jsonutil.dumps(data, separators=(",", ":"))
    _write_atomic(data_path, text)
    _record_artifact(store, DOSSIER_URL.format(target_id=target_id), data_path, text, target_id)
    # export_path and the pack link are cleared: an earlier export or pack was made from the earlier JSON
    store.upsert_dossier(target_id=target_id, signal_id=sid, generated=utcnow().replace(tzinfo=None),
                         data_path=str(data_path), search_version=signal["detector_version"],
                         ladder_version=ladder_versions(store, signal, tests)[0], dossier_version=DOSSIER_VERSION,
                         fap=fap.fap, fpp=fpp.reported_value, fap_detail=data["fap"], fpp_detail=data["fpp"],
                         tests_fingerprint=fp, status_at_generation=signal["final_status"], export_path="",
                         pack_path="", pack_built=None, pack_steps={})
    say(f"wrote {data_path}")
    return DossierReport(target_id, sid, data_path, True, fap, fpp, stale, gaia_errors=list(gaia_errors))


def status_label(verdict) -> tuple[str, str | None]:
    """(label, its glossary sentence) from ``explain.STATUS_GLOSSARY``: a reviewer's decision names the label when
    there is one (the glossary's "Approved / Rejected by reviewer / Needs data" entry), otherwise the engine status
    ("Candidate, stage 2", "Undetermined", ...). A status the glossary does not list keeps the verdict's word."""
    if verdict.reviewer:
        for term, text in explain.STATUS_GLOSSARY:
            for name in term.split(" / "):
                if verdict.reviewer == name or verdict.reviewer.startswith(name + " "):
                    return name, text
    engine = f"{verdict.word}, {verdict.stage}" if verdict.stage else verdict.word
    return engine, dict(explain.STATUS_GLOSSARY).get(engine)


def whitened(lc: LightCurve, signal: dict) -> np.ndarray:
    """The flux the sweep searched and vetted for this signal (the dashboard's ``whitened_fnorm``)."""
    return whitened_fnorm(lc, signal)


def _assemble(store, signal, target, tests, decisions, plots, pixels, star, planet, gaia_errors, fap, fpp, inputs,
              calibration, version, data_dir) -> dict:
    test_rows = [{"rung": t["rung"], "status": t["status"], "reason": t["reason"], "metrics": t["metrics"],
                  "created": _iso(t["created"])} for t in tests]
    verdict = explain.verdict(signal, tests, {"status": signal.get("candidate_status")} if signal.get(
        "candidate_status") else None, decisions)
    label, label_text = status_label(verdict)
    star_d = {**dataclasses.asdict(star), "gaia_errors": list(gaia_errors)}
    period, t0, dur = float(signal["period"]), float(signal["t0"]), float(signal["duration"])
    return {
        "dossier_version": DOSSIER_VERSION, "generated": utcnow().isoformat(),
        "sections": [{"number": f"{i:02d}", "title": t} for i, t in enumerate(SECTIONS, 1)],
        "target": {**target, "name": target_name(target)},
        "signal": {k: v for k, v in signal.items() if k not in ("candidate_status", "decision")},
        "status": {"final_status": signal["final_status"], "decision": decisions[0] if decisions else None,
                   "candidate_status": signal.get("candidate_status"), "label": label,
                   "label_text": label_text, "headline": verdict.headline,
                   "reviewer": verdict.reviewer},
        "summary": explain.summary_sentence(signal, target, tests),
        "ephemeris": {"period_days": period, "t0_bkjd": t0, "t0_bjd": t0 + BKJD_OFFSET, "duration_h": dur * 24,
                      "depth_ppm": float(signal["depth_ppm"]), "sde": float(signal["sde"]),
                      "snr": float(signal["snr"]), "n_events": int(signal["n_events"])},
        "plots": plots, "tests": test_rows, "why": [dataclasses.asdict(w) for w in explain.why(tests)],
        "rechecks": store.rechecks(signal["id"]), "children": store.children(signal["id"]),
        "fap": {**dataclasses.asdict(fap), "one_in": fap.one_in(), "words": fap.words(), "sde": float(signal["sde"]),
                "signal_search_version": signal["detector_version"], "calibration_version": version,
                "min_nulls": cal.MIN_NULLS, "sentence_not_covered": FAP_NOT_COVERED},
        "fpp": {**fpp.as_dict(), "inputs": dataclasses.asdict(inputs), "words": fpp.words(), "label": fpp.label(),
                "sentence_not_covered": FPP_NOT_COVERED},
        "pixels": pixels, "star": star_d, "planet": dataclasses.asdict(planet), "look": look_numbers(star, planet),
        "caveats": [], "provenance": _provenance(store, signal, target, calibration, data_dir),
    }


# ---------------------------------------------------------------------------------------------------- public paths

_LOCAL_PATH = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\|/(?!/))")
# a local path inside a sentence: quoted (it may hold spaces), or bare up to the next space or quote
_QUOTED_PATH = re.compile(r"""(['"])((?:[A-Za-z]:[\\/]|\\\\|/(?!/))[^'"\n]*)\1""")
_BARE_PATH = re.compile(r"""(?<![\w/:.\\'"])((?:[A-Za-z]:[\\/]|\\\\)[^\s'"<>|]+|/(?:[^\s'"<>/]+/)+[^\s'"<>/]*)""")


def _looks_local(text: str) -> bool:
    return (len(text) < 1024 and "\n" not in text and "://" not in text and bool(_LOCAL_PATH.match(text))
            and (text[1:2] == ":" or text.startswith("\\\\") or (" " not in text and text.count("/") >= 2)))


def public_paths(obj, data_dir: Path):
    """A copy of ``obj`` with every absolute local path cut to a name relative to ``data_dir`` (or to its file name
    when it lies elsewhere), whether it is a whole string or sits inside a sentence (an error text): a pack or a
    standalone export is meant for someone outside the laptop and must not carry the user's folders."""
    base = str(Path(data_dir).resolve()).replace("\\", "/").rstrip("/").lower()

    def cut(value: str) -> str:
        norm = value.replace("\\", "/")
        if norm.lower().startswith(base + "/"):
            return norm[len(base) + 1:]
        return PureWindowsPath(value).name if "\\" in value or value[1:2] == ":" else Path(value).name

    def in_text(text: str) -> str:
        if "://" in text:
            return text
        text = _QUOTED_PATH.sub(lambda m: m.group(1) + cut(m.group(2)) + m.group(1), text)
        return _BARE_PATH.sub(lambda m: cut(m.group(1)) if m.group(1).count("/") + m.group(1).count("\\") >= 2
                              or m.group(1)[1:2] == ":" else m.group(1), text)

    def fix(value):
        if isinstance(value, dict):
            return {k: fix(v) for k, v in value.items()}
        if isinstance(value, list):
            return [fix(v) for v in value]
        if isinstance(value, str):
            return cut(value) if _looks_local(value) else in_text(value)
        return value
    return fix(copy.deepcopy(obj))


# ---------------------------------------------------------------------------------------------------- export

_FONT_URL = re.compile(r'url\("/static/fonts/([A-Za-z0-9_.-]+\.woff2)"\)')


def _inline_fonts(css: str, fonts: Path) -> str:
    def data_uri(m: re.Match) -> str:
        b64 = base64.b64encode((fonts / m.group(1)).read_bytes()).decode("ascii")
        return f'url("data:font/woff2;base64,{b64}")'
    return _FONT_URL.sub(data_uri, css)


def export_html(data: dict, static_dir: Path = STATIC_DIR, data_dir: Path | None = None) -> str:
    """One standalone file: the dashboard's stylesheet (tokens and components) with the fonts as data URIs,
    ``dossier.css``, the JSON in ``<script id="dossier-data" type="application/json">`` (every ``/`` escaped, so no
    ``</`` and no URL appears in the markup) and ``dossier.js``, which draws the page and its own level toggle. Nothing
    is loaded from anywhere else. With ``data_dir`` every local path in the data is cut first (``public_paths``), as
    the pack does: the file is meant for someone outside the laptop."""
    static_dir = Path(static_dir)
    if data_dir is not None:
        data = public_paths(data, data_dir)
    app_css = _inline_fonts((static_dir / "app.css").read_text(encoding="utf-8"), static_dir / "fonts")
    css = (static_dir / "dossier.css").read_text(encoding="utf-8")
    js = (static_dir / "dossier.js").read_text(encoding="utf-8")
    payload = jsonutil.dumps(data, separators=(",", ":")).replace("/", "\\/").replace("<!--", "\\u003c!--")
    name = html.escape(str((data.get("target") or {}).get("name") or "Dossier"))
    return ("<!doctype html>\n"
            '<html lang="en" data-level="normal" data-mode="simple" data-theme="system">\n<head>\n'
            '<meta charset="utf-8">\n<meta name="viewport" content="width=device-width, initial-scale=1">\n'
            f"<title>{name} dossier</title>\n<style>\n{app_css}\n</style>\n<style>\n{css}\n</style>\n</head>\n"
            '<body class="dossier-standalone">\n<main id="dossier" class="dossier"></main>\n'
            "<noscript><p>This page draws itself with JavaScript; the data is in the dossier-data block.</p>"
            "</noscript>\n"
            f'<script id="dossier-data" type="application/json">{payload}</script>\n'
            f"<script>\n{js}\n</script>\n</body>\n</html>\n")


def write_export(store: Store, report: DossierReport, path: Path, static_dir: Path = STATIC_DIR) -> Path:
    """Render the report's JSON to ``path`` (atomically), record it as a pinned ``dossier`` artifact and on the row."""
    path = Path(path)
    data = json.loads(report.data_path.read_text(encoding="utf-8"))
    text = export_html(data, static_dir, data_dir_of(report.data_path))
    _write_atomic(path, text)
    _record_artifact(store, EXPORT_URL.format(target_id=report.target_id), path, text, report.target_id)
    store.upsert_dossier(target_id=report.target_id, export_path=str(path))
    return path
