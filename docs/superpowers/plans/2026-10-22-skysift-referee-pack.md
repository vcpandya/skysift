# SkySift Referee-Ready Pack Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every ephemeris and shape number in the dossier and the pack carries a 1-sigma error from a method with a named precedent, an independent TRICERATOPS false-positive estimate sits beside SkySift's own, the pixel rung's centroid error carries a measured systematic floor, and the live Zenodo gate refuses a record that lacks any of them.

**Architecture:** One new pure module, `skysift.ephemeris` (per-transit times, the linear ephemeris, a bootstrap over transits, parity depths, the secondary limit), feeds new blocks of `dossier_data.json`; `skysift.pack` prints them in every text and file; `skysift.crosscheck` exports the inputs for `tools/triceratops_check.py` (run in its own environment) and imports its result into a new `dossiers.cross_check` column; `lc/pixels.py` gains the floor constant and `tools/centroid_floor.py` measures it on the live pixel files; `recheck.wants_pixels` reopens rung 5 rows below the current rule version.

**Tech Stack:** Python 3.12, numpy, scipy (`least_squares` through `transit_model.fit_transit`), SQLAlchemy, typer, Jinja2, pytest. TRICERATOPS 1.1.0 in `.venv-triceratops` (uv, Python 3.12), never in `.venv`.

**Spec:** `docs/superpowers/specs/2026-10-22-skysift-referee-pack-design.md` (authority), with the checklist `docs/superpowers/notes/2026-10-22-referee-checklist.md`, on top of `docs/superpowers/specs/2026-10-20-skysift-dossiers-design.md` (sections 4.2, 5.4, 5.9) and the partial-box spec `docs/superpowers/specs/2026-10-22-skysift-pixel-partial-box-design.md` (merged into main before Task 6).

## Global Constraints

- Branch `feat/referee-pack` in the worktree `C:\Research\skysift\.worktrees\referee-pack` (off main 269ad97). Conventional commits ending with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`. Stage files by name. No `git stash`. Never push, never publish.
- Every Python command is `PYTHONUTF8=1 C:\Research\skysift\.venv\Scripts\python.exe -m <module>` (the engine's venv serves the worktree; never a bare `python`, never the `.exe` launchers under `.venv\Scripts` other than `python.exe`). pytest always with `-p no:cacheprovider`. Anything longer than two minutes runs in the background at BelowNormal priority.
- `python -m ruff check src tests tools` clean (line length 120, checked by hand since E501 is not selected). Tests stay off the network (the `fake_fetch` pattern; the cross-check tool is never imported by a test with triceratops present).
- Copy rules: no em-dashes anywhere; British spelling except the `-ize` family; "candidate", never "discovery" or "confirmed" before a reviewer approval; every probability keeps its name and its "not covered" sentence; prose in Vikram's measured voice.
- Nothing installs into `.venv`. TRICERATOPS goes into `.venv-triceratops` (git-ignored by a new `.venv-*/` line).
- No secrets in logs or files. Never kill a process other than your own.
- Numbers on the page and in the pack: errors to two significant figures and the value to the error's last digit (`pack._decimals`); BJD values with four decimals; the period to the digit of its error.
- Stored JSON stays strict (no NaN or inf): `None` for a missing number, through `jsonutil`.

## Review Focus

1. **A signal with fewer than three measurable transits** (a long period with gaps): no ephemeris error, the dossier caveat says so, the pack prints "no error" and the live gate refuses. Pinned by Task 1 `test_linear_fit_with_two_events_has_no_error`, Task 2 `test_generate_without_enough_transits_says_so`, Task 3 `test_a_live_draft_refuses_a_dossier_without_ephemeris_errors`.
2. **A rung 2 fit that failed** (`fit_ok` False): the shape block carries the box depth and duration with errors and no k, b or a/R* rows, and the Research Note leaves the fit sentence out. Pinned by Task 1 `test_bootstrap_without_a_fit_gives_box_depth_and_duration`, Task 3 `test_note_for_a_star_without_parameters_or_pixels` (extended).
3. **A dossier stored under version 2** (every live dossier): the page renders without the new blocks, `skysift dossier` regenerates it because of the version bump, and the pack refuses a live draft for it. Pinned by Task 2 `test_a_version_2_dossier_renders_and_regenerates`.
4. **A cross-check imported against an older dossier** (the period or the neighbour list changed since): the block is marked not current, the page says so, the gate refuses. Pinned by Task 4 `test_a_cross_check_against_another_fingerprint_is_not_current`.
5. **Seasons the floor tool must not pool** (a cut box, one usable quarter): left out of the pooled number and listed apart. Pinned by Task 6 `test_floor_tool_pools_complete_boxes_with_two_quarters_only`.
6. **A star with no stellar density** (route "none", or no mass for the Mamajek route): no prior fit is run. The free values are quoted with the reason "no stellar density, so no density prior", a/R* is labelled "fitted", and the planet radius uses the free k. Pinned by Task 10 `test_without_a_density_the_free_fit_is_quoted` and `test_shape_without_a_stellar_density_quotes_the_free_fit`.
7. **A prior claim without errors, or without a relationship** (KIC 5689121's claim gives no period or epoch error):
   - Each sigma uses our error alone, and the sentence says so.
   - A quantity the claim does not give is left out.
   - A missing relationship reads as None, and the live gate refuses it.
   Pinned by Task 11 `test_prior_comparison_gives_each_difference_in_sigma`, `test_a_prior_claim_with_an_unknown_relationship_is_refused` and `test_the_live_gate_refuses_a_claim_without_a_relationship_and_the_forbidden_words`.
8. **A target with no kept neighbour pixel file, or a rung 5 row without the PSF width** (files released by retention, a row from before the two-source fit): the predicted leak carries its reason, the texts print the measured dip alone, and nothing is fetched. Pinned by Task 12 `test_the_predicted_leak_is_the_targets_share_of_the_neighbours_aperture_times_the_depth`.
9. **A sweep count of 0** (a store holding only recheck runs, or a signal of another search version): `calibration.sweep_size` is None and the false-alarm sentence drops the S clause for the ladder clause alone. Pinned by Task 11 `test_sweep_size_counts_the_done_sweep_runs_of_one_source_and_search` and `test_the_false_alarm_sentence_names_the_nulls_and_the_sweep`.
10. **An SES cut that leaves fewer than three timed transits** (a shallow or long-period signal): the timing is not ok, every fold, rung 3 and the bootstrap stay on the search ephemeris, the texts say "search", and the live gate refuses as in item 1. Pinned by Task 10 `test_without_enough_ses_the_fold_stays_on_the_search_ephemeris`.

---

## File structure

| Path | Responsibility |
|---|---|
| `src/skysift/ephemeris.py` (new) | Pure numerics: `Template`, `transit_times`, `linear_fit`, `LinearEphemeris.predict`, `predict_from`, `bootstrap_shape`, `parity_depths`, `secondary_limit`. |
| `src/skysift/dossier.py` | `DOSSIER_VERSION = "3"`, `ephemeris_blocks`, the new JSON blocks, the planet-radius error fold, two caveats, the cross-check merge. |
| `src/skysift/ui/templates/dossier.html`, `src/skysift/ui/routes/dossiers.py` | The errors in the hero and sections 01 to 03, the cross-check block. |
| `src/skysift/pack.py`, `src/skysift/templates/pack/*.j2`, `src/skysift/zenodo.py` | `PACK_VERSION = "3"`, rows with errors, `transit_times.csv`, `stellar_provenance.csv`, `figure1_data.csv`, the ExoFOP parameter files, the texts, the gate. |
| `src/skysift/crosscheck.py` (new), `src/skysift/store/db.py`, `src/skysift/store/migrations.py`, `src/skysift/cli.py` | Export, import, verdict, the `cross_check` column (migration `004_cross_check`), `skysift crosscheck`. |
| `tools/triceratops_check.py` (new) | The TRICERATOPS driver, run under `.venv-triceratops`; its pure helpers importable without triceratops. |
| `src/skysift/lc/pixels.py`, `src/skysift/recheck.py`, `tools/centroid_floor.py` (new) | The floor constant, the per-season metrics, `rule_version` 3, the reopen rule, the measurement tool. |
| `tests/unit/test_ephemeris.py` (new), `test_dossier.py`, `test_ui_dossiers.py`, `test_pack.py`, `test_zenodo.py`, `test_crosscheck.py` (new), `test_migrations.py`, `test_store.py`, `test_pixels.py`, `test_rung5_pixels.py`, `test_recheck.py`, `test_centroid_floor.py` (new), `tests/golden/*` | Tests named in the tasks. |
| `docs/superpowers/notes/2026-10-22-fpp-cross-check.md`, `docs/superpowers/notes/2026-10-22-centroid-floor.md` | Task 5's comparison, Task 6's measurement. |
| `src/skysift/lc/transit_model.py` (round 2) | Task 10: `limb_darkening`, `quadratic_ld_flux`, `exposure_flux`, `fit_shape`, `compare_fits`, `stellar_density`; rung 2's `fit_transit` unchanged. |
| `src/skysift/dossier.py`, `ephemeris.py`, `store/db.py`, `stars.py`, `fpp.py`, `lc/pixels.py` (round 2) | Tasks 10 to 13: `DOSSIER_VERSION = "5"`, the free and prior fits, the folds on the linear ephemeris, the SES cut, `stability`, `calibration`, `aperture`, `predicted_leak`, `depth_trend`, the exclusion radius in the BEB term (`FPP_MODEL_VERSION = "3"`), RUWE and NSS. |
| `src/skysift/pack.py`, `pack_rows.py`, `pack_text.py`, `exofop.py`, `crosscheck.py`, `zenodo.py`, `tess.py`, `cli.py`, `templates/pack/*.j2` (round 2) | Tasks 11 to 13: `PACK_VERSION = "4"`, the vetting texts and the prior-claim comparison, the FPP headline, the sweep-wide false-alarm sentence, the gates, `docs/author.json` and `--final`. |
| `tests/golden/regenerate.py` (new, round 2) | Task 10: rewrites the golden from a copy of the live store. No test imports it, since it reaches Gaia. |

---

### Task 1: The ephemeris module

**Files:**
- Create: `src/skysift/ephemeris.py`
- Test: `tests/unit/test_ephemeris.py`

**Interfaces:**
- Consumes: `skysift.lc.ops.fold_phase`, `skysift.lc.ops.event_depths` (its `EventDepths`: `epoch`, `quarter`, `depth_ppm`, `err_ppm` arrays), `skysift.lc.transit_model.fit_transit(time, fnorm, period, t0, duration_days) -> TransitFit` (fields `ok`, `k`, `b`, `a_over_r`, `chi2`, `n_bins`) and `transit_model.uniform_disc_flux(dt_days, period, k, b, a_over_r)`, `transit_model._t14`; `tests/synthetic.py` (`make_lightcurve`, `inject_model`, `inject_transit`).
- Produces:
```python
BKJD_OFFSET = 2454833.0
EVENT_IN_MIN, EVENT_OUT_MIN, EVENT_OUT_SIDE_MIN = 3, 6, 2     # the pixel rung's event rules
GRID_STEP_MIN = 1.0            # minutes, the shift grid of the per-transit chi-square
GRID_HALF_DURATIONS = 1.0      # the grid spans plus and minus one duration
OUTLIER_MAD = 5.0              # Holczer et al. 2016
MIN_EVENTS = 3                 # fewer used events: no ephemeris error
N_BOOT, BOOT_MIN_CONVERGED, BOOT_SEED = 200, 20, 20261022
BOX_DURATION_FACTORS = (0.5, 2.0)      # the box-duration grid, times the search duration, 40 steps in log
EPHEMERIS_METHOD = ("weighted linear fit of per-transit mid-times, reference epoch at the centre of the series "
                    "(Deeg 2015; Holczer et al. 2016)")
SHAPE_METHOD = "bootstrap over transits of the uniform-disc fit (Schmitt et al. 2014)"

@dataclass(frozen=True)
class Template:
    kind: str                  # "fit" | "box"
    depth: float               # fraction, fixed
    duration_days: float
    k: float | None = None; b: float | None = None; a_over_r: float | None = None
    def flux(self, dt_days: np.ndarray, period: float) -> np.ndarray      # 1 - depth inside the box; the scaled uniform-disc model for "fit"
def template_from(shape_metrics: dict, depth_ppm: float, duration_days: float) -> Template   # "fit" when fit_ok and k, b, a_over_r finite

@dataclass(frozen=True)
class TransitTime:
    epoch: int; t_bkjd: float | None; err_days: float | None; chi2_red: float | None
    n_in: int; n_out: int; used: bool; reason: str | None
    def as_dict(self) -> dict

def transit_times(time, fnorm, period: float, t0: float, duration_days: float, template: Template) -> list[TransitTime]
    # one entry per epoch with any point within 3 durations; used=False with reason "too few in-transit points" |
    # "too few out-of-transit points" | "no minimum inside the grid" | "shift error above half a duration"

@dataclass(frozen=True)
class LinearEphemeris:
    ok: bool; reason: str | None
    period: float; period_err: float | None
    t_ref: float | None; t_ref_err: float | None; epoch_ref: int | None; cov: float | None   # cov of (t_ref, period)
    t0: float; t0_err: float | None            # at epoch 0
    chi2: float | None; dof: int | None; inflation: float | None; rms_min: float | None
    n_used: int; n_measured: int; n_outliers: int
    times: tuple[TransitTime, ...]              # every measured event, outliers with used=False and reason "outlier (x MAD)"
    def predict(self, epoch: int) -> tuple[float, float | None]            # (t_bkjd, err_days)
    def next_transits(self, after_bkjd: float, n: int = 3) -> list[dict]  # [{"epoch", "t_bkjd", "t_bjd", "err_days"}]
    def as_dict(self) -> dict                                               # the "timing" block, times as a list of dicts

def linear_fit(times: list[TransitTime], period: float, t0: float) -> LinearEphemeris
def predict_from(timing: dict, epoch: int) -> tuple[float, float | None]  # the same prediction from a stored as_dict()

@dataclass(frozen=True)
class ShapeErrors:
    ok: bool; reason: str | None; method: str; n_boot: int; n_converged: int; seed: int
    k: dict | None; b: dict | None; a_over_r: dict | None; t14_h: dict | None; model_depth_ppm: dict | None
    #   each {"median": the rung 2 point value, "minus", "plus", "boot_median"}
    box_depth_ppm: dict | None          # {"median": depth_ppm, "minus": err, "plus": err} from event_depths
    box_duration_h: dict | None         # only without a fit: {"median", "minus", "plus"} over the resamples
    def as_dict(self) -> dict

def bootstrap_shape(time, fnorm, period, t0, duration_days, point: dict | None, *, n_boot: int = N_BOOT,
                    seed: int = BOOT_SEED, used_epochs=None) -> ShapeErrors
    # point: {"k", "b", "a_over_r", "t14_h", "fit_depth_ppm"} from rung 2 when fit_ok, else None

@dataclass(frozen=True)
class ParityDepths:
    odd: dict; even: dict               # {"depth_ppm", "err_ppm", "n", "inflation"}
    difference_sigma: float | None
    def as_dict(self) -> dict
def parity_depths(events) -> ParityDepths            # events: lc.ops.EventDepths

@dataclass(frozen=True)
class SecondaryLimit:
    depth_ppm: float | None; err_ppm: float | None; limit_3sigma_ppm: float | None
    strongest_ppm: float | None; strongest_phase: float | None; strongest_sigma: float | None
    albedo_limit: float | None
    def as_dict(self) -> dict
def secondary_limit(shape_metrics: dict) -> SecondaryLimit | None   # None when secondary_ppm or control_rms_ppm is missing
```
- Numbers: `rms_min` is the rms of the used residuals in minutes; `inflation` is `max(1, sqrt(chi2 / dof))`, applied to `period_err`, `t_ref_err`, `t0_err` and the covariance (as the square); `t0_err` squared is `t_ref_err**2 + epoch_ref**2 * period_err**2 - 2 * epoch_ref * cov`; `predict(E)` error squared is `t_ref_err**2 + (E - epoch_ref)**2 * period_err**2`. A per-event error is the half-width at delta chi-square 1 of the parabola through the three lowest grid points, times `max(1, sqrt(chi2_min / (n - 1)))`. The per-point error is 1.4826 times the median absolute deviation of the window's points beyond one duration. Shape percentiles are 16, 50, 84; `minus = max(0, median - p16)`, `plus = max(0, p84 - median)` with `median` the rung 2 value. The albedo limit is `limit_3sigma_ppm * 1e-6 * (a_over_r / k) ** 2`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_ephemeris.py
P, T0, DUR = 5.4, 134.9, 3.2 / 24

def _planet(seed=3, noise=600):
    return inject_model(make_lightcurve(noise_ppm=noise, seed=seed), P, T0, 0.047, 0.31, 74.0)   # same scene as test_dossier

def test_transit_times_recover_the_injected_mid_times():
    lc = _planet(); fn = detrend(lc)
    tt = transit_times(lc.time, fn, P, T0, DUR, Template("box", 0.0022, DUR))
    used = [t for t in tt if t.used]
    assert len(used) >= 150
    pulls = [(t.t_bkjd - (T0 + t.epoch * P)) / t.err_days for t in used]
    assert abs(np.mean(pulls)) < 0.3 and 0.6 < np.std(pulls) < 1.6

def test_linear_fit_recovers_the_period_within_two_sigma_and_centres_the_epoch():
    eph = linear_fit(transit_times(...), P, T0)
    assert eph.ok and abs(eph.period - P) < 2 * eph.period_err and eph.period_err < 2e-5
    assert abs(eph.epoch_ref - np.mean([t.epoch for t in eph.times if t.used])) <= 1
    assert eph.t_ref_err < eph.t0_err                       # Deeg 2015: smallest at the centre
    far = eph.predict(eph.epoch_ref + 400)[1]
    assert far > eph.t_ref_err and far == pytest.approx(np.hypot(eph.t_ref_err, 400 * eph.period_err))

def test_predict_from_the_stored_block_matches_the_object():
    assert predict_from(eph.as_dict(), 123) == pytest.approx(eph.predict(123))

def test_an_outlier_is_dropped_by_the_mad_rule():
    # move one measured time by 40 minutes before the fit
    ...; assert eph.n_outliers == 1 and any(t.reason and t.reason.startswith("outlier") for t in eph.times)

def test_linear_fit_with_two_events_has_no_error():
    eph = linear_fit(times[:2], P, T0)
    assert not eph.ok and eph.period_err is None and eph.t0 == T0 and "fewer than 3" in eph.reason

def test_bootstrap_brackets_the_injected_radius_ratio(): 
    se = bootstrap_shape(lc.time, fn, P, T0, DUR, {"k": 0.047, "b": 0.31, "a_over_r": 74.0, "t14_h": 3.2, "fit_depth_ppm": 2209.0}, n_boot=20, seed=1)
    assert se.ok and se.n_converged >= 15 and se.k["minus"] > 0 and se.k["plus"] > 0
    assert se.k["minus"] < 0.01 and se.k["plus"] < 0.01 and se.t14_h["median"] == 3.2

def test_bootstrap_without_a_fit_gives_box_depth_and_duration():
    se = bootstrap_shape(..., None, n_boot=20)
    assert se.ok and se.k is None and se.box_duration_h["median"] == pytest.approx(3.2, abs=0.8)
    assert se.box_depth_ppm["median"] == pytest.approx(2200, abs=300)

def test_bootstrap_with_too_few_converged_resamples_says_so():   # a curve with 2 transits
    assert not se.ok and "converged" in se.reason

def test_parity_depths_agree_on_a_planet_and_differ_on_an_alternating_dip():
    ...; assert abs(pd.difference_sigma) < 3 ; alternating: assert pd.difference_sigma > 5

def test_secondary_limit_from_rung2_metrics():
    sl = secondary_limit({"secondary_ppm": 95.4, "control_rms_ppm": 128.2, "free_secondary_ppm": 341.7, "free_secondary_phase": 0.379, "free_secondary_sigma": 3.8, "fit_ok": True, "fit_k": 0.0478, "fit_a_over_r": 43.6})
    assert sl.limit_3sigma_ppm == pytest.approx(95.4 + 3 * 128.2) and sl.albedo_limit == pytest.approx(480e-6 * (43.6 / 0.0478) ** 2, rel=1e-2)
    assert secondary_limit({}) is None

def test_every_block_is_strict_json():   # json.dumps(..., allow_nan=False) on each as_dict
```

- [ ] **Step 2: Run them to see them fail** (`pytest tests/unit/test_ephemeris.py -q -p no:cacheprovider`: ImportError).
- [ ] **Step 3: Implement `src/skysift/ephemeris.py`** with the interfaces above. The chi-square scan: for each epoch, the window is the points within 3 durations of the predicted mid-time; the template is evaluated at `dt - shift` for each grid shift; the baseline level is the median of the points beyond one duration. The bootstrap resamples epochs with replacement among `used_epochs` (every epoch with data when None), concatenates their points and calls `fit_transit`; the box fallback measures, per resample, the depth of the box at each duration of the grid as the mean of one minus the flux inside over the baseline outside, and keeps the duration with the largest depth over its error.
- [ ] **Step 4: Run the tests** (all pass; the module under `ruff`).
- [ ] **Step 5: Commit** `feat(ephemeris): per-transit times, the linear ephemeris, a bootstrap over transits, parity depths and the secondary limit`.

---

### Task 2: The dossier carries the errors

**Files:**
- Modify: `src/skysift/dossier.py` (`DOSSIER_VERSION`, `_assemble`, `generate`, `caveats`), `src/skysift/ui/templates/dossier.html`, `src/skysift/ui/routes/dossiers.py` (only if a value needs preparing), `src/skysift/ui/static/dossier.js` (the export's section text, if it prints the ephemeris)
- Test: `tests/unit/test_dossier.py`, `tests/unit/test_ui_dossiers.py`

**Interfaces:**
- Consumes: Task 1.
- Produces:
```python
DOSSIER_VERSION = "3"      # 3: ephemeris, timing, shape, parity and secondary blocks (22 October 2026)
N_BOOT_DEFAULT = ephemeris.N_BOOT

def ephemeris_blocks(lc, fnorm, signal: dict, tests: list[dict], *, n_boot: int = N_BOOT_DEFAULT) -> dict
    # {"ephemeris_extra": {...the new ephemeris keys...}, "timing": ..., "shape": ..., "parity": ..., "secondary": ...}
    # generate(..., n_boot=N_BOOT_DEFAULT) passes it through (tests use 20)
```
- JSON: `ephemeris` gains `period_err_days`, `t0_bkjd_err`, `t0_bjd_err`, `t_ref_bkjd`, `t_ref_bjd`, `t_ref_err_days`, `epoch_ref`, `duration_h_err` (`{"minus", "plus"}` or None), `depth_ppm_err` (rung 2's `depth_err_ppm`), `depth_source`, `ephemeris_method`, `shape_method`; top-level `timing`, `shape`, `parity`, `secondary` (the `as_dict()` of Task 1; `secondary` may be None); `planet.rp_rearth` minus and plus become the quadrature of the stellar term and `rp * k_err / k` when `shape.k` has errors, with the source string extended "; radius-ratio error from the bootstrap"; the stored `dossiers` row gets no new column in this task (the cross-check column is Task 4).
- Caveats, in this order after the pixel caveats: key `no_ephemeris_error`, title "The ephemeris carries no error", text from `timing.reason`; key `no_shape_error`, title "The transit shape carries no error", text from `shape.reason`. When `parity.difference_sigma` and rung 2's `parity_sigma` differ by more than 0.5, section 02 prints both with one sentence ("the vetting number is the stacked fold's; the reporting number is from the per-event depths").
- Page: the hero tiles show the period and the duration as `value ± err` in Expert (`only-expert` span after the value); section 01's key-value list gains T_ref (BJD_TDB and BKJD) with its error, the period error, the timing rms and the used-over-measured count, and the method sentence; section 02 gains a key-value list "Odd and even" (depth ± err each, the difference in sigma) and "Secondary eclipse" (depth ± err at phase 0.5, the 3-sigma limit, the strongest secondary with its phase, the albedo limit) ; section 03's Expert view gains a table "Shape with errors" (k, b, a/R*, T14, model depth: value, minus, plus) with the method and `n_converged` of `n_boot`. Every new element reads an optional key, so a version-2 JSON renders without it.

- [ ] **Step 1: Write the failing tests**

```python
# test_dossier.py
def test_generate_writes_the_timing_shape_parity_and_secondary_blocks(env):
    rep = generate(env.store, env.blob, env.settings, STAGE2_TARGET, fetch=env.fetch, n_boot=20)
    d = json.loads(rep.data_path.read_text(encoding="utf-8"))
    assert d["dossier_version"] == "3" and d["timing"]["ok"] and d["ephemeris"]["period_err_days"] > 0
    assert d["ephemeris"]["t_ref_bjd"] == pytest.approx(d["ephemeris"]["t_ref_bkjd"] + 2454833.0)
    assert d["shape"]["ok"] and d["shape"]["n_boot"] == 20 and d["parity"]["odd"]["err_ppm"] > 0
    assert d["planet"]["rp_rearth"]["plus"] >= stellar_only_plus    # the k error is folded in
    json.dumps(d, allow_nan=False)

def test_generate_without_enough_transits_says_so(env, monkeypatch):   # a light curve kept in one quarter: 2 transits
    ...; assert d["timing"]["ok"] is False and any(c.get("key") == "no_ephemeris_error" for c in d["caveats"])

def test_a_version_2_dossier_renders_and_regenerates(env, client):   # strip the new keys, set dossier_version "2" on the row and in the file
    assert client.get(f"/dossiers/{STAGE2_TARGET}").status_code == 200   # no new block, no error
    rep = generate(...); assert rep.generated   # the version bump regenerates without --force

# test_ui_dossiers.py
def test_dossier_page_prints_the_ephemeris_errors_in_expert(client_expert, ...):
    html = ...; assert "± " in html and "T_ref" in html or "Reference epoch" in html
    assert "Odd and even" in html and "3-sigma limit" in html and "Shape with errors" in html
def test_dossier_export_carries_the_same_errors(...)
```

- [ ] **Step 2: Run them to see them fail.**
- [ ] **Step 3: Implement** `ephemeris_blocks`, the `_assemble` keys, the caveats, the template changes.
- [ ] **Step 4: Run `tests/unit/test_dossier.py tests/unit/test_ui_dossiers.py tests/unit/test_pack.py`** (the pack tests must still pass on the version-3 JSON).
- [ ] **Step 5: Commit** `feat(dossier): ephemeris, shape, parity and secondary blocks with errors (dossier version 3)`.

---

### Task 3: The pack prints the errors and the smaller rows

**Files:**
- Modify: `src/skysift/pack.py`, `src/skysift/templates/pack/README.md.j2`, `note.tex.j2`, `exofop_note.txt.j2`, `archive_email.txt.j2`, `src/skysift/zenodo.py` (`probability_sentences` unchanged; `metadata_from` description gains the ephemeris with errors), `src/skysift/cli.py` (the gate message)
- Test: `tests/unit/test_pack.py`, `tests/unit/test_zenodo.py`

**Interfaces:**
- Consumes: Task 2's JSON keys; `ephemeris.predict_from`.
- Produces:
```python
PACK_VERSION = "3"
SOFTWARE_DOI_PLACEHOLDER = "10.5281/zenodo.SOFTWARE"
EXOFOP_PLANET_TEMPLATE = "params_planet_{date}_001.txt"; EXOFOP_STELLAR_TEMPLATE = "params_stellar_{date}_001.txt"
def ephemeris_rows(data) -> list[dict]            # now with minus and plus from the new keys; source names the method
def shape_rows(data) -> list[dict]                # k, b, a/R*, T14, model depth, box duration with errors
def timing_rows(data) -> list[dict]               # one per measured transit: epoch, t_bkjd, t_bjd, err_days, chi2_red, used, reason
def provenance_rows(data) -> list[dict]           # parameter, value, minus, plus, unit, method, inputs, reference (star block + logg)
def dilution_rows(data) -> list[dict]             # crowdsap, psffrac (target meta), leak depth and error (rung 5)
def next_transits(data, after_bjd: float, n: int = 3) -> list[dict]     # via predict_from; [] without a timing block
def render_exofop_planet(data, date: str, tic: str | None, tag: str) -> str   # the pipe-delimited template with its comment header
def render_exofop_stellar(data, date: str, tic: str | None, tag: str) -> str
def figure_data_rows(data) -> list[list]          # phase_h, flux_ppm, err_ppm, n, model_ppm from plots.fold_bins and plots.model
def gate_problems(raw: dict) -> list[str]         # reasons a live draft is refused on the stored JSON: no ephemeris error, no shape error (Task 4 adds the cross-check)
```
- Files added to the pack: `data/transit_times.csv`, `data/shape.csv`, `data/stellar_provenance.csv`, `note/figure1_data.csv`, `exofop/params_planet_<date>_001.txt`, `exofop/params_stellar_<date>_001.txt`; `data/ephemeris.csv` keeps its columns and gains the dilution rows; `FILE_NOTES` names each; the ExoFOP copy list gains the two parameter files.
- Texts: `numbers()` gains `period_err`, `t0_err`, `t_ref_bjd`, `t_ref_err`, `duration_err`, `depth_err`, `rms_min`, `n_used`, `n_measured`, `odd_text`, `even_text`, `parity_sigma_text`, `secondary_text`, `albedo_text`, `shape_text`, `next_transits`, `crowdsap`, `psffrac`, `leak_text`, `software_line`, `code_sentence`; the README's numbers table prints `value ± err`; step 2 and step 4 wording per spec section 6; step 5 lists the next three transits after the build date with their windows; the ExoFOP note and the archive email quote P ± σ, T0 ± σ (BJD_TDB), duration ± σ and name the search version and commit; the Research Note's signal section quotes P, T_ref and the duration with errors and the timing rms, the detection section gains the model clause and the odd-even and secondary sentences, the pixel section gains the dilution sentence, the star section gains the provenance sentence, and the end gains `\software{...}` and `\facilities{Kepler, Gaia, MAST, Exoplanet Archive}` and the code-availability sentence in the data paragraph. The note stays within `NOTE_LIMITS` on the KIC 6146676 dossier (`note_problems` empty).
- The gate: `build_pack` raises `PackError` for a live draft when `gate_problems(raw)` is not empty, naming every problem; the CLI prints the same.

- [ ] **Step 1: Write the failing tests** (`test_pack.py`): `test_ephemeris_csv_carries_the_errors_and_the_dilution_rows`, `test_transit_times_and_shape_csvs`, `test_stellar_provenance_csv_names_the_method_and_the_reference`, `test_exofop_parameter_files_follow_the_templates` (pipe-delimited, the `period_unc` column filled, `TIC<TIC-TO-FILL>.01` without a TIC, `TIC123.01` with), `test_readme_step_five_lists_the_next_transits_with_windows`, `test_every_text_quotes_the_period_and_epoch_with_errors`, `test_note_has_software_facilities_and_the_model_clause_within_the_limits` (on the real KIC 6146676 JSON of `tests/golden/kic-006146676-generated.json` after Task 7 updates it; until then on the seed), `test_readme_says_the_archive_needs_a_refereed_paper`, `test_a_live_draft_refuses_a_dossier_without_ephemeris_errors`, `test_figure_data_csv_matches_the_fold_bins`, extend `test_note_for_a_star_without_parameters_or_pixels` for a failed fit.
- [ ] **Step 2: Run them to see them fail.**
- [ ] **Step 3: Implement** the functions, the templates and the gate.
- [ ] **Step 4: Run `tests/unit/test_pack.py tests/unit/test_zenodo.py tests/unit/test_cli.py tests/unit/test_ui_dossiers.py`.**
- [ ] **Step 5: Commit** `feat(pack): errors in every text and file, ExoFOP parameter files, provenance, dilution, software statement, truthful routes (pack version 3)`.

---

### Task 4: The cross-check column, command and tool

**Files:**
- Create: `src/skysift/crosscheck.py`, `tools/triceratops_check.py`, `tests/unit/test_crosscheck.py`
- Modify: `src/skysift/store/db.py` (`DossierRow.cross_check`, `Store.set_cross_check`), `src/skysift/store/migrations.py` (`004_cross_check`), `src/skysift/cli.py` (`crosscheck`), `src/skysift/dossier.py` (merge the stored block), `src/skysift/ui/templates/dossier.html` (section 03 block), `src/skysift/pack.py` (`cross_check_sentence`, `gate_problems`), `src/skysift/templates/pack/*.j2`, `.gitignore` (`.venv-*/`)
- Test: `tests/unit/test_crosscheck.py`, `test_migrations.py`, `test_store.py`, `test_dossier.py`, `test_pack.py`, `test_cli.py`

**Interfaces:**
```python
# crosscheck.py
TOOL = "triceratops"; LIKELY_FP_FPP = 0.5; LIKELY_NEARBY_NFPP = 0.1; N_RUNS = 10
RESULT_KEYS = ("tool", "tool_version", "run_utc", "n_runs", "fpp", "nfpp", "fpp_runs", "nfpp_runs", "scenarios", "stars",
               "inputs_sha256", "caveats", "settings")
