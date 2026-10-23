"""A small realistic database for dashboard tests: seven KBonus-like targets, one done run each. The first three: one
candidate with four passing rungs, one undetermined signal (rung 4 outage), one failed signal, raw artifacts for two
of them and three logged queries. Then a legacy candidate (pre-M3 metrics), a stage-2 candidate with a passing pixel
rung, an approved stage-1 candidate and a pending signal with no test rows (no artifacts, no queries). Every run is
moved five minutes into the past, so the seed reads as a finished sweep, not one still writing results. Opt-in extras:
``add_older_version`` and ``add_superseded_pair`` (a signal the recheck superseded and its P/2 child)."""
import json

from skysift.models import Signal, Target, TestResult, TestStatus
from skysift.store.db import SignalRow, Store, make_job_key
from skysift.vetting.rung5_pixels import PIXEL_RULE_VERSION

VERSION = "1+abcdef01"
STAGE1_TARGET = "kic-005689121"      # the candidate role: stage 1, no pixel rung
STAGE2_TARGET = "kic-006146676"      # the stage-2 role: a passing pixel rung written by the recheck


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


STAGE2_PIXEL_REASON = "the dip is on the target in 3/3 seasons (centroid >= 17.5 sigma nearer)"


def _stage2_season(season: int, target, neighbour, offset, sigma, sep, voting=True, why=None,
                   quarters: bool = False) -> dict:
    """One season of rung 5's metrics in the shape ``Localisation.metrics()`` and the rung write: positions as
    season-frame (col, row), the archive SEASON as its label. ``quarters`` adds the season's quarters by the archive
    SEASON (Q2, Q6, Q10, Q14 are season 0; Q1 and Q5 are season 3); the seeded rows leave them out, and the dossier
    then shows no quarters for a season (it never works them out from the quarter numbers)."""
    col, row = target[0] + offset[0], target[1] + offset[1]
    d_t = (offset[0] ** 2 + offset[1] ** 2) ** 0.5
    d_n = ((col - neighbour[0]) ** 2 + (row - neighbour[1]) ** 2) ** 0.5
    out = {"season": season, "n_events": 9 if voting else 1, "centroid_colrow": [col, row],
           "centroid_sigma_pix": sigma, "centroid_sigma_axis_pix": sigma * 1.1, "dist_target_pix": round(d_t, 3),
           "dist_neighbour_pix": round(d_n, 3), "nearer": "target" if voting else "unknown",
           "separation_sigma": sep, "depth_target_ppm": [2213.0, 78.0], "depth_neighbour_ppm": [-21.0, 25.0],
           "max_snr": 12.4, "two_source": voting, "voting": voting, "non_voting_reason": why,
           "pos_target": list(target), "pos_neighbour": list(neighbour)}
    if quarters:
        out["quarters"] = [q for q in range(1, 18) if (q - 2) % 4 == season][:4]
    return out


def stage2_pixel_metrics(quarters: bool = False) -> dict:
    """Rung 5's metrics for KIC 6146676 at 40.29 d in the shape the committed rung writes and as the real run found
    them: four seasons in archive SEASON order, seasons 0, 1 and 2 voting at 40.9, 20.5 and 17.5 sigma with the
    centroid 0.10 to 0.20 px from this star and about 2.8 px from the neighbour, archive season 3 not voting (this
    star's box half off the stamp), 18 pixel files read."""
    seasons = [
        _stage2_season(0, (4.2, 5.1), (6.9, 5.9), (0.10, -0.08), 0.05, 40.9, quarters=quarters),
        _stage2_season(1, (5.3, 3.8), (4.5, 6.6), (0.04, 0.10), 0.07, 20.5, quarters=quarters),
        _stage2_season(2, (3.1, 4.4), (0.4, 3.6), (-0.15, 0.12), 0.08, 17.5, quarters=quarters),
        _stage2_season(3, (0.6, 4.1), (3.4, 4.9), (0.12, 0.06), 0.06, 39.0, voting=False,
                       why="box incomplete: target", quarters=quarters),
    ]
    return {"seasons": seasons, "n_events_used": 28, "n_quarters_usable": 15, "unusable_quarters": [0, 17],
            "verdict": "pass", "reason": f"pixels: {STAGE2_PIXEL_REASON}", "centroid_sigma_pix_mean": 0.057,
            "leak_depth_ppm": [14.0, 31.0], "tpf_manifest": [f"kplr006146666-q{q:02d}_lpd-targ.fits.gz"
                                                             for q in range(18)],
            "n_tpf": 18, "neighbour_kic": 6146666, "neighbour_position": "catalogue", "n_seasons": 4,
            "n_seasons_voting": 3, "seasons_silenced": {"box incomplete: target": 1}, "pixels_sha256": None,
            "tpf_missing": [], "rule_version": PIXEL_RULE_VERSION}


