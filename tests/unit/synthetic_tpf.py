"""Synthetic Kepler long-cadence target pixel files for the TPF reader, rung 5 and the pixel recheck.

The scene is the one of ``tests/unit/test_pixels.make_cube``: a target and a five times brighter neighbour 2.9 px
away (Gaussian PSFs, photon noise, quarters of 93 d from BKJD 131.5) with a 3 h box transit every 10 d on one of
them. Each quarter is written as a gzipped FITS file laid out like the archive's: a primary header with KEPLERID,
QUARTER, SEASON, RA_OBJ and DEC_OBJ of the Kepler target (here the neighbour, as for a KBonus star extracted from a
neighbour's pixels), the TARGETTABLES binary table (TIME, QUALITY, FLUX cube) and the APERTURE image (bit 1 collected,
bit 2 the Kepler target's optimal aperture, a 3x3 box on it) with a TAN WCS whose reference pixel is the target. The
spacecraft rolls 90 degrees between seasons, so the neighbour's pixel position turns about the target from season to
season; within a season it stays put. SEASON is the archive's: ``(quarter + 2) % 4``, and 3 for Q0, which shares Q1's
module and roll. The stars' pixel positions come from each quarter's WCS, exactly as the reader computes them.
``FakeMast`` stands in for the archive."""
from __future__ import annotations

import gzip
import io
import math
from functools import lru_cache

import numpy as np
from astropy.io import fits
from astropy.wcs import WCS

from skysift.net import ServiceUnavailable
from tests.unit.test_pixels import (
    DEPTH,
    EPH,
    F_TARGET,
    POS_NEIGHBOUR,
    POS_TARGET,
    QUARTER_DAYS,
    RATIO,
    SHAPE,
    _in_transit,
    _render,
    _scene,
)

TARGET_RADEC = (291.03, 41.52)
TARGET_KIC = 6146676                # the KBonus star the light curve belongs to
NEIGHBOUR_KIC = 6146666             # the Kepler target whose pixel files hold it
PIX_DEG = 3.98 / 3600.0
Q0_DAYS = (109.0, 131.5)            # BKJD span of the synthetic Q0: two transits (117, 127) with baseline on both sides
CADENCE = 29.4 / 1440


def archive_season(quarter: int) -> int:
    """The Kepler archive SEASON of a quarter: Q0 and Q1 share season 3, then (quarter + 2) % 4."""
    return 3 if quarter == 0 else (quarter + 2) % 4


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
    a = math.radians(90.0 * ((season - 3) % 4))     # archive season 3 (Q0, Q1, Q5) has make_cube's orientation
    w.wcs.pc = [[math.cos(a), -math.sin(a)], [math.sin(a), math.cos(a)]]
    return w


def _pix(w: WCS, radec) -> tuple[float, float]:
    c, r = w.all_world2pix([radec[0]], [radec[1]], 0)
    return float(c[0]), float(r[0])


def neighbour_radec(target_radec=TARGET_RADEC) -> tuple[float, float]:
    """The sky position at make_cube's neighbour pixel in Q1's orientation (archive season 3)."""
    w = _wcs(target_radec, POS_TARGET, 3)
    ra, dec = w.all_pix2world([POS_NEIGHBOUR[0]], [POS_NEIGHBOUR[1]], 0)
    return float(ra[0]), float(dec[0])


def tpf_fits(quarter: int, time, quality, cube, wcs: WCS, aperture, kic: int, obj_radec, gz: bool = True,
             season: int | None | str = "archive") -> bytes:
    """One TPF as bytes, the archive's layout (gzipped unless ``gz`` is False). ``season`` is the header SEASON:
    the archive's by default, None to leave the keyword out."""
    nrow, ncol = cube.shape[1:]
    prim = fits.PrimaryHDU()
    season = archive_season(quarter) if season == "archive" else season
    for k, v in (("KEPLERID", int(kic)), ("QUARTER", int(quarter)), ("SEASON", season),
                 ("RA_OBJ", float(obj_radec[0])), ("DEC_OBJ", float(obj_radec[1]))):
        if v is not None:
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


def _q0_scene(dip_on: str):
    """Q0's times and the two stars' fluxes: BKJD 109 to 131.5, the scene's transits at 117 and 127."""
    time = np.arange(Q0_DAYS[0], Q0_DAYS[1], CADENCE)
    dip = np.where(_in_transit(time, EPH), DEPTH, 0.0)
    f_t = F_TARGET * (1.0 - (dip if dip_on == "target" else 0.0 * dip))
    f_n = RATIO * F_TARGET * (1.0 - (dip if dip_on == "neighbour" else 0.0 * dip))
    return time, f_t, f_n


