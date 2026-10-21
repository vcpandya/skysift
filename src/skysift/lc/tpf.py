"""Kepler long-cadence target pixel files (TPFs) from the MAST archive, for the pixel rung (mission control spec 4.4).

One directory per Kepler target, ``TPF_BASE``, listing one ``kplr<kic9>-<13 digits>_lpd-targ.fits.gz`` per quarter.
A file holds the primary header (KEPLERID, QUARTER, RA_OBJ, DEC_OBJ of the Kepler target), the TARGETTABLES binary
table (TIME in BKJD, QUALITY, the FLUX cube in e-/s) and the APERTURE image (bit 2 marks the optimal aperture the
pipeline summed into SAP_FLUX) whose header carries the stamp's celestial WCS. ``fetch_tpfs`` lists the directory and
fetches each file through ``net.fetch`` (service "mast", every request logged under the target), keeps it in the blob
cache as a ``kind="tpf"`` artifact and reads a kept file from the blob instead of fetching it again. The study this
ports read the same files the same way (``docs/candidates/kic-006146676/localisation/download_tpfs.py`` and
``diffimage.py``)."""
from __future__ import annotations

import gzip
import hashlib
import io
import logging
import math
import re
import warnings
from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np
from astropy.io import fits
from astropy.wcs import WCS, FITSFixedWarning

from skysift.net import ServiceUnavailable, looks_like_error_document

log = logging.getLogger(__name__)

TPF_BASE = "https://archive.stsci.edu/missions/kepler/target_pixel_files/{kic4}/{kic9}/"
OPTIMAL_APERTURE_BIT = 2        # APERTURE bitmask: 1 collected, 2 in the optimal (SAP) aperture
_GZIP_MAGIC = b"\x1f\x8b"


@dataclass
class TPF:
    quarter: int
    time: np.ndarray            # BKJD
    quality: np.ndarray
    cube: np.ndarray            # (n, rows, cols) FLUX, float64
    wcs: WCS | None
    name: str
    sha256: str                 # of the file as fetched (gzipped)
    aperture: np.ndarray = field(default_factory=lambda: np.zeros((0, 0), np.int32))   # (rows, cols) bitmask
    kepler_id: int | None = None
    ra_obj: float = math.nan    # the Kepler target's position from the primary header
    dec_obj: float = math.nan

    def pixel_of(self, ra: float, dec: float) -> tuple[float, float]:
        """Zero-based (col, row) of a sky position on this stamp's grid, pixel centres at integers; (NaN, NaN) when
        the WCS is missing or cannot place it, so a quarter with a broken header is unusable rather than fatal."""
        try:
            if self.wcs is None or not (math.isfinite(float(ra)) and math.isfinite(float(dec))):
                return math.nan, math.nan
            c, r = self.wcs.all_world2pix([float(ra)], [float(dec)], 0)
            c, r = float(np.asarray(c).ravel()[0]), float(np.asarray(r).ravel()[0])
        except Exception:               # a WCS that does not converge or a malformed header
            log.warning("TPF %s: the WCS could not place (%s, %s)", self.name, ra, dec, exc_info=True)
            return math.nan, math.nan
        return (c, r) if math.isfinite(c) and math.isfinite(r) else (math.nan, math.nan)

    def sap_flux(self) -> np.ndarray:
        """The flux summed over the optimal aperture per cadence (the Kepler target's SAP flux, without the
        pipeline's corrections); all NaN when the aperture is empty or does not fit the cube."""
        mask = (np.asarray(self.aperture).astype(np.int64) & OPTIMAL_APERTURE_BIT).astype(bool)
        if mask.shape != self.cube.shape[1:] or not mask.any():
            return np.full(self.cube.shape[0], np.nan)
        return self.cube[:, mask].sum(axis=1)


def _kic9(kic: int) -> str:
    return f"{int(kic):09d}"


def tpf_listing_url(kic: int) -> str:
    k = _kic9(kic)
    return TPF_BASE.format(kic4=k[:4], kic9=k)


def list_tpf_names(listing_html: str, kic: int) -> list[str]:
    """The long-cadence TPF names of ``kic`` linked from an archive directory listing, sorted, each once."""
    pattern = re.compile(rf'href="(?:[^"]*/)?(kplr{_kic9(kic)}-\d{{13}}_lpd-targ\.fits\.gz)"')
    return sorted(set(pattern.findall(listing_html)))


def _float(v) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return math.nan
    return x if math.isfinite(x) else math.nan