def _stage2_tests() -> list[TestResult]:
    """The four light-curve rungs with today's metrics and a passing pixel rung (KIC 6146676 at 40.29 d)."""
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
        TestResult(rung="rung5_pixels", status=TestStatus.PASS, reason=STAGE2_PIXEL_REASON,
                   metrics=stage2_pixel_metrics()),
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
            recheck = store.add_recheck(ids[role], "pixels", "4", pixels.status.value, pixels.reason)
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


PAIR_TARGET = "kic-006271633"      # the half-period role: 18.19 d superseded by a 9.10 d child rejected at 50 sigma


def add_superseded_pair(store: Store) -> dict:
    """A signal the recheck superseded and the P/2 child it made, written the way the recheck writes them: the sweep
    stores the 18.19 d signal undetermined (an equal secondary at phase 0.5), then one ``commit_recheck_step`` adds
    the period row (outcome "superseded"), the 9.0975 d child in its own ``recheck:`` run with its tests (rejected by
    ``secondary_at_0.5`` at 50 sigma, equal odd and even depths, as KIC 6271633's child was in the real run), and both
    statuses. Not part of ``seed`` so the seeded counts stay as they are. Returns
    {"superseded", "halved_child", "pair_recheck_run"}."""
    from skysift.store.db import RECHECK_JOB_PREFIX, ChildWrite, RunRow, StepWrite
    t = _target(PAIR_TARGET, kic=6271633, gaia="2101234567890123463", gmag=15.1)
    store.upsert_targets([t])
    rid = store.start_run(make_job_key(t.target_id, "box_dip", VERSION, "4"), t.target_id, "box_dip", VERSION, "4")
    parent = _signal(t.target_id, 18.195, 24.0, depth=4100.0)
    r1 = TestResult(rung="rung1_detection", status=TestStatus.PASS,
                    metrics={"sde": 24.0, "snr": 31.2, "n_events": 70, "duration_ratio": 1.1})
    r2 = TestResult(rung="rung2_shape", status=TestStatus.UNDETERMINED,
                    reason="equal secondary at phase 0.5: true period may be P / 2 (9.0975 d)",
                    metrics={"depth_ppm": 4100.0, "odd_ppm": 4080.0, "even_ppm": 4120.0, "parity_sigma": 0.2,
                             "secondary_ppm": 3990.0, "secondary_sigma": 21.0, "suggested_period": 9.0975})
    item = {"signal": parent.model_dump(), "outcome": "undetermined", "tests": [r.model_dump() for r in (r1, r2)],
            "timings": [0.01, 0.01]}
    store.record_results(rid, t.target_id, [item], [], pin_evidence=False)
    with store.session() as s:
        pid = s.query(SignalRow.id).filter(SignalRow.run_id == rid).scalar()
    child = _signal(t.target_id, 9.0975, 26.5, depth=4100.0).model_copy(update={"t0": 136.3, "extra": {
        "halving": {"from_period": 18.195}}})
    c1 = TestResult(rung="rung1_detection", status=TestStatus.PASS,
                    metrics={"sde": 26.5, "snr": 33.0, "n_events": 140, "duration_ratio": 1.0})
    c2 = TestResult(rung="rung2_shape", status=TestStatus.FAIL, reason="secondary_at_0.5 50.0 sigma",
                    metrics={"depth_ppm": 4100.0, "odd_ppm": 4095.0, "even_ppm": 4105.0, "parity_sigma": 0.1,
                             "secondary_ppm": 4010.0, "secondary_sigma": 50.0})
    done = store.commit_recheck_step(StepWrite(
        signal_id=pid, step="period", ladder_version="4", outcome="superseded",
        reason="re-measured at 9.0975 d: rejected",
        child=ChildWrite(job_key=f"{RECHECK_JOB_PREFIX}P/2:{pid}", signal=child, ladder_version="4",
                         branch="recheck:P/2", tests=[(c1, 0.01), (c2, 0.01)])))
    with store.session() as s:
        run = s.query(RunRow).filter(RunRow.job_key == f"{RECHECK_JOB_PREFIX}P/2:{pid}").one().id
    return {"superseded": pid, "halved_child": done["child_id"], "pair_recheck_run": run}


