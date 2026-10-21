"""Plot payloads for the signal page: plain lists, strict JSON, computed from the engine's own detrend and fold so the
plots show exactly what the rungs measured. The light-curve loader lives in the engine (``skysift.lc.loader``, the
recheck uses it too) and is re-exported from here with its helpers, so the routes and tests keep their imports."""
from __future__ import annotations

import logging
import math

import numpy as np

from skysift.detectors.periodogram import PeriodogramSummary
from skysift.lc.loader import (  # noqa: F401  re-exported: the routes and tests import the loader from here
    IntegrityError,
    LightCurveLoader,
    _HoldBlob,
    _ProvenanceStore,
)
from skysift.lc.ops import GAP_EDGE_TRIGGER_DAYS, apply_mask, detrend, fold_phase
from skysift.lc.prewhiten import prewhiten_model
from skysift.models import LightCurve
from skysift.sources import PARSERS  # noqa: F401  the loader's parsers, re-exported (tests patch entries through here)
from skysift.vetting.rung1_detection import SYSTEMATIC_PERIODS

log = logging.getLogger(__name__)
MAX_POINTS = 20_000         # the full curve
PANEL_POINTS = 4_000        # each scatter in the fold, odd/even and secondary panels (bins use every point)
BIN_MINUTES = 15.0
ALIAS_FACTORS = (0.5, 2.0, 3.0)
# The figure template plots.js merges into every panel's layout (the signal page carries it in data-layout): transparent
# paper so the card shows through, hairline grid, the page's body face. Colours depend on the theme, so MARKS names
# the CSS custom properties plots.js reads for the data, the faint points, the model line and the grid.
LAYOUT = {"paper_bgcolor": "rgba(0,0,0,0)", "plot_bgcolor": "rgba(0,0,0,0)", "showlegend": False,
          "font": {"family": '"IBM Plex Sans", system-ui, sans-serif', "size": 12},
          "title": {"font": {"family": '"Space Grotesk", system-ui, sans-serif', "size": 13}, "x": 0, "xanchor": "left"},
          "margin": {"l": 56, "r": 12, "t": 32, "b": 44},
          "xaxis": {"gridwidth": 1, "zeroline": False, "linewidth": 1, "ticks": "outside", "ticklen": 4},
          "yaxis": {"gridwidth": 1, "zeroline": False, "linewidth": 1, "ticks": "outside", "ticklen": 4}}
MARKS = {"data": "--ion", "faint": "--fg-2", "model": "--solar", "grid": "--line", "text": "--fg-2"}   # spec 3, "Marks"
# one plain caption under each panel, keyed by the panel id plots.js gives it
CAPTIONS = {
    "plot-fold": ("Every transit stacked on top of each other, zoomed on the dip. The dip should be the same shape "
                  "each time."),
    "plot-full": ("The whole light curve with the star's slow changes removed. The ticks mark each predicted transit. "
                  "BKJD: days since the Kepler reference date."),
    "plot-oddeven": "The odd and the even transits measured apart. A planet makes them equally deep.",
    "plot-secondary": ("Half an orbit after the dip, on the same scale. A second dip here is the mark of two stars "
                       "eclipsing each other."),
    "plot-periodogram": ("How far each trial period stands above the others. The dotted line is the threshold and the "
                         "dashed lines mark aliases and spacecraft rhythms."),
    "plot-seasons": ("The depth of the dip in each quarter group (quarter mod 4, how the stability check groups "
                     "the quarters). A real transit is equally deep in all of them."),
}
# the detrend of every signal stored before signals carried their detrend settings (DETREND_VERSION 1)
LEGACY_DETREND = {"version": 1, "edge_mask_days": 0.0, "edge_gap_days": GAP_EDGE_TRIGGER_DAYS, "median_mode": "nearest"}


def _lst(a, nd: int | None = None) -> list:
    """Plain list for JSON: non-finite values (empty bins, missing errors) become None."""
    arr = np.asarray(a, float)
    if nd is not None: arr = np.round(arr, nd)
    return [v if math.isfinite(v) else None for v in arr.tolist()]


def _stride(n: int, cap: int) -> slice:
    return slice(None, None, max(1, math.ceil(n / cap)))


def _bin(x: np.ndarray, y: np.ndarray, hi: float, width: float):
    """Mean of y in fixed bins centred on multiples of ``width`` out to ``hi``; empty bins are NaN. Fixed edges keep the
    odd and even curves on one shared phase grid."""
    n = math.ceil(hi / width)
    edges = (np.arange(-n, n + 2) - 0.5) * width
    centres = 0.5 * (edges[:-1] + edges[1:])
    if x.size == 0: return centres, np.full(centres.size, np.nan)
    idx = np.clip(np.digitize(x, edges) - 1, 0, centres.size - 1)
    sums = np.bincount(idx, weights=y, minlength=centres.size); counts = np.bincount(idx, minlength=centres.size)
    with np.errstate(invalid="ignore", divide="ignore"):
        means = sums / counts
    return centres, means


def _full_selection(t: np.ndarray, period: float, t0: float, dur: float) -> np.ndarray:
    """Indices for the full-curve panel: every point within one duration of a predicted transit (so individual dips stay
    visible) and a stride over the rest, at most MAX_POINTS in all."""
    near = np.abs(fold_phase(t, period, t0)) <= dur
    keep = np.flatnonzero(near); rest = np.flatnonzero(~near)
    if keep.size > MAX_POINTS // 2: keep = keep[::math.ceil(keep.size / (MAX_POINTS // 2))]
    budget = MAX_POINTS - keep.size
    if rest.size > budget: rest = rest[::math.ceil(rest.size / budget)]
    return np.sort(np.concatenate([keep, rest]))


