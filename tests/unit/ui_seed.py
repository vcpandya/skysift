"""A small realistic database for dashboard tests: seven KBonus-like targets, one done run each. The first three: one
candidate with four passing rungs, one undetermined signal (rung 4 outage), one failed signal, raw artifacts for two
of them and three logged queries. Then a legacy candidate (pre-M3 metrics), a stage-2 candidate with a passing pixel
rung, an approved stage-1 candidate and a pending signal with no test rows (no artifacts, no queries). Every run is
moved five minutes into the past, so the seed reads as a finished sweep, not one still writing results."""
from skysift.models import Signal, Target, TestResult, TestStatus
from skysift.store.db import SignalRow, Store, make_job_key

VERSION = "1+abcdef01"


def _target(tid, kic=None, gaia="2101234567890123456", gmag=16.4):
    return Target(source="kbonus_bkg", target_id=tid, ra=285.9, dec=40.9, gaia_id=gaia, kic=kic, gmag=gmag,
                  meta={"psffrac": 0.9, "crowdsap": 0.9, "file_name": f"{tid}.fits"})


def _signal(tid, period, sde, depth=1800.0):
    return Signal(target_id=tid, detector="box_dip", detector_version=VERSION, period=period, t0=136.3, duration=0.096,
                  depth_ppm=depth, snr=sde * 1.3, sde=sde, n_events=int(1400 / period),
                  extra={"baseline_days": 1400.0, "search_window": {"min_period_days": 0.5, "max_period_days": 200.0}})


def _tests(outcome):
    r1 = TestResult(rung="rung1_detection", status=TestStatus.PASS, metrics={"sde": 14.0, "snr": 18.0, "n_events": 99,
                                                                             "duration_ratio": 0.9})
    r2 = TestResult(rung="rung2_shape", status=TestStatus.PASS,
                    metrics={"depth_ppm": 1800.0, "depth_err_ppm": 90.0, "odd_ppm": 1790.0, "even_ppm": 1810.0,
                             "parity_sigma": 0.2, "secondary_ppm": 20.0, "secondary_sigma": 0.3,
                             "free_secondary_phase": 0.41, "free_secondary_ppm": 60.0, "free_secondary_sigma": 1.1,
                             "control_rms_ppm": 70.0, "timing_rms_min": None})
    r3 = TestResult(rung="rung3_stability", status=TestStatus.PASS,
                    metrics={"season_depths": {"0": 1750.0, "1": 1820.0, "2": 1790.0, "3": 1840.0},
                             "season_errs": {"0": 120.0, "1": 110.0, "2": 130.0, "3": 115.0},
                             "season_z": {"0": -0.4, "1": 0.3, "2": -0.1, "3": 0.5}, "chi2": 0.6, "dof": 3,
                             "p_value": 0.9, "n_events": 99, "sap_psf_ratio": 1.02})
    if outcome == "pass":
        r4 = TestResult(rung="rung4_novelty", status=TestStatus.PASS, metrics={"cumulative": 0, "q1_q17_dr25_tce": 0,
                                                                              "toi": 0, "kepler_eb": 0, "other_period_objects": []})
    elif outcome == "undetermined":
        r4 = TestResult(rung="rung4_novelty", status=TestStatus.UNDETERMINED, reason="outage:vizier: HTTP 503",
                        metrics={"cumulative": 0})
    else:
        r2 = TestResult(rung="rung2_shape", status=TestStatus.FAIL, reason="odd_even 4.2 sigma",
                        metrics={**r2.metrics, "parity_sigma": 4.2})
        return [r1, r2]
    return [r1, r2, r3, r4]


