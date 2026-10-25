"""Dossier generation (dossiers spec 5.4): one ``dossier_data.json`` per target, built from stored rows and files plus
the four Gaia queries, and the ``dossiers`` row that says which signal, versions and test rows it was built from.
Since version 3 the ephemeris carries errors: the period and T0 of a linear fit of the per-transit times, the shape
from a bootstrap over transits, the odd and even depths and the secondary limit (``ephemeris``, referee pack spec 3).

``generate`` picks the target's best signal (``STATUS_ORDER`` then SDE), compares the fingerprint of its latest test
rows, status and decision with the stored row (a match is a no-op unless forced), reloads the light curve through the
engine's loader and whitens it as the sweep did, thins the plot arrays to the sizes of the hand-built KIC 6146676 page
(``docs/candidates/kic-006146676/prep_data.py``), reads rung 5's ``pixels.npz`` artifact when present, asks Gaia for
the star and its neighbours (``stars``), and computes the two probabilities (``calibrate.false_alarm``,
``fpp.compute``). It never changes a signal's status, and it reaches the network only through the injected ``fetch``.
The TRICERATOPS cross-check stored on the row (``crosscheck``) is carried into ``cross_check`` with ``current``
recomputed; it is never computed here. Since version 4 the ``tess`` block states whether TESS should have seen the
transit (``skysift.tess.statement``, referee pack spec 10), from the inputs ``tools/tess_inputs.py`` stored in
``targets.meta["tess"]``, so the dossier stays offline. Since version 5 (spec 11.3) the transits are timed only above
the single-event threshold, every fold (depth, events, parity, the shape and its bootstrap, rung 3's ``stability``)
uses the linear ephemeris when the timing is ok, and the shape is a quadratic limb-darkened, exposure-integrated fit,
free and with the star's density as a prior, whose radius ratio the planet radius takes.
``export_html`` renders the JSON as one standalone file with its own level toggle and no external URL.

Every sentence here is built from stored numbers by a rule (spec 5.7); the hand-written narrative of the KIC 6146676
page stays in ``docs/candidates`` as the record."""
from __future__ import annotations

import base64
import copy
import dataclasses
import functools
import hashlib
import html
import io
import json
import logging
import math
import os
import re
import subprocess
import warnings
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path, PureWindowsPath

import numpy as np
from sqlalchemy import select

from skysift import calibrate as cal
from skysift import crosscheck as cc
from skysift import ephemeris as eph
from skysift import jsonutil, net, stars, tess
from skysift.calibrate import FalseAlarm
from skysift.detectors.base import get_detector
from skysift.detectors.periodogram import PeriodogramSummary
from skysift.fpp import FPP_MODEL_VERSION, Fpp, FppInputs, Neighbour, compute
from skysift.lc.loader import TARGET_FIELDS, LightCurveLoader
from skysift.lc.ops import apply_mask, event_depths, event_quality, fold_phase, season_of
from skysift.lc.pixels import aperture_share, psf_fluxes
from skysift.lc.tpf import OPTIMAL_APERTURE_BIT, parse_tpf, tpf_listing_url
from skysift.lc.transit_model import compare_fits, exposure_flux, fit_shape, limb_darkening
from skysift.models import LightCurve, Signal, Target
from skysift.net import ServiceUnavailable
from skysift.pack_text import PRIOR_CLAIM_NUMBERS, PRIOR_CLAIM_PUBLIC, RELATIONSHIPS
from skysift.pipeline import revet_flux, search_version
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
from skysift.vetting.base import DataBundle
from skysift.vetting.rung1_detection import SYSTEMATIC_PERIODS
from skysift.vetting.rung3_stability import Rung3Stability
from skysift.vetting.rung5_pixels import PIXEL_RULE_VERSION

log = logging.getLogger(__name__)

# 3: ephemeris, timing, shape, parity and secondary blocks; 4: the TESS block (22 Oct 2026); 5: limb-darkened free and
# prior fits, folds on the linear ephemeris, SES-cut timing (spec 11.3)
# 6: round 3 (spec 12): the extinction block, the four cross-check numbers, the sharing rows
DOSSIER_VERSION = "6"
N_BOOT_DEFAULT = eph.N_BOOT     # the shape bootstrap's resamples; ``generate`` reads it at call time
# best first; an undetermined signal (the pixel stage could not decide) outranks every closed one, so a target's
# tile and its dossier name the same signal when a superseded parent or a rejected sibling has the higher SDE
STATUS_ORDER = ("candidate_stage2", "approved", "candidate_stage1", "undetermined", "pending", "rejected", "superseded")
CLOSE_SIGMA = 3.5               # a voting season nearer the 3 sigma decision than this gets the Monte Carlo caveat
MIN_VOTING_SEASONS = 2          # rung 5 passes on two voting seasons at least; below that the FPP keeps the no-pixel rule
# the Monte Carlo caveat; {floor} takes ``floor_clause`` (empty for a row stored before the floor: CLOSE_CALL)
CLOSE_CALL_TEXT = ("The centroid error comes from 200 Monte Carlo draws{floor}, so a season this close to the 3 sigma "
                   "threshold can move by about 5 percent with the seed.")
CLOSE_CALL = CLOSE_CALL_TEXT.format(floor="")
# without a contrast curve TRICERATOPS lets a bound companion sit unresolved anywhere inside this (Giacalone et al.
# 2021, section 2.4.3): the open false-positive channel the last caveat names (referee pack ruling o)
UNRESOLVED_COMPANION_ARCSEC = 2.2
SERIES_BIN_DAYS = 0.25
GALLERY_HALF_HOURS = 8.0
SPECTRUM_BINS = 1500
FOLD_POINTS = 4000              # the fold scatter, strided; the bins use every point
FOLD_WINDOW_DURATIONS = 4.0     # the fold, odd/even and secondary windows: +-4 durations, as lc_analysis.py
MODEL_POINTS = 400
ALIAS_FACTORS = (0.5, 2.0, 3.0)
STARS_SEED = 20261021           # the seed of stars.lookup, so a dossier reproduces the lookup's draws
SIG_FIGS = 8                    # every float in the JSON outside the plot arrays (which carry their own rounding)
BJD_DECIMALS = 6                # a float under a key ending in "_bjd" (a full BJD_TDB, about 2.45e6 d): 0.09 s
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


def _tidy(obj, key: str | None = None):
    """Strict-JSON-ready copy with every float at ``SIG_FIGS`` significant figures (no float noise in the file),
    except a float under a key ending in ``_bjd``, a full BJD_TDB, which keeps ``BJD_DECIMALS`` decimals: eight
    significant figures of 2,454,969.30033 would leave a tenth of a day."""
    obj = jsonutil.sanitize(obj)
    if isinstance(obj, dict):
        return {k: _tidy(v, k) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_tidy(v, key) for v in obj]
    if isinstance(obj, float):
        return round(obj, BJD_DECIMALS) if key is not None and key.endswith("_bjd") else _sig(obj)
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


DIRTY_PATHS = ("src", "docs/candidates")     # an untracked file here changes the code or the texts a dossier runs


def git_commit_at(root: Path) -> str:
    """The short commit of the checkout at ``root``, with "-dirty" when a tracked file has uncommitted changes
    (``git describe --always --dirty``) or when ``git status`` lists any file, untracked ones included, under
    ``DIRTY_PATHS`` (an untracked package beside a module, or a claims file the commit does not hold, would otherwise
    pass as the stated commit); "unknown" outside a checkout."""
    try:
        out = subprocess.run(["git", "describe", "--always", "--dirty", "--abbrev=7", "--match=NO-TAGS"],
                             capture_output=True, text=True, timeout=10, cwd=root, check=False).stdout.strip()
        if not out:
            return "unknown"
        listed = subprocess.run(["git", "status", "--porcelain", "--untracked-files=all", "--", *DIRTY_PATHS],
                                capture_output=True, text=True, timeout=10, cwd=root, check=False)
        if listed.returncode != 0:
            return "unknown"
        if listed.stdout.strip() and not out.endswith("-dirty"):
            out += "-dirty"
        return out
    except (OSError, subprocess.SubprocessError):
        return "unknown"


@functools.lru_cache(maxsize=1)
def git_commit() -> str:
    """``git_commit_at`` for the checkout this module sits in. Cached: one call per process."""
    return git_commit_at(Path(__file__).resolve().parents[2])


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
    stage 1, a stage-2 signal stays stage 2 when approved, and an undetermined signal outranks a superseded, rejected
    or pending one whatever their SDE), then SDE, then id. ``signal`` holds ``id``,
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


def fingerprint(latest_tests: list[dict], final_status: str, decision: str | None, calibration_rows: int = 0,
                sweep_size: int | None = None, extinction_ebv: float | str | None = None,
                column_ebv: float | str | None = None) -> str:
    """sha256 of the sorted test row ids, the final status, the latest decision, the number of rows in the source's
    null calibration (``calibration_rows``), the sweep size the false-alarm sentence quotes (``sweep_size_of``; 0 for
    none), the FPP model version and the stored extinction (spec 12.3: the adopted E(B-V), ``extinction_ebv``, and
    the full-column upper bound, ``column_ebv``, each None without one): a dossier is stale when any of them changed
    since it was written, so one written while the calibration was short goes stale as the null searches arrive, one
    written before the sweep grew goes stale with it, one written under an older false-positive model goes stale with
    the model, and one written before a reddening was stored goes stale with it."""
    ids = sorted(int(t["id"]) for t in latest_tests)
    text = json.dumps({"tests": ids, "status": final_status, "decision": decision,
                       "calibration_rows": int(calibration_rows), "sweep_size": int(sweep_size or 0),
                       "fpp_model": FPP_MODEL_VERSION, "extinction_ebv": extinction_ebv, "column_ebv": column_ebv},
                      sort_keys=True)
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
                periodogram: PeriodogramSummary | None = None, fold: dict | None = None,
                shape: dict | None = None) -> dict:
    """The arrays sections 01 and 02 draw, in ppm of the whitened flux and hours from mid-transit, thinned as the
    hand-built page was: the full curve in ``SERIES_BIN_DAYS`` bins, the fold with its bins and model, odd and even,
    the phase 0.5 window, every event with its depth and quality and its points within ``GALLERY_HALF_HOURS``, the
    periodogram envelope (up to ``SPECTRUM_BINS``) and a line per quarter. Everything folds on ``fold`` ({"period",
    "t0"}, ``ephemeris_blocks``: the linear ephemeris when the transits were timed), on the search ephemeris when it
    is None. The model line is ``exposure_flux`` of ``shape`` ({"k", "b", "a_over_r", "u1", "u2", "exposure_days"},
    the quoted shape fit), the search box without it."""
    period, t0, dur = float(signal["period"]), float(signal["t0"]), float(signal["duration"])
    if fold is not None:
        period, t0 = float(fold["period"]), float(fold["t0"])
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
    k, b, a_r = (_num(shape or {}, x) for x in ("k", "b", "a_over_r"))
    if shape is not None and None not in (k, b, a_r) and a_r > 0:
        model_t = np.linspace(-win, win, MODEL_POINTS)
        flux = exposure_flux(model_t, period, k, b, a_r, float(shape["u1"]), float(shape["u2"]),
                             float(shape["exposure_days"]))
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
            "bin_minutes": _r(dur / 8 * 1440.0, 2),
            "model": model, "model_kind": model_kind,
            "odd_bins": _hours_bins(ph[odd], y[odd], dur / 4, win),
            "even_bins": _hours_bins(ph[even], y[even], dur / 4, win),
            "secondary_bins": _hours_bins(sec_ph[sm], y[sm], dur / 4, win),
            "event_gallery": gallery, "gallery_half_hours": GALLERY_HALF_HOURS, "events": events,
            "spectrum": _spectrum(periodogram), "spectrum_lines": lines, "quarters": quarters}


