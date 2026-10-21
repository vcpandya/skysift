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


def test_four_scenarios_sum_to_the_fpp():
    # The factor table of spec 4.2 for KIC 6146676, documented for the sign-off review.
    f = compute(base())
    pl, beb, eb_p, eb_2p = f.scenarios
    w = {s.name: s.weight for s in f.scenarios}
    assert list(w) == ["PL", "BEB", "EB-P", "EB-2P"]
    assert f.value == pytest.approx((w["BEB"] + w["EB-P"] + w["EB-2P"]) / sum(w.values()))
    assert 0 <= f.value <= 0.6
    # PL by hand. f_pl: the Dressing grid's bin centres around (2.61 Re, 40.29 d) are 2.236/2.739 Re and
    # 33.13/109.8 d; u = (log 2.61 - log 2.236) / (log 2.739 - log 2.236) = 0.763, t = 0.163. The cells as planets
    # per star per dex^2 (cell / (0.520 dex of period x 0.0969 or 0.0792 dex of radius)): 2.0-2.5 Re 27.58 -> 5.470
    # and 18.08 -> 3.583, 2.5-3.0 Re 14.51 -> 3.522 and 8.61 -> 2.088; bilinear 0.237 * (0.837 * 5.470 + 0.163 *
    # 3.583) + 0.763 * (0.837 * 3.522 + 0.163 * 2.088) = 3.733 per dex^2; times the window 0.602 x 0.269 = 0.1619
    # dex^2 gives f_pl = 0.6042 (21 October 2026 ruling: one density scale and one window for both grids).
    # R_star / a: a = (0.495 * (40.29 / 365.25)^2)^(1/3) = 0.1819 AU, R/a = 0.498 * 0.00465047 / 0.1819 = 0.01273.
    # D_shape: T14_fit = 40.29 / pi * asin(sqrt(1.048^2 - 0.885^2) / 44) = 0.1636 d against T14_circ = 0.1632 d,
    # z = 0.007, D_shape = 1.000. Weight 0.6042 * 0.01273 * 1.000 = 7.691e-3.
    assert pl.weight == pytest.approx(pl.prior * pl.geometric * pl.data)
    assert pl.weight == pytest.approx(0.6042 * 0.01273 * 1.0, rel=0.02)
    assert pl.prior == pytest.approx(0.6042, rel=0.01) and pl.geometric == pytest.approx(0.01273, rel=0.01)
    assert all(n["exclusion"] < 1e-6 for n in f.neighbours)        # k = 24.5 and 45.5 beyond r_c = 2.0"
    # BEB geometric: an equal-mass solar pair (2.0 Msun) at 2P = 80.58 d, a = (2.0 * (80.58 / 365.25)^2)^(1/3) =
    # (2.0 * 0.048671)^(1/3) = 0.46001 AU, (R1 + R2) / a = 2 * 0.00465047 / 0.46001 = 0.020219
    assert beb.factors["n_bright"] == 2 and beb.weight < 1e-6
    assert beb.geometric == pytest.approx(0.020219, rel=0.01)
    # EB prior: f_bin 0.26 (M) x g_P, the Raghavan log-normal (mu 5.03, sigma 2.28) between log10(P) = 1.6052 and
    # log10(4P) = 2.2073 for EB-2P (a factor of two around 2P): Phi(-1.2380) - Phi(-1.5021) = 0.10786 - 0.06654 =
    # 0.041315, prior 0.26 * 0.041315 = 0.010742; for EB-P between log10(P / 2) = 1.3042 and log10(2P) = 1.9062:
    # Phi(-1.3701) - Phi(-1.6341) = 0.08533 - 0.05112 = 0.03422, prior 0.26 * 0.03422 = 0.008896. Each hypothesis
    # carries the full binary fraction: they are about different orbital periods.
    # EB geometric, M1 + M2 = 0.495 * 1.5 = 0.7425 Msun: at P = 40.29 d a = (0.7425 * 0.012168)^(1/3) = 0.20828 AU,
    # (R1 + R2) / a = 0.498 * 1.5 * 0.00465047 / 0.20828 = 0.016679; at 2P = 80.58 d a = 0.33062 AU, 0.010507.
    # EB data: S_sec = 1 - Phi((216.5 - 72) / 80) = 0.03544 for EB-P, S_oe = 1 - Phi((541.25 - 87) / 199) = 0.01123
    # for EB-2P, S_v = 1 (b 0.885 < 0.9). Weights 0.008896 * 0.016679 * 0.03544 = 5.259e-6 and
    # 0.010742 * 0.010507 * 0.01123 = 1.2675e-6.
    assert eb_p.prior == pytest.approx(0.26 * 0.03422, rel=0.01) and eb_p.factors["g_P"] == pytest.approx(0.03422,
                                                                                                        rel=0.01)
    assert eb_2p.prior == pytest.approx(0.26 * 0.041315, rel=0.01) and eb_2p.factors["g_2P"] == pytest.approx(
        0.041315, rel=0.01)
    assert eb_p.geometric == pytest.approx(0.016679, rel=0.01) and eb_2p.geometric == pytest.approx(0.010507, rel=0.01)
    assert eb_p.data == pytest.approx(0.03544, rel=0.01) and eb_p.factors["S_sec"] == eb_p.data
    assert eb_2p.data == pytest.approx(0.01123, rel=0.01) and eb_2p.factors["S_oe"] == eb_2p.data
    assert eb_p.factors["S_v"] == eb_2p.factors["S_v"] == 1.0
    assert eb_p.weight == pytest.approx(5.259e-6, rel=0.01) and eb_2p.weight == pytest.approx(1.2675e-6, rel=0.01)
    # FPP: (BEB ~1e-134 + 5.259e-6 + 1.2675e-6) / (PL 7.6904e-3 + 6.526e-6) = 8.478e-4
    assert f.value == pytest.approx(8.478e-4, rel=0.01)
    # spec 4.2 as first written, kept for the sign-off: one EB at 2P with the prior in full and S_sec x S_oe,
    # 0.010742 * 0.010507 * 3.978e-4 = 4.490e-8, so (4.490e-8) / (7.6904e-3 + 4.490e-8) = 5.838e-6
    assert f.product_eb_weight == pytest.approx(4.490e-8, rel=0.01)
    assert f.product_value == pytest.approx(5.838e-6, rel=0.01)