def seed(store: Store, blob=None) -> dict:
    targets = [_target("kic-005689121", kic=5689121), _target("gaia-dr3-2101234567890123457", gaia="2101234567890123457"),
               _target("gaia-dr3-2101234567890123458", gaia="2101234567890123458", gmag=18.9)]
    store.upsert_targets(targets)
    ids, runs = {}, {}
    for t, (period, sde, outcome, key) in zip(targets, [(13.7695, 21.1, "pass", "candidate"),
                                                          (3.21, 10.4, "undetermined", "undetermined"),
                                                          (1.07, 9.6, "fail", "rejected")], strict=True):
        rid = store.start_run(make_job_key(t.target_id, "box_dip", VERSION, "1"), t.target_id, "box_dip", VERSION, "1")
        tests = _tests(outcome)
        item = {"signal": _signal(t.target_id, period, sde).model_dump(), "outcome": outcome,
                "tests": [r.model_dump() for r in tests], "timings": [0.01] * len(tests)}
        q = [{"service": "exoarchive", "url": "https://exoarchive/TAP", "params": {"query": "select 1"}, "status": 200,
              "nbytes": 12, "sha256": "ab" * 32, "error": "", "utc": "2026-10-14T00:00:00+00:00"}]
        if blob is not None and outcome != "fail":
            data = f"FITS bytes for {t.target_id}".encode()
            sha, loc = blob.put(data, f"{t.target_id}.fits")
            store.add_artifact(sha, len(data), f"https://mast/{t.target_id}.fits", loc, t.target_id)
        store.record_results(rid, t.target_id, [item], q, pin_evidence=outcome != "fail")
        runs[key] = rid
    key_of = {t.target_id: key for t, key in zip(targets, ("candidate", "undetermined", "rejected"), strict=True)}
    with store.session() as s:
        for row in s.query(SignalRow):
            ids[key_of[row.target_id]] = row.id
    ids.update(_seed_more(store))
    _backdate(store, SEED_AGE_MINUTES)
    return {**ids, "targets": [t.target_id for t in targets], "run_ids": runs,
            "approved_target": APPROVED_TARGET}


APPROVED_TARGET = "gaia-dr3-2101234567890123461"
SEED_AGE_MINUTES = 5


def _backdate(store: Store, minutes: int) -> None:
    """Move every seeded run ``minutes`` into the past: a store whose last run finished moments ago reads as a sweep
    still writing results (``queries.external_sweep``), and the seed stands for a finished one."""
    from datetime import timedelta

    from skysift.store.db import RunRow
    with store.session() as s:
        for run in s.query(RunRow):
            run.started -= timedelta(minutes=minutes)
            if run.finished is not None:
                run.finished -= timedelta(minutes=minutes)


def _legacy_tests() -> list[TestResult]:
    """Rungs 1 to 3 as stored before M3 (fewer metric keys, no rung1_events row) and a bare rung 4 pass."""
    return [TestResult(rung="rung1_detection", status=TestStatus.PASS,
                       metrics={"sde": 16.2, "snr": 20.1, "n_events": 60}),
            TestResult(rung="rung2_shape", status=TestStatus.PASS,
                       metrics={"depth_ppm": 950.0, "odd_ppm": 940.0, "even_ppm": 960.0, "parity_sigma": 0.4,
                                "secondary_ppm": 10.0, "secondary_sigma": 0.2}),
            TestResult(rung="rung3_stability", status=TestStatus.PASS,
                       metrics={"season_depths": {"0": 930.0, "1": 970.0, "2": 950.0}, "chi2": 0.4, "dof": 2,
                                "p_value": 0.82}),
            TestResult(rung="rung4_novelty", status=TestStatus.PASS, metrics={"cumulative": 0})]


def _stage2_tests() -> list[TestResult]:
    """The four light-curve rungs with today's metrics and a passing pixel rung (KIC 6146676 at 40.29 d)."""
    seasons = [{"season": s, "nearer": "target", "dist_target_pix": 0.15, "dist_neighbour_pix": 2.9,
                "centroid_sigma_pix": 0.05, "separation_sigma": 55.0, "depth_target_ppm": [2213, 78],
                "depth_neighbour_ppm": [-21, 25]} for s in range(4)]
    return [
        TestResult(rung="rung1_detection", status=TestStatus.PASS,
                   metrics={"sde": 50.5, "snr": 64.2, "n_events": 34, "duration_ratio": 0.82, "t_circ_days": 0.21,
                            "stellar_mass": 0.5, "stellar_radius": 0.5, "stellar_source": "fallback"}),
        TestResult(rung="rung1_events", status=TestStatus.PASS,
                   metrics={"n_events_with_data": 37, "n_covered": 32, "n_good": 32, "duty_cycle": 0.81,
                            "ses_rules_applied": True, "ses_max_over_mes": 0.21, "chases_median": 0.93,
                            "coverage": None, "ses": None, "chases": None, "coverage_summary": None,
                            "ses_summary": None}),
        TestResult(rung="rung2_shape", status=TestStatus.PASS,
                   metrics={"stacked": False, "depth_ppm": 2180.0, "depth_err_ppm": 60.0, "n_events": 32,
                            "odd_ppm": 2202.0, "even_ppm": 2115.0, "parity_sigma": 0.3, "secondary_ppm": 15.0,
                            "secondary_sigma": 0.2, "free_secondary_phase": 0.62, "free_secondary_ppm": 40.0,
                            "free_secondary_sigma": 1.4, "control_rms_ppm": 65.0, "box_chi2": 120.0,
                            "sine_chi2": 410.0, "shape_n": 300, "fit_ok": True, "fit_k": 0.047, "fit_b": 0.31,
                            "fit_depth_ppm": 2150.0, "v_shape_applied": False}),
        TestResult(rung="rung3_stability", status=TestStatus.PASS,
                   metrics={"stacked": False, "season_depths": {"0": 2150.0, "1": 2230.0, "2": 2190.0, "3": 2170.0},
                            "season_errs": {"0": 110.0, "1": 120.0, "2": 105.0, "3": 115.0},
                            "season_z": {"0": -0.3, "1": 0.4, "2": 0.1, "3": -0.1}, "chi2": 0.3, "dof": 3,
                            "p_value": 0.9, "n_events": 32, "sap_psf_ratio": 1.04, "aperture_z": 0.6,
                            "aperture_ratio": 1.04}),
        TestResult(rung="rung4_novelty", status=TestStatus.PASS,
                   metrics={"cumulative": 0, "q1_q17_dr25_tce": 0, "toi": 0, "kepler_eb": 0,
                            "other_period_objects": []}),
        TestResult(rung="rung5_pixels", status=TestStatus.PASS,
                   metrics={"seasons": seasons, "n_events_used": 35, "n_quarters_usable": 16}),
    ]