def inputs_fingerprint(data: dict) -> str        # sha256 of the JSON of (period_days, t0_bkjd, depth_ppm, duration_h, sorted neighbour source ids, gmag)
def export_inputs(store, blob, settings, target_id: str, out_dir: Path) -> Path
    # writes inputs.json (target, ephemeris with errors, star, neighbours, neighbour_kic, quarters, fingerprint),
    # lightcurve.csv (time_bkjd, flux_rel, err_rel, quarter; within 3 durations of a transit) and tpf/<name>.fits.gz copies
def import_result(store, target_id: str, path: Path) -> dict
    # validates RESULT_KEYS and numbers, computes {"current": inputs_sha256 == inputs_fingerprint(stored dossier)}, stores it
def verdict(block: dict | None) -> tuple[bool, str | None]   # (blocks the live gate, reason): missing | not current | likely false positive
def comparison_sentence(data: dict) -> str | None            # for the pack: both numbers; the ratio sentence when above 3
# db.py
DossierRow.cross_check: Mapped[dict | None] = mapped_column(JSON)
Store.set_cross_check(target_id, block: dict | None) -> None
# cli.py
skysift crosscheck --target <id> (--export <dir> | --import <file>)      # exit 2 for a bad result file, 1 without a dossier
```
- `tools/triceratops_check.py`: `main(argv)` with `--inputs <dir> --out <file> [--runs N] [--seed S]`; the pure helpers `match_neighbours(tic_rows, gaia_neighbours, arcsec=1.0) -> (matched, missing)`, `tmag_from_g(g, bp_rp) -> float` (G - 0.5 for BP-RP above 1.5, G - 0.3 otherwise), `quarter_images(tpf_paths) -> list[dict]` (the mean flux image, the reference pixel, the WCS) and `assemble_result(runs, stars, settings, inputs, version) -> dict` live above a guarded `import triceratops` so the tests import them under the engine's venv; the driver sets `target.sectors`, `target.TESS_images`, `target.col0s`, `target.row0s`, `target.pix_coords` from `quarter_images`, builds the 3 x 3 apertures around the target per quarter, calls `calc_depths` then `calc_probs(..., exptime=0.0204, N=1_000_000, parallel=False)` `--runs` times, and writes the result. The tool's docstring says how to make the environment: `uv venv .venv-triceratops --python 3.12` and `uv pip install --native-tls -p .venv-triceratops/Scripts/python.exe triceratops==1.1.0`.
- Dossier: `generate` and the page read `store.dossier(target)["cross_check"]` into `data["cross_check"]` (with `current` recomputed against the fresh fingerprint); regeneration keeps the stored block. Section 03 shows "Cross-check" with the tool, the version, the median FPP and NFPP, the run spread, `current` or "made for an earlier dossier", and the caveats. The pack: `comparison_sentence` in the README, the ExoFOP note, the archive email and the Research Note's false-positive section; `data/cross_check.json`; `gate_problems` adds `verdict`'s reason.

- [ ] **Step 1: Write the failing tests**: `test_inputs_fingerprint_changes_with_the_period_and_the_neighbours`, `test_export_writes_inputs_lightcurve_and_the_pixel_files` (the seed with stored synthetic TPFs), `test_import_validates_and_stores_the_result`, `test_a_cross_check_against_another_fingerprint_is_not_current` (export, change the stored period, import: `current` False, `verdict` blocks with "made for an earlier dossier"), `test_verdict_blocks_a_likely_false_positive_and_passes_a_small_disagreement` (SkySift 0.0035 against 0.012: no block, sentence names the ratio), `test_migration_004_adds_the_column`, `test_cli_crosscheck_export_and_import`, `test_dossier_page_shows_the_cross_check_block`, `test_pack_prints_the_cross_check_and_the_gate_refuses_without_one`, `test_tool_helpers_match_neighbours_and_estimate_tmag`, `test_tool_module_imports_without_triceratops`.
- [ ] **Step 2: Run them to see them fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the unit suite for the touched modules.**
- [ ] **Step 5: Commit** `feat(crosscheck): TRICERATOPS inputs, result column, command, page and pack blocks, the live gate`.

---

### Task 5: Run the cross-check on the two stars (operational)

**Files:**
- Create: `docs/superpowers/notes/2026-10-22-fpp-cross-check.md`; results under `.superpowers/referee-run/crosscheck/<target>/`
- Modify: nothing in `src/` unless the tool needs a fix (then a `fix(crosscheck)` commit with a test).

- [ ] **Step 1:** make `.venv-triceratops` (uv, Python 3.12, `triceratops==1.1.0`, `--native-tls`); record the installed versions in the note.
- [ ] **Step 2:** `skysift crosscheck --target kic-006146676 --export ...` and the same for `kic-005689121` against the live data (`SKYSIFT_DATA_DIR=.superpowers/sample-run`, the live database); the dossiers must be version 3 first (run `skysift dossier --target ... --force` on the live data, BelowNormal, in the background).
- [ ] **Step 3:** run the tool for each (background, BelowNormal, hidden window; it reaches MAST and Gaia through astroquery, which is allowed for this tool only), then `--import`.
- [ ] **Step 4:** write the note: the inputs used, every run's FPP and NFPP, the median, the scenario table, the stars matched and missing, SkySift's number beside it, the ratio, the reasons for the difference (scenario coverage, band, priors, aperture model), and the gate verdict. Commit the note.

---

### Task 6: The centroid floor (after the partial-box merge is brought in)

**Files:**
- Create: `tools/centroid_floor.py`, `tests/unit/test_centroid_floor.py`, `docs/superpowers/notes/2026-10-22-centroid-floor.md`
- Modify: `src/skysift/lc/pixels.py`, `src/skysift/recheck.py`, `src/skysift/dossier.py` (the caveat sentence names the floor), `src/skysift/pack.py` (`pixel_sentence` names it), `tests/unit/test_pixels.py`, `test_rung5_pixels.py`, `test_recheck.py`, `test_dossier.py`, `test_pack.py`

**Interfaces** (names to be confirmed against the merged `pixels.py`; the sibling branch defines `rule_version` 2 in the metrics, `PIXEL_RULE_VERSION` in `recheck.py`, `edge_bias_pix` and `edge_bias_axis_pix` per season, `BoxCoverage` and `box_pixels_target`):
```python
# pixels.py
CENTROID_FLOOR_PIX = <measured>      # per axis; measured 2026-10-2x by tools/centroid_floor.py on the live pixel files (note ...)
CENTROID_FLOOR_MEASURED = "2026-10-2x"
RULE_VERSION = 3                     # the metrics' rule_version
# per season metrics: centroid_sigma_mc_pix (raw), floor_pix, registration_shift_pix [dc, dr] (0, 0 unless corrected)
# tools/centroid_floor.py
def season_quarter_centroids(quarters: list[QuarterData], eph: Ephemeris) -> list[dict]
    # per season: {"season", "quarters", "box_complete": bool, "target": [[dc, dr] per quarter], "neighbour": [...],
    #              "registration_mean": [dc, dr], "n_quarters"} from each quarter's out-of-transit direct image on the season frame