def test_an_unlocalized_bright_neighbour_dominates_the_fpp():
    # Without a pixel stage r_c is two Kepler pixels (7.96", 21 October 2026 ruling): the G 19.77 star at 7.14" is
    # not excluded at all (X = 1) and the G 12.8 star at 10.8" keeps X = exp(-(10.8 - 7.96)^2 / (2 x 1.3^2)) = 0.092,
    # so the FPP without pixels is 3.9 percent against 0.085 percent with them: the pixel stage is what the
    # candidate needs. By hand, X_mean = (1 + 0.092) / 2 = 0.546, BEB = 0.014 x 2 x 0.020219 x 0.546 = 3.09e-4;
    # with the two on-target EB terms (5.26e-6 + 1.27e-6) FPP = (3.09e-4 + 6.53e-6) / (7.690e-3 + 3.09e-4 +
    # 6.53e-6) = 0.0394.
    unplaced = compute(no_pixels(base()))
    assert unplaced.neighbours[0]["exclusion"] == 1.0
    assert unplaced.neighbours[1]["exclusion"] == pytest.approx(0.092, rel=0.02)
    assert unplaced.value == pytest.approx(0.0394, rel=0.02) and unplaced.value > 40 * compute(base()).value
    # the G 12.8 star moved inside the two pixels: both neighbours count in full, X_mean = 1, BEB = 5.66e-4, 6.9 percent
    far = dataclasses.replace(base().neighbours[1], sep_arcsec=3.0)
    f = compute(no_pixels(base(neighbours=(base().neighbours[0], far))))
    assert f.label() == "False positive (no pixel data yet)" and not f.pixels
    assert f.neighbours[1]["bright_enough"] and f.neighbours[1]["exclusion"] == 1.0
    assert f.scenarios[1].weight > f.scenarios[2].weight
    assert f.value == pytest.approx(0.0693, rel=0.02)


def test_bright_neighbour_far_from_the_centroid_is_excluded():
    f = compute(base())
    near, far = f.neighbours
    assert far["bright_enough"] and far["exclusion"] < 1e-3
    # The brief asserted `not near["bright_enough"]`, but by the spec 4.2 rule a G 19.77 star beside a G 16.24 target
    # holds 3.7 percent of the light, over twice the 2165 ppm depth, so it can make the dip (ruling accepted). At
    # 6.9" from the centroid it is excluded by the pixels instead.
    assert near["bright_enough"] and near["flux_fraction"] == pytest.approx(0.0373, abs=1e-4)
    assert near["exclusion"] < 1e-6


def test_compute_without_pixels_uses_a_two_pixel_confusion_radius():
    f = compute(no_pixels(base()))
    assert not f.pixels and f.confusion_radius_arcsec == 7.96 and f.label() == "False positive (no pixel data yet)"
    assert f.neighbours[1]["exclusion"] == pytest.approx(math.exp(-(10.8 - 7.96) ** 2 / (2 * 1.3 ** 2)))


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
    assert [s["name"] for s in d["scenarios"]] == ["PL", "BEB", "EB-P", "EB-2P"]
    assert "S_sec" in d["scenarios"][2]["factors"] and "S_oe" in d["scenarios"][3]["factors"]
    assert d["neighbours_known"] is True and d["value_without_beb"] is None and d["value"] == compute(base()).value
    assert d["spec_product"]["value"] == pytest.approx(5.838e-6, rel=0.01)


def test_an_unknown_neighbour_list_is_not_computed():
    # a failed cone (or no target G) gives no neighbours for want of data: the BEB term would be 0 and the FPP fall,
    # so no number is given
    known, unknown = compute(base(neighbours=())), compute(base(neighbours=(), neighbours_known=False))
    assert known.words() == "under 1 percent" and known.reported_value == known.value
    assert unknown.words() == "not computed (no neighbour data)" and unknown.reported_value is None
    assert unknown.label() == "False positive (no neighbour data)"
    d = unknown.as_dict(); json.dumps(d, allow_nan=False)
    assert d["value"] is None and d["value_without_beb"] == pytest.approx(known.value)
    assert d["words"] == "not computed (no neighbour data)" and d["neighbours_known"] is False
    # an FppInputs stored before the flag existed recomputes as known
    stored = {k: v for k, v in dataclasses.asdict(base()).items() if k != "neighbours_known"}
    assert compute(FppInputs(**stored)).neighbours_known is True


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
    assert f.neighbours[1]["exclusion"] == pytest.approx(math.exp(-(10.8 - 7.96) ** 2 / (2 * 1.3 ** 2)))
    assert f.value == compute(no_pixels(base())).value
