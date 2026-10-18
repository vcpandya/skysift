# SkySift vetting ladder version 3 (published best practice) — plan, 18 October 2026

**Goal:** implement the gap analysis of `docs/superpowers/specs/2026-10-18-skysift-vetting-best-practice-design.md`
(the Kepler DR25 Robovetter tests SkySift lacks), as ladder version 3, plus `skysift inject` as a standing
completeness regression. The search version does not change, so the calibration pilot running in
`.superpowers/sample-run` stays valid.

**Spec:** the design document above; section numbers below refer to it. Thresholds come from Thompson et al. 2018
Appendix A (T18) and are quoted in the spec table; where SkySift chooses a value the spec says so.

## Global constraints
- Only `.venv/Scripts/python.exe -m <module>`; never a venv `.exe` launcher; never bare `python`. `PYTHONUTF8=1`;
  pytest with `-p no:cacheprovider`. Run only the test files named in the task (the calibration pilot occupies six
  CPU workers; never start the full suite or `tests/unit/test_box_dip.py`).
- `.venv/Scripts/python.exe -m ruff check src tests` clean before each commit. Commit trailer
  `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`. Work on main directly, commit only your task's files,
  never push.
- Do not touch `src/skysift/detectors`, `src/skysift/lc/prewhiten.py`, `src/skysift/lc/ops.py::detrend` or
  `Thresholds` fields that feed `search_version` (`sde_min`, `snr_min`, `min_events`, the period window, the
  pre-whitening fields). New `Thresholds` fields are allowed only where a task names them; check
  `pipeline.search_version` and keep them out of it.
- Synthetic fixtures come from `tests/synthetic.py` (`make_lightcurve`, `inject_transit`, `inject_v_eclipse`); add
  helpers there when two test modules need one.
- TDD: write the failing test, run it (RED), implement, run it (GREEN); quote both in the report.
- Every task writes its report to the path given in the dispatch and ends with one of DONE, DONE_WITH_CONCERNS,
  NEEDS_CONTEXT, BLOCKED.

## File structure
- `src/skysift/lc/transit_model.py` (new, Task 1): uniform-disc transit model and `fit_transit`.
- `src/skysift/lc/ops.py` (Task 2): `phase_significance`, `sweet`, `event_quality` (coverage, SES, Chases).
- `src/skysift/vetting/rung1_events.py` (new, Task 3): `Rung1Events`.
- `src/skysift/vetting/rung2_shape.py`, `vetting/base.py`, `pipeline.py` (Task 4).
- `src/skysift/vetting/rung3_stability.py`, `vetting/rung4_novelty.py`, `config.py`, `config/default.yaml` (Task 5).
- `src/skysift/inject.py` (new), `cli.py` (Task 7).
- `pipeline.py` (`build_ladder`, `DEFAULT_LADDER_VERSION`), `ui/templates/signal.html`, spec section 13, README
  (Task 6).

---

### Task 1: uniform-disc transit model and fit (`lc/transit_model.py`)

**Files:** create `src/skysift/lc/transit_model.py`; test `tests/unit/test_transit_model.py`.

**Produces:**
```python
@dataclass
class TransitFit:
    k: float            # R_p / R_star
    b: float            # impact parameter
    a_over_r: float     # semi-major axis over stellar radius
    depth_ppm: float    # model depth at mid-transit (k^2 x 1e6 when b + k <= 1; the overlap fraction otherwise)
    t14_days: float     # first-to-fourth contact duration of the fitted model
    chi2: float
    n_bins: int
    ok: bool            # False when the fold has fewer than 20 usable bins or the solver did not converge

def uniform_disc_flux(dt_days: np.ndarray, period: float, k: float, b: float, a_over_r: float) -> np.ndarray
def fit_transit(time, fnorm, period, t0, duration_days) -> TransitFit
```

**Model.** Circular orbit; the planet's projected separation from the star centre in stellar radii at time offset
`dt` from mid-transit is `z = sqrt((a_over_r sin(2 pi dt / P))^2 + (b cos(2 pi dt / P))^2)`. The uniform-source
occulted fraction for discs of radii 1 and k at separation z (Mandel and Agol 2002, equation 1 with no limb
darkening): 0 when z >= 1 + k; k^2 when z <= 1 - k (and k < 1); 1 when z <= k - 1 (k >= 1); otherwise
`(k^2 kappa0 + kappa1 - sqrt((4 z^2 - (1 + z^2 - k^2)^2) / 4)) / pi` with
`kappa1 = arccos((1 - k^2 + z^2) / (2 z))`, `kappa0 = arccos((k^2 + z^2 - 1) / (2 k z))` (clip the arccos
arguments to [-1, 1]). Flux = 1 - occulted fraction. `t14_days = (P / pi) arcsin(sqrt((1 + k)^2 - b^2) / a_over_r)`
(NaN when (1 + k)^2 <= b^2 or the argument exceeds 1).

