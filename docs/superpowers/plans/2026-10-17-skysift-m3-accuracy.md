# SkySift M3 Accuracy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the accuracy gaps the real sweeps of 14 to 17 October 2026 exposed, so that every stored verdict is determinate where the data allow it, known objects are recognised at the separations Kepler blends demand, a worker crash never charges a target, and the detection threshold can be calibrated at scale with one command.

**Architecture:** Engine-side changes only, each behind the existing interfaces: `skysift.lc.ops` gains stacked-fold depth measurements that rungs 2 and 3 fall back to when per-event measurements are starved (about two points per event at 30-minute cadence for sub-day periods); `BoxDip` resolves the 2P alias of deep signals; `Rung4Novelty` matches period-coincident objects out to 120 arcsec; the pipeline survives a broken process pool; a `calibrate` CLI runs the detector on real, inverted and scrambled flux so the 1000-star null pilot is one resumable command.

**Tech Stack:** Python 3.12, numpy, scipy, astropy BoxLeastSquares and LombScargle, SQLAlchemy 2, typer, pytest. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-10-13-skysift-design.md` (sections 3 to 5 and 9, plus the section 13 amendments of 14 to 17 October). The M1 and M2 ledgers (`docs/superpowers/plans/*-ledger.md`) record the rulings the code embodies. Measured context this plan argues from: the 30-curve null calibration (section 13, 17 October), the delta Scuti sub-harmonics, the K04802.01 blind recovery (0.6056 d, 1.0 h: rungs 2 and 3 undetermined with "too few odd/even events"), and the four undetermined signals per sweep that all came from that limitation.

## Global Constraints

- **Never run a console `.exe` from `.venv/Scripts`** (Norton CyberCapture froze this machine). Every command is `.venv/Scripts/python.exe -m <module>`: `python -m pytest`, `python -m ruff`, `python -m skysift`. Install with `uv pip install --native-tls -p .venv/Scripts/python.exe -e ".[dev,ui]"` only if a dependency is missing (none is expected).
- Work on branch `feat/m3-accuracy` off `main` (dcba434 or later). Commit messages are conventional (`feat(vetting): ...`) and end with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.
- Lint: `python -m ruff check src tests` clean (line length 120; `E701`, `E702`, `E731` ignored). Tests: `python -m pytest tests/unit -q -p no:cacheprovider` must stay green and warning-free (the suite runs with `-W error::DeprecationWarning`; about 15 minutes, the detector tests run real BLS). Run only the test files a task touches while iterating; the full suite runs once at the end of each task before its commit.
- Scientific defaults live in `src/skysift/config.py` (`Thresholds`) and `config/default.yaml`; both must stay identical (`tests/unit/test_config.py` pins it). `sde_min` is 11 (calibrated 17 October); do not change it.
- Every new metric a rung records must be strict-JSON (finite floats or `None`, never `NaN`): results cross a process boundary as JSON and the dashboard renders them.
- A rung returns `UNDETERMINED` only when the data cannot answer; where a stacked fold can answer, it must.
- Any change to what the detector returns (Task 3) must be reflected in `search_version` (a key in `SEARCH_CONFIG_KEYS` or the detector `version`), so earlier runs are searched again rather than mixed.
- Prose in docstrings, spec amendments and the README is plain declarative English; no em-dashes.
- Each task amends `docs/superpowers/specs/2026-10-13-skysift-design.md` section 13 with one bullet describing what changed and why (date 17 October 2026).

## Review Focus

1. **A sub-day transit at 30-minute cadence** (one hour long, about two points per event) must receive determinate rung 2 and rung 3 verdicts from the stacked folds, and an eclipsing binary with alternating depths at the same cadence must fail odd/even. Pinned by Task 1's `test_stacked_odd_even_passes_a_short_period_planet` and `test_stacked_odd_even_fails_alternating_eclipses`, and Task 2's `test_stacked_seasons_are_determinate_for_a_short_period_planet`.
2. **A deep transit ranked at twice its period** must be reported at the true period, and a genuine eclipsing binary whose secondary is shallower than its primary must not be halved. Pinned by Task 3's `test_deep_transit_found_at_2p_is_resolved_to_p` and `test_unequal_eclipses_keep_their_period`.
3. **A known object 90 arcsec away with the same period** (a blended neighbour in the same Kepler pixels) must count as known; the same object with a different period must not. Pinned by Task 4's `test_period_matched_neighbour_within_120_arcsec_is_known` and `test_unrelated_neighbour_beyond_20_arcsec_is_not`.
4. **A worker process that dies mid-sweep** must not charge the targets it was holding, must not stop the sweep, and must leave their runs for the next sweep. Pinned by Task 5's `test_worker_crash_marks_runs_unavailable_and_the_sweep_continues`.
5. **The calibration command interrupted half way** must resume without repeating finished targets, and its table must reproduce the 17 October numbers in shape (median, maximum, counts above thresholds). Pinned by Task 8's `test_calibrate_resumes_and_summarises`.

---

## File structure

| Path | Responsibility |
|---|---|
| `src/skysift/lc/ops.py` | `stacked_depth(...)` (depth of a phase window on the stacked fold with a red-noise-aware error) and `season_of(quarter)`. |
| `src/skysift/vetting/rung2_shape.py` | Falls back to stacked odd/even and stacked secondary when per-event measurements are starved; records `stacked: true`. |
| `src/skysift/vetting/rung3_stability.py` | Falls back to stacked per-season depths when fewer than four events have per-event depths. |
| `src/skysift/detectors/box_dip.py` | `_resolve_2p(...)`: halves the period of a top signal whose phase-0.5 dip matches its primary; `ALIAS_RESOLUTION = 1` in `SEARCH_CONFIG_KEYS` via a detector attribute. |
| `src/skysift/vetting/rung4_novelty.py` | Two radii: `radius_arcsec` (20) for any object, `period_radius_arcsec` (120) for period-matched objects; metric `period_matched`. |
| `src/skysift/pipeline.py` | `on_vetted` handles `BrokenProcessPool`: marks the broken pool's runs `unavailable: worker pool broke`, rebuilds the pool once per sweep, continues. |
| `src/skysift/ui/routes/live.py` + `src/skysift/ui/queries.py` | `mean_seconds(store)` as one SQL aggregate (dialect-aware), shared by the Live page and `summary()`. |
| `src/skysift/calibrate.py` + `src/skysift/cli.py` | `skysift calibrate`: resumable null calibration (real, inverted, scrambled) writing JSON lines and printing the quantile table. |
| `docs/superpowers/notes/2026-10-17-long-period-sde.md` | Task 7's study of the long-period band normalisation, with the ruling. |
| `tests/unit/test_lc_ops.py`, `test_rungs_1_3.py`, `test_box_dip.py`, `test_rung4.py`, `test_pipeline.py`, `test_ui_queries.py`, `test_calibrate.py` | Tests named in the tasks. |

---

### Task 1: Stacked-fold odd/even and secondary in rung 2

**Files:**
- Modify: `src/skysift/lc/ops.py` (add `stacked_depth`)
- Modify: `src/skysift/vetting/rung2_shape.py`
- Test: `tests/unit/test_lc_ops.py`, `tests/unit/test_rungs_1_3.py`

**Interfaces:**
- Produces: `stacked_depth(time, fnorm, period, t0, duration_days, *, epochs="all", phase_offset=0.0) -> StackedDepth` with fields `depth_ppm: float`, `err_ppm: float`, `n_in: int`, `n_out: int`, `inflation: float`. `epochs` is `"all"`, `"odd"` or `"even"` (epoch number `round((t - t0) / period)` parity). The window is `|phase - phase_offset * period| < 0.5 * duration` on the fold; the baseline is the median of points farther than 1.5 durations from both the primary and the window; `err_ppm` is the out-of-window MAD scatter divided by `sqrt(n_in)`, multiplied by `inflation = max(1, std(control depths) / formal err)` where the control depths are the same window measured at 9 phases from 0.1 to 0.9 that lie at least 1.5 durations from the primary and from the window (fewer than 3 usable controls: inflation 1). Returns `n_in = 0` and NaN depths when fewer than 10 in-window points or 50 baseline points exist.
- Consumes: `fold_phase`, `PRIMARY_MASK_DURATIONS` from `skysift.lc.ops`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_lc_ops.py
def test_stacked_depth_measures_a_short_transit_and_its_parity():
    lc = inject_transit(make_lightcurve(n_days=300, cadence_min=30, noise_ppm=500, seed=31), 0.6, 132.0, 1 / 24, 600)
    fn = detrend(lc)
    d_all = stacked_depth(lc.time, fn, 0.6, 132.0, 1 / 24)
    assert d_all.n_in > 800 and abs(d_all.depth_ppm - 600) < 3 * d_all.err_ppm and d_all.err_ppm < 60
    odd, even = (stacked_depth(lc.time, fn, 0.6, 132.0, 1 / 24, epochs=e) for e in ("odd", "even"))
    assert abs(odd.depth_ppm - even.depth_ppm) < 3 * np.hypot(odd.err_ppm, even.err_ppm)
    sec = stacked_depth(lc.time, fn, 0.6, 132.0, 1 / 24, phase_offset=0.5)
    assert abs(sec.depth_ppm) < 3 * sec.err_ppm
    assert stacked_depth(lc.time[:40], fn[:40], 0.6, 132.0, 1 / 24).n_in == 0


def test_stacked_depth_inflates_its_error_under_red_noise():
    base = make_lightcurve(n_days=300, cadence_min=30, noise_ppm=300, seed=32)
    f = base.flux * (1 - 1500e-6 * np.cos(2 * np.pi * base.time / 1.7))        # a 1.7 d wave: red noise for a 0.6 d fold
    red = inject_transit(LightCurve(base.time, f, base.flux_err, base.quality, base.quarter, f.copy()), 0.6, 132.0, 1 / 24, 600)
    d = stacked_depth(red.time, detrend(red), 0.6, 132.0, 1 / 24)
    assert d.inflation > 1.5 and abs(d.depth_ppm - 600) < 3 * d.err_ppm
```

```python
# tests/unit/test_rungs_1_3.py
def test_stacked_odd_even_passes_a_short_period_planet():
    """K04802.01 at 30-minute cadence: one hour of transit is two points per event, so the per-event odd/even test is
    starved; the stacked folds answer instead."""
    lc = inject_transit(make_lightcurve(n_days=300, cadence_min=30, noise_ppm=500, seed=33), 0.6, 132.0, 1 / 24, 600)
    s = Signal(target_id="t", detector="box_dip", detector_version="1", period=0.6, t0=132.0, duration=1 / 24,
               depth_ppm=600, snr=25.0, sde=30.0, n_events=480)
    r = Rung2Shape().run(s, bundle(lc))
    assert r.status == TestStatus.PASS, (r.reason, r.metrics)
    assert r.metrics["stacked"] is True and r.metrics["parity_sigma"] < 3 and r.metrics["n_events"] > 0


def test_stacked_odd_even_fails_alternating_eclipses():
    lc = inject_transit(make_lightcurve(n_days=300, cadence_min=30, noise_ppm=500, seed=34), 1.2, 132.0, 1 / 24, 2500)
    lc = inject_transit(lc, 1.2, 132.6, 1 / 24, 1200)
    s = Signal(target_id="t", detector="box_dip", detector_version="1", period=0.6, t0=132.0, duration=1 / 24,
               depth_ppm=1800, snr=40.0, sde=50.0, n_events=480)
    r = Rung2Shape().run(s, bundle(lc))
    assert r.status == TestStatus.FAIL and r.reason.startswith("odd_even") and r.metrics["stacked"] is True
```

- [ ] **Step 2: Run them to see them fail** (`python -m pytest tests/unit/test_lc_ops.py tests/unit/test_rungs_1_3.py -q -p no:cacheprovider -k "stacked"`; expected: ImportError / AttributeError).

- [ ] **Step 3: Implement `stacked_depth`** in `src/skysift/lc/ops.py` as specified in Interfaces (a dataclass `StackedDepth`), reusing `fold_phase`; keep it under 60 lines; controls that fall inside the primary mask or the window are skipped.

- [ ] **Step 4: Fall back in rung 2.** In `Rung2Shape.run`, after the per-event measurements: when `d_odd.n < 2 or d_even.n < 2`, compute `odd = stacked_depth(..., epochs="odd")`, `even = stacked_depth(..., epochs="even")`, `sec = stacked_depth(..., phase_offset=0.5)` and `all_s = stacked_depth(...)`; when `odd.n_in and even.n_in`, set `odd_ppm`, `even_ppm`, `parity_sigma`, `depth_ppm`, `depth_err_ppm`, `secondary_ppm`, `secondary_sigma`, `n_events` (the number of distinct epochs with at least one in-window point) from the stacked values and set `m["stacked"] = True`; otherwise keep the undetermined return. `m["stacked"]` is `False` on the per-event path. The sinusoidal contrast stays first. The secondary rule then uses the stacked `secondary_sigma` on this path.

- [ ] **Step 5: Run the two test files; then the full suite; ruff; amend the spec (section 5, rung 2); commit** `feat(vetting): stacked-fold odd/even and secondary when per-event tests are starved`.

---

### Task 2: Stacked-fold season stability in rung 3

**Files:**
- Modify: `src/skysift/lc/ops.py` (add `season_of`)
- Modify: `src/skysift/vetting/rung3_stability.py`
- Test: `tests/unit/test_rungs_1_3.py`

**Interfaces:**
- Produces: `season_of(quarter: np.ndarray) -> np.ndarray` (`quarter % 4`, the Kepler season). Rung 3 on the stacked path records `season_depths`, `season_errs`, `season_z`, `chi2`, `dof`, `p_value` exactly as today, plus `stacked: True` and `n_events` as the distinct-epoch count.
- Consumes: Task 1's `stacked_depth` (call it per season on the subset of points whose `season_of(quarter)` equals the season).

- [ ] **Step 1: Write the failing tests**

```python
def test_stacked_seasons_are_determinate_for_a_short_period_planet():
    lc = inject_transit(make_lightcurve(n_days=1400, cadence_min=30, noise_ppm=500, seed=35), 0.6, 132.0, 1 / 24, 600)
    s = Signal(target_id="t", detector="box_dip", detector_version="1", period=0.6, t0=132.0, duration=1 / 24,
               depth_ppm=600, snr=25.0, sde=30.0, n_events=2300)
    r = Rung3Stability().run(s, bundle(lc))
    assert r.status == TestStatus.PASS and r.metrics["stacked"] is True and len(r.metrics["season_depths"]) == 4


def test_stacked_seasons_catch_a_contaminated_season():
    lc = inject_transit(make_lightcurve(n_days=1400, cadence_min=30, noise_ppm=500, seed=36), 0.6, 132.0, 1 / 24, 600)
    f = lc.flux.copy(); half = season_of(lc.quarter) == 2
    ph = fold_phase(lc.time, 0.6, 132.0); dip = np.abs(ph) < 0.5 / 24
    f[half & dip] = 1 - 0.5 * (1 - f[half & dip])            # season 2 shows half the depth: contamination signature
    lc = LightCurve(lc.time, f, lc.flux_err, lc.quality, lc.quarter, f.copy())
    s = Signal(target_id="t", detector="box_dip", detector_version="1", period=0.6, t0=132.0, duration=1 / 24,
               depth_ppm=600, snr=25.0, sde=30.0, n_events=2300)
    r = Rung3Stability().run(s, bundle(lc))
    assert r.status == TestStatus.FAIL and "season-dependent" in r.reason and r.metrics["stacked"] is True
```

- [ ] **Step 2: Run to see them fail.**
- [ ] **Step 3: Implement.** When `ed.n < 4`: for each season present, `stacked_depth` on that season's points; seasons with `n_in == 0` are dropped; fewer than 3 seasons left keeps the undetermined return (`reason="too few events"`); otherwise the existing leave-one-out z, chi2 and p logic runs on the stacked means and errors, and `m["stacked"] = True`. The alt-flux ratio uses `stacked_depth` on `fnorm_alt` on this path.
- [ ] **Step 4: Tests, full suite, ruff, spec bullet (section 5, rung 3), commit** `feat(vetting): stacked-fold season stability for short transits`.

---

### Task 3: 2P alias resolution in BoxDip

**Files:**
- Modify: `src/skysift/detectors/box_dip.py`, `src/skysift/pipeline.py` (`SEARCH_CONFIG_KEYS`)
- Test: `tests/unit/test_box_dip.py`, `tests/unit/test_pipeline.py` (version hash)

**Interfaces:**
- Produces: `BoxDip.alias_resolution: int = 1` (0 turns it off; listed in `SEARCH_CONFIG_KEYS`). After ranking (first pass), for the strongest signal `s`: measure the stacked depth at phase 0.5 (`stacked_depth(t, y, s.period, s.t0, s.duration, phase_offset=0.5)`) and at the primary; when `sec.n_in`, `sec.depth_ppm > 5 * sec.err_ppm`, `|sec.depth_ppm - pri.depth_ppm| < 3 * hypot(sec.err_ppm, pri.err_ppm)` and `s.period / 2 >= thresholds.min_period_days`, the signal is re-issued at `period / 2` (same `t0`, same duration, `depth_ppm` the inverse-variance mean of the two, `n_events` recounted at the new period, `sde` the spectrum's per-band SDE at the trial period nearest `period / 2` when that exists, else unchanged) with `extra["alias_resolved"] = {"from_period": P, "primary_ppm": ..., "secondary_ppm": ...}`. The alias bookkeeping (`alias_of`) of the remaining signals is recomputed against the resolved period.

- [ ] **Step 1: Spike (30 minutes at most).** Find a synthetic configuration where the first-pass top peak is 2P: try `inject_transit(make_lightcurve(n_days=1400, seed=k, noise_ppm=1500), P, t0, dur, 20000)` for `(P, dur)` in `[(3.0, 0.06), (1.3, 0.05), (6.5, 0.1)]` with the 0.5 to 30 d window and `BoxDip(mask_iterations=0)`. Record in the ledger which configuration (if any) ranks 2P first. If none does, the implementation and tests still proceed with a signal constructed at 2P by hand (the rule is safe: it only halves a period whose phase-0.5 dip matches the primary).
- [ ] **Step 2: Write the failing tests**

```python
def test_deep_transit_found_at_2p_is_resolved_to_p():
    lc = inject_transit(make_lightcurve(n_days=500, seed=41, noise_ppm=1500), 3.0, 133.0, 0.06, 20000)
    det = BoxDip(mask_iterations=0)
    # whichever period the search ranks first, the reported signal is the true one
    sig = det.detect(lc, detrend(lc), Thresholds(min_period_days=0.5, max_period_days=15.0))
    assert abs(sig[0].period - 3.0) < 0.003
    # and a signal handed in at 2P is halved by the resolver
    two_p = sig[0].model_copy(update={"period": 6.0})
    resolved = det._resolve_2p(lc.time[np.isfinite(detrend(lc))], detrend(lc)[np.isfinite(detrend(lc))], two_p,
                               Thresholds(min_period_days=0.5, max_period_days=15.0), spec=None)
    assert abs(resolved.period - 3.0) < 1e-9 and resolved.extra["alias_resolved"]["from_period"] == 6.0


def test_unequal_eclipses_keep_their_period():
    lc = inject_transit(make_lightcurve(n_days=500, seed=42, noise_ppm=800), 4.0, 133.0, 0.1, 20000)
    lc = inject_transit(lc, 4.0, 135.0, 0.1, 6000)                      # secondary a third as deep
    det = BoxDip(mask_iterations=0)
    sig = det.detect(lc, detrend(lc), Thresholds(min_period_days=0.5, max_period_days=15.0))
    assert abs(sig[0].period - 4.0) < 0.004 and "alias_resolved" not in sig[0].extra
```

- [ ] **Step 3: Implement `_resolve_2p`** and call it in `search()` on `out[0]` before the masked pass (so the second pass masks the resolved period). Add `"alias_resolution"` to `SEARCH_CONFIG_KEYS` and assert in `tests/unit/test_pipeline.py` that `search_version` changes when `alias_resolution` is 0.
- [ ] **Step 4: Tests, full suite, ruff, spec bullet (section 3), commit** `feat(detector): resolve the 2P alias of deep transits`.

---

### Task 4: Period-matched neighbours out to 120 arcsec in rung 4

**Files:**
- Modify: `src/skysift/vetting/rung4_novelty.py`
- Test: `tests/unit/test_rung4.py`

**Interfaces:**
- Produces: `Rung4Novelty(radius_arcsec=20.0, period_radius_arcsec=120.0)`. Every archive query uses the larger radius (box plus circle filter); a returned row counts as known when it lies within `radius_arcsec`, or within `period_radius_arcsec` with a period match (`|P - P_row| / P_row < 0.001`, or the same for `2P` and `P / 2`). Metrics add `period_matched: [{"catalog": ..., "id": ..., "period": ..., "sep_arcsec": ...}]` (strict JSON) and keep the per-catalogue counts as the number of rows that count as known.
- Consumes: the existing `_box`, `_within`, `_separation_deg`, `_tsv_rows`; the fixtures in `tests/unit/test_rung4.py` (`bundle(...)`, `TGT`, `SIG`).

- [ ] **Step 1: Write the failing tests** (`test_period_matched_neighbour_within_120_arcsec_is_known`: a KOI row 90 arcsec away with `koi_period` equal to `SIG.period` makes the rung fail with `known:cumulative:...` and lists it in `period_matched`; `test_unrelated_neighbour_beyond_20_arcsec_is_not`: the same row with a period 1.37 times `SIG.period` leaves the rung passing with `cumulative == 0`; `test_box_query_uses_the_period_radius`: the issued ra/dec predicates span 120 arcsec).
- [ ] **Step 2: Run to see them fail; implement; run; full suite; ruff; spec bullet (section 5, rung 4); commit** `feat(vetting): known objects matched by period out to 120 arcsec`.

---

### Task 5: A broken process pool never charges a target

**Files:**
- Modify: `src/skysift/pipeline.py`
- Test: `tests/unit/test_pipeline.py`

**Interfaces:**
- Produces: in `run()`, `on_vetted` catches `concurrent.futures.process.BrokenProcessPool` from `fut.result()`: every run held by `cpu_jobs` is finished with `status="error"`, `error="unavailable: worker pool broke"` (the `UNAVAILABLE_PREFIX`, so no attempt is charged), `cpu_jobs` is cleared, `summary["pool_restarts"]` increments, and the pool is rebuilt with `_cpu_pool(settings)` at most twice per sweep (a third break ends the sweep with the existing abrupt path). Test hook: `_cpu_work` calls `os._exit(3)` when `lc.meta.get("__crash_worker__")` is truthy (documented as a test-only hook in the docstring).
- Consumes: `ScriptedSource`, `_env` from `tests/unit/test_pipeline.py`.

- [ ] **Step 1: Write the failing test** `test_worker_crash_marks_runs_unavailable_and_the_sweep_continues`: three targets, the second's light curve carries `meta["__crash_worker__"] = True`, `WORKERS_CPU=1`; after `run()` the first and third targets are `done`, the second's run is `error` with the unavailable text, `summary["pool_restarts"] == 1`, and `store.attempts(...)` (or the equivalent query used by the three-attempt cap) counts zero attempts for it.
- [ ] **Step 2: Run to see it fail; implement; run; full suite; ruff; spec bullet (section 9); commit** `fix(pipeline): survive a broken worker pool without charging targets`.

---

### Task 6: The Live page's mean run time as one SQL aggregate

**Files:**
- Modify: `src/skysift/ui/queries.py` (add `mean_seconds(store)`), `src/skysift/ui/routes/live.py` (use it)
- Test: `tests/unit/test_ui_queries.py`

**Interfaces:**
- Produces: `queries.mean_seconds(store) -> float | None`: one `SELECT` returning the average of `(finished - started)` in seconds over `runs.status == 'done'`, rounded to 2 decimals; SQLite uses `(julianday(finished) - julianday(started)) * 86400`, every other dialect `extract(epoch from finished - started)`, chosen from `store.engine.dialect.name`. `summary()["mean_seconds_per_run"]` and `live._mean_seconds` both call it (the private helper is removed).
- [ ] **Step 1: Write the failing test** `test_mean_seconds_matches_python_mean_and_handles_no_runs` (seed two done runs of 10 s and 30 s plus one running run; expect 20.0; an empty store gives `None`).
- [ ] **Step 2: Implement; run `tests/unit/test_ui_queries.py tests/unit/test_ui_pages.py`; full suite; ruff; commit** `perf(ui): mean run time as a single aggregate`.

---

### Task 7: Long-period band normalisation study

**Files:**
- Create: `docs/superpowers/notes/2026-10-17-long-period-sde.md`
- Possibly modify: `src/skysift/detectors/box_dip.py` (only if the study finds a defect; then with tests in `tests/unit/test_box_dip.py`)

**Context:** In the 14 October sweep three targets had peaks at 22 to 29 days with `snr` 30 to 78 but SDE 6.5 to 7.5, all at the 9 h duration cap (gaia-dr3-2053482589315365120 at 24.82 d is the clearest; its cached file is in `.superpowers/sample-run/blobs`). The question is whether the per-band median/MAD normalisation at long periods is inflated by a comb of systematics (quarter length 93 d and its harmonics; `SYSTEMATIC_PERIODS` in rung 1) so that genuine long-period dips are suppressed, or whether these peaks are themselves systematics.

- [ ] **Step 1:** Using `BoxDip(mask_iterations=0).search(..., keep_spectrum=True)` on that curve with the 0.5 to 30 d window, write the note with: the band's median, MAD and the peak's raw power rank within its band; the fold at 24.82 d (binned, 100 bins) and whether the dip is a single event or repeats; the same peak's SDE when the spectrum power within 1 percent of each systematic period and its first five harmonics is excluded from the band's median/MAD. State a ruling: keep, or change the normalisation (then implement it with a test that pins the 24.82 d SDE before and after, and a test that the 13.769 d synthetic recovery is unchanged).
- [ ] **Step 2:** Commit the note (and any code) `docs(detector): long-period SDE normalisation study` with the ruling in the ledger.

---

### Task 8: `skysift calibrate` for the null pilot

**Files:**
- Create: `src/skysift/calibrate.py`
- Modify: `src/skysift/cli.py`, `README.md`
- Test: `tests/unit/test_calibrate.py`

**Interfaces:**
- Produces: `python -m skysift calibrate --source kbonus_bkg --limit 1000 [--nulls scrambled,inverted] [--workers-cpu N] [--out <data_dir>/calibration/<source>.jsonl] [--min-period 0.5 --max-period 30]`. For each of the `limit` highest-priority targets (same ordering as `run`, cached raw files reused through the blob and `artifacts` when present, otherwise fetched and stored like a sweep but without vetting): the detrended and pre-whitened flux is searched as is, inverted (`2 - f`) and scrambled within each quarter (seed 1), with `BoxDip(mask_iterations=0)`; one JSON line per target `{"target_id", "n_points", "real": [top 3 sde], "inverted": [...], "scrambled": [...], "real_top": {...}, "seconds"}`. Targets already present in the file are skipped (resume). `python -m skysift calibrate-report <file>` prints, per kind, median and maximum top SDE and the counts above 7, 8, 9, 10, 11, 12, 15, plus the fraction of real curves whose top peak exceeds `sde_min`. `skysift.calibrate.summarise(rows) -> dict` is the testable core.
- Consumes: `make_source`, `_fetch`-equivalent loading (reuse `pipeline._fetch` or the loader pattern), `detrend`, `prewhiten`, `BoxDip`.

- [ ] **Step 1: Write the failing tests** `test_calibrate_resumes_and_summarises` (a `ScriptedSource` with three 60 d curves and a 30 to 40 d window so the search is instant; run with `limit=2`, then `limit=3`: the file has three lines, the first two were not recomputed (check `seconds` values unchanged or a counter on the source's fetches); `summarise` returns the expected keys and counts) and `test_calibrate_report_prints_the_table` (typer `CliRunner`).
- [ ] **Step 2: Implement; tests; full suite; ruff; README section "Calibrating the detection threshold"; spec bullet (section 5, rung 1); commit** `feat(cli): calibrate command for the null pilot`.

---

### Task 9: Mask the ramp after every data gap (added 17 October 2026 from the Task 7 study)

**Files:**
- Modify: `src/skysift/lc/ops.py` (`detrend`), `src/skysift/pipeline.py` (`search_version`)
- Test: `tests/unit/test_lc_ops.py`, `tests/unit/test_box_dip.py`, `tests/unit/test_pipeline.py`

**Context:** The Task 7 note (`docs/superpowers/notes/2026-10-17-long-period-sde.md`) shows that on gaia-dr3-2053482589315365120 every epoch deeper than 3000 ppm in the 24.82 d fold falls 0.17 to 0.86 d after a data gap: thermal ramps after safe modes and downlinks that the 2 d running-median detrend leaves in the flux. They produce the false 22 to 29 d peaks and, worse, suppress genuine long-period signals: an injected 1250 ppm, 9 h box on that star scores SDE 6.2 as is and 24.6 when the first 1.0 d after each gap is masked (about 5 percent of the points).

**Interfaces:**
- Produces: `GAP_EDGE_MASK_DAYS = 1.0` and `DETREND_VERSION = 2` in `skysift.lc.ops`; `detrend(lc, window_days=2.0, gap_days=1.0, flux=None, edge_mask_days=GAP_EDGE_MASK_DAYS)` returns NaN for every point within `edge_mask_days` after a gap longer than `gap_days` (the first point of the series is not a gap edge; a gap is a step between consecutive finite times). Everything downstream (pre-whitening, detector, rungs, dashboard plots through `whitened_fnorm`) sees the same masked flux because they all call `detrend`. `search_version` adds `cfg["detrend"] = {"version": DETREND_VERSION, "edge_mask_days": GAP_EDGE_MASK_DAYS}` so earlier runs are searched again rather than mixed.
- Measurements to record in the Task 7 note (a new dated section) and in the spec bullet: the 24.82 d star's top peak and SDE with the mask; the injected 1250 ppm, 9 h box SDE with the mask (expected about 24); the pre-whitening component count on that star before and after the mask (Task 7 saw 47 to 49 of the 50 cap, possibly fitting the ramps); the fraction of points masked on the three study stars.

- [ ] **Step 1: Write the failing tests**
  - `tests/unit/test_lc_ops.py::test_detrend_masks_the_ramp_after_a_gap`: a synthetic curve with a 3 d gap and an exponential ramp of 5000 ppm decaying over 0.5 d after it; `detrend` returns NaN for every point within 1.0 d after the gap and finite values elsewhere; the number of NaN points equals the count of points in that window; with `edge_mask_days=0` nothing is masked (the old behaviour).
  - `tests/unit/test_box_dip.py::test_gap_ramps_do_not_hide_a_long_period_transit`: `make_lightcurve(n_days=1400, seed=7, noise_ppm=600)` with 1 percent ramps (decay 0.5 d) added after each quarter gap and an injected 13.0 d, 6 h, 1500 ppm transit; with the default detrend the detector's top signal is at 13.0 d within 0.01 and its SDE is at least 1.5 times the SDE obtained with `edge_mask_days=0` on the same curve (state both numbers in the assertion message). Keep this to two searches.
  - `tests/unit/test_pipeline.py`: `search_version` changes when `GAP_EDGE_MASK_DAYS` is monkeypatched to 0.
- [ ] **Step 2: Run them to see them fail; implement; run them green.** The mask is applied before the running median (so the median is not pulled by the ramp) and the masked points stay NaN in the output.
- [ ] **Step 3: Measure on the real file** (scratch script, see Task 7 context) and append the dated section to the Task 7 note.
- [ ] **Step 4: Full suite, ruff, spec section 13 bullet (17 October 2026: gap-edge mask, the numbers, why), commit** `fix(lc): mask the ramp after every data gap before detrending`.

---

## Final whole-branch review

Dispatch the final reviewer on the most capable model with the whole branch diff against `main`; ONE fix wave; then `superpowers:finishing-a-development-branch` (merge decision stays with Vikram).
