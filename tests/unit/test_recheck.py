"""The stage-2 recheck, steps 1 to 3 (mission control spec 4.3): ladder re-vet, half-period re-vet, catalogue retry.

The store is seeded the way the sweep writes it (``record_results``), with the raw file kept through the blob and read
back by the ``fake`` source's parser (the pickled curve). Pre-whitening is off in this environment: the whitener
removes the 23rd to 49th harmonics of a synthetic box transit, which no restore puts back, so a clean planet would be
vetted on a distorted flux and the outcomes would test the whitener, not the recheck."""
import math
import pickle

import pytest
from sqlalchemy import select

from skysift import pipeline
from skysift import recheck as recheck_mod
from skysift.config import load_settings
from skysift.detectors.base import get_detector
from skysift.models import Signal, Target, TestResult, TestStatus
from skysift.net import ServiceUnavailable
from skysift.recheck import PIXEL_REASON_PREFIXES, RecheckReport, pixels_qualify, recheck
from skysift.sources import PARSERS
from skysift.store.blob import blob_from_url
from skysift.store.db import QueryRow, RunRow, SignalRow, Store, make_job_key, status_from_tests
from skysift.vetting.rung2_shape import Rung2Shape
from tests.synthetic import inject_transit, make_lightcurve
from tests.unit import fake_fetch

LC_RUNGS = ("rung1_detection", "rung1_events", "rung2_shape", "rung3_stability", "rung4_novelty")
HALVING = "equal secondary at phase 0.5: true period may be P / 2 (8 d)"
STEPS_NONE = {"ladder": 0, "period": 0, "catalogue": 0, "pixels": 0}


def outage_fetch(url, *, service, **_kw):
    raise ServiceUnavailable(f"{service}: HTTP 503")


class Env:
    def __init__(self, monkeypatch):
        self.monkeypatch = monkeypatch
        self.settings = load_settings()
        self.settings.data_dir.mkdir(parents=True, exist_ok=True)
        self.store = Store(self.settings.database_url); self.store.init_schema()
        self.blob = blob_from_url(self.settings.blob_url)
        self.version = pipeline.search_version(get_detector("box_dip"), self.settings.thresholds)
        monkeypatch.setitem(PARSERS, "fake", pickle.loads)        # the fake source's raw file is the pickled curve
        self.n = 0

    def seed_signal(self, ladder_version: str = "4", tests=(), metrics: dict | None = None, raw_file: bool = True,
                    source_down: bool = False, lc=None, period: float = 10.0, t0: float = 140.0,
                    duration: float = 0.12, depth_ppm: float = 3000.0, version: str | None = None) -> int:
        """One target's sweep result, written by ``record_results``: every light-curve rung passes unless ``tests``
        ((rung, status, reason) tuples, with ``metrics`` per rung) says otherwise. Without ``raw_file`` the curve must
        be re-fetched, which the ``fake`` source cannot do (it has no live adapter: ``source_down``)."""
        assert raw_file or source_down, "the fake source has no live adapter: a signal without its file is unloadable"
        self.n += 1
        tid = f"t{self.n}"
        version = version or self.version
        lc = lc if lc is not None else inject_transit(make_lightcurve(noise_ppm=400, seed=3), period, t0, duration,
                                                      depth_ppm)
        self.store.upsert_target(Target(source="fake", target_id=tid, ra=10.0 + self.n, dec=20.0, gmag=16.5))
        if raw_file:
            data = pickle.dumps(lc)
            sha, loc = self.blob.put(data, f"{tid}.fits")
            self.store.add_artifact(sha, len(data), f"https://fake/{tid}", loc, tid, kind="raw")
        rid = self.store.start_run(make_job_key(tid, "box_dip", version, ladder_version), tid, "box_dip", version,
                                   ladder_version)
        sig = Signal(target_id=tid, detector="box_dip", detector_version=version, period=period, t0=t0,
                     duration=duration, depth_ppm=depth_ppm, snr=40.0, sde=20.0, n_events=int(1400 / period),
                     extra={"detrend": pipeline.detrend_settings(),
                            "search_window": {"min_period_days": 0.5, "max_period_days": 200.0}})
        rows = {rung: TestResult(rung=rung, status=TestStatus.PASS) for rung in LC_RUNGS}
        for rung, status, reason in tests:
            rows[rung] = TestResult(rung=rung, status=TestStatus(status), reason=reason,
                                    metrics=(metrics or {}).get(rung, {}))
        dumped = [r.model_dump() for r in rows.values()]
        item = {"signal": sig.model_dump(), "outcome": status_from_tests(dumped), "tests": dumped,
                "timings": [0.0] * len(dumped)}
        self.store.record_results(rid, tid, [item], [], pin_evidence=True)
        with self.store.session() as s:
            return s.scalar(select(SignalRow.id).where(SignalRow.run_id == rid))

    def seed_doubled_planet(self, child_reason: str | None = None, lc=None) -> int:
        """An 8 d, 2500 ppm, 0.1 d planet stored as a 16 d signal whose rung 2 found the equal secondary at phase 0.5.
        ``child_reason`` makes rung 2 give that undetermined reason on the P/2 signal (a fold that is still equal)."""
        if child_reason is not None:
            original = Rung2Shape.run

            def still_equal(rung, s, b):
                if s.extra.get("halving"):
                    return TestResult(rung=rung.name, status=TestStatus.UNDETERMINED, reason=child_reason,
                                      metrics={"suggested_period": s.period / 2})
                return original(rung, s, b)
            self.monkeypatch.setattr(Rung2Shape, "run", still_equal)
        if lc is None:
            lc = inject_transit(make_lightcurve(noise_ppm=400, seed=7), 8.0, 140.0, 0.1, 2500.0)
        return self.seed_signal(lc=lc, period=16.0, t0=140.0, duration=0.1, depth_ppm=2500.0,
                                tests=[("rung2_shape", "undetermined", HALVING)],
                                metrics={"rung2_shape": {"suggested_period": 8.0}})

    def seed_doubled_binary(self) -> int:
        """Alternating 3000 and 1500 ppm eclipses 8 d apart, stored as one 16 d signal with the P/2 suggestion."""
        lc = make_lightcurve(noise_ppm=400, seed=7)
        lc = inject_transit(inject_transit(lc, 16.0, 140.0, 0.1, 3000.0), 16.0, 148.0, 0.1, 1500.0)
        return self.seed_doubled_planet(lc=lc)