def _v(median, minus, plus, source="seed"):
    return {"median": median, "minus": minus, "plus": plus, "source": source}


def _seed_star() -> tuple[dict, dict, dict]:
    """The star, planet and look blocks of a cool dwarf (the KIC 6146676 numbers)."""
    teff, radius, mass, a_au = 3653.0, 0.509, 0.507, 0.1834
    star = {"route": "mann", "spectral_type": "M1V", "teff": _v(teff, 73.0, 90.0), "radius": _v(radius, 0.016, 0.017),
            "mass": _v(mass, 0.013, 0.013), "density_gcc": _v(5.42, 0.47, 0.54), "distance_pc": _v(303.0, 3.5, 3.6),
            "abs_g": 8.829, "abs_ks": 5.623,
            "gaia_cross_check": {"teff_gspphot": 3855.0, "radius_flame": 0.5394, "mass_flame": 0.5595,
                                 "distance_bailer_jones_pc": 301.9, "note": "Gaia DR3 cross-check (seed)."},
            "reddening_note": ("Teff and spectral type assume E(BP-RP) at or below 0.1 at the observed BP-RP 2.10; if "
                               "E(BP-RP) were 0.2 the dwarf-table radius would be 0.569 Rsun."),
            "alt_radius_e02": _v(0.569, 0.034, 0.036), "caveats": [], "gaia_errors": [],
            "ruwe": None, "non_single_star": None, "ebpminrp_gspphot": None}       # the Gaia flags (Task 13)
    planet = {"rp_rearth": _v(2.61, 0.08, 0.09), "a_au": _v(a_au, 0.0016, 0.0016), "a_over_r": _v(77.5, 2.3, 2.5),
              "teq_k": _v(269.0, 7.0, 8.0), "insolation_earth": _v(1.24, 0.12, 0.15),
              "transit_probability": _v(0.0129, 0.0004, 0.0004), "hz_inner_au": 0.2177, "hz_outer_au": 0.413,
              "in_habitable_zone": False, "hz_optimistic_inner_au": 0.1648, "hz_optimistic_outer_au": 0.4295,
              "in_optimistic_habitable_zone": True}
    look = {"teff_k": teff, "radius_rsun": radius, "mass_msun": mass, "luminosity_lsun": 0.0416,
            "spectral_type": "M1V", "star_colour": "red", "a_au": a_au, "rp_rearth": 2.61, "teq_k": 269.0,
            "insolation_earth": 1.24, "hz_inner_au": 0.2177, "hz_outer_au": 0.413, "hz_optimistic_inner_au": 0.1648,
            "hz_optimistic_outer_au": 0.4295, "in_habitable_zone": False, "in_optimistic_habitable_zone": True,
            "angular_size_deg": 1.479, "angular_size_sun": 2.775}
    return star, planet, look


