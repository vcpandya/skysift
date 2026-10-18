from dataclasses import replace

import numpy as np

from skysift.config import Thresholds
from skysift.lc.ops import apply_mask, detrend
from skysift.models import LightCurve, Signal, Target, TestStatus
from skysift.vetting.base import DataBundle
from skysift.vetting.rung1_events import METRIC_LIST_MAX, Rung1Events
from tests.synthetic import (
    DC_DROP,
    DC_DUR,
    DC_P,
    DC_T0,
    FQ_DUR,
    FQ_P,
    FQ_T0,
    duty_cycle_planet,
    four_quarter_planet,
    inject_transit,
    make_lightcurve,
)

TGT = Target(source="test", target_id="t", ra=0, dec=0)
NOISE = 600


def bundle(lc):
    return DataBundle(lc=lc, fnorm=detrend(lc), fnorm_alt=detrend(lc, flux=lc.flux_alt), target=TGT, thresholds=Thresholds())


def signal(period, t0, duration, depth_ppm=3000.0):
    return Signal(target_id="t", detector="box_dip", detector_version="1", period=period, t0=t0, duration=duration,
                  depth_ppm=depth_ppm, snr=20.0, sde=15.0, n_events=3)


def inject_per_epoch(lc, period, t0, duration, depths_ppm):
    """``lc`` with a box of ``duration`` at every epoch, the depth of epoch ``e`` being ``depths_ppm[e]`` (0 when absent)."""
    ep = np.round((lc.time - t0) / period).astype(int)
    ph = lc.time - t0 - ep * period
    depth = np.array([depths_ppm.get(int(e), 0.0) for e in ep]) * 1e-6
    factor = np.where(np.abs(ph) < 0.5 * duration, 1.0 - depth, 1.0)
    return LightCurve(lc.time, lc.flux * factor, lc.flux_err, lc.quality, lc.quarter, lc.flux_alt * factor, dict(lc.meta))


def thin(lc, keep):
    return LightCurve(lc.time[keep], lc.flux[keep], lc.flux_err[keep], lc.quality[keep], lc.quarter[keep],
                      lc.flux_alt[keep], dict(lc.meta))


def test_passes_a_clean_long_period_planet():
    lc = inject_transit(make_lightcurve(seed=17, noise_ppm=NOISE), 40.0, 140.0, 0.25, 3000)
    r = Rung1Events().run(signal(40.0, 140.0, 0.25), bundle(lc))
    assert r.status == TestStatus.PASS, r.reason
    assert r.metrics["n_good"] >= 3 and r.metrics["ses_rules_applied"] is True
    assert r.metrics["chases"] is None and r.metrics["chases_median"] is None


def test_fails_gapped_events():
    period, t0, dur = 40.0, 140.0, 0.25
    lc = inject_transit(make_lightcurve(seed=17, noise_ppm=NOISE), period, t0, dur, 3000)
    ep = np.round((lc.time - t0) / period).astype(int)
    near = np.abs(lc.time - t0 - ep * period) < dur
    # no point within one duration of any event but two: those epochs are expected transits with coverage 0
    r = Rung1Events().run(signal(period, t0, dur), bundle(thin(lc, ~(near & ~np.isin(ep, [3, 7])))))
    assert r.status == TestStatus.FAIL and r.reason.startswith("gapped events"), r.reason
    assert r.metrics["n_events_with_data"] > 30 and r.metrics["n_covered"] == 2
    assert r.metrics["coverage"].count(0.0) == r.metrics["n_events_with_data"] - 2


def test_fails_sparsely_covered_events():
    period, t0, dur = 40.0, 140.0, 0.25
    lc = inject_transit(make_lightcurve(seed=17, noise_ppm=NOISE), period, t0, dur, 3000)
    ep = np.round((lc.time - t0) / period).astype(int)
    near = np.abs(lc.time - t0 - ep * period) < dur
    # every event but two keeps one point in five of its window: present, but at a coverage of about 0.2
    sparse = near & ~np.isin(ep, [3, 7]) & (np.arange(lc.time.size) % 5 != 0)
    r = Rung1Events().run(signal(period, t0, dur), bundle(thin(lc, ~sparse)))
    assert r.status == TestStatus.FAIL and r.reason.startswith("gapped events"), r.reason
    assert r.metrics["n_covered"] <= 0.5 * r.metrics["n_events_with_data"]


def test_fails_when_one_event_dominates_a_long_period_signal():
    period, t0, dur = 95.0, 150.0, 0.25
    lc = inject_per_epoch(make_lightcurve(n_days=300, seed=17, noise_ppm=NOISE), period, t0, dur,
                          {0: 6000.0, 1: 400.0, 2: 400.0})
    r = Rung1Events().run(signal(period, t0, dur), bundle(lc))
    ratio = r.metrics["ses_max_over_mes"]
    assert r.metrics["n_good"] == 3 and ratio > 0.8, (r.metrics, ratio)
    assert r.status == TestStatus.FAIL and r.reason.startswith("single event dominates"), r.reason


def test_dominance_rule_does_not_apply_below_90_days():
    period, t0, dur = 40.0, 150.0, 0.25
    lc = inject_per_epoch(make_lightcurve(n_days=130, seed=17, noise_ppm=NOISE), period, t0, dur,
                          {0: 6000.0, 1: 400.0, 2: 400.0})
    r = Rung1Events().run(signal(period, t0, dur), bundle(lc))
    assert r.metrics["ses_max_over_mes"] > 0.8, r.metrics
    assert not r.reason or not r.reason.startswith("single event"), r.reason


