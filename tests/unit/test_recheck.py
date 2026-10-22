"""The stage-2 recheck, steps 1 to 3 (mission control spec 4.3): ladder re-vet, half-period re-vet, catalogue retry.

The store is seeded the way the sweep writes it (``record_results``), with the raw file kept through the blob and read
back by the ``fake`` source's parser (the pickled curve). Pre-whitening is off in this environment: the whitener
removes the 23rd to 49th harmonics of a synthetic box transit, which no restore puts back, so a clean planet would be
vetted on a distorted flux and the outcomes would test the whitener, not the recheck."""
import copy
import dataclasses
import math
import pickle
from types import SimpleNamespace

import numpy as np
import pytest
from sqlalchemy import select

from skysift import pipeline
from skysift import recheck as recheck_mod
from skysift.config import load_settings
from skysift.detectors.base import get_detector
from skysift.lc.prewhiten import prewhiten_model
from skysift.models import Signal, Target, TestResult, TestStatus
from skysift.net import ServiceUnavailable
from skysift.recheck import PIXEL_REASON_PREFIXES, RecheckReport, pixels_qualify, recheck
from skysift.sources import PARSERS
from skysift.store.blob import blob_from_url
from skysift.store.db import ArtifactRow, QueryRow, RunRow, SignalRow, Store, make_job_key, status_from_tests
from skysift.vetting.rung2_shape import Rung2Shape
from tests.synthetic import _with_dip, inject_transit, make_lightcurve
from tests.unit import fake_fetch
from tests.unit.synthetic_tpf import FakeMast, listing_html, tpf_name, tpf_quarters
from tests.unit.test_pixels import EPH

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
        self.tpfs: dict[int, tuple[str, tuple[float, float]]] = {}   # Kepler target kic -> (dip on, target RA/Dec)
        self.tpf_calls: list[int] = []

    def tpf_loader(self, kic, *, fetch, blob, store, target_id, timeout=600.0):
        """The archive's pixel files of the Kepler target ``kic``: the synthetic scene seeded for it, the dip on the
        target or on the neighbour, the target at its catalogue RA/Dec."""
        from skysift.lc.tpf import parse_tpf
        self.tpf_calls.append(kic)
        mode, radec = self.tpfs[kic]
        return [parse_tpf(b, tpf_name(kic, b.quarter)) for b in tpf_quarters(mode, target_radec=radec, kic=kic)]

    def seed_signal(self, ladder_version: str = "4", tests=(), metrics: dict | None = None, raw_file: bool = True,
                    source_down: bool = False, lc=None, period: float = 10.0, t0: float = 140.0,
                    duration: float = 0.12, depth_ppm: float = 3000.0, version: str | None = None,
                    prewhitening: dict | None = None, tpfs: str | None = None, status: str | None = None,
                    window: tuple[float, float] | None = (0.5, 200.0), doubled: bool = False) -> int:
        """One target's sweep result, written by ``record_results``: every light-curve rung passes unless ``tests``
        ((rung, status, reason) tuples, with ``metrics`` per rung) says otherwise. Without ``raw_file`` the curve must
        be re-fetched, which the ``fake`` source cannot do (it has no live adapter: ``source_down``). ``tpfs`` ("target"
        or "neighbour") makes the target a KBonus star in the pixels of a Kepler target of its own whose synthetic
        pixel files put the dip on that star, the signal at the scene's ephemeris; ``status``, when given, is the
        status the seeded rows must give. ``window`` is the search window the sweep ran with (its search version and
        ``extra["search_window"]``; None stores no window); ``doubled`` stores the signal at twice the injected
        period, with rung 2's period-halving row."""
        assert raw_file or source_down, "the fake source has no live adapter: a signal without its file is unloadable"
        self.n += 1
        tid = f"t{self.n}"
        kic, meta = None, {}
        if tpfs is not None:
            kic, period, t0, duration = 6146000 + self.n, EPH.period, EPH.t0, EPH.duration
            meta = {"tpf_org": 7146000 + self.n}
            self.tpfs[meta["tpf_org"]] = (tpfs, (10.0 + self.n, 20.0))
        if version is None and window not in (None, (0.5, 200.0)):
            th = self.settings.thresholds.model_copy(update={"min_period_days": window[0],
                                                             "max_period_days": window[1]})
            version = pipeline.search_version(get_detector("box_dip"), th)
        version = version or self.version
        lc = lc if lc is not None else inject_transit(make_lightcurve(noise_ppm=400, seed=3), period, t0, duration,
                                                      depth_ppm)
        if doubled:
            halving = f"equal secondary at phase 0.5: true period may be P / 2 ({period:g} d)"
            tests = [*tests, ("rung2_shape", "undetermined", halving)]
            metrics = {**(metrics or {}), "rung2_shape": {"suggested_period": period}}
            period = 2 * period
        self.store.upsert_target(Target(source="fake", target_id=tid, ra=10.0 + self.n, dec=20.0, gmag=16.5, kic=kic,
                                        meta=meta))
        lc = dataclasses.replace(lc, meta={**lc.meta, "target_id": tid})      # one raw file per target, never shared
        if raw_file:
            data = pickle.dumps(lc)
            sha, loc = self.blob.put(data, f"{tid}.fits")
            self.store.add_artifact(sha, len(data), f"https://fake/{tid}", loc, tid, kind="raw")
        rid = self.store.start_run(make_job_key(tid, "box_dip", version, ladder_version), tid, "box_dip", version,
                                   ladder_version)
        sig = Signal(target_id=tid, detector="box_dip", detector_version=version, period=period, t0=t0,
                     duration=duration, depth_ppm=depth_ppm, snr=40.0, sde=20.0, n_events=int(1400 / period),
                     extra={"detrend": pipeline.detrend_settings(),
                            **({"search_window": {"min_period_days": window[0], "max_period_days": window[1]}}
                               if window is not None else {}),
                            **({"prewhitening": prewhitening} if prewhitening is not None else {})})
        rows = {rung: TestResult(rung=rung, status=TestStatus.PASS) for rung in LC_RUNGS}
        for rung, st, reason in tests:
            rows[rung] = TestResult(rung=rung, status=TestStatus(st), reason=reason,
                                    metrics=(metrics or {}).get(rung, {}))
        dumped = [r.model_dump() for r in rows.values()]
        item = {"signal": sig.model_dump(), "outcome": status_from_tests(dumped), "tests": dumped,
                "timings": [0.0] * len(dumped)}
        self.store.record_results(rid, tid, [item], [], pin_evidence=True)
        with self.store.session() as s:
            sid = s.scalar(select(SignalRow.id).where(SignalRow.run_id == rid))
        assert status is None or self.store.signal_status(sid) == status
        return sid

    def seed_doubled_planet(self, child_reason: str | None = None, lc=None, prewhitening: dict | None = None) -> int:
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
                                prewhitening=prewhitening, tests=[("rung2_shape", "undetermined", HALVING)],
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
    # the next recheck neither halves the parent again nor the child (it is a P/2 signal already), and writes nothing
    rows = {i: env.store.rechecks(i) for i in (sid, child["id"])}
    before = env.store.counts()
    rep = recheck(env.store, env.blob, env.settings, fetch=fake_fetch.fetch, pixels=False)
    assert rep.steps["period"] == 0 and len(env.store.children(sid)) == 1 and env.store.children(child["id"]) == []
    assert {i: env.store.rechecks(i) for i in (sid, child["id"])} == rows and env.store.counts() == before
    assert rows[child["id"]] == []


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
    """A second recheck writes nothing on the ladder, P/2 (superseded) and catalogue paths: no test, run or signal
    row (``counts``) and no rechecks row (which ``counts`` does not count)."""
    ids = [env.seed_signal(ladder_version="3"), env.seed_doubled_planet(),
           env.seed_signal(tests=[("rung4_novelty", "undetermined", "outage:exoarchive: HTTP 403")])]
    first = recheck(env.store, env.blob, env.settings, fetch=fake_fetch.fetch, pixels=False)
    assert first.steps == {"ladder": 1, "period": 1, "catalogue": 1, "pixels": 0}
    before, rows = env.store.counts(), {i: env.store.rechecks(i) for i in ids}
    rep = recheck(env.store, env.blob, env.settings, fetch=fake_fetch.fetch, pixels=False)
    assert rep.steps == {"ladder": 0, "period": 0, "catalogue": 0, "pixels": 0} and env.store.counts() == before
    assert {i: env.store.rechecks(i) for i in ids} == rows


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
                              "superseded": 0, "skipped": 1}, "seconds": 0.0, "stopped": False}
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