**Fit.** Fold on (period, t0) as `lc.ops.fold_phase`; keep points within +-3 durations of mid-transit; bin at
`SHAPE_BINS_PER_DURATION` (= 4, import from `lc.ops`) bins per duration with the bin error = standard error of the
bin's points (floor: half the robust point scatter over sqrt(count), as `shape_contrast` does); require >= 5 points
per bin and >= 20 usable bins (else `ok=False`, parameters NaN). `scipy.optimize.least_squares` on the weighted
residuals with parameters (k, b, a_over_r) and a free baseline level `c` (model x c); bounds k in [1e-3, 2.0], b in
[0, 3.0] (the bound 1 + k is enforced by the model returning 1.0 flux when b >= 1 + k), a_over_r in [1.5, 1e4], c in
[0.9, 1.1]; start k = sqrt(max(box depth, 1e-6)) where the box depth is the mean of 1 - bin means inside +-duration/2,
b = 0.3, a_over_r from `duration_days ~ P / (pi a_over_r)` (so `a_over_r = P / (pi duration_days)`, clipped to the
bounds), c = 1. `ok = result.success and n_bins >= 20`. `depth_ppm = (1 - uniform_disc_flux(0, ...)) x 1e6`.

**Tests** (real fits on `tests.synthetic` curves, seed 11, noise 500 ppm, 1400 d):
- `test_uniform_disc_flux_limits`: at dt = 0 with k = 0.1, b = 0, a_over_r = 20: flux = 1 - 0.01 within 1e-9; at
  dt = P / 4: flux = 1; with b = 1.2 and k = 0.1 the flux is 1 everywhere (no transit).
- `test_fit_recovers_a_box_like_planet`: `inject_transit` is a box, so inject with the model instead: multiply the
  flux by `uniform_disc_flux(dt, P=3.7, k=0.08, b=0.2, a_over_r=12)` at each cadence (write a small helper
  `inject_model(lc, period, t0, k, b, a_over_r)` in `tests/synthetic.py`), duration for the fit = the model's t14.
  Assert `ok`, k within 0.08 +- 0.01, k + b < 1.04, depth_ppm within 15 percent of 6400.
- `test_fit_flags_a_grazing_eclipse`: inject with k = 0.5, b = 1.3, a_over_r = 8, P = 5.2 (grazing: depth of a few
  percent with a V shape). Assert `ok` and k + b > 1.04.
- `test_fit_on_a_v_eclipse_fixture`: `inject_v_eclipse(lc, 24.0, t0, 11/24, 40000)` from the detector tests: the fit
  gives k + b > 1.04 (state the fitted values in the report).
- `test_fit_is_not_ok_on_a_sparse_fold`: a 60 d curve with a 25 d period (two events): `ok` is False or n_bins < 20,
  and no exception.

Commit: `feat(lc): uniform-disc transit model and fold fit (k, b, a/R*)`.

---

### Task 2: fold-level helpers in `lc/ops.py` (`phase_significance`, `sweet`, `event_quality`)

**Files:** modify `src/skysift/lc/ops.py` (append after `shape_contrast`); test `tests/unit/test_lc_ops.py`.