def pooled_floor(rows: list[dict]) -> dict      # {"n_seasons", "n_quarters", "rms_col", "rms_row", "rms_axis_mean", "per_season": [...]} over complete boxes with >= 2 quarters
def excess_scatter(seasons_metrics: list[dict], per_quarter_diff_centroids) -> dict   # the KIC 6146676 validation
# recheck.py
def floor_reopens(rung5_row: dict) -> bool      # rule_version below RULE_VERSION, any status
```

- [ ] **Step 1:** bring main in (`sync_with_base_branch`), run the full unit suite once, note the merged names.
- [ ] **Step 2:** write the failing tests: the tool on the synthetic TPF scene (`season_quarter_centroids` gives one row per season with per-quarter centroids within 0.05 px of each other on a noiseless scene and `pooled_floor` leaves out a one-quarter season and a cut-box season: `test_floor_tool_pools_complete_boxes_with_two_quarters_only`); `test_season_error_carries_the_floor_in_quadrature` (`centroid_sigma_pix**2 == mc**2 + edge**2 + floor**2` within 1e-9, `floor_pix` and `centroid_sigma_mc_pix` stored, `rule_version` 3); `test_wants_pixels_reopens_any_rung5_row_below_rule_version_3` (a pass with version 2 reopens; a pass with version 3 stays); the dossier and pack sentences name the floor.
- [ ] **Step 3:** run the tool on a copy of the live database (read-only, as `tools/pixels_rerun.py` does), write the note with the per-season table, the pooled numbers, the neighbour check, the registration residuals and the KIC 6146676 validation, and decide the registration ruling (spec 5.1). Set the constant.
- [ ] **Step 4:** implement, run `tests/unit/test_pixels.py test_rung5_pixels.py test_recheck.py test_dossier.py test_pack.py`.
- [ ] **Step 5:** commit `feat(pixels): a measured systematic floor on the centroid error (rule version 3), reopened by the recheck`.

---

### Task 7: The live run and the record (operational)

- [ ] **Step 1:** checkpoint and back up `.superpowers/sample-run/engine.db` to `engine.db.bak-<yyyymmdd>.db` (and the dossier-run database likewise); `skysift migrate` on the live database (column 004).
- [ ] **Step 2:** `skysift recheck` on the live data in the background (BelowNormal, hidden); report every rung 5 row that changed status or reason with its per-season numbers before and after.
- [ ] **Step 3:** regenerate every stored dossier (`--force`), rebuild the packs of KIC 6146676 and KIC 5689121 (no Zenodo), re-import the cross-checks if the fingerprints changed (rerun Task 5's tool when the inputs changed).
- [ ] **Step 4:** update `tests/golden/kic-006146676-generated.json` and the golden assertions with the new numbers; commit.
- [ ] **Step 5:** final whole-branch review (`269ad97..HEAD`, fresh reviewer), fix wave, re-review; the full gate `pytest tests -p no:cacheprovider -q -m "not slow and not golden"` detached, hidden, BelowNormal on the branch tip in its own worktree; the report with the checklist summary, the numbers before and after, and every scientific call with its precedent.


### Task 8: The TESS non-detection statement (added 22 October, spec section 10)

**Files:**
- Create: `src/skysift/tess.py` (pure: `SECTORS` table, `tmag_from_gaia`, `aperture_fraction`, `dilution`, `sigma_1h_nominal`, `sigma_1h_conservative`, `scale_to_duration`, `count_transits`, `significance`, `verdict`, `statement`), `tools/tess_inputs.py`, `tests/unit/test_tess.py`
- Modify: `src/skysift/dossier.py` (the `tess` block, `DOSSIER_VERSION = "4"`), `src/skysift/pack.py` / `pack_text.py` / templates (sentence, row, line), `src/skysift/ui/templates/dossier.html` + `dossier.js` (the block), `docs/superpowers/notes/2026-10-22-referee-checklist.md` (one row), `tests/unit/test_dossier.py`, `test_pack.py`, `test_ui_dossiers.py`

**Interfaces:**
- Consumes: `targets.meta["tess"]` written by the tool: `{"queried": date, "tic": int, "tmag": float, "tmag_source": "TIC"|"Gaia (Stassun 2019 eq. 1)", "ra", "dec", "sectors": [int], "neighbours": [{"tic", "dst_arcsec", "dra_pix", "ddec_pix", "tmag", "tmag_source"}]}`; the dossier's `ephemeris` block (P, T0 BJD, T14 hours, depth ppm and their errors).
- Produces: `data["tess"]` with keys `tic, tmag, tmag_source, sectors: [{sector, cadence_min, n_transits, uncertain}], n_transits, n_expected, timing_error_hours_at_tess, dilution: {c, c_hard42, depth_tess_ppm, t_total, aperture_pixels}, noise: {nominal_1h_ppm, conservative_1h_ppm, nominal_t14_ppm, conservative_t14_ppm}, significance: {per_transit_nominal, per_transit_conservative, folded_nominal, folded_conservative}, verdict, caveats: [str], queried`.

Steps: tests first for every pure helper (the Gaussian aperture fraction at zero offset against a numerical integral; dilution for one equal-magnitude neighbour at one pixel; the polynomial at T = 15 gives 5190 ppm within 1 per cent; the conservative value at T = 15 gives 8804; the transit count on a hand-made window; the four verdicts), then the module, then the dossier block and version bump, the texts, the page, the tool with a mocked `net.fetch`, the checklist row, commit.


---

## Round 2 (spec section 11, after the three independent reviews)

Tasks 10 to 13 carry out spec section 11 (`docs/superpowers/specs/2026-10-22-skysift-referee-pack-design.md`) on the reviews in `docs/superpowers/notes/2026-10-22-referee-round-2.md`. They run in order: Task 11 reads Task 10's blocks, Task 12 reads Task 11's exclusion radius, Task 13 sets the pack version. The golden is never edited by hand: each task that changes dossier content regenerates it with Task 10's helper.

**Run commands of round 2.** The engine's venv installs SkySift in editable mode from the main checkout (`.venv\Lib\site-packages\__editable__.skysift-0.1.0.pth` holds `C:\Research\skysift\src`). So a bare `python -m pytest` in the worktree imports main's code once a task changes the worktree's `src`. Every command of Tasks 10 to 13 therefore sets `PYTHONPATH=src`, as the methods audit did for its scripts. The ruled test command of each task is:

```
PYTHONUTF8=1 PYTHONPATH=src C:\Research\skysift\.venv\Scripts\python.exe -m pytest <the task's test files> -q -p no:cacheprovider
PYTHONUTF8=1 PYTHONPATH=src C:\Research\skysift\.venv\Scripts\python.exe -m ruff check src tests tools
```

**Word budget.** Round 2 adds words to the note: the limb-darkening clause, two citations, the sweep clause, the exclusion radius, the model-shift margins, the stacked parity, the aperture ratio, the follow-up clause and the TESS clause. It also takes words out: the hand-built sentence, the habitable-zone sentence and "the two models answer different questions". Every task therefore ends by running the live-shape limit test (`-k live_shape -vv`) and recording the four counts in its commit message. Task 13 Step 12 makes the cuts if the counts pass 1,480.

---

### Task 10: The limb-darkened shape fit, folds on the linear ephemeris and dossier version 5 (spec 11.3)

**Files:**
- Modify: `src/skysift/lc/transit_model.py`.
  - New code goes after `fit_transit` (lines 125 to 215).
  - `fit_transit` itself stays as it is. It is rung 2's binned uniform-disc V-shape fit (`vetting/rung2_shape.py:158`) and keeps its rule and its tests.
- Modify: `src/skysift/ephemeris.py`.
  - Line 47: `SHAPE_METHOD`.
  - Line 55: `EXPOSURE_RESOLUTION_MIN` moves to `transit_model` and is imported back.
  - Lines 329 to 358: `ShapeErrors`. Lines 372 to 379: `_spread`. Lines 391 to 453: `bootstrap_shape`. Lines 456 to 491: parity.
- Modify: `src/skysift/dossier.py`.
  - Line 81: `DOSSIER_VERSION`.
  - Lines 378 to 452: `plot_arrays`.
  - Lines 457 to 539: `_fit_point`, `_timing`, `_ephemeris_extra`, `ephemeris_blocks`.
  - Lines 841 to 877: `fpp_inputs`. Lines 919 to 941: `_error_caveats`.
  - Lines 1381 to 1452: `generate`. Lines 1473 to 1511: `_assemble`.
- Modify: `src/skysift/pack_rows.py`. Lines 37 to 91: `param_rows`. Lines 121 to 153: `SHAPE_ROWS` and `shape_rows`. A new `event_rows`.
- Modify: `src/skysift/pack.py`.
  - Lines 127 to 128: `MODEL_CLAUSE`.
  - Lines 280 to 299: `references`. Line 302: `BIBLIOGRAPHY`.
  - Lines 538 to 577: `shape_sentence`.
  - Lines 1099 to 1135: `FILE_NOTES`. Lines 1266 to 1309: `_fields`.
  - Lines 1971 to 2047: `_write_pack`.
- Modify: `src/skysift/exofop.py` (lines 90 to 117, `render_exofop_planet`).
- Modify: `src/skysift/templates/pack/README.md.j2` (the "What is in the pack" list).
- Modify: `src/skysift/ui/static/dossier.js` (line 569) and `src/skysift/ui/templates/dossier.html` (line 304). Both read `boot_median`.
- Modify: `tools/dossier_compare.py` (lines 33 to 35, `GOLDEN_KEYS`).
- Create: `tests/golden/regenerate.py`.
- Test: `tests/unit/test_transit_model.py` and `tests/unit/test_ephemeris.py`. In `test_ephemeris.py`, lines 171 to 214 read `boot_median`.
- Test: `tests/unit/test_dossier.py`. Lines 676, 974, 1249 and 1323 pin version "4"; lines 1056 to 1059 use `boot_median`.
- Test: `tests/unit/test_pack.py`. Lines 948 to 957 hold `SHAPE_OK`, lines 1061 to 1070 the shape CSV, and line 1815 a pin of version "4".
- Test: `tests/unit/test_crosscheck.py`, `tests/unit/test_ui_dossiers.py` and `tests/unit/ui_seed.py` (line 333).
- Test: `tests/golden/kic-006146676-generated.json`, regenerated in Step 7 and never edited.

**Interfaces:**
- Consumes:
  - From `transit_model`: `uniform_disc_flux`, `_t14`, `K_BOUNDS`, `B_BOUNDS`, `A_BOUNDS`, `C_BOUNDS`, `FIT_WINDOW_DURATIONS`, `START_DURATION_FACTORS`.
  - From `lc.ops`: `fold_phase`, `event_depths`, `stacked_depth`, `event_quality` (its `epochs` and `ses`).
  - From `ephemeris`: `transit_times`, `linear_fit`, `point_degeneracy`, `ingress_minutes`.
  - `stars.derive(gaia, period, fit_k, depth, rng) -> (StarParams, PlanetParams)`, with `StarParams.teff` and `StarParams.density_gcc` (`Value`: median, minus, plus).
  - `vetting.rung3_stability.Rung3Stability().run(Signal, DataBundle) -> TestResult`.
  - `pipeline.revet_flux(lc, th, sig) -> (fn, fn_alt, fn_detrended)`.
  - `tools.dossier_compare.golden_block(data) -> dict`.
- Produces:
```python
# lc/transit_model.py
EXPOSURE_RESOLUTION_MIN = 0.1        # minutes (moved from ephemeris, which imports it)
LD_LOGG = 4.5
LD_SOURCE = ("Claret and Bloemen 2011, Kepler band, quadratic law, ATLAS models (method L, log g 4.5, [M/H] 0, "
             "microturbulence 2 km/s), linear in Teff between the rows, held at the table's edges")
LD_TABLE = ((3500, 0.4158, 0.3203), (3750, 0.4224, 0.3037), (4000, 0.5516, 0.1880), (4250, 0.6834, 0.0718),
            (4500, 0.6811, 0.0672), (4750, 0.6270, 0.1082), (5000, 0.5684, 0.1530), (5250, 0.5086, 0.1959),
            (5500, 0.4526, 0.2327), (5750, 0.4035, 0.2622), (6000, 0.3588, 0.2868), (6250, 0.3239, 0.3029),
            (6500, 0.2971, 0.3119), (6750, 0.2753, 0.3195), (7000, 0.2567, 0.3263))   # (Teff K, u1, u2)
LD_NO_TEFF = 5750                    # the row used when the star has no Teff
LD_NODES = 32                        # Gauss-Legendre nodes of the annulus integral (cosine substitution)
CONSTRAINED_DELTA_CHI2 = 2.0         # spec 11.3: within 2, the shape does not constrain b and a/R*
G_CGS = 6.674e-8
UNCONSTRAINED = "unconstrained at this cadence, consistent with the host's density"
A_LABEL_STELLAR, A_LABEL_FITTED = "from the stellar parameters", "fitted"
NO_DENSITY = "no stellar density, so no density prior"

def limb_darkening(teff: float | None) -> dict   # {"law": "quadratic", "u1", "u2", "teff", "logg": 4.5, "source"}
def quadratic_ld_flux(dt_days, period: float, k: float, b: float, a_over_r: float, u1: float, u2: float) -> np.ndarray
    # 1 - blocked; blocked = integral of I(r) 2 r alpha(r) dr / (pi (1 - u1/3 - u2/6)) with
    # I(r) = 1 - u1 (1 - mu) - u2 (1 - mu)^2, mu = sqrt(1 - r^2), alpha = arccos(clip((r^2 + z^2 - k^2) / (2 r z), -1, 1)),
    # r from max(0, z - k) to min(1, z + k) as r = mid - half cos(theta), LD_NODES nodes in theta; 1 behind the star
def exposure_flux(dt_days, period, k, b, a_over_r, u1, u2, exposure_days: float) -> np.ndarray
    # quadratic_ld_flux on an EXPOSURE_RESOLUTION_MIN grid spanning the points plus one exposure, a running mean over
    # round(exposure / resolution) samples, interpolated to dt_days; an exposure at or below the resolution gives the
    # plain flux
def stellar_density(a_over_r: float, period_days: float) -> float     # 3 pi (a/R*)^3 / (G P^2), g/cm3

@dataclass(frozen=True)
class ShapeFit:
    ok: bool
    reason: str | None
    variant: str                       # "free" | "prior"
    k: float
    b: float
    a_over_r: float
    t14_days: float
    depth_ppm: float
    chi2_data: float
    chi2_prior: float                  # the prior term; 0 for "free"
    chi2_total: float                  # chi2_data + chi2_prior
    n_points: int
    u1: float
    u2: float
    exposure_min: float
    density_gcc: float                 # stellar_density(a_over_r, period) of this solution
    prior_gcc: tuple[float, float] | None   # (mean, sigma) of the prior; None for "free"
    def as_dict(self) -> dict

def fit_shape(time, fnorm, period, t0, duration_days, *, u1: float, u2: float, exposure_days: float,
              density: tuple[float, float] | None = None, start: ShapeFit | None = None) -> ShapeFit
    # least squares (scipy least_squares) on the unbinned fold within FIT_WINDOW_DURATIONS durations of mid-transit,
    # every point weighted by the window's robust scatter (1.4826 MAD); parameters (k, b, a_over_r, c) inside
    # K_BOUNDS, B_BOUNDS, A_BOUNDS, C_BOUNDS; nine starts as fit_transit makes them (START_DURATION_FACTORS times the
    # three geometries), or the one ``start``; with ``density`` the residuals gain (stellar_density(a) - mean) / sigma.
    # ok False with NaN values and a reason below 20 points in the window or without a converged start

def compare_fits(free: ShapeFit, prior: ShapeFit | None) -> dict
    # {"constrained": bool | None, "quoted": "free" | "prior", "delta_chi2": float | None,
    #  "density_tension_sigma": float | None, "a_over_r_label": str, "reason": str | None}
    # delta_chi2 = prior.chi2_total - free.chi2_total, the prior fit's chi-square including its prior term (the data
    # term alone moves by under 1 when a wrong prior meets a shape that cannot resist it; the prototype gave 0.36 for
    # a density eight times too high, against 76 for the prior term). density_tension_sigma = (free.density_gcc -
    # mean) / sigma. No prior fit, or one that is not ok: quoted "free", constrained None, label A_LABEL_FITTED,
    # reason NO_DENSITY (or the prior fit's reason). delta_chi2 <= CONSTRAINED_DELTA_CHI2: constrained False, quoted
    # "prior", label A_LABEL_STELLAR, reason UNCONSTRAINED. Otherwise constrained True, quoted "free", label
    # A_LABEL_FITTED, reason None.

# ephemeris.py
SES_MIN = 2.5
LOW_SES_REASON = "single-event S/N below 2.5 (Holczer et al. 2016)"
SHAPE_METHOD = "bootstrap over transits of the quadratic limb-darkened, exposure-integrated fit (Schmitt et al. 2014)"
def drop_low_ses(times: list[TransitTime], ses_by_epoch: dict[int, float],
                 min_ses: float | None = None) -> list[TransitTime]
    # min_ses None reads SES_MIN at call time; an event with a finite SES below it becomes used=False, t_bkjd=None,
    # err_days=None, chi2_red=None, reason LOW_SES_REASON; an epoch without an SES is kept as it is
def fold_depth(time, fnorm, quarter, period, t0, duration_days) -> dict
    # {"depth_ppm", "err_ppm", "source"}: event_depths' mean and err_scaled, source "per-event weighted mean of the box
    # depth"; below 4 usable events stacked_depth, source "box depth of the stacked fold"; None values when neither
def bootstrap_shape(time, fnorm, period, t0, duration_days, point: dict | None, *, n_boot: int = N_BOOT,
                    seed: int = BOOT_SEED, used_epochs=None, model: dict | None = None) -> ShapeErrors
    # model: {"u1", "u2", "exposure_days", "density": (mean, sigma) | None, "free": ShapeFit,
    #         "prior": ShapeFit | None, "quoted": "free" | "prior"}. Each resample fits both variants with fit_shape,
    # started from their full-data solutions (start=). k, t14_h and model_depth_ppm come from the prior variant when
    # there is one (spec 11.3: the radius ratio from the prior fit); b and a_over_r come from the quoted one.
    # model None: u1 = u2 = 0, the CADENCE_MIN exposure and the free variant only (the module's own tests).
# Each bootstrapped block becomes {"median": resample p50, "minus": p50 - p16, "plus": p84 - p50, "point": the
# full-data value}; "boot_median" is gone. A degenerate quoted-free b or a/R* is {"median": point, "minus": None,
# "plus": None, "point": point}; a quoted-prior b and a/R* keep their errors, since the prior removes the degeneracy.
# ParityDepths.as_dict() gains "stacked": {"odd": {"depth_ppm", "err_ppm"}, "even": {...}, "difference_sigma"},
# measured with stacked_depth(..., epochs="odd" | "even") on the same fold (Task 12 prints it).

# dossier.py
DOSSIER_VERSION = "5"   # 5: limb-darkened free and prior fits, folds on the linear ephemeris, SES-cut timing (spec 11.3)
def shape_model(star: stars.StarParams, lc: LightCurve) -> dict
    # {"limb_darkening": limb_darkening(star.teff.median or None), "u1", "u2",
    #  "exposure_days": the median cadence of lc.time, "density": (median, (minus + plus) / 2) | None}
def ephemeris_blocks(lc, fnorm, signal: dict, tests: list[dict], *, n_boot: int = N_BOOT_DEFAULT,
                     star: stars.StarParams | None = None, fnorm_alt=None, thresholds=None, target=None) -> dict
    # keys: ephemeris_extra, timing, shape, parity, secondary, stability, fold
    # fold = {"period", "t0", "source": "linear fit of N transit times" | "search"}
def plot_arrays(lc, fnorm, signal, tests, *, periodogram=None, fold: dict | None = None,
                shape: dict | None = None) -> dict
    # folds on ``fold`` (the search ephemeris when None); the model line is exposure_flux of the quoted ShapeFit
# data["ephemeris"] gains "fold_ephemeris" (fold["source"]); depth_ppm, depth_ppm_err and depth_source come from
#   fold_depth.
# data["shape"] gains "fits": {"free": ShapeFit.as_dict(), "prior": ... | None}, "comparison": compare_fits(...),
#   "limb_darkening": {...} and "exposure_min".
# data["stability"]: {"status", "reason", "ephemeris": fold["source"], "metrics": the rung 3 metrics} | None.

# pack_rows.py
def event_rows(data: dict) -> list[dict]
    # one row per epoch of timing.times (of plots.events without a timing block): {epoch, t_mid_bkjd, quarter,
    # depth_ppm, err_ppm, coverage, ses, timed, timing_reason}; the depth columns come from plots.events by epoch
    # (blank when that event has no depth); ``timed`` is the timing row's ``used``
def param_rows(data: dict, *, both_a_over_r: bool = False) -> list[dict]
    # one a/R* row, "a/R* from the stellar parameters (Kepler's third law)" (planet.a_over_r) or "a/R* fitted"
    # (shape.a_over_r), as shape.comparison.a_over_r_label says; both_a_over_r=True (ephemeris.csv, and so its ExoFOP
    # copy) gives both rows, the second named "a/R* fitted (free fit)" and read from shape.fits.free

# tests/golden/regenerate.py
def main(argv: list[str] | None = None) -> int
    # --engine-root C:\Research\skysift --data-dir .superpowers/sample-run --target kic-006146676 --out <golden>
```

- [ ] **Step 1: Write the failing model tests** in `tests/unit/test_transit_model.py`. The two fit scenarios and their numbers were checked on a prototype of the model (22 October 2026, round 2 planning): the cadence-smeared case moved the data chi-square by 0.0001 under its true-density prior, and the wrong-density case gave a tension of -8.8 sigma.

```python
import json

import pytest

from skysift.lc.transit_model import (A_LABEL_FITTED, A_LABEL_STELLAR, LD_TABLE, NO_DENSITY, UNCONSTRAINED, ShapeFit,
                                      compare_fits, exposure_flux, fit_shape, limb_darkening, quadratic_ld_flux,
                                      stellar_density)
from tests.synthetic import _with_dip

EXPOSURE = 29.4 / 1440


def _ld_planet(period, t0, k, b, a, u1, u2, noise, seed):
    lc = make_lightcurve(noise_ppm=noise, seed=seed)
    ph = (lc.time - t0 + 0.5 * period) % period - 0.5 * period
    return _with_dip(lc, exposure_flux(ph, period, k, b, a, u1, u2, EXPOSURE))


def test_without_limb_darkening_the_quadratic_model_is_the_uniform_disc():
    dt = np.linspace(-0.1, 0.1, 2001)
    assert np.max(np.abs(quadratic_ld_flux(dt, 13.77, 0.046, 0.3, 38.0, 0.0, 0.0)
                         - uniform_disc_flux(dt, 13.77, 0.046, 0.3, 38.0))) < 1e-10


def test_the_central_depth_of_a_small_planet_follows_the_limb_darkening():
    u1, u2, k = 0.4158, 0.3203, 0.046
    depth = 1.0 - float(quadratic_ld_flux(np.array([0.0]), 13.77, k, 0.0, 38.0, u1, u2)[0])
    assert depth == pytest.approx(k ** 2 / (1 - u1 / 3 - u2 / 6), rel=1e-3)        # 2,618 ppm, not k^2 = 2,116


def test_the_coefficients_interpolate_in_teff_and_hold_the_table_edges():
    ld = limb_darkening(3582.0)                                   # KIC 5689121's Teff
    assert ld["u1"] == pytest.approx(0.4158 + 82 / 250 * (0.4224 - 0.4158), abs=1e-6)
    assert ld["u2"] == pytest.approx(0.3203 + 82 / 250 * (0.3037 - 0.3203), abs=1e-6)
    assert ld["law"] == "quadratic" and ld["logg"] == 4.5 and "Claret and Bloemen 2011" in ld["source"]
    assert (limb_darkening(3000.0)["u1"], limb_darkening(9000.0)["u2"]) == (LD_TABLE[0][1], LD_TABLE[-1][2])
    assert limb_darkening(None)["teff"] is None and limb_darkening(None)["u1"] == 0.4035      # the 5750 K row


def test_the_exposure_average_keeps_the_bottom_and_rounds_the_contact():
    dt = np.array([0.0, 0.5 * 2.4 / 24])                         # mid-transit and first contact of a 2.4 h transit
    plain = quadratic_ld_flux(dt, 13.77, 0.046, 0.0, 38.0, 0.42, 0.31)
    smeared = exposure_flux(dt, 13.77, 0.046, 0.0, 38.0, 0.42, 0.31, EXPOSURE)
    assert smeared[0] == pytest.approx(plain[0], abs=2e-5) and smeared[1] < plain[1]
    assert np.array_equal(exposure_flux(dt, 13.77, 0.046, 0.0, 38.0, 0.42, 0.31, 0.05 / 1440), plain)


def test_the_stellar_density_of_kic_5689121s_orbit():
    assert stellar_density(38.1, 13.769414) == pytest.approx(5.52, abs=0.01)    # the live dossier's 5.52 g/cm3


def test_fit_shape_recovers_a_limb_darkened_transit():
    period, t0, k, b, a = 5.4, 134.9, 0.08, 0.2, 12.9
    lc = _ld_planet(period, t0, k, b, a, 0.42, 0.315, 300, 7)
    fit = fit_shape(lc.time, detrend(lc), period, t0, _t14(period, k, b, a), u1=0.42, u2=0.315,
                    exposure_days=EXPOSURE)
    assert fit.ok and fit.variant == "free" and fit.chi2_prior == 0.0 and fit.prior_gcc is None
    assert abs(fit.k - k) < 0.002 and abs(fit.b - b) < 0.1 and abs(fit.a_over_r - a) < 1.0
    assert fit.n_points > 5000 and fit.exposure_min == pytest.approx(29.4)
    json.dumps(fit.as_dict(), allow_nan=False)


