import dataclasses
import json
import math

import pytest

from skysift.fpp import Fpp, FppInputs, Neighbour, compute

PX = 3.98   # arcsec per Kepler pixel


def base(**over) -> FppInputs:
    # KIC 6146676-like: P 40.29, depth 2165e-6, G 16.24, Teff 3650, R 0.498, M 0.495, rp 2.61, k 0.048, b 0.885,
    # a/R 44.0, secondary 72 +- 80 ppm, odd 2202 even 2115 err 199, two neighbours (G 19.77 at 7.1", G 12.8 at 10.8"
    # with centroid separations 0.19 and 2.8 pixels), centroid sigma 0.05 pixel
    inputs = FppInputs(
        period=40.29, depth=2165e-6, gmag=16.24, teff=3650.0, radius=0.498, mass=0.495, rp_rearth=2.61,
        fit_k=0.048, fit_b=0.885, fit_a_over_r=44.0, secondary_ppm=72.0, secondary_err_ppm=80.0,
        odd_ppm=2202.0, even_ppm=2115.0, odd_even_err_ppm=199.0,
        neighbours=(Neighbour("near", 7.1, 19.77, None, centroid_sep_arcsec=0.19 * PX),
                    Neighbour("far", 10.8, 12.8, None, centroid_sep_arcsec=2.8 * PX)),
        centroid_sigma_arcsec=0.05 * PX)
    return dataclasses.replace(inputs, **over)


def test_three_scenarios_sum_to_the_fpp():
    f = compute(base())
    w = {s.name: s.weight for s in f.scenarios}
    assert f.value == pytest.approx((w["BEB"] + w["EB"]) / sum(w.values()))
    assert 0.01 <= f.value <= 0.6                       # the expected scale of spec 4.2 for this star


def test_bright_neighbour_far_from_the_centroid_is_excluded():
    f = compute(base())
    near, far = f.neighbours
    assert far["bright_enough"] and far["exclusion"] < 1e-3
    # The brief asserted `not near["bright_enough"]`, but by the spec 4.2 rule a G 19.77 star beside a G 16.24 target
    # holds 3.7 percent of the light, over twice the 2165 ppm depth, so it can make the dip. As the fixture places it
    # 0.19 px from the difference centroid (inside r_c), it is not excluded either. Flagged for review.
    assert near["bright_enough"] and near["flux_fraction"] == pytest.approx(0.0373, abs=1e-4)
    assert near["exclusion"] == 1.0


def test_compute_without_pixels_uses_one_pixel_confusion_radius():
    no_pix = tuple(dataclasses.replace(n, centroid_sep_arcsec=None) for n in base().neighbours)
    f = compute(base(centroid_sigma_arcsec=None, neighbours=no_pix))
    assert not f.pixels and f.confusion_radius_arcsec == 3.98 and f.label() == "False positive (no pixel data yet)"
    assert f.neighbours[1]["exclusion"] == pytest.approx(math.exp(-(10.8 - 3.98) ** 2 / (2 * 1.3 ** 2)))


def test_a_deep_secondary_raises_the_eb_weight():
    clean, dirty = compute(base()), compute(base(secondary_ppm=400.0, secondary_err_ppm=80.0))
    assert dirty.scenarios[2].factors["S_sec"] > clean.scenarios[2].factors["S_sec"] and dirty.value > clean.value


def test_a_grazing_fit_halves_the_v_shape_factor():
    assert compute(base(fit_b=0.95)).scenarios[2].factors["S_v"] == 0.5
    assert compute(base(fit_b=0.3)).scenarios[2].factors["S_v"] == 1.0


def test_missing_fit_and_star_use_the_fallbacks():
    f = compute(base(teff=None, radius=None, mass=None, fit_k=None, fit_b=None, fit_a_over_r=None, rp_rearth=None))
    assert f.scenarios[0].factors["D_shape"] == 1.0 and f.scenarios[0].factors["class"] == "FGK"


@pytest.mark.parametrize("value,words", [(0.004, "under 1 percent"), (0.034, "about 3 percent"),
                                         (0.27, "about 30 percent"), (0.95, "over 90 percent")])
def test_words(value, words):
    f = compute(base()); assert Fpp(**{**f.__dict__, "value": value}).words() == words


def test_as_dict_is_json_ready_and_carries_every_factor():
    d = compute(base()).as_dict(); json.dumps(d)
    assert {s["name"] for s in d["scenarios"]} == {"PL", "BEB", "EB"} and "S_sec" in d["scenarios"][2]["factors"]