def _seed_errors(period: float, t0: float) -> dict:
    """The version-3 blocks of a dossier (the timing, shape, parity, secondary and, since version 5, stability blocks
    of ``dossier.ephemeris_blocks`` and the ephemeris keys they give), hand-made for a 40.29 d candidate: 32 of 33
    timed transits, a degenerate shape fit (ingress shorter than the cadence) with 20 of 20 resamples converged."""
    from skysift import ephemeris as eph
    p_fit, p_err, e_ref, t_ref_err, t0_err = period + 4e-6, 9.1e-5, 16, 0.00041, 0.0016
    t_ref = t0 + 0.0002 + e_ref * p_fit
    t0_fit = t_ref - e_ref * p_fit
    times = [{"epoch": e, "t_bkjd": t0_fit + e * p_fit, "t_bjd": t0_fit + e * p_fit + 2454833.0, "err_days": 0.0024,
              "chi2_red": 1.02, "n_in": 4, "n_out": 18, "used": e != 7,
              "reason": "outlier (6.2 MAD)" if e == 7 else None}
             for e in range(33)]
    timing = {"ok": True, "reason": None, "method": eph.EPHEMERIS_METHOD, "period": p_fit, "period_err": p_err,
              "t_ref": t_ref, "t_ref_bjd": t_ref + 2454833.0, "t_ref_err": t_ref_err, "epoch_ref": e_ref,
              "cov": 1.2e-9, "t0": t0_fit, "t0_bjd": t0_fit + 2454833.0, "t0_err": t0_err, "chi2": 31.4, "dof": 30,
              "inflation": 1.023, "rms_min": 7.9, "n_used": 32, "n_measured": 33, "n_outliers": 1, "times": times,
              "template": "fit", "template_reason": None}

    def spread(median, minus, plus, point):
        return {"median": median, "minus": minus, "plus": plus, "point": point}
    shape = {"ok": True, "reason": None, "method": eph.SHAPE_METHOD, "n_boot": 20, "n_converged": 20,
             "seed": eph.BOOT_SEED, "k": spread(0.047, 0.0004, 0.0003, 0.0471),
             "b": spread(0.31, None, None, 0.31), "a_over_r": spread(74.0, None, None, 74.0),
             "t14_h": spread(4.16, 0.06, 0.07, 4.17), "model_depth_ppm": spread(2150.0, 36.0, 26.0, 2160.0),
             "box_depth_ppm": {"median": 2166.0, "minus": 52.0, "plus": 52.0}, "box_duration_h": None,
             "ingress_min": 12.3, "degenerate": True, "degeneracy_reason": eph.DEGENERACY_REASON}
    parity = {"odd": {"depth_ppm": 2196.0, "err_ppm": 70.0, "n": 16, "inflation": 1.1},
              "even": {"depth_ppm": 2160.0, "err_ppm": 72.0, "n": 16, "inflation": 1.0}, "difference_sigma": 0.36}
    secondary = {"depth_ppm": 15.0, "err_ppm": 65.0, "limit_3sigma_ppm": 210.0, "strongest_ppm": 40.0,
                 "strongest_phase": 0.62, "strongest_sigma": 1.4, "albedo_limit": None}
    extra = {"period_days": p_fit, "t0_bkjd": t0_fit, "t0_bjd": t0_fit + 2454833.0, "period_err_days": p_err,
             "t0_bkjd_err": t0_err, "t0_bjd_err": t0_err, "t_ref_bkjd": t_ref, "t_ref_bjd": t_ref + 2454833.0,
             "t_ref_err_days": t_ref_err, "epoch_ref": e_ref, "period_source": "linear fit of 32 transit times",
             "duration_h": 4.16, "duration_h_err": {"minus": 0.06, "plus": 0.07}, "duration_source": "fitted T14",
             "depth_ppm": 2180.0, "depth_ppm_err": 60.0, "depth_source": "per-event weighted mean of the box depth",
             "fold_ephemeris": "linear fit of 32 transit times",
             "ephemeris_method": eph.EPHEMERIS_METHOD, "shape_method": eph.SHAPE_METHOD}
    stability = {"status": "pass", "reason": None, "ephemeris": "linear fit of 32 transit times",
                 "metrics": {"stacked": False, "chi2": 0.3, "dof": 3, "p_value": 0.9, "n_events": 32}}
    # spec 11.5: no SAP fold (so no aperture block) and a depth trend below the printing threshold
    trend = {"slope_ppm_per_year": -12.0, "err_ppm_per_year": 20.0, "sigma": 0.6, "chi2_red": 0.9, "n": 32,
             "printed": False}
    return {"ephemeris_extra": extra, "timing": timing, "shape": shape, "parity": parity, "secondary": secondary,
            "stability": stability, "aperture": None, "depth_trend": trend}


