"""Synthetic Kepler long-cadence target pixel files for the TPF reader, rung 5 and the pixel recheck.

The scene is the one of ``tests/unit/test_pixels.make_cube``: a target and a five times brighter neighbour 2.9 px
away (Gaussian PSFs, photon noise, quarters of 93 d from BKJD 131.5) with a 3 h box transit every 10 d on one of
them. Each quarter is written as a gzipped FITS file laid out like the archive's: a primary header with KEPLERID,
QUARTER, SEASON, RA_OBJ and DEC_OBJ of the Kepler target (here the neighbour, as for a KBonus star extracted from a
neighbour's pixels), the TARGETTABLES binary table (TIME, QUALITY, FLUX cube) and the APERTURE image (bit 1 collected,
bit 2 the Kepler target's optimal aperture, a 3x3 box on it) with a TAN WCS whose reference pixel is the target. The
spacecraft rolls 90 degrees between seasons, so the neighbour's pixel position turns about the target from season to
season; within a season it stays put. The stars' pixel positions come from each quarter's WCS, exactly as the reader
computes them."""
from __future__ import annotations

import gzip
import io
import math
from functools import lru_cache

import numpy as np
from astropy.io import fits
from astropy.wcs import WCS

from tests.unit.test_pixels import DEPTH, POS_NEIGHBOUR, POS_TARGET, QUARTER_DAYS, RATIO, SHAPE, _render, _scene

TARGET_RADEC = (291.03, 41.52)
TARGET_KIC = 6146676                # the KBonus star the light curve belongs to
NEIGHBOUR_KIC = 6146666             # the Kepler target whose pixel files hold it
PIX_DEG = 3.98 / 3600.0


class TpfBytes(bytes):
    """A gzipped TPF with the truth it was built from: quarter, (col, row) pixel positions and sky positions."""
    quarter: int
    pos_target: tuple[float, float]
    pos_neighbour: tuple[float, float]
    target_radec: tuple[float, float]
    neighbour_radec: tuple[float, float]


def _wcs(radec, crpix_colrow, season: int) -> WCS:
    w = WCS(naxis=2)
    w.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    w.wcs.crval = [float(radec[0]), float(radec[1])]
    w.wcs.crpix = [float(crpix_colrow[0]) + 1.0, float(crpix_colrow[1]) + 1.0]      # FITS pixels are one-based
    w.wcs.cdelt = [-PIX_DEG, PIX_DEG]
    a = math.radians(90.0 * ((season - 1) % 4))     # season 1 (Q1) has make_cube's orientation
    w.wcs.pc = [[math.cos(a), -math.sin(a)], [math.sin(a), math.cos(a)]]
    return w


def _pix(w: WCS, radec) -> tuple[float, float]:
    c, r = w.all_world2pix([radec[0]], [radec[1]], 0)
    return float(c[0]), float(r[0])


def neighbour_radec(target_radec=TARGET_RADEC) -> tuple[float, float]:
    """The sky position at make_cube's neighbour pixel in the season-1 orientation."""
    w = _wcs(target_radec, POS_TARGET, 1)
    ra, dec = w.all_pix2world([POS_NEIGHBOUR[0]], [POS_NEIGHBOUR[1]], 0)
    return float(ra[0]), float(dec[0])


def tpf_fits(quarter: int, time, quality, cube, wcs: WCS, aperture, kic: int, obj_radec, gz: bool = True) -> bytes:
    """One TPF as bytes, the archive's layout (gzipped unless ``gz`` is False)."""
    nrow, ncol = cube.shape[1:]
    prim = fits.PrimaryHDU()
    for k, v in (("KEPLERID", int(kic)), ("QUARTER", int(quarter)), ("SEASON", int(quarter) % 4),
                 ("RA_OBJ", float(obj_radec[0])), ("DEC_OBJ", float(obj_radec[1]))):
        prim.header[k] = v
    table = fits.BinTableHDU.from_columns([
        fits.Column(name="TIME", format="D", array=np.asarray(time, float)),
        fits.Column(name="QUALITY", format="J", array=np.asarray(quality, np.int32)),
        fits.Column(name="FLUX", format=f"{nrow * ncol}E", dim=f"({ncol},{nrow})",
                    array=np.asarray(cube, np.float32))], name="TARGETTABLES")
    ap = fits.ImageHDU(data=np.asarray(aperture, np.int32), header=wcs.to_header(), name="APERTURE")
    buf = io.BytesIO()
    fits.HDUList([prim, table, ap]).writeto(buf)
    data = buf.getvalue()
    return gzip.compress(data, mtime=0) if gz else data


def _aperture(shape, pos) -> np.ndarray:
    ap = np.ones(shape, np.int32)
    c, r = round(pos[0]), round(pos[1])
    ap[max(r - 1, 0):r + 2, max(c - 1, 0):c + 2] |= 2
    return ap


@lru_cache(maxsize=16)
def tpf_quarters(dip_on: str = "target", target_radec: tuple[float, float] = TARGET_RADEC, n_quarters: int = 5,
                 seed: int = 0, stamp: tuple | None = None, kic: int = NEIGHBOUR_KIC) -> tuple[TpfBytes, ...]:
    """One gzipped TPF per quarter 1 to ``n_quarters`` (quarters 1 and 5 share season 1). ``stamp`` is
    ((rows, cols), (col, row) origin of the stamp on the default 9 x 9 grid) to cut the stamp down; the default is
    the 9 x 9 grid with the target on pixel (4, 4)."""
    rng = np.random.default_rng(seed)
    time, quarter, f_t, f_n = _scene(QUARTER_DAYS * n_quarters, dip_on, DEPTH, RATIO, n_quarters)
    nb = neighbour_radec(target_radec)
    shape, (oc, orow) = stamp or (SHAPE, (0, 0))
    out = []
    for q in range(1, n_quarters + 1):
        m = quarter == q
        w = _wcs(target_radec, (POS_TARGET[0] - oc, POS_TARGET[1] - orow), q % 4)
        pt, pn = _pix(w, target_radec), _pix(w, nb)
        cube = _render(rng, f_t[m], f_n[m], pt, pn, tuple(shape))
        b = TpfBytes(tpf_fits(q, time[m], np.zeros(int(m.sum()), int), cube, w, _aperture(tuple(shape), pn), kic,
                              nb))
        b.quarter, b.pos_target, b.pos_neighbour = q, pt, pn
        b.target_radec, b.neighbour_radec = tuple(target_radec), nb
        out.append(b)
    return tuple(out)


def tpf_name(kic: int, quarter: int) -> str:
    return f"kplr{kic:09d}-{2009131105131 + quarter:013d}_lpd-targ.fits.gz"