def test_a_cadence_smeared_transit_leaves_b_and_a_over_r_to_the_density():
    """KIC 5689121's geometry (13.77 d, k 0.046, a/R* 38.1) at 1,500 ppm: the true-density prior moves the chi-square
    by far less than 2, so the prior fit is quoted and a/R* comes from the stellar parameters."""
    period, t0, k, b, a = 13.77, 136.3, 0.046, 0.3, 38.1
    lc = _ld_planet(period, t0, k, b, a, 0.42, 0.315, 1500, 5)
    args = (lc.time, detrend(lc), period, t0, _t14(period, k, b, a))
    kw = {"u1": 0.42, "u2": 0.315, "exposure_days": EXPOSURE}
    free = fit_shape(*args, **kw)
    prior = fit_shape(*args, **kw, density=(stellar_density(a, period), 0.5))
    cmp = compare_fits(free, prior)
    assert prior.variant == "prior" and prior.chi2_total == pytest.approx(prior.chi2_data + prior.chi2_prior)
    assert cmp["constrained"] is False and cmp["quoted"] == "prior" and cmp["delta_chi2"] <= 2.0
    assert cmp["a_over_r_label"] == A_LABEL_STELLAR and cmp["reason"] == UNCONSTRAINED
    assert abs(prior.a_over_r - a) < 2.0


def test_a_shape_that_contradicts_the_density_is_quoted_free_with_the_tension():
    """A prior eight times the injected density (a/R* doubled), at 10 per cent: the prior term costs about 76 in
    chi-square, so the free values are quoted and the tension is stated in sigma."""
    period, t0, k, b, a = 5.4, 134.9, 0.08, 0.2, 12.9
    lc = _ld_planet(period, t0, k, b, a, 0.42, 0.315, 300, 7)
    args = (lc.time, detrend(lc), period, t0, _t14(period, k, b, a))
    kw = {"u1": 0.42, "u2": 0.315, "exposure_days": EXPOSURE}
    wrong = stellar_density(2 * a, period)
    cmp = compare_fits(fit_shape(*args, **kw), fit_shape(*args, **kw, density=(wrong, 0.1 * wrong)))
    assert cmp["constrained"] is True and cmp["quoted"] == "free" and cmp["a_over_r_label"] == A_LABEL_FITTED
    assert cmp["delta_chi2"] > 2.0 and cmp["density_tension_sigma"] < -5


def test_without_a_density_the_free_fit_is_quoted():
    free = ShapeFit(True, None, "free", 0.05, 0.3, 20.0, 0.1, 2600.0, 100.0, 0.0, 100.0, 500, 0.4, 0.3, 29.4, 5.0,
                    None)
    assert compare_fits(free, None) == {"constrained": None, "quoted": "free", "delta_chi2": None,
                                        "density_tension_sigma": None, "a_over_r_label": A_LABEL_FITTED,
                                        "reason": NO_DENSITY}
```

- [ ] **Step 2: Write the failing ephemeris tests** in `tests/unit/test_ephemeris.py`. Lines 171 to 214 change from `boot_median` to `median` (the resample median) and `point` (the full-data value).

```python
from skysift.ephemeris import LOW_SES_REASON, SES_MIN, drop_low_ses, fold_depth
from skysift.lc.ops import stacked_depth


def test_a_transit_below_the_ses_threshold_is_not_timed(scene):
    _lc, _fn, times = scene
    timed = [t for t in times if t.used][:2]
    cut = drop_low_ses(times, {timed[0].epoch: 1.37, timed[1].epoch: SES_MIN})      # 2.5 itself is kept
    first = next(t for t in cut if t.epoch == timed[0].epoch)
    assert (first.used, first.t_bkjd, first.err_days, first.reason) == (False, None, None, LOW_SES_REASON)
    assert next(t for t in cut if t.epoch == timed[1].epoch) == timed[1] and len(cut) == len(times)


def test_fold_depth_is_the_per_event_mean_with_its_scaled_error(scene):
    lc, fn, _times = scene
    d = fold_depth(lc.time, fn, lc.quarter, P, T0, DUR)
    ev = event_depths(lc.time, fn, lc.quarter, P, T0, DUR)
    assert (d["depth_ppm"], d["err_ppm"]) == pytest.approx((ev.mean, ev.err_scaled))
    assert d["source"] == "per-event weighted mean of the box depth"


def test_the_bootstrap_quotes_the_resample_median_and_keeps_the_full_data_value(scene):
    lc, fn, _times = scene
    se = bootstrap_shape(lc.time, fn, P, T0, DUR, POINT, n_boot=20, seed=1)
    assert set(se.k) == {"median", "minus", "plus", "point"} and se.k["point"] == K
    assert se.k["median"] - 3 * se.k["minus"] <= K <= se.k["median"] + 3 * se.k["plus"]
    assert se.method == ("bootstrap over transits of the quadratic limb-darkened, exposure-integrated fit "
                         "(Schmitt et al. 2014)")


def test_parity_carries_the_stacked_estimator_beside_the_per_event_one(scene):
    lc, fn, _times = scene
    pd = parity_depths(event_depths(lc.time, fn, lc.quarter, P, T0, DUR), lc.time, fn, P, T0, DUR).as_dict()
    st = pd["stacked"]
    assert st["odd"]["depth_ppm"] == pytest.approx(stacked_depth(lc.time, fn, P, T0, DUR, epochs="odd").depth_ppm)
    assert abs(st["difference_sigma"]) < 3 and abs(pd["difference_sigma"]) < 3
```

`parity_depths(events, time=None, fnorm=None, period=None, t0=None, duration_days=None)` keeps its one-argument form. Without the fold arguments `stacked` is None.

- [ ] **Step 3: Write the failing dossier and pack tests.** In `tests/unit/test_dossier.py`:

```python
from skysift.ephemeris import LOW_SES_REASON
from skysift.lc.transit_model import NO_DENSITY


def test_version_5_folds_on_the_linear_ephemeris_and_stores_both_fits(env):
    d = json.loads(generate(env.store, env.blob, env.settings, STAGE2_TARGET, fetch=env.fetch,
                            force=True).data_path.read_text(encoding="utf-8"))
    assert d["dossier_version"] == "5" == DOSSIER_VERSION
    e, timing, shape = d["ephemeris"], d["timing"], d["shape"]
    assert e["fold_ephemeris"] == f"linear fit of {timing['n_used']} transit times"
    assert d["stability"]["ephemeris"] == e["fold_ephemeris"] and "season_depths" in d["stability"]["metrics"]
    assert e["depth_source"] == "per-event weighted mean of the box depth" and e["depth_ppm_err"] > 0
    assert set(shape["fits"]) == {"free", "prior"} and shape["fits"]["prior"]["variant"] == "prior"
    assert shape["limb_darkening"]["law"] == "quadratic" and shape["exposure_min"] == pytest.approx(29.4, abs=0.1)
    assert shape["comparison"]["quoted"] in ("free", "prior")
    assert f"k {shape['fits']['prior']['k']:.4f} from the transit fit" in d["planet"]["rp_rearth"]["source"]
    assert d["plots"]["model_kind"] == "fit"


def test_without_enough_ses_the_fold_stays_on_the_search_ephemeris(env, monkeypatch):
    from skysift import ephemeris as eph_module
    monkeypatch.setattr(eph_module, "SES_MIN", 1e6)            # every transit falls below the threshold
    d = json.loads(generate(env.store, env.blob, env.settings, STAGE2_TARGET, fetch=env.fetch,
                            force=True).data_path.read_text(encoding="utf-8"))
    assert d["timing"]["ok"] is False and d["ephemeris"]["fold_ephemeris"] == "search"
    assert {t["reason"] for t in d["timing"]["times"]} == {LOW_SES_REASON}
    assert d["stability"]["ephemeris"] == "search"


def test_shape_without_a_stellar_density_quotes_the_free_fit(settings, monkeypatch):
    env = _make_env(settings, monkeypatch, responses={**RESPONSES, "WHERE g.source_id": GAIA_HEADER})
    d = json.loads(generate(env.store, env.blob, env.settings, STAGE2_TARGET,
                            fetch=env.fetch).data_path.read_text(encoding="utf-8"))
    assert d["star"]["density_gcc"] is None and d["shape"]["fits"]["prior"] is None
    cmp = d["shape"]["comparison"]
    assert (cmp["quoted"], cmp["reason"], cmp["a_over_r_label"]) == ("free", NO_DENSITY, "fitted")
```

In `tests/unit/test_pack.py`:

```python
def test_events_and_transit_times_list_the_same_epochs_and_the_readme_says_how_many(env_with_dossier):
    rep = _build(env_with_dossier)
    events, times = _rows(rep, "data/events.csv"), _rows(rep, "data/transit_times.csv")
    assert [r["epoch"] for r in events] == [r["epoch"] for r in times]
    assert _header(rep, "data/events.csv") == ["epoch", "t_mid_bkjd", "quarter", "depth_ppm", "err_ppm",
                                               "coverage", "ses", "timed", "timing_reason"]
    readme = (rep.out_dir / "README.md").read_text(encoding="utf-8")
    used = sum(r["used"] == "True" for r in times)
    assert f"`data/events.csv`: the {len(events)} transits with data" in readme
    assert f"the same {len(times)} epochs as `data/events.csv`, {used} of them used" in readme


def test_one_a_over_r_in_the_texts_and_both_in_the_ephemeris_csv(env_with_dossier):
    rep = _build(env_with_dossier)
    rows = {r["parameter"] for r in _rows(rep, "data/ephemeris.csv")}
    assert {"a/R* from the stellar parameters (Kepler's third law)", "a/R* fitted (free fit)"} <= rows
    copy = next(p for p in (rep.out_dir / "exofop").iterdir() if p.name.endswith("ephemeris.csv"))
    assert copy.read_bytes() == (rep.out_dir / "data" / "ephemeris.csv").read_bytes()
    texts = _texts(rep)
    for name in ("README.md", "note/note.tex", "exofop/note.txt", "archive_email.txt"):
        labels = sum(texts[name].count(w) for w in ("a/R* from the stellar parameters", "a/R* fitted"))
        assert labels <= 1, name


def test_the_note_names_the_limb_darkening_and_the_exposure_integration(env_with_dossier):
    env = env_with_dossier
    _store_errors(env)
    flat = " ".join((_build(env).out_dir / "note" / "note.tex").read_text(encoding="utf-8").split())
    assert "quadratic limb-darkened model" in flat and "integrated over the 29.4-minute exposure" in flat
    assert r"\citep{claret2011}" in flat and r"Claret, A., \& Bloemen, S. 2011, A\&A, 529, A75" in flat
    assert "uniform-disc" not in flat and "without limb darkening" not in flat
    assert "on the linear-fit ephemeris" in flat                # the texts say which ephemeris folded
    readme = (_build(env).out_dir / "README.md").read_text(encoding="utf-8")
    k = _stored(env)["shape"]["k"]
    assert f"(full-data fit {k['point']:.4f})" in readme       # the point value beside the median
```

`SHAPE_OK` (test_pack.py lines 948 to 957) gains `"fits"` (both variants), `"comparison"` (quoted "prior", `UNCONSTRAINED`, label "from the stellar parameters"), `"limb_darkening"` and `"exposure_min": 29.4`. Every `boot_median` there, in `test_dossier.py` lines 1056 to 1059 and in `ui_seed.py` line 333 becomes `point`. The header assertion at test_pack.py line 1061 reads `"point"`.

- [ ] **Step 4: Run them to see them fail.**
  - Command: `PYTHONUTF8=1 PYTHONPATH=src C:\Research\skysift\.venv\Scripts\python.exe -m pytest tests/unit/test_transit_model.py tests/unit/test_ephemeris.py tests/unit/test_dossier.py tests/unit/test_pack.py -q -p no:cacheprovider`.
  - Expected: collection stops at `ImportError: cannot import name 'A_LABEL_FITTED' from 'skysift.lc.transit_model'` and `cannot import name 'LOW_SES_REASON' from 'skysift.ephemeris'`. `test_dossier.py` and `test_pack.py` stop at the same imports.

- [ ] **Step 5: Implement the model** in `src/skysift/lc/transit_model.py`, after `fit_transit`, with the signatures of the Interfaces.
  - `limb_darkening`: linear in Teff over `LD_TABLE` (`np.interp`, which holds the edges). Without a Teff it uses the `LD_NO_TEFF` row and stores `teff` as None.
  - `quadratic_ld_flux`: the integral as written in the Interfaces.
    - The projected separation is `a_over_r * sqrt(sin(phi)^2 + (b / a_over_r * cos(phi))^2)` with `phi = 2 pi dt / P`, as in `uniform_disc_flux`.
    - The flux is 1 where `cos(phi) <= 0`.
  - `exposure_flux`: only the grid points with `z < 1 + k` are integrated; the rest are 1.
  - `stellar_density`, `ShapeFit`, `fit_shape` and `compare_fits`.
  - The module docstring gains a paragraph: rung 2 keeps `fit_transit` for the V-shape rule, and the dossier quotes `fit_shape`.
  - Move `EXPOSURE_RESOLUTION_MIN` here and import it in `ephemeris.py`.
  - Run time: a nine-start fit on 3,300 to 10,400 window points took 4 to 8 s in the prototype. With one start per resample, the 200 resamples of both variants take about 2 to 3 minutes per target.

- [ ] **Step 6: Implement the blocks.**
  - `ephemeris.py`: `SES_MIN`, `LOW_SES_REASON`, `drop_low_ses`, `fold_depth`, the new `SHAPE_METHOD`, `_spread(samples, point)` returning `{"median": p50, "minus", "plus", "point": point}`, `bootstrap_shape(..., model=)` as in the Interfaces, and `parity_depths` with its `stacked` block.
  - `dossier.py`, part 1:
    - `shape_model`.
    - `_timing` calls `eph.drop_low_ses(times, ses)` before `linear_fit`. The `ses` come from `event_quality(time, fnorm, period, t0, dur)` on the search ephemeris, from its `epochs` and `ses`.
    - `ephemeris_blocks` works in this order:
      1. Time the transits.
      2. Fold on the linear ephemeris (`fold`) when the timing is ok, else on the search ephemeris (source `"search"`).
      3. On that fold: `fold_depth`, `parity_depths(events, time, fnorm, P, t0, dur)` and `secondary_limit`.
      4. `fit_shape` free, and, with `shape_model(...)["density"]`, prior; then `compare_fits`.
      5. `bootstrap_shape(..., point=<the quoted full-data fit as the point dict>, model=...)`.
      6. `stability = Rung3Stability().run(Signal(**{**{k: signal[k] for k in Signal.model_fields if k in signal}, "period": fold["period"], "t0": fold["t0"]}), DataBundle(lc, fnorm, target, thresholds, fnorm_alt=fnorm_alt))`, kept as `{"status", "reason", "ephemeris", "metrics"}`, or None without thresholds.
    - The rung 2 point (`_fit_point`) still decides whether a shape is fitted, so a failed rung 2 fit keeps Review Focus item 2: the box depth and duration only.
    - `_ephemeris_extra` takes the depth from `fold_depth` and adds `fold_ephemeris`.
  - `dossier.py`, part 2:
    - `generate` works in this order:
      1. `_lookup` first, which gives the star.
      2. `ephemeris_blocks(..., star=star, fnorm_alt=revet_flux(lc, settings.thresholds, Signal(...))[1], thresholds=settings.thresholds, target=tgt)`.
      3. When `shape.fits.prior` is ok (else `.free`) and Gaia answered, `stars.derive(gaia, period, k, depth, np.random.default_rng(STARS_SEED))` runs again, so the planet radius uses the prior fit's k. The star draws are the same, since the seed is the same.
      4. `plot_arrays(..., fold=blocks["fold"], shape=<the quoted fit with u1, u2 and the exposure>)`.
    - `fpp_inputs` gains `depth_ppm: float | None = None`, given `blocks["ephemeris_extra"]["depth_ppm"]`. None keeps `_depth_fraction`.
    - `_assemble` stores `stability`.
    - `_error_caveats` compares `point` with `median` and says "quote the full-data value beside it".
  - `pack_rows.py`: `event_rows`, `param_rows(both_a_over_r=)`, and `shape_rows` with a `point` column.
  - `pack.py`, part 1:
    - `MODEL_CLAUSE = "a quadratic limb-darkened model on a circular orbit, integrated over the 29.4-minute exposure"`.
    - The shape sentence cites `\citep{claret2011}` after "limb-darkened model" in the tex form, and "(Claret and Bloemen 2011)" in the plain form.
    - `BIBLIOGRAPHY` gains `("claret2011", r"{Claret} \& {Bloemen}(2011)", r"Claret, A., \& Bloemen, S. 2011, A\&A, 529, A75")`.
    - `references` gains `"claret2011": shape_sentence(data) is not None`.
    - `shape_sentence` prints one a/R*:
      - quoted "prior": "b and a/R* are unconstrained at this cadence, consistent with the host's density: b is <b> and a/R* <planet a/R*> from the stellar parameters";
      - quoted "free": "b is <b> and a/R* <a> fitted, <|tension|> sigma from the host's density".
  - `pack.py`, part 2:
    - `_write_pack` writes `ephemeris.csv` from `param_rows(data, both_a_over_r=True)`, `shape.csv` with the header `["parameter", "value", "minus", "plus", "unit", "point", "note"]`, and `events.csv` from `event_rows`.
    - `file_notes(data)` replaces the two static `FILE_NOTES` lines, and `_fields` uses it:
      - "`data/events.csv`: the N transits with data: epoch, mid-time, quarter, depth, error, coverage, single-event statistic and whether the transit was timed";
      - "`data/transit_times.csv`: the same N epochs as `data/events.csv`, M of them used: mid-time in BKJD and BJD_TDB, error, reduced chi-square and why a transit was not used".
  - `exofop.py`: `ar_star` is the quoted a/R*, which is the stellar one with its errors when `quoted` is "prior".
  - The texts say which ephemeris folded:
    - the note's signal section adds "; the depth and the shape below are measured on the linear-fit ephemeris" (or "on the search ephemeris", from `fold_ephemeris`);
    - the caption reads "folded on the <P> day linear-fit (search) ephemeris";
    - the README's depth row names it in its source.
  - The README numbers table prints the shape rows k, b and a/R* as "<median ± err> (full-data fit <point>)", so the point value stands beside the resample median (spec 11.3).
  - `dossier.js:569` and `dossier.html:304`: `boot_median` becomes `point`, labelled "full-data fit".
  - `tools/dossier_compare.py`: `GOLDEN_KEYS` gains `"stability"`.

- [ ] **Step 7: Write the golden helper and regenerate the golden.**
  - `tests/golden/regenerate.py` (`main(argv)`; no test imports it, since it reaches Gaia) works in this order:
    1. `os.chdir(--engine-root)`, because the stored blob paths are relative to `C:\Research\skysift`.
    2. Copy `<data-dir>/engine.db` with `sqlite3.connect(src).backup(sqlite3.connect(dst))`, and `<data-dir>/calibration/kbonus_bkg.jsonl`, into a fresh folder under the system temp directory.
    3. Build `Settings(data_dir=scratch, database_url=f"sqlite:///{scratch}/engine.db", blob_url=f"file://./{data_dir}/blobs")`.
    4. Run `dossier.generate(store, blob, settings, target, force=True, fetch=net.fetch)`.
    5. Write `tools.dossier_compare.golden_block(data)` to `--out` with `jsonutil.dumps(..., indent=1)`.
    6. Exit 1, writing nothing, when the dossier's `dossier_version` is not `dossier.DOSSIER_VERSION`.
  - The live store and the blob folder are only read.
  - Run it in the background at BelowNormal from `C:\Research\skysift`:

    ```
    PYTHONUTF8=1 PYTHONPATH=".worktrees/referee-pack/src;.worktrees/referee-pack" .venv\Scripts\python.exe .worktrees/referee-pack/tests/golden/regenerate.py --engine-root C:\Research\skysift --data-dir .superpowers/sample-run --target kic-006146676 --out .worktrees/referee-pack/tests/golden/kic-006146676-generated.json
    ```

    Run it by path, not with `-m`, so that the main checkout's `tests` package does not shadow the worktree's.
  - Then replace the pins of version "4" (test_dossier.py lines 676, 974, 1249 and 1323; test_pack.py line 1815) with `DOSSIER_VERSION`.
  - The golden's numbers move with the linear-ephemeris fold. So the three `_check_live_shape` assertions that type the version 4 digits (test_pack.py lines 1853 to 1855: the odd and even depths, the phase 0.5 depth, the strongest dip) become `str(pack_module.parity_sentence(data, True)) in flat` and `str(pack_module.secondary_sentence(data, True)) in flat`. They then follow the golden rather than pin it.

- [ ] **Step 8: Run the tests** (all pass).
  - `PYTHONUTF8=1 PYTHONPATH=src C:\Research\skysift\.venv\Scripts\python.exe -m pytest tests/unit/test_transit_model.py tests/unit/test_ephemeris.py tests/unit/test_dossier.py tests/unit/test_pack.py tests/unit/test_crosscheck.py tests/unit/test_ui_dossiers.py tests/unit/test_rung3_aperture.py -q -p no:cacheprovider`.
  - Then `PYTHONUTF8=1 PYTHONPATH=src C:\Research\skysift\.venv\Scripts\python.exe -m ruff check src tests tools`.
  - Then record the four live-shape counts: `... -m pytest tests/unit/test_pack.py -k live_shape -vv -p no:cacheprovider`.

- [ ] **Step 9: Commit.**

```
git add src/skysift/lc/transit_model.py src/skysift/ephemeris.py src/skysift/dossier.py src/skysift/pack_rows.py src/skysift/pack.py src/skysift/exofop.py src/skysift/templates/pack/README.md.j2 src/skysift/ui/static/dossier.js src/skysift/ui/templates/dossier.html tools/dossier_compare.py tests/golden/regenerate.py tests/golden/kic-006146676-generated.json tests/unit/test_transit_model.py tests/unit/test_ephemeris.py tests/unit/test_dossier.py tests/unit/test_pack.py tests/unit/test_crosscheck.py tests/unit/test_ui_dossiers.py tests/unit/ui_seed.py
git commit -m "feat(shape): limb-darkened free and density-prior fits, folds on the linear ephemeris, SES-cut timing (dossier version 5)