# ---------------------------------------------------------------------------------------------------- errors

def _fit_point(shape: dict) -> dict | None:
    """Rung 2's uniform-disc fit, or None unless ``fit_ok`` is true and all five values (k, b, a/R*, T14 and the model
    depth) are finite. Since version 5 it decides only whether a shape is fitted at all (``ephemeris_blocks``); the
    bootstrap's point is the quoted limb-darkened fit."""
    if shape.get("fit_ok") is not True:
        return None
    point = {"k": _num(shape, "fit_k"), "b": _num(shape, "fit_b"), "a_over_r": _num(shape, "fit_a_over_r"),
             "t14_h": _num(shape, "fit_t14_h"), "fit_depth_ppm": _num(shape, "fit_depth_ppm")}
    return None if None in point.values() else point


def _minus_plus(block: dict | None) -> dict | None:
    if not block or block.get("minus") is None or block.get("plus") is None:
        return None
    return {"minus": block["minus"], "plus": block["plus"]}


def _finite_or_none(x) -> float | None:
    return float(x) if x is not None and math.isfinite(float(x)) else None


def _timing(time, fnorm, signal: dict, shape: dict) -> dict:
    """The ``timing`` block: per-transit times against the rung 2 template (the search box when the fit is not ok,
    or when its geometry does not dip, which ``template_from`` refuses), the transits whose single-event S/N on the
    search ephemeris (``event_quality``) is below ``eph.SES_MIN`` set aside (``eph.drop_low_ses``, spec 11.3), the
    linear ephemeris of the rest, and the template used. Each timing row carries ``ses``, the statistic it was cut on
    (None for an epoch ``event_quality`` has none for)."""
    period, t0, dur = float(signal["period"]), float(signal["t0"]), float(signal["duration"])
    depth = float(signal["depth_ppm"])
    note = None
    try:
        template = eph.template_from(shape, depth, dur)
    except ValueError as e:
        template = eph.Template("box", depth * 1e-6, dur)
        note = f"the rung 2 fit does not dip ({e}), so the transits are timed with the search box"
        log.warning("signal %s: %s", signal.get("id"), note)
    quality = event_quality(time, fnorm, period, t0, dur)
    ses = {int(e): float(s) for e, s in zip(quality.epochs, quality.ses, strict=True)}
    times = eph.drop_low_ses(eph.transit_times(time, fnorm, period, t0, dur, template), ses)
    lin = eph.linear_fit(times, period, t0).as_dict()
    # each row keeps the statistic it was cut on, so events.csv prints that one (fix round 1 of Task 10, M-4)
    lin["times"] = [{**t, "ses": _finite_or_none(ses.get(int(t["epoch"])))} for t in lin["times"]]
    return {**lin, "template": template.kind, "template_reason": note}


def shape_model(star: stars.StarParams | None, lc: LightCurve) -> dict:
    """What the shape fit needs beyond the fold: the quadratic limb darkening at the star's Teff (``limb_darkening``,
    its 5,750 K row without one), the exposure (the median time step of ``lc.time``) and the Gaussian density prior
    (the star's density median and the mean of its two half-widths), None when the star has no density."""
    teff = star.teff.median if star is not None and star.teff is not None else None
    ld = limb_darkening(teff or None)
    t = np.sort(np.asarray(lc.time, float)[np.isfinite(np.asarray(lc.time, float))])
    steps = np.diff(t)
    exposure = float(np.median(steps)) if steps.size else eph.CADENCE_MIN / 1440.0
    dens = star.density_gcc if star is not None else None
    density = None
    if dens is not None and None not in (dens.median, dens.minus, dens.plus):
        mean, sigma = float(dens.median), 0.5 * (float(dens.minus) + float(dens.plus))
        if math.isfinite(mean) and math.isfinite(sigma) and mean > 0 and sigma > 0:
            density = (mean, sigma)
    return {"limb_darkening": ld, "u1": ld["u1"], "u2": ld["u2"], "exposure_days": exposure, "density": density}


def _ephemeris_extra(signal: dict, depth: dict, timing: dict, shape: dict, fitted_t14_h: float | None,
                     fold: dict) -> dict:
    """The ephemeris keys of version 3, each value beside its own error: the period and T0 of the linear fit when it
    is ok (else the search values with no error), the fitted T14 when the fit was bootstrapped (the resample median,
    or without resamples the quoted fit's full-data T14; else the bootstrap box width when the shape block is ok,
    else the search box), and since version 5 the depth on the fold (``eph.fold_depth``) and the ephemeris that
    folded (``fold_ephemeris``)."""
    fitted = fitted_t14_h is not None
    ok = timing.get("ok") is True
    out = {"period_days": float(signal["period"]), "t0_bkjd": float(signal["t0"]),
           "t0_bjd": float(signal["t0"]) + BKJD_OFFSET, "period_source": "search"}
    if ok:
        out = {"period_days": timing["period"], "t0_bkjd": timing["t0"], "t0_bjd": timing["t0_bjd"],
               "period_source": f"linear fit of {timing['n_used']} transit times"}
    pick = (lambda key: timing.get(key) if ok else None)
    out.update({"period_err_days": pick("period_err"), "t0_bkjd_err": pick("t0_err"), "t0_bjd_err": pick("t0_err"),
                "t_ref_bkjd": pick("t_ref"), "t_ref_bjd": pick("t_ref_bjd"), "t_ref_err_days": pick("t_ref_err"),
                "epoch_ref": pick("epoch_ref")})
    if fitted:
        duration = (shape.get("t14_h") or {}).get("median")       # the full-data T14 without resamples
        out.update(duration_h=fitted_t14_h if duration is None else duration,
                   duration_h_err=_minus_plus(shape.get("t14_h")), duration_source="fitted T14")
    elif shape.get("ok") and (shape.get("box_duration_h") or {}).get("median") is not None:
        out.update(duration_h=shape["box_duration_h"]["median"], duration_h_err=_minus_plus(shape["box_duration_h"]),
                   duration_source="bootstrap box width")
    else:
        out.update(duration_h=float(signal["duration"]) * 24, duration_h_err=None, duration_source="search box")
    if depth.get("depth_ppm") is not None:
        out.update(depth_ppm=depth["depth_ppm"], depth_ppm_err=depth["err_ppm"], depth_source=depth["source"])
    else:
        out.update(depth_ppm=float(signal["depth_ppm"]), depth_ppm_err=None, depth_source="search box")
    out.update(ephemeris_method=timing.get("method"), shape_method=shape.get("method"),
               fold_ephemeris=fold["source"])
    return out


FOLD_SEARCH = "search"          # fold["source"] when the transits could not be timed


def _fold(signal: dict, timing: dict) -> dict:
    """The ephemeris every fold uses (spec 11.3): the linear fit of the per-transit times when the timing block is
    ok, the search period and epoch otherwise (source ``FOLD_SEARCH``)."""
    if timing.get("ok") is True:
        return {"period": float(timing["period"]), "t0": float(timing["t0"]),
                "source": f"linear fit of {timing['n_used']} transit times"}
    return {"period": float(signal["period"]), "t0": float(signal["t0"]), "source": FOLD_SEARCH}


def _point_of(fit) -> dict:
    """A shape fit as the bootstrap's point: {"k", "b", "a_over_r", "t14_h", "fit_depth_ppm"}."""
    return {"k": fit.k, "b": fit.b, "a_over_r": fit.a_over_r, "t14_h": fit.t14_days * 24.0,
            "fit_depth_ppm": fit.depth_ppm}


def _stability(lc: LightCurve, fnorm, signal: dict, fold: dict, *, fnorm_alt, thresholds, target) -> dict | None:
    """Rung 3 again on the fold (spec 11.3: the depth stability on the linear-fit ephemeris when there is one),
    {"status", "reason", "ephemeris", "metrics"}; None without thresholds."""
    if thresholds is None:
        return None
    fields = {k: signal[k] for k in Signal.model_fields if signal.get(k) is not None}
    sig = Signal(**{**fields, "period": fold["period"], "t0": fold["t0"]})
    res = Rung3Stability().run(sig, DataBundle(lc, fnorm, target, thresholds, fnorm_alt=fnorm_alt))
    return {"status": str(res.status.value), "reason": res.reason, "ephemeris": fold["source"],
            "metrics": jsonutil.sanitize(res.metrics)}


def aperture_block(psf: dict, sap: dict, crowdsap: float | None, ephemeris: str | None) -> dict | None:
    """Rung 3's aperture comparison on the fold (spec 11.5): the SAP depth (the secondary aperture, ``fnorm_alt``)
    over the PSF depth (``fnorm``), each from ``eph.fold_depth`` on the same ephemeris, with the error of the ratio
    (the two fractional errors in quadrature), against the KBonus CROWDSAP, the ratio expected if the aperture lost
    only the light CROWDSAP gives to other stars; ``shortfall_sigma`` is (ratio - CROWDSAP) over the ratio's error
    (None without CROWDSAP). None without both depths and errors, or with a depth that is not positive."""
    pd, pe, sd, se = (_num(x or {}, k) for x, k in ((psf, "depth_ppm"), (psf, "err_ppm"), (sap, "depth_ppm"),
                                                     (sap, "err_ppm")))
    if None in (pd, pe, sd, se) or not (pd > 0 and sd > 0):
        return None
    ratio = sd / pd
    err = ratio * math.hypot(se / sd, pe / pd)
    crowd = _num({"x": crowdsap}, "x")
    shortfall = (ratio - crowd) / err if crowd is not None and err > 0 else None
    return {"psf_depth_ppm": pd, "psf_err_ppm": pe, "sap_depth_ppm": sd, "sap_err_ppm": se, "ratio": ratio,
            "ratio_err": err, "crowdsap": crowd, "shortfall_sigma": shortfall, "ephemeris": ephemeris}


