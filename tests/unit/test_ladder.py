from skysift.models import Signal, TestResult, TestStatus
from skysift.vetting.base import Ladder


class R:
    def __init__(self, name, cost, status, boom=False): self.name, self.cost, self.status, self.boom = name, cost, status, boom
    def run(self, signal, bundle):
        if self.boom: raise ValueError("kaboom")
        return TestResult(rung=self.name, status=self.status)


SIG = Signal(target_id="t", detector="d", detector_version="1", period=1, t0=0, duration=0.1, depth_ppm=1, snr=1, sde=1, n_events=3)


def test_runs_in_cost_order_and_stops_at_fail():
    seen = []
    ladder = Ladder([R("slow", 10, TestStatus.PASS), R("fast", 1, TestStatus.FAIL), R("mid", 5, TestStatus.PASS)], "1")
    outcome, _results = ladder.run(SIG, None, on_result=lambda r, dt: seen.append(r.rung))
    assert outcome == "fail" and seen == ["fast"]


def test_undetermined_continues_and_marks_outcome():
    ladder = Ladder([R("a", 1, TestStatus.UNDETERMINED), R("b", 2, TestStatus.PASS)], "1")
    outcome, results = ladder.run(SIG, None)
    assert outcome == "undetermined" and [r.rung for r in results] == ["a", "b"]


def test_exception_becomes_undetermined():
    ladder = Ladder([R("a", 1, TestStatus.PASS, boom=True)], "1")
    outcome, results = ladder.run(SIG, None)
    assert outcome == "undetermined" and results[0].reason.startswith("exception")