Live-shape words: <four counts>.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```


---

### Task 11: The prior-claim comparison, the FPP headline, the false-alarm wording and their gates (spec 11.1, 11.2, 11.4)

**Files:**
- Modify: `docs/candidates/kic-005689121/prior_claims.json` (the new fields; the existing six stay)
- Modify: `src/skysift/dossier.py`.
  - Lines 1298 to 1343: `PRIOR_CLAIM_FIELDS`, `prior_claims`, `PRIOR_CLAIM_PUBLIC`, `public_claims`.
  - Lines 655 to 708: the exclusion radius, beside `centroid_offset`.
  - Lines 1033 to 1147: the caveat title "This is a candidate until independent follow-up confirms it".
  - Lines 1381 to 1452 and 1473 to 1511: the `calibration` block in `generate` and `_assemble`.
- Modify: `src/skysift/store/db.py`: `Store.sweep_size`, after `finish_run`, line 399.
- Modify: `src/skysift/pack_text.py` (lines 216 to 226, `PRIOR_CLAIM_PUBLIC` and `prior_claims`).
- Modify: `src/skysift/pack.py`.
  - Lines 345 to 347: `RECOVERY_WORDS` goes.
  - Lines 382 to 410: `prior_claim_sentence` and `priority_sentence`. A new `prior_comparison` and `comparison_text`.
  - Lines 889 to 967: `numbers`. Lines 980 to 1022: `abstract_text`. Lines 1025 to 1052: `archive_paragraph`.
  - Lines 1121 and 1250 to 1258: "the independent TRICERATOPS cross-check", "independent of this one".
  - Lines 1184 to 1219: `gate_problems`.
- Modify: `src/skysift/crosscheck.py`.
  - Line 52: `DIFFERENT_QUESTIONS` goes. Line 570: `verdict`'s "no independent cross-check".
  - Lines 598 to 607: `CLASS_PHRASES`, which the dossier page keeps.
  - Lines 647 to 685: `comparison_sentence`, and a new `headline`.
  - Lines 391 to 403: the `pixel_exclusion` key of `inputs.json`.
- Modify: `src/skysift/zenodo.py` (lines 131 to 151, `probability_sentences`).
- Modify: `src/skysift/exofop.py` (lines 76 to 88, `prior_fields`).
- Modify: `src/skysift/templates/pack/note.tex.j2` (line 7, the title; line 54, the false-positive section), `README.md.j2` (line 71), `exofop_note.txt.j2` (lines 7 and 33) and `archive_email.txt.j2`.
- Modify: `tools/dossier_compare.py` (`GOLDEN_KEYS`).
- Test: `tests/unit/test_dossier.py` (`PRIOR_CLAIM`, line 1192, and the prior-claim tests after it), `tests/unit/test_store.py`, `tests/unit/test_pack.py`.
  - In `test_pack.py`: lines 1564 to 1675 hold the prior-claim tests, and line 1594 is renamed. Lines 1834 to 1861 hold `_check_live_shape`, with "validation lines".
- Test: `tests/unit/test_crosscheck.py`, `tests/unit/test_zenodo.py`, and the golden (regenerated).

**Interfaces:**
- Consumes:
  - Task 10's dossier: `ephemeris.period_days`, `period_err_days`, `depth_ppm`, `depth_ppm_err` (on the fold ephemeris) and the `timing` block.
  - `ephemeris.predict_from(timing, epoch) -> (t_bkjd, err_days | None)`.
  - `crosscheck.complete(block)`, `probability_text(x)`, `range_text(block, metric)`.
  - `calibrate.FalseAlarm` (`k`, `n`, `fap`, `calibrated`, `n_by_kind`) and `calibrate.read_rows(path)`, whose rows carry `target_id`.
  - `RunRow` (`job_key`, `target_id`, `detector`, `detector_version`, `status`), `TargetRow.source`, `RECHECK_JOB_PREFIX`.
- Produces:
```python
# dossier.py
RELATIONSHIPS = ("re-detection", "blind-recovery", "vetting-only")
PRIOR_CLAIM_NUMBERS = ("period_days", "period_err_days", "t0_bjd", "t0_err_days", "depth_ppm", "depth_err_ppm", "fpp")
PRIOR_CLAIM_FIELDS = ("doi", "date", "author", "title", "status", "note", "relationship", "development_note",
                      *PRIOR_CLAIM_NUMBERS, "fpp_tool")
PRIOR_CLAIM_PUBLIC = ("doi", "date", "author", "title", "relationship", "development_note", *PRIOR_CLAIM_NUMBERS,
                      "fpp_tool")
# prior_claims(): text fields stripped strings ("" when absent), numbers float or None, relationship one of
# RELATIONSHIPS or None (absent); another value raises PriorClaimsError naming it
EXCLUSION_FLOOR_ARCSEC = 0.5
EXCLUSION_RULE = "max(3 sigma, 0.5 arcsec) about the Gaia-referenced difference centroid (spec 11.5)"
def exclusion_radius(block: dict | None) -> float | None
    # max(3 * catalogue_referenced_err_arcsec, EXCLUSION_FLOOR_ARCSEC); None without the block or the error
# data["centroid_offset"] gains "exclusion_radius_arcsec" and "exclusion_rule"
SWEEP_QUERY = ("distinct target_id of runs with status done, the signal's detector and detector version, a job key "
               "not starting 'recheck:', on targets of the signal's source")
def calibration_block(store, signal: dict, source: str, rows: list[dict], fap, target_id: str) -> dict
    # {"sweep_size": int | None (None for 0), "sweep_source", "sweep_detector", "sweep_detector_version",
    #  "sweep_query": SWEEP_QUERY, "null_stars": distinct calibration target_ids other than target_id,
    #  "null_searches": fap.n, "expected_by_chance": sweep_size * fap.fap | None}
# data["calibration"] = calibration_block(...)

# store/db.py
def sweep_size(self, source: str, detector: str, detector_version: str) -> int

# pack.py
FORBIDDEN_WORDS = (r"\bblind\b", r"\bindependent recovery\b")      # spec 11.1, case-insensitive
RELATIONSHIP_WORDS = {"re-detection": "a re-detection",
                      "blind-recovery": "a recovery made before SkySift knew of the claim",
                      "vetting-only": "a vetting of the claim's signal, not a detection"}
RELATIONSHIP_VERBS = {"re-detection": "re-detected it in its sweep of",
                      "blind-recovery": "found it in its sweep of", "vetting-only": "vetted it on"}
IN_HOUSE = "a four-scenario estimate without a bound-companion or diluted-planet scenario gives {value}, a lower bound"
BOTH_TOOLS = ("Both tools exclude a nearby or background eclipsing binary once the pixels are counted; an unresolved "
              "bound companion is the open channel.")
def prior_comparison(data: dict) -> dict | None
    # the earliest claim against the dossier: {"period": row, "epoch": {**row, "epoch": E}, "depth": row,
    #  "fpp": {"theirs", "tool", "ours", "ours_tool"}, "one_sided": [names compared with our error alone]}, each
    #  row {"ours", "ours_err", "theirs", "theirs_err", "sigma"}; sigma = |ours - theirs| / hypot(the errors present),
    #  None when neither error exists; the epoch is E = epoch_ref + round((t0_claim_bkjd - t_ref) / period) and
    #  "ours" its predict_from(timing, E); a quantity the claim does not give is left out; None without a claim
def comparison_text(data: dict) -> str | None
def prior_claim_sentence(data: dict, tex: bool = False) -> str | None     # who, when, the relationship and the note
def in_house_text(data: dict) -> str                                       # IN_HOUSE, or "not computed" wording
# crosscheck.py
NO_CLASS_CLAUSE = "in none of its validation classes, which do not apply at 30-minute cadence"
def headline(data: dict, tex: bool = False) -> str | None
    # "TRICERATOPS <version> gives a false-positive probability of <fpp> (<fpp_no_stp> without a planet on an
    #  unresolved bound companion), in none of its validation classes, which do not apply at 30-minute cadence
    #  (Giacalone et al. 2021)"; None without a complete, current cross-check
# comparison_sentence keeps the method, the three medians with their ranges, the run count and the dominant scenario,
# prints NO_CLASS_CLAUSE where CLASS_PHRASES stood and drops the "SkySift's own estimate ..." tail; CLASS_PHRASES and
# classify stay for the dossier page
# export_inputs: inputs.json gains "pixel_exclusion": {"radius_arcsec", "rule", "centre_offset_arcsec"} | None
#   (the triceratops tool does not read it; a rerun with the excluded field stars removed is a follow-up)
# zenodo.py
FAP_NULLS = ("{k} of {n:,} null searches (the inverted and scrambled light curves of {stars:,} other KBonus stars, "
             "searched without the masked pass and alias resolution) reached the signal's SDE")
FAP_SWEEP = ("; across the {s:,}-star sweep about {expected} such peaks are expected by chance, and the vetting "
             "ladder, not the SDE, separates the candidate from them")
FAP_NO_SWEEP = "; the vetting ladder, not the SDE, separates the candidate from such peaks"
def probability_sentences(data: dict) -> tuple[str, str]
    # fap: "False-alarm probability: <words>. " + FAP_NULLS + (FAP_SWEEP | FAP_NO_SWEEP) + "."; expected is
    # round(S x FAP) with a thousands separator, "fewer than one" below 0.5
    # fpp: "False-positive probability: " + headline + "; " + IN_HOUSE + "." with a current cross-check, else
    # "False-positive probability: " + IN_HOUSE with a capital + "." (the provisional tail as before)
```

- [ ] **Step 1: Write the failing dossier and store tests.** In `tests/unit/test_dossier.py` (`PRIOR_CLAIM` at line 1192 gains the KIC 5689121 fields of spec 11.1):

```python
from skysift.dossier import SWEEP_QUERY

DEVELOPMENT_NOTE = ("the sweep's target list and the pipeline's tests used this star from 14 October 2026, the day "
                    "after the claim; the search itself used no prior ephemeris")
PRIOR_CLAIM = {"doi": "10.5281/zenodo.23325261", "date": "2026-10-13", "author": "Kuber Mehta",
               "title": "Planethunt: archival evidence for the KIC 5689121 transit candidate",
               "status": "Research Note submitted 13 October 2026, reported not accepted",
               "note": "same host assignment; SkySift's sweep a week later",
               "relationship": "re-detection", "development_note": DEVELOPMENT_NOTE,
               "period_days": 13.769448, "period_err_days": None, "t0_bjd": 2455630.24543, "t0_err_days": None,
               "depth_ppm": 1883, "depth_err_ppm": 106, "fpp": 0.07, "fpp_tool": "TRICERATOPS"}


def test_prior_claims_read_the_relationship_and_the_numbers(tmp_path):
    folder = tmp_path / "kic-005689121"
    folder.mkdir()
    (folder / "prior_claims.json").write_text(json.dumps([PRIOR_CLAIM]), encoding="utf-8")
    claim = dossier_module.prior_claims("kic-005689121", root=tmp_path)[0]
    assert claim["relationship"] == "re-detection" and claim["development_note"] == DEVELOPMENT_NOTE
    assert (claim["period_days"], claim["t0_bjd"], claim["depth_ppm"], claim["depth_err_ppm"], claim["fpp"]) == (
        13.769448, 2455630.24543, 1883.0, 106.0, 0.07)
    assert claim["period_err_days"] is None and claim["fpp_tool"] == "TRICERATOPS"
    public = dossier_module.public_claims({"prior_claims": [claim]})["prior_claims"][0]
    assert set(public) == set(dossier_module.PRIOR_CLAIM_PUBLIC) and "status" not in public and "note" not in public


def test_a_prior_claim_with_an_unknown_relationship_is_refused(tmp_path):
    folder = tmp_path / "kic-005689121"
    folder.mkdir()
    (folder / "prior_claims.json").write_text(json.dumps([{**PRIOR_CLAIM, "relationship": "independent"}]),
                                              encoding="utf-8")
    with pytest.raises(dossier_module.PriorClaimsError, match="relationship 'independent'"):
        dossier_module.prior_claims("kic-005689121", root=tmp_path)
    (folder / "prior_claims.json").write_text(json.dumps([{k: v for k, v in PRIOR_CLAIM.items()
                                                           if k != "relationship"}]), encoding="utf-8")
    assert dossier_module.prior_claims("kic-005689121", root=tmp_path)[0]["relationship"] is None


def test_the_exclusion_radius_is_three_sigma_with_a_half_arcsec_floor():
    assert dossier_module.exclusion_radius({"catalogue_referenced_err_arcsec": 0.25312484}) == pytest.approx(
        0.75937, abs=1e-5)                                          # KIC 5689121's live error
    assert dossier_module.exclusion_radius({"catalogue_referenced_err_arcsec": 0.1}) == 0.5
    assert dossier_module.exclusion_radius(None) is None
    assert dossier_module.exclusion_radius({"catalogue_referenced_err_arcsec": None}) is None


def test_the_dossier_stores_the_sweep_size_and_the_exclusion_radius(env):
    d = json.loads(generate(env.store, env.blob, env.settings, STAGE2_TARGET, fetch=env.fetch,
                            force=True).data_path.read_text(encoding="utf-8"))
    sig = d["signal"]
    assert d["calibration"]["sweep_size"] == env.store.sweep_size("kbonus_bkg", sig["detector"],
                                                                  sig["detector_version"])
    assert d["calibration"]["null_searches"] == d["fap"]["n"] and d["calibration"]["sweep_query"] == SWEEP_QUERY
    co = d["centroid_offset"]
    assert co["exclusion_radius_arcsec"] == pytest.approx(max(3 * co["catalogue_referenced_err_arcsec"], 0.5))
```

In `tests/unit/test_store.py`:

```python
def test_sweep_size_counts_the_done_sweep_runs_of_one_source_and_search(tmp_path):
    s = make_store(tmp_path)
    for tid, source in (("a", "kbonus_bkg"), ("b", "kbonus_bkg"), ("c", "kbonus_bkg"), ("d", "kbonus_bkg"),
                        ("e", "kbonus_bkg"), ("f", "other"), ("g", "kbonus_bkg")):
        s.upsert_target(Target(source=source, target_id=tid, ra=0, dec=0))
    for job, tid, version, status in (("a", "a", "5", "done"), ("b", "b", "5", "done"), ("c1", "c", "5", "error"),
                                      ("c2", "c", "5", "done"), ("d", "d", "5", "error"),
                                      ("recheck:P/2:1", "e", "5", "done"), ("f", "f", "5", "done"),
                                      ("g", "g", "4", "done")):
        s.finish_run(s.start_run(job, tid, "box_dip", version, "1"), status)
    assert s.sweep_size("kbonus_bkg", "box_dip", "5") == 3            # a, b and c; not d, e, f or g
    assert s.sweep_size("kbonus_bkg", "box_dip", "9") == 0
```

- [ ] **Step 2: Write the failing pack, cross-check and Zenodo tests.** In `tests/unit/test_pack.py`:

```python
import math                                     # at the top of the file

LIVE_5689121 = {"ephemeris": {"period_days": 13.769414, "period_err_days": 4.7713013e-05, "depth_ppm": 2031.0,
                              "depth_ppm_err": 113.0},
                "timing": {"ok": True, "t0": 136.314294, "t_ref": 797.246185, "t_ref_err": 0.0014377853,
                           "epoch_ref": 48, "period": 13.769414, "period_err": 4.7713013e-05},
                "fpp": {"value": 0.0015958986, "signed_off": True},
                "prior_claims": [{k: PRIOR_CLAIM[k] for k in dossier_module.PRIOR_CLAIM_PUBLIC}]}


def test_prior_comparison_gives_each_difference_in_sigma():
    cmp = pack_module.prior_comparison(LIVE_5689121)
    assert cmp["period"]["sigma"] == pytest.approx(3.4e-5 / 4.7713013e-05, rel=1e-3)        # 0.71
    assert cmp["epoch"]["epoch"] == 48
    assert cmp["epoch"]["sigma"] == pytest.approx(0.000755 / 0.0014377853, rel=1e-3)          # 0.53
    assert cmp["depth"]["sigma"] == pytest.approx(148 / math.hypot(113, 106), rel=1e-3)       # 0.96
    assert cmp["one_sided"] == ["period", "epoch"] and cmp["fpp"]["theirs"] == 0.07
    assert pack_module.comparison_text(LIVE_5689121) == (
        "Against that claim the period differs by 0.7 sigma, the mid-transit time at epoch 48 by 0.5 sigma and the "
        "depth by 1.0 sigma (1,883 ± 106 ppm there; the claim gives no error on the period or the epoch, so those "
        "use ours alone); its false-positive probability of 0.07 (TRICERATOPS) sits beside our 0.0016 (SkySift's "
        "four-scenario estimate).")


def test_a_prior_claim_makes_every_text_a_vetting_and_never_a_blind_recovery(env_prior):
    rep = _build(env_prior, tic="169822650")
    texts = _texts(rep)
    note = " ".join(texts["note/note.tex"].split())
    assert r"\title{SkySift vetting of the 40.290 day transit candidate on KIC 6146676}" in note
    for name, text in texts.items():
        assert not re.search(r"\bblind\b", text, re.I) and "independent recovery" not in text.lower(), name
        assert "independent" not in text.lower().replace("independent in method", ""), name
        assert PRIOR_DOI in text and "13 October 2026" in text, name
    for name in ("README.md", "note/note.tex"):
        assert "a re-detection" in texts[name] and "the search itself used no prior ephemeris" in texts[name], name
        assert "Against that claim the period differs by" in texts[name], name


def test_the_live_gate_refuses_a_claim_without_a_relationship_and_the_forbidden_words(env_prior):
    data = _stored(env_prior)
    claim = data["prior_claims"][0]
    no_rel = {**data, "prior_claims": [{**claim, "relationship": None}]}
    assert ("prior claim doi:10.5281/zenodo.23325261 has no relationship (re-detection, blind-recovery or "
            "vetting-only)") in pack_module.gate_problems(no_rel)
    blind = {**data, "prior_claims": [{**claim, "development_note": "a blind search found it"}]}
    assert "the Research Note says 'blind', which spec 11.1 rules out" in pack_module.gate_problems(blind)


def test_the_fpp_headline_leads_with_triceratops(env_with_dossier):
    env = env_with_dossier
    _store_errors(env)
    _store_cross_check(env)
    data = _stored_with_cross_check(env)
    abstract = pack_module.abstract_text(data)
    assert abstract.index("TRICERATOPS") < abstract.index("a four-scenario estimate without a bound-companion")
    assert "in none of its validation classes, which do not apply at 30-minute cadence (Giacalone et al. 2021)" in (
        abstract)
    assert f"gives {cross_check.probability_text(data['fpp']['value'])}, a lower bound" in abstract
    texts = _texts(_build(env))
    assert all("answer different questions" not in t for t in texts.values())
    assert pack_module.BOTH_TOOLS in " ".join(texts["note/note.tex"].split())


def test_the_gate_refuses_an_abstract_with_the_in_house_fpp_alone(env_with_dossier, monkeypatch):
    env = env_with_dossier
    _store_errors(env)
    _store_cross_check(env)
    monkeypatch.setattr(cross_check, "headline", lambda data, tex=False: None)
    assert ("the abstract carries the in-house false-positive probability without the TRICERATOPS one"
            in pack_module.gate_problems(_stored_with_cross_check(env)))
```

In `tests/unit/test_zenodo.py`:

```python
FAP = {"calibrated": True, "k": 38, "n": 1998, "words": "1 in 51", "fap": 0.019509755}


def test_the_false_alarm_sentence_names_the_nulls_and_the_sweep():
    fap, _fpp = zenodo.probability_sentences({"fap": FAP, "calibration": {"sweep_size": 1579, "null_stars": 999}})
    assert fap == ("False-alarm probability: 1 in 51. 38 of 1,998 null searches (the inverted and scrambled light "
                   "curves of 999 other KBonus stars, searched without the masked pass and alias resolution) reached "
                   "the signal's SDE; across the 1,579-star sweep about 31 such peaks are expected by chance, and the "
                   "vetting ladder, not the SDE, separates the candidate from them.")
    fap, _fpp = zenodo.probability_sentences({"fap": FAP, "calibration": {"sweep_size": None, "null_stars": 999}})
    assert fap.endswith("reached the signal's SDE; the vetting ladder, not the SDE, separates the candidate from "
                        "such peaks.") and "of this source" not in fap
```

In `tests/unit/test_crosscheck.py`:

```python
from skysift.dossier import EXCLUSION_RULE


def test_the_export_writes_the_pixel_exclusion_radius(env, tmp_path):
    out = cc.export_inputs(env.store, env.blob, env.settings, STAGE2_TARGET, tmp_path / "cc", fetch=env.xfetch)
    inputs = json.loads((out / "inputs.json").read_text(encoding="utf-8"))
    co = _stored(env)["centroid_offset"]
    assert inputs["pixel_exclusion"] == {"radius_arcsec": co["exclusion_radius_arcsec"], "rule": EXCLUSION_RULE,
                                         "centre_offset_arcsec": co["catalogue_referenced_arcsec"]}


def test_the_comparison_sentence_names_no_class_and_no_different_questions(env):
    data = {**_stored(env), "cross_check": {**fake_result(cc.inputs_fingerprint(_stored(env))), "current": True}}
    sentence = cc.comparison_sentence(data)
    assert cc.NO_CLASS_CLAUSE in sentence and "answer different questions" not in sentence
    assert "SkySift's own estimate" not in sentence and not hasattr(cc, "DIFFERENT_QUESTIONS")