def test_the_pixel_step_receives_the_tpf_loader_and_runs_only_with_pixels(env, monkeypatch):
    sid = env.seed_signal()
    seen = []
    monkeypatch.setattr(recheck_mod, "step_pixels", lambda ctx, sig: seen.append((sig.id, ctx.tpf_loader)) or None)
    rep = recheck(env.store, env.blob, env.settings, fetch=fake_fetch.fetch, pixels=True, tpf_loader="loader")
    assert seen == [(sid, "loader")] and rep.steps == STEPS_NONE and env.store.rechecks(sid) == []
    seen.clear()
    recheck(env.store, env.blob, env.settings, fetch=fake_fetch.fetch, pixels=False)
    assert seen == []


@pytest.mark.parametrize("seed", ["ladder", "period"])
def test_a_step_that_fails_while_writing_leaves_nothing_and_is_redone(env, monkeypatch, seed):
    """One transaction per step: a failure inside the write leaves no rechecks row of the step, no P/2 signal and no
    run row (only the crash is recorded, as a skip with the exception's class name); the next recheck redoes it."""
    sid = env.seed_signal(ladder_version="3") if seed == "ladder" else env.seed_doubled_planet()
    before = env.store.counts()
    real = Store._add_test_row
    calls = []

    def flaky(self, *a, **kw):
        calls.append(1)
        if len(calls) == 3:
            raise RuntimeError("database went away at 0x7f00")
        return real(self, *a, **kw)
    monkeypatch.setattr(Store, "_add_test_row", flaky)
    rep = recheck(env.store, env.blob, env.settings, fetch=fake_fetch.fetch, pixels=False)
    def written(counts):            # the catalogue requests the re-vet made are logged either way: provenance
        return {k: v for k, v in counts.items() if k != "queries"}
    assert rep.steps == STEPS_NONE and written(env.store.counts()) == written(before) and env.store.children(sid) == []
    assert [(r["step"], r["outcome"], r["reason"]) for r in env.store.rechecks(sid)] == [
        (seed, "skipped", "exception: RuntimeError")]
    assert all(t["recheck_id"] is None for t in env.store.latest_tests(sid))
    monkeypatch.setattr(Store, "_add_test_row", real)
    rep = recheck(env.store, env.blob, env.settings, fetch=fake_fetch.fetch, pixels=False)
    assert rep.steps[seed] == 1 and env.store.rechecks(sid)[-1]["step"] == seed
    assert env.store.rechecks(sid)[-1]["outcome"] != "skipped"
    if seed == "period":
        assert env.store.signal_status(sid) == "superseded" and len(env.store.children(sid)) == 1
        with env.store.session() as s:
            assert [r.status for r in s.scalars(select(RunRow).where(RunRow.job_key.like("recheck:%")))] == ["done"]