@lru_cache(maxsize=16)
def tpf_quarters(dip_on: str = "target", target_radec: tuple[float, float] = TARGET_RADEC, n_quarters: int = 5,
                 seed: int = 0, stamp: tuple | None = None, kic: int = NEIGHBOUR_KIC, with_q0: bool = False,
                 season: str | None = "archive") -> tuple[TpfBytes, ...]:
    """One gzipped TPF per quarter 1 to ``n_quarters`` (quarters 1 and 5 share archive season 3), and a Q0 at Q1's
    roll first when ``with_q0`` (two transits). ``stamp`` is ((rows, cols), (col, row) origin of the stamp on the
    default 9 x 9 grid) to cut the stamp down; the default is the 9 x 9 grid with the target on pixel (4, 4).
    ``season=None`` writes no SEASON keyword."""
    rng = np.random.default_rng(seed)
    time, quarter, f_t, f_n = _scene(QUARTER_DAYS * n_quarters, dip_on, DEPTH, RATIO, n_quarters)
    nb = neighbour_radec(target_radec)
    shape, (oc, orow) = stamp or (SHAPE, (0, 0))
    out = []

    def add(q, t, ft, fn, r):
        w = _wcs(target_radec, (POS_TARGET[0] - oc, POS_TARGET[1] - orow), archive_season(q))
        pt, pn = _pix(w, target_radec), _pix(w, nb)
        cube = _render(r, ft, fn, pt, pn, tuple(shape))
        b = TpfBytes(tpf_fits(q, t, np.zeros(t.size, int), cube, w, _aperture(tuple(shape), pn), kic, nb,
                              season=season))
        b.quarter, b.pos_target, b.pos_neighbour = q, pt, pn
        b.target_radec, b.neighbour_radec = tuple(target_radec), nb
        out.append(b)
    if with_q0:
        add(0, *_q0_scene(dip_on), np.random.default_rng(seed + 1000))     # own stream: Q1 to Q5 stay as they are
    for q in range(1, n_quarters + 1):
        m = quarter == q
        add(q, time[m], f_t[m], f_n[m], rng)
    return tuple(out)


def tpf_name(kic: int, quarter: int) -> str:
    return f"kplr{kic:09d}-{2009131105131 + quarter:013d}_lpd-targ.fits.gz"


def listing_html(kic: int, names) -> bytes:
    """An archive directory listing linking ``names`` (relative links, as MAST serves them)."""
    rows = "".join(f'<li><a href="{n}"> {n}</a></li>' for n in names)
    return (f'<html><body><h1>Index of /missions/kepler/target_pixel_files/{kic:09d}</h1><ul>'
            f'<li><a href="/missions/kepler/target_pixel_files/"> Parent Directory</a></li>{rows}</ul></body></html>'
            ).encode()


class FakeMast:
    """The archive: a directory URL answers its listing (registered under the key "listing", or under the URL
    itself), a file URL the bytes registered under its name; a name in ``fail`` answers HTTP 503 and anything
    unknown HTTP 404, as ``net.fetch`` raises them. Every request is counted and logged like ``net.fetch`` logs it.
    Other services get ``other`` (the catalogues' fake), when given."""

    def __init__(self, files: dict, fail=(), other=None):
        self.files, self.fail, self.other = files, set(fail), other
        self.calls, self.urls = 0, []

    def __call__(self, url, *, params=None, service, store=None, target_id=None, timeout=60.0, **kw):
        if service != "mast" and self.other is not None:
            return self.other(url, params=params, service=service, store=store, target_id=target_id, **kw)
        self.calls += 1; self.urls.append(url)
        assert service == "mast"
        name = url.rsplit("/", 1)[-1]
        if url.endswith("/"):
            data = self.files.get(url, self.files.get("listing"))
        else:
            data = None if name in self.fail else next(
                (v for k, v in self.files.items() if k != "listing" and url.endswith(k)), None)
        code = 200 if data is not None else 503 if name in self.fail else 404
        if store is not None:
            store.log_query(service, url, params or {}, code, len(data or b""), target_id=target_id)
        if data is None:
            raise ServiceUnavailable(f"{service} {url} -> HTTP {code}")
        return data