```

`_check_live_shape` (test_pack.py lines 1834 to 1861) drops its `"validation lines"` assertion for `cross_check.NO_CLASS_CLAUSE in flat`, and `test_a_prior_claim_makes_every_text_an_independent_recovery` (line 1594) is the renamed test above. `RECOVERY` (line 1564) goes.

- [ ] **Step 3: Run them to see them fail.**
  - Command: `PYTHONUTF8=1 PYTHONPATH=src C:\Research\skysift\.venv\Scripts\python.exe -m pytest tests/unit/test_dossier.py tests/unit/test_store.py tests/unit/test_pack.py tests/unit/test_crosscheck.py tests/unit/test_zenodo.py -q -p no:cacheprovider`.
  - Expected failures:
    - `AttributeError: 'Store' object has no attribute 'sweep_size'`;
    - `AttributeError: module 'skysift.dossier' has no attribute 'exclusion_radius'`;
    - `KeyError: 'relationship'` in the claims test;
    - `AttributeError: module 'skysift.pack' has no attribute 'prior_comparison'`;
    - the false-alarm assertion, which still reads "null searches of this source".

- [ ] **Step 4: Implement the claims and the sweep.**
  - `dossier.prior_claims`: reads the new fields (numbers through `_num`, text stripped) and checks `relationship` against `RELATIONSHIPS`, raising `PriorClaimsError(f"{path}: claim {i + 1} has the relationship {value!r}, not one of re-detection, blind-recovery, vetting-only")`.
  - `public_claims` uses the new `PRIOR_CLAIM_PUBLIC`. `pack_text.prior_claims` keeps the numbers as numbers.
  - `Store.sweep_size`:

```python
def sweep_size(self, source: str, detector: str, detector_version: str) -> int:
    """The targets the sweep searched with this search: distinct target ids of done runs of ``detector`` at
    ``detector_version`` on targets of ``source``, recheck runs left out (SWEEP_QUERY in the dossier)."""
    q = (select(func.count(func.distinct(RunRow.target_id))).join(TargetRow, TargetRow.target_id == RunRow.target_id)
         .where(RunRow.status == "done", RunRow.detector == detector, RunRow.detector_version == detector_version,
                RunRow.job_key.not_like(f"{RECHECK_JOB_PREFIX}%"), TargetRow.source == source))
    with self.session() as s:
        return int(s.scalar(q) or 0)
```

  On the live store this gives 1,579 for search version `5+6cfe4cf4` (1,590 targets have runs; 11 ended in a MAST outage).
  - `dossier.calibration_block`, `exclusion_radius` and the two `centroid_offset` keys. `generate` stores `data["calibration"]`, and `GOLDEN_KEYS` gains `"calibration"`.
  - Edit `docs/candidates/kic-005689121/prior_claims.json` to the `PRIOR_CLAIM` of Step 1. The `status` and `note` it already holds stay, and they stay out of every public file.

- [ ] **Step 5: Implement the texts and the gates.**
  - `pack.prior_claim_sentence`:
    - Plain form: "Kuber Mehta reported this signal first, in "<title>" (Zenodo, 13 October 2026, doi:<doi>); SkySift's result is a re-detection: <development note>."
    - Tex form: `\citet{mehta2026}` in place of the name and title.
  - `pack.comparison_text`, with the exact wording of the Step 2 test. Together with `prior_claim_sentence` it is one sentence on the relationship and one on the comparison (spec 11.1).
    - The sigmas are printed to one decimal.
    - The claim's own numbers are printed as the claim gives them (`f"{v:,g}"`: "1,883 ± 106", "0.07"): they quote another work, so `pm_text`'s two-figure rule does not round them.
    - Our FPP is `probability_text` of the TRICERATOPS median, "(TRICERATOPS)", when a current cross-check exists; otherwise of `fpp.value`, "(SkySift's four-scenario estimate)".
  - `README.md.j2` line 17 and `exofop_note.txt.j2` line 12 print `n.comparison_text` after `n.prior_plain`.
  - `numbers()` gains `comparison_text` and `in_house_text`.
  - `note.tex.j2`:
    - Line 7: `\BLOCK{if n.prior_text}SkySift vetting of the\BLOCK{else}A\BLOCK{endif}`.
    - The signal section prints `n.prior_text` then `n.comparison_text`.
    - Line 54 becomes: `\VAR{n.fap_text}`, the cross-check sentence (when present), then `\VAR{n.in_house_text}` with a capital, the method sentence after \citet{morton2012} as now, then `\VAR{n.both_tools}` (`BOTH_TOOLS`, printed when the pixel rung voted), then the TESS sentence.
  - `README.md.j2` line 71 goes; the README prints `n.fpp_text`, `n.cross_check_sentence` and `BOTH_TOOLS`.
  - `exofop_note.txt.j2` line 7: `{% if n.prior_plain %}vetting of the {% endif %}`.
  - `exofop.prior_fields`: `notes = f"SkySift vetting of the signal first reported in doi:{doi} ({date}); {relationship}"`, cut to `EXOFOP_NOTES_MAX`.
  - `archive_paragraph` with a claim: "... first reported by <author> on <date> (doi:...). SkySift's vetting of it is now in an accepted refereed paper ... (doi:RNAAS) that reported the vetting. SkySift (search version ..., commit ...) <RELATIONSHIP_VERBS[relationship]> the KBonus Kepler Background light curves; <development note>."
  - `priority_sentence`: "The upload note presents this result as SkySift's vetting of that claim's signal (<RELATIONSHIP_WORDS>), and the planet file's paper and notes fields name its DOI."
  - Every other "independent" in the texts goes, so the word stays for the vetting ladder alone (spec 11.1):
    - "until follow-up confirms it", in `abstract_text`, the `dossier.caveats` title and `exofop_note.txt.j2` line 33;
    - "the TRICERATOPS cross-check", in `FILE_NOTES`;
    - "at a period unrelated to this one", in `other_signals_sentence`;
    - "no TRICERATOPS cross-check", for the reason "no independent cross-check" in `crosscheck.verdict` (line 570), which the README prints. The gate lists of `test_a_live_draft_refuses_a_dossier_without_ephemeris_errors` (test_pack.py lines 1371 to 1379) change with it.
  - `abstract_text`:
    - With a current cross-check: "... The false-alarm probability against 1,998 null searches of other KBonus stars is <words>; <headline>; <IN_HOUSE>."
    - Without one: "...; <IN_HOUSE>."
    - "of this source" goes.
  - `crosscheck.headline`, `NO_CLASS_CLAUSE` and the trimmed `comparison_sentence`. `DIFFERENT_QUESTIONS` goes. `export_inputs` writes `pixel_exclusion` from the stored `centroid_offset`.
  - `zenodo.probability_sentences`, with `FAP_NULLS`, `FAP_SWEEP` and `FAP_NO_SWEEP` from `data["calibration"]` (`null_stars`; `sweep_size`).
  - `gate_problems` gains the three rules, in this order:
    1. For each claim without `relationship`: f"prior claim doi:{doi} has no relationship (re-detection, blind-recovery or vetting-only)".
    2. For each of `render_note(raw)` ("the Research Note"), `abstract_text(raw)` ("the abstract"), `archive_paragraph(raw, DOI_PLACEHOLDER, None)` ("the archive email") and `exofop.prior_fields(raw).get("notes", "")` ("the ExoFOP notes field"), and each `FORBIDDEN_WORDS` pattern found (case-insensitive): f"the {name} says '{match}', which spec 11.1 rules out".
    3. When `cc.complete(raw.get("cross_check"))` and "TRICERATOPS" is not in `abstract_text(raw)`: "the abstract carries the in-house false-positive probability without the TRICERATOPS one".
  - The README and the ExoFOP upload note print the same sentence functions, so these four sources cover them.

- [ ] **Step 6: Regenerate the golden** with Task 10's helper (Step 7's command). Then run the tests (all pass): `PYTHONUTF8=1 PYTHONPATH=src C:\Research\skysift\.venv\Scripts\python.exe -m pytest tests/unit/test_dossier.py tests/unit/test_store.py tests/unit/test_pack.py tests/unit/test_crosscheck.py tests/unit/test_zenodo.py tests/unit/test_ui_dossiers.py -q -p no:cacheprovider`, then `PYTHONUTF8=1 PYTHONPATH=src C:\Research\skysift\.venv\Scripts\python.exe -m ruff check src tests tools`, then record the four live-shape counts (`-k live_shape -vv`).

- [ ] **Step 7: Commit.**

```
git add docs/candidates/kic-005689121/prior_claims.json src/skysift/dossier.py src/skysift/store/db.py src/skysift/pack_text.py src/skysift/pack.py src/skysift/crosscheck.py src/skysift/zenodo.py src/skysift/exofop.py src/skysift/templates/pack/note.tex.j2 src/skysift/templates/pack/README.md.j2 src/skysift/templates/pack/exofop_note.txt.j2 src/skysift/templates/pack/archive_email.txt.j2 tools/dossier_compare.py tests/golden/kic-006146676-generated.json tests/unit/test_dossier.py tests/unit/test_store.py tests/unit/test_pack.py tests/unit/test_crosscheck.py tests/unit/test_zenodo.py
git commit -m "feat(pack): the prior claim compared in sigma, the TRICERATOPS FPP first, the sweep-wide false-alarm sentence, three gate rules

Live-shape words: <four counts>.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```


---

### Task 12: Pixels and red noise: aperture ratio, predicted leak, centroid order, model-shift margins, parity and depth trend (spec 11.5)

**Files:**
- Modify: `src/skysift/lc/pixels.py`: new `psf_fluxes` and `aperture_share`, after `gauss_psf` (line 555).
- Modify: `src/skysift/ephemeris.py`: new `depth_trend`, after `parity_depths` (line 480).
- Modify: `src/skysift/fpp.py`.
  - Lines 38 to 41: `FPP_MODEL_VERSION` becomes "3".
  - Lines 69 to 100: `FppInputs.exclusion_radius_arcsec`.
  - Lines 222 to 248: `_background`.
- Modify: `src/skysift/dossier.py`.
  - Lines 673 to 708: `centroid_offset` gains `nearest_beyond`, `direct_pull_pix` and `pulled`.
  - Lines 841 to 877: `fpp_inputs` passes the radius.
  - Lines 900 to 917: `floor_clause` names the pointing-jitter floor.
  - `ephemeris_blocks`: `aperture` and `depth_trend`.
  - New `predicted_leak` and `_kept_tpfs`. `generate` and `_assemble`.
- Modify: `src/skysift/ui/explain.py` (lines 269 to 284, `_stability`).
- Modify: `src/skysift/pack.py`.
  - Lines 412 to 433: `hand_built_sentence`. Lines 591 to 615: `parity_sentence`. Lines 631 to 651: `secondary_sentence`.
  - Lines 654 to 691: `_vetting_sentences`, the depth trend. Lines 746 to 784: `offset_sentence`. Lines 786 to 831: `pixel_sentence`.
  - Lines 844 to 859: `dilution_sentence`, the PSF photometry, the aperture ratio and the predicted leak.
  - Lines 1099 to 1135: `FILE_NOTES`. Lines 1971 to 2047: `_write_pack`, the hand-built file.
- Modify: `src/skysift/templates/pack/README.md.j2` (the "Pixel offset" paragraph, lines 62 to 65).
- Modify: `tools/dossier_compare.py` (`GOLDEN_KEYS`).
- Test: `tests/unit/test_pixels.py`, `tests/unit/test_ephemeris.py`, `tests/unit/test_fpp.py`, `tests/unit/test_dossier.py` (lines 599 to 603), `tests/unit/test_ui_explain.py`, `tests/unit/test_crosscheck.py` (it keeps synthetic pixel files, so the predicted-leak test lives there), and `tests/unit/test_pack.py`.
  - In `test_pack.py`: lines 587, 713, 732 and 1685, and 1834 to 1861, the `_check_live_shape` assertions on the hand-built and odd/even sentences.
- Test: the golden (regenerated).

**Interfaces:**
- Consumes:
  - Task 10: `ephemeris_blocks`' `fnorm_alt` and `fold`; `fold_depth`; `parity.stacked`.
  - Task 11: `exclusion_radius(block)`, `EXCLUSION_RULE`, `centroid_offset.exclusion_radius_arcsec`.
  - Rung 5 metrics: `seasons[*].psf_sigma_pix`, `shift_pix`, `pos_target`, `direct_centroid_colrow`, `voting`, `season`; `leak_depth_ppm` [value, err]; `neighbour_kic`.
  - The TPF: `TPF.aperture` (APERTURE extension, read by `lc.tpf.parse_tpf`, line 125), `OPTIMAL_APERTURE_BIT` (2), `TPF.cube`, `TPF.quality`, `TPF.pixel_of(ra, dec)`, `TPF.season`, `TPF.ra_obj`/`dec_obj`.
  - The kept files: `store.kept_artifacts("tpf", tpf_listing_url(kic))`, `blob.get(path)`, `parse_tpf(raw, name)`.
  - `lc.pixels.gauss_psf(shape, col, row, sig)`, the PSF model rung 5's two-source fit uses (`two_source_fit`, line 575: two circular Gaussians with a common width and a common shift).
  - Rung 2 metrics: `sigma_pos`, `sigma_ter`. Rung 3's `stability` block. `target.meta["crowdsap"]`.
- Produces:
```python
# lc/pixels.py
def psf_fluxes(image, valid, pos_target, pos_neighbour, psf_sigma_pix: float,
               shift_pix=(0.0, 0.0)) -> tuple[float, float] | None
    # the two stars' fluxes by linear least squares of image = F_t P_t + F_n P_n + c on the valid finite pixels, P the
    # gauss_psf of rung 5's width at each position plus the common shift; None when the system is singular
def aperture_share(mask, pos_target, pos_neighbour, psf_sigma_pix: float, shift_pix, flux_target: float,
                   flux_neighbour: float) -> float | None
    # F_t sum(P_t[mask]) / (F_t sum(P_t[mask]) + F_n sum(P_n[mask])): the target's share of the light in the mask
    # under the two-PSF model (other stars and the background left out); None for an empty mask or a zero total

# ephemeris.py
DEPTH_TREND_SIGMA = 2.0
def depth_trend(epochs, depths, errors, period: float) -> dict
    # weighted linear fit of the per-event depths against time (years from the weighted mean epoch):
    # {"slope_ppm_per_year", "err_ppm_per_year", "sigma", "chi2_red", "n", "printed": sigma > DEPTH_TREND_SIGMA}; the
    # error is scaled by sqrt(max(1, chi2_red)); None values below 3 usable events

# fpp.py
FPP_MODEL_VERSION = "3"     # 3: the BEB confusion radius is the pixel exclusion radius max(3 sigma, 0.5 arcsec)
class FppInputs: ...; exclusion_radius_arcsec: float | None = None     # last field, so stored inputs still load
# _background: r_c = inputs.exclusion_radius_arcsec when it is set, else the version 2 rule

# dossier.py
PULL_PIX = 0.3                         # spec 11.5: a direct centroid this far from the Gaia position is pulled
PSF_PHOTOMETRY = "KBonus PSF photometry, decontaminated to first order, with no crowding correction applied"
def predicted_leak(tpfs: list, target: dict, rung5_metrics: dict, depth_ppm: float | None) -> dict | None
    # None without a stored leak test; else {"predicted_ppm", "share", "quarters": [{"quarter", "season", "share"}],
    #  "measured_ppm", "measured_err_ppm", "kic", "model", "reason"}: per kept file the mask is
    #  (aperture & OPTIMAL_APERTURE_BIT) != 0, the image the median over quality-0 cadences, the positions
    #  tpf.pixel_of(target ra, dec) and of the neighbour (meta tpf_org_ra/dec, else ra_obj/dec_obj, as rung 5), the width
    #  and shift those of rung 5's season with the file's archive SEASON (the median over the seasons when it is
    #  absent), the fluxes from psf_fluxes; share the mean over the files, predicted_ppm = depth_ppm x share.
    #  Without a kept file: predicted_ppm None, reason f"no kept pixel file of KIC {kic}"; without a stored PSF
    #  width: reason "rung 5 stored no PSF width"
def _kept_tpfs(store, blob, kic: int | None) -> list      # parse_tpf of each kept file, unreadable ones skipped
# data["aperture"]: {"psf_depth_ppm", "psf_err_ppm", "sap_depth_ppm", "sap_err_ppm", "ratio", "ratio_err",
#                    "crowdsap", "shortfall_sigma", "ephemeris"} | None; ratio = sap / psf (fold_depth on fnorm_alt and
#                    fnorm), ratio_err = ratio * hypot(sap_err / sap, psf_err / psf), shortfall = (ratio - crowdsap) / ratio_err
# data["predicted_leak"], data["depth_trend"]
# data["centroid_offset"] gains "nearest_beyond": {"source_id", "sep_arcsec", "centroid_sep_arcsec", "gmag"} | None
#   (the fpp neighbour with the smallest centroid_sep_arcsec above the exclusion radius), "direct_pull_pix" (the
#   largest |direct_centroid_colrow - pos_target| over the voting seasons) and "pulled" (direct_pull_pix > PULL_PIX)

# pack.py
JITTER_FLOOR = "pointing-jitter floor"
def aperture_sentence(data: dict, tex: bool = False) -> str | None
def leak_sentence(data: dict, tex: bool = False) -> str | None
def trend_sentence(data: dict) -> str | None
```

- [ ] **Step 1: Write the failing pixel, ephemeris and FPP tests.** In `tests/unit/test_pixels.py`:

```python
from skysift.lc.pixels import aperture_share, gauss_psf, psf_fluxes


def test_psf_fluxes_recover_the_two_stars_of_a_noiseless_image():
    shape, pt, pn, sig, shift = (9, 9), (4.0, 4.0), (6.8, 4.6), 0.9, (0.1, -0.05)
    image = (1.0e6 * gauss_psf(shape, pt[0] + shift[0], pt[1] + shift[1], sig)
             + 5.0e6 * gauss_psf(shape, pn[0] + shift[0], pn[1] + shift[1], sig) + 2.0e3)
    ft, fn = psf_fluxes(image, np.ones(shape, bool), pt, pn, sig, shift)
    assert (ft, fn) == pytest.approx((1.0e6, 5.0e6), rel=1e-6)


def test_aperture_share_is_the_targets_psf_light_in_the_mask():
    shape, pt, pn, sig, shift = (9, 9), (4.0, 4.0), (6.8, 4.6), 0.9, (0.1, -0.05)
    mask = np.zeros(shape, bool)
    mask[3:6, 6:9] = True                                     # the neighbour's 3 x 3 optimal aperture
    t = gauss_psf(shape, pt[0] + 0.1, pt[1] - 0.05, sig)[mask].sum()
    n = gauss_psf(shape, pn[0] + 0.1, pn[1] - 0.05, sig)[mask].sum()
    assert aperture_share(mask, pt, pn, sig, shift, 1.0e6, 5.0e6) == pytest.approx(1.0e6 * t / (1.0e6 * t + 5.0e6 * n))
    assert aperture_share(np.zeros(shape, bool), pt, pn, sig, shift, 1.0e6, 5.0e6) is None
```

In `tests/unit/test_ephemeris.py`:

```python
from skysift.ephemeris import DEPTH_TREND_SIGMA, depth_trend


def test_a_depth_trend_is_measured_with_scaled_errors_and_printed_above_two_sigma():
    epochs = np.arange(40)
    years = epochs * 40.0 / 365.25
    trend = depth_trend(epochs, 2000.0 + 200.0 * (years - years.mean()), np.full(40, 50.0), 40.0)
    assert trend["slope_ppm_per_year"] == pytest.approx(200.0) and trend["printed"] is True
    assert trend["sigma"] == pytest.approx(200.0 / trend["err_ppm_per_year"]) and trend["n"] == 40
    flat = depth_trend(epochs, 2000.0 + np.random.default_rng(1).normal(0, 50.0, 40), np.full(40, 50.0), 40.0)
    assert flat["printed"] is (flat["sigma"] > DEPTH_TREND_SIGMA) and flat["sigma"] < 3
    assert depth_trend(epochs[:2], np.array([1.0, 2.0]), np.array([1.0, 1.0]), 40.0)["sigma"] is None
```

In `tests/unit/test_fpp.py`:

```python
def test_the_beb_confusion_radius_is_the_pixel_exclusion_radius():
    near = (Neighbour("near", 1.5, 19.0, None, centroid_sep_arcsec=1.5),)       # bright enough, 1.5" from the centroid
    old = compute(base(centroid_sigma_arcsec=0.25312484, neighbours=near))
    new = compute(base(centroid_sigma_arcsec=0.25312484, neighbours=near, exclusion_radius_arcsec=0.75937))
    beb_old, beb_new = (next(s for s in f.scenarios if s.name == "BEB") for f in (old, new))
    assert beb_old.factors["r_c_arcsec"] == 2.0 and beb_old.factors["X_mean"] == 1.0         # inside 2" nothing is excluded
    assert beb_new.factors["r_c_arcsec"] == pytest.approx(0.75937)
    assert beb_new.factors["X_mean"] == pytest.approx(math.exp(-((1.5 - 0.75937) / 0.25312484) ** 2 / 2))
    assert FPP_MODEL_VERSION == "3"
```

(`base` is the module's KIC 6146676 builder at line 10; `FPP_MODEL_VERSION` joins its import from `skysift.fpp`.)

- [ ] **Step 2: Write the failing dossier, explain and pack tests.** In `tests/unit/test_crosscheck.py` (its `env` keeps five synthetic pixel files of KIC 6146666):

```python
from skysift import dossier as ds
from skysift.lc.tpf import parse_tpf
from tests.unit.synthetic_tpf import TARGET_RADEC
from tests.unit.test_pixels import _psf