**Produces:**
```python
def phase_significance(time, fnorm, period, t0, duration_days) -> PhaseSignificance
@dataclass
class PhaseSignificance:
    phases: np.ndarray        # cycle fraction in [-0.5, 0.5), step = (duration / period) / SHAPE_BINS_PER_DURATION
    sigma: np.ndarray         # depth significance of the box centred at each phase (positive = dip)
    sigma_pri: float          # at phase 0
    sigma_sec: float; phase_sec: float      # deepest box at |phase| >= 1.5 durations from the primary
    sigma_ter: float          # next deepest, also >= 1.5 durations from the secondary
    sigma_pos: float          # largest brightening (most negative sigma, as a positive number) away from the primary
    fa2: float                # sqrt(2) * erfcinv(duration / period)
    ms2: float; ms3: float    # FA2 - (sigma_pri - sigma_ter); FA2 - (sigma_pri - sigma_pos)

def sweet(time, flux, period, depth_ppm) -> SweetResult
@dataclass
class SweetResult:
    factor: float             # 0.5, 1.0 or 2.0: the period multiple of the strongest sine
    amplitude_ppm: float
    snr: float                # amplitude over its least-squares error

def event_quality(time, fnorm, period, t0, duration_days) -> EventQuality
@dataclass
class EventQuality:
    epochs: np.ndarray        # every epoch with at least one point within one duration of mid-transit
    coverage: np.ndarray      # points within +-1 duration / expected (2 duration / median cadence), per epoch
    ses: np.ndarray           # depth / err per epoch from _box_depth; NaN when the box holds fewer than 3 points
    chases: np.ndarray | None # C_i per epoch when len(epochs) <= CHASES_MAX_EVENTS, else None
```
Constants in `ops.py`: `EVENT_COVERAGE_MIN = 0.75`, `CHASES_MAX_EVENTS = 5`, `CHASES_FRACTION = 0.8`,
`CHASES_MIN = 0.01`, `SWEET_FACTORS = (0.5, 1.0, 2.0)`.

**phase_significance.** Fold as `fold_phase`; for each trial phase c (step as above, covering [-0.5, 0.5)), box =
points with circular distance to c below duration/2; baseline = points at least `PRIMARY_MASK_DURATIONS` (1.5)
durations from the box centre AND from the primary (phase 0); depth = 1 - mean(box) / median(baseline); sigma =
depth / (1.4826 MAD(baseline) / sqrt(n_box)). Require n_box >= 5 and n_baseline >= 50, else NaN at that phase. The
primary's own sigma uses the baseline at least 1.5 durations from phase 0 only. Secondary: argmax of sigma over
phases at least 1.5 durations from 0; tertiary: argmax over phases at least 1.5 durations from both 0 and
`phase_sec`; positive: max of -sigma over phases at least 1.5 durations from 0. `fa2 = sqrt(2) *
scipy.special.erfcinv(duration_days / period)`. Reuse `_circular`.

**sweet.** For each factor f in `SWEET_FACTORS`: design matrix [1, cos(2 pi t / (f P)), sin(2 pi t / (f P))] on the
finite points, `np.linalg.lstsq`; amplitude = hypot(a, b); covariance from the residual variance and
`inv(X^T X)`; amplitude error by propagation (`sqrt((a^2 var_a + b^2 var_b) / amp^2)`, and with the covariance
term when it is finite). Keep the factor with the largest snr. `depth_ppm` is accepted for the caller's rule and
returned untouched in no field (the rung compares).

**event_quality.** Epochs = unique `round((t - t0) / P)` over points with |fold| < duration. Coverage per epoch =
count of points with |fold| < duration / (2 duration / median cadence of the finite times). SES per epoch = `_box_depth`
on that epoch's points (as `event_depths` does) -> depth / err, NaN when None. Chases, only when the number of
epochs <= `CHASES_MAX_EVENTS`: for each epoch with finite SES, window = points with |t - t_event| <= P / 10 (t_event
= t0 + epoch P); slide a box of width duration across the window at every cadence centre; at each centre, box =
points within duration/2, baseline = the rest of the window at least 1.5 durations from the centre, significance =
(median(baseline) - mean(box)) / (1.4826 MAD(baseline) / sqrt(n_box)) (NaN when n_box < 3 or n_baseline < 10);
ignore centres within 1.5 durations of t_event; dt = min |centre - t_event| over centres with |significance| >
`CHASES_FRACTION` x SES_i (dt = P/10 when none); C_i = min(dt, P/10) / (P/10). Epochs without a finite SES get NaN.

**Tests** (`tests/unit/test_lc_ops.py`, append; seed 13, noise 600 ppm, 1400 d unless stated):
- `test_phase_significance_of_a_clean_transit_is_unique`: 4.1 d, 3 h, 1500 ppm box: sigma_pri > 10, ms2 < 2.0,
  ms3 < 4.0, and the sigma array has its maximum at |phase| < step.
- `test_phase_significance_flags_two_equal_dips`: the same box plus a second box of equal depth at phase 0.37 (t0 +
  0.37 P): ms2 > 2.0.
- `test_phase_significance_flags_a_brightening`: the box plus a brightening of 1500 ppm (flux x (1 + 1.5e-3)) of 3
  h at phase 0.3: ms3 > 4.0.
- `test_sweet_finds_the_wave_at_the_period`: flux x (1 + 2e-3 sin(2 pi t / 2.3)) on a quiet curve: `sweet(..., 2.3,
  500)` returns factor 1.0, amplitude within 10 percent of 2000 ppm, snr > 50.
