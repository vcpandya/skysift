import pytest

from skysift.config import Thresholds
from skysift.detectors.box_dip import BoxDip
from skysift.lc.kbonus import parse_kbonus_fits
from skysift.lc.ops import detrend, event_depths
from skysift.models import Target, TestStatus
from skysift.vetting.base import DataBundle
from skysift.vetting.rung1_detection import Rung1Detection
from skysift.vetting.rung2_shape import Rung2Shape
from skysift.vetting.rung3_stability import Rung3Stability
from tests.golden.fixtures import fixture_bytes

pytestmark = [pytest.mark.golden, pytest.mark.slow]
P_PUB, T0_PUB = 13.769448251493595, 2455630.245430646 - 2454833.0


def test_kic5689121_signal_and_rungs_1_to_3():
    lc = parse_kbonus_fits(fixture_bytes("kbonus_005689121.fits"))
    assert lc.meta["kic"] == 5689121 and lc.meta["gaia_id"] == "2103792461826460288"
    fn = detrend(lc)
    sig = BoxDip().detect(lc, fn, Thresholds())[0]
    assert abs(sig.period - 13.7695) < 0.002 and sig.sde > 9
    ed = event_depths(lc.time, fn, lc.quarter, P_PUB, T0_PUB, 0.0958)
    assert abs(ed.mean - 1883) < 150 and ed.n >= 65
    tgt = Target(source="kbonus_bkg", target_id="kic-005689121", ra=285.907827, dec=40.941988, gaia_id="2103792461826460288", kic=5689121,
                 meta={"mass": 0.505, "radius": 0.507})
    b = DataBundle(lc=lc, fnorm=fn, fnorm_alt=detrend(lc, flux=lc.flux_alt), target=tgt, thresholds=Thresholds())
    assert Rung1Detection().run(sig, b).status == TestStatus.PASS
    r2 = Rung2Shape().run(sig, b); assert r2.status == TestStatus.PASS and r2.metrics["parity_sigma"] < 2
    r3 = Rung3Stability().run(sig, b); assert r3.status == TestStatus.PASS


def test_neighbour_kic5689116_shows_no_dip():
    lc = parse_kbonus_fits(fixture_bytes("kbonus_005689116.fits"))
    ed = event_depths(lc.time, detrend(lc), lc.quarter, P_PUB, T0_PUB, 0.0958)
    assert abs(ed.mean) < 3 * ed.err_scaled and ed.mean < 150
