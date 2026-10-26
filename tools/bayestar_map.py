"""Read the Bayestar19 3D dust map (Green et al. 2019) from its HDF5 file without ``dustmaps`` or ``healpy`` (spec 12.5).

``healpy`` has no Windows wheel, and ``dustmaps.bayestar`` imports it at module level, so neither installs on the build
machine. This module does what ``dustmaps.bayestar.BayestarQuery`` does for a handful of stars, rule by rule (checked
against ``dustmaps/bayestar.py`` on GitHub master, read as text on 26 October 2026):

- The layout: ``/pixel_info`` is a structured dataset sorted by (nside, healpix_index), with the fields ``nside``,
  ``healpix_index`` (nested ordering), ``converged``, ``DM_reliable_min`` and ``DM_reliable_max``. Its attribute
  ``DM_bin_edges`` holds the distance moduli. ``/samples`` has shape (pixels, samples, distances): for bayestar2019
  that is (4,214,070, 5, 120). There is no ``/GRDiagnostic`` in bayestar2019, and dustmaps never reads one. The
  convergence flag is the ``converged`` field.
- The pixel: the sightline's nested HEALPix index at each nside present (``astropy_healpix`` in place of
  ``healpy.ang2pix``, on Galactic l and b). dustmaps lets a finer level overwrite a coarser one, so the search here
  runs from the largest nside down and stops at the first match.
- The distance: dm = 5 log10(d / 10 pc). Between two moduli the reddening is linear in dm. Below the first modulus it
  is the first bin times 10^(0.2 (dm - dm_0)), the ratio of the distances. Beyond the last modulus it is the last bin.
  The percentiles are ``np.nanpercentile`` over the samples, as the ``percentile`` mode gives them. The distance is
  reliable when DM_reliable_min <= dm <= DM_reliable_max, both finite.
- The units: E(B-V) = 0.884 times the map's value (Green et al. 2019).

numpy and astropy load at import, ``astropy_healpix`` inside ``pixel_index``, and ``h5py`` inside ``main``, so the
engine's interpreter imports the pure functions. Reading the file needs the third venv:

    uv venv C:/Research/skysift/.venv-dustmaps --python 3.12
    uv pip install --native-tls -p C:/Research/skysift/.venv-dustmaps/Scripts/python.exe h5py astropy \\
        astropy-healpix numpy
    C:/Research/skysift/.venv-dustmaps/Scripts/python.exe -I tools/bayestar_map.py --map <bayestar2019.h5> \\
        --ra <deg> --dec <deg> --distance <pc> [<pc> ...]

``main`` prints the queries as a JSON list on stdout, which ``tools/extinction.py --map`` reads when the engine's
interpreter cannot open the file itself. The map is doi:10.7910/DVN/2EJ9TX on Harvard Dataverse, 727,902,836 bytes,
MD5 ab815d2fd3068d1b81a1bd61fb18a722.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np

MAP_VERSION = "bayestar2019"
MAP_DOI = "10.7910/DVN/2EJ9TX"
MAP_MD5 = "ab815d2fd3068d1b81a1bd61fb18a722"      # the Dataverse record's
BAYESTAR_TO_EBV = 0.884                            # E(B-V) = 0.884 x the map's units (Green et al. 2019)
PERCENTILES = (16.0, 50.0, 84.0)
PIXEL_FIELDS = ("nside", "healpix_index", "converged", "DM_reliable_min", "DM_reliable_max")


class MapLayoutError(ValueError):
    """The file lacks a dataset, a field of ``/pixel_info`` or the ``DM_bin_edges`` attribute that the reader needs."""


def galactic(ra_deg: float, dec_deg: float) -> tuple[float, float]:
    """(l, b) in degrees of an ICRS position, through astropy."""
    import astropy.units as u
    from astropy.coordinates import SkyCoord
    g = SkyCoord(ra=ra_deg * u.deg, dec=dec_deg * u.deg, frame="icrs").galactic
    return float(g.l.deg), float(g.b.deg)


def pixel_index(l_deg: float, b_deg: float, pixel_info) -> int | None:
    """The row of ``pixel_info`` (anything indexable by "nside" and "healpix_index") whose pixel holds (l, b): each
    nside present is tried from the largest down, with its nested HEALPix index from ``astropy_healpix``; None when
    no row holds the sightline (outside the map's footprint)."""
    import astropy.units as u
    from astropy_healpix import HEALPix
    nside = np.asarray(pixel_info["nside"])
    index = np.asarray(pixel_info["healpix_index"])
    for level in sorted({int(n) for n in np.unique(nside)}, reverse=True):
        ipix = int(HEALPix(nside=level, order="nested").lonlat_to_healpix(l_deg * u.deg, b_deg * u.deg))
        hits = np.flatnonzero((nside == level) & (index == ipix))
        if hits.size:
            return int(hits[0])
    return None


def distance_modulus(distance_pc: float) -> float:
    """5 log10(d / 10 pc), the same as dustmaps' 5 (log10(d_kpc) + 2)."""
    return 5.0 * math.log10(distance_pc) - 5.0


def reddening_at(samples_row, dm_grid, distance_pc: float) -> np.ndarray:
    """One cumulative reddening per sample (the map's units) at ``distance_pc``, from one pixel's samples (samples x
    distances), by dustmaps' rules: linear in distance modulus between the grid's moduli, the first bin times
    10^(0.2 (dm - dm_0)) below the first, the last bin beyond the last."""
    samples, grid = np.asarray(samples_row, dtype=float), np.asarray(dm_grid, dtype=float)
    dm = distance_modulus(distance_pc)
    ceil = int(np.searchsorted(grid, dm))
    if ceil == 0:
        return 10.0 ** (0.2 * (dm - grid[0])) * samples[:, 0]
    if ceil == grid.size:
        return samples[:, -1].copy()
    a = (grid[ceil] - dm) / (grid[ceil] - grid[ceil - 1])
    return (1.0 - a) * samples[:, ceil] + a * samples[:, ceil - 1]


def percentiles(values) -> dict | None:
    """{"median", "p16", "p84"} over the finite values (``np.nanpercentile``, as dustmaps' percentile mode), or None
    when none is finite."""
    v = np.asarray(values, dtype=float)
    if not np.isfinite(v).any():
        return None
    p16, p50, p84 = (float(x) for x in np.nanpercentile(v, PERCENTILES))
    return {"median": p50, "p16": p16, "p84": p84}


def check_layout(h5) -> None:
    """``MapLayoutError`` naming the first of ``/pixel_info``, its fields (``PIXEL_FIELDS``), its ``DM_bin_edges``
    attribute and ``/samples`` that the file lacks."""
    if "pixel_info" not in h5:
        raise MapLayoutError("the map has no /pixel_info")
    info = h5["pixel_info"]
    names = info.dtype.names or ()
    for field in PIXEL_FIELDS:
        if field not in names:
            raise MapLayoutError(f"/pixel_info has no field {field}")
    if "DM_bin_edges" not in info.attrs:
        raise MapLayoutError("/pixel_info has no attribute DM_bin_edges")
    if "samples" not in h5:
        raise MapLayoutError("the map has no /samples")


def _scaled(p: dict | None) -> dict | None:
    return None if p is None else {k: BAYESTAR_TO_EBV * v for k, v in p.items()}


def query_many(h5, ra_deg: float, dec_deg: float, distances_pc) -> list[dict]:
    """``query`` at each distance on one sightline; the pixel is found once."""
    check_layout(h5)
    info = h5["pixel_info"]
    l_deg, b_deg = galactic(ra_deg, dec_deg)
    row = pixel_index(l_deg, b_deg, info.fields(["nside", "healpix_index"])[:])
    out = []
    if row is None:
        for d in distances_pc:
            out.append({"l_deg": l_deg, "b_deg": b_deg, "distance_pc": float(d), "dm": distance_modulus(d),
                        "in_footprint": False, "row": None, "nside": None, "healpix_index": None, "converged": None,
                        "reliable_distance": False, "n_samples": 0, "ebv_map_units": None, "ebv": None})
        return out
    pixel = info[row]
    grid = np.asarray(info.attrs["DM_bin_edges"], dtype=float)
    samples = np.asarray(h5["samples"][row], dtype=float)
    low, high = float(pixel["DM_reliable_min"]), float(pixel["DM_reliable_max"])
    for d in distances_pc:
        dm = distance_modulus(d)
        values = reddening_at(samples, grid, float(d))
        p = percentiles(values)
        out.append({"l_deg": l_deg, "b_deg": b_deg, "distance_pc": float(d), "dm": dm, "in_footprint": True,
                    "row": row, "nside": int(pixel["nside"]), "healpix_index": int(pixel["healpix_index"]),
                    "converged": bool(pixel["converged"]),
                    "reliable_distance": bool(math.isfinite(low) and math.isfinite(high) and low <= dm <= high),
                    "n_samples": int(np.isfinite(values).sum()), "ebv_map_units": p, "ebv": _scaled(p)})
    return out


def query(h5, ra_deg: float, dec_deg: float, distance_pc: float) -> dict:
    """The map at one star: {"l_deg", "b_deg", "distance_pc", "dm", "in_footprint", "row", "nside",
    "healpix_index", "converged", "reliable_distance", "n_samples", "ebv_map_units": {"median", "p16", "p84"},
    "ebv": the same times 0.884}. Outside the footprint ``in_footprint`` is False and the pixel's keys are None; a
    pixel without a finite sample at the distance gives ``n_samples`` 0 and None for both percentile blocks."""
    return query_many(h5, ra_deg, dec_deg, [distance_pc])[0]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--map", type=Path, required=True, help="bayestar2019.h5")
    ap.add_argument("--ra", type=float, required=True, help="ICRS, degrees")
    ap.add_argument("--dec", type=float, required=True, help="ICRS, degrees")
    ap.add_argument("--distance", type=float, nargs="+", required=True, help="parsecs, one or more")
    args = ap.parse_args(argv)
    if not args.map.is_file():
        print(f"{args.map} is not there", file=sys.stderr)
        return 2
    import h5py
    try:
        with h5py.File(args.map, "r") as h5:
            out = query_many(h5, args.ra, args.dec, args.distance)
    except (MapLayoutError, OSError) as e:
        print(f"{args.map}: {e}", file=sys.stderr)
        return 2
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