def _seed_probabilities(period: float, sde: float) -> tuple[dict, dict]:
    """FAP 1 in 1,700 and FPP 0.03 ("about 3 percent"), with the keys ``generate`` writes."""
    from skysift import dossier as ds
    neighbours = [{"source_id": "2101234567890120002", "sep_arcsec": 10.8, "gmag": 12.8, "bp_rp": 1.1,
                   "centroid_sep_arcsec": 10.2}]
    inputs = {"period": period, "depth": 0.0022, "gmag": 16.2, "teff": 3653.0, "radius": 0.509, "mass": 0.507,
              "rp_rearth": 2.61, "fit_k": 0.047, "fit_b": 0.31, "fit_a_over_r": None, "secondary_ppm": 15.0,
              "secondary_err_ppm": 65.0, "odd_ppm": 2202.0, "even_ppm": 2115.0, "odd_even_err_ppm": 290.0,
              "neighbours": neighbours, "centroid_sigma_arcsec": 0.1, "sigma_c_stat_arcsec": 0.1,
              "sigma_c_scatter_arcsec": 0.05, "sigma_c_source": "statistical", "neighbours_known": True}
    scenarios = [{"name": name, "prior": prior, "geometric": geo, "data": dat, "weight": prior * geo * dat,
                  "factors": {}} for name, prior, geo, dat in
                 (("PL", 0.165, 0.0129, 1.0), ("BEB", 0.014, 0.0202, 0.0), ("EB-P", 0.00535, 0.0170, 0.574),
                  ("EB-2P", 0.00535, 0.0107, 0.0574))]
    fpp = {"value": 0.03, "words": "about 3 percent", "label": "False positive", "signed_off": False, "pixels": True,
           "neighbours_known": True, "value_without_beb": None,
           "spec_product": {"eb_weight": 6.6e-6, "value": 0.003,
                            "rule": "spec 4.2 as first written: one EB scenario at 2P weighted by S_sec x S_oe"},
           "confusion_radius_arcsec": 2.0,
           "neighbours": [{**neighbours[0], "s_arcsec": 10.2, "flux_fraction": 0.958, "bright_enough": True,
                           "exclusion": 0.0}],
           "scenarios": scenarios, "inputs": inputs, "sentence_not_covered": ds.FPP_NOT_COVERED}
    fap = {"k": 0, "n": 1699, "fap": 1 / 1700, "calibrated": True, "version_matches": True, "versions": [VERSION],
           "k_by_kind": {"inverted": 0, "scrambled": 0}, "n_by_kind": {"inverted": 850, "scrambled": 849},
           "one_in": 1700, "words": "1 in 1,700", "sde": sde, "signal_search_version": VERSION,
           "calibration_version": VERSION, "min_nulls": 200, "sentence_not_covered": ds.FAP_NOT_COVERED}
    return fap, fpp