def test_a_stop_request_ends_the_recheck_between_signals(env):
    first = env.seed_signal(tests=[("rung4_novelty", "undetermined", "outage:exoarchive: HTTP 403")])
    second = env.seed_signal(tests=[("rung4_novelty", "undetermined", "outage:exoarchive: HTTP 403")])
    rep = recheck(env.store, env.blob, env.settings, fetch=fake_fetch.fetch, pixels=False,
                  stop_requested=lambda: bool(env.store.rechecks(first)))
    assert rep.stopped and rep.as_dict()["stopped"] is True and rep.considered == 1
    assert len(env.store.rechecks(first)) == 1 and env.store.rechecks(second) == []
    rep = recheck(env.store, env.blob, env.settings, fetch=fake_fetch.fetch, pixels=False)
    assert not rep.stopped and env.store.signal_status(second) == "candidate_stage1"


class _NoTargets:
    name = "fake"

    def list_targets(self, flt):
        return []


def test_the_sweep_passes_its_stop_check_to_the_recheck_and_keeps_its_summary(env, monkeypatch):
    first = env.seed_signal(tests=[("rung4_novelty", "undetermined", "outage:exoarchive: HTTP 403")])
    env.seed_signal(tests=[("rung4_novelty", "undetermined", "outage:exoarchive: HTTP 403")])
    out = pipeline.run(_NoTargets(), env.store, env.blob, env.settings, filter={}, fetch_module="tests.unit.fake_fetch",
                       stop_requested=lambda: bool(env.store.rechecks(first)))
    assert out["processed"] == 0 and "stopped" not in out                # the sweep itself ran to its end
    assert out["recheck"]["stopped"] is True and out["recheck"]["considered"] == 1

    def hard_stop(*a, **kw):
        raise KeyboardInterrupt
    monkeypatch.setattr(recheck_mod, "recheck", hard_stop)
    out = pipeline.run(_NoTargets(), env.store, env.blob, env.settings, filter={}, stop_requested=lambda: False)
    assert out["processed"] == 0 and out["recheck"] == {"stopped": True, "error": "interrupted"}


def test_without_novelty_the_catalogue_retry_does_not_run(env):
    sid = env.seed_signal(tests=[("rung4_novelty", "undetermined", "outage:exoarchive: HTTP 403")])
    calls = []
    rep = recheck(env.store, env.blob, env.settings, fetch=lambda *a, **k: calls.append(1), pixels=False,
                  with_novelty=False)
    assert rep.steps == STEPS_NONE and calls == [] and env.store.rechecks(sid) == []


def test_a_signal_no_step_applies_to_is_not_loaded(env, monkeypatch):
    from skysift.lc.loader import LightCurveLoader
    sid = env.seed_signal(raw_file=False, source_down=True)       # ladder 4, every rung passed
    loads = []
    monkeypatch.setattr(LightCurveLoader, "load", lambda self, target, pin=True: loads.append(target) or None)
    rep = recheck(env.store, env.blob, env.settings, fetch=fake_fetch.fetch, pixels=False)
    assert loads == [] and env.store.rechecks(sid) == [] and rep.outcomes["candidate_stage1"] == 1


def test_a_reject_during_the_step_is_kept(env):
    """A reviewer rejects the signal while its catalogue retry runs: the step writes only a skipped row."""
    sid = env.seed_signal(tests=[("rung4_novelty", "undetermined", "outage:exoarchive: HTTP 403")])

    def deciding_fetch(url, **kw):
        if not env.store.decisions(sid):
            env.store.decide(sid, "reject", "a blend: the neighbour is the source")
        return fake_fetch.fetch(url, **kw)
    recheck(env.store, env.blob, env.settings, fetch=deciding_fetch, pixels=False)
    assert [(r["step"], r["outcome"], r["reason"]) for r in env.store.rechecks(sid)] == [
        ("catalogue", "skipped", "reviewer decided meanwhile")]
    assert env.store.signal_status(sid) == "rejected"
    assert all(t["recheck_id"] is None for t in env.store.latest_tests(sid))


