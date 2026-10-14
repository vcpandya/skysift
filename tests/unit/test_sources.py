import hashlib
import io

import numpy as np
import pytest
from astropy.io import fits
from astropy.table import MaskedColumn, Table

from skysift.models import Target
from skysift.sources.base import load_registry
from skysift.sources.kbonus_bkg import BASE, CATALOG_URL, KBonusBkg, _kic_of, lc_url
from skysift.store.db import ArtifactRow, Store


def test_registry_has_live_kbonus():
    reg = load_registry()
    assert reg["kbonus_bkg"].status == "live" and reg["kbonus_bkg"].n_lightcurves == 606900
    assert all(v.est_yield_per_10k >= 0 for v in reg.values())


def test_registry_costs_and_cpu_hours():
    reg = load_registry()
    assert reg["kbonus_bkg"].seconds_per_star == 250
    assert {k: reg[k].seconds_per_star for k in ("kepler", "k2", "tess_lc", "exoarchive_bulk", "gaia_epoch", "ztf")} == {
        "kepler": 250, "k2": 15, "tess_lc": 5, "exoarchive_bulk": 20, "gaia_epoch": 1, "ztf": 2}
    assert "~250 CPU-s per star" in reg["kbonus_bkg"].notes
    assert reg["kbonus_bkg"].cpu_hours_per_10k() == pytest.approx(250 * 1e4 / 3600)
    assert reg["tess_lc"].cpu_hours_per_10k() == pytest.approx(5 * 1e4 / 3600)


def test_lc_url_for_kic_and_gaia():
    assert lc_url(Target(source="kbonus_bkg", target_id="kic-005689121", ra=0, dec=0, kic=5689121,
                         meta={"file_name": "hlsp_kbonus-bkg_kepler_kepler_kic-005689121_kepler_v1.0_lc.fits"})) == \
        "https://archive.stsci.edu/hlsps/kbonus-bkg/lcs/0056/005689121/hlsp_kbonus-bkg_kepler_kepler_kic-005689121_kepler_v1.0_lc.fits"
    assert lc_url(Target(source="kbonus_bkg", target_id="gaia-dr3-2049134501869730432", ra=0, dec=0, gaia_id="2049134501869730432",
                         meta={"file_name": "hlsp_kbonus-bkg_kepler_kepler_gaia-dr3-2049134501869730432_kepler_v1.0_lc.fits"})) == \
        "https://archive.stsci.edu/hlsps/kbonus-bkg/lcs/2049/2049134501869730432/hlsp_kbonus-bkg_kepler_kepler_gaia-dr3-2049134501869730432_kepler_v1.0_lc.fits"


def _catalog_table(kic, gmag=(15.0, 18.5, 17.0), psffrac=(0.9, 0.9, 0.3)):
    return Table({"gaia_designation": np.array(["Gaia DR3 1", "Gaia DR3 2", "Gaia DR3 3"], dtype="U32"),
                 "ra": [1.0, 2.0, 3.0], "dec": [1.0, 2.0, 3.0],
                 "phot_g_mean_mag": gmag, "psffrac": psffrac, "kic": kic,
                 "fname": np.array(["kic-000000123", "gaia-dr3-2", "gaia-dr3-3"], dtype="S28"),   # real column names (v1.0 catalogue)
                 "tpforg": [1, 1, 1], "crowdsap": [0.9, 0.8, 0.7],
                 "sap_avail": np.array(["111111111111111111", "011011001000100011", "100000000000000000"], dtype="S18")})


def _write_catalog(path, kic, **kw):
    _catalog_table(kic, **kw).write(path, format="fits")


def test_list_targets_filters(tmp_path):
    p = tmp_path / "cat.fits"; _write_catalog(p, [123, -1, -1])
    src = KBonusBkg(blob=None, store=None, catalog_path=p)
    ids = [t.target_id for t in src.list_targets({"gmag_max": 18.0, "background_only": True, "psffrac_min": 0.5})]
    assert ids == []   # 2 is too faint, 3 has low psffrac, 1 is a KIC target
    ids = [t.target_id for t in src.list_targets({"gmag_max": 19.0, "background_only": True, "psffrac_min": 0.5})]
    assert ids == ["gaia-dr3-2"]


