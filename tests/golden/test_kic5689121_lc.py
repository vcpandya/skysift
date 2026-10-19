import math

import pytest

from skysift.config import Thresholds
from skysift.detectors.box_dip import BoxDip
from skysift.lc.kbonus import parse_kbonus_fits
from skysift.lc.ops import detrend, event_depths
from skysift.models import Target, TestStatus
from skysift.vetting.base import DataBundle
from skysift.vetting.rung1_detection import Rung1Detection
from skysift.vetting.rung1_events import Rung1Events
from skysift.vetting.rung2_shape import Rung2Shape
from skysift.vetting.rung3_stability import Rung3Stability
from tests.golden.fixtures import fixture_bytes

pytestmark = [pytest.mark.golden, pytest.mark.slow]
P_PUB, T0_PUB = 13.769448251493595, 2455630.245430646 - 2454833.0


def test_kic5689121_signal_and_rungs_1_to_3():
    lc = parse_kbonus_fits(fixture_bytes("kbonus_005689121.fits"))
    assert lc.meta["kic"] == 5689121, lc.meta["kic"]
    assert lc.meta["gaia_id"] == "2103792461826460288", lc.meta["gaia_id"]
    fn = detrend(lc)
    signals = BoxDip().detect(lc, fn, Thresholds())
    assert len(signals) >= 1, "detector returned no signal"
    sig = signals[0]
    assert abs(sig.period - 13.7695) < 0.002, f"period {sig.period}"
    assert sig.sde > Thresholds().sde_min, f"sde {sig.sde}"
    ed = event_depths(lc.time, fn, lc.quarter, P_PUB, T0_PUB, 0.0958)
    # Consistency with the published depth (1883 ppm, measured with a 1-day detrend window; the engine uses 2 days).
    assert abs(ed.mean - 1883) < 3 * math.hypot(ed.err_scaled, 106), f"depth {ed.mean} +/- {ed.err_scaled}"
    # 69 clean events in the independent check; the 1.5 d gap-edge mask of 17 October 2026 drops those after a gap (60 left).
    assert ed.n >= 58, f"n events {ed.n}"
    # Regression pin of the engine's own measurement on fixed data; update knowingly if detrending changes
    # (2037 with DETREND_VERSION 2; 2100 since the 1.5 d gap-edge mask and mirror padding of 17 October 2026).
    assert abs(ed.mean - 2100) < 50, f"depth {ed.mean} drifted from the pinned 2100"
    tgt = Target(source="kbonus_bkg", target_id="kic-005689121", ra=285.907827, dec=40.941988, gaia_id="2103792461826460288", kic=5689121,
                 meta={"mass": 0.505, "radius": 0.507})
    b = DataBundle(lc=lc, fnorm=fn, fnorm_alt=detrend(lc, flux=lc.flux_alt), target=tgt, thresholds=Thresholds())
    r1 = Rung1Detection().run(sig, b)
    assert r1.status == TestStatus.PASS, f"rung 1: {r1.status} {r1.reason}"
    r1e = Rung1Events().run(sig, b)
    assert r1e.status == TestStatus.PASS, f"rung 1 events: {r1e.status} {r1e.reason} {r1e.metrics}"
    r2 = Rung2Shape().run(sig, b)
    assert r2.status == TestStatus.PASS, f"rung 2: {r2.status} {r2.reason} {r2.metrics}"
    assert r2.metrics["parity_sigma"] < 2, f"parity sigma {r2.metrics['parity_sigma']}"
    r3 = Rung3Stability().run(sig, b)
    assert r3.status == TestStatus.PASS, f"rung 3: {r3.status} {r3.reason} {r3.metrics}"


def test_neighbour_kic5689116_shows_no_dip():
    lc = parse_kbonus_fits(fixture_bytes("kbonus_005689116.fits"))
    ed = event_depths(lc.time, detrend(lc), lc.quarter, P_PUB, T0_PUB, 0.0958)
    assert abs(ed.mean) < 3 * ed.err_scaled, f"neighbour depth {ed.mean} +/- {ed.err_scaled}"
    assert ed.mean < 150, f"neighbour depth {ed.mean}"