def _seed_plots(period: float, t0: float) -> dict:
    events = [{"epoch": e, "t_mid": round(t0 + e * period, 3), "quarter": 1 + e // 2, "depth_ppm": 2190.0 + 10 * e,
               "err_ppm": 250.0, "coverage": 0.97, "ses": 8.7} for e in range(3)]
    return {"series_binned": [[t0 + 0.25 * i, -30.0 * (i % 3), 12] for i in range(8)], "series_bin_days": 0.25,
            "window_h": 9.216, "fold_points": [[-2.0, 10.0], [0.0, -2200.0], [2.0, -5.0]],
            "fold_bins": [[-1.0, -40.0, 60.0, 20], [0.0, -2190.0, 60.0, 20], [1.0, -30.0, 60.0, 20]],
            "bin_minutes": 22.5,
            "model": [[-9.216, 0.0], [-1.152, 0.0], [-1.152, -2200.0], [1.152, -2200.0], [1.152, 0.0], [9.216, 0.0]],
            "model_kind": "box", "odd_bins": [[0.0, -2202.0, 90.0, 10]], "even_bins": [[0.0, -2115.0, 90.0, 10]],
            "secondary_bins": [[0.0, 15.0, 60.0, 20]],
            "event_gallery": [{**{k: e[k] for k in ("epoch", "t_mid", "quarter", "depth_ppm", "err_ppm")},
                               "pts": [[-1.0, 20.0], [0.0, -2200.0], [1.0, -10.0]]} for e in events],
            "gallery_half_hours": 8.0, "events": events,
            "spectrum": [[1.0, 3.2], [20.14, 9.8], [period, 50.5], [80.58, 12.1]],
            "spectrum_lines": {"aliases": [period / 2, period * 2, period * 3],
                               "systematic_periods": [93.0, 46.5, 372.5, 186.25], "peaks": []},
            "quarters": [{"q": q, "season": q % 4, "t_start": 131.5 + 93 * (q - 1), "t_end": 224.0 + 93 * (q - 1),
                          "n": 4400, "rms_ppm": 610.0} for q in (1, 2, 3, 4)]}


def _seed_pixels() -> list[dict]:
    return [{"season": season, "quarters": [season + 4 * k for k in range(1, 4)], "shape": [3, 3],
             "snr": [[None, 1.2, 0.4], [0.8, 9.5, 1.1], [0.2, 0.9, 0.1]],
             "direct": [[None, 12.0, 11.0], [13.0, 40.0, 14.0], [10.0, 12.0, 11.0]],
             "valid": [[False, True, True], [True, True, True], [True, True, True]],
             "pos_target": [1.0, 1.0], "pos_neighbour": [2.6, 1.4], "centroid": [1.05, 1.02], "centroid_sigma": 0.05,
             "nearer": "target", "dist_target_pix": 0.15, "dist_neighbour_pix": 2.9,
             "depth_target_ppm": [2213, 78], "depth_neighbour_ppm": [-21, 25]} for season in (0, 1)]


# the TESS inputs ``tools/tess_inputs.py`` would store (``targets.meta["tess"]``): one bright neighbour half a pixel away
SEED_TESS = {"queried": "2026-10-22", "tic": 169822650, "tmag": 15.1926, "tmag_source": "TIC", "ra": 297.86876,
             "dec": 41.45565, "sectors": [14, 15, 41, 54, 55, 74, 75, 81, 82],
             "neighbours": [{"tic": 169822655, "dst_arcsec": 11.7, "dra_pix": 0.40, "ddec_pix": -0.39,
                             "tmag": 12.3923, "tmag_source": "TIC"}]}