- `test_sweet_finds_the_half_period_wave`: wave at 1.15 d, period argument 2.3: factor 0.5.
- `test_sweet_is_quiet_on_a_transit`: a 2000 ppm box at 2.3 d on noise: snr < 50.
- `test_event_quality_coverage_and_ses`: a 30.0 d, 6 h, 3000 ppm box over 1400 d: every coverage within [0.9, 1.1]
  except any epoch that falls in a gap (list them in the report), SES of covered epochs > 3; delete the points of
  one epoch's second half (NaN) and assert its coverage < 0.75.
- `test_event_quality_chases_is_near_one_for_clean_events` (the same curve with 4 events in a 130 d window: use
  `n_days=130`): chases is not None and every finite C_i > 0.8.
- `test_event_quality_chases_drops_to_zero_next_to_a_comparable_dip`: add a second 3000 ppm dip 1.2 d after one
  event: that event's C_i < 0.5 (state the value), the others > 0.8.
- `test_event_quality_chases_is_none_above_five_events`: the 30 d box over 1400 d (more than 5 events): chases is
  None.

Commit: `feat(lc): fold uniqueness, SWEET sine fit and per-event quality helpers`.

---

### Task 3: rung 1 events (`vetting/rung1_events.py`)

**Files:** create `src/skysift/vetting/rung1_events.py`; test `tests/unit/test_rung1_events.py`. Do not edit
`pipeline.py` (Task 6 registers the rung and bumps the ladder version).

**Consumes** `lc.ops.event_quality` (Task 2) and the constants `EVENT_COVERAGE_MIN`, `CHASES_MIN`.
**Produces** `class Rung1Events: name = "rung1_events"; cost = 0.02; run(s, b) -> TestResult`.
Constants in the module: `GAPPED_FRACTION_MAX = 0.5`, `ZUMA_SES_MIN = -1.0`, `SES_DOMINANCE_MIN_PERIOD = 90.0`,
`SES_DOMINANCE_MAX = 0.8`, `CHASES_MEDIAN_MIN = 0.8`.

**Rules, in order** (q = `event_quality(b.lc.time, b.fnorm, s.period, s.t0, s.duration)`; th = `b.thresholds`):
1. `covered = q.coverage >= EVENT_COVERAGE_MIN`. FAIL "gapped events (f of n covered)" when `covered.mean() <=
   GAPPED_FRACTION_MAX` (n = len(q.epochs); with n == 0 the fraction is 0).
2. `has_ses = isfinite(q.ses)`. When `has_ses.sum() < th.min_events`: the SES rules are not applied (metric
   `ses_rules_applied: False`); `n_good = covered.sum()`.
   Otherwise `bad = has_ses & (q.ses < ZUMA_SES_MIN)` (Zuma); if `q.chases is not None`: `bad |= isfinite(q.chases)
   & (q.chases < CHASES_MIN)`; `n_good = (covered & ~bad).sum()`; `ses_max_over_mes = max(ses[good]) /
   sqrt(sum(ses[good]^2))` over the good events with an SES (NaN when none).
3. FAIL "n_good k < min_events m" when `n_good < th.min_events`.
4. FAIL "single event dominates (r)" when the SES rules apply, `s.period > SES_DOMINANCE_MIN_PERIOD` and
   `ses_max_over_mes > SES_DOMINANCE_MAX`.
5. FAIL "chases median c < 0.8" when the SES rules apply, `q.chases is not None`, at least one finite C_i, and
   `nanmedian(q.chases) < CHASES_MEDIAN_MIN`.
6. PASS. Metrics on every result: `n_events_with_data`, `n_covered`, `n_good`, `coverage` (list, 3 decimals),
   `ses` (list, 2 decimals, None for NaN), `ses_rules_applied`, `ses_max_over_mes` (None when NaN), `chases` (list
   or None), `chases_median` (None when not applied).

**Tests** (`tests/unit/test_rung1_events.py`; build `DataBundle` as `tests/unit/test_rungs_1_3.py` does, thresholds
`Thresholds()`, a `Signal` with the injected period, t0, duration; seed 17, noise 600 ppm):
- `test_passes_a_clean_long_period_planet`: 40 d, 6 h, 3000 ppm over 1400 d: PASS, `n_good >= 3`,
  `ses_rules_applied` True.
- `test_fails_gapped_events`: the same injection but NaN every point within one duration of the events except two
  of them (so the covered fraction is <= 0.5): FAIL, reason starts "gapped events".
