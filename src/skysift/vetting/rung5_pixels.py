"""Rung 5, the pixel rung (mission control spec 4.4): is the dip on the target or on the neighbouring star whose
Kepler target pixel files hold it?

The pixel files are those of the Kepler target whose stamp holds the target: ``tpf_org`` for a KBonus star extracted
from a neighbour's pixels (the target catalogue's meta, else the light curve's TPFORG header), the target's own KIC
otherwise. That Kepler target is the neighbour the dip is tested against; a target that is itself the Kepler target
has no neighbour to test against. Per pixel file the two stars are placed on the stamp from its own WCS (the target
from its catalogue RA/Dec, the neighbour from ``tpf_org_ra``/``tpf_org_dec`` in the target's meta when present, else
the file's RA_OBJ/DEC_OBJ), and ``localise_quarters`` stacks the quarters per season on union frames, localizes the
lost light and gives the verdict. The leak test measures the dip at the ephemeris in the neighbour's own flux, summed
over the optimal aperture of its pixel files (per-quarter detrend). The season arrays the dossier draws are stored once
as a ``pixels.npz`` artifact (``PIXELS_URL``), pinned with the signal. Seasons are the Kepler archive SEASON (0 to 3,
from each file's header; Q0 shares Q1's). A listed pixel file the archive did not serve makes the rung an outage, so
the next recheck fetches the missing files and localizes on the full set."""
from __future__ import annotations

import hashlib
import io
import logging
import math
import zipfile
from collections import Counter
from collections.abc import Callable

import numpy as np

from skysift.lc.pixels import Ephemeris, Localisation, QuarterData, leak_depth, localise_quarters
from skysift.lc.tpf import TPF, fetch_tpfs
from skysift.models import Signal, TestResult, TestStatus
from skysift.net import ServiceUnavailable
from skysift.store.db import PIXELS_URL, Store
from skysift.vetting.base import DataBundle

log = logging.getLogger(__name__)

NO_FILES = "pixels: no pixel files for this star"
NO_NEIGHBOUR = "pixels: no neighbour to test against"
_ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)      # fixed member times: the same arrays give the same file and sha256


def _num(x) -> float | None:
    """A Python float for JSON, None for NaN and inf."""
    x = float(x)
    return x if math.isfinite(x) else None


