import dataclasses
import json
import math

import pytest

from skysift.fpp import Fpp, FppInputs, Neighbour, compute


def base(**over) -> FppInputs:
    # KIC 6146676: P 40.29, depth 2165e-6, G 16.24, Teff 3650, R 0.498, M 0.495, rp 2.61, k 0.048, b 0.885, a/R 44.0,
    # secondary 72 +- 80 ppm, odd 2202 even 2115 err 199. The season-averaged difference centroid sits 0.19 px
    # (0.76") from the target, with sigma 0.05 px (0.2"). Two Gaia neighbours: G 19.77 at 7.14" from the target and
    # about 6.9" from the centroid; G 12.8 at 10.8" from the target and about 11.1" from the centroid.
    inputs = FppInputs(
        period=40.29, depth=2165e-6, gmag=16.24, teff=3650.0, radius=0.498, mass=0.495, rp_rearth=2.61,
        fit_k=0.048, fit_b=0.885, fit_a_over_r=44.0, secondary_ppm=72.0, secondary_err_ppm=80.0,
        odd_ppm=2202.0, even_ppm=2115.0, odd_even_err_ppm=199.0,
        neighbours=(Neighbour("near", 7.14, 19.77, None, centroid_sep_arcsec=6.9),
                    Neighbour("far", 10.8, 12.8, None, centroid_sep_arcsec=11.1)),
        centroid_sigma_arcsec=0.2)
    return dataclasses.replace(inputs, **over)


def no_pixels(inputs: FppInputs) -> FppInputs:
    return dataclasses.replace(inputs, centroid_sigma_arcsec=None, neighbours=tuple(
        dataclasses.replace(n, centroid_sep_arcsec=None) for n in inputs.neighbours))


def test_three_scenarios_sum_to_the_fpp():
    # The factor table of spec 4.2 for KIC 6146676, documented for the sign-off review.
    f = compute(base())
    pl, beb, eb = f.scenarios
    w = {s.name: s.weight for s in f.scenarios}
    assert f.value == pytest.approx((w["BEB"] + w["EB"]) / sum(w.values()))
    assert 0 <= f.value <= 0.6
    # PL by hand. f_pl: the Dressing grid's bin centres around (2.61 Re, 40.29 d) are 2.236/2.739 Re and
    # 33.13/109.8 d; u = (log 2.61 - log 2.236) / (log 2.739 - log 2.236) = 0.763, t = 0.163; rows 2.0-2.5 Re
    # (27.58, 18.08 percent) -> 0.2603 and 2.5-3.0 Re (14.51, 8.61 percent) -> 0.1355; f_pl = 0.237 * 0.2603 +
    # 0.763 * 0.1355 = 0.1651. R_star / a: a = (0.495 * (40.29 / 365.25)^2)^(1/3) = 0.1819 AU, R/a = 0.498 *
    # 0.00465047 / 0.1819 = 0.01273. D_shape: T14_fit = 40.29 / pi * asin(sqrt(1.048^2 - 0.885^2) / 44) = 0.1636 d
    # against T14_circ = 0.1632 d, z = 0.007, D_shape = 1.000. Weight 0.1651 * 0.01273 * 1.000 = 2.10e-3.
    assert pl.weight == pytest.approx(pl.prior * pl.geometric * pl.data)
    assert pl.weight == pytest.approx(0.1651 * 0.01273 * 1.0, rel=0.2)
    assert pl.prior == pytest.approx(0.1651, rel=0.01) and pl.geometric == pytest.approx(0.01273, rel=0.01)
    assert all(n["exclusion"] < 1e-6 for n in f.neighbours)        # k = 24.5 and 45.5 beyond r_c = 2.0"
    # BEB geometric: an equal-mass solar pair (2.0 Msun) at 2P = 80.58 d, a = (2.0 * (80.58 / 365.25)^2)^(1/3) =
    # (2.0 * 0.048671)^(1/3) = 0.46001 AU, (R1 + R2) / a = 2 * 0.00465047 / 0.46001 = 0.020219
    assert beb.factors["n_bright"] == 2 and beb.weight < 1e-6
    assert beb.geometric == pytest.approx(0.020219, rel=0.01)
    # EB prior: f_bin 0.26 (M) x g_P, the Raghavan log-normal (mu 5.03, sigma 2.28) between log10(P) = 1.6052 and
    # log10(4P) = 2.2073: Phi(-1.2380) - Phi(-1.5021) = 0.10786 - 0.06654 = 0.041315; prior 0.26 * 0.041315 = 0.010742.
    # EB geometric: M1 + M2 = 0.495 * 1.5 = 0.7425 Msun at 80.58 d, a = (0.7425 * 0.048671)^(1/3) = 0.33062 AU,
    # (R1 + R2) / a = 0.498 * 1.5 * 0.00465047 / 0.33062 = 0.010507.
    # EB data: S_sec = 1 - Phi((216.5 - 72) / 80) = 0.03544, S_oe = 1 - Phi((541.25 - 87) / 199) = 0.01123, S_v = 1
    # (b 0.885 < 0.9); data 3.978e-4, weight 0.010742 * 0.010507 * 3.978e-4 = 4.490e-8.
    assert eb.weight < 1e-6
    assert eb.prior == pytest.approx(0.26 * 0.041315, rel=0.01) and eb.geometric == pytest.approx(0.010507, rel=0.01)
    assert eb.factors["S_sec"] == pytest.approx(0.0354, rel=0.01) and eb.factors["S_oe"] == pytest.approx(
        0.0112, rel=0.01) and eb.factors["S_v"] == 1.0
    # FPP: (BEB ~1e-134 + EB 4.490e-8) / (PL 2.1012e-3 + 4.490e-8) = 2.137e-5
    assert f.value == pytest.approx(2.137e-5, rel=0.01)