- `test_fails_when_one_event_dominates_a_long_period_signal`: 95 d period, 6 h, three events: make one event 6000
  ppm and the other two 400 ppm (inject separately per epoch): FAIL "single event dominates" (state the ratio).
- `test_dominance_rule_does_not_apply_below_90_days`: the same depths at 40 d: no "single event dominates" FAIL
  (the result may be PASS or another FAIL; assert the reason does not start with "single event").
- `test_fails_chases_when_events_sit_in_comparable_noise`: 130 d curve, 30 d, 6 h, 3000 ppm box with a 3000 ppm
  dip 1.2 d after each of the four events: FAIL "chases median".
- `test_short_period_signal_skips_the_ses_rules`: 0.6 d, 1 h, 2000 ppm over 1400 d (two points per event): the
  result is PASS with `ses_rules_applied` False.

Commit: `feat(vetting): rung 1 events: coverage, gapped fraction, single-event dominance, Chases, Zuma`.

---

### Task 4: rung 2 uniqueness, SWEET, transit fit and the secondary exceptions

**Files:** modify `src/skysift/vetting/rung2_shape.py`, `src/skysift/vetting/base.py` (one field),
`src/skysift/pipeline.py` (`whitened_flux` return and `_cpu_work` bundle only), `src/skysift/config.py`
(`Thresholds.ms2_max = 2.0`, `ms3_max = 4.0`; keep them OUT of `pipeline.search_version`: read that function and
assert in a test that the version string is unchanged by these two fields), `config/default.yaml` (the two fields);
tests `tests/unit/test_rungs_1_3.py` (append), `tests/unit/test_pipeline.py` (one test).

**Consumes** `lc.transit_model.fit_transit` (Task 1), `lc.ops.phase_significance`, `lc.ops.sweet` (Task 2),
`vetting.rung1_detection._stellar_param` and `FALLBACK_STAR`.

**base.py:** `DataBundle.flux_detrended: np.ndarray | None = None` (the detrended flux before pre-whitening).
**pipeline.py:** `whitened_flux` returns a 4-tuple `(residual, record, settings, detrended)`; update every caller
(`grep -n whitened_flux src tests`), `_cpu_work` passes `flux_detrended=` to the bundle; `_vetting_bundle` masks it
like `fnorm` when a mask is present.

**rung2_shape.py, new order** (keep every existing metric and reason string):
1. existing sinusoidal (shape contrast) FAIL;
2. SWEET on `b.flux_detrended` when present (skip, metric `sweet: None`, when absent): `sw = sweet(lc.time,
   b.flux_detrended, s.period, depth)` where depth = the stacked or per-event depth already computed (`m["depth_ppm"]`
   after the stacked branch; compute the stacked branch first, then SWEET). FAIL "sinusoidal (SWEET f, A ppm, snr)"
   when `sw.snr > SWEET_SNR` and `sw.amplitude_ppm > depth` and `s.period < SWEET_PLANET_MAX_PERIOD`; FAIL
   "ellipsoidal (SWEET EB ...)" when `sw.snr > SWEET_SNR` and `sw.amplitude_ppm < depth` and `sw.amplitude_ppm >
   SWEET_EB_MIN_AMPLITUDE_PPM` and `s.period < SWEET_EB_MAX_PERIOD`. Constants in the module: `SWEET_SNR = 50.0`,
   `SWEET_PLANET_MAX_PERIOD = 5.0`, `SWEET_EB_MIN_AMPLITUDE_PPM = 5000.0`, `SWEET_EB_MAX_PERIOD = 10.0`. Metrics
   `sweet_factor`, `sweet_amplitude_ppm`, `sweet_snr`.
3. uniqueness: `ps = phase_significance(...)`; metrics `sigma_pri, sigma_sec, phase_sec, sigma_ter, sigma_pos, fa2,
   ms2, ms3` (None for NaN); FAIL "not unique (MS2 x > y)" when finite and `ps.ms2 > th.ms2_max`; FAIL "positive
   event as significant as the transit (MS3 x > y)" when finite and `ps.ms3 > th.ms3_max`.
4. existing odd/even FAIL (unchanged);
5. transit fit once: `fit = fit_transit(lc.time, fn, s.period, s.t0, s.duration)`; metrics `fit_ok, fit_k, fit_b,
   fit_a_over_r, fit_depth_ppm, fit_t14_h, fit_chi2` (None for NaN / not ok).
