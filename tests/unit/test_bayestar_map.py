"""tools/bayestar_map.py (spec 12.5): the Bayestar19 reader without healpy, on a synthetic map written here with h5py.

The map has the layout ``dustmaps.bayestar.BayestarQuery`` reads (verified against its source on 26 October 2026):
``/pixel_info`` with the fields nside, healpix_index, converged, DM_reliable_min and DM_reliable_max and the attribute
``DM_bin_edges``, and ``/samples`` of shape (pixels, samples, distances). Three nsides, four pixels, 5 samples and
bayestar2019's own grid, 120 distance moduli from 4.0 to 18.875 in steps of 0.125 (the real file's ``DM_bin_edges``
as the task review read them on 26 October 2026). The cumulative reddening is linear in DM, so every expected number
can be worked by hand. One pixel is reliable only from DM 8.04 (406 pc), above the query's DM 7.41 (302 pc), as KIC
6146676's pixel is on the real map; one has a reliable range with no finite upper end.

The h5py and HEALPix tests need the third venv, and skip under the engine's:

    C:/Research/skysift/.venv-dustmaps/Scripts/python.exe -m pytest tests/unit/test_bayestar_map.py -q \\
        -p no:cacheprovider --noconftest

No test opens the real map."""
import json

import numpy as np
import pytest

from tools import bayestar_map as bm

KIC_6146676 = (297.86871, 41.455567)     # (l, b) = (76.0444, 7.4198); nested 23376 at nside 64, 93504 at 128
SIBLING = (297.6085018, 41.8463527)      # the centre of nside-128 pixel 93505: inside 23376, not inside 93504
OTHER = (291.0, 40.9)                    # nested 23411 at nside 64, the unconverged pixel
NEAR = (297.6114774, 41.2431682)         # the centre of nside-256 pixel 374018, inside 93504 and 23376
SOUTH = (0.0, -80.0)                     # no pixel of the synthetic map holds it
DM_GRID = np.arange(4.0, 19.0, 0.125)    # 120 edges, the last 18.875
SCALE = np.array([0.01, 0.02, 0.03, 0.04, 0.05])
D_DM8 = 10 ** (13 / 5)                   # 398.107 pc, distance modulus 8 exactly
D_NEAR = 10 ** (12.41 / 5)               # 302.0 pc, distance modulus 7.41, between the edges 7.375 and 7.5


def _samples(factor: float) -> np.ndarray:
    """Cumulative reddening linear in DM: sample k at modulus dm is factor x SCALE[k] x (dm - 3)."""
    return factor * SCALE[:, None] * (DM_GRID[None, :] - 3.0)


def write_map(path, *, drop: str | None = None):
    """The synthetic map; ``drop`` leaves out one name ("samples", "DM_bin_edges" or a pixel_info field)."""
    h5py = pytest.importorskip("h5py")
    fields = [("nside", "<i4"), ("healpix_index", "<i8"), ("converged", "u1"), ("DM_reliable_min", "<f4"),
              ("DM_reliable_max", "<f4")]
    rows = [(64, 23376, 1, 5.0, 7.5), (64, 23411, 0, 5.0, np.nan), (128, 93504, 1, 5.0, 12.0),
            (256, 374018, 1, 8.04, 12.0)]
    keep = [i for i, (name, _type) in enumerate(fields) if name != drop]
    info = np.array([tuple(r[i] for i in keep) for r in rows], dtype=[fields[i] for i in keep])
    with h5py.File(path, "w") as f:
        ds = f.create_dataset("pixel_info", data=info)
        if drop != "DM_bin_edges":
            ds.attrs["DM_bin_edges"] = DM_GRID
        if drop != "samples":
            f.create_dataset("samples", data=np.stack([_samples(2.0), _samples(2.0), _samples(1.0),
                                                       _samples(3.0)]).astype("f4"))
        f.create_dataset("best_fit", data=np.stack([_samples(2.0)[2], _samples(2.0)[2], _samples(1.0)[2],
                                                    _samples(3.0)[2]]))
    return path


@pytest.fixture
def h5(tmp_path):
    h5py = pytest.importorskip("h5py")
    pytest.importorskip("astropy_healpix")
    with h5py.File(write_map(tmp_path / "bayestar2019.h5"), "r") as f:
        yield f


# ------------------------------------------------------------------------------------------- pure (any venv)

def test_the_synthetic_grid_is_bayestar2019s():
    assert DM_GRID.size == 120 and DM_GRID[0] == 4.0 and DM_GRID[-1] == 18.875
    assert np.allclose(np.diff(DM_GRID), 0.125)


