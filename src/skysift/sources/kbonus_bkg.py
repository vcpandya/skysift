from __future__ import annotations

import hashlib
import io
from collections.abc import Iterator
from pathlib import Path

import numpy as np
from astropy.table import Table

from skysift import net
from skysift.lc.kbonus import parse_kbonus_fits
from skysift.models import LightCurve, Target

# the KBonus crowding metric of each extraction: this column of the source catalogue, read into ``meta["crowdsap"]``
# (the light-curve file's per-quarter CROWDSAP header keywords are the same metric per quarter)
CROWDSAP_COLUMN = "crowdsap"
CATALOGUE = "KBonus-bkg v1.0 source catalogue (Martinez-Palomera et al. 2023)"

BASE = "https://archive.stsci.edu/hlsps/kbonus-bkg"
CATALOG_URL = f"{BASE}/catalogs/hlsp_kbonus-bkg_kepler_kepler_source-catalog_kepler_v1.0_cat.fits"
FILE_TEMPLATE = "hlsp_kbonus-bkg_kepler_kepler_{fname}_kepler_v1.0_lc.fits"   # verified against MAST, October 2026


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


def _floats(col) -> np.ndarray:
    """Float array with masked entries as NaN."""
    return np.ma.filled(np.ma.asarray(col).astype(float), np.nan)


def _strings(col) -> np.ndarray:
    arr = np.asarray(col)
    return np.char.decode(arr, "utf-8") if arr.dtype.kind == "S" else arr.astype(str)


class KBonusBkg:
    name = "kbonus_bkg"

    def __init__(self, blob, store, fetch=net.fetch, catalog_path: Path | None = None):
        self.blob, self.store, self.fetch, self._catalog_path = blob, store, fetch, catalog_path
        self._table: Table | None = None

    def catalog(self) -> Table:
        if self._table is None:
            if self._catalog_path is not None:
                self._table = Table.read(self._catalog_path, format="fits")
            else:
                data = self.fetch(CATALOG_URL, service="mast", store=self.store, timeout=600)
                sha, loc = self.blob.put(data, "kbonus_source_catalog.fits")
                if self.store is not None: self.store.add_artifact(sha, len(data), CATALOG_URL, loc, None, pinned=True)
                self._table = Table.read(io.BytesIO(data), format="fits")
        return self._table

    def list_targets(self, filter: dict) -> Iterator[Target]:
        tab = self.catalog()
        gmag_max = filter.get("gmag_max", 19.0); psffrac_min = filter.get("psffrac_min", 0.5)
        # ``background_only`` skips the stars Kepler itself targeted and searched: a row whose ``kic`` equals ``tpforg``
        # was extracted from its own target-pixel file. A KIC id alone is not that: the KIC lists 13 million stars and
        # Kepler targeted about 200,000, so a KIC-numbered star extracted from a neighbour's pixels (KIC 5689121 in the
        # pixels of KIC 5689116) was never searched and is kept like a no-KIC star. Counts of 19 October 2026 after the
        # G <= 19 and psffrac >= 0.5 cuts: 34,161 no-KIC stars, 84,487 KIC bystanders, 166,215 Kepler targets.
        background_only = filter.get("background_only", True); limit = filter.get("limit")
        # Columns become numpy arrays once; astropy row iteration is far too slow for 606,900 rows.
        gmag = _floats(tab["phot_g_mean_mag"]); psffrac = _floats(tab["psffrac"])
        keep = np.isfinite(gmag) & np.isfinite(psffrac) & (gmag <= gmag_max) & (psffrac >= psffrac_min)
        kic_col = np.ma.asarray(tab["kic"])
        ra = np.asarray(tab["ra"], dtype=float); dec = np.asarray(tab["dec"], dtype=float)
        # The v1.0 source catalogue names each target by its ``fname`` stem (``kic-003357387`` or
        # ``gaia-dr3-2049134982905809664``); the light-curve file name is derived from it (see FILE_TEMPLATE).
        gaia_col = _strings(tab["gaia_designation"]); fname_col = _strings(tab["fname"])
        tpf_org = np.asarray(tab["tpforg"]); crowdsap = np.asarray(tab[CROWDSAP_COLUMN], dtype=float)
        # ``sap_avail`` is an 18-character bit string, one per Kepler quarter (Q0 to Q17); its count of ones is the
        # number of quarters with a light curve. 5,472 of the 34,161 no-KIC stars have only Q0 (9.7 days, 469 points),
        # which is below the detector's MIN_POINTS and cannot be searched; 57,002 of the 84,487 KIC bystanders have
        # four or more quarters.
        n_quarters = np.array([str(v).count("1") for v in _strings(tab["sap_avail"])], dtype=int)
        if limit is not None and limit <= 0: return
        n = 0
        for i in np.flatnonzero(keep):
            kic = _kic_of(kic_col[i])
            kepler_target = kic is not None and kic == int(tpf_org[i])
            if background_only and kepler_target: continue
            gaia = gaia_col[i].replace("Gaia DR3 ", "").strip()
            tid = f"kic-{kic:09d}" if kic else f"gaia-dr3-{gaia}"
            yield Target(source=self.name, target_id=tid, ra=float(ra[i]), dec=float(dec[i]), gaia_id=gaia, kic=kic,
                         gmag=float(gmag[i]),
                         meta={"file_name": FILE_TEMPLATE.format(fname=str(fname_col[i]).strip()), "tpf_org": int(tpf_org[i]),
                               "crowdsap": float(crowdsap[i]), "n_quarters": int(n_quarters[i]),
                               "psffrac": float(psffrac[i]), "kepler_target": kepler_target})
            n += 1
            if limit is not None and n >= limit: return

    def fetch_lightcurve(self, target: Target) -> tuple[LightCurve, str, str]:
        url = lc_url(target)
        data = self.fetch(url, service="mast", store=self.store, timeout=300,
                          target_id=target.target_id)
        sha = hashlib.sha256(data).hexdigest()
        if self.blob is not None:
            _, loc = self.blob.put(data, Path(url).name)
            if self.store is not None: self.store.add_artifact(sha, len(data), url, loc, target.target_id)
        lc = parse_kbonus_fits(data); lc.meta["target_id"] = target.target_id
        return lc, sha, url