6. secondary, with the exceptions evaluated when a secondary would fail (either the phase-0.5 rule or the free-phase
   rule as today):
   (a) period doubling: `abs(ps.sigma_pri - ps.sigma_sec) < ps.fa2` and `abs(ps.phase_sec - 0.5) * s.period <= 0.25 *
   s.duration` -> UNDETERMINED "equal secondary at phase 0.5: period may be 2 x (P d)", metric `suggested_period = 2 P`;
   (b) planet occultation: with `radius, _ = _stellar_param(meta, "radius")`, `mass, _ = _stellar_param(meta,
   "mass")`, `R_SUN_M = 6.957e8`, `R_EARTH_M = 6.371e6`, `G M_sun = 1.32712440018e20 m^3 s^-2`: `rp_rearth = fit.k *
   radius * R_SUN_M / R_EARTH_M`; `a_m = (GM_sun * mass * (P * 86400)^2 / (4 pi^2))^(1/3)`; `albedo = (d_sec / d_pri) *
   (a_m / (fit.k * radius * R_SUN_M))^2` with d_sec the failing secondary's depth (ppm, the phase-0.5 or free-phase
   one) and d_pri = `m["depth_ppm"]`; when `fit.ok and albedo < 1 and rp_rearth < 30 and d_sec < 0.1 * d_pri and
   fit.b < 0.95` the secondary does not fail: continue with metrics `occultation_albedo`, `planet_radius_rearth` and
   reason "secondary consistent with planet occultation" on the final PASS;
   otherwise the existing FAILs as today.
7. V shape: FAIL "V-shaped or deep (k + b = x > 1.04)" when `fit.ok and fit.k + fit.b > V_SHAPE_MAX` (`V_SHAPE_MAX
   = 1.04` in the module).
8. PASS (with the occultation reason when (b) applied).

**Tests** (append to `tests/unit/test_rungs_1_3.py`; seed 19, noise 600 ppm):
- `test_rung2_fails_two_equal_dips_as_not_unique`: 4.1 d, 3 h, 1500 ppm box plus an equal box at phase 0.37: FAIL
  reason starts "not unique".
- `test_rung2_sweet_fails_a_short_period_wave_removed_by_detrending`: a bundle whose `fnorm` is a 2.3 d, 2 h, 1500
  ppm box on noise and whose `flux_detrended` is that flux times (1 + 3e-3 sin(2 pi t / 2.3)): FAIL reason starts
  "sinusoidal (SWEET".
- `test_rung2_sweet_eb_fails_ellipsoidal_variation`: `fnorm` a 6000 ppm, 3 h box at 1.4 d; `flux_detrended` that
  flux times (1 + 5.5e-3 sin(2 pi t / 0.7)) (the half-period wave of a contact binary): FAIL reason starts
  "ellipsoidal".
- `test_rung2_doubles_the_period_of_equal_eclipses`: inject one 3 h, 4000 ppm box train at P_inj = 3.0 d and vet it
  as a signal with `period = 6.0`, the same `t0` and duration (the 6.0 d fold then shows an equal "secondary" at phase
  0.5, which is the odd transit of the train; odd and even depths on the 6.0 d fold are equal, so the odd/even rule
  passes): expected status UNDETERMINED, reason starts "equal secondary", `suggested_period == 12.0` within 1e-6.
- `test_rung2_keeps_a_planet_with_a_shallow_occultation`: the albedo rule `A_g = (d_sec / d_pri) (a / R_p)^2 < 1`
  uses `a` from the meta mass and the period, so the fixture is a close-in giant planet on a very low-mass host:
  1500 d curve, noise 100 ppm, seed 29; inject with `tests.synthetic.inject_model` (Task 1) k = 0.3, b = 0.3,
  a_over_r = 3, P = 2.2 d (primary depth about 90000 ppm) plus a 60 ppm box of the model's t14 at phase 0.5; target
  meta radius 0.2 and mass 0.02. Then R_p = 0.3 x 0.2 R_sun = 6.5 R_earth (< 30), a = 1.37e9 m, (a / R_p)^2 = 1080,
  d_sec / d_pri = 6.7e-4, albedo about 0.72 (< 1), b = 0.3 (< 0.95), d_sec < 0.1 d_pri. The 60 ppm secondary over
  about 680 events at 100 ppm noise is detected at the phase-0.5 rule (state the measured `secondary_sigma`; if it
  is below 3 sigma, raise the secondary depth and lower the meta mass together so the albedo stays below 1, and record
  the values used). Assert PASS, reason "secondary consistent with planet occultation", `occultation_albedo` < 1.