def ephemeris_blocks(lc: LightCurve, fnorm, signal: dict, tests: list[dict], *, n_boot: int = N_BOOT_DEFAULT,
                     star: stars.StarParams | None = None, fnorm_alt=None, thresholds=None, target=None) -> dict:
    """The error blocks (referee pack spec 3 and, since version 5, 11.3), in this order: ``timing`` (``_timing``, the
    SES cut included); ``fold``, the ephemeris every fold below uses (``_fold``: the linear fit when the timing is ok,
    the search otherwise); on that fold the depth (``eph.fold_depth``), ``parity`` (the per-event odd and even depths
    with the stacked fold of each beside them) and ``secondary`` (rung 2's phase 0.5 limit, None without it); the
    shape fits, free and, when the star has a density (``shape_model``), with the density prior, compared by
    ``compare_fits``; ``shape``, the bootstrap of the quoted fit over every transit the full-data fits see, not only
    the timed ones (the SES cut is the timing's alone; round-2 final review, C-1) (of the box
    without a rung 2 fit: ``_fit_point`` still decides whether a shape is fitted), with the two fits, the comparison,
    the limb darkening and the exposure; ``stability``, rung 3 on the fold (``_stability``); ``aperture``, the SAP
    depth over the PSF depth on the fold against CROWDSAP (``aperture_block``, None without ``fnorm_alt``);
    ``depth_trend``, the per-event depths against time (``eph.depth_trend``); and ``ephemeris_extra``, the ephemeris
    keys they give (``_ephemeris_extra``)."""
    dur = float(signal["duration"])
    time, fnorm = np.asarray(lc.time, float), np.asarray(fnorm, float)
    quarter = np.asarray(lc.quarter)
    metrics = _metrics(tests, "rung2_shape")
    timing = _timing(time, fnorm, signal, metrics)
    fold = _fold(signal, timing)
    period, t0 = fold["period"], fold["t0"]
    depth = eph.fold_depth(time, fnorm, quarter, period, t0, dur)
    events = event_depths(time, fnorm, quarter, period, t0, dur)
    parity = eph.parity_depths(events, time, fnorm, period, t0, dur).as_dict()
    model = shape_model(star, lc)
    # a geometry template_from refused (it does not dip) is not fitted either: timing and shape use one model
    rung2 = _fit_point(metrics) if timing["template"] == "fit" else None
    fits, comparison, point, boot_model, quoted = {"free": None, "prior": None}, None, None, None, None
    free = prior = None
    if rung2 is not None:
        kw = {"u1": model["u1"], "u2": model["u2"], "exposure_days": model["exposure_days"]}
        free = fit_shape(time, fnorm, period, t0, dur, **kw)
        prior = fit_shape(time, fnorm, period, t0, dur, **kw, density=model["density"]) if model["density"] else None
        comparison = compare_fits(free, prior)
        fits = {"free": free.as_dict(), "prior": None if prior is None else prior.as_dict()}
        quoted = prior if comparison["quoted"] == "prior" else free
        if quoted.ok:
            point = _point_of(quoted)
            boot_model = {"u1": model["u1"], "u2": model["u2"], "exposure_days": model["exposure_days"],
                          "density": model["density"], "free": free, "prior": prior, "quoted": comparison["quoted"]}
        else:
            log.warning("signal %s: the shape fit failed (%s); the shape keeps the box", signal.get("id"),
                        quoted.reason)
    shape = eph.bootstrap_shape(time, fnorm, period, t0, dur, point, n_boot=n_boot, model=boot_model).as_dict()
    shape.update(fits=fits, comparison=comparison, limb_darkening=model["limb_darkening"],
                 exposure_min=model["exposure_days"] * 1440.0)
    secondary = eph.secondary_limit(metrics, degenerate=shape.get("degenerate") is True)
    # the quoted fit gives all five values (spec 11.3 as amended in fix round 1), so the duration is its T14
    fitted_t14 = point["t14_h"] if point is not None else None
    aperture = None
    if fnorm_alt is not None:
        crowdsap = (getattr(target, "meta", None) or {}).get("crowdsap")
        aperture = aperture_block(depth, eph.fold_depth(time, np.asarray(fnorm_alt, float), quarter, period, t0, dur),
                                  crowdsap, fold["source"])
    trend = eph.depth_trend(events.epochs, events.depths, events.errors, period)
    return {"ephemeris_extra": _ephemeris_extra(signal, depth, timing, shape, fitted_t14, fold), "timing": timing,
            "aperture": aperture, "depth_trend": trend,
            "shape": shape, "parity": parity, "secondary": None if secondary is None else secondary.as_dict(),
            "stability": _stability(lc, fnorm, signal, fold, fnorm_alt=fnorm_alt, thresholds=thresholds,
                                    target=target),
            "fold": fold}


def quoted_fit(shape: dict | None) -> dict | None:
    """The stored shape fit the dossier quotes (``shape.comparison.quoted`` of ``shape.fits``) when it is ok, else
    None."""
    shape = shape if isinstance(shape, dict) else {}
    fits, cmp = shape.get("fits") or {}, shape.get("comparison") or {}
    fit = fits.get(cmp.get("quoted") or "free")
    return fit if isinstance(fit, dict) and fit.get("ok") is True else None


def with_k_error(planet: dict, shape: dict | None) -> dict:
    """``planet`` with the bootstrap error of the radius ratio folded into ``rp_rearth``: each side becomes the
    quadrature of its stellar term and ``rp * k_err / k``, and the source says so. Unchanged when the shape block
    has no finite k error (no fit, or not ok) or there is no radius."""
    rp, k = planet.get("rp_rearth"), (shape or {}).get("k") or {}
    kv, km, kp = (k.get(x) for x in ("median", "minus", "plus"))
    if not rp or rp.get("median") is None or None in (kv, km, kp) or not kv > 0:
        return planet
    r = float(rp["median"])
    folded = {**rp, "minus": round(math.hypot(rp["minus"], r * km / kv), 2),
              "plus": round(math.hypot(rp["plus"], r * kp / kv), 2),
              "source": f"{rp['source']}; radius-ratio error from the bootstrap"}
    return {**planet, "rp_rearth": folded}


def _star_with_k_errors(star_d: dict, shape: dict | None) -> dict:
    """The star block with the bootstrap error of the radius ratio folded into the unreddened and the full-column
    planet radii too (``with_k_error``), so every planet radius the reddening texts set side by side carries it."""
    for holder in (star_d.get("unreddened"), (star_d.get("extinction") or {}).get("column")):
        if isinstance(holder, dict) and isinstance(holder.get("rp_rearth"), dict):
            holder["rp_rearth"] = with_k_error({"rp_rearth": holder["rp_rearth"]}, shape)["rp_rearth"]
    return star_d


# ---------------------------------------------------------------------------------------------------- pixels

def _pixel_seasons(metrics: dict) -> list[dict]:
    return [s for s in metrics.get("seasons") or [] if isinstance(s, dict)]


@dataclass(frozen=True)
class CentroidEstimate:
    """The season-averaged difference centroid of rung 5's voting seasons against the target's catalogue position (the
    Gaia position projected through each stamp's WCS), in arcsec. ``offset`` is the length of the inverse-variance
    weighted vector mean of the seasons' (col, row) offsets, each taken along and across the season's own
    neighbour-to-target axis so that any turn of the stamp between seasons drops out (``_axis_offsets``); a row
    stored before the positions falls back to the weighted mean of the scalar distances (``dist_target_pix``).
    ``sigma`` is the larger of ``sigma_stat`` (the per-axis error of that weighted mean) and ``sigma_scatter`` (the
    seasons' per-axis RMS scatter about it divided by sqrt(n)), since the statistical error alone leaves out the
    systematics (star positions, WCS, the PRF of a bright neighbour) that show up as season-to-season scatter;
    ``source`` names the one that won."""
    offset: float
    sigma: float
    sigma_stat: float
    sigma_scatter: float
    source: str                     # "statistical" | "scatter"
    n_seasons: int


def crowdsap_provenance(meta: dict | None, lc_meta: dict | None, quarters=None) -> dict | None:
    """The star block's ``crowdsap`` (Task 15 addendum 2): the KBonus crowding metric C of this extraction, the
    catalogue column it is read from and the catalogue, and the CROWDSAP header keyword of each usable quarter of the
    light-curve file (``lc_meta["per_quarter"]``, the quarters in ``quarters`` when given) with their minimum and
    maximum. {"value", "column", "catalogue", "per_quarter": {str(quarter): C_q}, "per_quarter_min",
    "per_quarter_max"}; None without a finite C."""
    from skysift.sources.kbonus_bkg import CATALOGUE, CROWDSAP_COLUMN
    value = _num(meta or {}, "crowdsap")
    if value is None:
        return None
    use = None if quarters is None else {int(q) for q in quarters}
    per = {}
    for q, kw in sorted(((lc_meta or {}).get("per_quarter") or {}).items(), key=lambda kv: int(kv[0])):
        c = _num(kw or {}, "crowdsap")
        if c is not None and (use is None or int(q) in use):
            per[str(int(q))] = c
    return {"value": value, "column": CROWDSAP_COLUMN, "catalogue": CATALOGUE, "per_quarter": per,
            "per_quarter_min": min(per.values()) if per else None,
            "per_quarter_max": max(per.values()) if per else None}


def season_votes(season: dict) -> bool:
    """Whether a rung 5 season voted: its ``voting`` flag; a row written before rung 5 stored the flag votes when
    its ``nearer`` names a star."""
    if "voting" in season:
        return season["voting"] is True
    return season.get("nearer") in ("target", "neighbour")


def _pair(s: dict, key: str) -> np.ndarray | None:
    """A stored (col, row) pair as an array, None when it is missing or not finite."""
    v = s.get(key)
    if not isinstance(v, (list, tuple)) or len(v) != 2:
        return None
    a = [_num({"x": x}, "x") for x in v]
    return None if None in a else np.array(a, float)


def _axis_offsets(s: dict, reference: np.ndarray) -> np.ndarray | None:
    """The season's difference centroid less ``reference`` (a season-frame (col, row)), as (along, across) the unit
    vector from the neighbour to the target in that frame; None without the centroid or the two positions. The
    Kepler stamps keep their orientation on the sky from season to season (the stored WCS of the live stars shows
    it), and the star axis takes out any turn between seasons, so the components of different seasons add."""
    c, pt, pn = _pair(s, "centroid_colrow"), _pair(s, "pos_target"), _pair(s, "pos_neighbour")
    if c is None or pt is None or pn is None:
        return None
    axis = pt - pn
    n = float(np.hypot(*axis))
    if not n > 0:
        return None
    u = axis / n
    d = c - reference
    return np.array([float(d @ u), float(d @ np.array([-u[1], u[0]]))])


def _vector_mean(rows: list[tuple[np.ndarray, float]]) -> tuple[np.ndarray, float, float]:
    """(weighted mean vector, its per-axis error, the per-axis RMS scatter about it over sqrt(n)) of (vector, error)
    rows, weights 1 / error squared; a single row has no scatter."""
    w = np.array([1.0 / e ** 2 for _v, e in rows])
    v = np.array([v for v, _e in rows])
    mean = (w[:, None] * v).sum(axis=0) / w.sum()
    n, dims = v.shape
    scatter = float(math.sqrt(np.sum((v - mean) ** 2) / (dims * (n - 1))) / math.sqrt(n)) if n > 1 else 0.0
    return mean, float(1.0 / math.sqrt(w.sum())), scatter


def _voting_rows(rung5_metrics: dict) -> list[dict]:
    """The seasons that voted (``season_votes``) with a positive centroid error."""
    return [s for s in _pixel_seasons(rung5_metrics) if season_votes(s)
            and (e := _num(s, "centroid_sigma_pix")) is not None and e > 0]


def centroid_estimate(rung5_metrics: dict) -> CentroidEstimate | None:
    """The ``CentroidEstimate`` of the seasons that voted with a positive error, at ``PIXEL_ARCSEC`` per pixel; None
    when no season voted, so the FPP falls back to the no-pixel rule."""
    seasons = _voting_rows(rung5_metrics)
    vectors = [(_axis_offsets(s, p), _num(s, "centroid_sigma_pix")) for s in seasons
               if (p := _pair(s, "pos_target")) is not None]
    if seasons and len(vectors) == len(seasons) and all(v is not None for v, _e in vectors):
        mean, stat, scatter = _vector_mean(vectors)
        offset, n = float(np.hypot(*mean)), len(vectors)
    else:                           # a row stored before the positions: the weighted mean of the distances
        rows = [(np.array([d]), _num(s, "centroid_sigma_pix")) for s in seasons
                if (d := _num(s, "dist_target_pix")) is not None]
        if not rows:
            return None
        mean, stat, scatter = _vector_mean(rows)
        offset, n = float(mean[0]), len(rows)
    source = "scatter" if scatter > stat else "statistical"
    return CentroidEstimate(offset=offset * PIXEL_ARCSEC, sigma=max(stat, scatter) * PIXEL_ARCSEC,
                            sigma_stat=stat * PIXEL_ARCSEC, sigma_scatter=scatter * PIXEL_ARCSEC, source=source,
                            n_seasons=n)