def _npz_bytes(arrays: dict[str, np.ndarray]) -> bytes:
    """``np.savez_compressed`` with fixed member times, so the file is a pure function of the arrays."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for key, a in arrays.items():
            info = zipfile.ZipInfo(f"{key}.npy", date_time=_ZIP_EPOCH)
            info.compress_type = zipfile.ZIP_DEFLATED
            with z.open(info, "w", force_zip64=True) as f:
                np.lib.format.write_array(f, np.asanyarray(a), allow_pickle=False)
    return buf.getvalue()


def season_arrays(loc: Localisation) -> dict[str, np.ndarray]:
    """The arrays of ``pixels.npz``: ``seasons`` (int array of Kepler archive SEASON numbers, 0 to 3, as in the study's
    figure) and per season ``s`` (each on its own union frame, so never stacked) ``diff_<s>``, ``var_<s>``,
    ``direct_<s>``, ``valid_<s>``, and ``pos_target_<s>``, ``pos_neighbour_<s>`` as season-frame (col, row)."""
    out: dict[str, np.ndarray] = {"seasons": np.array([int(im.season) for im in loc.images], dtype=np.int64)}
    for im in loc.images:
        s = int(im.season)
        out[f"diff_{s}"] = np.asarray(im.diff, float)
        out[f"var_{s}"] = np.asarray(im.var, float)
        out[f"direct_{s}"] = np.asarray(im.direct, float)
        out[f"valid_{s}"] = np.asarray(im.valid, bool)
        out[f"pos_target_{s}"] = np.asarray(im.pos_target, float)
        out[f"pos_neighbour_{s}"] = np.asarray(im.pos_neighbour, float)
    return out


def _leak(tpfs: list[TPF], eph: Ephemeris) -> tuple[float | None, float | None]:
    """The dip at the ephemeris in the neighbour's optimal-aperture flux, every file's quarter detrended apart."""
    rows = [(t.time, t.sap_flux(), t.quality, np.full(t.time.size, t.quarter)) for t in tpfs]
    rows = [r for r in rows if np.isfinite(r[1]).any()]
    if not rows:
        return None, None
    time, flux, quality, quarter = (np.concatenate(x) for x in zip(*rows, strict=True))
    d, e = leak_depth(time, flux, quality, eph, quarter=quarter)
    return _num(d), _num(e)


def _fail_reason(loc: Localisation) -> str:
    """"the dip is on a neighbouring star (<k> sigma, <n>/<m> seasons)", the numbers of the fail verdict
    (``Localisation.neighbour_votes``)."""
    k, n, m = loc.neighbour_votes()
    return f"the dip is on a neighbouring star ({k:.1f} sigma, {n}/{m} seasons)"


class Rung5Pixels:
    name = "rung5_pixels"
    cost = 1000.0

    def __init__(self, tpf_loader: Callable = fetch_tpfs, rng_seed: int = 20261020, *, blob=None,
                 signal_id: int | None = None):
        """``tpf_loader(kic, *, fetch, blob, store, target_id)`` returns the Kepler target's pixel files
        (``fetch_tpfs``). ``blob`` keeps the fetched files and ``pixels.npz``; ``signal_id`` names the stored signal
        the arrays belong to (the recheck passes both; without them nothing is stored)."""
        self.tpf_loader = tpf_loader or fetch_tpfs
        self.rng_seed = rng_seed
        self.blob = blob
        self.signal_id = signal_id

    def _undetermined(self, reason: str, metrics: dict | None = None) -> TestResult:
        return TestResult(rung=self.name, status=TestStatus.UNDETERMINED, reason=reason, metrics=metrics or {})

    def _store_arrays(self, loc: Localisation, b: DataBundle) -> str | None:
        """Write ``pixels.npz`` through the blob and ``b.store`` (a real Store: the recheck runs in-process) as a
        pinned ``kind="pixels"`` artifact at ``PIXELS_URL``; returns its sha256, None when it was not stored. An
        earlier file of the signal with other arrays (a rerun on more quarters) is released first: its blob file
        deleted and its row marked deleted, so one pinned file per signal remains."""
        if not isinstance(b.store, Store) or self.blob is None or self.signal_id is None:
            log.warning("rung5 for %s: pixels.npz not stored (needs the main process's store, a blob and the "
                        "signal id; got store %s)", b.target.target_id, type(b.store).__name__)
            return None
        data = _npz_bytes(season_arrays(loc))
        url = PIXELS_URL.format(signal_id=self.signal_id)
        new = hashlib.sha256(data).hexdigest()
        while (old := b.store.live_artifact(url)) is not None and old["sha256"] != new:
            try:
                self.blob.delete(old["local_path"])
            except OSError:
                log.warning("could not delete the replaced %s at %s", url, old["local_path"], exc_info=True)
            b.store.mark_artifact_deleted(old["sha256"])
        sha, loc_path = self.blob.put(data, "pixels.npz")
        b.store.add_artifact(sha, len(data), url, loc_path, target_id=b.target.target_id, pinned=True, kind="pixels")
        return sha

    def run(self, s: Signal, b: DataBundle) -> TestResult:
        t = b.target
        kic = t.meta.get("tpf_org") or b.lc.meta.get("tpf_org") or t.kic
        if not kic:
            return self._undetermined(NO_FILES)
        kic = int(kic)
        if t.kic is not None and kic == int(t.kic):
            return self._undetermined(NO_NEIGHBOUR)
        try:
            got = self.tpf_loader(kic, fetch=b.fetch, blob=self.blob, store=b.store, target_id=t.target_id)
        except ServiceUnavailable as e:
            return self._undetermined(f"outage:mast: {e}", {"neighbour_kic": kic})
        tpfs, missing = list(got), list(getattr(got, "missing", []))
        files = {"neighbour_kic": kic, "tpf_manifest": [x.name for x in tpfs], "n_tpf": len(tpfs),
                 "tpf_missing": missing}
        unreachable = sum(1 for m in missing if m.get("cause") == "unreachable")
        if unreachable:          # a partial set must not settle the signal: the next recheck fetches the rest
            return self._undetermined(
                f"outage:mast: {unreachable} of {len(tpfs) + len(missing)} pixel files unreachable", files)
        if not tpfs:
            return self._undetermined(f"pixels: no readable pixel files ({len(missing)} listed)" if missing
                                      else NO_FILES, files)
        n_ra, n_dec = t.meta.get("tpf_org_ra"), t.meta.get("tpf_org_dec")
        from_catalogue = n_ra is not None and n_dec is not None
        quarters = []
        for tpf in tpfs:
            ra, dec = (n_ra, n_dec) if from_catalogue else (tpf.ra_obj, tpf.dec_obj)
            quarters.append(QuarterData(quarter=int(tpf.quarter), cube=tpf.cube, time=tpf.time, quality=tpf.quality,
                                        pos_target=tpf.pixel_of(t.ra, t.dec), pos_neighbour=tpf.pixel_of(ra, dec),
                                        season=tpf.season))
        eph = Ephemeris(float(s.period), float(s.t0), float(s.duration))
        loc = localise_quarters(quarters, eph, rng=np.random.default_rng(self.rng_seed))
        leak = _leak(tpfs, eph)
        silenced = Counter(x.non_voting_reason for x in loc.seasons if x.non_voting_reason is not None)
        metrics = {**loc.metrics(), "leak_depth_ppm": list(leak), **files,
                   "neighbour_position": "catalogue" if from_catalogue else "tpf header",
                   "n_seasons": len(loc.seasons), "n_seasons_voting": sum(1 for x in loc.seasons if x.voting),
                   "seasons_silenced": dict(sorted(silenced.items()))}
        metrics["pixels_sha256"] = self._store_arrays(loc, b) if loc.images else None
        if loc.verdict == "pass":
            return TestResult(rung=self.name, status=TestStatus.PASS, metrics=metrics,
                              reason=loc.reason.removeprefix("pixels: "))
        if loc.verdict == "fail":
            return TestResult(rung=self.name, status=TestStatus.FAIL, metrics=metrics, reason=_fail_reason(loc))
        return self._undetermined(loc.reason, metrics)