def test_fails_chases_when_events_sit_in_comparable_noise():
    period, t0, dur = 30.0, 145.0, 0.25
    base = make_lightcurve(n_days=130, seed=17, noise_ppm=NOISE)
    lc = inject_per_epoch(base, period, t0, dur, {e: 3000.0 for e in range(4)})
    lc = inject_per_epoch(lc, period, t0 + 1.2, dur, {e: 3000.0 for e in range(4)})
    r = Rung1Events().run(signal(period, t0, dur), bundle(lc))
    assert r.metrics["chases"] is not None and len(r.metrics["chases"]) == 4
    assert r.status == TestStatus.FAIL and r.reason.startswith("chases median"), (r.reason, r.metrics)


def test_short_period_signal_skips_the_ses_rules():
    period, t0, dur = 0.6, 140.0, 1 / 24
    lc = inject_transit(make_lightcurve(seed=17, noise_ppm=NOISE), period, t0, dur, 2000)
    r = Rung1Events().run(signal(period, t0, dur, 2000.0), bundle(lc))
    assert r.status == TestStatus.PASS, r.reason
    assert r.metrics["ses_rules_applied"] is False and r.metrics["ses_max_over_mes"] is None


def test_caps_the_per_epoch_lists_on_short_periods():
    """More than ``METRIC_LIST_MAX`` expected epochs store summaries instead of one value per epoch."""
    period, t0, dur = 0.6, 140.0, 1 / 24
    lc = inject_transit(make_lightcurve(seed=17, noise_ppm=NOISE), period, t0, dur, 2000)
    m = Rung1Events().run(signal(period, t0, dur, 2000.0), bundle(lc)).metrics
    assert m["n_events_with_data"] > METRIC_LIST_MAX
    assert m["coverage"] is None and m["ses"] is None and m["chases"] is None
    cs, ss = m["coverage_summary"], m["ses_summary"]
    assert cs["n"] == m["n_events_with_data"] and cs["below_min"] == m["n_events_with_data"] - m["n_covered"]
    assert 0.0 <= cs["min"] <= cs["median"] and set(ss) == {"n_finite", "min", "median", "max"}
    assert ss["n_finite"] <= cs["n"]
    long = Rung1Events().run(signal(40.0, 140.0, 0.25), bundle(inject_transit(make_lightcurve(seed=17, noise_ppm=NOISE),
                                                                             40.0, 140.0, 0.25, 3000))).metrics
    assert len(long["coverage"]) == long["n_events_with_data"] <= METRIC_LIST_MAX
    assert long["coverage_summary"] is None and long["ses_summary"] is None


def test_passes_a_planet_observed_in_four_quarters():
    """A star observed in one season of four quarters: transits in the quarters it never had are not expected."""
    lc = four_quarter_planet()
    r = Rung1Events().run(signal(FQ_P, FQ_T0, FQ_DUR), bundle(lc))
    assert r.status == TestStatus.PASS, (r.reason, r.metrics["n_covered"], r.metrics["n_events_with_data"])
    assert not (r.reason or "").startswith("gapped events")
    assert r.metrics["n_events_with_data"] < 40 and r.metrics["n_good"] >= 25


def test_a_detector_mask_is_not_missing_data():
    """A second-pass signal is vetted with the first-pass eclipses NaN; ``fnorm_unmasked`` tells the rung which
    cadences were observed, so a mask of 40 percent of the points is not read as missing data. Without it the mask
    lowers the duty cycle to about 0.6 and empties the events it covers (measured: duty 0.602, 111 of 189 covered,
    which since the duty-cycle coverage no longer fails the gapped-events rule); with it the duty cycle is 1.0 and
    183 of 189 events are covered."""
    period, t0, dur = 2.1, 132.3, 2.2 / 24
    lc = inject_transit(make_lightcurve(n_days=400, seed=17, noise_ppm=NOISE), period, t0, dur, 1500)
    b = bundle(lc)
    mask = [{"period": 0.72, "t0": 131.9, "half_primary_days": 0.144, "half_secondary_days": 0.0}]
    masked = replace(b, fnorm=apply_mask(lc.time, b.fnorm, mask))
    r = Rung1Events().run(signal(period, t0, dur, 1500.0), masked)
    assert r.metrics["duty_cycle"] < 0.7, r.metrics["duty_cycle"]                       # the mask read as missing data
    assert r.metrics["n_covered"] < 0.7 * r.metrics["n_events_with_data"], r.metrics["n_covered"]
    r = Rung1Events().run(signal(period, t0, dur, 1500.0), replace(masked, fnorm_unmasked=b.fnorm))
    assert r.status == TestStatus.PASS, r.reason
    assert r.metrics["duty_cycle"] > 0.95 and r.metrics["n_covered"] >= 0.9 * r.metrics["n_events_with_data"]


def test_passes_a_planet_on_a_star_with_a_low_duty_cycle():
    """35 percent of the cadences removed at random (a star whose quality mask keeps 65 percent, as many real KBonus
    curves do): coverage is measured against the star's own duty cycle, so the events are covered and the planet
    passes. The gapped-events tests above still fail: they empty whole event windows, which no duty cycle excuses."""
    lc = duty_cycle_planet()
    r = Rung1Events().run(signal(DC_P, DC_T0, DC_DUR, 2000.0), bundle(lc))
    assert r.status == TestStatus.PASS, (r.reason, r.metrics["n_covered"], r.metrics["n_events_with_data"])
    assert not (r.reason or "").startswith("gapped events")
    assert abs(r.metrics["duty_cycle"] - (1 - DC_DROP)) < 0.05, r.metrics["duty_cycle"]
    assert r.metrics["n_covered"] > 0.8 * r.metrics["n_events_with_data"]