def test_list_targets_kic_targets_limit_and_fields(tmp_path):
    p = tmp_path / "cat.fits"; _write_catalog(p, [123, -1, -1])
    src = KBonusBkg(blob=None, store=None, catalog_path=p)
    ts = list(src.list_targets({"gmag_max": 19.0, "background_only": False, "psffrac_min": 0.0}))
    assert [t.target_id for t in ts] == ["kic-000000123", "gaia-dr3-2", "gaia-dr3-3"]
    assert ts[0].kic == 123 and ts[0].gaia_id == "1" and ts[1].kic is None
    assert ts[1].gmag == 18.5 and ts[1].meta == {"file_name": "hlsp_kbonus-bkg_kepler_kepler_gaia-dr3-2_kepler_v1.0_lc.fits",
                                                 "tpf_org": 1, "crowdsap": 0.8, "psffrac": 0.9, "n_quarters": 8}
    assert [t.meta["n_quarters"] for t in ts] == [18, 8, 1]
    assert lc_url(ts[0]) == f"{BASE}/lcs/0000/000000123/hlsp_kbonus-bkg_kepler_kepler_kic-000000123_kepler_v1.0_lc.fits"
    assert [t.target_id for t in src.list_targets({"gmag_max": 19.0, "background_only": False, "psffrac_min": 0.0, "limit": 2})] \
        == ["kic-000000123", "gaia-dr3-2"]


def test_list_targets_masked_kic_counts_as_no_kic(tmp_path):
    p = tmp_path / "cat.fits"
    _write_catalog(p, MaskedColumn([123, 0, 7], mask=[False, True, False], dtype="i8"))
    src = KBonusBkg(blob=None, store=None, catalog_path=p)
    ts = list(src.list_targets({"gmag_max": 19.0, "background_only": False, "psffrac_min": 0.0}))
    assert [t.kic for t in ts] == [123, None, 7]
    assert [t.target_id for t in src.list_targets({"gmag_max": 19.0, "background_only": True, "psffrac_min": 0.0})] == ["gaia-dr3-2"]


def test_kic_of_sentinels():
    assert _kic_of(None) is None and _kic_of(np.ma.masked) is None
    assert _kic_of(float("nan")) is None and _kic_of(-1) is None and _kic_of(0) is None
    assert _kic_of(np.int64(5689121)) == 5689121 and _kic_of(5689121.0) == 5689121


def test_list_targets_rejects_non_finite_gmag_and_psffrac(tmp_path):
    p = tmp_path / "cat.fits"
    _write_catalog(p, [-1, -1, -1], gmag=[float("nan"), 15.0, 16.0],
                   psffrac=MaskedColumn([0.9, 0.0, 0.9], mask=[False, True, False], dtype="f8"))
    src = KBonusBkg(blob=None, store=None, catalog_path=p)
    ids = [t.target_id for t in src.list_targets({"gmag_max": 19.0, "background_only": True, "psffrac_min": 0.0})]
    assert ids == ["gaia-dr3-3"]   # 1 has NaN gmag, 2 has a masked psffrac


def test_list_targets_limit_zero_emits_nothing(tmp_path):
    p = tmp_path / "cat.fits"; _write_catalog(p, [-1, -1, -1])
    src = KBonusBkg(blob=None, store=None, catalog_path=p)
    flt = {"gmag_max": 19.0, "background_only": True, "psffrac_min": 0.0}
    assert list(src.list_targets({**flt, "limit": 0})) == []
    assert len(list(src.list_targets({**flt, "limit": None}))) == 3


class MemBlob:
    def __init__(self): self.items = {}

    def put(self, data, name_hint):
        sha = hashlib.sha256(data).hexdigest(); self.items[sha] = data
        return sha, f"mem://{sha}"


class FakeFetch:
    def __init__(self, payload): self.payload, self.calls = payload, []

    def __call__(self, url, *, service, store=None, **kw):
        self.calls.append((url, service)); return self.payload


@pytest.fixture
def store(tmp_path):
    s = Store(f"sqlite:///{tmp_path.as_posix()}/t.db"); s.init_schema(); return s


def _artifacts(store):
    with store.session() as s:
        return [(r.sha256, r.url, r.target_id, r.pinned, r.nbytes) for r in s.query(ArtifactRow).all()]