@pytest.fixture
def env(settings, monkeypatch):
    monkeypatch.setenv("SKYSIFT_THRESHOLDS__PREWHITEN_MAX_COMPONENTS", "0")
    return Env(monkeypatch)


def test_ladder_revet_resolves_too_few_events_under_the_new_ladder(env):
    sid = env.seed_signal(ladder_version="3", tests=[("rung2_shape", "undetermined", "too few odd/even events")])
    rep = recheck(env.store, env.blob, env.settings, fetch=fake_fetch.fetch, pixels=False)
    assert rep.steps["ladder"] == 1 and env.store.rechecks(sid)[0]["step"] == "ladder"
    assert env.store.signal_status(sid) in ("candidate_stage1", "rejected", "undetermined")
    assert env.store.latest_tests(sid)[0]["recheck_id"] is not None
    # every rung of ladder 4 wrote a row that points at the recheck, and the status is the one those rows give
    latest = env.store.latest_tests(sid)
    assert {t["rung"] for t in latest} == set(LC_RUNGS) and len({t["recheck_id"] for t in latest}) == 1
    assert env.store.rechecks(sid)[0]["ladder_version"] == pipeline.DEFAULT_LADDER_VERSION
    assert env.store.signal_status(sid) == "candidate_stage1"         # a clean planet once the stacked fold measures it
    assert rep.considered == 1 and rep.outcomes["candidate_stage1"] == 1


def test_halving_supersedes_a_period_doubled_signal(env):
    # synthetic 8 d planet, stored signal at 16 d with rung2 undetermined and suggested_period = 8
    sid = env.seed_doubled_planet()
    rep = recheck(env.store, env.blob, env.settings, fetch=fake_fetch.fetch, pixels=False)
    assert rep.steps["period"] == 1 and env.store.signal_status(sid) == "superseded"
    child = env.store.children(sid)[0]
    assert child["branch"] == "recheck:P/2" and abs(child["period"] - 8.0) < 0.01 and child["parent_signal_id"] == sid
    assert child["final_status"] == "candidate_stage1"
    assert child["ladder_version"] == pipeline.DEFAULT_LADDER_VERSION and child["extra"]["parent_period"] == 16.0
    row = env.store.rechecks(sid)[-1]
    assert row["step"] == "period" and row["outcome"] == "superseded" and row["new_signal_id"] == child["id"]
    assert {t["recheck_id"] for t in env.store.latest_tests(child["id"])} == {row["id"]}
    assert rep.outcomes["superseded"] == 1
    with env.store.session() as s:          # the P/2 signal has its own finished run, outside the sweep's job keys
        run = s.get(RunRow, child["run_id"])
        assert run.job_key == f"recheck:P/2:{sid}" and run.status == "done" and run.ladder_version == "4"