def test_the_predicted_leak_is_the_targets_share_of_the_neighbours_aperture_times_the_depth():
    tpfs = [parse_tpf(bytes(b), tpf_name(NEIGHBOUR_KIC, b.quarter)) for b in tpf_quarters()]
    rung5 = {"leak_depth_ppm": [14.0, 31.0], "neighbour_kic": NEIGHBOUR_KIC,
             "seasons": [{"season": s, "psf_sigma_pix": 0.9, "shift_pix": [0.0, 0.0]} for s in range(4)]}
    target = {"ra": TARGET_RADEC[0], "dec": TARGET_RADEC[1], "meta": {}}
    block = ds.predicted_leak(tpfs, target, rung5, 2200.0)
    expected = []
    for b, t in zip(tpf_quarters(), tpfs, strict=True):
        mask = (t.aperture & 2) != 0
        pt, pn = _psf(b.pos_target, t.cube.shape[1:])[mask].sum(), _psf(b.pos_neighbour, t.cube.shape[1:])[mask].sum()
        expected.append(pt / (pt + 5.0 * pn))                   # the scene's neighbour is five times the target
    assert block["share"] == pytest.approx(np.mean(expected), rel=0.05)
    assert block["predicted_ppm"] == pytest.approx(2200.0 * block["share"])
    assert (block["measured_ppm"], block["measured_err_ppm"], block["kic"]) == (14.0, 31.0, NEIGHBOUR_KIC)
    assert [q["quarter"] for q in block["quarters"]] == [1, 2, 3, 4, 5] and block["reason"] is None
    assert ds.predicted_leak([], target, rung5, 2200.0)["reason"] == f"no kept pixel file of KIC {NEIGHBOUR_KIC}"
    assert ds.predicted_leak(tpfs, target, {**rung5, "leak_depth_ppm": None}, 2200.0) is None
```

In `tests/unit/test_dossier.py`:

```python
def test_the_aperture_block_compares_the_sap_depth_with_crowdsap():
    block = dossier_module.aperture_block({"depth_ppm": 2031.0, "err_ppm": 113.0},
                                          {"depth_ppm": 1289.0, "err_ppm": 109.0}, 0.74724716, "linear fit")
    ratio = 1289.0 / 2031.0
    err = ratio * math.hypot(109.0 / 1289.0, 113.0 / 2031.0)
    assert (block["ratio"], block["ratio_err"]) == pytest.approx((ratio, err))
    assert block["shortfall_sigma"] == pytest.approx((ratio - 0.74724716) / err)        # -1.75
    assert dossier_module.aperture_block({"depth_ppm": 2031.0, "err_ppm": 113.0},
                                         {"depth_ppm": None, "err_ppm": None}, 0.75, "search") is None


def test_the_centroid_block_names_the_nearest_source_beyond_the_radius_and_the_pull(env):
    d = json.loads(generate(env.store, env.blob, env.settings, STAGE2_TARGET, fetch=env.fetch,
                            force=True).data_path.read_text(encoding="utf-8"))
    co, inputs = d["centroid_offset"], d["fpp"]["inputs"]
    beyond = [n for n in inputs["neighbours"] if n["centroid_sep_arcsec"] > co["exclusion_radius_arcsec"]]
    nearest = min(beyond, key=lambda n: n["centroid_sep_arcsec"])
    assert co["nearest_beyond"]["source_id"] == nearest["source_id"]
    assert inputs["exclusion_radius_arcsec"] == co["exclusion_radius_arcsec"]
    assert co["pulled"] is (co["direct_pull_pix"] > 0.3)
```

`aperture_block(psf: dict, sap: dict, crowdsap: float | None, ephemeris: str) -> dict | None` is the pure part of `data["aperture"]`. In `tests/unit/test_ui_explain.py`:

```python
def test_the_stability_line_gives_the_aperture_ratio_not_agreement():
    line = explain.why([{"rung": "rung3_stability", "status": "pass", "reason": None, "created": None,
                         "metrics": {"season_depths": {"0": 2295.0, "1": 1962.0, "2": 1864.0, "3": 1912.0},
                                     "season_errs": {}, "p_value": 0.32, "chi2": 3.5, "dof": 3,
                                     "aperture_ratio": 0.63, "aperture_z": -4.7}}])[0]
    assert "the SAP depth is 0.63 of the PSF depth" in line.sentence and "apertures agree" not in line.sentence
```

In `tests/unit/test_pack.py`:

```python
def test_the_centroid_paragraph_leads_with_the_gaia_offset_and_the_exclusion_radius(env_with_dossier):
    data = _stored(env_with_dossier)
    co = data["centroid_offset"]
    text = pack_module.offset_sentence(data)
    gaia = text.index("Against the Gaia position")
    assert gaia == 0 and text.index("Against the star's own out-of-transit image") > gaia
    assert f"an exclusion radius of {co['exclusion_radius_arcsec']:.2f} arcsec (max(3 sigma, 0.5 arcsec))" in text
    assert "within which a background source is not excluded" in text
    if co["nearest_beyond"]:
        assert f"Gaia DR3 {co['nearest_beyond']['source_id']}" in text
    assert ("its reference is pulled by the neighbour" in text) is co["pulled"]
    assert "pointing-jitter floor" in pack_module.pixel_sentence(data) and "systematic floor" not in (
        pack_module.pixel_sentence(data))


def test_the_red_noise_parity_and_trend_sentences():
    data = {"tests": [{"rung": "rung2_shape", "status": "pass",
                       "metrics": {"sigma_pos": 7.98, "sigma_ter": 3.10, "fit_ok": True}}],
            "secondary": {"depth_ppm": 95.0, "err_ppm": 128.0, "limit_3sigma_ppm": 480.0, "strongest_ppm": 573.0,
                          "strongest_phase": 0.726, "strongest_sigma": 3.98},
            "parity": {"odd": {"depth_ppm": 2031.0, "err_ppm": 178.0, "n": 28},
                       "even": {"depth_ppm": 2031.0, "err_ppm": 138.0, "n": 27}, "difference_sigma": 0.002,
                       "stacked": {"odd": {"depth_ppm": 2050.0, "err_ppm": 120.0},
                                   "even": {"depth_ppm": 2010.0, "err_ppm": 115.0}, "difference_sigma": 0.24}},
            "depth_trend": {"slope_ppm_per_year": -61.0, "err_ppm_per_year": 25.0, "sigma": 2.44, "printed": True}}
    assert pack_module.secondary_sentence(data).endswith(
        "the strongest dip elsewhere is 573 ppm at phase 0.73 (4.0 sigma), against model-shift margins of 8.0 sigma "
        "for the largest positive excursion and 3.1 sigma for the tertiary dip.")
    assert pack_module.parity_sentence(data) == (
        "The odd and even depths are 2,030 ± 180 and 2,030 ± 140 ppm per event (0.0 sigma apart) and 2,050 ± 120 and "
        "2,010 ± 120 ppm on the stacked folds (0.2 sigma apart).")
    assert pack_module.trend_sentence(data) == (
        "The per-event depths drift by -61 ± 25 ppm per year (2.4 sigma, errors scaled by the scatter).")
    assert pack_module.trend_sentence({**data, "depth_trend": {**data["depth_trend"], "printed": False}}) is None


def test_the_hand_built_comparison_leaves_the_note_for_the_readme_and_its_file(env_prior):
    rep = _build(env_prior)
    texts = _texts(rep)
    assert "hand-built localisation" not in texts["note/note.tex"]
    assert ("The centroid agrees with the hand-built localisation within 0.03 px over 4 seasons "
            "(data/hand_built_localisation.json)") in texts["README.md"]
    copied = rep.out_dir / "data" / "hand_built_localisation.json"
    source = dossier_module.CANDIDATES_DIR / STAGE2_TARGET / "localisation" / "results.json"
    assert copied.read_bytes() == source.read_bytes()


def test_the_note_names_the_photometry_the_aperture_ratio_and_the_predicted_leak(env_with_dossier):
    data = {**_stored(env_with_dossier),
            "aperture": {"ratio": 0.63466, "ratio_err": 0.064244, "crowdsap": 0.74724716, "shortfall_sigma": -1.7525},
            "predicted_leak": {"predicted_ppm": 61.2, "share": 0.0301, "measured_ppm": 130.4,
                               "measured_err_ppm": 41.8, "kic": 5689116, "reason": None}}
    flat = " ".join(render_note(data).split())
    assert "KBonus PSF photometry, decontaminated to first order, with no crowding correction applied" in flat
    assert pack_module.aperture_sentence(data) == (
        "The SAP depth is 0.635 ± 0.064 of the PSF depth against a CROWDSAP of 0.75, 1.8 sigma short of it.")
    assert pack_module.leak_sentence(data) == (
        "KIC 5689116's optimal aperture dims by 130 ± 42 ppm, where this star's light alone (3.0 percent of that "
        "aperture under the PSF model of the pixel check) predicts 61 ppm.")
```

The lines that pin the old wording change: test_pack.py lines 587 and 713 ("measured systematic floor" becomes "pointing-jitter floor"), lines 732 and 1852 (the hand-built sentence leaves the note), and line 1685 (the source becomes `data/hand_built_localisation.json`). So do test_dossier.py lines 599 to 603 (the Monte Carlo caveat's floor clause).

- [ ] **Step 3: Run them to see them fail.**
  - Command: `PYTHONUTF8=1 PYTHONPATH=src C:\Research\skysift\.venv\Scripts\python.exe -m pytest tests/unit/test_pixels.py tests/unit/test_ephemeris.py tests/unit/test_fpp.py tests/unit/test_crosscheck.py tests/unit/test_dossier.py tests/unit/test_ui_explain.py tests/unit/test_pack.py -q -p no:cacheprovider`.
  - Expected: collection stops at `ImportError: cannot import name 'aperture_share' from 'skysift.lc.pixels'` and `cannot import name 'depth_trend' from 'skysift.ephemeris'`. The FPP test fails on `TypeError: FppInputs.__init__() got an unexpected keyword argument 'exclusion_radius_arcsec'`. The explain test fails on "apertures agree".

- [ ] **Step 4: Implement the numerics.**
  - `lc/pixels.py`: `psf_fluxes` (`np.linalg.lstsq` on the columns [P_t, P_n, 1] of the valid finite pixels) and `aperture_share`.
  - `ephemeris.py`: `DEPTH_TREND_SIGMA` and `depth_trend` (`np.polyfit` with weights 1/err, or the closed-form weighted least squares; time in years = epoch x period / 365.25, centred on the weighted mean).
  - `fpp.py`: `exclusion_radius_arcsec` and its use in `_background`, and `FPP_MODEL_VERSION = "3"` (part of the dossier fingerprint, so every stored dossier regenerates).
  - The 2.0 arcsec `CONFUSION_FLOOR_ARCSEC` of the sign-off (spec 4.2) gives way to spec 11.5's 0.5 arcsec floor whenever the pixel rung voted. The README's false-positive paragraph says so in one sentence: "The background-binary term uses the pixel exclusion radius, max(3 sigma, 0.5 arcsec), as its confusion radius (spec 11.5; 2.0 arcsec before)."

- [ ] **Step 5: Implement the dossier blocks.**
  - `ephemeris_blocks` gains:
    - `aperture = aperture_block(fold_depth(fnorm), fold_depth(fnorm_alt), target.meta.get("crowdsap"), fold["source"])`, None without `fnorm_alt`;
    - `depth_trend` of the fold's `event_depths` (its `epochs`, `depths`, `errors`).
  - `_kept_tpfs` reads the files as `crosscheck._copy_pixel_files` does, from `store.kept_artifacts("tpf", tpf_listing_url(kic))` and `parse_tpf(blob.get(path), name)`, skipping one that does not parse.
  - `predicted_leak` as in the Interfaces.
  - `generate` calls `predicted_leak(_kept_tpfs(store, blob, kic), target, rung5, data["ephemeris"]["depth_ppm"])`, with kic = `meta.tpf_org` or `rung5.neighbour_kic`. It reads only kept files and fetches nothing.
  - `centroid_offset` gains `direct_pull_pix` and `pulled`. `generate` adds `nearest_beyond` once `fpp_inputs` has the neighbours' centroid separations.
  - `fpp_inputs` sets `exclusion_radius_arcsec=exclusion_radius({"catalogue_referenced_err_arcsec": centroid.sigma})` when the centroid votes.
  - `floor_clause` says "with a pointing-jitter floor of F px per axis added in quadrature".
  - `explain._stability` replaces "the two apertures agree (ratio R)" with "the SAP depth is R of the PSF depth" plus ", Z sigma apart" when `aperture_z` is stored.
  - `GOLDEN_KEYS` gains `"aperture"`, `"predicted_leak"` and `"depth_trend"`.

- [ ] **Step 6: Implement the texts.**
  - `offset_sentence(short=True)`, the note's form, in this order:
    1. "Against the Gaia position, which carries the WCS zero point and registration, the difference-image centroid sits <cat ± err> arcsec away, with an exclusion radius of <r> arcsec (max(3 sigma, 0.5 arcsec)) within which a background source is not excluded".
    2. "; the nearest Gaia source beyond it is Gaia DR3 <id> at <s> arcsec (G = <g>)" when `nearest_beyond` is set.
    3. ". Against the star's own out-of-transit image the offset is <off ± err> arcsec (<sigma> sigma), <verdict> by the rule of \citet{bryson2013} (over 3 sigma and 0.2 arcsec)".
    4. "; its reference is pulled by the neighbour, up to <pull> px from the Gaia position" when `pulled`.
    5. ". " and `SAME_ERROR_SHORT`.
  - The long form (README, ExoFOP note) keeps the same order with the long connectives.
  - `pixel_sentence` uses `JITTER_FLOOR` ("its error carrying a pointing-jitter floor of F px") and drops `hand_built_sentence`.
  - The README's "Pixel offset" paragraph prints `hand_built_sentence(data, source="data/hand_built_localisation.json")`. The source keyword defaults to the block's `source`.
  - `_write_pack` copies `ds.CANDIDATES_DIR / target_id / "localisation" / "results.json"` to `data/hand_built_localisation.json` when it exists. `FILE_NOTES` gains "the hand-built localisation study of the same pixel files that the README compares with (season by season, its centroid's distance from the star)".
  - `secondary_sentence` appends ", against model-shift margins of P sigma for the largest positive excursion and T sigma for the tertiary dip" from rung 2's `sigma_pos` and `sigma_ter`, when both are stored.
  - `parity_sentence` gives the per-event pair, "per event (D sigma apart)", then the stacked pair, "on the stacked folds (S sigma apart)", when `parity.stacked` exists. The clause on "the vetting rung's own comparison" goes.
  - `trend_sentence` (in `_vetting_sentences`, after the rung 3 sentence) prints only when `printed` is true.
  - `dilution_sentence` (now printed for every KBonus target, so never None for one) opens with "The depth is " + `PSF_PHOTOMETRY` + ". " then `aperture_sentence` ("... 1.8 sigma short of it." or "above it"), then the CROWDSAP and PSFFRAC clause as now, then `leak_sentence` in place of the bare leak number. Without a prediction the measured dip keeps its present clause.
  - The `_check_live_shape` assertion on the hand-built sentence (test_pack.py line 1852) becomes `"hand-built" not in flat`. Its parity and secondary assertions already follow the golden (Task 10 Step 7).

- [ ] **Step 7: Regenerate the golden** with Task 10's helper. Then run the tests (all pass): `PYTHONUTF8=1 PYTHONPATH=src C:\Research\skysift\.venv\Scripts\python.exe -m pytest tests/unit/test_pixels.py tests/unit/test_ephemeris.py tests/unit/test_fpp.py tests/unit/test_crosscheck.py tests/unit/test_dossier.py tests/unit/test_ui_explain.py tests/unit/test_ui_dossiers.py tests/unit/test_pack.py tests/unit/test_rung5_pixels.py -q -p no:cacheprovider`, then `PYTHONUTF8=1 PYTHONPATH=src C:\Research\skysift\.venv\Scripts\python.exe -m ruff check src tests tools`, then record the four live-shape counts (`-k live_shape -vv`).

- [ ] **Step 8: Commit.**

```
git add src/skysift/lc/pixels.py src/skysift/ephemeris.py src/skysift/fpp.py src/skysift/dossier.py src/skysift/ui/explain.py src/skysift/pack.py src/skysift/templates/pack/README.md.j2 tools/dossier_compare.py tests/golden/kic-006146676-generated.json tests/unit/test_pixels.py tests/unit/test_ephemeris.py tests/unit/test_fpp.py tests/unit/test_crosscheck.py tests/unit/test_dossier.py tests/unit/test_ui_explain.py tests/unit/test_pack.py
git commit -m "feat(pixels): aperture ratio against CROWDSAP, predicted leak, Gaia-first centroid with the exclusion radius in the BEB term, red-noise margins, stacked parity, depth trend

Live-shape words: <four counts>.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```


---

### Task 13: Star, citations, author config, final placeholders and the minor sweep (spec 11.6, pack version 4)

**Files:**
- Create: `docs/author.json`. It holds `{"name": "Vikram Pandya", "affiliation": "", "email": "", "orcid": ""}`, the name from `CITATION.cff`. Vikram fills in the other three; nobody types them for him.
- Modify: `src/skysift/stars.py`.
  - Lines 75 to 79: `Q_SOURCE` gains `g.non_single_star, g.ebpminrp_gspphot`.
  - Lines 90 to 113: `GaiaRow`, two fields with defaults at the end. Lines 128 to 142: `StarParams`, three fields with defaults at the end.
  - Lines 282 to 315: `query_gaia`. Lines 407 to 528: `derive` copies them.
- Modify: `src/skysift/dossier.py`. Lines 1033 to 1147, `caveats`: the reddening in the "No spectrum" title, and "quarter group".
- Modify: `src/skysift/pack_rows.py`. Lines 209 to 238: `provenance_rows` gains RUWE, the NSS flag and E(BP-RP).
- Modify: `src/skysift/pack.py`, part 1.
  - Line 103: `PACK_VERSION`. Lines 117 to 128: the placeholders and `CODE_SENTENCE`.
  - Lines 222 to 255: `_count` and `note_words`.
  - Lines 280 to 343: `references` and `BIBLIOGRAPHY`.
  - Lines 654 to 691: `_vetting_sentences`, the SES sentence and "quarter group".
- Modify: `src/skysift/pack.py`, part 2.
  - Lines 880 to 886: `software_line`. Lines 889 to 967: `numbers`. Lines 980 to 1022: `abstract_text`.
  - Lines 1055 to 1088: `_authors` and `render_note`.
  - Lines 1589 to 1663: `_png_figures`.
  - Lines 1709 to 1735: `load_citation`, which also reads the DOI. Lines 1871 to 1969: `build_pack`, with `final`. Lines 2049 to 2063: `_versions`, which records the software versions.
- Modify: `src/skysift/pack_text.py` (lines 205 to 213, `ephemeris_sentence` at the central epoch).
- Modify: `src/skysift/exofop.py` (lines 90 to 117, the central epoch).
- Modify: `src/skysift/tess.py` (lines 97 to 103 `VERDICT_PHRASES`, lines 462 to 485 `_plain_sentence`).
- Modify: `src/skysift/cli.py` (lines 309 to 372, `pack_command` gains `--final`).
- Modify: `src/skysift/templates/pack/note.tex.j2` (lines 11 and 14, the author block; lines 24, 41 and 45 to 47, the citations, the star sentence and the habitable-zone lines; line 58, the follow-up clause; line 62, the caption).
- Modify: `src/skysift/templates/pack/README.md.j2` (the star paragraph, the habitable zone, the references, the follow-up window).
- Test: `tests/unit/test_stars.py` (lines 285 to 292, `GAIA_SOURCE_COLUMNS`), `tests/unit/test_dossier.py`, `tests/unit/test_pack.py`, `tests/unit/test_tess.py`, `tests/unit/test_zenodo.py`, `tests/unit/test_cli.py`, and the golden (regenerated).

**Interfaces:**
- Consumes:
  - `GaiaRow`, `StarParams.gaia_cross_check["radius_flame"]`, `StarParams.alt_radius_e02`, `PlanetParams` (the habitable-zone edges).
  - `pack_rows.next_transits(data, after_bjd, n) -> [{"epoch", "t_bjd", "window_min", "date", ...}]`.
  - `ZenodoClient.create_deposition` and `reserved_doi`. The reservation is already there: `create_deposition` POSTs `{"metadata": {"prereserve_doi": True}}` (`zenodo.py` line 73), and `test_zenodo.py` line 88 and `test_pack.py` line 779 pin it. This task adds only the `--final` check after it.
  - `importlib.metadata.version`.
- Produces:
```python
# stars.py
GaiaRow: ...; non_single_star: int | None = None; ebpminrp_gspphot: float | None = None
StarParams: ...; ruwe: float | None = None; non_single_star: int | None = None; ebpminrp_gspphot: float | None = None
# pack.py
PACK_VERSION = "4"      # 4: round 2 (spec 11): the vetting texts, the limb-darkened shape, author.json, --final
AUTHOR_FILE = Path(__file__).resolve().parents[2] / "docs" / "author.json"
AUTHOR_FIELDS = ("name", "affiliation", "email", "orcid")
FINAL_PLACEHOLDERS = ("XXXXXXX", "to be minted", "TO FILL IN", SOFTWARE_DOI_PLACEHOLDER)
FINAL_TEXTS = ("note/note.tex", "README.md", "exofop/note.txt", "archive_email.txt", "zenodo_metadata.json")
SOFTWARE_PACKAGES = ("numpy", "scipy", "astropy", "matplotlib")
FOLLOWUP_REACH = ", within reach of a 1 to 2 m telescope"
FOLLOWUP_REACH_DEPTH_PPM, FOLLOWUP_REACH_GMAG = 1000.0, 17.0
def load_author(path: Path = AUTHOR_FILE) -> dict | None   # the four fields as stripped strings; None without the file
def author_problems(author: dict | None) -> list[str]
    # "no docs/author.json"; "docs/author.json has no <field>" per empty field; "docs/author.json has the ORCID
    # <value>, not 0000-0000-0000-000X" for a malformed ORCID