def test_the_distance_modulus_is_five_log_d_over_ten_parsecs():
    assert bm.distance_modulus(10.0) == pytest.approx(0.0)
    assert bm.distance_modulus(D_DM8) == pytest.approx(8.0)
    assert bm.distance_modulus(1000.0) == pytest.approx(10.0)


def test_the_reddening_at_a_distance_follows_dustmaps():
    row = _samples(1.0)
    assert bm.reddening_at(row, DM_GRID, D_DM8) == pytest.approx(SCALE * 5.0)          # linear in DM, exact
    on_edge = 10 ** ((DM_GRID[37] + 5) / 5)
    assert bm.reddening_at(row, DM_GRID, on_edge) == pytest.approx(SCALE * (DM_GRID[37] - 3.0))
    # below the first modulus dustmaps scales the first bin by 10^(0.2 (dm - dm_0)), the ratio of the distances
    assert bm.reddening_at(row, DM_GRID, 10 ** (8 / 5)) == pytest.approx(SCALE * 10 ** -0.2)
    assert bm.reddening_at(row, DM_GRID, 10 ** (9 / 5)) == pytest.approx(SCALE * 1.0)   # dm 4.0: the first bin
    assert bm.reddening_at(row, DM_GRID, 1e6) == pytest.approx(SCALE * 15.875)          # beyond the map: the last
    # DM 7.41 between the edges 7.375 and 7.5: linear in DM, not the first bin scaled
    assert bm.reddening_at(row, DM_GRID, D_NEAR) == pytest.approx(SCALE * 4.41)
    two = np.array([[0.1, 0.2, 0.3, 0.4], [0.2, 0.3, 0.5, 0.6]])
    edges = np.array([4.0, 5.0, 6.0, 7.0])
    assert bm.reddening_at(two, edges, 10 ** (10.5 / 5)) == pytest.approx([0.25, 0.4])


def test_the_percentiles_are_dustmaps_nanpercentile_over_the_finite_samples():
    assert bm.percentiles(SCALE * 5.0) == pytest.approx({"median": 0.15, "p16": 0.082, "p84": 0.218})
    assert bm.percentiles(np.array([0.1, np.nan, 0.3])) == pytest.approx({"median": 0.2, "p16": 0.132,
                                                                            "p84": 0.268})
    assert bm.percentiles(np.array([np.nan, np.nan])) is None


def test_the_constants_name_the_dataverse_record():
    assert (bm.MAP_VERSION, bm.MAP_DOI, bm.BAYESTAR_TO_EBV) == ("bayestar2019", "10.7910/DVN/2EJ9TX", 0.884)
    assert bm.MAP_MD5 == "ab815d2fd3068d1b81a1bd61fb18a722"


# ------------------------------------------------------------------------- astropy, then h5py and HEALPix

def test_galactic_coordinates_come_from_icrs_through_astropy():
    assert bm.galactic(*KIC_6146676) == pytest.approx((76.04442, 7.41979), abs=1e-4)


def test_pixel_index_takes_the_finest_nside_that_holds_the_sightline():
    pytest.importorskip("astropy_healpix")
    info = np.array([(64, 23376), (64, 23411), (128, 93504)], dtype=[("nside", "<i4"), ("healpix_index", "<i8")])
    assert bm.pixel_index(*bm.galactic(*KIC_6146676), info) == 2       # 93504 at nside 128 over its parent
    assert bm.pixel_index(*bm.galactic(*SIBLING), info) == 0           # only the nside-64 parent holds it
    assert bm.pixel_index(*bm.galactic(*OTHER), info) == 1
    assert bm.pixel_index(*bm.galactic(*SOUTH), info) is None
    assert bm.pixel_index(*bm.galactic(*KIC_6146676), info[:2]) == 0


def test_query_gives_the_samples_percentiles_in_map_units_and_e_b_v(h5):
    q = bm.query(h5, *KIC_6146676, D_DM8)
    assert (q["in_footprint"], q["row"], q["nside"], q["healpix_index"]) == (True, 2, 128, 93504)
    assert q["converged"] is True and q["reliable_distance"] is True and q["n_samples"] == 5
    assert (q["dm_reliable_min"], q["dm_reliable_max"]) == pytest.approx((5.0, 12.0))
    assert q["dm"] == pytest.approx(8.0) and q["distance_pc"] == pytest.approx(D_DM8)
    assert q["ebv_map_units"] == pytest.approx({"median": 0.15, "p16": 0.082, "p84": 0.218}, rel=1e-6)
    assert q["ebv"] == pytest.approx({"median": 0.1326, "p16": 0.072488, "p84": 0.192712}, rel=1e-6)
    assert (q["l_deg"], q["b_deg"]) == pytest.approx((76.04442, 7.41979), abs=1e-4)
    json.dumps(q)                                                       # plain Python types only