def test_halving_rejects_an_eclipsing_binary_at_half_period(env):
    sid = env.seed_doubled_binary()         # alternating 3000 / 1500 ppm dips at 8 d stored as a 16 d signal
    recheck(env.store, env.blob, env.settings, fetch=fake_fetch.fetch, pixels=False)
    child = env.store.children(sid)[0]
    assert child["final_status"] == "rejected" and "odd_even" in env.store.latest_tests(child["id"])[2]["reason"]
    assert env.store.signal_status(sid) == "superseded"      # not undetermined for the same reason: the fix stands


def test_halving_does_not_supersede_when_the_half_period_is_undetermined_for_the_same_reason(env):
    sid = env.seed_doubled_planet(child_reason="equal secondary at phase 0.5: true period may be P / 2 (4 d)")
    recheck(env.store, env.blob, env.settings, fetch=fake_fetch.fetch, pixels=False)
    assert env.store.signal_status(sid) == "undetermined"
    assert env.store.rechecks(sid)[-1]["outcome"] == "undetermined"
    child = env.store.children(sid)[0]
    assert child["final_status"] == "undetermined"
    # the next recheck neither halves the parent again nor the child (it is a P/2 signal already)
    rep = recheck(env.store, env.blob, env.settings, fetch=fake_fetch.fetch, pixels=False)
    assert rep.steps["period"] == 0 and len(env.store.children(sid)) == 1 and env.store.children(child["id"]) == []
    assert env.store.rechecks(child["id"])[-1]["reason"] == "already re-measured at half the period"


def test_halving_skips_when_the_half_period_cannot_be_measured(env, monkeypatch):
    sid = env.seed_doubled_planet()
    monkeypatch.setattr(recheck_mod, "halved_signal", lambda sig, lc, fnorm: None)
    rep = recheck(env.store, env.blob, env.settings, fetch=fake_fetch.fetch, pixels=False)
    assert rep.steps["period"] == 0 and env.store.signal_status(sid) == "undetermined" and env.store.children(sid) == []
    rows = env.store.rechecks(sid)
    assert [(r["step"], r["outcome"], r["reason"]) for r in rows] == [("period", "skipped",
                                                                       "half period not measurable")]
    recheck(env.store, env.blob, env.settings, fetch=fake_fetch.fetch, pixels=False)
    assert len(env.store.rechecks(sid)) == 1               # the same skip is not written twice in a row


def test_catalogue_retry_after_an_outage(env):
    sid = env.seed_signal(tests=[("rung4_novelty", "undetermined", "outage:exoarchive: HTTP 403")])
    rep = recheck(env.store, env.blob, env.settings, fetch=fake_fetch.fetch, pixels=False)
    assert rep.steps["catalogue"] == 1 and env.store.latest_tests(sid)[-1]["status"] == "pass"
    assert env.store.signal_status(sid) == "candidate_stage1"
    assert rep.steps["ladder"] == 0                       # vetted under ladder 4 already: rung 4 alone runs
    assert [(r["step"], r["outcome"]) for r in env.store.rechecks(sid)] == [("catalogue", "pass")]
    with env.store.session() as s:                        # every catalogue request is logged under its target
        assert {q.target_id for q in s.scalars(select(QueryRow))} == {"t1"}


def test_catalogue_retry_that_fails_again_stays_undetermined(env):
    sid = env.seed_signal(tests=[("rung4_novelty", "undetermined", "outage:exoarchive: HTTP 403")])
    recheck(env.store, env.blob, env.settings, fetch=outage_fetch, pixels=False)
    assert env.store.signal_status(sid) == "undetermined" and len(env.store.rechecks(sid)) == 1
    row = env.store.rechecks(sid)[0]
    assert row["outcome"] == "undetermined" and row["reason"].startswith("outage:exoarchive")


def test_recheck_skips_a_signal_whose_curve_cannot_be_loaded(env):
    sid = env.seed_signal(ladder_version="3", raw_file=False, source_down=True)
    rep = recheck(env.store, env.blob, env.settings, fetch=fake_fetch.fetch, pixels=False)
    assert rep.outcomes["skipped"] == 1 and "light curve unavailable" in env.store.rechecks(sid)[0]["reason"]
    assert env.store.rechecks(sid)[0]["step"] == "ladder" and env.store.rechecks(sid)[0]["outcome"] == "skipped"
    assert env.store.signal_status(sid) == "candidate_stage1" and rep.steps == STEPS_NONE