def test_an_unlocalized_bright_neighbour_dominates_the_fpp():
    # Without a pixel stage r_c is 3.98". At its real 10.8" the G 12.8 star is far outside (X = 1e-6) and the G 19.77
    # star at 7.14" drives BEB (X = 0.052), giving only 0.7 percent; so, as ruled, the G 12.8 star sits at 3.0" here.
    unplaced = compute(no_pixels(base()))
    assert unplaced.neighbours[0]["exclusion"] > 1e3 * unplaced.neighbours[1]["exclusion"]
    far = dataclasses.replace(base().neighbours[1], sep_arcsec=3.0)
    f = compute(no_pixels(base(neighbours=(base().neighbours[0], far))))
    assert f.label() == "False positive (no pixel data yet)" and not f.pixels
    assert f.neighbours[1]["bright_enough"] and f.neighbours[1]["exclusion"] == 1.0
    assert f.scenarios[1].weight > f.scenarios[2].weight
    # The ruling asked for f.value > 0.5. The model cannot reach it: a fully unexcluded neighbour adds F_EB x
    # 2 Rsun / a(2P, 2 Msun) = 0.014 * 0.0202 = 2.8e-4 of weight against PL 2.10e-3. By hand, sum X = 1 + 0.052,
    # BEB = 0.014 * 0.0202 * 1.052 = 2.98e-4, FPP = 2.98e-4 / (2.10e-3 + 2.98e-4) = 0.124. Flagged for sign-off.
    assert f.value == pytest.approx(0.124, rel=0.02)


def test_bright_neighbour_far_from_the_centroid_is_excluded():
    f = compute(base())
    near, far = f.neighbours
    assert far["bright_enough"] and far["exclusion"] < 1e-3
    # The brief asserted `not near["bright_enough"]`, but by the spec 4.2 rule a G 19.77 star beside a G 16.24 target
    # holds 3.7 percent of the light, over twice the 2165 ppm depth, so it can make the dip (ruling accepted). At
    # 6.9" from the centroid it is excluded by the pixels instead.
    assert near["bright_enough"] and near["flux_fraction"] == pytest.approx(0.0373, abs=1e-4)
    assert near["exclusion"] < 1e-6


def test_compute_without_pixels_uses_one_pixel_confusion_radius():
    f = compute(no_pixels(base()))
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
    # 0.5 Rsun: rp = sqrt(2165e-6) * 0.5 * 109.2 = 2.54 Re; 0.5 Msun: a = (0.5 * (40.29 / 365.25)^2)^(1/3) = 0.1825 AU
    assert f.scenarios[0].factors["rp_rearth"] == pytest.approx(2.54, rel=0.01)
    assert f.scenarios[0].factors["a_au"] == pytest.approx(0.1825, rel=0.01)


@pytest.mark.parametrize("value,words", [(0.004, "under 1 percent"), (0.01, "about 1 percent"),
                                         (0.034, "about 3 percent"), (0.27, "about 30 percent"),
                                         (0.9, "about 90 percent"), (0.95, "over 90 percent")])
def test_words(value, words):
    f = compute(base()); assert Fpp(**{**f.__dict__, "value": value}).words() == words


def test_as_dict_is_json_ready_and_carries_every_factor():
    d = compute(base()).as_dict(); json.dumps(d, allow_nan=False)
    assert {s["name"] for s in d["scenarios"]} == {"PL", "BEB", "EB"} and "S_sec" in d["scenarios"][2]["factors"]


def test_a_nan_secondary_reads_as_missing():
    nan, missing = compute(base(secondary_ppm=float("nan"))), compute(base(secondary_ppm=None))
    assert nan.scenarios[2].factors["S_sec"] == missing.scenarios[2].factors["S_sec"] == 1.0
    assert nan.value == missing.value and math.isfinite(nan.value)


@pytest.mark.parametrize("field,value", [("fit_a_over_r", 0.0), ("depth", 0.0), ("period", -1.0)])
def test_invalid_inputs_raise(field, value):
    with pytest.raises(ValueError, match=field):
        compute(base(**{field: value}))


def test_without_pixels_a_stray_centroid_separation_is_ignored():
    f = compute(base(centroid_sigma_arcsec=None))        # neighbours still carry centroid_sep_arcsec
    assert not f.pixels and f.neighbours[1]["s_arcsec"] == 10.8
    assert f.neighbours[1]["exclusion"] == pytest.approx(math.exp(-(10.8 - 3.98) ** 2 / (2 * 1.3 ** 2)))
    assert f.value == compute(no_pixels(base())).value
