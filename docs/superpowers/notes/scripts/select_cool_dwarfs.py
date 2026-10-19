"""Pick the first real-sweep list: cool dwarfs Kepler never targeted, bright and clean, with giants removed.

Reads the cached KBonus source catalogue through the adapter (registry rule: not a Kepler target, G <= 19,
psffrac >= 0.5), applies the cuts below, fetches Gaia DR3 parallaxes for what is left (one TAP query per 400 stars)
and keeps the dwarfs by absolute magnitude. Writes the id list (one target id per line, M dwarfs first, then brightest first) and prints
the counts for the note. Read-only on the data directory.

    SKYSIFT_DATA_DIR=... SKYSIFT_BLOB_URL=... python -m docs.superpowers.notes.scripts.select_cool_dwarfs OUT.txt

Cuts (19 October 2026): quarters >= 6 (three or more transits out to about 150 d and the season test), G <= 17
(per-cadence scatter under about 2000 ppm), BP - RP >= 1.2 (K and M dwarfs: a given planet makes a deeper transit on
a smaller star, and KIC 5689121 is an M dwarf at BP - RP 2.19), crowdsap >= 0.7 (most of the aperture flux is the
star's own), Gaia parallax_over_error >= 5 and absolute magnitude M_G >= 4.0 (giants at the same colour sit at
M_G < 2 and their transits are too shallow to find).
"""
from __future__ import annotations

import glob
import io
import os
import sys
from pathlib import Path

import numpy as np
from astropy.table import Table

from skysift.net import fetch
from skysift.sources.kbonus_bkg import KBonusBkg, _kic_of

GAIA_TAP = "https://gea.esac.esa.int/tap-server/tap/sync"
MIN_QUARTERS, G_MAX, BP_RP_MIN, CROWDSAP_MIN = 6, 17.0, 1.2, 0.7
PLX_OVER_ERR_MIN, ABS_MAG_MIN = 5.0, 4.0
M_DWARF_BP_RP = 1.8                 # roughly M0 and later; these go first in the list
CHUNK = 100                      # the TAP endpoint is a GET; 400 ids made the URL too long (HTTP 414)


def _floats(col):
    return np.ma.filled(np.ma.asarray(col).astype(float), np.nan)


def main(out: Path) -> None:
    data_dir = Path(os.environ.get("SKYSIFT_DATA_DIR", "data"))
    cat = sorted(glob.glob(str(data_dir / "blobs" / "**" / "*kbonus_source_catalog.fits"), recursive=True))
    if not cat:
        raise SystemExit(f"no cached catalogue under {data_dir / 'blobs'}")
    src = KBonusBkg(blob=None, store=None, catalog_path=Path(cat[-1]))
    tab: Table = src.catalog()
    g, bp, rp = _floats(tab["phot_g_mean_mag"]), _floats(tab["phot_bp_mean_mag"]), _floats(tab["phot_rp_mean_mag"])
    psffrac, crowd = _floats(tab["psffrac"]), _floats(tab["crowdsap"])
    kic = np.array([_kic_of(v) or 0 for v in np.ma.asarray(tab["kic"])]); tpf = np.asarray(tab["tpforg"], dtype=int)
    nq = np.array([str(v).count("1") for v in tab["sap_avail"]])
    gaia = np.array([str(v).replace("Gaia DR3 ", "").strip() for v in tab["gaia_designation"]])
    registry = np.isfinite(g) & np.isfinite(psffrac) & (g <= 19) & (psffrac >= 0.5) & ~((kic > 0) & (kic == tpf))
    cut = registry & (nq >= MIN_QUARTERS) & (g <= G_MAX) & (bp - rp >= BP_RP_MIN) & (crowd >= CROWDSAP_MIN)
    idx = np.flatnonzero(cut)
    print(f"registry {registry.sum()}; after quarters/brightness/colour/crowding cuts {idx.size}")
    ids = [gaia[i] for i in idx if gaia[i].isdigit()]
    plx: dict[str, tuple[float, float]] = {}
    for k in range(0, len(ids), CHUNK):
        chunk = ids[k:k + CHUNK]
        q = f"select source_id, parallax, parallax_over_error from gaiadr3.gaia_source where source_id in ({','.join(chunk)})"
        rows = Table.read(io.BytesIO(fetch(GAIA_TAP, params={"request": "doQuery", "lang": "ADQL", "format": "csv", "query": q},
                                           service="gaia", timeout=300)), format="csv")
        for r in rows:
            plx[str(r["source_id"])] = (float(r["parallax"]), float(r["parallax_over_error"]))
        print(f"  gaia {min(k + CHUNK, len(ids))} of {len(ids)}", file=sys.stderr)
    kept = []
    n_noplx = n_giant = 0
    for i in idx:
        p = plx.get(gaia[i])
        if p is None or not np.isfinite(p[0]) or p[0] <= 0 or p[1] < PLX_OVER_ERR_MIN:
            n_noplx += 1; continue
        abs_g = g[i] + 5 * np.log10(p[0] / 1000.0) + 5
        if abs_g < ABS_MAG_MIN:
            n_giant += 1; continue
        tid = f"kic-{kic[i]:09d}" if kic[i] > 0 else f"gaia-dr3-{gaia[i]}"
        kept.append((bp[i] - rp[i] < M_DWARF_BP_RP, g[i], -nq[i], tid, bp[i] - rp[i], abs_g, nq[i], kic[i] > 0))
    kept.sort()                                     # M dwarfs first (smallest stars, deepest transits), then brightest first
    print(f"no or poor parallax {n_noplx}; giants (M_G < {ABS_MAG_MIN}) {n_giant}; dwarfs kept {len(kept)}")
    print(f"kept: KIC bystanders {sum(k[7] for k in kept)}, no-KIC {sum(not k[7] for k in kept)}; "
          f"median G {np.median([k[1] for k in kept]):.2f}, median quarters {np.median([k[6] for k in kept]):.0f}, "
          f"median BP-RP {np.median([k[4] for k in kept]):.2f}, median M_G {np.median([k[5] for k in kept]):.2f}")
    print(f"M dwarfs (BP-RP >= {M_DWARF_BP_RP}) among kept, listed first: {sum(not k[0] for k in kept)}")
    header = "# cool dwarfs Kepler never targeted: M dwarfs first, then K dwarfs, brightest first within each (select_cool_dwarfs.py, 19 October 2026)"
    out.write_text(header + chr(10) + "".join(k[3] + chr(10) for k in kept), encoding="utf-8")
    print(f"wrote {out} ({len(kept)} ids)")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