- `test_rung2_fails_a_v_shaped_eclipse`: `inject_v_eclipse(lc, 9.0, t0, 6/24, 30000)`: FAIL reason starts
  "V-shaped" (odd/even and secondary pass: equal V eclipses, no secondary).
- `tests/unit/test_pipeline.py::test_search_version_ignores_the_rung_2_thresholds`: `search_version(det, th)` equals
  `search_version(det, th.model_copy(update={"ms2_max": 9.0, "ms3_max": 9.0}))`.

Commit: `feat(vetting): rung 2 uniqueness (MS2/MS3), SWEET, transit fit, V shape and the secondary exceptions`.

---

### Task 5: rung 3 and rung 4 verdict changes

**Files:** modify `src/skysift/vetting/rung3_stability.py`, `src/skysift/vetting/rung4_novelty.py`,
`src/skysift/config.py` (`Thresholds.aperture_sigma = 3.0`, outside `search_version`), `config/default.yaml`; tests
`tests/unit/test_rungs_1_3.py`, `tests/unit/test_rung4.py`.

**Rung 3:**
- the season-dependent depth verdict becomes `TestStatus.UNDETERMINED` with the same reason string; update the two
  existing tests that assert FAIL (`test_rung3_fails_on_season_dependence`, `test_rung3_single_deviant_season_...`,
  `test_stacked_seasons_catch_a_half_depth_season`) to assert UNDETERMINED, renaming them to say "flags".
- aperture-dependent depth: when `b.fnorm_alt is not None`, on the path already taken (per event: `event_depths` on
  `fnorm_alt` -> `alt.mean`, `alt.err_scaled`; stacked: `stacked_depth(...)` -> `depth_ppm`, `err_ppm`), `z = (d_alt -
  d) / sqrt(e_alt^2 + e^2)` with d, e the primary flux's depth and error already in hand; metrics `alt_depth_ppm`,
  `alt_depth_err_ppm`, `aperture_z` (None when not measurable); after the season check, UNDETERMINED
  "aperture-dependent depth (z sigma)" when finite and `abs(z) > th.aperture_sigma`. Keep `sap_psf_ratio`.
- tests: `test_rung3_flags_an_aperture_dependent_depth`: `make_lightcurve` then inject a 4000 ppm box into `flux`
  and only a 1000 ppm box (same ephemeris) into `flux_alt` (set `lc.flux_alt` explicitly), 3.3 d, 3 h, seed 23: the
  result is UNDETERMINED with reason starting "aperture-dependent"; `test_rung3_passes_equal_depths_in_both_fluxes`:
  the same box in both: PASS with `abs(aperture_z) < 3`.

**Rung 4:** in `run`, after a period match is found: when the matched rows all carry an epoch (`epoch_offset_h` is not
None) and none `epoch_agrees`, return UNDETERMINED with reason `f"period matches {catalog} {id} but epoch differs
({offset_h:.1f} h)"` and the same metrics; otherwise the present FAIL. Test in `tests/unit/test_rung4.py` with the
existing fake-fetch fixtures: a KOI row at the signal's period with `koi_time0bk` shifted by 0.3 x period ->
UNDETERMINED; the same row with the signal's epoch -> FAIL as today; a row without `koi_time0bk` -> FAIL as today.

Commit: `feat(vetting): seasonal and aperture depth differences route to review; epoch mismatch is undetermined`.

---

### Task 6: ladder version 3, registration, dashboard sentences, documentation

**Files:** `src/skysift/pipeline.py` (`build_ladder` adds `Rung1Events()`; `DEFAULT_LADDER_VERSION = "3"` with a
comment listing what changed), `src/skysift/ui/templates/signal.html` (two sentences), `tests/unit/test_pipeline.py`,
`tests/unit/test_ui_review.py`, `docs/superpowers/specs/2026-10-13-skysift-design.md` section 13 (one dated bullet
pointing at the best-practice spec), `README.md` (the ladder paragraph lists the new rules in one sentence each).

- `build_ladder(version="3", with_novelty=True)` returns rungs in cost order `rung1_detection, rung1_events,
  rung2_shape, rung3_stability[, rung4_novelty]`; test asserts the names.
- signal.html header line: when a rung result has metric `suggested_period`, add "· the secondary matches the
  primary; the true period may be {{ suggested_period }} d"; when `occultation_albedo` is present, "· secondary
  consistent with a planet occultation (albedo {{ v }})". Follow the existing pattern of the restored-waves sentence;
  one test each in `test_ui_review.py` using the seed helpers of `tests/unit/ui_seed.py`.