def test_catalog_download_is_stored_pinned_and_cached(store):
    buf = io.BytesIO(); _catalog_table([123, -1, -1]).write(buf, format="fits"); data = buf.getvalue()
    fetch = FakeFetch(data)
    src = KBonusBkg(blob=MemBlob(), store=store, fetch=fetch)
    assert len(src.catalog()) == 3
    assert fetch.calls == [(CATALOG_URL, "mast")]
    sha = hashlib.sha256(data).hexdigest()
    assert _artifacts(store) == [(sha, CATALOG_URL, None, True, len(data))]
    assert len(src.catalog()) == 3 and len(fetch.calls) == 1
    assert [t.target_id for t in src.list_targets({"gmag_max": 19.0, "background_only": True, "psffrac_min": 0.5})] == ["gaia-dr3-2"]
    assert len(fetch.calls) == 1


def _kbonus_fits_bytes():
    n = 5
    cols = lambda: [fits.Column(name=c, format="D", array=np.linspace(1.0, 2.0, n)) for c in
                    ("TIME", "FLUX", "FLUX_ERR", "SAP_FLUX", "SAP_FLUX_ERR")] +         [fits.Column(name="SAP_QUALITY", format="J", array=np.zeros(n, int)),
         fits.Column(name="QUARTER", format="J", array=np.full(n, 1))]
    prim = fits.PrimaryHDU()
    prim.header.update({"KEPLERID": 5689121, "GAIAID": "Gaia DR3 2049134501869730432", "TPFORG": 1, "GMAG": 17.2})
    stitched = fits.BinTableHDU.from_columns(cols(), name="LIGHTCURVE_STITCHED")
    q1 = fits.BinTableHDU.from_columns(cols(), name="LIGHTCURVE_Q1")
    q1.header.update({"FLFRCSAP": 0.8, "CROWDSAP": 0.9, "NPIXSAP": 12, "PSFFRAC": 0.7, "QUARTER": 1})
    buf = io.BytesIO(); fits.HDUList([prim, stitched, q1]).writeto(buf); return buf.getvalue()


TARGET = Target(source="kbonus_bkg", target_id="kic-005689121", ra=0, dec=0, kic=5689121, gaia_id="2049134501869730432",
                meta={"file_name": "hlsp_kbonus-bkg_kepler_kepler_kic-005689121_kepler_v1.0_lc.fits"})


def test_fetch_lightcurve_stores_unpinned_artifact(store):
    data = _kbonus_fits_bytes(); fetch = FakeFetch(data)
    src = KBonusBkg(blob=MemBlob(), store=store, fetch=fetch)
    lc, sha, url = src.fetch_lightcurve(TARGET)
    assert fetch.calls == [(lc_url(TARGET), "mast")] and url == lc_url(TARGET)
    assert sha == hashlib.sha256(data).hexdigest()
    assert _artifacts(store) == [(sha, url, TARGET.target_id, False, len(data))]
    assert lc.meta["target_id"] == TARGET.target_id and lc.meta["kic"] == 5689121
    assert len(lc.time) == 5 and lc.meta["per_quarter"][1]["psffrac"] == 0.7


def test_fetch_lightcurve_without_blob_adds_no_artifact(store):
    data = _kbonus_fits_bytes()
    src = KBonusBkg(blob=None, store=store, fetch=FakeFetch(data))
    lc, sha, _url = src.fetch_lightcurve(TARGET)
    assert sha == hashlib.sha256(data).hexdigest() and lc.meta["target_id"] == TARGET.target_id
    assert _artifacts(store) == []


class _Resp:
    def __init__(self, content): self.status_code, self.content = 200, content


class _Session:
    def __init__(self, content): self.content = content

    def get(self, url, params=None, timeout=None): return _Resp(self.content)


def _queries(store):
    from skysift.store.db import QueryRow
    with store.session() as s:
        return [(q.service, q.url, q.target_id) for q in s.query(QueryRow).all()]


def test_fetch_lightcurve_logs_the_download_with_its_target(store):
    from functools import partial

    from skysift import net
    src = KBonusBkg(blob=MemBlob(), store=store, fetch=partial(net.fetch, session=_Session(_kbonus_fits_bytes())))
    src.fetch_lightcurve(TARGET)
    assert _queries(store) == [("mast", lc_url(TARGET), TARGET.target_id)]