def final_problems(out_dir: Path) -> list[str]     # f"{name} still says {placeholder!r}" per text and placeholder
def software_versions() -> dict[str, str | None]    # {package: version | None}, written into provenance/versions.json
def software_line(data: dict, versions: dict | None = None) -> str
    # "SkySift <v> (search version <s>, commit <c>), numpy <v>, scipy <v>, astropy <v>, matplotlib <v>[, triceratops]"
def followup_text(data: dict, after_bjd: float) -> str | None
def build_pack(..., zenodo: ZenodoClient | None = None, final: bool = False, log=None) -> PackReport
# load_citation(): the citation dict also carries "doi" from CITATION.cff; CODE_SENTENCE becomes code_sentence(citation):
# "the SkySift code at the stated commit is archived at doi:<doi>", or the placeholder with "(to be minted)"
# cli.py: skysift pack ... --final (needs --zenodo-live; exit 2 otherwise)
```

- [ ] **Step 1: Write the failing star, citation, author and placeholder tests.** In `tests/unit/test_stars.py`, `GAIA_SOURCE_COLUMNS` gains `"non_single_star"` and `"ebpminrp_gspphot"`. In `tests/unit/test_pack.py`:

```python
def _star_extras(data, **star):
    return {**data, "star": {**data["star"], "ruwe": 0.9449, "non_single_star": 0, "ebpminrp_gspphot": None, **star}}


def test_the_star_sentence_and_the_provenance_carry_ruwe_nss_and_the_reddening(env_with_dossier):
    data = _star_extras(_stored(env_with_dossier))
    flat = " ".join(render_note(data).split())
    assert "a RUWE of 0.94 and no non-single-star solution" in flat
    assert "the reddening is not measured" in flat                     # the "not yet known" titles name it
    rows = {r["parameter"]: r for r in pack_rows.provenance_rows(data)}
    assert rows["RUWE"]["value"] == 0.9449 and rows["Gaia non-single-star flag"]["value"] == 0
    red = _star_extras(data, ebpminrp_gspphot=0.12)
    assert "GSP-Phot E(BP-RP) of 0.12" in " ".join(render_note(red).split())
    assert "the reddening is not measured" not in " ".join(render_note(red).split())


def test_the_readme_gives_the_flame_radius_as_a_systematic_and_keeps_the_habitable_zone(env_with_dossier):
    rep = _build(env_with_dossier)
    readme, note = _texts(rep)["README.md"], _texts(rep)["note/note.tex"]
    flame = _stored(env_with_dossier)["star"]["gaia_cross_check"]["radius_flame"]
    assert f"The Gaia FLAME radius of {flame:.3f} Rsun" in readme and "a systematic on the stellar radius" in readme
    assert "kopparapu2013" not in note and "habitable" not in note.lower()
    assert "sub-Neptune" in readme and "Rogers 2015" in readme and "Fulton et al. 2017" in readme


def test_the_note_cites_gaia_dr3_and_kepler_and_its_software_line_is_not_counted(env_with_dossier):
    tex = render_note(_stored(env_with_dossier))
    flat = " ".join(tex.split())
    for key in ("gaia2016", "gaia2023", "borucki2010"):
        assert f"]{{{key}}}" in flat, key
    assert r"\citep{gaia2016,gaia2023}" in flat and r"\citep{borucki2010}" in flat
    versions = pack_module.software_versions()
    assert f"numpy {versions['numpy']}" in flat and f"scipy {versions['scipy']}" in flat
    software = re.search(r"\\software\{[^}]*\}", tex).group(0)
    assert note_words(tex) == note_words(tex.replace(software, r"\software{}"))
    readme = _texts(_build(env_with_dossier))["README.md"]
    for ref in ("Skrutskie", "Thompson", "Kirk", "Stassun"):            # 2MASS, the KOI and EB catalogues, the TIC
        assert ref in readme, ref


def test_the_author_block_comes_from_docs_author_json(env_with_dossier, tmp_path, monkeypatch):
    path = tmp_path / "author.json"
    path.write_text(json.dumps({"name": "A. Person", "affiliation": "Somewhere", "email": "a@example.org",
                                "orcid": "0000-0002-1825-0097"}), encoding="utf-8")
    monkeypatch.setattr(pack_module, "AUTHOR_FILE", path)
    tex = render_note(_stored(env_with_dossier), citation=None)
    assert r"\author[0000-0002-1825-0097]{A. Person}" in tex and r"\affiliation{Somewhere}" in tex
    assert r"\email{a@example.org}" in tex and "TO FILL IN" not in tex
    assert pack_module.author_problems(pack_module.load_author(path)) == []
    path.write_text(json.dumps({"name": "A. Person", "affiliation": "", "email": "", "orcid": "12"}),
                    encoding="utf-8")
    assert pack_module.author_problems(pack_module.load_author(path)) == [
        "docs/author.json has no affiliation", "docs/author.json has no email",
        "docs/author.json has the ORCID '12', not 0000-0000-0000-000X"]
    assert pack_module.author_problems(None) == ["no docs/author.json"]


def test_final_refuses_without_the_live_draft_and_with_any_placeholder(env_with_dossier, fake_session, tmp_path,
                                                                     monkeypatch):
    from skysift import fpp
    monkeypatch.setattr(fpp, "SIGNED_OFF", True)
    env = env_with_dossier
    _approve(env)
    _store_errors(env)
    _store_cross_check(env)
    with pytest.raises(PackError, match="--final needs --zenodo-live"):
        _build(env, final=True)
    path = tmp_path / "author.json"
    path.write_text(json.dumps({"name": "A. Person", "affiliation": "", "email": "a@example.org",
                                "orcid": "0000-0002-1825-0097"}), encoding="utf-8")
    monkeypatch.setattr(pack_module, "AUTHOR_FILE", path)
    live = ZenodoClient("t", sandbox=False, session=fake_session)
    with pytest.raises(PackError, match="docs/author.json has no affiliation"):
        _build(env, zenodo=live, final=True)
    assert fake_session.calls == []                                  # refused before anything is written
    path.write_text(json.dumps({"name": "A. Person", "affiliation": "Somewhere", "email": "a@example.org",
                                "orcid": "0000-0002-1825-0097"}), encoding="utf-8")
    with pytest.raises(ZenodoError, match=r"still says '10\.5281/zenodo\.SOFTWARE'.*unfinished"):
        _build(env, zenodo=live, final=True)                         # CITATION.cff carries no software DOI yet
    assert [c[0] for c in fake_session.calls] == ["POST"]            # reserved, never filled or uploaded
    monkeypatch.setattr(pack_module, "load_citation",
                        lambda path=None: ({"authors": [{"name": "A. Person"}], "doi": "10.5281/zenodo.777"}, "test"))
    fake_session.calls.clear()
    rep = _build(env, zenodo=live, final=True)
    assert pack_module.final_problems(rep.out_dir) == []
    tex = (rep.out_dir / "note" / "note.tex").read_text(encoding="utf-8")
    assert f"10.5281/zenodo.{DEPOSITION_ID}" in tex and "doi:10.5281/zenodo.777" in tex
    assert [c[0] for c in fake_session.calls] == ["POST", "PUT", "PUT"]
```

In `tests/unit/test_cli.py`, `--final` without `--zenodo-live` exits 2 with "--final needs --zenodo-live".

- [ ] **Step 2: Run them to see them fail.**
  - Command: `PYTHONUTF8=1 PYTHONPATH=src C:\Research\skysift\.venv\Scripts\python.exe -m pytest tests/unit/test_stars.py tests/unit/test_pack.py tests/unit/test_cli.py -q -p no:cacheprovider`.
  - Expected: the column test fails on the two missing columns; `AttributeError: module 'skysift.pack' has no attribute 'software_versions'` (and `'author_problems'`); `TypeError: build_pack() got an unexpected keyword argument 'final'`; "a RUWE of 0.94" is not in the note.

- [ ] **Step 3: The star sentence, the provenance and the reddening.**
  - `Q_SOURCE`, `GaiaRow`, `query_gaia` (`_int` for `non_single_star`, `_num` for `ebpminrp_gspphot`), and `StarParams` with `derive` copying `ruwe`, `non_single_star` and `ebpminrp_gspphot`.
  - The note's star sentence (template line 41) gains "Gaia gives a RUWE of R and no non-single-star solution", or "a non-single-star solution (flag N)" when the flag is above 0. It also gains ", and a GSP-Phot E(BP-RP) of E" when present.
  - `provenance_rows` gains three rows: "RUWE", "Gaia non-single-star flag" and "GSP-Phot E(BP-RP)", each with method "Gaia DR3 gaia_source" and reference "Gaia Collaboration 2023".
  - Without E(BP-RP), `dossier.caveats` titles the stellar-radius caveat "No spectrum: the planet radius depends on the stellar radius from Gaia colour and parallax, and the reddening is not measured". The README's "What is not yet known" entry keeps its text with the E(BP-RP) = 0.2 radius, which is the sensitivity sentence.
  - The README's star paragraph gains: "The Gaia FLAME radius of F Rsun differs from the adopted radius by P percent; read it as a systematic on the stellar radius, and so on the planet radius." It reads `gaia_cross_check["radius_flame"]` and is left out without it.

- [ ] **Step 4: The habitable zone moves to the README.** Delete template lines 45 to 47, so `references()` no longer cites `kopparapu2013` in the note. The README prints, under the numbers table: "Habitable zone: the orbit lies <hz_where> of Kopparapu et al. (2013), whose conservative edges for this star sit at <inner> and <outer> AU. A planet of <rp> Earth radii is more likely a sub-Neptune with a gaseous envelope than a rocky world (Rogers 2015; Fulton et al. 2017)." The README's references gain:
  - "Rogers, L. A. 2015, ApJ, 801, 41";
  - "Fulton, B. J., Petigura, E. A., Howard, A. W., et al. 2017, AJ, 154, 109".

- [ ] **Step 5: The citations, the software line and the word counter.**
  - `BIBLIOGRAPHY` gains three entries:
    - `gaia2016`: `Gaia Collaboration, Prusti, T., de Bruijne, J.~H.~J., et~al. 2016, A\&A, 595, A1`;
    - `gaia2023`: `Gaia Collaboration, Vallenari, A., Brown, A.~G.~A., et~al. 2023, A\&A, 674, A1`;
    - `borucki2010`: `Borucki, W.~J., Koch, D., Basri, G., et~al. 2010, Science, 327, 977`.
  - `references` sets all three True. Template line 24 reads "Kepler \citep{borucki2010} recorded the star only in the background of other targets' pixels"; line 41 reads "the parameters come from Gaia DR3 \citep{gaia2016,gaia2023} colour and parallax".
  - `software_versions()` and `software_line(data, versions)`. `_versions` writes `"software": software_versions()` into `provenance/versions.json`, so the line and the provenance print one set.
  - `note_words` strips `re.compile(r"\\software\{[^}]*\}")` before `_count` (AAS counts text, not the software tag; spec 11.6).
  - The README gains a "References" section with the note's bibliography in plain text, plus:
    - Skrutskie, M. F., et al. 2006, AJ, 131, 1163 (2MASS);
    - Thompson, S. E., et al. 2018, ApJS, 235, 38 (the KOI cumulative table and the DR25 TCE list);
    - Guerrero et al. 2021 (the TOI list);
    - Kirk, B., et al. 2016, AJ, 151, 68 (the Kepler eclipsing binary catalogue);
    - Stassun et al. 2019 (the TIC).

- [ ] **Step 6: The author config, `--final` and the reserved DOI.**
  - `load_author`, `author_problems`, and `render_note` taking its authors from `load_author()` when the file exists, else from the citation as now. The placeholders "AFFILIATION TO FILL IN" and "EMAIL TO FILL IN" stay only for an empty field.
  - `load_citation` keeps `doi` from `CITATION.cff`, and `code_sentence(citation)` replaces `CODE_SENTENCE`.
  - `build_pack(final=True)`, in this order:
    1. `PackError("--final needs --zenodo-live: the record's DOI is reserved on zenodo.org")` unless `zenodo` is a live client.
    2. `author_problems` as a `PackError` before anything is written.
    3. After `create_deposition` and `_write_texts` with the reserved DOI, `final_problems(out_dir)`. Any problem raises the existing unfinished-draft `ZenodoError`, naming each placeholder, before `set_metadata` and `upload`.
  - `cli.pack_command` gains `final: bool = typer.Option(False, "--final", help="Refuse empty author fields and any placeholder DOI (needs --zenodo-live).")`.

- [ ] **Step 7: Minor item, `imp_unc`.** Test (`test_pack.py`):

```python
def test_imp_unc_carries_the_larger_error():
    data = {"ephemeris": {}, "planet": {}, "shape": {"ok": True, "b": {"median": 0.5, "minus": 0.063, "plus": 0.030,
                                                                      "point": 0.5}}}
    row = _parse_pipe(exofop.render_exofop_planet(data, "20261022", None, "tag"), PLANET_COLUMNS)
    assert row["imp"] == "0.500" and row["imp_unc"] == "0.063"
```

  `exofop._plain` already takes the larger half-width, so this may pass at once. If it does, it stays as the pin of the reviewer's item, and the review notes record that the file they read came from the quoted-free b of a version 4 dossier. If it fails, `_pair` passes both errors to `_plain`.

- [ ] **Step 8: Minor item, the central epoch.**

```python
def test_exofop_and_the_archive_quote_the_central_epoch(env_with_dossier):
    data = _with_errors(_stored(env_with_dossier))
    t = data["timing"]
    row = _parse_pipe(exofop.render_exofop_planet(data, "20261022", None, "tag"), PLANET_COLUMNS)
    assert row["epoch"] == f"{t['t_ref'] + BKJD:.4f}"
    assert float(row["epoch_unc"]) == pytest.approx(t["t_ref_err"], rel=0.05)
    assert f"(epoch {t['epoch_ref']}, the centre of the series)" in pack_text.ephemeris_sentence(data)
```

  `render_exofop_planet` takes `epoch` from `_bjd(eph, "t_ref")` with `t_ref_err_days` when the timing is ok (epoch 0 otherwise). `ephemeris_sentence` prints "T0 = BJD_TDB <t_ref ± err> (epoch E, the centre of the series)", so the ExoFOP note, the archive email and the Zenodo description follow.

- [ ] **Step 9: Minor item, the single-event sentence.**

```python
def test_the_single_event_sentence_compares_the_statistics():
    data = {"tests": [{"rung": "rung1_events", "status": "pass",
                       "metrics": {"n_covered": 31, "n_events_with_data": 33, "ses_max_over_mes": 0.28}}]}
    assert pack_module._vetting_sentences(data)[0] == (
        "Of the 33 predicted transits with data, 31 are well covered, and the largest single-event statistic is 0.28 "
        "of the multiple-event statistic.")
```

- [ ] **Step 10: Minor items, the `events.csv` row counts and the abstract's period digits.** Task 10's `file_notes` already prints the counts, and `test_events_and_transit_times_list_the_same_epochs_and_the_readme_says_how_many` pins them. This step adds the abstract:

```python
def test_the_abstract_quotes_the_period_with_the_texts_digits(env_with_dossier):
    data = _with_errors(_stored(env_with_dossier))
    assert f"repeats every {pack_text.quantities(data)['period']} days" in pack_module.abstract_text(data)
```

  `abstract_text` prints `quantities(data)["period"]` in place of `_f(period, 5)`.

- [ ] **Step 11: Minor item, "season" and "quarter group".**

```python
def test_season_means_the_roll_season_and_the_depth_test_speaks_of_quarter_groups():
    data = {"stability": {"metrics": {"season_depths": {"0": 1, "1": 2, "2": 3, "3": 4}, "chi2": 3.5, "dof": 3,
                                      "p_value": 0.32}}, "tests": []}
    text = " ".join(pack_module._vetting_sentences(data))
    assert "The depth agrees across 4 quarter groups (quarters taken modulo 4;" in text and "seasons" not in text
```

  The rung 3 sentence reads `data["stability"]` (Task 10) and falls back to the rung 3 row. The dossier caveat "The depth is measured in one season only" becomes "... in one quarter group only", and its text says "quarter group". Rung 5's archive seasons keep "season".

- [ ] **Step 12: Minor item, the TESS clause.** In `tests/unit/test_tess.py`:

```python
def test_the_tess_clause_says_no_data_were_examined_and_qualifies_not_expected():
    block = {**tess.statement(META, EPHEMERIS), "verdict": "not expected"}
    text = tess.note_sentence(block, readme=False)
    assert text.endswith("so TESS is not expected to have detected the transit at the nominal noise; no TESS data "
                         "were examined.")
```

  (`META` and `EPHEMERIS` are the module's inputs, lines 192 on.) `VERDICT_PHRASES["not expected"]` gains " at the nominal noise". `_plain_sentence` ends every counted prediction with "; no TESS data were examined." before the pointer.

- [ ] **Step 13: Minor item, the figure panels.**

```python
def test_the_note_figure_carries_the_fold_the_parity_and_the_season_difference_images(env_with_dossier):
    if pack_module._matplotlib() is None:
        pytest.skip("matplotlib is not installed")
    rep = _build(env_with_dossier)
    assert (rep.out_dir / "note" / "figure1.png").is_file()
    flat = " ".join(_texts(rep)["note/note.tex"].split())
    assert "(a) the light curve" in flat and "(b) odd and (c) even transits" in flat
    assert "(d) the per-season difference images" in flat and flat.count(r"\begin{figure}") == 1
```

  - `_png_figures` draws `note/figure1.png` and `.pdf` as one figure on a `GridSpec(3, max(2, n_seasons))`:
    - the fold across the top row;
    - odd and even in the second row;
    - one difference image per season in the third.
  - Each panel is labelled (a) to (d) and drawn by the same code as `fold.png`, `oddeven.png` and `centroids.png`, which stay as they are. The SVG copy `note/figure1.svg` stays the fold alone, and its file note says so.
  - The caption names the panels: "(a) the light curve ... (line); (b) odd and (c) even transits on the same scale; (d) the per-season difference images with this star (circle), the neighbour (square) and the centroid (cross)". Without pixel data the caption drops (d).

- [ ] **Step 14: Minor item, the follow-up clause.**

```python
def test_one_clause_gives_the_follow_up_window_and_the_photometry(env_with_dossier):
    data = _with_errors(_stored(env_with_dossier))
    after = 2461335.5                                         # 22 October 2026
    nxt = pack_rows.next_transits(data, after, n=1)[0]
    text = pack_module.followup_text(data, after)
    depth = float(f"{data['ephemeris']['depth_ppm']:.2g}")
    assert text == (f"A transit in {nxt['date'][-4:]} is predicted to within {nxt['window_min']:.0f} minutes "
                    f"(1 sigma) and needs photometry at about {depth:,.0f} ppm on a G "
                    f"{data['target']['gmag']:.1f} star, within reach of a 1 to 2 m telescope.")
    assert pack_module.followup_text({**data, "timing": None}, after) is None
```

  `followup_text` prints after the note's caveat titles in "What is not yet known" and in the README's step 5. `FOLLOWUP_REACH` is printed only when the depth is at least `FOLLOWUP_REACH_DEPTH_PPM` and G is at most `FOLLOWUP_REACH_GMAG`.

- [ ] **Step 15: Pack version 4 and the word budget.**
  - Set `PACK_VERSION = "4"` (test_pack.py's version assertions read the constant).
  - Run the live-shape limit test (`-k live_shape -vv`). If any of the four counts passes 1,480, cut in this order, re-measuring after each cut, and stop at the first that fits:
    1. The in-house method sentence of the false-positive section ("The estimate weighs a planet ... \citet{morton2012}, with planet occurrence from ...") moves to the README's false-positive paragraph. Its three bibitems leave the note.
    2. The cross-check sentence keeps its medians and run count in the note; the 16th to 84th percentile ranges move to the README.
    3. The comparison sentence of the prior claim drops the FPP clause, which stays in the README.
  - Record the counts in the commit message. Every number, error, citation and verdict of spec 11 stays.

- [ ] **Step 16: Regenerate the golden and run the tests** (all pass).
  - Regenerate with Task 10's helper.
  - `PYTHONUTF8=1 PYTHONPATH=src C:\Research\skysift\.venv\Scripts\python.exe -m pytest tests/unit/test_stars.py tests/unit/test_dossier.py tests/unit/test_pack.py tests/unit/test_tess.py tests/unit/test_zenodo.py tests/unit/test_cli.py tests/unit/test_ui_dossiers.py -q -p no:cacheprovider`.
  - Then `PYTHONUTF8=1 PYTHONPATH=src C:\Research\skysift\.venv\Scripts\python.exe -m ruff check src tests tools`.
  - Then the unit suite once, detached and hidden, at BelowNormal: `PYTHONUTF8=1 PYTHONPATH=src C:\Research\skysift\.venv\Scripts\python.exe -m pytest tests -q -p no:cacheprovider -m "not slow and not golden"`.

- [ ] **Step 17: Commit.**

```
git add docs/author.json src/skysift/stars.py src/skysift/dossier.py src/skysift/pack_rows.py src/skysift/pack.py src/skysift/pack_text.py src/skysift/exofop.py src/skysift/tess.py src/skysift/cli.py src/skysift/templates/pack/note.tex.j2 src/skysift/templates/pack/README.md.j2 tests/golden/kic-006146676-generated.json tests/unit/test_stars.py tests/unit/test_dossier.py tests/unit/test_pack.py tests/unit/test_tess.py tests/unit/test_zenodo.py tests/unit/test_cli.py
git commit -m "feat(pack): RUWE and NSS, Gaia and Kepler citations, docs/author.json and --final, the minor sweep (pack version 4)

Live-shape words: <four counts>.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

The live run of spec 11.7 follows these four tasks under Task 7's protocol:
- regenerate the five live dossiers and the second period of KIC 6699887;
- repeat the four TRICERATOPS runs with the round-2 protocol, since the depth moves the fingerprint, and re-import them;
- rebuild both packs;
- run the full gate on the tip.
