import numpy as np
import pytest
from astropy.table import MaskedColumn, Table

from skysift.models import Target
from skysift.sources.base import load_registry
from skysift.sources.kbonus_bkg import KBonusBkg, _kic_of, lc_url


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


def _write_catalog(path, kic):
    tab = Table({"gaia_designation": np.array(["Gaia DR3 1", "Gaia DR3 2", "Gaia DR3 3"], dtype="U32"),
                 "ra": [1.0, 2.0, 3.0], "dec": [1.0, 2.0, 3.0],
                 "phot_g_mean_mag": [15.0, 18.5, 17.0], "psffrac": [0.9, 0.9, 0.3], "kic": kic,
                 "file_name": np.array(["a.fits", "b.fits", "c.fits"], dtype="U32"),
                 "tpf_org": [1, 1, 1], "crowdsap": [0.9, 0.8, 0.7]})
    tab.write(path, format="fits")


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
    assert ts[1].gmag == 18.5 and ts[1].meta == {"file_name": "b.fits", "tpf_org": 1, "crowdsap": 0.8, "psffrac": 0.9}
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