def test_the_half_period_signal_is_restored_at_its_own_harmonics(env, monkeypatch):
    """A 1 d, 2500 ppm planet stored as a 2 d signal, with a 600 ppm stellar wave at 3.5 per day: the whitener removed
    the wave and the sweep's restore for the 2 d detection put it back (its seventh harmonic, shallower than the
    transit). At 1 d that wave is a half-integer harmonic and changes sign between consecutive transits. Vetted on the
    parent's replay, the P/2 signal fails rung 2 as odd_even; the recheck restores the child at its own harmonics
    only, so the wave stays removed and the child passes and supersedes the parent. The whitener is stood in for (it
    removes exactly this wave): the real one also strips a short transit's harmonics above the eighth, which no
    restore puts back, in the sweep as here."""
    th = env.settings.thresholds
    period, t0, duration, depth, f = 1.0, 140.0, 0.08, 2500.0, 3.5
    w0 = 2 * np.pi * f * t0
    comp = {"frequency": f, "amplitude_ppm": 600.0, "snr": 80.0,
            "coef": [0.0, 600e-6 * np.cos(w0), 600e-6 * np.sin(w0), 0.0, 0.0]}
    planet = inject_transit(make_lightcurve(noise_ppm=400, seed=7), period, t0, duration, depth)
    lc = _with_dip(planet, 1.0 + prewhiten_model(planet.time, [comp]))
    real = pipeline._sweep_flux

    def whitened(lc_, th_):
        flux = real(lc_, th_)                                    # whitening is off in this environment
        model = prewhiten_model(lc_.time, [comp])
        record = {"n": 1, "components": [comp], "skipped": [], "capped": False}
        return flux._replace(fn=flux.fn - model, record=record, alt=flux.alt - model, alt_record=record)
    monkeypatch.setattr(recheck_mod, "_sweep_flux", whitened)
    base = whitened(lc, th)
    _fn, _alt, stored = pipeline.restore_from(lc.time, base, [(1 / (2 * period), depth)])     # the sweep at 2 d
    assert [r["frequency"] for r in stored["restored"]] == [f]
    halving = "equal secondary at phase 0.5: true period may be P / 2 (1 d)"
    sid = env.seed_signal(lc=lc, period=2 * period, t0=t0, duration=duration, depth_ppm=depth, prewhitening=stored,
                          tests=[("rung2_shape", "undetermined", halving)],
                          metrics={"rung2_shape": {"suggested_period": period}})

    # the trap: the P/2 signal vetted on the parent's restore replay
    with env.store.session() as s:
        row = s.get(SignalRow, sid)
        parent = Signal(**{k: getattr(row, k) for k in Signal.model_fields})
    replay = pipeline.revet_flux(lc, th, parent, base=base)
    old_child = pipeline.halved_signal(parent, lc, replay[0])
    _outcome, old = pipeline.revet(lc, Target(source="fake", target_id=parent.target_id, ra=11.0, dec=20.0),
                                   th, old_child, pipeline.build_ladder(), fetch=fake_fetch.fetch, flux=replay)
    assert old[2].rung == "rung2_shape" and old[2].reason.startswith("odd_even"), old

    rep = recheck(env.store, env.blob, env.settings, fetch=fake_fetch.fetch, pixels=False)
    child = env.store.children(sid)[0]
    rung2 = env.store.latest_tests(child["id"])[2]
    assert rep.steps["period"] == 1 and rung2["rung"] == "rung2_shape" and rung2["status"] == "pass", rung2
    assert child["final_status"] == "candidate_stage1" and env.store.signal_status(sid) == "superseded"
    record = child["extra"]["prewhitening"]
    assert not record.get("restored") and [c["frequency"] for c in record["components"]] == [f]


def test_pixel_step_promotes_a_candidate_to_stage_2(env):
    sid = env.seed_signal(ladder_version="4", status="candidate_stage1", tpfs="target")
    rep = recheck(env.store, env.blob, env.settings, fetch=fake_fetch.fetch, tpf_loader=env.tpf_loader)
    assert rep.steps["pixels"] == 1 and env.store.signal_status(sid) == "candidate_stage2"
    row = env.store.rechecks(sid)[-1]
    assert (row["step"], row["outcome"]) == ("pixels", "pass") and row["ladder_version"] == "4"
    rung5 = env.store.latest_tests(sid)[-1]
    assert rung5["rung"] == "rung5_pixels" and rung5["recheck_id"] == row["id"] and rung5["metrics"]["n_tpf"] == 5
    assert rep.outcomes["candidate_stage2"] == 1
    with env.store.session() as s:          # the season arrays the dossier draws, pinned with the signal
        art = s.scalars(select(ArtifactRow).where(ArtifactRow.url == f"skysift://pixels/{sid}")).one()
        assert art.kind == "pixels" and art.pinned and art.sha256 == rung5["metrics"]["pixels_sha256"]
    before = env.store.counts()
    rep = recheck(env.store, env.blob, env.settings, fetch=fake_fetch.fetch, tpf_loader=env.tpf_loader)
    assert rep.considered == 0 and env.store.counts() == before        # stage 2 leaves the queue


