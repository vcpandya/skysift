import math

import pytest

from skysift.calibrate import MIN_NULLS, FalseAlarm, false_alarm


def rows(n, tops, version="5+abc"):
    # n rows; row i has inverted top tops[2*i] and scrambled top tops[2*i+1]
    assert len(tops) == 2 * n
    return [{"target_id": f"t{i}", "search_version": version, "real": [60.0],
             "inverted": [tops[2 * i], tops[2 * i] - 1.0], "scrambled": [tops[2 * i + 1]]} for i in range(n)]


def test_false_alarm_uses_the_add_one_rule():
    r = rows(150, [8.0] * 299 + [52.0], version="v")
    fa = false_alarm(r, sde=50.0, version="v")
    assert (fa.k, fa.n) == (1, 300) and fa.fap == pytest.approx(2 / 301) and fa.calibrated
    assert fa.one_in() == 150 and fa.words() == "1 in 150"


def test_false_alarm_above_every_null_is_never_zero():
    fa = false_alarm(rows(200, [9.0] * 400, "v"), sde=50.0, version="v")
    assert fa.k == 0 and fa.fap == pytest.approx(1 / 401) and fa.words() == "1 in 400"


def test_false_alarm_below_two_hundred_nulls_is_not_calibrated():
    fa = false_alarm(rows(99, [9.0] * 198, "v"), sde=50.0, version="v")
    assert not fa.calibrated and fa.fap is None and fa.one_in() is None and fa.words() == "not yet calibrated"


def test_false_alarm_notes_a_version_mismatch_but_still_counts():
    fa = false_alarm(rows(200, [9.0] * 400, "old"), sde=50.0, version="new")
    assert fa.calibrated and not fa.version_matches and fa.versions == ("old",)
    assert fa.words() == "calibration is for an older search"


def test_false_alarm_ignores_null_tops_that_are_null():
    r = rows(200, [9.0] * 400, "v"); r[0]["inverted"] = [None]
    assert false_alarm(r, 50.0, "v").n == 399


def test_false_alarm_counts_at_or_above_and_ignores_the_real_search():
    # the real top (60) is never a null; a null exactly at the candidate's SDE counts, as rung 1 passes sde >= sde_min
    r = rows(100, [50.0] + [9.0] * 199, "v")
    fa = false_alarm(r, sde=50.0, version="v")
    assert (fa.k, fa.n) == (1, 200) and fa.calibrated and fa.version_matches and fa.versions == ("v",)


def test_false_alarm_skips_empty_and_non_finite_tops():
    r = rows(201, [9.0] * 402, "v")
    r[0]["inverted"] = []; r[1]["scrambled"] = [float("nan")]; del r[2]["inverted"]
    assert false_alarm(r, 50.0, "v").n == 399


def test_false_alarm_with_mixed_versions_lists_them_sorted():
    r = rows(100, [9.0] * 200, "b") + rows(100, [9.0] * 200, "a")
    fa = false_alarm(r, 50.0, "a")
    assert fa.n == 400 and not fa.version_matches and fa.versions == ("a", "b")


def test_one_in_rounds_to_two_significant_figures_and_words_use_thousands_separators():
    fa = FalseAlarm(k=0, n=1716, fap=1 / 1717, calibrated=True, version_matches=True, versions=("v",))
    assert fa.one_in() == 1700 and fa.words() == "1 in 1,700"
    fa = FalseAlarm(k=5, n=10, fap=6 / 11, calibrated=True, version_matches=True, versions=("v",))
    assert fa.one_in() == 2 and fa.words() == "1 in 2"
    assert MIN_NULLS == 200 and not math.isnan(fa.fap)


def test_false_alarm_with_no_rows_is_not_calibrated():
    fa = false_alarm([], 50.0, "v")
    assert (fa.k, fa.n, fa.fap, fa.calibrated, fa.versions) == (0, 0, None, False, ())