OFFSET_LEVEL_ARCSEC = 0.2       # Bryson et al. 2013: an offset counts when above 3 sigma and above 0.2 arcsec
OFFSET_SIGMA = 3.0
OFFSET_RULE = "Bryson et al. 2013: an offset counts when above 3 sigma and above 0.2 arcsec"
SELF_REFERENCES = {"direct": "the star's own direct-image centroid",
                   "fit": "the catalogue position plus the two-source fit's common shift"}


def _self_reference(s: dict) -> tuple[np.ndarray, str] | None:
    """The target's own position in a season frame: the stored direct-image centroid (``direct_centroid_colrow``,
    stored by rung 5 from 22 October 2026 on), else the catalogue position plus the two-source fit's common shift
    (``shift_pix``, where the fit puts the star on the direct image); None without either."""
    direct = _pair(s, "direct_centroid_colrow")
    if direct is not None:
        return direct, "direct"
    pt, shift = _pair(s, "pos_target"), _pair(s, "shift_pix")
    return (pt + shift, "fit") if pt is not None and shift is not None else None


PULL_PIX = 0.3                  # spec 11.5: a direct centroid this far from the Gaia position is pulled
PSF_PHOTOMETRY = "KBonus PSF photometry, decontaminated to first order, with no crowding correction applied"
EXCLUSION_FLOOR_ARCSEC = 0.5
EXCLUSION_RULE = "max(3 sigma, 0.5 arcsec) about the Gaia-referenced difference centroid (spec 11.5)"


def exclusion_radius(block: dict | None) -> float | None:
    """The radius about the Gaia-referenced difference centroid inside which a background source is not excluded
    (spec 11.5): three times ``catalogue_referenced_err_arcsec``, at least ``EXCLUSION_FLOOR_ARCSEC``. None without
    the block or the error."""
    err = _num(block, "catalogue_referenced_err_arcsec") if isinstance(block, dict) else None
    return None if err is None else max(3.0 * err, EXCLUSION_FLOOR_ARCSEC)


def centroid_offset(rung5_metrics: dict) -> dict | None:
    """The ``centroid_offset`` block of section 04 (referee pack spec 5, ruling e), or None without a voting season:
    ``self_referenced_arcsec``, the length of the weighted vector mean over the voting seasons of the difference
    centroid less the star's own position (``_self_reference``), with ``self_referenced_err_arcsec`` (the seasons'
    errors combined for a mean, per axis) and ``sigma`` (the one over the other); ``catalogue_referenced_arcsec`` and
    its error, the vector mean against the projected Gaia position (``centroid_estimate``); ``level_arcsec`` and
    ``sigma_level``, the rule's two thresholds, ``counts`` (both exceeded; None unless every season's reference is
    the star's own direct-image centroid, since the fit's common shift is not Bryson's estimator), ``rule``,
    ``reference`` (what the star's own position was) and ``n_seasons``. Both errors are the larger of the seasons'
    combined error and their scatter. The self-referenced keys are None when a voting season has no position.
    ``exclusion_radius_arcsec`` (``exclusion_radius``) and ``exclusion_rule`` say how far about the Gaia-referenced
    centroid a background source is not excluded. ``direct_pull_pix`` is the largest distance over the voting seasons
    of the star's direct-image centroid from its projected Gaia position (None without either) and ``pulled`` whether
    it passes ``PULL_PIX``, where the neighbour pulls the self-referenced offset's reference (spec 11.5).
    ``nearest_beyond`` (``nearest_beyond``) is filled in by ``generate`` once the FPP neighbours are known."""
    est = centroid_estimate(rung5_metrics)
    if est is None:
        return None
    rows, kinds = [], set()
    for s in _voting_rows(rung5_metrics):
        ref = _self_reference(s)
        v = _axis_offsets(s, ref[0]) if ref is not None else None
        if v is None:
            rows = []
            break
        rows.append((v, _num(s, "centroid_sigma_pix")))
        kinds.add(ref[1])
    block = {"self_referenced_arcsec": None, "self_referenced_err_arcsec": None, "sigma": None, "counts": None,
             "reference": None, "catalogue_referenced_arcsec": est.offset,
             "catalogue_referenced_err_arcsec": est.sigma, "level_arcsec": OFFSET_LEVEL_ARCSEC,
             "sigma_level": OFFSET_SIGMA, "rule": OFFSET_RULE, "n_seasons": est.n_seasons}
    block.update(exclusion_radius_arcsec=exclusion_radius(block), exclusion_rule=EXCLUSION_RULE)
    pulls = [float(np.hypot(*(d - pt))) for s in _voting_rows(rung5_metrics)
             if (d := _pair(s, "direct_centroid_colrow")) is not None and (pt := _pair(s, "pos_target")) is not None]
    pull = max(pulls) if pulls else None
    block.update(direct_pull_pix=pull, pulled=None if pull is None else bool(pull > PULL_PIX), nearest_beyond=None)
    if rows:
        mean, stat, scatter = _vector_mean(rows)
        # the larger of the combined error and the scatter, the rule of the catalogue-referenced error
        off, err = float(np.hypot(*mean)) * PIXEL_ARCSEC, max(stat, scatter) * PIXEL_ARCSEC
        sigma = off / err
        # Bryson's rule is judged only against the star's own image; the fit's common shift is not that estimator
        counts = bool(sigma > OFFSET_SIGMA and off > OFFSET_LEVEL_ARCSEC) if kinds == {"direct"} else None
        block.update(self_referenced_arcsec=off, self_referenced_err_arcsec=err, sigma=sigma, counts=counts,
                     reference=" and ".join(SELF_REFERENCES[k] for k in sorted(kinds)))
    return block


def nearest_beyond(block: dict | None, neighbours) -> dict | None:
    """The Gaia neighbour nearest the Gaia-referenced difference centroid beyond the block's exclusion radius, by its
    ``centroid_sep_arcsec`` (``fpp_inputs``' neighbours): {"source_id", "sep_arcsec", "centroid_sep_arcsec",
    "gmag"}; None without the radius or such a neighbour."""
    radius = _num(block, "exclusion_radius_arcsec") if isinstance(block, dict) else None
    rows = [n for n in neighbours or [] if radius is not None and n.centroid_sep_arcsec is not None
            and math.isfinite(n.centroid_sep_arcsec) and n.centroid_sep_arcsec > radius]
    if not rows:
        return None
    n = min(rows, key=lambda x: x.centroid_sep_arcsec)
    return {"source_id": n.source_id, "sep_arcsec": n.sep_arcsec, "centroid_sep_arcsec": n.centroid_sep_arcsec,
            "gmag": n.gmag}


# ---------------------------------------------------------------------------------------------------- predicted leak

LEAK_MODEL = ("the target's share of the two stars' light inside the neighbour's optimal aperture under rung 5's "
              "two-Gaussian PSF model (its season's width and common shift), the two fluxes fitted to each pixel "
              "file's median quality-0 image, times the target's depth")


def _kept_tpfs(store, blob, kic: int | None) -> list:
    """The kept pixel files of ``kic`` (``Store.kept_artifacts``), each read from the blob with ``parse_tpf``, one per
    quarter in quarter order, as ``crosscheck._copy_pixel_files`` reads them; one that does not read back is skipped.
    Nothing is fetched."""
    if not kic:
        return []
    out: dict = {}
    for url, path in sorted(store.kept_artifacts("tpf", tpf_listing_url(int(kic))).items()):
        try:
            tpf = parse_tpf(blob.get(path), url.rsplit("/", 1)[-1])
        except Exception:               # gone from the blob, or not a readable pixel file
            log.warning("kept pixel file %s unreadable; left out of the predicted leak", url, exc_info=True)
            continue
        out.setdefault(int(tpf.quarter), tpf)
    return [out[q] for q in sorted(out)]


def _season_psf(seasons: list[dict], season: int | None) -> tuple[float, tuple[float, float]] | None:
    """(PSF width, common shift) of rung 5's season ``season`` (a missing shift reads as none), or the medians over
    the seasons with a width when the file has no archive SEASON or rung 5 stored no width for it; None when no
    season has a positive width."""
    rows = []
    for s in seasons:
        w = _num(s, "psf_sigma_pix")
        if w is not None and w > 0:
            shift = _pair(s, "shift_pix")
            rows.append((s.get("season"), w, np.zeros(2) if shift is None else shift))
    if not rows:
        return None
    match = [r for r in rows if season is not None and r[0] == season]
    if match:
        _s, w, shift = match[0]
    else:
        w, shift = float(np.median([r[1] for r in rows])), np.median(np.array([r[2] for r in rows]), axis=0)
    return float(w), (float(shift[0]), float(shift[1]))


def predicted_leak(tpfs: list, target: dict, rung5_metrics: dict, depth_ppm: float | None) -> dict | None:
    """The dip this star alone would put into the neighbour's optimal-aperture flux, printed beside rung 5's measured
    leak (spec 11.5); None without a stored leak test. Per kept pixel file: the optimal-aperture mask ((APERTURE &
    ``OPTIMAL_APERTURE_BIT``) != 0), the median image over the quality-0 cadences, the two stars' positions from the
    file's WCS (the target's catalogue position; the neighbour's from the target meta's ``tpf_org_ra``/``tpf_org_dec``
    when present, else the file's RA_OBJ/DEC_OBJ, as rung 5 places them), the PSF width and common shift of rung 5's
    season with the file's archive SEASON (``_season_psf``), the two fluxes (``psf_fluxes``) and the target's share
    of the light in the mask (``aperture_share``). Only the quarters rung 5's seasons used (their ``quarters``
    lists; every kept file for a row stored before them) are read, so Q0 and the unusable quarters are left out
    (Task 12 review M-4). ``share`` is the mean over those files and ``predicted_ppm`` the depth times it. A file
    whose fitted flux of this star is not positive gives a share of 0 with that reason (M-7). {"predicted_ppm",
    "share", "quarters": [{"quarter", "season", "share", "reason"}], "measured_ppm", "measured_err_ppm", "kic",
    "model", "reason"}: without a kept file the reason is "no kept pixel file of KIC <kic>", without a stored width
    "rung 5 stored no PSF width", without a used file "no pixel file of KIC <kic> that the pixel check used", and
    without a depth "no depth on the fold" (M-3), and the prediction is None."""
    m = rung5_metrics or {}
    leak = m.get("leak_depth_ppm")
    measured = _num({"x": leak[0]}, "x") if isinstance(leak, list) and leak else None
    if measured is None:
        return None
    meta = (target or {}).get("meta") or {}
    kic = meta.get("tpf_org") or m.get("neighbour_kic")
    block = {"predicted_ppm": None, "share": None, "quarters": [], "measured_ppm": measured,
             "measured_err_ppm": _num({"x": leak[1]}, "x") if len(leak) > 1 else None, "kic": kic,
             "model": LEAK_MODEL, "reason": None}
    seasons = _pixel_seasons(m)
    if not tpfs:
        return {**block, "reason": f"no kept pixel file of KIC {kic}"}
    if _season_psf(seasons, None) is None:
        return {**block, "reason": "rung 5 stored no PSF width"}
    listed = [s.get("quarters") for s in seasons if isinstance(s.get("quarters"), list)]
    if listed:
        used = {int(q) for qs in listed for q in qs}
        tpfs = [tpf for tpf in tpfs if int(tpf.quarter) in used]
        if not tpfs:
            return {**block, "reason": f"no pixel file of KIC {kic} that the pixel check used"}
    n_ra, n_dec = meta.get("tpf_org_ra"), meta.get("tpf_org_dec")
    quarters = []
    for tpf in tpfs:
        mask = (np.asarray(tpf.aperture).astype(np.int64) & OPTIMAL_APERTURE_BIT) != 0
        good = np.asarray(tpf.quality) == 0
        if mask.shape != tpf.cube.shape[1:] or not mask.any() or not good.any():
            continue
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)         # an all-NaN pixel stays NaN and is left out
            image = np.nanmedian(tpf.cube[good], axis=0)
        ra, dec = (n_ra, n_dec) if n_ra is not None and n_dec is not None else (tpf.ra_obj, tpf.dec_obj)
        pt, pn = tpf.pixel_of(target.get("ra"), target.get("dec")), tpf.pixel_of(ra, dec)
        width, shift = _season_psf(seasons, tpf.season)
        fluxes = psf_fluxes(image, np.isfinite(image), pt, pn, width, shift)
        share = aperture_share(mask, pt, pn, width, shift, *fluxes) if fluxes is not None else None
        if share is not None:
            why = "the fitted flux of this star is not positive" if fluxes[0] <= 0 else None
            quarters.append({"quarter": int(tpf.quarter), "season": tpf.season, "share": share, "reason": why})
    if not quarters:
        return {**block, "reason": f"no kept pixel file of KIC {kic} gave the two stars' fluxes"}
    share = float(np.mean([q["share"] for q in quarters]))
    depth = _num({"x": depth_ppm}, "x")
    if depth is None:
        return {**block, "quarters": quarters, "share": share, "reason": "no depth on the fold"}
    return {**block, "quarters": quarters, "share": share, "predicted_ppm": depth * share}


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
    """The inputs of section 06 (the illustrated panel), or None when the star could not be characterised."""
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