def test_pixel_step_rejects_a_neighbour_dip_and_runs_for_pixel_reasons(env):
    sid = env.seed_signal(ladder_version="4", tests=[("rung3_stability", "undetermined",
                                                      "season-dependent depth (p=5.7e-04)")], tpfs="neighbour")
    recheck(env.store, env.blob, env.settings, fetch=fake_fetch.fetch, tpf_loader=env.tpf_loader)
    assert env.store.signal_status(sid) == "rejected"
    row = env.store.rechecks(sid)[-1]
    assert (row["step"], row["outcome"]) == ("pixels", "fail")
    assert row["reason"].startswith("the dip is on a neighbouring star (")


def test_pixel_step_does_not_run_for_outage_reasons(env):
    sid = env.seed_signal(ladder_version="4", tests=[("rung4_novelty", "undetermined", "outage:exoarchive: HTTP 403")])
    recheck(env.store, env.blob, env.settings, fetch=outage_fetch, tpf_loader=env.tpf_loader)
    assert [r["step"] for r in env.store.rechecks(sid)] == ["catalogue"]
    assert env.tpf_calls == []


def test_a_settled_pixel_step_is_not_run_again(env):
    """An undetermined signal whose only open question is one the pixels settle (season-dependent depth) and whose
    pixels put the dip on the target becomes a stage 2 candidate (spec 4.3, ``store.db.pixels_settle``); the next
    recheck does not fetch or localize it again."""
    sid = env.seed_signal(tests=[("rung3_stability", "undetermined", "season-dependent depth (p=5.7e-04)")],
                          tpfs="target")
    rep = recheck(env.store, env.blob, env.settings, fetch=fake_fetch.fetch, tpf_loader=env.tpf_loader)
    assert rep.steps["pixels"] == 1 and env.store.signal_status(sid) == "candidate_stage2"
    assert env.store.latest_tests(sid)[-1]["status"] == "pass"
    before, rows = env.store.counts(), env.store.rechecks(sid)
    rep = recheck(env.store, env.blob, env.settings, fetch=fake_fetch.fetch, tpf_loader=env.tpf_loader)
    assert rep.steps["pixels"] == 0 and env.store.counts() == before and env.store.rechecks(sid) == rows
    assert len(env.tpf_calls) == 1


SEASONS = [{"season": 0, "voting": True}, {"season": 1, "voting": False}]        # a row that localised
SILENCED = {"seasons_silenced": {"box incomplete: target": 3}, "n_seasons_voting": 1, "seasons": SEASONS}
ONE_SEASON = "pixels: only one season could vote (season 0: box incomplete: target)"
PASSED = "the dip is on the target in 3/3 seasons (centroid >= 17.5 sigma nearer)"
FAILED = "the dip is on a neighbouring star (62.4 sigma, 1/1 seasons)"


def _wants(env, status, reason, metrics):
    """``wants_pixels`` for a fresh signal whose rung 5 row is the given one (the rest of its ladder passes)."""
    sid = env.seed_signal(tests=[("rung5_pixels", status, reason)], metrics={"rung5_pixels": metrics})
    return recheck_mod.wants_pixels(env.store, SimpleNamespace(id=sid), env.store.latest_tests(sid))


def test_wants_pixels_reopens_any_rung5_row_below_rule_version_3(env):
    """Spec 2026-10-22 referee pack, section 5.2: the measured floor changes every season's error, so every rung 5
    row that localised (seasons in its metrics) under an older rule version reopens, whatever its status: a pass, a
    fail, an undetermined row (the box rule's among them). A row at the current version stays settled, and a row that
    never localised (no neighbour, an outage, a crash) is not reopened by the version."""
    assert recheck_mod.PIXEL_RULE_VERSION == 3
    v2 = {"seasons": SEASONS, "rule_version": 2}
    assert _wants(env, "pass", PASSED, v2) is True
    assert _wants(env, "pass", PASSED, {**v2, "rule_version": 3}) is False
    assert _wants(env, "pass", PASSED, {"seasons": SEASONS}) is True                 # no key: version 1
    assert _wants(env, "fail", FAILED, v2) is True
    assert _wants(env, "fail", FAILED, {**v2, "rule_version": 3}) is False
    assert _wants(env, "undetermined", "pixels: season 2 ambiguous (1.0 sigma)", v2) is True
    assert _wants(env, "undetermined", ONE_SEASON, SILENCED) is True
    assert _wants(env, "undetermined", ONE_SEASON, {**SILENCED, "rule_version": 3}) is False
    assert _wants(env, "undetermined", "pixels: no neighbour to test against", {}) is False
    # a signal another rung rejects is not reopened by its pixel row
    sid = env.seed_signal(tests=[("rung2_shape", "fail", "v-shaped"), ("rung5_pixels", "pass", PASSED)],
                          metrics={"rung5_pixels": v2})
    assert env.store.signal_status(sid) == "rejected"
    assert recheck_mod.wants_pixels(env.store, SimpleNamespace(id=sid), env.store.latest_tests(sid)) is False
    row = {"status": "pass", "reason": PASSED}
    assert recheck_mod.floor_reopens({**row, "metrics": v2}) is True
    assert recheck_mod.floor_reopens({**row, "status": "fail", "metrics": v2}) is True
    assert recheck_mod.floor_reopens({**row, "metrics": {**v2, "rule_version": 3}}) is False
    assert recheck_mod.floor_reopens({**row, "metrics": {}}) is False
    assert recheck_mod.floor_reopens({**row, "metrics": None}) is False
    assert recheck_mod.floor_reopens({**row, "metrics": "not a dict"}) is False
    assert recheck_mod.floor_reopens({"status": "undetermined", "reason": "outage:mast: HTTP 503",
                                      "metrics": {"n_tpf": 3}}) is False