- the end-to-end pipeline test (`test_run_end_to_end_with_fake_source_and_resume`) already reads
  `pipeline.DEFAULT_LADDER_VERSION`; run `tests/unit/test_pipeline.py` fully.

Commit: `feat(pipeline): ladder version 3 (rung 1 events, uniqueness, SWEET, transit fit, review routing)`.

---

### Task 7: `skysift inject` and `skysift inject-report`

**Files:** create `src/skysift/inject.py`; modify `src/skysift/cli.py` (two commands); tests
`tests/unit/test_inject.py`, `tests/unit/test_cli.py` (append). Read `docs/superpowers/notes/scripts/inject_recovery.py`
and `inject_rednoise.py` for the exact injection recipe and the red-noise SNR; they stay where they are.

**`inject.py`:**
```python
SNR_DEFAULT = (7.0, 10.0, 15.0, 25.0)
def plan_injections(lc, th, rng, per_star, snr_targets, own_periods) -> list[dict]   # period, t0, duration, depth_ppm, snr_nominal
def inject_box(lc, period, t0, duration, depth_ppm) -> LightCurve
def red_noise_snr(time, flux_whitened, period, duration, depth_ppm) -> float          # duration-wide bins, as inject_rednoise.binned_scatter
def score(injection, signals_with_results) -> dict                                   # found, ranked_first, hit (period, sde, outcome, rung results)
def run(source, store, blob, settings, filter, limit, per_star, snr_targets, out, workers_cpu, seed=1) -> dict
def summarise(rows) -> dict; def format_table(summary) -> str
```
`run` reuses `calibrate._load` to fetch or read the cached raw curve, computes the star's own peaks by one
`search_with_restore` on the real flux (periods to avoid within 2 percent and their 2x, 1/2), plans `per_star`
injections (one per SNR target, cycling), and runs `pipeline._cpu_work` on each injected curve with `with_novelty=False`
in a `ProcessPoolExecutor(max_workers=workers_cpu)`; one JSON line per injection with `target_id, search_version,
ladder_version, period, t0, duration, depth_ppm, snr_nominal, snr_red, found, ranked_first, hit`. "Found" = a returned
signal within 1 percent of the injected period or of its 2, 1/2, 3, 1/3 multiple. Resumable: lines already present
for a target skip it. `summarise` tabulates found / ranked first / pass rungs 1 to 3 against nominal SNR bins (the
targets) and red-noise SNR bins (0-5, 5-7, 7-9, 9-11, 11-14, 14-20, 20+), by period (0.8-3, 3-10, 10-25, 25+ d), and
the median depth ratio.

**CLI:** `inject` with options `--source` (required), `--limit` (default 30), `--per-star` (4), `--snr`
("7,10,15,25"), `--workers-cpu`, `--out` (default `<data_dir>/injections/<source>.jsonl`), `--min-period/--max-period`,
`--gmag-max`, `--background-only/--all`, `--seed` (1); `inject-report FILE`. Mirror `calibrate` for the context,
warnings and exit codes.

**Tests:** `test_inject.py`: `plan_injections` avoids the own periods and produces depths that give the requested
nominal SNR (recompute from the formula); `score` marks found / ranked_first on a synthetic result list; `run` on the
fake source of `tests/unit/test_calibrate.py` with `limit=1, per_star=2, workers_cpu=1` writes 2 lines with the
expected keys and is resumable (a second run adds nothing). `test_cli.py`: `inject --help` and `inject-report` on a
two-line file print the table.

Commit: `feat(cli): skysift inject and inject-report, completeness as a standing regression`.

---

## Order
Tasks 1, 2, 5 and 7 touch disjoint files and run in parallel. Task 3 needs Task 2; Task 4 needs Tasks 1 and 2; Tasks 3
and 4 touch disjoint files and run in parallel after those. Task 6 last, then a whole-change review and a run of
`tests/unit/test_rungs_1_3.py tests/unit/test_rung1_events.py tests/unit/test_rung4.py tests/unit/test_lc_ops.py
tests/unit/test_transit_model.py tests/unit/test_pipeline.py tests/unit/test_inject.py tests/unit/test_cli.py
tests/unit/test_ui_review.py`, and `skysift inject --limit 30` on the cached sample curves (6 workers are taken by the
pilot: run it with `--workers-cpu 2` or after the pilot) to measure what ladder version 3 costs in completeness.