def _lookup(target: Target, signal: dict, tests: list[dict], *, fetch, store: Store, extinction: dict | None = None):
    """``stars.lookup`` with each query group caught: a failed query on the star's own row gives route "none", one on
    the neighbour cone gives ``None`` for the neighbours (unknown, not empty); each is logged at warning level and
    returned in the error list as "<group>: <error>" (``GAIA_STAR`` or ``GAIA_CONE``). A Gaia row ``stars.derive``
    would characterise (a parallax, its error and a BP-RP) without a stored ``extinction`` raises ``NoExtinction``
    (spec 12.3: never a silent fallback); the block goes to ``stars.derive``."""
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
    if (extinction is None and gaia is not None and gaia.parallax is not None and gaia.parallax_error is not None
            and gaia.bp_rp is not None):
        raise NoExtinction(NO_EXTINCTION.format(target=target.target_id))
    cone_target = target
    if target.gmag is None and gaia is not None and gaia.gmag is not None:
        cone_target = target.model_copy(update={"gmag": gaia.gmag})
    try:
        neighbours = stars.query_neighbours(cone_target, depth, fetch=fetch, store=store)
    except ServiceUnavailable as e:
        neighbours = None
        errors.append(f"{GAIA_CONE}: {e}")
        log.warning("Gaia neighbour cone for %s failed: %s", target.target_id, e)
    star, planet = stars.derive(gaia, float(signal["period"]), fit_k, depth, np.random.default_rng(STARS_SEED),
                                extinction)
    return star, planet, neighbours, gaia, errors


def quoted_geometry(shape: dict | None) -> tuple[float, float, float] | None:
    """(k, b, a/R*) of the quoted shape fit as the texts quote them: the resample medians when the bootstrap is ok,
    else the fit's full-data values (``quoted_fit``); None without an ok quoted fit or with a value missing."""
    shape = shape if isinstance(shape, dict) else {}
    fit = quoted_fit(shape)
    if fit is None:
        return None
    boot = shape.get("ok") is True
    vals = []
    for key in ("k", "b", "a_over_r"):
        median = (shape.get(key) or {}).get("median") if boot else None
        vals.append(_num({"x": fit.get(key) if median is None else median}, "x"))
    return None if None in vals else (vals[0], vals[1], vals[2])


def fpp_inputs(signal: dict, tests: list[dict], star: stars.StarParams, planet: stars.PlanetParams,
               neighbours: list[Neighbour] | None, gmag: float | None, depth_ppm: float | None = None, *,
               blocks: dict | None = None) -> FppInputs:
    """The FPP inputs from the signal, rung 2, the star and, when a rung 5 row has at least ``MIN_VOTING_SEASONS``
    voting seasons (the rung's own pass rule; one season decides nothing), the centroid (``centroid_estimate``): each Gaia neighbour's ``centroid_sep_arcsec`` is then its separation less the centroid's
    offset from the target (the nearest the centroid can sit to it, since the cone gives no position angle), floored
    at zero, and the centroid error with the two numbers it was chosen from goes into the inputs record. The
    neighbour list counts as known only when the cone answered (``neighbours`` is not None) and the target has a G;
    otherwise the FPP is not computed. ``depth_ppm`` (the dossier's depth on its fold, ``ephemeris_extra``) is the
    depth when given and positive; rung 2's, else the search depth (``_depth_fraction``), otherwise. With the
    dossier's error ``blocks`` (FPP model 3, the Task 10 review's M-5) the geometry is the quoted shape fit's
    (``quoted_geometry``; none when the quoted fit failed) and the odd and even depths are the parity block's per-event
    ones, their error the two errors in quadrature; without the blocks they are rung 2's. With the centroid the BEB
    term's confusion radius is the pixel exclusion radius (``exclusion_radius``, spec 11.5)."""
    shape = _metrics(tests, "rung2_shape")
    fit_ok = shape.get("fit_ok") is True
    fit_k = _num(shape, "fit_k") if fit_ok else None
    fit_b = _num(shape, "fit_b") if fit_ok else None
    fit_a = _num(shape, "fit_a_over_r") if fit_ok else None
    odd, even, parity = _num(shape, "odd_ppm"), _num(shape, "even_ppm"), _num(shape, "parity_sigma")
    odd_even_err = abs(odd - even) / parity if odd is not None and even is not None and parity else None
    blocks = blocks if isinstance(blocks, dict) else {}
    if isinstance(blocks.get("shape"), dict):
        fit_k, fit_b, fit_a = quoted_geometry(blocks["shape"]) or (None, None, None)
    if isinstance(blocks.get("parity"), dict):
        o, e = blocks["parity"].get("odd") or {}, blocks["parity"].get("even") or {}
        po, pe, eo, ee = _num(o, "depth_ppm"), _num(e, "depth_ppm"), _num(o, "err_ppm"), _num(e, "err_ppm")
        if None not in (po, pe, eo, ee):
            odd, even, odd_even_err = po, pe, math.hypot(eo, ee)
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
        period=float(signal["period"]),
        depth=depth_ppm / 1e6 if depth_ppm is not None and depth_ppm > 0 else _depth_fraction(signal, shape),
        gmag=float(gmag) if gmag is not None else float("nan"),
        teff=star.teff.median if star.teff else None, radius=star.radius.median if star.radius else None,
        mass=star.mass.median if star.mass else None,
        rp_rearth=planet.rp_rearth.median if planet.rp_rearth else None,
        fit_k=fit_k, fit_b=fit_b, fit_a_over_r=fit_a,
        secondary_ppm=_num(shape, "secondary_ppm"), secondary_err_ppm=_num(shape, "control_rms_ppm"),
        odd_ppm=odd, even_ppm=even, odd_even_err_ppm=odd_even_err, neighbours=tuple(neighbours),
        centroid_sigma_arcsec=centroid.sigma if centroid is not None else None, neighbours_known=known,
        sigma_c_stat_arcsec=centroid.sigma_stat if centroid is not None else None,
        sigma_c_scatter_arcsec=centroid.sigma_scatter if centroid is not None else None,
        sigma_c_source=centroid.source if centroid is not None else None,
        exclusion_radius_arcsec=(exclusion_radius({"catalogue_referenced_err_arcsec": centroid.sigma})
                                 if centroid is not None else None))


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


def floor_pix(seasons: list[dict]) -> float | None:
    """The systematic floor in the seasons' centroid errors (``floor_pix``, stored from pixel rule version 3 on, the
    largest when they differ); None for a row stored before it."""
    vals = [v for s in seasons if isinstance(s, dict) and (v := _num(s, "floor_pix")) is not None and v > 0]
    return max(vals) if vals else None


def floor_clause(seasons: list[dict]) -> str:
    """", with a pointing-jitter floor of F px per axis added in quadrature" for a row that stores the floor (the
    measured quarter-to-quarter term, named so since spec 11.5), "" otherwise: the clause the Monte Carlo caveat
    carries."""
    f = floor_pix(seasons)
    return "" if f is None else f", with a pointing-jitter floor of {f:.3f} px per axis added in quadrature"


def close_call_text(seasons: list[dict]) -> str:
    """The Monte Carlo caveat, naming the floor when the row stores it (``floor_clause``)."""
    return CLOSE_CALL_TEXT.format(floor=floor_clause(seasons))


def _error_caveats(data: dict) -> list[dict]:
    """The caveats of the version-3 error blocks, in order: no ephemeris error, no shape error, and a k full-data
    value (``point``) further from the quoted resample median than the larger half-width. A dossier without the blocks
    gets none."""
    timing, shape = data.get("timing"), data.get("shape")
    out = []
    if isinstance(timing, dict) and timing.get("ok") is False:
        reason = timing.get("reason") or "no reason stored"
        need = (f" An error needs at least {eph.MIN_EVENTS} transits timed one by one."
                if reason.startswith(f"fewer than {eph.MIN_EVENTS}") else "")
        out.append({"key": "no_ephemeris_error", "title": "The ephemeris carries no error",
                    "text": f"The period and the mid-transit time are the search values, quoted without an error "
                            f"({reason}).{need}"})
    if isinstance(shape, dict) and shape.get("ok") is False:
        out.append({"key": "no_shape_error", "title": "The transit shape carries no error",
                    "text": f"The radius ratio, impact parameter, a/R* and duration have no bootstrap error "
                            f"({shape.get('reason') or 'no reason stored'})."})
    k = (shape or {}).get("k") if isinstance(shape, dict) and shape.get("ok") else None
    vals = [(k or {}).get(x) for x in ("median", "point", "minus", "plus")]
    if k and None not in vals and abs(vals[0] - vals[1]) > max(vals[2], vals[3]):
        out.append({"key": "shape_offset", "title": "The shape fit sits off its bootstrap",
                    "text": "The full-curve radius ratio differs from the bootstrap median, the quoted value, by more "
                            "than its 1-sigma spread; quote the full-data value beside it."})
    return out


def partial_box_seasons(seasons: list[dict]) -> list[dict]:
    """The seasons that voted (``voting`` stored and true) with a 3 x 3 box cut by the stamp edge (``box_partial``
    stored and true). A row stored before the box keys existed has none."""
    return [s for s in seasons if isinstance(s, dict) and s.get("voting") is True and s.get("box_partial") is True]


def partial_box_sentence(seasons: list[dict], short: bool = False) -> str:
    """One sentence naming the voting seasons whose box is cut by the stamp edge, the star or stars whose box it is
    (a box with fewer than nine pixels) and the largest ``edge_bias_pix`` allowance among them; "" when none is.
    ``short`` is the Research Note's form: "..., adding up to B px to the centroid error."."""
    cut = partial_box_seasons(seasons)
    if not cut:
        return ""
    names = [str(s.get("season")) if s.get("season") is not None else "?" for s in cut]
    listed = names[0] if len(names) == 1 else ", ".join(names[:-1]) + f" and {names[-1]}"
    target = any((n := _num(s, "box_pixels_target")) is not None and n < 9 for s in cut)
    neighbour = any((n := _num(s, "box_pixels_neighbour")) is not None and n < 9 for s in cut)
    if target and neighbour:
        whose = "both stars' 3 x 3 boxes are"
    elif neighbour:
        whose = "the neighbour's 3 x 3 box is"
    else:
        whose = "this star's 3 x 3 box is"
    bias = max((_num(s, "edge_bias_pix") or 0.0 for s in cut), default=0.0)
    if short:
        return (f"In season{'s' if len(cut) > 1 else ''} {listed} {whose} cut by the stamp edge, adding up to "
                f"{bias:.2f} px to the centroid error.")
    return (f"In season{'s' if len(cut) > 1 else ''} {listed} {whose} cut by the stamp edge; the centroid there is "
            f"measured on the pixels present and the shift the cut would give a point source ({bias:.2f} px at most) "
            "is added to its error.")