def test_the_recheck_reruns_a_settled_pixel_row_below_the_rule_version_once(env):
    """A stage 2 candidate and a signal rung 5 rejected, both stored under rule version 2, leave the queue as
    settled; the recheck takes them back for the pixel step alone, writes rung 5 rows of the current version (the
    target's dip passes again, the neighbour's fails again) and leaves them at the statuses those rows give; the
    recheck after that writes nothing."""
    v2 = {"seasons": SEASONS, "rule_version": 2}
    passed = env.seed_signal(tpfs="target", tests=[("rung5_pixels", "pass", PASSED)], metrics={"rung5_pixels": v2})
    failed = env.seed_signal(tpfs="neighbour", tests=[("rung5_pixels", "fail", FAILED)],
                             metrics={"rung5_pixels": v2})
    assert env.store.signal_status(passed) == "candidate_stage2" and env.store.signal_status(failed) == "rejected"
    rep = recheck(env.store, env.blob, env.settings, fetch=fake_fetch.fetch, tpf_loader=env.tpf_loader)
    assert rep.steps == {"ladder": 0, "period": 0, "catalogue": 0, "pixels": 2} and len(env.tpf_calls) == 2
    for sid, status in ((passed, "pass"), (failed, "fail")):
        rung5 = env.store.latest_tests(sid)[-1]
        assert rung5["rung"] == "rung5_pixels" and rung5["status"] == status and rung5["recheck_id"] is not None
        assert rung5["metrics"]["rule_version"] == 3
        assert all(x["floor_pix"] > 0 for x in rung5["metrics"]["seasons"])
        assert [r["step"] for r in env.store.rechecks(sid)] == ["pixels"]
    assert env.store.signal_status(passed) == "candidate_stage2" and env.store.signal_status(failed) == "rejected"
    before = env.store.counts()
    rep = recheck(env.store, env.blob, env.settings, fetch=fake_fetch.fetch, tpf_loader=env.tpf_loader)
    assert rep.considered == 0 and env.store.counts() == before and len(env.tpf_calls) == 2


def test_the_recheck_reruns_a_silenced_pixel_row_once_end_to_end(env):
    """A stage-1 candidate the 21 October recheck left undetermined by the complete-box rule is reopened by the
    next recheck, which writes a rung 5 row carrying the current rule version; the recheck after that writes
    nothing."""
    sid = env.seed_signal(tpfs="target", tests=[("rung5_pixels", "undetermined", ONE_SEASON)],
                          metrics={"rung5_pixels": SILENCED})
    assert env.store.signal_status(sid) == "undetermined" and env.tpf_calls == []
    rep = recheck(env.store, env.blob, env.settings, fetch=fake_fetch.fetch, tpf_loader=env.tpf_loader)
    assert rep.steps["pixels"] == 1 and len(env.tpf_calls) == 1
    rung5 = env.store.latest_tests(sid)[-1]
    assert rung5["rung"] == "rung5_pixels" and rung5["metrics"]["rule_version"] == 3
    assert not rung5["reason"].startswith("exception:") and rung5["recheck_id"] is not None
    before, rows = env.store.counts(), env.store.rechecks(sid)
    rep = recheck(env.store, env.blob, env.settings, fetch=fake_fetch.fetch, tpf_loader=env.tpf_loader)
    assert rep.steps["pixels"] == 0 and env.store.counts() == before and env.store.rechecks(sid) == rows
    assert len(env.tpf_calls) == 1


def test_a_pixel_archive_outage_is_retried_by_the_next_recheck(env):
    sid = env.seed_signal(tpfs="target")

    def down(kic, **_kw):
        raise ServiceUnavailable("mast https://archive.stsci.edu -> HTTP 503")
    recheck(env.store, env.blob, env.settings, fetch=fake_fetch.fetch, tpf_loader=down)
    row = env.store.rechecks(sid)[-1]
    assert (row["step"], row["outcome"]) == ("pixels", "undetermined") and row["reason"].startswith("outage:mast: ")
    assert env.store.signal_status(sid) == "undetermined"
    rep = recheck(env.store, env.blob, env.settings, fetch=fake_fetch.fetch, tpf_loader=env.tpf_loader)
    assert rep.steps["pixels"] == 1 and env.store.signal_status(sid) == "candidate_stage2"


