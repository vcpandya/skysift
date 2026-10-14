"""Plot payloads for the signal page: plain lists, strict JSON, computed from the engine's own detrend and fold so the
plots show exactly what the rungs measured. The loader serves the pinned raw file from the blob, or re-fetches it
through the adapter with a SHA-256 check when retention released it (parent spec, data flow step 5)."""
from __future__ import annotations

import logging
import math

import numpy as np
from sqlalchemy import select

from skysift.detectors.periodogram import PeriodogramSummary
from skysift.lc.kbonus import parse_kbonus_fits
from skysift.lc.ops import fold_phase
from skysift.models import LightCurve, Target
from skysift.sources.factory import make_source
from skysift.store.db import ArtifactRow, Store
from skysift.vetting.rung1_detection import SYSTEMATIC_PERIODS

log = logging.getLogger(__name__)
PARSERS = {"kbonus_bkg": parse_kbonus_fits}
MAX_POINTS = 20_000         # the full curve
PANEL_POINTS = 4_000        # each scatter in the fold, odd/even and secondary panels (bins use every point)
BIN_MINUTES = 15.0
ALIAS_FACTORS = (0.5, 2.0, 3.0)
TARGET_FIELDS = ("source", "target_id", "ra", "dec", "gaia_id", "kic", "gmag")


class LightCurveLoader:
    def __init__(self, store: Store, blob, source_factory=make_source, parsers: dict | None = None):
        self.store, self.blob, self.source_factory = store, blob, source_factory
        self.parsers = parsers if parsers is not None else PARSERS

    def _raw_rows(self, target_id: str) -> list[ArtifactRow]:
        with self.store.session() as s:
            rows = s.scalars(select(ArtifactRow).where(ArtifactRow.target_id == target_id, ArtifactRow.kind == "raw")
                             .order_by(ArtifactRow.created.desc())).all()
            s.expunge_all()
            return rows

    def load(self, target: dict) -> LightCurve:
        tid, source = target["target_id"], target.get("source", "kbonus_bkg")
        parse = self.parsers.get(source, parse_kbonus_fits)
        rows = self._raw_rows(tid)
        for row in rows:
            if row.deleted_at is None and self.blob.exists(row.local_path):
                lc = parse(self.blob.get(row.local_path)); lc.meta.setdefault("target_id", tid)
                return lc
        # released by retention (or never fetched): re-fetch through the adapter, which records the artifact row again
        src = self.source_factory(source, self.store, self.blob)
        tgt = (Target(**{k: target[k] for k in TARGET_FIELDS if target.get(k) is not None},
                      meta=target.get("meta") or {}) if "ra" in target else target)
        lc, sha, _url = src.fetch_lightcurve(tgt)
        known = {r.sha256 for r in rows}
        if known and sha not in known:
            self._discard(sha, tid)                      # the adapter stored it; never serve it from the blob
            raise ValueError(f"re-fetched file sha256 {sha[:12]} differs from the recorded {min(known)[:12]}; "
                             "the archive file changed since the sweep")
        self.store.pin_target_artifacts(tid)              # evidence the operator is looking at stays
        lc.meta.setdefault("target_id", tid)
        return lc

    def _discard(self, sha: str, target_id: str) -> None:
        """Forget a re-fetched file whose hash does not match the evidence: delete the blob and the row the adapter just
        recorded, so the next load repeats the check instead of serving (or trusting) the mismatching file."""
        try:
            with self.store.session() as s:
                row = s.get(ArtifactRow, sha)
                if row is None or row.target_id != target_id: return
                path = row.local_path
                s.delete(row)
            self.blob.delete(path)
        except Exception:                                  # best effort; the integrity error is what the caller needs
            log.warning("could not discard the mismatching file %s", sha[:12], exc_info=True)


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


def _fold_hours(t, period, t0):
    return fold_phase(t, period, t0) * 24.0


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
    sl = _stride(t.size, MAX_POINTS)
    full = {"time": _lst(t[sl], 5), "flux": _lst(f[sl], 7), "transit_times": _lst(t0 + epochs * period, 5)}
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
              "peaks": periodogram.peaks, "sde_min": float(thresholds.sde_min), "detected_period": period,
              "aliases": [round(period * a, 6) for a in ALIAS_FACTORS], "systematic_periods": list(SYSTEMATIC_PERIODS),
              "band_edges": periodogram.band_edges}
    return {"periodogram": pg, "full": full, "fold": fold, "oddeven": oddeven, "secondary": secondary, "seasons": seasons}