def partial_box_clause(seasons: list[dict]) -> str:
    """The short form for a word-limited text: ", N of them with a box cut by the stamp edge", N the number of voting
    seasons whose box is cut; "" when none is."""
    n = len(partial_box_seasons(seasons))
    return f", {n} of them with a box cut by the stamp edge" if n else ""


PIXEL_RULE_STATUSES = ("candidate_stage2", "undetermined")     # the settled statuses a stale pixel rule matters for


def pixel_rule_stale(data: dict) -> str | None:
    """"the pixel verdict predates pixel rule version N" when a stage 2 or undetermined signal's latest rung 5 row
    localised (its metrics hold the seasons) under a rule version below ``PIXEL_RULE_VERSION`` (1 when none is
    stored): the recheck reruns such a row, and until it has, the pack's live draft is refused (``gate_problems``)."""
    if (data.get("status") or {}).get("final_status") not in PIXEL_RULE_STATUSES:
        return None
    metrics = _metrics(data.get("tests") or [], "rung5_pixels")
    if not isinstance(metrics.get("seasons"), list):
        return None
    try:
        version = int(metrics.get("rule_version") or 1)
    except (TypeError, ValueError):
        version = 1
    if version >= PIXEL_RULE_VERSION:
        return None
    return f"the pixel verdict predates pixel rule version {PIXEL_RULE_VERSION}"


PIXEL_DISAGREEING = {"approve": ("fail", "undetermined")}   # as recheck.DISAGREEING


def pixel_verdict_disagrees(data: dict) -> str | None:
    """The sentence naming both when the latest rung 5 row, written after the reviewer's latest decision (the pixel
    rerun of a decided signal under a newer rule), contradicts it: a fail or an undetermined row under an approval.
    None otherwise, so a verdict that agrees clears nothing but the stale-rule caveat, and a reviewer who approved
    over an older row knowingly is not flagged. ``gate_problems`` refuses the live draft on it."""
    decision = (data.get("status") or {}).get("decision") or {}
    row = _rung(data.get("tests") or [], "rung5_pixels")
    if not isinstance(decision, dict) or row is None:
        return None
    verdict, what = row.get("status"), decision.get("decision")
    if verdict not in PIXEL_DISAGREEING.get(what or "", ()):
        return None
    try:
        made = datetime.fromisoformat(str(row.get("created")))
        decided = datetime.fromisoformat(str(decision["utc"]))
    except (KeyError, TypeError, ValueError):
        return None
    if made.replace(tzinfo=None) <= decided.replace(tzinfo=None):
        return None
    return (f"The reviewer's decision is {what}, but the pixel step rerun under pixel rule {PIXEL_RULE_VERSION} "
            f"after it gives {verdict} ({row.get('reason') or 'no reason stored'}). The decision stands until a "
            "reviewer looks again, and the live Zenodo draft waits for that.")


def prior_claims_unchecked(data: dict) -> bool:
    """The dossier was built where the candidates folder was absent (``prior_claims`` stored as None), so no prior
    claim on the signal was looked for. A dossier from before the key existed reads as checked with none."""
    return "prior_claims" in data and data["prior_claims"] is None


CANDIDATE_CAVEAT = "This is a candidate until follow-up confirms it"
NO_SPECTRUM = "No spectrum: the planet radius depends on the stellar radius from Gaia colour and parallax"
NO_REDDENING = ", and the reddening is not measured"
# spec 12.3 as amended: no 3D value, the reddening bracketed by the full column; neither, the build-time clause
REDDENING_BRACKETED = ", and the reddening is only bracketed"
NO_BAYESTAR = " (Bayestar19 not available at build time)"