def test_query_flags_an_unconverged_pixel_an_unreliable_distance_and_a_sightline_outside_the_map(h5):
    other = bm.query(h5, *OTHER, D_DM8)
    assert other["converged"] is False and other["row"] == 1 and other["ebv"]["median"] == pytest.approx(
        0.884 * 0.30, rel=1e-6)
    # a reliable range with no finite upper end: no reliable distance, and None for the end (JSON has no NaN)
    assert other["reliable_distance"] is False and other["dm_reliable_max"] is None
    assert other["dm_reliable_min"] == pytest.approx(5.0)
    sibling = bm.query(h5, *SIBLING, D_DM8)                            # row 0 is reliable only to DM 7.5
    assert sibling["row"] == 0 and sibling["reliable_distance"] is False and sibling["converged"] is True
    assert sibling["dm_reliable_max"] == pytest.approx(7.5) and sibling["dm"] > sibling["dm_reliable_max"]
    south = bm.query(h5, *SOUTH, D_DM8)
    assert south["in_footprint"] is False and south["ebv"] is None and south["converged"] is None
    assert south["row"] is None and south["n_samples"] == 0 and south["reliable_distance"] is False
    assert south["dm_reliable_min"] is None and south["dm_reliable_max"] is None
    many = bm.query_many(h5, *KIC_6146676, [10 ** (8 / 5), D_DM8, 1e6])
    assert [m["ebv_map_units"]["median"] for m in many] == pytest.approx([0.03 * 10 ** -0.2, 0.15, 0.47625])
    assert [m["row"] for m in many] == [2, 2, 2]


def test_a_star_nearer_than_its_pixels_reliable_range_is_flagged_with_the_range(h5):
    """The live case (review M4): DM 7.41 on a pixel reliable only from DM 8.04, as KIC 6146676 is on the real map.
    The value is the samples interpolated in DM between the edges 7.375 and 7.5, below the reliable minimum."""
    q = bm.query(h5, *NEAR, D_NEAR)
    assert (q["row"], q["nside"], q["healpix_index"]) == (3, 256, 374018)
    assert q["dm"] == pytest.approx(7.41) and q["reliable_distance"] is False and q["converged"] is True
    assert q["dm_reliable_min"] == pytest.approx(8.04, abs=1e-6) and q["dm"] < q["dm_reliable_min"]
    assert q["dm_reliable_max"] == pytest.approx(12.0)
    assert q["ebv_map_units"]["median"] == pytest.approx(3.0 * 0.03 * 4.41, rel=1e-6)
    json.dumps(q)


def test_check_layout_names_the_first_missing_dataset_field_or_attribute(tmp_path):
    h5py = pytest.importorskip("h5py")
    for drop, words in (("samples", "the map has no /samples"),
                        ("DM_bin_edges", "/pixel_info has no attribute DM_bin_edges"),
                        ("converged", "/pixel_info has no field converged")):
        with h5py.File(write_map(tmp_path / f"{drop}.h5", drop=drop), "r") as f, pytest.raises(bm.MapLayoutError,
                                                                                      match=words):
            bm.check_layout(f)


def test_main_prints_the_queries_as_json_and_refuses_a_missing_file(tmp_path, capsys):
    pytest.importorskip("h5py")
    pytest.importorskip("astropy_healpix")
    path = write_map(tmp_path / "bayestar2019.h5")
    ra, dec = KIC_6146676
    assert bm.main(["--map", str(path), "--ra", str(ra), "--dec", str(dec), "--distance", str(D_DM8), "1e6"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert [q["ebv_map_units"]["median"] for q in out] == pytest.approx([0.15, 0.47625])
    assert bm.main(["--map", str(tmp_path / "none.h5"), "--ra", "1", "--dec", "1", "--distance", "100"]) == 2
    assert "none.h5 is not there" in capsys.readouterr().err
    bad = write_map(tmp_path / "bad.h5", drop="samples")
    assert bm.main(["--map", str(bad), "--ra", "1", "--dec", "1", "--distance", "100"]) == 2
    assert "the map has no /samples" in capsys.readouterr().err