def test_a_first_pixel_crash_is_retried_once(env):
    sid = env.seed_signal(tpfs="target")
    calls = []

    def crash_then_load(kic, **kw):
        calls.append(kic)
        if len(calls) == 1:
            raise RuntimeError("bad header")
        return env.tpf_loader(kic, **kw)
    recheck(env.store, env.blob, env.settings, fetch=fake_fetch.fetch, tpf_loader=crash_then_load)
    assert env.store.latest_tests(sid)[-1]["reason"].startswith("exception: ")
    assert env.store.signal_status(sid) == "undetermined"
    rep = recheck(env.store, env.blob, env.settings, fetch=fake_fetch.fetch, tpf_loader=crash_then_load)
    assert rep.steps["pixels"] == 1 and len(calls) == 2 and env.store.signal_status(sid) == "candidate_stage2"


def test_a_second_pixel_crash_stays_for_a_person(env):
    sid = env.seed_signal(tpfs="target")
    calls = []

    def always_crash(kic, **kw):
        calls.append(kic)
        raise RuntimeError("bad header")
    for _ in range(3):
        recheck(env.store, env.blob, env.settings, fetch=fake_fetch.fetch, tpf_loader=always_crash)
    assert len(calls) == 2 and env.store.signal_status(sid) == "undetermined"
    assert [r["step"] for r in env.store.rechecks(sid)] == ["pixels", "pixels"]


def test_the_pixel_step_end_to_end_through_fetch_tpfs(env):
    """The default loader (``fetch_tpfs``) inside a recheck: the stamped fetch reaches a fake MAST, the files go into
    the blob as the target's ``kind="tpf"`` artifacts, every request is logged under the target, and the rung 5 row
    and the pinned ``pixels.npz`` land."""
    from skysift.lc.tpf import tpf_listing_url
    sid = env.seed_signal(tpfs="target")
    kic, (mode, radec) = next(iter(env.tpfs.items()))
    files = {tpf_name(kic, b.quarter): b for b in tpf_quarters(mode, target_radec=radec, kic=kic)}
    mast = FakeMast({tpf_listing_url(kic): listing_html(kic, sorted(files)), **files}, other=fake_fetch.fetch)
    rep = recheck(env.store, env.blob, env.settings, fetch=mast)
    with env.store.session() as s:
        tid = s.get(SignalRow, sid).target_id
    assert rep.steps["pixels"] == 1 and env.store.signal_status(sid) == "candidate_stage2"
    assert mast.calls == 6 and env.tpf_calls == []
    rung5 = env.store.latest_tests(sid)[-1]
    assert rung5["rung"] == "rung5_pixels" and rung5["metrics"]["tpf_manifest"] == sorted(files)
    with env.store.session() as s:
        tpfs = s.scalars(select(ArtifactRow).where(ArtifactRow.kind == "tpf")).all()
        assert len(tpfs) == 5 and {a.target_id for a in tpfs} == {tid}
        assert {a.url for a in tpfs} == {tpf_listing_url(kic) + n for n in files}
        npz = s.scalars(select(ArtifactRow).where(ArtifactRow.kind == "pixels")).one()
        assert npz.pinned and npz.url == f"skysift://pixels/{sid}" and npz.sha256 == rung5["metrics"]["pixels_sha256"]
        mast_queries = s.scalars(select(QueryRow).where(QueryRow.service == "mast")).all()
        assert len(mast_queries) == 6 and {q.target_id for q in mast_queries} == {tid}


def test_a_p2_child_joins_the_work_list_and_reaches_stage_2_in_one_run(env):
    """The period step's child is rechecked right after its parent, in the same run: its pixel step runs there, so
    a period-doubled planet reaches stage 2 in one recheck; a second recheck writes nothing new."""
    sid = env.seed_signal(tpfs="target", doubled=True)
    rep = recheck(env.store, env.blob, env.settings, fetch=fake_fetch.fetch, tpf_loader=env.tpf_loader)
    child = env.store.children(sid)[0]
    assert rep.steps["period"] == 1 and rep.steps["pixels"] == 1 and rep.considered == 2
    assert env.store.signal_status(sid) == "superseded" and child["final_status"] == "candidate_stage2"
    assert [(r["step"], r["outcome"]) for r in env.store.rechecks(child["id"])] == [("pixels", "pass")]
    assert rep.outcomes["superseded"] == 1 and rep.outcomes["candidate_stage2"] == 1
    before, rows = env.store.counts(), {i: env.store.rechecks(i) for i in (sid, child["id"])}
    rep = recheck(env.store, env.blob, env.settings, fetch=fake_fetch.fetch, tpf_loader=env.tpf_loader)
    assert rep.considered == 0 and env.store.counts() == before
    assert {i: env.store.rechecks(i) for i in (sid, child["id"])} == rows and len(env.tpf_calls) == 1


