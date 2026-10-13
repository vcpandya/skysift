from __future__ import annotations

import hashlib
from collections.abc import Iterator
from pathlib import Path

import numpy as np
from astropy.table import Table

from skysift import net
from skysift.lc.kbonus import parse_kbonus_fits
from skysift.models import LightCurve, Target

BASE = "https://archive.stsci.edu/hlsps/kbonus-bkg"
CATALOG_URL = f"{BASE}/catalogs/hlsp_kbonus-bkg_kepler_kepler_source-catalog_kepler_v1.0_cat.fits"


def _kic_of(value) -> int | None:
    """KIC id from a catalogue cell; None, masked, NaN and values <= 0 all mean 'no KIC'."""
    if value is None or value is np.ma.masked:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return int(v) if np.isfinite(v) and v > 0 else None


def _id_string(t: Target) -> str:
    return f"{t.kic:09d}" if t.kic and t.kic > 0 else str(t.gaia_id)


def lc_url(t: Target) -> str:
    sid = _id_string(t)
    return f"{BASE}/lcs/{sid[:4]}/{sid}/{t.meta['file_name']}"


def _strings(col) -> np.ndarray:
    arr = np.asarray(col)
    return np.char.decode(arr, "utf-8") if arr.dtype.kind == "S" else arr.astype(str)


class KBonusBkg:
    name = "kbonus_bkg"

    def __init__(self, blob, store, fetch=net.fetch, catalog_path: Path | None = None):
        self.blob, self.store, self.fetch, self._catalog_path = blob, store, fetch, catalog_path

    def catalog(self) -> Table:
        if self._catalog_path is None:
            data = self.fetch(CATALOG_URL, service="mast", store=self.store, timeout=600)
            sha, loc = self.blob.put(data, "kbonus_source_catalog.fits")
            if self.store: self.store.add_artifact(sha, len(data), CATALOG_URL, loc, None, pinned=True)
            self._catalog_path = Path(loc)
        return Table.read(self._catalog_path, format="fits")

    def list_targets(self, filter: dict) -> Iterator[Target]:
        tab = self.catalog()
        gmag_max = filter.get("gmag_max", 19.0); psffrac_min = filter.get("psffrac_min", 0.5)
        background_only = filter.get("background_only", True); limit = filter.get("limit")
        # Columns become numpy arrays once; astropy row iteration is far too slow for 606,900 rows.
        gmag = np.asarray(tab["phot_g_mean_mag"], dtype=float); psffrac = np.asarray(tab["psffrac"], dtype=float)
        keep = ~(gmag > gmag_max) & ~(psffrac < psffrac_min)
        kic_col = np.ma.asarray(tab["kic"])
        ra = np.asarray(tab["ra"], dtype=float); dec = np.asarray(tab["dec"], dtype=float)
        gaia_col = _strings(tab["gaia_designation"]); file_col = _strings(tab["file_name"])
        tpf_org = np.asarray(tab["tpf_org"]); crowdsap = np.asarray(tab["crowdsap"], dtype=float)
        n = 0
        for i in np.flatnonzero(keep):
            kic = _kic_of(kic_col[i])
            if background_only and kic: continue
            gaia = gaia_col[i].replace("Gaia DR3 ", "").strip()
            tid = f"kic-{kic:09d}" if kic else f"gaia-dr3-{gaia}"
            yield Target(source=self.name, target_id=tid, ra=float(ra[i]), dec=float(dec[i]), gaia_id=gaia, kic=kic,
                         gmag=float(gmag[i]),
                         meta={"file_name": str(file_col[i]), "tpf_org": int(tpf_org[i]), "crowdsap": float(crowdsap[i]),
                               "psffrac": float(psffrac[i])})
            n += 1
            if limit and n >= limit: return

    def fetch_lightcurve(self, target: Target) -> tuple[LightCurve, str, str]:
        url = lc_url(target)
        data = self.fetch(url, service="mast", store=self.store, timeout=300)
        sha = hashlib.sha256(data).hexdigest()
        if self.blob is not None:
            _, loc = self.blob.put(data, Path(url).name)
            if self.store: self.store.add_artifact(sha, len(data), url, loc, target.target_id)
        lc = parse_kbonus_fits(data); lc.meta["target_id"] = target.target_id
        return lc, sha, url
