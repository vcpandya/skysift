"""tools/centroid_floor: the quarter-to-quarter scatter of the direct-image 3 x 3 box centroid per season (spec
2026-10-22 referee pack, section 5.1), on a nearly noiseless version of the test_pixels scene, so that every quarter
of a season puts its stars on the same frame pixels and the only scatter is the one the test builds in."""
import math

import numpy as np

from skysift.lc.pixels import QuarterData
from tests.unit.test_pixels import (
    BACKGROUND,
    DEPTH,
    EPH,
    NO_TARGET,
    POS_NEIGHBOUR,
    POS_TARGET,
    PSF_SIGMA,
    QUARTER_DAYS,
    RATIO,
    SHAPE,
    SUBPIX,
    _psf,
    _scene,
)
from tools.centroid_floor import excess_scatter, pooled_floor, season_quarter_centroids


def _quiet_quarters(layout=None, n_quarters=6) -> list[QuarterData]:
    """make_quarters' scene with 1 e- of noise per pixel (the difference images need a variance above zero): Q1 and
    Q5 share season 3, Q2 and Q6 season 0, Q3 and Q4 are alone in seasons 1 and 2."""
    layout = layout or {}
    rng = np.random.default_rng(1)
    time, quarter, f_t, f_n = _scene(QUARTER_DAYS * n_quarters, "target", DEPTH, RATIO, n_quarters)
    out = []
    for q in range(1, n_quarters + 1):
        m = quarter == q
        shape, (oc, orow) = layout.get(q, (SHAPE, (0, 0)))
        pt = (POS_TARGET[0] - oc, POS_TARGET[1] - orow); pn = (POS_NEIGHBOUR[0] - oc, POS_NEIGHBOUR[1] - orow)
        model = (f_t[m, None, None] * _psf(pt, shape, PSF_SIGMA) + f_n[m, None, None] * _psf(pn, shape, PSF_SIGMA)
                 + BACKGROUND)
        out.append(QuarterData(quarter=q, cube=model + rng.normal(size=model.shape), time=time[m],
                               quality=np.zeros(m.sum(), int), pos_target=pt, pos_neighbour=pn))
    return out


def _by_season(rows):
    return {r["season"]: r for r in rows}


def test_floor_tool_pools_complete_boxes_with_two_quarters_only():
    rows = season_quarter_centroids(_quiet_quarters(), EPH)
    by = _by_season(rows)
    assert sorted(by) == [0, 1, 2, 3]
    assert by[3]["quarters"] == [1, 5] and by[0]["quarters"] == [2, 6] and by[1]["quarters"] == [3]
    for r in rows:
        assert r["box_complete"] and r["n_quarters"] == len(r["quarters"]) == len(r["target"])
        assert len(r["neighbour"]) == len(r["n_events"]) == len(r["residual_offsets_pix"]) == len(r["quarters"])
        for star in ("target", "neighbour"):
            pts = np.array(r[star])
            assert np.ptp(pts[:, 0]) < 0.05 and np.ptp(pts[:, 1]) < 0.05
        assert r["registration_mean"] == [0.0, 0.0]
    # the target's box lies near its frame position; the neighbour's light pulls it towards the neighbour (+col)
    dc, dr = by[3]["target"][0]
    assert 0.0 < dc < 0.5 and abs(dr) < 0.2

    # season 0 with the target on the stamp's first column in both its quarters: a cut box, left out with season 1
    # and season 2 (one quarter each); season 3 is the only one pooled
    cut = season_quarter_centroids(_quiet_quarters(layout={2: NO_TARGET, 6: NO_TARGET}), EPH)
    assert _by_season(cut)[0]["box_complete"] is False
    out = pooled_floor(cut)
    assert out["n_seasons"] == 1 and out["n_quarters"] == 2
    why = {s["season"]: s["why"] for s in out["per_season"]}
    assert why == {0: "cut box", 1: "one quarter", 2: "one quarter", 3: None}
    assert all(s["pooled"] is (s["why"] is None) for s in out["per_season"])
    assert out["rms_col"] < 0.05 and out["rms_row"] < 0.05
    assert math.isclose(out["rms_axis_mean"], math.sqrt(0.5 * (out["rms_col"] ** 2 + out["rms_row"] ** 2)))
    assert {"rms_col", "rms_row", "rms_axis_mean"} <= set(out["neighbour"])
    assert out["adopted"] == max(out["rms_axis_mean"], out["neighbour"]["rms_axis_mean"])
    assert out["adopted_from"] in ("target", "neighbour")