def _seed_more(store: Store) -> dict:
    """The roles added for the plain-language explanations, each on its own target so the per-target assertions
    above hold: a legacy candidate, a stage-2 candidate, an approved stage-1 candidate and a pending signal with no
    test rows. The stage-2 candidate is written the way the recheck writes it: the sweep stores the light-curve
    rungs, then a pixel recheck row, the rung 5 row pointing at it, and the derived status."""
    specs = [("legacy", _target("gaia-dr3-2101234567890123459", gaia="2101234567890123459"), 5.4321, 16.2,
              _legacy_tests()),
             ("stage2", _target("kic-006146676", kic=6146676, gaia="2101234567890123462", gmag=16.2), 40.2899, 50.5,
              _stage2_tests()),
             ("approved", _target(APPROVED_TARGET, gaia="2101234567890123461"), 7.654, 18.3, _tests("pass")),
             ("pending", _target("gaia-dr3-2101234567890123460", gaia="2101234567890123460"), 2.345, 12.0, [])]
    store.upsert_targets([t for _role, t, *_rest in specs])
    ids = {}
    for role, t, period, sde, tests in specs:
        rid = store.start_run(make_job_key(t.target_id, "box_dip", VERSION, "1"), t.target_id, "box_dip", VERSION, "1")
        sig = _signal(t.target_id, period, sde, depth=2200.0 if role == "stage2" else 1800.0)
        swept = [r for r in tests if r.rung != "rung5_pixels"]
        item = {"signal": sig.model_dump(), "outcome": "pass", "tests": [r.model_dump() for r in swept],
                "timings": [0.01] * len(swept)}
        store.record_results(rid, t.target_id, [item], [], pin_evidence=False)
        with store.session() as s:
            ids[role] = s.query(SignalRow.id).filter(SignalRow.run_id == rid).scalar()
        for pixels in (r for r in tests if r.rung == "rung5_pixels"):
            recheck = store.add_recheck(ids[role], "pixels", "4", pixels.status.value)
            store.add_test(ids[role], pixels, 0.01, recheck_id=recheck)
            store.refresh_status(ids[role])
    store.set_signal_status(ids["pending"], "pending")
    store.decide(ids["approved"], "approve", "looks real")
    return ids


OLD_VERSION = "0+0ld0ld00"


def add_older_version(store: Store, target_id: str = "kic-005689121", period: float = 6.88475) -> int:
    """A stage-1 candidate for ``target_id`` from an earlier sweep under ``OLD_VERSION`` (its run started a day before
    the seeded runs, though its rows are written after them). Returns the signal id."""
    from datetime import timedelta

    from skysift.store.db import RunRow
    rid = store.start_run(make_job_key(target_id, "box_dip", OLD_VERSION, "1"), target_id, "box_dip", OLD_VERSION, "1")
    sig = _signal(target_id, period, 30.0).model_copy(update={"detector_version": OLD_VERSION})
    tests = _tests("pass")
    item = {"signal": sig.model_dump(), "outcome": "pass", "tests": [r.model_dump() for r in tests],
            "timings": [0.01] * len(tests)}
    store.record_results(rid, target_id, [item], [], pin_evidence=False)
    with store.session() as s:
        run = s.get(RunRow, rid)
        run.started = min(r.started for r in s.query(RunRow).filter(RunRow.id != rid)) - timedelta(days=1)
        return s.query(SignalRow.id).filter(SignalRow.run_id == rid).scalar()