def no_spectrum_title(star: dict | None) -> str:
    """The stellar-radius caveat's title (spec 11.6, 12.3): ``NO_SPECTRUM`` alone when the star carries a 3D
    extinction (adopted or stored by hand); with ``REDDENING_BRACKETED`` when it is not available and the full column
    bounds it; with ``NO_REDDENING`` and ``NO_BAYESTAR`` when neither is there. A star block without an extinction (a
    dossier before version 6) keeps the earlier rule: ``NO_REDDENING`` when Gaia gives no GSP-Phot E(BP-RP). The pack
    reads the title from here, so it follows the star block it prints."""
    star = star or {}
    ext = star.get("extinction")
    if isinstance(ext, dict):
        if ext.get("state") != stars.EXT_NOT_AVAILABLE:
            return NO_SPECTRUM
        if (ext.get("column") or {}).get("status") == stars.COLUMN_OK:
            return NO_SPECTRUM + REDDENING_BRACKETED
        return NO_SPECTRUM + NO_REDDENING + NO_BAYESTAR
    red = star.get("ebpminrp_gspphot")
    return NO_SPECTRUM + ("" if isinstance(red, (int, float)) and not isinstance(red, bool) else NO_REDDENING)


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
        out.append({"key": "close_call", "title": "A season sits close to the 3 sigma line",
                    "text": close_call_text(_pixel_seasons(_metrics(tests, "rung5_pixels")))})
    cut = partial_box_sentence(_pixel_seasons(_metrics(tests, "rung5_pixels")))
    if cut:
        out.append({"key": "partial_box", "title": "A season's box is cut by the stamp edge", "text": cut})
    if fpp.get("pixels") is False:
        out.append({"title": "The false-positive estimate has no pixel exclusion",
                    "text": f"Without a difference-image centroid every neighbour within two Kepler pixels "
                            f"({NO_PIXEL_CONFUSION_ARCSEC:g} arcsec) counts in full towards the background binary "
                            f"term, with a fall-off of one pixel ({NO_PIXEL_FALLOFF_ARCSEC:g} arcsec) beyond, so the "
                            "estimate is labelled \"no pixel data yet\"."})
    stale = pixel_rule_stale(data)
    if stale:
        out.append({"key": "pixel_rule_stale", "title": stale.capitalize(),
                    "text": f"The rung 5 row was made under an older pixel rule, so {stale}. The recheck reruns it "
                            "under the current rule, and the live Zenodo draft waits for that."})
    disagree = pixel_verdict_disagrees(data)
    if disagree:
        out.append({"key": "pixel_verdict_disagrees", "title": "The pixel rerun disagrees with the reviewer's decision",
                    "text": disagree})
    if prior_claims_unchecked(data):
        out.append({"key": "prior_claims_unchecked", "title": "Prior claims on this signal were not checked",
                    "text": "The engine ran without the docs/candidates folder of a source checkout, so it could not "
                            "read whether someone reported this signal first. The live Zenodo draft waits for a "
                            "dossier built from a checkout."})
    out.extend(_error_caveats(data))
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
        out.append({"title": "The star cannot be characterised from Gaia photometry",
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
    out.append({"key": "no_spectrum", "title": no_spectrum_title(star), "text": text})
    stab = next((t.get("metrics") or {} for t in tests if t.get("rung") == "rung3_stability"), None)
    if stab is not None:
        n_seasons = stab.get("n_seasons")
        n_seasons = len(stab.get("season_depths") or {}) if n_seasons is None else int(n_seasons)
        if n_seasons < 2:          # rung 3's groups are quarters modulo 4, not the archive roll season (spec 11.6)
            out.append({"title": "The depth is measured in one quarter group only",
                        "text": f"Rung 3 found data in {n_seasons} quarter group{'' if n_seasons == 1 else 's'}, so "
                                "a depth that changes with the detector cannot be tested."})
    out.append({"title": CANDIDATE_CAVEAT,
                "text": "Follow-up would need ground-based transit photometry at a predicted time, a spectrum of the "
                        "host and high-resolution imaging. Without imaging, an unresolved bound companion within "
                        f"{UNRESOLVED_COMPANION_ARCSEC} arcsec that carries the transit is the open false-positive "
                        "channel: SkySift's scenario model leaves it out, and TRICERATOPS counts it as its STP "
                        "scenario."})
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
            "ladder_versions": per_rung, "git_commit": git_commit(), "queries": queries, "files": files,
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


CANDIDATES_DIR = Path(__file__).resolve().parents[2] / "docs" / "candidates"     # present in a source checkout only
# spec 12.3: docs/candidates/<target>/extinction.json, written once by tools/extinction.py; tests point it elsewhere
EXTINCTION_DIR = CANDIDATES_DIR
NoExtinction, ExtinctionError = stars.NoExtinction, stars.ExtinctionError
NO_EXTINCTION = ("no stored extinction for {target}: run tools/extinction.py --target {target} --data-dir <data dir> "
                 "(it stores the IRSA full-column E(B-V) and the state of the Bayestar19 value), or store a 3D value "
                 "by hand with --store")
MALFORMED = "malformed"


def extinction_keys(target_id: str) -> dict:
    """``fingerprint``'s extinction arguments from the target's stored file, {"extinction_ebv", "column_ebv"}, for the
    checks that recompute the fingerprint (the page's staleness, the pack's approval) as ``generate`` does; a malformed
    file gives ``MALFORMED`` for both, so the dossier reads as stale until the file is fixed."""
    try:
        block = stars.load_extinction(target_id, EXTINCTION_DIR)
    except stars.ExtinctionError:
        return {"extinction_ebv": MALFORMED, "column_ebv": MALFORMED}
    return {"extinction_ebv": (block or {}).get("ebv"), "column_ebv": stars.column_ebv(block)}


PRIOR_CLAIM_FIELDS = ("doi", "date", "author", "title", "status", "note", "relationship", "development_note",
                      "development_note_short", *PRIOR_CLAIM_NUMBERS, "fpp_tool")
PRIOR_CLAIM_REQUIRED = ("doi", "date", "author", "title")


class PriorClaimsError(ValueError):
    """A ``prior_claims.json`` that is not a list of claims each carrying a DOI, a date, an author and a title, or
    that names a relationship outside ``RELATIONSHIPS``."""


def prior_claims(target_id: str, root: Path | None = None) -> list[dict]:
    """The public, dated claims on the same signal made before SkySift's, as the person running the engine declared
    them in ``docs/candidates/<target>/prior_claims.json`` (a list of {doi, date, author, title, status, note,
    relationship, development_note, development_note_short (the Research Note's clause, Task 13 fix round 1), the
    claim's numbers ``PRIOR_CLAIM_NUMBERS`` and fpp_tool}; the date as
    YYYY-MM-DD). Text fields come back as stripped strings ("" when absent), numbers as finite floats or None, and the
    relationship as one of ``RELATIONSHIPS`` or None when absent. An empty list when the file is absent. A file that
    does not read as such a list, or names another relationship, raises PriorClaimsError, since a claim dropped in
    silence would let the texts call a re-detection a new candidate."""
    path = Path(CANDIDATES_DIR if root is None else root) / target_id / "prior_claims.json"
    if not path.is_file():
        return []
    try:
        claims = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise PriorClaimsError(f"{path} does not read as JSON ({e})") from e
    if not isinstance(claims, list) or not all(isinstance(c, dict) for c in claims):
        raise PriorClaimsError(f"{path} is not a list of claims")
    out = []
    for i, claim in enumerate(claims):
        missing = [k for k in PRIOR_CLAIM_REQUIRED if not str(claim.get(k) or "").strip()]
        if missing:
            raise PriorClaimsError(f"{path}: claim {i + 1} has no {', '.join(missing)}")
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(claim["date"])):
            raise PriorClaimsError(f"{path}: claim {i + 1} has the date {claim['date']!r}, not YYYY-MM-DD")
        value = claim.get("relationship")
        relationship = str(value).strip() if value is not None and str(value).strip() else None
        if relationship is not None and relationship not in RELATIONSHIPS:
            raise PriorClaimsError(f"{path}: claim {i + 1} has the relationship {value!r}, not one of "
                                   f"{', '.join(RELATIONSHIPS)}")
        row = {k: (_num(claim, k) if k in PRIOR_CLAIM_NUMBERS else str(claim.get(k) or "").strip())
               for k in PRIOR_CLAIM_FIELDS}
        out.append({**row, "relationship": relationship})
    return sorted(out, key=lambda c: c["date"])



def public_claims(data: dict) -> dict:
    """``data`` with each prior claim cut to ``PRIOR_CLAIM_PUBLIC`` (the status of someone else's submission and the
    owner's note stay in prior_claims.json): ``generate`` stores this, and the pack and the standalone export cut a
    dossier stored by earlier code again before they embed it. ``data`` itself is not changed; None stays None."""
    claims = data.get("prior_claims")
    if not isinstance(claims, list):
        return data
    return {**data, "prior_claims": [{k: c.get(k) for k in PRIOR_CLAIM_PUBLIC} for c in claims if isinstance(c, dict)]}


def hand_built_localisation(target_id: str, rung5_metrics: dict, root: Path | None = None) -> dict | None:
    """How far, season by season, the rung 5 centroid's distance from the star sits from the hand-built study's
    (``docs/candidates/<target>/localisation/results.json``, its S/N-box centroid distance to the candidate per
    season, quarter mod 4 as rung 5's seasons): {"max_diff_pix", "n_seasons", "agrees", "combined_err_pix",
    "source"} over the seasons that voted and that the study lists, ``agrees`` when every season's difference is below
    its combined error (rung 5's centroid error and the study's Monte Carlo error in quadrature; ``agrees`` and
    ``combined_err_pix`` None when a season has neither error), or None without the file or a common season."""
    base = Path(CANDIDATES_DIR if root is None else root)
    path = base / target_id / "localisation" / "results.json"
    if not path.is_file():
        return None
    try:
        study = json.loads(path.read_text(encoding="utf-8"))
        hand = (study.get("summary") or {}).get("seasons_snrbox_centroid_dist_to_candidate_pix") or {}
        seasons = study.get("seasons") if isinstance(study.get("seasons"), dict) else {}
    except (OSError, ValueError, AttributeError):
        return None
    diffs = []          # (difference, combined error) per common season
    for s in (rung5_metrics or {}).get("seasons") or []:
        ours, theirs = s.get("dist_target_pix"), hand.get(str(s.get("season")))
        if s.get("voting") and isinstance(ours, int | float) and isinstance(theirs, int | float):
            err = _num(s, "centroid_sigma_pix") or 0.0
            mc = (seasons.get(str(s.get("season"))) or {}).get("snrbox_diff_centroid_sigma_mc_colrow")
            if isinstance(mc, list) and len(mc) == 2 and all(isinstance(x, int | float) for x in mc):
                err = math.hypot(err, math.sqrt((mc[0] ** 2 + mc[1] ** 2) / 2))
            diffs.append((abs(float(ours) - float(theirs)), err))
    if not diffs:
        return None
    worst = max(diffs)
    known = all(e > 0 for _d, e in diffs)        # a season without either error gives no verdict
    return {"max_diff_pix": round(worst[0], 4), "n_seasons": len(diffs),
            "agrees": all(d < e for d, e in diffs) if known else None,
            "combined_err_pix": round(worst[1], 4) if known else None,
            "source": f"docs/candidates/{target_id}/localisation/results.json"}


def _alt_flux(lc: LightCurve, thresholds, signal: dict):
    """The secondary-aperture flux the signal was vetted on (``pipeline.revet_flux``), with a second-pass signal's
    mask applied as the sweep applied it (``pipeline._vetting_bundle``); None without a secondary flux."""
    if lc.flux_alt is None or thresholds is None:
        return None
    sig = Signal(**{k: signal[k] for k in Signal.model_fields if signal.get(k) is not None})
    alt = revet_flux(lc, thresholds, sig)[1]
    return None if alt is None else apply_mask(lc.time, alt, (signal.get("extra") or {}).get("mask"))


def quoted_radius_ratio(shape: dict | None) -> float | None:
    """The one radius ratio every text prints and the planet radius is made from (spec 11.3 as amended in fix round 1
    of Task 10, I-3): the quoted centre, the resample median of the quoted fit, when the bootstrap is ok, else the
    quoted fit's full-data k; None without an ok quoted fit."""
    shape = shape if isinstance(shape, dict) else {}
    fit = quoted_fit(shape)
    if fit is None:
        return None
    median = (shape.get("k") or {}).get("median") if shape.get("ok") is True else None
    return _finite_or_none(median if median is not None else fit.get("k"))


def _rederive(gaia, star: stars.StarParams, planet: stars.PlanetParams, blocks: dict | None,
              extinction: dict | None = None):
    """The star and planet again with the quoted radius ratio (``quoted_radius_ratio``) at the fold's period and
    depth, when Gaia answered; the same seed and the same stored extinction, so the star's draws are those of
    ``_lookup``. Unchanged without a fit."""
    k = quoted_radius_ratio((blocks or {}).get("shape"))
    if gaia is None or k is None:
        return star, planet
    extra = blocks.get("ephemeris_extra") or {}
    depth = (_num(extra, "depth_ppm") or 0.0) / 1e6
    return stars.derive(gaia, float(extra["period_days"]), k, depth, np.random.default_rng(STARS_SEED), extinction)


def _model_line(shape: dict | None) -> dict | None:
    """The quoted shape fit with its limb darkening and exposure, as ``plot_arrays`` draws its model line; None
    without one."""
    fit = quoted_fit(shape)
    if fit is None or None in (fit.get("k"), fit.get("b"), fit.get("a_over_r")):
        return None
    return {"k": fit["k"], "b": fit["b"], "a_over_r": fit["a_over_r"], "u1": fit["u1"], "u2": fit["u2"],
            "exposure_days": fit["exposure_min"] / 1440.0}


SWEEP_QUERY = ("distinct target_id of runs with status done, the signal's detector and detector version, a job key "
               "not starting 'recheck:', on targets of the signal's source")


def sweep_size_of(store: Store, signal: dict, source: str | None) -> int:
    """``Store.sweep_size`` for the signal's detector and search version on ``source`` (kbonus_bkg when None): the
    count ``calibration_block`` stores and ``fingerprint`` watches."""
    return store.sweep_size(source or "kbonus_bkg", str(signal.get("detector") or ""),
                            str(signal.get("detector_version") or ""))


def calibration_block(store: Store, signal: dict, source: str, rows: list[dict], fap, target_id: str) -> dict:
    """The ``calibration`` block the false-alarm sentence reads (spec 11.4): ``sweep_size``, the targets the sweep
    searched with the signal's search (``Store.sweep_size``, by ``SWEEP_QUERY``; None when the count is 0, a store
    of recheck runs only or a signal of another search version), the source, detector and version it was counted
    for, ``null_stars`` (the distinct stars of the null calibration other than this target), ``null_stars_in_sweep``
    (how many of them the sweep searched too, ``Store.sweep_targets``: the calibration pilot is a selection apart
    from the sweep; None without a sweep size; round-2 final review, minor 6), ``null_searches`` (``fap.n``) and
    ``expected_by_chance``, the sweep size times the FAP (None without either)."""
    detector, version = str(signal.get("detector") or ""), str(signal.get("detector_version") or "")
    size = sweep_size_of(store, signal, source) or None
    stars = {str(r.get("target_id")) for r in rows if r.get("target_id") and r.get("target_id") != target_id}
    inside = (len(stars & store.sweep_targets(source or "kbonus_bkg", detector, version)) if size is not None
              else None)
    return {"sweep_size": size, "sweep_source": source, "sweep_detector": detector,
            "sweep_detector_version": version, "sweep_query": SWEEP_QUERY, "null_stars": len(stars),
            "null_stars_in_sweep": inside,
            "null_searches": fap.n,
            "expected_by_chance": size * fap.fap if size is not None and fap.fap is not None else None}


def generate(store: Store, blob, settings, target_id: str, *, signal_id: int | None = None, force: bool = False,
             fetch=net.fetch, log: Callable[[str], None] | None = None, n_boot: int | None = None) -> DossierReport:
    """Write ``<data_dir>/dossiers/<target_id>/dossier_data.json`` and its ``dossiers`` row (spec 5.4). A no-op
    (``generated=False``, the file untouched) when the stored fingerprint and dossier version match and ``force`` is
    False. Raises NoSignal when the target has no signal (or not ``signal_id``) and LightCurveReleased when the raw
    light curve is no longer stored (it is not downloaded again: offline except Gaia, spec 5.8). ``n_boot`` is the
    shape bootstrap's resample count, ``N_BOOT_DEFAULT`` (read at call time) when None."""
    say = log or (lambda _text: None)
    n_boot = N_BOOT_DEFAULT if n_boot is None else n_boot
    signal = pick_signal(store, target_id, signal_id)
    sid = signal["id"]
    tests = store.latest_tests(sid)
    decisions = store.decisions(sid)
    decision = decisions[0]["decision"] if decisions else None
    source = _target_dict(store, target_id).get("source") or "kbonus_bkg"
    # spec 12.3: the stored extinction first, so a malformed file stops the run before any work
    extinction = stars.load_extinction(target_id, EXTINCTION_DIR)
    fp = fingerprint(tests, signal["final_status"], decision, calibration_rows(settings.data_dir, source),
                     sweep_size_of(store, signal, source), extinction_ebv=(extinction or {}).get("ebv"),
                     column_ebv=stars.column_ebv(extinction))
    data_path = Path(settings.data_dir) / "dossiers" / target_id / "dossier_data.json"
    old = store.dossier(target_id)
    if (old is not None and not force and old["tests_fingerprint"] == fp and old["signal_id"] == sid
            and old["dossier_version"] == DOSSIER_VERSION and data_path.is_file()):
        say(f"dossier for {target_id} is current (signal {sid}); nothing to do")
        return _stored_report(data_path, sid, target_id)
    stale = _stale_reason(old, signal, force, fp)
    # read first: a malformed file stops the run before any work. Without the candidates folder (an installed
    # package, not a checkout) nothing was checked: None, a caveat and a gate problem, never a silent empty list.
    # The claim's status and note stay in the owner's file: the dossier JSON travels with the public pack.
    claims = (public_claims({"prior_claims": prior_claims(target_id)})["prior_claims"]
              if Path(CANDIDATES_DIR).is_dir() else None)
    target = _target_dict(store, target_id)
    say(f"signal {sid} ({signal['final_status']}, SDE {signal['sde']:.1f}); loading the light curve")
    lc = LightCurveLoader(store, blob, source_factory=_offline_factory(target_id, sid)).load(target, pin=True)
    fnorm = whitened(lc, signal)
    errors: dict[str, dict] = {}
    periodogram = _section("periodogram", errors, lambda: _periodogram(store, blob, target_id))
    pixels = _section("pixels", errors, lambda: pixels_section(store, blob, sid, _metrics(tests, "rung5_pixels")))
    # the star first: the shape fit's limb darkening and density prior come from it (spec 11.3)
    say("asking Gaia for the star and its neighbours")
    tgt = Target(**{k: target[k] for k in TARGET_FIELDS if target.get(k) is not None}, meta=target.get("meta") or {})
    star, planet, neighbours, gaia, gaia_errors = _lookup(tgt, signal, tests, fetch=fetch, store=store,
                                                         extinction=extinction)
    for err in gaia_errors:
        say(f"Gaia query failed: {err}")
    say(f"timing the transits, fitting the shape and bootstrapping it ({n_boot} resamples)")
    blocks = _section("ephemeris", errors, lambda: ephemeris_blocks(
        lc, fnorm, signal, tests, n_boot=n_boot, star=star, fnorm_alt=_alt_flux(lc, settings.thresholds, signal),
        thresholds=settings.thresholds, target=tgt))
    star, planet = _rederive(gaia, star, planet, blocks, extinction)
    plots = plot_arrays(lc, fnorm, signal, tests, periodogram=periodogram, fold=(blocks or {}).get("fold"),
                        shape=_model_line((blocks or {}).get("shape")))
    gmag = target.get("gmag") if target.get("gmag") is not None else (gaia.gmag if gaia is not None else None)
    inputs = fpp_inputs(signal, tests, star, planet, neighbours, gmag,
                        depth_ppm=((blocks or {}).get("ephemeris_extra") or {}).get("depth_ppm"), blocks=blocks)
    fpp = compute(inputs)
    calibration = cal.default_path(settings.data_dir, target.get("source") or "kbonus_bkg")
    rows = cal.read_rows(calibration) if calibration.is_file() else []
    version = calibration_version_for(signal, settings.thresholds)
    fap = cal.false_alarm(rows, float(signal["sde"]), version)
    data = _assemble(store, signal, target, tests, decisions, plots, pixels, star, planet, gaia_errors, fap, fpp,
                     inputs, calibration, version, settings.data_dir, blocks)
    data["calibration"] = calibration_block(store, signal, source, rows, fap, target_id)
    data["star"]["crowdsap"] = crowdsap_provenance(target.get("meta"), getattr(lc, "meta", None),
                                                   np.unique(np.asarray(lc.quarter)) if lc.quarter is not None
                                                   else None)
    if isinstance(data.get("centroid_offset"), dict):
        data["centroid_offset"]["nearest_beyond"] = nearest_beyond(data["centroid_offset"], inputs.neighbours)
    rung5 = _metrics(tests, "rung5_pixels")
    kic = (target.get("meta") or {}).get("tpf_org") or rung5.get("neighbour_kic")
    # the kept pixel files only: nothing is fetched (spec 5.8)
    data["predicted_leak"] = _section("predicted_leak", errors, lambda: predicted_leak(
        _kept_tpfs(store, blob, kic), target, rung5, data["ephemeris"].get("depth_ppm")))
    data["prior_claims"] = claims
    data["hand_built_localisation"] = hand_built_localisation(target_id, _metrics(tests, "rung5_pixels"))
    data["section_errors"] = errors
    data["caveats"] = caveats(data)
    data = _tidy(data)
    # the cross-check is only ever stored by ``skysift crosscheck --import``; here it is carried over, with
    # ``current`` recomputed against this dossier's inputs
    data["cross_check"] = cc.with_current((old or {}).get("cross_check"), data)
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
              calibration, version, data_dir, blocks: dict | None = None) -> dict:
    test_rows = [{"rung": t["rung"], "status": t["status"], "reason": t["reason"], "metrics": t["metrics"],
                  "created": _iso(t["created"])} for t in tests]
    verdict = explain.verdict(signal, tests, {"status": signal.get("candidate_status")} if signal.get(
        "candidate_status") else None, decisions)
    label, label_text = status_label(verdict)
    star_d = _star_with_k_errors({**dataclasses.asdict(star), "gaia_errors": list(gaia_errors)},
                                 (blocks or {}).get("shape"))
    period, t0, dur = float(signal["period"]), float(signal["t0"]), float(signal["duration"])
    blocks = blocks or {}
    ephemeris = {"period_days": period, "t0_bkjd": t0, "t0_bjd": t0 + BKJD_OFFSET, "duration_h": dur * 24,
                 "depth_ppm": float(signal["depth_ppm"]), "sde": float(signal["sde"]), "snr": float(signal["snr"]),
                 "n_events": int(signal["n_events"]), **(blocks.get("ephemeris_extra") or {})}
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
        "ephemeris": ephemeris,
        **{key: blocks.get(key) for key in ("timing", "shape", "parity", "secondary", "stability", "aperture",
                                            "depth_trend")},
        "plots": plots, "tests": test_rows,
        "why": [dataclasses.asdict(w) for w in explain.why(tests, aperture=blocks.get("aperture"),
                                                           stability=blocks.get("stability"))],
        "rechecks": store.rechecks(signal["id"]), "children": store.children(signal["id"]),
        "fap": {**dataclasses.asdict(fap), "one_in": fap.one_in(), "words": fap.words(), "sde": float(signal["sde"]),
                "signal_search_version": signal["detector_version"], "calibration_version": version,
                "min_nulls": cal.MIN_NULLS, "sentence_not_covered": FAP_NOT_COVERED},
        "fpp": {**fpp.as_dict(), "inputs": dataclasses.asdict(inputs), "words": fpp.words(), "label": fpp.label(),
                "sentence_not_covered": FPP_NOT_COVERED},
        "pixels": pixels, "centroid_offset": centroid_offset(_metrics(tests, "rung5_pixels")),
        "star": star_d, "planet": with_k_error(dataclasses.asdict(planet), blocks.get("shape")),
        "look": look_numbers(star, planet),
        # the TESS non-detection statement (referee pack spec 10) from the stored inputs and the fitted ephemeris
        "tess": tess.statement((target.get("meta") or {}).get("tess"), ephemeris),
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