def test_floor_tool_sees_a_sub_pixel_offset_and_reports_the_registration_residual():
    """Q5's stamp 0.3 px off: both stars' centroids move with it, by less than 0.3 px (a 3 x 3 box compresses the
    shift of a 0.9 px PSF to about 0.18 px, and the target's box also holds the neighbour's light, which moves away
    from it); the season's residual is the event-weighted mean of Q1's 0 and Q5's 0.3 px."""
    rows = season_quarter_centroids(_quiet_quarters(layout={5: SUBPIX}), EPH)
    s3 = _by_season(rows)[3]
    assert s3["residual_offsets_pix"][0] == [0.0, 0.0] and math.isclose(s3["residual_offsets_pix"][1][0], 0.3)
    w = np.array(s3["n_events"], float)
    assert math.isclose(s3["registration_mean"][0], 0.3 * w[1] / w.sum()) and s3["registration_mean"][1] == 0.0
    out = pooled_floor(rows)
    assert out["n_seasons"] == 2                       # seasons 3 and 0
    stats = {s["season"]: s for s in out["per_season"]}
    assert 0.02 < stats[3]["target_rms"][0] < 0.3 and 0.1 < stats[3]["neighbour_rms"][0] < 0.3 / math.sqrt(2)
    assert stats[3]["neighbour_rms"][1] < 0.01 and stats[0]["target_rms"][0] < 0.01
    # the neighbour's box follows the shift more closely than the blended target's: its rms is the one adopted
    assert out["adopted_from"] == "neighbour" and out["adopted"] == out["neighbour"]["rms_axis_mean"]
    # pooled over seasons 3 and 0, one degree of freedom each
    assert math.isclose(out["rms_col"], math.sqrt(0.5 * (stats[3]["target_rms"][0] ** 2
                                                         + stats[0]["target_rms"][0] ** 2)))


def test_excess_scatter_is_the_scatter_beyond_the_monte_carlo_error():
    seasons = [{"season": 0, "voting": True, "centroid_sigma_mc_pix": 0.03},
               {"season": 1, "voting": True, "centroid_sigma_pix": 0.03},
               {"season": 2, "voting": False, "centroid_sigma_pix": 0.5},
               {"season": 3, "voting": True, "box_partial": True, "centroid_sigma_pix": 0.03}]
    quarters = {0: [{"quarter": 2, "centroid": [1.0, 2.0], "sigma_mc": 0.05},
                    {"quarter": 6, "centroid": [1.2, 2.0], "sigma_mc": 0.05}],
                1: [{"quarter": 3, "centroid": [1.0, 2.0], "sigma_mc": 0.1},
                    {"quarter": 7, "centroid": [1.01, 2.01], "sigma_mc": 0.1}],
                2: [{"quarter": 4, "centroid": [0.0, 0.0], "sigma_mc": 0.1},
                    {"quarter": 8, "centroid": [5.0, 5.0], "sigma_mc": 0.1}],
                3: [{"quarter": 5, "centroid": [0.0, 0.0], "sigma_mc": 0.1},
                    {"quarter": 9, "centroid": [5.0, 5.0], "sigma_mc": 0.1}]}
    out = excess_scatter(seasons, quarters)
    by = {s["season"]: s for s in out["per_season"]}
    assert sorted(by) == [0, 1]                 # a season that does not vote, and a cut box, are left out
    # season 0: deviations +-0.1 px in col, one degree of freedom; the quarters' Monte Carlo variance 0.05^2
    assert math.isclose(by[0]["scatter_rms"][0], math.sqrt(0.02)) and by[0]["scatter_rms"][1] == 0.0
    assert math.isclose(by[0]["excess"][0], math.sqrt(0.02 - 0.0025)) and by[0]["excess"][1] == 0.0
    assert by[1]["excess"] == [0.0, 0.0]               # scatter below the Monte Carlo error: no excess
    assert by[0]["season_sigma_mc"] == 0.03 and by[1]["season_sigma_mc"] == 0.03
    # the season's centroid: the quarters' scatter over sqrt(2) against the season's Monte Carlo error
    assert math.isclose(by[0]["sem"][0], 0.1) and math.isclose(by[0]["season_excess"][0], math.sqrt(0.01 - 0.0009))
    assert by[1]["season_excess"] == [0.0, 0.0] and math.isclose(out["season_excess_max"], math.sqrt(0.0091))
    assert out["n_seasons"] == 2 and out["n_quarters"] == 4
    assert math.isclose(out["excess_col"], math.sqrt(max(0.0, (0.02 + 0.00005) / 2 - (0.0025 + 0.01) / 2)))