def parse_tpf(data: bytes, name: str = "") -> TPF:
    """Read a TPF, gzipped or plain FITS. The quarter comes from the primary header's QUARTER, the cube from the
    TARGETTABLES FLUX column, the WCS from the APERTURE header (None when it has no celestial WCS). Raises ValueError
    on a file that is not a TPF."""
    sha = hashlib.sha256(data).hexdigest()
    raw = gzip.decompress(data) if data[:2] == _GZIP_MAGIC else data
    if not raw.startswith(b"SIMPLE"):
        raise ValueError(f"{name or 'TPF'}: not a FITS file")
    with fits.open(io.BytesIO(raw), memmap=False) as h:
        prim = h[0].header
        if "QUARTER" not in prim:
            raise ValueError(f"{name or 'TPF'}: no QUARTER in the primary header")
        names = [x.name for x in h]
        tab = h["TARGETTABLES"] if "TARGETTABLES" in names else h[1]
        ap_hdu = h["APERTURE"] if "APERTURE" in names else h[2]
        time = np.asarray(tab.data["TIME"], float)
        quality = np.asarray(tab.data["QUALITY"]).astype(np.int64)
        cube = np.asarray(tab.data["FLUX"], float)
        aperture = np.asarray(ap_hdu.data).astype(np.int32)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", FITSFixedWarning)
            wcs = WCS(ap_hdu.header, naxis=2)
        if not wcs.has_celestial:
            wcs = None
        kid = prim.get("KEPLERID")
        tpf = TPF(quarter=int(prim["QUARTER"]), time=time, quality=quality, cube=cube, wcs=wcs, name=name,
                  sha256=sha, aperture=aperture, kepler_id=None if kid is None else int(kid),
                  ra_obj=_float(prim.get("RA_OBJ")), dec_obj=_float(prim.get("DEC_OBJ")))
    if cube.ndim != 3 or cube.shape[0] != time.size or quality.size != time.size:
        raise ValueError(f"{name or 'TPF'}: FLUX {cube.shape} does not match {time.size} cadences")
    return tpf


def _cached(store, blob, url: str, name: str) -> TPF | None:
    """The kept file of ``url`` read from the blob, or None when there is none or it no longer reads back intact."""
    find = getattr(store, "live_artifact", None)
    if blob is None or find is None:
        return None
    art = find(url)
    if art is None:
        return None
    try:
        data = blob.get(art["local_path"])
        if hashlib.sha256(data).hexdigest() != art["sha256"]:
            raise ValueError("checksum differs")
        tpf = parse_tpf(data, name)
    except Exception:                   # gone from the cache, truncated, changed: fetch again
        log.warning("kept pixel file %s unreadable; fetching it again", url, exc_info=True)
        return None
    store.touch_artifact(art["sha256"])
    return tpf


def fetch_tpfs(kic: int, *, fetch: Callable, blob, store, target_id: str, timeout: float = 600.0) -> list[TPF]:
    """Every long-cadence TPF of the Kepler target ``kic``, sorted by quarter: one listing request (service "mast"),
    then one request per file that the blob cache does not hold. A fetched file is stored through ``blob.put`` and
    recorded with ``store.add_artifact(..., target_id=target_id, kind="tpf")`` (unpinned: budget retention may evict
    it, and pinning the target's evidence pins it); a kept, undeleted file is read from the blob instead. A file that
    cannot be fetched or read is skipped. Returns [] when the listing names no file (the star has no pixel files);
    raises ServiceUnavailable when the listing fails or none of the listed files is reachable."""
    base = tpf_listing_url(kic)
    listing = fetch(base, service="mast", store=store, target_id=target_id, timeout=timeout)
    err = looks_like_error_document(listing)
    if err:
        raise ServiceUnavailable(f"mast {base}: {err}")
    names = list_tpf_names(listing.decode("utf-8", "replace"), kic)
    out, last = [], ""
    for name in names:
        url = base + name
        tpf = _cached(store, blob, url, name)
        if tpf is None:
            try:
                data = fetch(url, service="mast", store=store, target_id=target_id, timeout=timeout)
                tpf = parse_tpf(data, name)
            except ServiceUnavailable as e:
                last = str(e); log.warning("pixel file %s not reachable: %s", url, e)
                continue
            except Exception as e:      # noqa: BLE001  (an error page or a damaged file in place of a TPF)
                last = f"{name}: {e}"; log.warning("pixel file %s unreadable: %s", url, e)
                continue
            if blob is not None:
                sha, loc = blob.put(data, name)
                if store is not None and hasattr(store, "add_artifact"):
                    store.add_artifact(sha, len(data), url, loc, target_id=target_id, kind="tpf")
        out.append(tpf)
    if names and not out:
        raise ServiceUnavailable(f"mast: no pixel file of KIC {int(kic)} reachable ({len(names)} listed): {last}")
    return sorted(out, key=lambda t: (t.quarter, t.name))