def test_recheck_skips_a_signal_of_another_search_version(env):
    sid = env.seed_signal(ladder_version="3", version="0+00000000")
    rep = recheck(env.store, env.blob, env.settings, fetch=fake_fetch.fetch, pixels=False,
                  detector_version="0+00000000")
    row = env.store.rechecks(sid)[0]
    assert rep.considered == 1 and rep.outcomes["skipped"] == 1 and rep.steps == STEPS_NONE
    assert (row["step"], row["outcome"]) == ("ladder", "skipped")
    assert row["reason"].startswith("search version differs: ")
    assert env.store.signal_status(sid) == "candidate_stage1" and env.store.latest_tests(sid)[0]["recheck_id"] is None


def test_recheck_skips_signals_with_a_reviewer_decision(env):
    sid = env.seed_signal(ladder_version="3"); env.store.decide(sid, "approve")
    assert recheck(env.store, env.blob, env.settings, fetch=fake_fetch.fetch, pixels=False).considered == 0


def test_recheck_is_idempotent(env):
    env.seed_signal(ladder_version="3")
    recheck(env.store, env.blob, env.settings, fetch=fake_fetch.fetch, pixels=False)
    before = env.store.counts()
    rep = recheck(env.store, env.blob, env.settings, fetch=fake_fetch.fetch, pixels=False)
    assert rep.steps == {"ladder": 0, "period": 0, "catalogue": 0, "pixels": 0} and env.store.counts() == before


def test_recheck_takes_only_the_named_signals(env):
    first = env.seed_signal(tests=[("rung4_novelty", "undetermined", "outage:exoarchive: HTTP 403")])
    second = env.seed_signal(tests=[("rung4_novelty", "undetermined", "outage:exoarchive: HTTP 403")])
    rep = recheck(env.store, env.blob, env.settings, fetch=fake_fetch.fetch, pixels=False, signal_ids=[second])
    assert rep.considered == 1 and env.store.rechecks(first) == [] and len(env.store.rechecks(second)) == 1


def test_recheck_without_a_finished_sweep_has_nothing_to_do(env):
    rep = recheck(env.store, env.blob, env.settings, fetch=fake_fetch.fetch, pixels=False)
    assert rep.considered == 0 and rep.as_dict()["steps"] == STEPS_NONE and rep.as_dict()["seconds"] >= 0


def test_report_as_dict_is_plain_json():
    rep = RecheckReport(considered=2)
    rep.steps["ladder"] += 1; rep.outcomes["skipped"] += 1
    d = rep.as_dict()
    assert d == {"considered": 2, "steps": {"ladder": 1, "period": 0, "catalogue": 0, "pixels": 0},
                 "outcomes": {"candidate_stage1": 0, "candidate_stage2": 0, "undetermined": 0, "rejected": 0,
                              "superseded": 0, "skipped": 1}, "seconds": 0.0}
    d["steps"]["ladder"] = 9
    assert rep.steps["ladder"] == 1 and math.isfinite(d["seconds"])


def test_pixel_stage_takes_stage_1_candidates_and_pixel_reasons_only():
    undetermined = [{"rung": "rung3_stability", "status": "undetermined",
                     "reason": "season-dependent depth (4.1 sigma)"}]
    assert pixels_qualify("candidate_stage1", [])
    assert pixels_qualify("undetermined", undetermined)
    assert not pixels_qualify("undetermined", [{"rung": "rung2_shape", "status": "undetermined",
                                                "reason": "too few odd/even events"}])
    assert not pixels_qualify("rejected", undetermined) and not pixels_qualify("superseded", [])
    assert PIXEL_REASON_PREFIXES == ("season-dependent depth", "aperture-dependent depth", "systematic_alias",
                                     "period matches")


def test_the_pixel_step_is_a_hook_that_writes_nothing_yet(env, monkeypatch):
    sid = env.seed_signal()
    seen = []
    monkeypatch.setattr(recheck_mod, "step_pixels", lambda ctx, sig: seen.append((sig.id, ctx.tpf_loader)) or False)
    rep = recheck(env.store, env.blob, env.settings, fetch=fake_fetch.fetch, pixels=True, tpf_loader="loader")
    assert seen == [(sid, "loader")] and rep.steps == STEPS_NONE and env.store.rechecks(sid) == []
    seen.clear()
    recheck(env.store, env.blob, env.settings, fetch=fake_fetch.fetch, pixels=False)
    assert seen == []