def page_texts(data: dict) -> dict:
    """The sentences the dossier page, served or standalone, prints in the pack's own words (spec 11.1, 11.2), so
    the page says what the README says: ``fpp_lead`` the TRICERATOPS headline (``crosscheck.headline``) and
    ``fpp_value`` its median, both None without a complete, current cross-check; ``in_house`` the four-scenario
    estimate as a lower bound naming the scenarios it lacks (``zenodo.in_house_text``); ``class_text`` where the
    medians fall on Giacalone et al.'s scale, the class name as a label only (``crosscheck.class_label_text``);
    ``prior`` and ``comparison`` the prior-claim sentence and its sigma comparison (``pack.prior_claim_sentence``,
    ``pack.comparison_text``), None without a claim. Since the round-2 final review (I-2), every number the
    Research Note prints reaches the page too, in the README's sentences: ``fap`` the false-alarm sentence with its
    sweep-wide expectation (``zenodo.fap_sentence``); ``dilution`` the photometry statement, the aperture ratio
    against CROWDSAP with its shortfall and the predicted leak beside the measured dip (``pack.dilution_sentence``);
    ``trend`` the depth trend with its direction when it is printed (``pack.trend_sentence``); ``shape``,
    ``parity``, ``secondary`` and ``stability`` the vetting sentences (``pack.vetting_texts``); ``pixels`` the
    centroid sentence (``pack.pixel_sentence``); ``star`` the Gaia flags and the reddening (``pack.star_texts``);
    ``cross_check`` the TRICERATOPS sentence with its ranges (``crosscheck.comparison_sentence``); ``excluded`` the
    scenario that carries the pixel-excluded set's weight (``crosscheck.dominant_excluded_sentence``);
    ``own_evidence`` which of the two FPPs rests on SkySift's own evidence (``crosscheck.own_evidence_sentence``);
    ``leakage_basis`` the three-part basis of the leakage budget (``crosscheck.leakage_basis``); ``crowdsap`` the
    star provenance row of its crowding metric (``pack_rows.crowdsap_row``, Task 15 fix round 2, M7); ``tess`` the
    note's TESS sentence; and ``fold`` the figure's fold (``pack.fold_text``). Each None when it does not apply."""
    from skysift import pack, pack_rows, zenodo  # pack imports this module

    lead = cc.headline(data)
    block = data.get("cross_check") or {}
    in_house = zenodo.in_house_text(data)
    fap = data.get("fap") or {}
    return {"fpp_lead": f"{lead}." if lead else None,
            "fpp_value": cc.probability_text(block["fpp"]) if lead else None,
            "in_house": f"{in_house[:1].upper()}{in_house[1:]}.",
            "class_text": cc.class_label_text(block.get("class")) if cc.complete(block) else None,
            "prior": pack.prior_claim_sentence(data), "comparison": pack.comparison_text(data),
            "fap": zenodo.fap_sentence(data) if fap.get("calibrated") else None,
            "dilution": pack.dilution_sentence(data), "trend": pack.trend_sentence(data),
            **pack.vetting_texts(data), "pixels": pack.pixel_sentence(data) if _rung(data.get("tests") or [],
                                                                                   "rung5_pixels") else None,
            "star": pack.star_texts(data), "cross_check": cc.comparison_sentence(data),
            "excluded": cc.dominant_excluded_sentence(data), "own_evidence": cc.own_evidence_sentence(data),
            "leakage_basis": cc.leakage_basis(data), "crowdsap": pack_rows.crowdsap_row(data),
            "tess": pack.tess_text(data) if (data.get("tess") or {}).get("available") else None,
            "fold": pack.fold_text(data), "ephemeris": _ephemeris_words(data)}


def _ephemeris_words(data: dict) -> str | None:
    """"Ephemeris: <pack_text.ephemeris_sentence>; the star has G = 16.2." for the page (I-2), the G magnitude to the
    note's one decimal; None without an ephemeris."""
    from skysift.pack_text import ephemeris_sentence
    text = ephemeris_sentence(data)
    if not text:
        return None
    gmag = _num(data.get("target") or {}, "gmag")
    return f"Ephemeris: {text}" + (f"; the star has G = {gmag:.1f}" if gmag is not None else "") + "."


def export_html(data: dict, static_dir: Path = STATIC_DIR, data_dir: Path | None = None) -> str:
    """One standalone file: the dashboard's stylesheet (tokens and components) with the fonts as data URIs,
    ``dossier.css``, the JSON in ``<script id="dossier-data" type="application/json">`` (every ``/`` escaped, so no
    ``</`` and no URL appears in the markup) and ``dossier.js``, which draws the page and its own level toggle. Nothing
    is loaded from anywhere else. Prior claims are cut to their public fields (``public_claims``), and with
    ``data_dir`` every local path in the data is cut first (``public_paths``), as the pack does: the file is meant for
    someone outside the laptop. ``page_texts`` (the pack's sentences the page prints, spec 11.2) joins the JSON."""
    static_dir = Path(static_dir)
    texts = page_texts(data)
    data = public_claims(data)
    if data_dir is not None:
        data = public_paths(data, data_dir)
    data = {**data, "page_texts": texts}
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
    """Render the report's JSON to ``path`` (atomically), record it as a pinned ``dossier`` artifact and on the row.
    The row's cross-check replaces the JSON's copy (it may have been imported since), ``current`` recomputed."""
    path = Path(path)
    data = json.loads(report.data_path.read_text(encoding="utf-8"))
    data["cross_check"] = cc.with_current((store.dossier(report.target_id) or {}).get("cross_check"), data)
    text = export_html(data, static_dir, data_dir_of(report.data_path))
    _write_atomic(path, text)
    _record_artifact(store, EXPORT_URL.format(target_id=report.target_id), path, text, report.target_id)
    store.upsert_dossier(target_id=report.target_id, export_path=str(path))
    return path