def seed_dossier(store: Store, data_dir, target_id: str = STAGE2_TARGET) -> dict:
    """A hand-made ``dossier_data.json`` for ``target_id``'s best signal (small arrays, every key ``generate`` writes,
    the star and planet of a cool dwarf, FPP 0.03, FAP 1 in 1,700, the version-3 error blocks of ``_seed_errors``)
    and its ``dossiers`` row carrying the signal's
    current fingerprint, so the dossier pages have one current dossier to draw. Returns the JSON as written."""
    import dataclasses
    from datetime import datetime
    from pathlib import Path

    from skysift import dossier as ds
    from skysift import tess
    from skysift.jsonutil import dumps
    from skysift.store.db import TargetRow
    from skysift.ui import explain
    sig = ds.pick_signal(store, target_id)
    sid, tests, decisions = sig["id"], store.latest_tests(sig["id"]), store.decisions(sig["id"])
    with store.session() as s:
        t = s.get(TargetRow, target_id)
        target = {c.name: getattr(t, c.name) for c in TargetRow.__table__.columns}
    period, t0, dur = float(sig["period"]), float(sig["t0"]), float(sig["duration"])
    star, planet, look = _seed_star()
    fap, fpp = _seed_probabilities(period, float(sig["sde"]))
    errors = _seed_errors(period, t0)
    cand = {"status": sig["candidate_status"]} if sig.get("candidate_status") else None
    verdict = explain.verdict(sig, tests, cand, decisions)
    label, label_text = ds.status_label(verdict)
    ladder, per_rung = ds.ladder_versions(store, sig, tests)
    data = {
        "dossier_version": ds.DOSSIER_VERSION, "generated": "2026-10-21T09:00:00+00:00",
        "sections": [{"number": f"{i:02d}", "title": title} for i, title in enumerate(ds.SECTIONS, 1)],
        "target": {**target, "name": ds.target_name(target)},
        "signal": {k: v for k, v in sig.items() if k not in ("candidate_status", "decision")},
        "status": {"final_status": sig["final_status"], "decision": decisions[0] if decisions else None,
                   "candidate_status": sig.get("candidate_status"), "label": label,
                   "label_text": label_text, "headline": verdict.headline,
                   "reviewer": verdict.reviewer},
        "summary": explain.summary_sentence(sig, target, tests),
        "ephemeris": {"period_days": period, "t0_bkjd": t0, "t0_bjd": t0 + 2454833.0, "duration_h": dur * 24,
                      "depth_ppm": float(sig["depth_ppm"]), "sde": float(sig["sde"]), "snr": float(sig["snr"]),
                      "n_events": int(sig["n_events"]), **errors["ephemeris_extra"]},
        **{key: errors[key] for key in ("timing", "shape", "parity", "secondary", "stability", "aperture",
                                        "depth_trend")},
        "plots": _seed_plots(period, t0),
        "tests": [{"rung": x["rung"], "status": x["status"], "reason": x["reason"], "metrics": x["metrics"],
                   "created": x["created"].isoformat()} for x in tests],
        "why": [dataclasses.asdict(w) for w in explain.why(tests, aperture=errors["aperture"])],
        "rechecks": store.rechecks(sid), "children": store.children(sid), "fap": fap, "fpp": fpp,
        "pixels": _seed_pixels(),
        "centroid_offset": ds.centroid_offset(next((x["metrics"] for x in tests if x["rung"] == "rung5_pixels"), {})),
        "prior_claims": [], "hand_built_localisation": None, "predicted_leak": None,     # no kept pixel files
        # spec 11.4: the sweep the false-alarm sentence counts (the seed's own searches, by SWEEP_QUERY)
        "calibration": {"sweep_size": 1579, "sweep_source": "kbonus_bkg", "sweep_detector": sig["detector"],
                        "sweep_detector_version": sig["detector_version"], "sweep_query": ds.SWEEP_QUERY,
                        "null_stars": 850, "null_searches": fap.get("n"),
                        "expected_by_chance": 1579 * fap["fap"] if fap.get("fap") is not None else None},
        "star": star, "planet": planet, "look": look, "caveats": [],
        "section_errors": {}, "cross_check": None,
        "provenance": {"search_version": sig["detector_version"], "ladder_version": ladder,
                       "ladder_versions": per_rung, "git_commit": "seed", "queries": [], "files": [],
                       "calibration_file": "calibration/kbonus_bkg.jsonl",
                       "commands": [f"skysift vet --source kbonus_bkg --target-id {target_id}",
                                    "skysift calibrate --source kbonus_bkg",
                                    f"skysift dossier --target {target_id} --signal {sid} --force"]},
    }
    data["tess"] = tess.statement(SEED_TESS, data["ephemeris"])
    data["caveats"] = ds.caveats(data)
    text = dumps(data)
    path = Path(data_dir) / "dossiers" / target_id / "dossier_data.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    decision = decisions[0]["decision"] if decisions else None
    store.upsert_dossier(target_id=target_id, signal_id=sid, generated=datetime(2026, 10, 21, 9, 0),
                         data_path=str(path), search_version=sig["detector_version"],
                         ladder_version=ladder, dossier_version=ds.DOSSIER_VERSION, fap=fap["fap"],
                         fpp=fpp["value"], fap_detail=fap, fpp_detail=fpp,
                         tests_fingerprint=ds.fingerprint(tests, sig["final_status"], decision, 0,
                                                          ds.sweep_size_of(store, sig, target.get("source"))),
                         status_at_generation=sig["final_status"])
    return json.loads(text)