def test_two_overlapping_rechecks_make_one_p2_child(env, monkeypatch):
    """Another recheck commits the same P/2 signal after this one decided to make it: the store writes one child and
    records the loser as skipped, inside the transaction."""
    sid = env.seed_doubled_planet()
    real = Store.commit_recheck_step
    raced = []

    def overlapping(self, write):
        if write.child is not None and not raced:
            raced.append(real(self, copy.deepcopy(write)))      # the other recheck lands first
        return real(self, write)
    monkeypatch.setattr(Store, "commit_recheck_step", overlapping)
    rep = recheck(env.store, env.blob, env.settings, fetch=fake_fetch.fetch, pixels=False)
    assert raced[0]["written"] and len(env.store.children(sid)) == 1 and rep.steps["period"] == 0
    assert [(r["step"], r["outcome"], r["reason"]) for r in env.store.rechecks(sid)] == [
        ("period", "superseded", "re-measured at 8 d: candidate_stage1"), ("period", "skipped", "child already exists")]
    assert env.store.signal_status(sid) == "superseded"
    with env.store.session() as s:
        assert len(s.scalars(select(RunRow).where(RunRow.job_key.like("recheck:%"))).all()) == 1


def test_a_standalone_recheck_uses_each_signals_own_search_window(env, caplog):
    """A sweep run with --min-period 5 --max-period 40 writes signals of another search version than the default
    window; a standalone recheck (default settings) rechecks them under their stored window instead of skipping
    them. A signal without a stored window uses the configured one and the log says so."""
    windowed = env.seed_signal(window=(5.0, 40.0),
                               tests=[("rung4_novelty", "undetermined", "outage:exoarchive: HTTP 403")])
    rep = recheck(env.store, env.blob, env.settings, fetch=fake_fetch.fetch, pixels=False)
    assert rep.considered == 1 and rep.outcomes["skipped"] == 0 and rep.steps["catalogue"] == 1
    assert [(r["step"], r["outcome"]) for r in env.store.rechecks(windowed)] == [("catalogue", "pass")]
    bare = env.seed_signal(window=None, tests=[("rung4_novelty", "undetermined", "outage:exoarchive: HTTP 403")])
    with caplog.at_level("INFO", logger="skysift.recheck"):
        rep = recheck(env.store, env.blob, env.settings, fetch=fake_fetch.fetch, pixels=False,
                      detector_version=env.version)
    assert [(r["step"], r["outcome"]) for r in env.store.rechecks(bare)] == [("catalogue", "pass")]
    assert any("no stored search window" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("mode", ["stream", "keep"])
def test_stream_retention_releases_the_pixel_files_after_the_pixel_step(env, mode):
    """Stream mode deletes the target's pixel files once the pixel step is written (the pinned pixels.npz holds what
    the dossier draws); keep mode keeps them."""
    from skysift.lc.tpf import tpf_listing_url
    from skysift.store.retention import Retention, RetentionPolicy
    sid = env.seed_signal(tpfs="target")
    kic, (scene, radec) = next(iter(env.tpfs.items()))
    files = {tpf_name(kic, b.quarter): b for b in tpf_quarters(scene, target_radec=radec, kic=kic)}
    mast = FakeMast({tpf_listing_url(kic): listing_html(kic, sorted(files)), **files}, other=fake_fetch.fetch)
    retention = Retention(RetentionPolicy.parse(mode), env.store, env.blob)
    recheck(env.store, env.blob, env.settings, fetch=mast, retention=retention)
    assert env.store.signal_status(sid) == "candidate_stage2"
    with env.store.session() as s:
        tpfs = s.scalars(select(ArtifactRow).where(ArtifactRow.kind == "tpf")).all()
        npz = s.scalars(select(ArtifactRow).where(ArtifactRow.url == f"skysift://pixels/{sid}")).one()
        assert len(tpfs) == 5 and npz.pinned and npz.deleted_at is None and env.blob.exists(npz.local_path)
        released = [a.deleted_at is not None and not env.blob.exists(a.local_path) for a in tpfs]
        assert released == [mode == "stream"] * 5


def test_the_p2_child_does_not_inherit_the_parents_alias_note():
    parent = Signal(target_id="t", detector="box_dip", detector_version="v", period=16.0, t0=140.0, duration=0.1,
                    depth_ppm=2500, snr=30, sde=20, n_events=80,
                    extra={"alias_resolved": {"from_period": 8.0, "factor": 2}, "mask": [[1.0, 2.0]],
                           "search_window": {"min_period_days": 0.5, "max_period_days": 200.0}})
    lc = inject_transit(make_lightcurve(noise_ppm=400, seed=7), 8.0, 140.0, 0.1, 2500.0)
    child = pipeline.halved_signal(parent, lc, lc.flux)
    assert "alias_resolved" not in child.extra and child.extra["mask"] == [[1.0, 2.0]]
    assert child.extra["search_window"] == parent.extra["search_window"] and "alias_resolved" in parent.extra