def _fold_hours(t, period, t0):
    return fold_phase(t, period, t0) * 24.0


def whitened_fnorm(lc: LightCurve, signal: dict) -> np.ndarray:
    """The detrended flux minus the pre-whitening model stored with the signal, so the plots show exactly what the
    detector and the rungs saw. The detrend replays the settings stored in ``extra["detrend"]``; a signal without
    them was stored before the record existed and was measured on the version-1 detrend (``LEGACY_DETREND``: no
    gap-edge mask, nearest padding). A signal without a stored model gets the detrend alone. A second-pass signal
    (``extra["mask"]``) was found and vetted with the first-pass signals masked, so the masked points are NaN here and
    absent from its fold and river plot."""
    extra = signal.get("extra") or {}
    d = extra.get("detrend") or LEGACY_DETREND
    fnorm = detrend(lc, edge_mask_days=float(d["edge_mask_days"]), edge_gap_days=float(d["edge_gap_days"]),
                    pad_mode=str(d["median_mode"]))
    comps = (extra.get("prewhitening") or {}).get("components") or []
    if comps:
        fnorm = fnorm - prewhiten_model(lc.time, comps)
    return apply_mask(lc.time, fnorm, extra.get("mask"))


def build_plots(lc: LightCurve, fnorm: np.ndarray, signal: dict, tests: list[dict],
                periodogram: PeriodogramSummary | None, thresholds) -> dict:
    period, t0, dur = float(signal["period"]), float(signal["t0"]), float(signal["duration"])
    good = np.isfinite(fnorm) & np.isfinite(lc.time)
    t, f = lc.time[good], fnorm[good]
    bin_h = BIN_MINUTES / 60.0

    def scatter(ph, flux, mask):
        """Points of one fold panel, capped by stride; the bins below are computed from every point."""
        sl = _stride(int(mask.sum()), PANEL_POINTS)
        return ph[mask][sl], flux[mask][sl]

    # full curve with the transit epochs that fall inside the observed span
    epochs = np.arange(math.ceil((t.min() - t0) / period), math.floor((t.max() - t0) / period) + 1) if t.size else np.array([])
    sel = _full_selection(t, period, t0, dur)
    full = {"time": _lst(t[sel], 5), "flux": _lst(f[sel], 7), "transit_times": _lst(t0 + epochs * period, 5)}
    # primary fold (dip at phase 0) with bins and the box model
    ph = _fold_hours(t, period, t0)
    window = min(max(6 * dur * 24.0, 6.0), 0.5 * period * 24.0)
    k = np.abs(ph) < window
    bc, bm = _bin(ph[k], f[k], window, bin_h)
    px, py = scatter(ph, f, k)
    fold = {"phase_h": _lst(px, 4), "flux": _lst(py, 7), "bin_phase_h": _lst(bc, 4), "bin_flux": _lst(bm, 7),
            "model_depth_ppm": float(signal["depth_ppm"]), "model_duration_h": dur * 24.0, "window_h": window}
    # odd and even transits by epoch parity
    ep = np.round((t - t0) / period).astype(int)
    odd, even = (ep % 2 == 1) & k, (ep % 2 == 0) & k
    oc, om = _bin(ph[odd], f[odd], window, bin_h); _, em = _bin(ph[even], f[even], window, bin_h)
    ox, oy = scatter(ph, f, odd); ex, ey = scatter(ph, f, even)
    oddeven = {"odd_phase_h": _lst(ox, 4), "odd_flux": _lst(oy, 7), "even_phase_h": _lst(ex, 4), "even_flux": _lst(ey, 7),
               "bin_phase_h": _lst(oc, 4), "odd_bin": _lst(om, 7), "even_bin": _lst(em, 7)}
    # phase 0.5 window at the same scale
    ph2 = _fold_hours(t, period, t0 + 0.5 * period); k2 = np.abs(ph2) < window
    sc, sm = _bin(ph2[k2], f[k2], window, bin_h)
    sx, sy = scatter(ph2, f, k2)
    secondary = {"phase_h": _lst(sx, 4), "flux": _lst(sy, 7), "bin_phase_h": _lst(sc, 4), "bin_flux": _lst(sm, 7),
                 "window_h": window}
    # per-season depths from rung 3
    seasons = None
    r3 = next((x for x in tests if x["rung"] == "rung3_stability" and (x.get("metrics") or {}).get("season_depths")), None)
    if r3:
        d = r3["metrics"]["season_depths"]; e = r3["metrics"].get("season_errs") or {}
        keys = sorted(d, key=int)
        depths = np.array([d[q] for q in keys], float); errs = np.array([e.get(q, np.nan) for q in keys], float)
        ok = np.isfinite(errs) & (errs > 0)
        w = np.where(ok, 1.0 / np.where(ok, errs, 1.0) ** 2, 0.0)
        mean = float(np.sum(w * depths) / np.sum(w)) if w.sum() > 0 else float(np.mean(depths))
        seasons = {"season": [int(q) for q in keys], "depth_ppm": _lst(depths), "err_ppm": _lst(errs), "mean_ppm": mean}
    # periodogram envelope
    pg = None
    if periodogram is not None:
        pg = {"period": _lst(periodogram.bin_periods()), "sde": _lst([np.nan if v is None else v for v in periodogram.envelope]),
              "peaks": periodogram.peaks, "sde_min": float(thresholds.sde_min), "sde_min_label": "current threshold",
              "detected_period": period,
              "aliases": [round(period * a, 6) for a in ALIAS_FACTORS], "systematic_periods": list(SYSTEMATIC_PERIODS),
              "band_edges": periodogram.band_edges}
    return {"periodogram": pg, "full": full, "fold": fold, "oddeven": oddeven, "secondary": secondary, "seasons": seasons}
