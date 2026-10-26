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
11. **A star with no voting season, or a voting season without a PSF width** (KIC 10352243 has none; KIC 9243078's season 1 votes with no `psf_sigma_pix`): the export's `rung5_psf_sigma.sigma_pix` is None with its reason, the tool refuses the export with exit 2 and no flag overrides it, and `pixel_excluded` excludes nothing with the reason "rung 5 has no voting season". A season without a width is left out of the mean, not read as zero. Pinned by Task 14 `test_rung5_psf_sigma_is_the_mean_over_voting_seasons` and `test_tool_refuses_an_export_without_rung_5s_psf_width`, and Task 15 `test_a_season_without_a_vote_keeps_every_neighbour_and_no_voting_season_excludes_nothing`.
12. **A neighbour with a missing vote** (a voting season without `dist_target_pix`, `centroid_sigma_axis_pix` or, for rung 5's own neighbour, `separation_sigma`): the neighbour is not excluded, its row says which season lacks what, and the third variant keeps it. A neighbour within 1 arcsec of the stamp separation that is not the nearest match (KIC 6146676's 10.94 arcsec star beside the 11.45 arcsec KIC 6146666) takes the geometric bound, never rung 5's vote. Pinned by Task 15 `test_pixel_excluded_takes_rung_5s_vote_for_its_neighbour_and_the_bound_for_the_others` and `test_a_season_without_a_vote_keeps_every_neighbour_and_no_voting_season_excludes_nothing`.
13. **A dust-map query that gives NaN, or a distance beyond the map** (a sightline outside the Pan-STARRS footprint, a pixel whose samples are all NaN, a star beyond the map's reliable distance): NaN is never stored; the tool exits 2 with the `--store` instruction. Beyond the last distance bin the map's last value is carried, as `dustmaps` does, and the stored block says `reliable_distance: false`, which the texts print as a clause. Pinned by Task 16 `test_the_reddening_at_a_distance_follows_dustmaps` and `test_a_nan_sightline_stops_the_tool_with_the_store_instruction`.
14. **A result file without the third variant** (every result of round 2, and any made by a tool before Task 15): the import refuses it ("the result is schema 2 (no schema key), not 3", or "the result file lacks variant_excluded"); a stored block of that format is not `complete`, so the page says it was stored by an earlier version and the live gate refuses. Pinned by Task 14 `test_the_import_refuses_a_result_before_schema_3` and Task 15 `test_the_import_requires_the_pixel_excluded_variant`.
15. **A star the dossier would characterise but without a stored extinction** (a new target, or `docs/candidates/<target>/extinction.json` deleted): `dossier.generate` raises `stars.NoExtinction` naming the tool command, and `skysift dossier` exits 2; a star Gaia cannot characterise (route "none") needs none. Pinned by Task 16 `test_generate_refuses_a_characterised_star_without_a_stored_extinction` and `test_a_star_gaia_cannot_characterise_needs_no_extinction`.

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
| `tools/triceratops_check.py`, `src/skysift/crosscheck.py`, `tools/leakage_note.py` (new) (round 3) | Tasks 14 and 15: the Kepler-pixel overlap shares under the budget cap, `rung5_psf_sigma`, `pixel_excluded`, the second run set with `remove_star`, export and result schema 3, the four numbers in every text. |
| `src/skysift/stars.py`, `tools/extinction.py` (new), `docs/candidates/<target>/extinction.json` (new) (round 3) | Task 16: the Bayestar19 E(B-V) read from the map file under `.venv-triceratops`, stored per target, converted with the Gaia (E)DR3 law and propagated through the draws; `DOSSIER_VERSION = "6"` and `PACK_VERSION = "5"` from Task 15. |
| `tools/bayestar_map.py` (new), `tools/extinction.py`, `src/skysift/stars.py`, `pack.py`, `pack_rows.py` (round 4 preparation) | Task 17: the Bayestar19 map read from a local file without healpy (under `.venv-dustmaps`), the tool's `--map` path, the adopted 3D reddening in every text with the full column as a check. |

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

---

## Round 3 (spec section 12, after reading results-r2)

Tasks 14 to 16 carry out spec section 12 (`docs/superpowers/specs/2026-10-22-skysift-referee-pack-design.md`, 12.1 to 12.4). They run in order: Task 15 reads Task 14's export keys and result schema, and Task 16 adds its block under the dossier version Task 15 sets. The golden is never edited by hand: Tasks 15 and 16 regenerate it with Task 10's helper (`tests/golden/regenerate.py`).

**Run commands of round 3** are those of round 2: every command sets `PYTHONPATH=src`, and each task ends with its ruled test command on the files it touched and ruff:

```
PYTHONUTF8=1 PYTHONPATH=src C:\Research\skysift\.venv\Scripts\python.exe -m pytest <the task's test files> -q -p no:cacheprovider
PYTHONUTF8=1 PYTHONPATH=src C:\Research\skysift\.venv\Scripts\python.exe -m ruff check src tests tools
```

`tools/extinction.py` alone runs under the other interpreter, `C:\Research\skysift\.venv-triceratops\Scripts\python.exe -I`, because it reads the dust map with `h5py`, which `.venv-triceratops` holds (h5py 3.16.0, astropy 7.2.3, numpy 1.26.4) and `.venv` does not. Its pure helpers need numpy and astropy only, so the engine's tests import them.

**What the controller measured (23 October 2026), and what it means for Task 14.** In `results-r2/kic-006146676.json` every TIC star carries `box_factor` 1.0, and the G 12.8 star at 11.69 arcsec (TIC 169822655, Gaia DR3 2073896190925687296, the stamp's own KIC 6146666) took 0.1615 of the 0.16753 budget. Rung 5's PSF width over the four voting seasons of the live dossier is 0.58222301, 0.55311152, 0.54572735 and 0.59730051 px, so `sigma_5` = 0.5695906 px. Under the rule of spec 12.1 that star's overlap at 2.94 Kepler pixels is 0.005864, but its Kepler-band flux is 23.555 times the target's, so its share is 0.1381: 15 percent below the round-2 value, not "effectively zero" as 12.1 expects. The seven listed neighbours sum to 0.15488, under the budget, so the cap does not bind. Task 14 implements the rule as ruled and writes these numbers into the comparison note; the note says plainly that the rule leaves the G 12.8 star with most of its share, and the live runs wait for the controller's reading of it (Task 14 Step 8).

**The extinction path (Task 16), and why.** Two paths were offered: the `dustmaps` package with the Bayestar19 file, and the Argonaut web API, one request per star through `net.fetch`. Neither works as offered on this machine, checked on 23 October 2026:
- `argonaut.skymaps.info` refused TCP on ports 80 and 443 (curl: "Could not connect to server" after 21 s), and its API takes a JSON POST, which `net.fetch` (GET only) cannot send.
- `dustmaps` 1.0.14 requires `healpy`, and healpy 1.20.1 publishes wheels for macOS and Linux only (PyPI file list), so `uv pip install dustmaps` on Windows would try to build healpy from source; `dustmaps.bayestar` imports healpy at module level.

The plan takes a third path that installs nothing: download `bayestar2019.h5` once from Harvard Dataverse (doi:10.7910/DVN/2EJ9TX, file id 3424724, 727,902,836 bytes, MD5 ab815d2fd3068d1b81a1bd61fb18a722; the dataset's other file, `bayestar2019.fits.gz`, is the 543 MB one) and read it in `tools/extinction.py` with h5py, which `.venv-triceratops` already holds. The tool does the two things `dustmaps` does: the nested HEALPix pixel of the sightline (`ang2pix_nest`, about 30 lines of numpy, checked against the 12 base-pixel centres and the nesting rule) and the distance interpolation of `BayestarQuery.query` (copied rule by rule from `dustmaps/bayestar.py`). It is lighter than the alternatives in every way that matters here: no package install into either venv, no build toolchain, no external service in the run, and five stars read in seconds from a file whose checksum is recorded. The download needs Vikram's yes first (Task 16 Step 7).

**Word budget.** Task 15 adds the variant's two medians and the dominant-scenario sentence to the note, and Task 16 one reddening clause. Each task ends with the live-shape limit test (`-k live_shape -vv`) and records the four counts in its commit message; Task 16 Step 13 gives the cut order if a count passes 1,480.

---

### Task 14: Leakage shares from the Kepler-pixel overlap, the budget as a cap, export and result schema 3 (spec 12.1)

**Files:**
- Modify: `tools/triceratops_check.py`.
  - Lines 36 to 49: the docstring's "Flux shares" paragraph (the rule of spec 12.1 replaces ruling n's quota).
  - Lines 105 to 123: constants. `PIXEL_ARCSEC` and `PSF_SIGMA_PIX` stay (they define `share_before`); add `EXPORT_SCHEMA`, `RESULT_SCHEMA`, `KEPLER_PIX_ARCSEC`, `OVERLAP`.
  - Lines 124 to 145: `CAVEATS` gains `PRF_CAVEAT`.
  - Lines 280 to 351: `offset_box_factor` (numbers unchanged), new `_box_fraction`, `overlap`, `budget_shares`; `leakage_shares` rewritten.
  - Lines 354 to 364: `leakage_caveat`.
  - Lines 397 to 417: `export_problems` (schema and `rung5_psf_sigma`).
  - Lines 549 to 571: `star_rows` (`overlap`, `weight`, `share_before`).
  - Lines 574 to 594: `assemble_result` (`schema`).
  - Lines 660 to 675: `checkpoint_key` (the PSF width in `flux_shares`).
  - Lines 804 to 812 and 839 to 892 of `main`: the refusal without a PSF width, the separations and the width passed to `leakage_shares`.
- Modify: `src/skysift/crosscheck.py`.
  - Lines 32 to 46: `EXPORT_SCHEMA`, `RESULT_SCHEMA`, `PSF_SIGMA_SOURCE`; `RESULT_KEYS` gains `"schema"`.
  - After `_leak` (lines 268 to 277): new `rung5_psf_sigma`.
  - Lines 404 to 417: `inputs.json` gains `"schema"` and `"rung5_psf_sigma"`.
  - Lines 471 to 524: `validate_result` checks the schema first.
- Create: `tools/leakage_note.py` (writes the comparison note; imports `tools.triceratops_check` helpers and runs under `.venv`).
- Create: `docs/superpowers/notes/2026-10-23-leakage-shares.md` (written by Step 7, never by hand).
- Test: `tests/unit/test_crosscheck.py`.
  - Lines 80 to 92: `fake_result` gains `"schema": 3`.
  - Lines 559 to 610: the two round-2 leakage tests keep their numbers on `share_before` through `tc.budget_shares` and call `leakage_shares` with the new arguments.
  - Lines 788 to 805: the added-star test keeps `offset_box_factor` and checks `share_before`.
  - Lines 824 to 851: the old-export test's message list gains the two schema problems, and the `export_problems` call at lines 848 to 850 gains `"schema": 3` and `"rung5_psf_sigma": LIVE_PSF`.
  - Lines 987 to 1000: `star_rows` rows carry `overlap`, `weight` and `share_before`.
  - New tests below.

**Interfaces:**
- Consumes:
  - `dossier.season_votes(season) -> bool`, `dossier._pixel_seasons(metrics) -> list[dict]`, `dossier._metrics(tests, rung) -> dict`, `dossier._num(m, key) -> float | None`.
  - Rung 5's stored season fields `voting`, `season`, `psf_sigma_pix` (the two-source fit's Gaussian width in Kepler pixels).
  - `results-r2/kic-006146676.json` `stars` rows: `id`, `sep_arcsec`, `gmag`, `kepler_flux`, `share`, `gaia_source_id`; `settings.flux_shares.budget`; `nfpp`, `nfpp_p16`, `nfpp_p84`.
  - `skysift.lc.pixels.PIX_ARCSEC` (3.98), pinned equal to the tool's copy by a test.
- Produces:
```python
# tools/triceratops_check.py
EXPORT_SCHEMA = 3                   # skysift.crosscheck.EXPORT_SCHEMA
RESULT_SCHEMA = 3                   # skysift.crosscheck.RESULT_SCHEMA
KEPLER_PIX_ARCSEC = 3.98            # skysift.lc.pixels.PIX_ARCSEC (the engine is not importable here; a test pins it)
OVERLAP = "Kepler-pixel overlap, capped by the contamination budget"
PRF_CAVEAT = ("The shares use a Gaussian of rung 5's fitted width over the target's 3 x 3 box, which has no PRF "
              "wings, so each is a floor on the light that neighbour leaks into the KBonus PSF light curve (Bryson "
              "et al. 2013 on PRF-fit decontamination; Martinez-Palomera et al. 2023 on the KBonus photometry).")
def _box_fraction(d_pix: float, s_pix: float) -> float
    # [Phi((1.5 - d) / s) - Phi((-1.5 - d) / s)] / [Phi(1.5 / s) - Phi(-1.5 / s)], Phi the standard normal CDF
def offset_box_factor(sep_arcsec) -> float          # unchanged: _box_fraction(|sep| / PIXEL_ARCSEC, PSF_SIGMA_PIX)
def overlap(sep_arcsec, sigma_pix) -> float
    # spec 12.1: _box_fraction(|sep| / KEPLER_PIX_ARCSEC, sigma_pix); 1.0 for a separation that is None or not
    # finite; ValueError("overlap needs rung 5's PSF width, a finite number above 0, not <value>") otherwise
def budget_shares(stars, crowdsap, gmags, box_factors=None) -> tuple[list[float], list[float | None]]
    # the round-2 rule, kept for share_before: (g, the normalised Kepler-band box fractions, one per row; the
    # shares B g_i / sum_{j>0} g_j, None for the target and every row when CROWDSAP is not in (0, 1]); exactly the
    # numbers leakage_shares gave before this task
def leakage_shares(stars, crowdsap, gmags, tdepth: float, seps_arcsec, sigma_pix: float | None,
                   box_factors=None) -> dict
    # stars: the package's rows after calc_depths, the target first ("ID", "Tmag", "fluxratio"); seps_arcsec: one
    # separation per row (the TIC row's "sep (arcsec)", the stored separation for a star the tool added).
    # With CROWDSAP C in (0, 1] and a finite sigma_pix above 0: B = (1 - C) / C; for every row i > 0,
    # w_i = overlap(sep_i, sigma_pix) * f_i with f_i = 10^(-0.4 (G_i - G_t)) (band "Gaia G") or, without both G,
    # 10^(-0.4 (T_i - T_t)) (band "Tmag"), or 0 (band None) without either; S_w = sum w_i; scale = min(1, B / S_w)
    # (1 when S_w is 0); share_i = w_i * scale; S = sum share_i; fluxratio_target = 1 / (1 + S),
    # fluxratio_i = share_i / (1 + S); the target keeps tdepth and star i needs tdepth / share_i, 0 above 1 or
    # without a share. Without such a C or such a sigma the package's box stands, as before (source BOX).
    # Returns {"source": OVERLAP | BOX, "budget": B | None, "contaminating_fraction": 1 - C | None, "crowdsap",
    #  "sigma_pix", "weight_sum": S_w | None, "capped": S_w > B | None, "scale": scale | None,
    #  "rows": [{"id", "g_package", "box_factor", "band", "kepler_flux" (f_i from G, None without it), "g"
    #  (budget_shares' g), "overlap" (None for the target), "weight" (None for the target), "share_before"
    #  (budget_shares' share), "share" (None for the target), "fluxratio", "tdepth", "source"}]}
def star_rows(...) -> list[dict]     # each row also carries "overlap", "weight" and "share_before"
def assemble_result(runs, stars, settings, inputs, version) -> dict     # gains "schema": RESULT_SCHEMA first
# export_problems(): + "the export is schema <value>, not 3" and "the export has no rung5_psf_sigma key"
# main(): with "rung5_psf_sigma" present and its sigma_pix None (or not finite, or not above 0), exit 2 with
#   "rung 5 has no fitted PSF width for this star (<reason>), so the Kepler-pixel overlap cannot be computed; no run
#   is started" before the package's constructor; --allow-old-export does not override it
# checkpoint_key(): "flux_shares" holds {"source", "budget", "crowdsap", "sigma_pix"}

# src/skysift/crosscheck.py
EXPORT_SCHEMA = 3
RESULT_SCHEMA = 3
PSF_SIGMA_SOURCE = "rung 5's two-source fit (psf_sigma_pix), mean over the voting seasons with a width"
RESULT_KEYS = ("schema", "tool", "tool_version", ...)         # "schema" first, the rest as before
def rung5_psf_sigma(data: dict) -> dict
    # {"sigma_pix": mean of psf_sigma_pix over the seasons that vote (dossier.season_votes) with a finite width
    #  above 0 | None, "seasons": their season numbers in order, "per_season": {str(season): width},
    #  "source": PSF_SIGMA_SOURCE, "reason": None | "rung 5 has not run" | "rung 5 has no voting season with a
    #  fitted PSF width"}
# inputs.json gains "schema": EXPORT_SCHEMA and "rung5_psf_sigma": rung5_psf_sigma(data)
# validate_result(): first, a result without "schema" raises BadResult("the result is schema 2 (no schema key), not
#   3: it was made before round 3 (spec 12) without the Kepler-pixel shares; run the tool again"), and another
#   value "the result is schema <value>, not 3: ..." with the same tail

# tools/leakage_note.py
NOTE_WITHIN_ARCSEC = 21.0           # the note lists the stars within this of the target (seven for KIC 6146676)
def note_rows(result: dict, sigma_pix: float) -> list[dict]
    # result["stars"] other than the first (the target) with a finite sep_arcsec at or below NOTE_WITHIN_ARCSEC,
    # sorted by separation: {"id", "gaia_source_id", "sep_arcsec", "sep_pix" (sep / KEPLER_PIX_ARCSEC), "gmag",
    # "kepler_flux", "overlap", "share_before" (the stored share), "share"}; share = overlap * kepler_flux * scale,
    # scale = min(1, budget / the same product summed over every non-target row with a kepler_flux); a row without
    # kepler_flux gets share None
def render(result: dict, sigma: dict, rows: list[dict], after: dict | None = None) -> str
def main(argv: list[str] | None = None) -> int
    # --result <results-r2 json> --dossier <dossier_data.json> --out <note.md> [--after <round-3 result json>]
```

- [ ] **Step 1: Write the failing tool tests** in `tests/unit/test_crosscheck.py`. The numbers are KIC 6146676's: the four rows are the target and the three nearest TIC stars of `results-r2/kic-006146676.json`, and the expected values were computed from them on 23 October 2026 with the rule of spec 12.1.

```python
SIGMA_5 = 0.5695905975              # KIC 6146676: rung 5's psf_sigma_pix, mean over its four voting seasons
R2_STARS = [                        # results-r2/kic-006146676.json: the target and the three nearest TIC stars
    {"ID": "169822650", "Tmag": 15.1926, "fluxratio": 0.6157398795699183, "sep (arcsec)": 0.0},
    {"ID": "1879962333", "Tmag": 19.0349, "fluxratio": 0.007575984235464079, "sep (arcsec)": 6.938},
    {"ID": "169822636", "Tmag": 17.086, "fluxratio": 0.01056327860128502, "sep (arcsec)": 10.784},
    {"ID": "169822655", "Tmag": 12.3923, "fluxratio": 0.36537585545284657, "sep (arcsec)": 11.69}]
R2_G = [16.236654, 19.771944, 17.895601, 12.806441]
R2_SEPS = [r["sep (arcsec)"] for r in R2_STARS]
CROWDSAP_6146676, DEPTH_6146676 = 0.85650748, 0.0022139651


def test_tool_overlap_is_the_box_fraction_at_rung_5s_width_in_kepler_pixels():
    from skysift.lc.pixels import PIX_ARCSEC
    from tools import triceratops_check as tc
    assert tc.KEPLER_PIX_ARCSEC == PIX_ARCSEC == 3.98
    assert tc.overlap(11.69, SIGMA_5) == pytest.approx(0.005864323, rel=1e-5)        # 2.94 Kepler pixels
    assert tc.overlap(6.938, SIGMA_5) == pytest.approx(0.3375419, rel=1e-5)
    assert tc.overlap(10.784, SIGMA_5) == pytest.approx(0.01699803, rel=1e-5)
    assert tc.overlap(0.0, SIGMA_5) == pytest.approx(1.0) and tc.overlap(None, SIGMA_5) == 1.0
    assert tc.offset_box_factor(7.0) == pytest.approx(0.38705, abs=1e-4)             # the round-2 rule, unchanged
    for bad in (None, 0.0, -0.5, math.nan):
        with pytest.raises(ValueError, match="overlap needs rung 5's PSF width"):
            tc.overlap(11.69, bad)


def test_tool_shares_are_overlap_times_kepler_flux_under_the_budget_cap():
    """Spec 12.1 on KIC 6146676's three nearest TIC stars: w = overlap x 10^(-0.4 (G - G_t)). Their sum 0.15483 is
    below the budget 0.16753, so the shares stand: the G 12.8 star at 11.69 arcsec keeps 0.1381 of the 0.1618 the
    round-2 rule gives it on these four rows (0.1615 on the full list)."""
    from tools import triceratops_check as tc
    out = tc.leakage_shares(R2_STARS, CROWDSAP_6146676, R2_G, DEPTH_6146676, R2_SEPS, SIGMA_5)
    assert out["source"] == tc.OVERLAP and out["sigma_pix"] == SIGMA_5
    assert out["budget"] == pytest.approx(0.1675321271, rel=1e-9) and out["capped"] is False and out["scale"] == 1.0
    assert out["weight_sum"] == pytest.approx(0.1548311, rel=1e-5)
    t, a, b, c = out["rows"]
    assert [r["share"] for r in (a, b, c)] == pytest.approx([0.01300803, 0.003688244, 0.1381348], rel=1e-5)
    assert [r["share_before"] for r in (a, b, c)] == pytest.approx([0.002491678, 0.003249592, 0.1617909], rel=1e-5)
    assert c["overlap"] == pytest.approx(0.005864323, rel=1e-5) and c["kepler_flux"] == pytest.approx(23.5551,
                                                                                                      rel=1e-5)
    assert t["share"] is None and t["share_before"] is None and t["overlap"] is None
    assert t["fluxratio"] == pytest.approx(0.8659275, rel=1e-6) and c["fluxratio"] == pytest.approx(0.1196147, rel=1e-5)
    assert c["tdepth"] == pytest.approx(0.01602757, rel=1e-5) and t["tdepth"] == DEPTH_6146676
    assert sum(r["fluxratio"] for r in out["rows"]) == pytest.approx(1.0)
    assert all(r["source"] == tc.OVERLAP for r in out["rows"]) and c["band"] == "Gaia G"


def test_tool_shares_above_the_budget_are_scaled_down_to_it():
    from tools import triceratops_check as tc
    out = tc.leakage_shares(R2_STARS, 0.95, R2_G, DEPTH_6146676, R2_SEPS, SIGMA_5)
    assert out["capped"] is True and out["scale"] == pytest.approx(0.3399291, rel=1e-6)
    t, a, b, c = out["rows"]
    assert a["share"] + b["share"] + c["share"] == pytest.approx(0.05 / 0.95)
    assert c["share"] == pytest.approx(0.04695603, rel=1e-5) and b["tdepth"] == 0.0      # 1.77 needed: no eclipse
    assert t["fluxratio"] == pytest.approx(0.95)


def test_tool_without_rung_5s_width_or_a_budget_the_package_box_stands():
    from tools import triceratops_check as tc
    for crowdsap, sigma in ((CROWDSAP_6146676, None), (None, SIGMA_5), (CROWDSAP_6146676, math.nan)):
        box = tc.leakage_shares(R2_STARS, crowdsap, R2_G, DEPTH_6146676, R2_SEPS, sigma)
        assert box["source"] == tc.BOX and box["weight_sum"] is None and box["capped"] is None
        assert [r["fluxratio"] for r in box["rows"]] == [r["fluxratio"] for r in R2_STARS]


LIVE_SEASONS = [                    # KIC 6146676's rung 5 seasons in the live dossier, the fields spec 12 reads
    {"season": 0, "voting": True, "psf_sigma_pix": 0.58222301, "nearer": "target", "separation_sigma": 27.845078,
     "dist_target_pix": 0.19970259, "centroid_sigma_axis_pix": 0.057549507,
     "pos_target": [0.4881452, 4.1079262], "pos_neighbour": [3.2587867, 3.2589792]},
    {"season": 1, "voting": True, "psf_sigma_pix": 0.55311152, "nearer": "target", "separation_sigma": 19.814692,
     "dist_target_pix": 0.18730524, "centroid_sigma_axis_pix": 0.068707453,
     "pos_target": [0.92328348, 3.0889016], "pos_neighbour": [3.6952899, 2.2423046]},
    {"season": 2, "voting": True, "psf_sigma_pix": 0.54572735, "nearer": "target", "separation_sigma": 17.424733,
     "dist_target_pix": 0.11745102, "centroid_sigma_axis_pix": 0.078553466,
     "pos_target": [0.90604171, 2.9713739], "pos_neighbour": [3.6771262, 2.1225931]},
    {"season": 3, "voting": True, "psf_sigma_pix": 0.59730051, "nearer": "target", "separation_sigma": 12.657602,
     "dist_target_pix": 0.18658094, "centroid_sigma_axis_pix": 0.10056612,
     "pos_target": [0.73929463, 3.0401927], "pos_neighbour": [3.5106545, 2.1932004]}]
LIVE_NEIGHBOURS = [                 # its fpp.inputs.neighbours (Gaia DR3 id, separation, G)
    {"source_id": "2073896294004902144", "sep_arcsec": 7.1422442, "gmag": 19.771944},
    {"source_id": "2073896289692192128", "sep_arcsec": 10.941229, "gmag": 17.895601},
    {"source_id": "2073896190925687296", "sep_arcsec": 11.452381, "gmag": 12.806441},
    {"source_id": "2073896190926955520", "sep_arcsec": 12.965638, "gmag": 19.735788},
    {"source_id": "2073896190921609600", "sep_arcsec": 13.513456, "gmag": 19.379517},
    {"source_id": "2073896289692177024", "sep_arcsec": 18.624956, "gmag": 18.883438}]


def _live(seasons=LIVE_SEASONS, neighbours=LIVE_NEIGHBOURS) -> dict:
    return {"tests": [{"rung": "rung5_pixels", "status": "pass", "metrics": {"seasons": seasons}}],
            "fpp": {"inputs": {"neighbours": neighbours}}}


LIVE_PSF = cc.rung5_psf_sigma(_live()) if hasattr(cc, "rung5_psf_sigma") else None


def test_rung5_psf_sigma_is_the_mean_over_voting_seasons():
    out = cc.rung5_psf_sigma(_live())
    assert out["sigma_pix"] == pytest.approx(SIGMA_5) and out["seasons"] == [0, 1, 2, 3] and out["reason"] is None
    assert out["per_season"]["3"] == 0.59730051 and out["source"] == cc.PSF_SIGMA_SOURCE
    some = [{**LIVE_SEASONS[0], "voting": False}, *LIVE_SEASONS[1:3], {**LIVE_SEASONS[3], "psf_sigma_pix": None}]
    assert cc.rung5_psf_sigma(_live(some))["sigma_pix"] == pytest.approx((0.55311152 + 0.54572735) / 2)
    assert cc.rung5_psf_sigma(_live(some))["seasons"] == [1, 2]
    none = cc.rung5_psf_sigma(_live([{**s, "voting": False} for s in LIVE_SEASONS]))
    assert none["sigma_pix"] is None and none["reason"] == "rung 5 has no voting season with a fitted PSF width"
    assert cc.rung5_psf_sigma({"tests": []})["reason"] == "rung 5 has not run"


def test_the_export_carries_schema_3_and_rung_5s_psf_width(env, tmp_path):
    cc.export_inputs(env.store, env.blob, env.settings, STAGE2_TARGET, tmp_path / "cc", fetch=env.xfetch)
    inputs = json.loads((tmp_path / "cc" / "inputs.json").read_text(encoding="utf-8"))
    assert inputs["schema"] == cc.EXPORT_SCHEMA == 3
    assert inputs["rung5_psf_sigma"] == cc.rung5_psf_sigma(_stored(env))


def _current_export(path: Path, **inputs_changes) -> None:
    """``_old_export`` with everything an export of round 3 carries; ``inputs_changes`` replace keys of inputs.json."""
    _old_export(path)
    inputs = json.loads((path / "inputs.json").read_text(encoding="utf-8"))
    inputs.update({"schema": 3, "rung5_psf_sigma": LIVE_PSF,
                   "target": {**inputs["target"], "crowdsap": 0.8565, "psffrac": 0.8},
                   "neighbours": [{**n, "params_source": {}, "mass_radius": None} for n in inputs["neighbours"]],
                   "lightcurve": {**inputs["lightcurve"], "clipped": 0}, **inputs_changes})
    (path / "inputs.json").write_text(json.dumps(inputs), encoding="utf-8")


def test_tool_refuses_an_export_without_rung_5s_psf_width(tmp_path, monkeypatch, capsys):
    from tools import triceratops_check as tc
    monkeypatch.chdir(tmp_path)
    none = cc.rung5_psf_sigma(_live([{**s, "voting": False} for s in LIVE_SEASONS]))
    _current_export(tmp_path, rung5_psf_sigma=none)
    monkeypatch.setattr(tc, "tr", object())                 # as if TRICERATOPS were installed
    args = ["--inputs", str(tmp_path), "--out", str(tmp_path / "r.json")]
    assert tc.main(args) == 2 and tc.main([*args, "--allow-old-export"]) == 2
    err = capsys.readouterr().err
    assert ("rung 5 has no fitted PSF width for this star (rung 5 has no voting season with a fitted PSF width), so "
            "the Kepler-pixel overlap cannot be computed; no run is started") in err
    assert not (tmp_path / "r.json").exists()


def test_the_import_refuses_a_result_before_schema_3():
    good = fake_result("a" * 64)
    assert good["schema"] == cc.RESULT_SCHEMA == 3 and cc.RESULT_KEYS[0] == "schema"
    cc.validate_result(good)
    with pytest.raises(cc.BadResult, match=r"the result is schema 2 \(no schema key\), not 3: it was made before "
                                           r"round 3"):
        cc.validate_result({k: v for k, v in good.items() if k != "schema"})
    with pytest.raises(cc.BadResult, match="the result is schema 2, not 3"):
        cc.validate_result({**good, "schema": 2})
    assert not cc.complete({k: v for k, v in good.items() if k != "schema"})       # a stored round-2 block


def test_tool_result_and_checkpoint_carry_the_schema_and_the_width():
    from tools import triceratops_check as tc
    shares = tc.leakage_shares(R2_STARS, CROWDSAP_6146676, R2_G, DEPTH_6146676, R2_SEPS, SIGMA_5)
    key = tc.checkpoint_key("a" * 64, seed=1, draws=2000, check_draws=2000, parallel_check=True, flux_err=0.001,
                            shares=shares, quarters=[1], allow_old_export=False, version="1.1.0")
    assert key["flux_shares"] == {"source": tc.OVERLAP, "budget": shares["budget"], "crowdsap": CROWDSAP_6146676,
                                  "sigma_pix": SIGMA_5}
    rows = tc.star_rows(R2_STARS, [], {}, shares, R2_G, {})
    assert rows[3]["overlap"] == pytest.approx(0.005864323, rel=1e-5) and rows[3]["share_before"] == pytest.approx(
        0.1617909, rel=1e-5) and rows[3]["share"] == pytest.approx(0.1381348, rel=1e-5)
    assert tc.assemble_result([tc.run_record(1, 0.1, 0.01, False, [])], rows, {}, {"fingerprint": "a" * 64},
                              "1.1.0")["schema"] == tc.RESULT_SCHEMA == 3
```

`LIVE_PSF` is a module constant so that `_current_export` and Task 15 can share it; while `rung5_psf_sigma` does not exist it is None and the tests that read it fail on their own assertions.

- [ ] **Step 2: Write the failing note test** in the same file. The rows are KIC 6146676's round-2 `stars` rows (the target and the eight nearest stars; the eighth, at 22.45 arcsec, lies beyond `NOTE_WITHIN_ARCSEC` and is left out).

```python
R2_ROWS = [                         # results-r2/kic-006146676.json "stars": id, sep, G, Kepler flux, share, Gaia id
    ("169822650", 0.0, 16.236654, 1.0, None, None),
    ("1879962333", 6.938, 19.771944, 0.03853754101954881, 0.0024876585183042496, "2073896294004902144"),
    ("169822636", 10.784, 17.895601, 0.21698074699419992, 0.003244349526576897, "2073896289692192128"),
    ("169822655", 11.69, 12.806441, 23.555113433751, 0.16152983418298314, "2073896190925687296"),
    ("508690015", 13.151, 19.735788, 0.0398424833663588, 0.00013322001395335415, "2073896190926955520"),
    ("169822646", 13.584, 19.379517, 0.055316513563358254, 0.0001354611838130699, "2073896190921609600"),
    ("169822657", 18.369, 18.883438, 0.08735472482743722, 1.5945761920931935e-06, "2073896289692177024"),
    ("1879962327", 20.886, 20.5462, 0.018887809767256545, 5.1585379086076936e-09, None),
    ("1879962326", 22.446, 20.3416, 0.02280455489544754, 2.579127821808112e-09, None)]
R2_RESULT = {"stars": [{"id": i, "sep_arcsec": s, "gmag": g, "kepler_flux": f, "share": sh, "gaia_source_id": gid}
                       for i, s, g, f, sh, gid in R2_ROWS],
             "settings": {"flux_shares": {"source": "contamination budget", "budget": 0.1675321270982946,
                                          "crowdsap": 0.85650748}},
             "nfpp": 0.02232841831933376, "nfpp_p16": 0.016386351289008505, "nfpp_p84": 0.03243009921894156,
             "n_runs": 20}


def test_the_leakage_note_lists_each_neighbour_before_and_after(tmp_path):
    from tools import leakage_note as ln
    rows = ln.note_rows(R2_RESULT, SIGMA_5)
    assert [r["sep_arcsec"] for r in rows] == [6.938, 10.784, 11.69, 13.151, 13.584, 18.369, 20.886]
    assert [r["share_before"] for r in rows] == [r[4] for r in R2_ROWS[1:8]]
    assert [r["share"] for r in rows] == pytest.approx([0.01300803, 0.003688244, 0.1381348, 3.087380e-05,
                                                        2.184640e-05, 1.98896e-09, 4.48961e-13], rel=1e-4)
    assert rows[2]["sep_pix"] == pytest.approx(2.937186, rel=1e-6) and rows[2]["gaia_source_id"] == (
        "2073896190925687296")
    sigma = cc.rung5_psf_sigma(_live())
    text = ln.render(R2_RESULT, sigma, rows)
    assert "| 169822655 | 2073896190925687296 | 11.69 | 2.94 | 12.81 | 23.56 | 0.005864 | 0.1615 | 0.1381 |" in text
    assert "NFPP before: 0.022 (16th to 84th percentile 0.016 to 0.032) over 20 runs" in text
    assert "NFPP after: not yet measured (the round-3 run)" in text
    assert ("The largest share after the rule is 0.138 (TIC 169822655, 11.69 arcsec, 2.94 Kepler pixels): its "
            "overlap 0.0059 times its Kepler-band flux of 23.6 times the target's.") in text
    after = {**R2_RESULT, "nfpp": 0.0191, "nfpp_p16": 0.014, "nfpp_p84": 0.027}
    assert "NFPP after: 0.019 (16th to 84th percentile 0.014 to 0.027) over 20 runs" in ln.render(
        R2_RESULT, sigma, rows, after)
    assert "\u2014" not in text
```

- [ ] **Step 3: Run them to see them fail.**
  - Command: `PYTHONUTF8=1 PYTHONPATH=src C:\Research\skysift\.venv\Scripts\python.exe -m pytest tests/unit/test_crosscheck.py -q -p no:cacheprovider`.
  - Expected: `AttributeError: module 'tools.triceratops_check' has no attribute 'KEPLER_PIX_ARCSEC'` (and `'overlap'`, `'OVERLAP'`); `TypeError: leakage_shares() missing 2 required positional arguments` or an unexpected one in the round-2 tests until they are edited; `AttributeError: module 'skysift.crosscheck' has no attribute 'rung5_psf_sigma'`; `KeyError: 'schema'` in the import test; `ModuleNotFoundError: No module named 'tools.leakage_note'`.

- [ ] **Step 4: The overlap and the shares** in `tools/triceratops_check.py`.
  - Add the constants of the interface block after line 122. `offset_box_factor` keeps its docstring and becomes `_box_fraction(abs(sep) / PIXEL_ARCSEC, PSF_SIGMA_PIX)`, so its numbers do not move.
  - `overlap(sep_arcsec, sigma_pix)` as specified. The ValueError text is "overlap needs rung 5's PSF width, a finite number above 0, not {sigma_pix!r}".
  - `budget_shares` holds lines 317 to 345 of today's `leakage_shares` (the Kepler-band re-weighting and B g_i / sum g_j), returning `(g, shares)`.
  - `leakage_shares(stars, crowdsap, gmags, tdepth, seps_arcsec, sigma_pix, box_factors=None)`: the BOX branch is today's lines 347 to 351 with the new keys set to None; the OVERLAP branch follows the interface comment line by line. The target's row has `overlap`, `weight` and `share` None. Each neighbour row's `kepler_flux` is `10 ** (-0.4 * (G_i - G_t))` when both G are known, else None.
  - `leakage_caveat(shares, crowdsap)` for OVERLAP reads: "CROWDSAP {C:.4f} of the target's KBonus file bounds the light its neighbours can leak into its PSF light curve at (1 - CROWDSAP) / CROWDSAP = {B:.4f} of the target's own. Each neighbour's share is the fraction of a Gaussian of rung 5's fitted width ({sigma:.3f} Kepler pixels, the mean over its voting seasons) at its separation that falls inside the target's 3 x 3 box, over that of a star at the centre, times its Kepler-band flux relative to the target's (Gaia G as the proxy); the shares sum to {S_w:.4f}, {'above the budget, so they are scaled down to it' if capped else 'within the budget, so they stand'}." The BOX caveat stays as it is.
  - `CAVEATS` gains `PRF_CAVEAT` after the first item, and the docstring's "Flux shares" paragraph is rewritten to the rule above.
  - `star_rows` copies `overlap`, `weight` and `share_before` from the share rows. `assemble_result` puts `"schema": RESULT_SCHEMA` first.
  - `checkpoint_key` keeps `{k: shares.get(k) for k in ("source", "budget", "crowdsap", "sigma_pix")}`.
  - `export_problems` appends the two schema problems of the interface block.
  - `main`: after the `problems` check (line 809), read `psf = inputs.get("rung5_psf_sigma")`. When it is a dict whose `sigma_pix` is not a finite number above 0, print the refusal of the interface block (with `psf.get("reason")`) and return 2. At line 888, pass `seps_arcsec`: the TIC row's `"sep (arcsec)"`, or for a star in `added` the stored separation of its Gaia neighbour (`by_source[sid]["sep_arcsec"]`), since the package places an added star on the target's pixel. Pass `sigma_pix=_finite((psf or {}).get("sigma_pix"))`.
  - Settings `flux_shares` gains `"sigma_pix"`, `"weight_sum"`, `"capped"` and `"scale"`.

- [ ] **Step 5: The export and the import** in `src/skysift/crosscheck.py`.
  - `rung5_psf_sigma(data)` reads `ds._metrics(data.get("tests") or [], "rung5_pixels")`. Without that row (empty metrics) the reason is "rung 5 has not run". It takes the seasons of `ds._pixel_seasons(metrics)` where `ds.season_votes(s)` holds and `ds._num(s, "psf_sigma_pix")` is above 0, in stored order. The mean is `float(np.mean(widths))`.
  - `export_inputs` writes `"schema": EXPORT_SCHEMA` first and `"rung5_psf_sigma": rung5_psf_sigma(data)` after `"leak"`.
  - `RESULT_KEYS = ("schema", *the round-2 keys)`. `validate_result` checks `result.get("schema")` before the missing-key check, with the two messages of the interface block.
  - The module docstring says that the result format is schema 3 and why.

- [ ] **Step 6: The note script**, `tools/leakage_note.py`, with a module docstring that says what it writes and from what. `main` reads `--result` and `--dossier` (JSON), takes `sigma = crosscheck.rung5_psf_sigma(dossier)`, and stops with exit 2 when `sigma["sigma_pix"]` is None. It writes `render(...)` to `--out` and prints the path. `render` writes, in this order:
  - A title, "Leakage shares of KIC 6146676 before and after spec 12.1 (23 October 2026)".
  - One paragraph naming the source files, the budget ("(1 - CROWDSAP) / CROWDSAP = 0.16753"), `sigma_pix` with its seasons and per-season widths, and the rule in one sentence.
  - The table: `| TIC | Gaia DR3 | sep (arcsec) | sep (Kepler px) | G | Kepler flux / target | overlap | share before | share after |`. Separations are printed to 2 decimals, pixels to 2, G to 2, the flux to 2, the overlap to 4 significant figures, and the shares to 4 significant figures ("0" below 1e-12). A row without a Gaia id prints "not matched".
  - "NFPP before: <nfpp> (16th to 84th percentile <p16> to <p84>) over <n> runs" (`crosscheck.probability_text`), and "NFPP after: not yet measured (the round-3 run)" or, with `after`, the same form from it.
  - When the largest share after the rule exceeds 0.01: "The largest share after the rule is <share:.3g> (TIC <id>, <sep:.2f> arcsec, <sep_pix:.2f> Kepler pixels): its overlap <overlap:.2g> times its Kepler-band flux of <flux:.3g> times the target's. Spec 12.1 expected a star beyond two Kepler pixels to take effectively nothing; this one keeps most of its round-2 share, so the NFPP it drives is for the controller to read before the live runs."
  - `PRF_CAVEAT` as the last paragraph.

- [ ] **Step 7: Write the note.** From the worktree, `PYTHONUTF8=1 PYTHONPATH="src;." C:\Research\skysift\.venv\Scripts\python.exe tools/leakage_note.py --result .superpowers/sdd/2026-10-22-skysift-referee-pack/live/results-r2/kic-006146676.json --dossier C:\Research\skysift\.superpowers\sample-run\dossiers\kic-006146676\dossier_data.json --out docs/superpowers/notes/2026-10-23-leakage-shares.md`. The dossier is only read. Check the note against Step 2's numbers: the 11.69 arcsec row reads 0.1615 before and 0.1381 after, and the seven rows are at 6.94, 10.78, 11.69, 13.15, 13.58, 18.37 and 20.89 arcsec.

- [ ] **Step 8: Report the finding.** The task's report to the controller quotes the note's "largest share" paragraph and the table's 11.69 arcsec row. The live runs of spec 12.4 wait for the controller's reading. The code of Tasks 15 and 16 does not, since it does not depend on the shares' values.

- [ ] **Step 9: Run the tests** (all pass).
  - `PYTHONUTF8=1 PYTHONPATH=src C:\Research\skysift\.venv\Scripts\python.exe -m pytest tests/unit/test_crosscheck.py -q -p no:cacheprovider`
  - `PYTHONUTF8=1 PYTHONPATH=src C:\Research\skysift\.venv\Scripts\python.exe -m ruff check src tests tools`

- [ ] **Step 10: Commit.**

```
git add tools/triceratops_check.py tools/leakage_note.py src/skysift/crosscheck.py tests/unit/test_crosscheck.py docs/superpowers/notes/2026-10-23-leakage-shares.md
git commit -m "fix(crosscheck): leakage shares from the Kepler-pixel overlap at rung 5's PSF width, the budget a cap, export and result schema 3

The G 12.8 star at 11.69 arcsec goes from 0.1615 to 0.1381 of KIC 6146676's 0.1675 budget (the note says why).

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 15: The third variant, pixel-excluded neighbours removed, and the four numbers (spec 12.2; dossier version 6, pack version 5)

**Files:**
- Modify: `src/skysift/crosscheck.py`.
  - Lines 1 to 15: the module docstring (the third variant).
  - Lines 32 to 46: `EXCLUDE_SIGMA`, `STAMP_MATCH_ARCSEC`, `EXCLUDE_RULE`, `VARIANT_KEYS`; `RESULT_KEYS` gains `"variant_excluded"`.
  - Lines 254 to 265: `_pixel_exclusion` keeps the radius; its docstring drops "The tool does not read it yet".
  - New `stamp_neighbour`, `season_margin` and `pixel_excluded` after `rung5_psf_sigma`.
  - Lines 404 to 417: `inputs.json` gains `"pixel_excluded"`.
  - Lines 455 to 524: `_validate_run` unchanged; new `_validate_variant`, called from `validate_result`.
  - Lines 549 to 555: `with_current` adds `"class_excluded"`.
  - Lines 623 to 649: `dominant_scenario` takes a block of either set; new `removed_phrase`, `excluded_text`, `dominant_excluded_sentence`.
  - Lines 671 to 741: `headline` and `comparison_sentence` print the four numbers.
- Modify: `tools/triceratops_check.py`.
  - Lines 1 to 86: the docstring gains a "Third variant" paragraph.
  - Lines 124 to 145: the last caveat of `CAVEATS` is revised.
  - Lines 397 to 417: `export_problems` gains "the export has no pixel_excluded key".
  - New `excluded_stars` and `variant_block`, after `summarise` (line 505).
  - Lines 574 to 594: `assemble_result(..., *, variant)`.
  - Lines 660 to 734: `checkpoint_key` gains `excluded_tic_ids`; `new_state` gains `runs_excluded`; `checkpoint_runs` and `resume_state` read the second set.
  - Lines 955 to 1002 of `main`: the second run set.
- Modify: `src/skysift/dossier.py`.
  - Lines 90 to 92: `DOSSIER_VERSION = "6"`.
  - Lines 2023 to 2056: `page_texts` gains `"excluded"`.
- Modify: `src/skysift/pack.py`.
  - Line 122: `PACK_VERSION = "5"`.
  - Lines 412 to 416: `BOTH_TOOLS` goes; `both_tools` (lines 738 to 744) returns `cc.dominant_excluded_sentence(data, tex=tex, short=True)`.
  - Lines 2056 to 2058: the `data/cross_check.json` file note names the second run set.
- Modify: `src/skysift/templates/pack/note.tex.j2` (line 51, `n.both_tools`) and `README.md.j2` (lines 97 to 104).
- Modify: `src/skysift/ui/templates/dossier.html` (lines 236 to 256) and `src/skysift/ui/static/dossier.js` (lines 525 to 545).
- Test: `tests/unit/test_crosscheck.py`, `tests/unit/test_dossier.py`, `tests/unit/test_pack.py`, `tests/unit/test_zenodo.py` and `tests/unit/test_ui_dossiers.py`.
  - In `test_crosscheck.py`: `fake_result` (lines 80 to 92) gains `"variant_excluded": variant or fake_variant()`; the result test at lines 632 to 660 passes `variant=tc.variant_block(runs, [], True)`; and `_current_export` gains `"pixel_excluded": cc.pixel_excluded(_live())`.
  - In `test_ui_dossiers.py`, line 771 holds `test_dossier_page_shows_the_cross_check_block`.
  - The dossier-version pins in `test_dossier.py` read the constant.
  - The golden, regenerated in Step 11.

**Interfaces:**
- Consumes:
  - Task 14: `rung5_psf_sigma`, `RESULT_SCHEMA`, `EXPORT_SCHEMA`, `LIVE_SEASONS`, `LIVE_NEIGHBOURS`, `_live`, `_current_export`.
  - Rung 5's stored season fields. For every voting season: `season`, `voting`, `nearer`, `separation_sigma` (the bisector distance of the difference centroid over the inflated axis error, signed by `nearer`), `dist_target_pix` (the centroid's distance from the target's position), `centroid_sigma_axis_pix`, `pos_target` and `pos_neighbour`.
  - `fpp.inputs.neighbours` (`source_id`, `sep_arcsec`, `gmag`); `centroid_offset.exclusion_radius_arcsec`.
  - `dossier.season_votes`, `dossier._pixel_seasons`, `dossier._num`, `dossier._pair`, `priors.PIXEL_ARCSEC` (3.98), `dossier.UNRESOLVED_COMPANION_ARCSEC` (2.2).
  - TRICERATOPS 1.1.0's `target.remove_star(drop_stars)`, which drops the rows of `.stars` whose `ID` is in the list and leaves the target's `fluxratio`; `calc_probs` reads `.stars` by position (`.values`), so nothing else needs resetting.
- Produces:
```python
# src/skysift/crosscheck.py
EXCLUDE_SIGMA = 3.0                 # spec 12.2: nearer the target than the neighbour by more than this, every season
STAMP_MATCH_ARCSEC = 1.0            # rung 5's own neighbour: the Gaia neighbour nearest the stamp separation, within this
EXCLUDE_RULE = ("a neighbour is excluded when, in every voting season of rung 5, the difference-image centroid lies "
                "nearer the target than the neighbour by more than 3 sigma: for the neighbour rung 5 fitted, its own "
                "vote (separation_sigma, nearer the target); for every other Gaia neighbour the lower bound (D / 2 - "
                "dist_target_pix) / centroid_sigma_axis_pix on the same bisector statistic, D its separation in "
                "Kepler pixels, which holds whatever its direction")
VARIANT_KEYS = ("n_runs", "n_degenerate", *SUMMARY_KEYS, "runs", "scenarios", "removed", "same_as_all", "seeds",
                "median_run_seed")
RESULT_KEYS = (*the Task 14 keys, "variant_excluded")
def stamp_neighbour(neighbours: list[dict], seasons: list[dict]) -> str | None
    # the source_id of the Gaia neighbour rung 5 fitted: the median over the voting seasons of
    # |pos_neighbour - pos_target| x PIXEL_ARCSEC is the stamp separation; the neighbour whose sep_arcsec is nearest
    # it, within STAMP_MATCH_ARCSEC, ties to the brighter G; None without positions or a match
def season_margin(season: dict, sep_arcsec: float, stamp: bool) -> tuple[float | None, str, str | None]
    # (margin in sigma, basis, why None). stamp: separation_sigma, positive when nearer is "target", negative when
    # "neighbour", basis "rung 5 vote"; None with "season <n> has no rung 5 vote against this neighbour" when the
    # value or nearer is missing. Otherwise basis "geometric bound": (sep_arcsec / PIXEL_ARCSEC / 2 -
    # dist_target_pix) / centroid_sigma_axis_pix; None with "season <n> has no centroid distance or error" when
    # either is missing or the error is not above 0
def pixel_excluded(data: dict) -> dict
    # {"rule": EXCLUDE_RULE, "sigma": EXCLUDE_SIGMA, "pixel_arcsec": PIXEL_ARCSEC, "seasons": [voting season
    #  numbers], "stamp_source_id", "neighbours": [{"source_id", "sep_arcsec", "gmag", "excluded", "margins":
    #  [{"season", "margin_sigma", "basis"}], "min_margin_sigma", "reason"}], "excluded_ids": [source ids in the
    #  stored order], "reason": None | "rung 5 has not run" | "rung 5 has no voting season"}.
    # A neighbour is excluded when there is a voting season and every margin is a number above EXCLUDE_SIGMA; its
    # reason is then None, else the first season's why ("season 3 margin 1.42 sigma, not above 3" for a number), or
    # "no separation stored"
# inputs.json gains "pixel_excluded": pixel_excluded(data)
def removed_phrase(variant: dict) -> str
    # "the one neighbour the difference images exclude" (1), "the <n> neighbours the difference images exclude" (n)
def excluded_text(block: dict, ranges: bool) -> str
    # the variant's four numbers in prose, used by headline and comparison_sentence (forms below)
def dominant_excluded_sentence(data: dict, tex: bool = False, short: bool = False) -> str | None
    # the scenario with the largest probability in the variant's median-run table, other than TP, PTP and DTP
    # (dominant_scenario of the variant block). For STP, the long form is "With those neighbours removed the
    # false-positive weight sits mainly on STP, a planet transiting an unresolved bound companion inside the
    # <r:.2f> arcsec exclusion radius, which no high-resolution imaging constrains, so the signal stays a
    # candidate." and the short form "The remaining weight is mainly STP, a planet on an unresolved bound companion
    # inside the <r:.2f> arcsec exclusion radius, which no imaging constrains, so the signal stays a candidate."
    # r is centroid_offset.exclusion_radius_arcsec; without it, "within 2.2 arcsec" (UNRESOLVED_COMPANION_ARCSEC)
    # replaces "inside the r arcsec exclusion radius". Another scenario: "... sits mainly on <id>, <SCENARIO_WORDS>
    # ..., so the signal stays a candidate." None without a current, complete cross-check or without such a scenario
# with_current(): "class_excluded": classify(variant fpp, variant nfpp) for a complete block

# tools/triceratops_check.py
VARIANT_KEYS = (...)                # skysift.crosscheck.VARIANT_KEYS
def excluded_stars(pixel_excluded: dict | None, matched: list[dict], added: dict) -> tuple[list[dict], list[str]]
    # ([{"tic_id", "source_id", "sep_arcsec", "min_margin_sigma"}] in the export's order: the TIC id of each
    #  excluded Gaia neighbour from match_neighbours, or its own id when the tool added it), [one note per excluded
    #  neighbour in neither: "Gaia DR3 <id> is excluded by the difference images but is not in the run's star list,
    #  so there is nothing to remove."]); ([], []) without the block or an excluded id
def variant_block(runs: list[dict], removed: list[dict], same_as_all: bool) -> dict
    # keyed VARIANT_KEYS: summarise(runs), the runs (RUN_KEYS), the scenario table of the non-degenerate run nearest
    # its median FPP, the removed stars, same_as_all, the seeds and that run's seed
def assemble_result(runs, stars, settings, inputs, version, *, variant: dict) -> dict      # + "variant_excluded"
def checkpoint_key(..., excluded_tic_ids: list[str] | None = None) -> dict   # + "excluded_tic_ids": sorted ids
# new_state(): + "runs_excluded": []; checkpoint_runs(): the larger of the two sets' lengths; resume_state(): the
# second set's seeds must also run on from the first seed, and a non-empty second set needs a first set at least as
# long ("the checkpoint's second set runs ahead of its first")

# src/skysift/dossier.py
DOSSIER_VERSION = "6"   # 6: round 3 (spec 12): the extinction block, the four cross-check numbers, the sharing rows
# page_texts(): + "excluded": cc.dominant_excluded_sentence(data)
# src/skysift/pack.py
PACK_VERSION = "5"      # 5: round 3 (spec 12): the pixel-excluded variant and the dust-map extinction
```

The texts' forms (`block` a current, complete cross-check, `v = block["variant_excluded"]`, numbers in `probability_text`):
- `headline` (every text but the note's abstract): "TRICERATOPS <ver> gives a false-positive probability of <fpp> (<fpp_no_stp> without a planet on an unresolved bound companion), and <v.fpp> (<v.fpp_no_stp> without it) with <removed_phrase> removed, in none of its validation classes, which do not apply at 30-minute cadence (Giacalone et al. 2021)". With `same_as_all`, the middle reads "and the same with no neighbour removed, since the difference images exclude none".
- `headline(short=True)` (the abstract): "TRICERATOPS <ver> gives a false-positive probability of <fpp> (<fpp_no_stp> without a planet on an unresolved bound companion; <v.fpp> and <v.fpp_no_stp> with <removed_phrase> removed; no validation class applies at 30-minute cadence; Giacalone et al. 2021)".
- `comparison_sentence(after_headline=True)` (the README, the ExoFOP note and the archive email): the round-2 sentence, then "With <removed_phrase> removed (<per neighbour: Gaia DR3 <id> at <sep:.2f> arcsec, smallest margin <m:.3g> sigma>, joined by "; "), <v.n_runs> seeded runs give <v.fpp> (16th to 84th percentile <range>), <v.fpp_no_stp> (<range>) without STP and a nearby false-positive probability of <v.nfpp> (<range>)." and then `dominant_excluded_sentence(data)`.
- `comparison_sentence(short=True)` (the note): the round-2 short sentence, then "With <removed_phrase> removed it gives <v.fpp>, and <v.fpp_no_stp> without STP." The ranges stay in the README, as Task 13 ruled for the note's word limit.
- The long `comparison_sentence` (the dossier page's `cross_check` text) adds the after-headline variant sentence with its ranges.
- The note's `both_tools` slot prints `dominant_excluded_sentence(data, tex=True, short=True)` in place of `BOTH_TOOLS`.

- [ ] **Step 1: Write the failing exclusion and tool tests** in `tests/unit/test_crosscheck.py`. The margins are those of KIC 6146676's live seasons (Task 14's `LIVE_SEASONS`). The 7.14 arcsec star's are (7.1422442 / 3.98 / 2 - `dist_target_pix`) / `centroid_sigma_axis_pix` per season.

```python
def test_pixel_excluded_takes_rung_5s_vote_for_its_neighbour_and_the_bound_for_the_others():
    out = cc.pixel_excluded(_live())
    assert out["seasons"] == [0, 1, 2, 3] and out["reason"] is None and out["sigma"] == 3.0
    assert out["stamp_source_id"] == "2073896190925687296"          # 11.45 arcsec against the stamp's 11.53
    rows = {n["source_id"]: n for n in out["neighbours"]}
    stamp = rows["2073896190925687296"]
    assert [m["basis"] for m in stamp["margins"]] == ["rung 5 vote"] * 4
    assert [m["margin_sigma"] for m in stamp["margins"]] == pytest.approx([27.845078, 19.814692, 17.424733,
                                                                           12.657602])
    near = rows["2073896294004902144"]                                # 7.14 arcsec: D / 2 = 0.8973 px
    assert [m["basis"] for m in near["margins"]] == ["geometric bound"] * 4
    assert [m["margin_sigma"] for m in near["margins"]] == pytest.approx([12.12, 10.33, 9.93, 7.07], abs=0.01)
    assert near["min_margin_sigma"] == pytest.approx(7.07, abs=0.01) and near["reason"] is None
    # 10.94 arcsec lies within 1 arcsec of the stamp separation too, but 11.45 is nearer: the bound for it
    other = rows["2073896289692192128"]["margins"][0]
    assert other["basis"] == "geometric bound" and other["margin_sigma"] == pytest.approx(20.41, abs=0.01)
    assert out["excluded_ids"] == [n["source_id"] for n in LIVE_NEIGHBOURS]
    assert all(n["excluded"] for n in rows.values()) and out["rule"] == cc.EXCLUDE_RULE


def test_a_season_without_a_vote_keeps_every_neighbour_and_no_voting_season_excludes_nothing():
    holed = [*LIVE_SEASONS[:3], {**LIVE_SEASONS[3], "dist_target_pix": None, "separation_sigma": None}]
    out = cc.pixel_excluded(_live(holed))
    assert out["excluded_ids"] == []
    rows = {n["source_id"]: n for n in out["neighbours"]}
    assert rows["2073896294004902144"]["reason"] == "season 3 has no centroid distance or error"
    assert rows["2073896190925687296"]["reason"] == "season 3 has no rung 5 vote against this neighbour"
    assert rows["2073896294004902144"]["excluded"] is False
    low = [*LIVE_SEASONS[:3], {**LIVE_SEASONS[3], "centroid_sigma_axis_pix": 0.5}]  # (0.8973 - 0.1866) / 0.5 = 1.42
    out = cc.pixel_excluded(_live(low))
    assert "2073896294004902144" not in out["excluded_ids"] and "2073896190925687296" in out["excluded_ids"]
    assert next(n for n in out["neighbours"] if n["source_id"] == "2073896294004902144")["reason"] == (
        "season 3 margin 1.42 sigma, not above 3")
    none = cc.pixel_excluded(_live([{**s, "voting": False} for s in LIVE_SEASONS]))
    assert none["excluded_ids"] == [] and none["reason"] == "rung 5 has no voting season"
    assert cc.pixel_excluded({"tests": [], "fpp": {"inputs": {"neighbours": LIVE_NEIGHBOURS}}})["reason"] == (
        "rung 5 has not run")


def test_the_export_lists_the_pixel_excluded_neighbours(env, tmp_path):
    cc.export_inputs(env.store, env.blob, env.settings, STAGE2_TARGET, tmp_path / "cc", fetch=env.xfetch)
    inputs = json.loads((tmp_path / "cc" / "inputs.json").read_text(encoding="utf-8"))
    assert inputs["pixel_excluded"] == cc.pixel_excluded(_stored(env))


def test_tool_maps_the_excluded_neighbours_to_the_stars_it_removes():
    from tools import triceratops_check as tc
    px = {"excluded_ids": ["g1", "g2", "g3"],
          "neighbours": [{"source_id": "g1", "sep_arcsec": 7.14, "min_margin_sigma": 7.07},
                         {"source_id": "g2", "sep_arcsec": 11.45, "min_margin_sigma": 12.66},
                         {"source_id": "g3", "sep_arcsec": 18.6, "min_margin_sigma": 21.4}]}
    removed, notes = tc.excluded_stars(px, [{"source_id": "g2", "tic_id": "169822655", "dsep_arcsec": 0.24}],
                                       {"g1": 19.3})
    assert removed == [{"tic_id": "g1", "source_id": "g1", "sep_arcsec": 7.14, "min_margin_sigma": 7.07},
                       {"tic_id": "169822655", "source_id": "g2", "sep_arcsec": 11.45, "min_margin_sigma": 12.66}]
    assert notes == ["Gaia DR3 g3 is excluded by the difference images but is not in the run's star list, so there "
                     "is nothing to remove."]
    assert tc.excluded_stars(None, [], {}) == ([], []) and tc.excluded_stars({"excluded_ids": []}, [], {}) == ([], [])
    assert tc.VARIANT_KEYS == cc.VARIANT_KEYS


def test_tool_result_carries_the_pixel_excluded_variant_and_passes_the_import():
    from tools import triceratops_check as tc
    table = [{"ID": "t", "scenario": "TP", "prob": 0.92}, {"ID": "t", "scenario": "STP", "prob": 0.05}]
    runs = [tc.run_record(1 + k, 0.08, 0.02, False, table) for k in range(20)]
    ex = [tc.run_record(1 + k, 0.055, 0.0, False, [{"ID": "t", "scenario": "TP", "prob": 0.945},
                                                    {"ID": "t", "scenario": "STP", "prob": 0.05}]) for k in range(20)]
    removed = [{"tic_id": "169822655", "source_id": "g2", "sep_arcsec": 11.45, "min_margin_sigma": 12.66}]
    settings = {"N": 1_000_000, "field_stars": {"file": "p.csv", "n_rows": 10, "sha256": "c" * 64}}
    result = tc.assemble_result(runs, [], settings, {"fingerprint": "a" * 64}, "1.1.0",
                                variant=tc.variant_block(ex, removed, False))
    assert result["schema"] == 3 and tuple(result) == cc.RESULT_KEYS
    v = result["variant_excluded"]
    assert tuple(v) == cc.VARIANT_KEYS and v["removed"] == removed and v["seeds"] == list(range(1, 21))
    assert v["fpp"] == pytest.approx(0.055) and v["nfpp"] == 0.0 and v["same_as_all"] is False
    cc.validate_result(result)
    same = tc.assemble_result(runs, [], settings, {"fingerprint": "a" * 64}, "1.1.0",
                              variant=tc.variant_block(runs, [], True))
    assert same["variant_excluded"]["fpp"] == same["fpp"] and same["variant_excluded"]["removed"] == []
    cc.validate_result(same)


def test_tool_checkpoint_resumes_the_second_set(tmp_path):
    from tools import triceratops_check as tc
    key = tc.checkpoint_key("a" * 64, seed=1, draws=2000, check_draws=2000, parallel_check=True, flux_err=0.001,
                            shares={"source": tc.OVERLAP}, quarters=[1], allow_old_export=False, version="1.1.0",
                            excluded_tic_ids=["1879962333", "169822655"])
    assert key["excluded_tic_ids"] == ["169822655", "1879962333"]                         # sorted
    state = tc.new_state()
    assert state["runs_excluded"] == []
    state["runs"] = [tc.run_record(1 + k, 0.1, 1e-4, False, []) for k in range(3)]
    state["runs_excluded"] = [tc.run_record(1 + k, 0.05, 0.0, False, []) for k in range(2)]
    part = tmp_path / "r.partial.json"
    tc.write_partial(part, key, state)
    back = tc.resume_state(part, key)
    assert back["runs_excluded"] == state["runs_excluded"] and tc.checkpoint_runs(part) == 3
    with pytest.raises(ValueError, match=r"\(excluded_tic_ids differ\)"):
        tc.resume_state(part, {**key, "excluded_tic_ids": []})
    tc.write_partial(part, key, {**state, "runs": state["runs"][:1]})
    with pytest.raises(ValueError, match="the checkpoint's second set runs ahead of its first"):
        tc.resume_state(part, key)
```

- [ ] **Step 2: Write the failing import and text tests** in `tests/unit/test_crosscheck.py`. `fake_variant` and the new `fake_result` go beside `fake_runs` (lines 72 to 92); every other test that builds a result keeps working through the default.

```python
REMOVED = [{"tic_id": "169822655", "source_id": "2073896190925687296", "sep_arcsec": 11.452381,
            "min_margin_sigma": 12.657602}]


def fake_variant(fpp: float = 0.004, nfpp: float = 0.0, n: int = cc.N_RUNS, p_stp: float = 0.003,
                 removed: list | None = None, scenarios: list | None = None) -> dict:
    """The pixel-excluded run set as the tool writes it: ``n`` identical runs, seeds 1 to n."""
    runs = fake_runs(fpp, nfpp, n, p_stp)
    return {"n_runs": n, "n_degenerate": 0, **cc.summarise(runs), "runs": runs,
            "scenarios": scenarios or [{"ID": "169822650", "scenario": "TP", "prob": 1 - fpp},
                                       {"ID": "169822650", "scenario": "STP", "prob": p_stp}],
            "removed": REMOVED if removed is None else removed, "same_as_all": False,
            "seeds": [r["seed"] for r in runs], "median_run_seed": 1}

# fake_result(sha, fpp=0.012, nfpp=1e-4, n=cc.N_RUNS, p_stp=0.0, scenarios=None, variant=None) returns the dict of
# Task 14 with "variant_excluded": variant if variant is not None else fake_variant(n=n), keyed cc.RESULT_KEYS


def test_the_import_requires_the_pixel_excluded_variant():
    good = fake_result("a" * 64)
    cc.validate_result(good)
    with pytest.raises(cc.BadResult, match="the result file lacks variant_excluded"):
        cc.validate_result({k: v for k, v in good.items() if k != "variant_excluded"})
    v = good["variant_excluded"]
    with pytest.raises(cc.BadResult, match="variant_excluded's seeds are not the first set's"):
        cc.validate_result({**good, "variant_excluded": {**v, "seeds": list(range(2, 22))}})
    with pytest.raises(cc.BadResult, match="variant_excluded says same_as_all but its runs differ from the first "
                                           "set's"):
        cc.validate_result({**good, "variant_excluded": {**v, "same_as_all": True}})
    with pytest.raises(cc.BadResult, match=r"variant_excluded\.fpp 0\.5 is not the non-degenerate runs' value"):
        cc.validate_result({**good, "variant_excluded": {**v, "fpp": 0.5}})
    with pytest.raises(cc.BadResult, match="variant_excluded.removed must list"):
        cc.validate_result({**good, "variant_excluded": {**v, "removed": [{"tic_id": "1"}]}})
    stored = cc.with_current({**good, "current": True}, DATA)
    assert stored["class_excluded"] == cc.classify(v["fpp"], v["nfpp"])


def _four(fpp_ex: float = 0.055, nfpp_ex: float = 0.0, removed=None, same: bool = False) -> dict:
    sha = cc.inputs_fingerprint(DATA)
    variant = fake_variant(fpp=fpp_ex, nfpp=nfpp_ex, p_stp=0.05, removed=removed)
    if same:
        variant = {**fake_variant(fpp=0.08, nfpp=0.02, p_stp=0.05, removed=[]), "same_as_all": True}
    block = {**fake_result(sha, fpp=0.08, nfpp=0.02, p_stp=0.05, variant=variant), "current": True}
    return {**DATA, "fpp": {"value": 0.0035}, "cross_check": block,
            "centroid_offset": {"exclusion_radius_arcsec": 0.83480394}}


def test_every_text_carries_the_four_numbers():
    data = _four()
    assert cc.headline(data) == (
        "TRICERATOPS 1.1.0 gives a false-positive probability of 0.080 (0.032 without a planet on an unresolved bound "
        "companion), and 0.055 (0.0053 without it) with the one neighbour the difference images exclude removed, in "
        "none of its validation classes, which do not apply at 30-minute cadence (Giacalone et al. 2021)")
    assert cc.headline(data, short=True).endswith(
        "(0.032 without a planet on an unresolved bound companion; 0.055 and 0.0053 with the one neighbour the "
        "difference images exclude removed; no validation class applies at 30-minute cadence; Giacalone et al. 2021)")
    after = cc.comparison_sentence(data, after_headline=True)
    assert ("With the one neighbour the difference images exclude removed (Gaia DR3 2073896190925687296 at 11.45 "
            "arcsec, smallest margin 12.7 sigma), 20 seeded runs give 0.055 (16th to 84th percentile 0.055 to "
            "0.055), 0.0053 (0.0053 to 0.0053) without STP and a nearby false-positive probability of 0 (0 to "
            "0).") in after
    assert after.endswith(
        "With those neighbours removed the false-positive weight sits mainly on STP, a planet transiting an "
        "unresolved bound companion inside the 0.83 arcsec exclusion radius, which no high-resolution imaging "
        "constrains, so the signal stays a candidate.")
    assert "With the one neighbour the difference images exclude removed it gives 0.055, and 0.0053 without STP." \
        in cc.comparison_sentence(data, short=True)
    assert cc.dominant_excluded_sentence(data, short=True) == (
        "The remaining weight is mainly STP, a planet on an unresolved bound companion inside the 0.83 arcsec "
        "exclusion radius, which no imaging constrains, so the signal stays a candidate.")
    six = _four(removed=REMOVED * 6)
    assert "with the 6 neighbours the difference images exclude removed" in cc.headline(six)
    assert "and the same with no neighbour removed, since the difference images exclude none" in cc.headline(
        _four(same=True))
    assert cc.dominant_excluded_sentence({**data, "cross_check": {**data["cross_check"], "current": False}}) is None
    no_radius = {**data, "centroid_offset": None}
    assert "within 2.2 arcsec, which no high-resolution imaging" in cc.dominant_excluded_sentence(no_radius)
    assert "\u2014" not in after
```

- [ ] **Step 3: Write the failing pack, Zenodo, dossier and page tests.**
  - In `tests/unit/test_pack.py` (helpers `_store_cross_check`, `_stored_with_cross_check`, `_build`, `_texts`, `render_note`), with a cross-check stored by `_store_cross_check(env)`:
    - The note carries `str(cross_check.comparison_sentence(data, tex=True, short=True))` and `str(cross_check.dominant_excluded_sentence(data, tex=True, short=True))`. It no longer carries "a run without them is a follow-up".
    - The README carries `cross_check.comparison_sentence(data, after_headline=True)` whole.
    - `exofop/note.txt` and `archive_email.txt` each carry "with the one neighbour the difference images exclude removed".
    - The `PACK_VERSION` assertion reads "5" through the constant.
  - In `tests/unit/test_zenodo.py`: `probability_sentences(data)[1]` of a dossier with a current cross-check (the existing fixture) contains "with the one neighbour the difference images exclude removed".
  - In `tests/unit/test_dossier.py`: the version test reads `d["dossier_version"] == "6" == DOSSIER_VERSION`, and `page_texts(data)["excluded"]` equals `cc.dominant_excluded_sentence(data)`.
  - In `tests/unit/test_ui_dossiers.py`, `test_dossier_page_shows_the_cross_check_block` (line 771) also checks the served page and the standalone page. Each shows "with the one neighbour the difference images exclude removed" and the variant's FPP spread `0.004 (16th to 84th percentile 0.004 to 0.004)`, in the page's `g3` form.
  - `_live_shape` (test_pack.py lines 2104 to 2147) builds its block with `fake_result(..., variant=fake_variant(fpp=0.004))` for "validated" and `fake_variant(fpp=0.31, nfpp=0.0, p_stp=0.3, removed=REMOVED * 6)` for "dominant". It sets `data["centroid_offset"]["exclusion_radius_arcsec"]` from the golden, so both long forms of the variant reach the count. `_check_live_shape` asserts the short variant sentence and the dominant-excluded sentence are in the note.

- [ ] **Step 4: Run them to see them fail.**
  - Command: `PYTHONUTF8=1 PYTHONPATH=src C:\Research\skysift\.venv\Scripts\python.exe -m pytest tests/unit/test_crosscheck.py tests/unit/test_dossier.py tests/unit/test_pack.py tests/unit/test_zenodo.py tests/unit/test_ui_dossiers.py -q -p no:cacheprovider`.
  - Expected: `AttributeError: module 'skysift.crosscheck' has no attribute 'pixel_excluded'` (and `VARIANT_KEYS`, `dominant_excluded_sentence`); `TypeError: assemble_result() got an unexpected keyword argument 'variant'`; the version pins fail on "5"; the text assertions fail on the missing variant clause.

- [ ] **Step 5: The exclusion** in `src/skysift/crosscheck.py`.
  - `stamp_neighbour`, `season_margin` and `pixel_excluded` as specified. Voting seasons are `ds.season_votes(s)` over `ds._pixel_seasons(metrics)`. The stamp separation is the median of `float(np.hypot(*(pn - pt))) * PIXEL_ARCSEC` over the voting seasons where `ds._pair` gives both positions. Margins are rounded nowhere, and the JSON keeps 8 significant figures as everywhere.
  - A season's "why" for a margin at or below 3 reads `f"season {n} margin {m:.2f} sigma, not above {EXCLUDE_SIGMA:g}"`.
  - `export_inputs` writes `"pixel_excluded": pixel_excluded(data)` after `"pixel_exclusion"`.
  - The module docstring gains one paragraph: the third variant, the rule, and why it does not make the cross-check circular. The first set stands beside it unchanged, and the variant is the run Giacalone et al. 2021 allow once a centroid analysis rules a star out.

- [ ] **Step 6: The second run set** in `tools/triceratops_check.py`.
  - `excluded_stars` and `variant_block` as specified. `variant_block` takes its scenario table and median seed as `assemble_result` takes them today; factor that code into `_median_run(runs) -> int`, the index of the non-degenerate run nearest the median FPP, and use it in both.
  - `export_problems` appends "the export has no pixel_excluded key" when the key is absent.
  - In `main`, after the `matched` and `added` steps, call `removed, ex_notes = excluded_stars(inputs.get("pixel_excluded"), matched, added)` and add `ex_notes` to `notes`. Pass `excluded_tic_ids=[r["tic_id"] for r in removed]` to `checkpoint_key`.
  - After the first set's loop (line 968), when `removed` is empty, `variant = variant_block(runs, [], True)` and nothing more runs.
  - Otherwise:
    - `t.remove_star([r["tic_id"] for r in removed])`, then the same loop for `k in range(len(state["runs_excluded"]), a.runs)` with seed `a.seed + k`, the same `probs` and the same parallel fallback, appending to `state["runs_excluded"]` and writing the checkpoint after each run.
    - Print `f"pixel-excluded run {k + 1}/{a.runs} (seed {seed}): FPP ..., NFPP ..."`.
    - `variant = variant_block(state["runs_excluded"], removed, False)`.
  - `notes` gains "The pixel-excluded variant removes <n> neighbour(s) (TIC <ids>) with remove_star before calc_probs and runs the same seeds; Giacalone et al. 2021 allow removing a star a centroid analysis rules out, and the margins are in the export's pixel_excluded block." or, with none removed, "The difference images exclude no neighbour, so the pixel-excluded variant is the first set."
  - The last item of `CAVEATS` becomes "The first run set removes no star. The pixel-excluded variant removes the neighbours rung 5's difference images exclude by more than 3 sigma in every voting season (spec 12.2), so it rests on SkySift's own centroid test and is quoted beside the first set, never in its place."
  - The docstring's "Third variant" paragraph says the same, with the resume rule: `--resume` covers both sets, and the second starts only once the first holds `--runs` runs.
  - `checkpoint_runs` returns `max(len(runs), len(runs_excluded))`. `resume_state` checks `runs_excluded` as it checks `runs`, and refuses a second set longer than the first.

- [ ] **Step 7: The import** in `src/skysift/crosscheck.py`.
  - `RESULT_KEYS = (*Task 14's, "variant_excluded")`.
  - `_validate_variant(result)` raises `BadResult` in this order:
    1. "variant_excluded must be an object holding <VARIANT_KEYS>".
    2. `n_runs` other than the first set's: "variant_excluded.n_runs <n> is not the first set's <m>".
    3. Each run through `_validate_run` with the prefix "variant_excluded run <i>".
    4. "variant_excluded's seeds are not the first set's" when `seeds` or the runs' seeds differ from the first set's.
    5. Its `n_degenerate`, every run degenerate, and each `SUMMARY_KEYS` value against `summarise(v["runs"])`: "variant_excluded.<key> <value> is not the non-degenerate runs' value <expected>".
    6. `removed` not a list of objects each with a string `tic_id` and `source_id`: "variant_excluded.removed must list {tic_id, source_id} objects".
    7. `same_as_all` not a bool, or True with an empty `removed` whose runs differ from the first set's: "variant_excluded says same_as_all but its runs differ from the first set's".
  - `with_current` adds `"class_excluded"`. `complete` is unchanged, so a round-2 block reads as stored by an earlier version.

- [ ] **Step 8: The texts.**
  - `removed_phrase`, `excluded_text`, `dominant_excluded_sentence`, and the new `headline` and `comparison_sentence` forms, exactly as the forms list above. The removed list prints in the export's order.
  - `pack.both_tools` returns `cc.dominant_excluded_sentence(data, tex=tex, short=True)` when `cross_check_sentence(data)` is not None and the pixel rung voted, else None; `numbers()` passes `tex`. `BOTH_TOOLS` and its comment go.
  - `README.md.j2` lines 98 to 104 already print `n.cross_check_after`, which now carries the variant. The README's file note for `data/cross_check.json` (pack.py line 2056) becomes "the TRICERATOPS cross-check: every run's false-positive probability with all neighbours and with the neighbours the difference images exclude removed, ...", the rest as it is.
  - `zenodo.probability_sentences` reads `headline` and needs no change. Check it in the test.
  - `dossier.page_texts` gains `"excluded"`.
  - `dossier.html` (after line 244) and `dossier.js` (after line 534) print the variant on the page when `cc.variant_excluded` exists: "With <removed_phrase> removed, over <n> runs: median false-positive probability <spread>, <spread> without STP, and nearby false-positive probability <spread>." In the page, `removed_phrase` is written out as "the one neighbour the difference images exclude" or "the N neighbours the difference images exclude" from `variant_excluded.removed.length`. That sentence is followed by `texts.excluded` (`PT.excluded`), and each removed neighbour goes in a small list with its Gaia id, separation and smallest margin.
  - `DOSSIER_VERSION = "6"` with the comment of the interface block, and `PACK_VERSION = "5"`.

- [ ] **Step 9: The word budget.** Run `PYTHONUTF8=1 PYTHONPATH=src C:\Research\skysift\.venv\Scripts\python.exe -m pytest tests/unit/test_pack.py -k live_shape -vv -p no:cacheprovider` and note the four counts. Cuts, if any count passes 1,480, wait for Task 16 Step 13, which measures after the reddening clause too; this task records the counts only.

- [ ] **Step 10: Run the tests** (all pass).
  - `PYTHONUTF8=1 PYTHONPATH=src C:\Research\skysift\.venv\Scripts\python.exe -m pytest tests/unit/test_crosscheck.py tests/unit/test_dossier.py tests/unit/test_pack.py tests/unit/test_zenodo.py tests/unit/test_ui_dossiers.py -q -p no:cacheprovider`
  - `PYTHONUTF8=1 PYTHONPATH=src C:\Research\skysift\.venv\Scripts\python.exe -m ruff check src tests tools`

- [ ] **Step 11: Regenerate the golden** with Task 10's helper, from the engine root: `PYTHONUTF8=1 PYTHONPATH=".worktrees/referee-pack/src;.worktrees/referee-pack" .venv/Scripts/python.exe .worktrees/referee-pack/tests/golden/regenerate.py --engine-root C:/Research/skysift --data-dir .superpowers/sample-run --target kic-006146676 --out .worktrees/referee-pack/tests/golden/kic-006146676-generated.json`.
  - The helper copies the live store read-only and never writes to it.
  - The golden's `cross_check` is the stored round-2 block. It is now not `complete`, and the golden test (`-m golden`) must say so rather than fail on it. Run `PYTHONUTF8=1 PYTHONPATH=src C:\Research\skysift\.venv\Scripts\python.exe -m pytest tests/unit/test_dossier.py -m golden -q -p no:cacheprovider`.
  - This step regenerates before Task 16 exists, so the golden has no extinction block yet. Task 16 regenerates it again.

- [ ] **Step 12: Commit.**

```
git add tools/triceratops_check.py src/skysift/crosscheck.py src/skysift/dossier.py src/skysift/pack.py src/skysift/templates/pack/note.tex.j2 src/skysift/templates/pack/README.md.j2 src/skysift/ui/templates/dossier.html src/skysift/ui/static/dossier.js tests/golden/kic-006146676-generated.json tests/unit/test_crosscheck.py tests/unit/test_dossier.py tests/unit/test_pack.py tests/unit/test_zenodo.py tests/unit/test_ui_dossiers.py
git commit -m "feat(crosscheck): the pixel-excluded variant, a second seeded run set with remove_star, four numbers in every text (dossier version 6, pack version 5)

Live-shape words: <four counts>.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 16: Extinction from Bayestar19, stored per target, through the Gaia DR3 law and the draws (spec 12.3)

**Files:**
- Create: `tools/extinction.py`, run under `.venv-triceratops` (`-I`). Its pure helpers need numpy and astropy only; `h5py` is imported inside `main`.
- Create: `docs/candidates/<target>/extinction.json`, one per live target, written by the tool in Step 8: `kic-005689121`, `kic-006146676`, `kic-006699887`, `kic-009243078` and `kic-010352243`. The 3480 copy is the second period of `kic-006699887` and shares its file.
- Modify: `src/skysift/stars.py`.
  - After line 50: the extinction constants and the Gaia (E)DR3 coefficients.
  - Lines 128 to 147: `StarParams` gains `extinction` and `unreddened` (defaults None, at the end).
  - New `ExtinctionError`, `NoExtinction`, `gaia_k`, `gaia_extinction`, `load_extinction`, `_split_normal`, before `derive`.
  - Lines 420 to 541: `derive(..., extinction=None)`.
- Modify: `src/skysift/dossier.py`.
  - Line 1648: `EXTINCTION_DIR` beside `CANDIDATES_DIR`.
  - Lines 309 to 323: `fingerprint(..., extinction_ebv=None)`.
  - Lines 1113 to 1137: `_lookup(..., extinction)` refuses.
  - Lines 1377 to 1381: `no_spectrum_title`.
  - Lines 1764 to 1773: `_rederive` passes the extinction.
  - Lines 1817 to 1903: `generate` reads the file first.
- Modify: `src/skysift/cli.py` (line 289: `ds.NoExtinction` and `ds.ExtinctionError` join the exit-2 errors; `dossier.py` re-exports both from `stars`).
- Modify: `src/skysift/pack.py`.
  - Lines 320 to 343: `references` gains `green2019`.
  - Line 346: `BIBLIOGRAPHY` gains `green2019`.
  - Lines 922 to 939: `README_REFERENCES` gains Cardelli et al. 1989, Fitzpatrick et al. 2019 and Riello et al. 2021.
  - Lines 1444 to 1468: `reddening_text` keyed on the adopted extinction.
  - Lines 1202 to 1208: `star_texts`.
  - Lines 1702 to 1808: `numbers` gains `reddening_plain` and `reddening_rows`.
  - Lines 1883 to 1913: `archive_paragraph`.
- Modify: `src/skysift/pack_rows.py` (lines 347 to 380: `EXTINCTION_ROWS`, `provenance_rows`, and a new `reddening_rows`).
- Modify: `src/skysift/zenodo.py` (lines 235 to 262: `metadata_from`'s paragraphs).
- Modify: the templates.
  - `note.tex.j2` line 41: the star sentence's reddening clause.
  - `README.md.j2` lines 53 to 55: the three-column table under the star paragraph.
  - `exofop_note.txt.j2` lines 19 to 21: the reddening line after the planet line.
- Modify: `src/skysift/ui/templates/dossier.html` (lines 398 to 404, the star block's reddening) and `src/skysift/ui/static/dossier.js` (line 629, `PT.star`).
- Test: `tests/unit/test_extinction.py` (new), `tests/unit/test_stars.py`, `tests/unit/test_dossier.py` (`_make_env`, lines 139 to 170, writes the fixture file for every seeded target), `tests/unit/test_pack.py`, `tests/unit/test_zenodo.py`, `tests/unit/test_ui_dossiers.py`, `tests/unit/test_cli.py`, `tests/unit/test_crosscheck.py` (its `env` uses `_make_env`, so nothing to change there), and the golden (regenerated in Step 12).

**Interfaces:**
- Consumes:
  - The stored dossier's `target.ra`, `target.dec`, `star.distance_pc.median` (1000 over the Lindegren-corrected parallax, spec 12.3) and `star.route`.
  - `stars.derive`'s draws: `colour`, `dm_s`, `ks_s`, `teff_s`, `radius_s`, `mass_s`; `_value`, `_interp`, `mamajek_labels`, `COLOUR_SIGMA`, `A_KS_MAX`.
  - `dossier.CANDIDATES_DIR`, `dossier._lookup`, `dossier._rederive`, `dossier.fingerprint`.
  - `pack.value_text`, `pack.star_characterized`, `pack._joined`, `pack_rows._split_source`.
  - The map file `bayestar2019.h5`: dataset `/pixel_info` (structured: `nside`, `healpix_index`, `DM_reliable_min`, `DM_reliable_max`, and `converged` when present) with attribute `DM_bin_edges`, and dataset `/samples` of shape (pixels, samples, distance bins). This is the layout `dustmaps.bayestar.BayestarQuery.__init__` reads.
- Produces:
```python
# tools/extinction.py
MAP_VERSION = "bayestar2019"
MAP_DOI = "10.7910/DVN/2EJ9TX"
MAP_URL = "https://dataverse.harvard.edu/api/access/datafile/3424724"
MAP_BYTES = 727_902_836
MAP_MD5 = "ab815d2fd3068d1b81a1bd61fb18a722"
BAYESTAR19_TO_EBV = 0.884           # E(B-V) = 0.884 x the Bayestar19 value (the Bayestar19 documentation's relation)
PERCENTILES = (16.0, 50.0, 84.0)
LIVE_TARGETS = ("kic-005689121", "kic-006146676", "kic-006699887", "kic-009243078", "kic-010352243")
EXTINCTION_FILE = "extinction.json"
STORE_HINT = ("store it by hand: tools/extinction.py --target {target} --store --ebv <E(B-V)> --ebv-p16 <16th> "
              "--ebv-p84 <84th> --distance-pc {distance} --source \"<where the value comes from>\"")
def galactic(ra_deg: float, dec_deg: float) -> tuple[float, float]       # astropy ICRS to Galactic (l, b), degrees
def ang2pix_nest(nside: int, theta: float, phi: float) -> int
    # HEALPix nested index (Gorski et al. 2005), theta = radians(90 - b), phi = radians(l), as dustmaps.lb2pix:
    # z = cos theta, tt = (phi mod 2 pi) x 2 / pi. Equatorial (|z| <= 2/3): t1 = nside (0.5 + tt), t2 = 0.75 nside z,
    # jp = int(t1 - t2), jm = int(t1 + t2), ifp = jp // nside, ifm = jm // nside, face = ifp | 4 when ifp == ifm,
    # ifp when ifp < ifm, else ifm + 8; ix = jm & (nside - 1), iy = nside - (jp & (nside - 1)) - 1. Polar:
    # ntt = min(int(tt), 3), tp = tt - ntt, tmp = nside sqrt(3 (1 - |z|)), jp = min(int(tp tmp), nside - 1),
    # jm = min(int((1 - tp) tmp), nside - 1); north: face ntt, ix = nside - jm - 1, iy = nside - jp - 1; south:
    # face ntt + 8, ix = jp, iy = jm. Index = face nside^2 + the bits of ix in the even and of iy in the odd places
def pixel_row(nside: np.ndarray, healpix_index: np.ndarray, l_deg: float, b_deg: float) -> int | None
    # the row of pixel_info whose (nside, healpix_index) holds the sightline, searching each nside level in
    # ascending order as dustmaps' _find_data_idx; None outside the map
def reddening_at(samples: np.ndarray, dm_edges: np.ndarray, distance_pc: float) -> np.ndarray
    # one value per sample, as BayestarQuery.query with a distance: dm = 5 log10(d_pc) - 5; below the first edge
    # 10^(0.2 (dm - dm_edges[0])) x samples[:, 0]; beyond the last samples[:, -1]; between edges k-1 and k linear in
    # dm: (1 - a) samples[:, k-1] + a samples[:, k], a = (dm - dm_edges[k-1]) / (dm_edges[k] - dm_edges[k-1])
def extinction_block(values: np.ndarray, *, distance_pc: float, l_deg: float, b_deg: float, nside: int,
                     reliable: bool, converged: bool | None, queried_utc: str) -> dict
    # E(B-V) = BAYESTAR19_TO_EBV x values; the 16th, 50th and 84th percentiles over the finite samples, rounded to
    # 4 decimals. Raises ValueError("the map gives no finite reddening on this sightline") when none is finite.
    # {"ebv", "ebv_p16", "ebv_p84", "source": "Bayestar19 (Green et al. 2019) at the Gaia distance, E(B-V) = 0.884 x
    #  the map's reddening, the 16th, 50th and 84th percentiles over its samples", "map": "bayestar2019
    #  (doi:10.7910/DVN/2EJ9TX, md5 ab815d2fd3068d1b81a1bd61fb18a722)", "queried_utc", "distance_pc", "l_deg",
    #  "b_deg", "nside", "reliable_distance": reliable, "converged", "n_samples"}
def store_path(root: Path, target_id: str) -> Path                       # root / target_id / EXTINCTION_FILE
def main(argv: list[str] | None = None) -> int
    # (--target T | --all) --data-dir D [--root R (default <repo>/docs/candidates)] (--map M | --store --ebv E
    #  --ebv-p16 L --ebv-p84 H --distance-pc P --source S) [--force]
    # exit 0: written, or "stored; nothing to do" when the file exists and --force is not given (the run never
    # queries again while the stored value is present), or "the star has no Gaia distance; nothing to store";
    # exit 2: the map is missing or not MAP_BYTES long, h5py is not importable, the file lacks a required name,
    # the sightline is outside the map, or no finite sample. Each prints STORE_HINT with the target and distance.

# src/skysift/stars.py
R_V = 3.1
GAIA_COLOUR_RANGE = (-0.06, 2.5)      # the fit's validity in (BP-RP)_0; outside it the colour is held at the edge
GAIA_A0_RANGE = (0.01, 20.0)          # the fit's validity in A_0 (documented, not enforced: below 0.01 is no dust)
GAIA_LAW = ("Gaia (E)DR3 extinction law, main-sequence fit (log g 4.5) in (BP-RP)_0 and A_0 on the Fitzpatrick "
            "et al. 2019 law (Riello et al. 2021 passbands): www.cosmos.esa.int/web/gaia/edr3-extinction-law, "
            "Fitz19_EDR3_MainSequence.csv")
# k = a1 + a2 X + a3 X^2 + a4 X^3 + a5 A0 + a6 A0^2 + a7 A0^3 + a8 A0 X + a9 A0 X^2 + a10 X A0^2, X = (BP-RP)_0
GAIA_K_G = (0.995969721536602, -0.159726460302015, 0.0122380738156057, 0.00090726555099859, -0.0377160263914123,
            0.00151347495244888, -2.52364537395142e-05, 0.0114522658102451, -0.000936914989014318,
            -0.000260296774134201)
GAIA_K_BP = (1.15363197483424, -0.0814012991657388, -0.036013023976704, 0.0192143585568966, -0.022397548243016,
             0.000840562680547171, -1.31018008013549e-05, 0.00660124080271006, -0.000882247501989453,
             -0.000111215755291684)
GAIA_K_RP = (0.66320787941067, -0.0179847164933981, 0.000493769449961458, -0.00267994405695751,
             -0.00651422146709376, 3.30179903473159e-05, 1.57894227641527e-06, -7.9800898337247e-05,
             0.000255679812110045, 1.10476584967393e-05)
GAIA_COLOUR_ITERATIONS = 10
A_KS_OVER_A0 = 0.117                  # Cardelli, Clayton and Mathis 1989 at R_V 3.1 and the 2MASS Ks 2.159 um
EXTINCTION_SEED = 20261023            # the E(B-V) draws, a generator of their own, so the other draws do not move
EXTINCTION_FILE = "extinction.json"
EXTINCTION_FIELDS = ("ebv", "ebv_p16", "ebv_p84", "source", "map", "queried_utc", "distance_pc")
class ExtinctionError(ValueError): ...        # a stored file that is not a valid block, naming what is wrong
class NoExtinction(LookupError): ...          # a characterised star without a stored file
def gaia_k(coeffs, x, a0)                      # the polynomial above; scalars or numpy arrays
def gaia_extinction(ebv, bp_rp_observed: float) -> dict
    # A_0 = R_V E(B-V); X_0 starts at the observed colour and, GAIA_COLOUR_ITERATIONS times, X_0 = observed -
    # A_0 (k_BP - k_RP) at X_0 held in GAIA_COLOUR_RANGE; then {"ebv", "a0", "bp_rp0", "colour_held" (X_0 outside
    # the range), "k_g", "k_bp", "k_rp", "ebprp": A_0 (k_BP - k_RP), "a_g": A_0 k_G, "a_ks": A_KS_OVER_A0 A_0}, every
    # k at the held X_0. ebv may be a numpy array (the draws): then each value is an array, colour_held a bool array
def load_extinction(target_id: str, root: Path) -> dict | None
    # root / target_id / EXTINCTION_FILE as a dict, or None without the file; ExtinctionError naming the first
    # problem: not a JSON object, a field of EXTINCTION_FIELDS missing, a number not finite, not
    # 0 <= ebv_p16 <= ebv <= ebv_p84, or an empty source
def _split_normal(rng, median: float, low: float, high: float, n: int) -> np.ndarray
    # median + z (median - low) for z < 0 and z (high - median) for z >= 0, z standard normal, held at 0 and above
def derive(gaia, period, fit_k, depth, rng, extinction: dict | None = None) -> tuple[StarParams, PlanetParams]
    # extinction None: unchanged. With a block, before any draw the unreddened pair is derive(gaia, ..., deepcopy(rng),
    # None), so it carries exactly the version-5 numbers. Then the same draws in the same order, but:
    #  - e = gaia_extinction(_split_normal(default_rng(EXTINCTION_SEED), ebv, ebv_p16, ebv_p84, N_DRAWS), bp_rp);
    #  - colour = rng.normal(bp_rp, COLOUR_SIGMA, N_DRAWS) - e["ebprp"] (the 0.10 width stays);
    #  - mks = ks_s - dm_s - e["a_ks"] in place of the uniform 0 to A_KS_MAX draw;
    #  - abs_g = G - dm0 - median(a_g); the dwarf sequence, the table check and the spectral type at
    #    median(bp_rp0);
    #  - the source strings: Teff "Mamajek dwarf table at the dereddened BP-RP <x0:.3f> +- 0.10 (<ref>), E(BP-RP)
    #    <e:.3f> from Bayestar19"; Ks "2MASS Ks <ks:.3f>, A_Ks <a:.3f> from Bayestar19, Gaia parallax"; and the
    #    reddening_note "Teff and spectral type use the BP-RP <bp_rp:.2f> dereddened by the Bayestar19 E(BP-RP) of
    #    <e:.3f> (E(B-V) <ebv:.3f> at <d:.0f> pc); if E(BP-RP) were 0.2 the dwarf-table radius would be <alt:.3f>
    #    Rsun.".
    # StarParams.extinction = {**the stored block, "r_v": R_V, "law": GAIA_LAW, "a_ks_over_a0": A_KS_OVER_A0,
    #   "bp_rp0": median, "colour_held": any held, "a0", "ebprp", "a_g", "a_ks": _value(draws, 3, ...).as_dict()}
    # StarParams.unreddened = {"route", "bp_rp0": bp_rp, "teff", "radius", "mass": the unreddened Value dicts,
    #   "rp_rearth", "insolation_earth": the unreddened planet's Value dicts (None where absent)}

# src/skysift/dossier.py
EXTINCTION_DIR = CANDIDATES_DIR       # docs/candidates/<target>/extinction.json; tests point it at their own folder
NoExtinction, ExtinctionError = stars.NoExtinction, stars.ExtinctionError
NO_EXTINCTION = ("no stored extinction for {target}: run tools/extinction.py --target {target} --data-dir <data "
                 "dir> --map <bayestar2019.h5> under .venv-triceratops, or store it by hand with --store")
def fingerprint(latest_tests, final_status, decision, calibration_rows=0, sweep_size=None,
                extinction_ebv: float | None = None) -> str        # + "extinction_ebv" in the hashed JSON
# _lookup(target, signal, tests, *, fetch, store, extinction): raises NoExtinction(NO_EXTINCTION) when the Gaia row
#   has a parallax, its error and a BP-RP (derive would characterise the star) and extinction is None
# no_spectrum_title(): NO_SPECTRUM alone when star.extinction holds a block; else as before

# src/skysift/pack.py, pack_rows.py
REDDENING_CLAUSE = ("the reddening E(B-V) = {ebv} ({p16} to {p84}) comes from the Bayestar19 dust map at the Gaia "
                    "distance{green}, a sightline average, in place of the unreddened colour assumed before{gsp}")
#   {gsp} = " and of GSP-Phot's E(BP-RP) of {value:.2f}, which is biased for cool dwarfs{andrae}" when stored
def reddening_text(data, tex=False) -> str | None          # the note's clause, capitalised; None without the block
def reddening_plain(data) -> str | None
    # "Reddening: E(B-V) <ebv> (16th to 84th percentile <p16> to <p84>) from Bayestar19 (Green et al. 2019) at <d>
    #  pc, a sightline average[, outside the map's reliable distance range on this sightline], gives E(BP-RP)
    #  <ebprp>, A_G <a_g> and A_Ks <a_ks>; unreddened, the earlier assumption, Teff <t_u> K, radius <r_u> Rsun and
    #  planet radius <rp_u> Earth radii against the adopted <t> K, <r> Rsun and <rp>[; GSP-Phot E(BP-RP) <g>,
    #  biased for cool dwarfs (Andrae et al. 2023)]."
def reddening_rows(data) -> list[dict]
    # the README's table: {"quantity", "adopted", "unreddened", "gsp_phot"} for E(B-V), E(BP-RP), A_G, A_Ks,
    # (BP-RP)_0, Teff (K), radius (Rsun), mass (Msun), insolation (Earth) and planet radius (Earth radii);
    # "0 (assumed)" for the unreddened extinctions, the GSP-Phot column E(BP-RP) and Teff (teff_gspphot) only,
    # "not given" elsewhere
EXTINCTION_ROWS = (("E(B-V) (Bayestar19)", "ebv"), ("E(BP-RP) adopted", "ebprp"), ("A_G adopted", "a_g"),
                   ("A_Ks adopted", "a_ks"))   # provenance_rows: method from the block's source, reference "Green
                                               # et al. 2019; Gaia (E)DR3 extinction law; Cardelli et al. 1989"
```

Every number in the texts follows the precision rule: errors to two significant figures, the value to the error's last digit. Reddening values without an error print to three decimals.

- [ ] **Step 1: Write the failing law and draw tests** in `tests/unit/test_stars.py`. The expected values come from the coefficients above, checked on 23 October 2026.

```python
EXT = {"ebv": 0.05, "ebv_p16": 0.03, "ebv_p84": 0.07, "source": "a test block", "map": "bayestar2019",
       "queried_utc": "2026-10-23T20:00:00+00:00", "distance_pc": 303.0}


def test_the_gaia_dr3_law_turns_e_b_v_into_e_bp_rp_a_g_and_a_ks():
    out = stars.gaia_extinction(0.05, 2.0971918)                 # KIC 6146676's BP-RP
    assert out["a0"] == pytest.approx(0.155) and out["colour_held"] is False
    assert out["bp_rp0"] == pytest.approx(2.0361684, abs=1e-6)
    assert out["ebprp"] == pytest.approx(0.0610234, abs=1e-6) and out["a_g"] == pytest.approx(0.1125808, abs=1e-6)
    assert out["a_ks"] == pytest.approx(0.018135, abs=1e-9)
    zero = stars.gaia_extinction(0.0, 2.0971918)
    assert (zero["ebprp"], zero["a_g"], zero["a_ks"], zero["bp_rp0"]) == (0.0, 0.0, 0.0, 2.0971918)
    held = stars.gaia_extinction(0.05, 2.7)                      # (BP-RP)_0 2.63 is above the fit's 2.5
    assert held["colour_held"] is True and held["ebprp"] == pytest.approx(0.0689518, abs=1e-6)
    hot = stars.gaia_extinction(0.30, 0.8)
    assert hot["ebprp"] == pytest.approx(0.4187245, abs=1e-6) and hot["a_g"] == pytest.approx(0.8434732, abs=1e-6)
    many = stars.gaia_extinction(np.array([0.0, 0.05]), 2.0971918)
    assert many["ebprp"] == pytest.approx([0.0, 0.0610234], abs=1e-6)


def test_a_ks_over_a0_is_ccm_at_the_2mass_ks_wavelength():
    x = 1 / 2.159                                                # um^-1; CCM 1989 infrared: a = 0.574 x^1.61
    assert stars.A_KS_OVER_A0 == round(0.574 * x ** 1.61 - 0.527 * x ** 1.61 / 3.1, 3) == 0.117


def test_derive_with_a_stored_extinction_dereddens_and_keeps_the_unreddened_values():
    gaia = gaia_row_6146676()
    plain, plain_planet = derive(gaia, 40.29109, 0.048, 2165e-6, np.random.default_rng(1))
    star, planet = derive(gaia, 40.29109, 0.048, 2165e-6, np.random.default_rng(1), extinction=EXT)
    assert star.unreddened["teff"] == plain.teff.as_dict() and star.unreddened["radius"] == plain.radius.as_dict()
    assert star.unreddened["rp_rearth"] == plain_planet.rp_rearth.as_dict() and star.unreddened["bp_rp0"] == (
        gaia.bp_rp)
    assert star.teff.median > plain.teff.median                  # a bluer intrinsic colour is a hotter star
    e = star.extinction
    assert e["ebv"] == 0.05 and e["ebprp"]["median"] == pytest.approx(0.061, abs=0.003)
    assert e["a_ks"]["median"] == pytest.approx(0.018, abs=0.002) and e["bp_rp0"] == pytest.approx(2.036, abs=0.002)
    assert e["law"] == stars.GAIA_LAW and e["r_v"] == 3.1
    assert "Bayestar19" in star.radius.source and "A_Ks 0 to 0.07" not in star.radius.source
    assert derive(gaia, 40.29109, 0.048, 2165e-6, np.random.default_rng(1), extinction=EXT) == (star, planet)
    assert plain.extinction is None and plain.unreddened is None


def test_load_extinction_reads_validates_and_refuses(tmp_path):
    import json
    folder = tmp_path / "kic-006146676"
    folder.mkdir()
    assert stars.load_extinction("kic-006146676", tmp_path) is None
    (folder / "extinction.json").write_text(json.dumps(EXT), encoding="utf-8")
    assert stars.load_extinction("kic-006146676", tmp_path) == EXT
    for bad, words in (({**EXT, "ebv": None}, "ebv is not a finite number"),
                       ({**EXT, "ebv_p16": 0.06}, "not 0 <= ebv_p16 <= ebv <= ebv_p84"),
                       ({**EXT, "source": " "}, "source is empty"),
                       ({k: v for k, v in EXT.items() if k != "map"}, "map is missing")):
        (folder / "extinction.json").write_text(json.dumps(bad), encoding="utf-8")
        with pytest.raises(stars.ExtinctionError, match=words):
            stars.load_extinction("kic-006146676", tmp_path)
```

- [ ] **Step 2: Write the failing tool tests** in `tests/unit/test_extinction.py` (new). The tool's helpers run under the engine's interpreter; no test reads a map file.

```python
"""tools/extinction.py's pure helpers (spec 12.3): the HEALPix index, the pixel search, the distance interpolation
that dustmaps' BayestarQuery applies, the stored block, and main's refusals; no test reads the map."""
import json
import math

import numpy as np
import pytest

from tools import extinction as ex


def test_ang2pix_nest_finds_the_twelve_base_pixels_and_nests():
    centres = ([(math.acos(2 / 3), math.pi / 4 + k * math.pi / 2) for k in range(4)]
               + [(math.pi / 2, k * math.pi / 2) for k in range(4)]
               + [(math.acos(-2 / 3), math.pi / 4 + k * math.pi / 2) for k in range(4)])
    assert [ex.ang2pix_nest(1, t, p) for t, p in centres] == list(range(12))
    rng = np.random.default_rng(3)
    for _ in range(2000):
        theta, phi = math.acos(rng.uniform(-1, 1)), rng.uniform(0, 2 * math.pi)
        for nside in (2, 64, 1024):                              # a pixel's parent is its index over 4
            assert ex.ang2pix_nest(nside, theta, phi) // 4 == ex.ang2pix_nest(nside // 2, theta, phi)


def test_kic_6146676_lies_at_l_76_b_7_and_its_pixels():
    l, b = ex.galactic(297.86871, 41.455567)
    assert (l, b) == pytest.approx((76.0444, 7.4198), abs=1e-3)
    theta, phi = math.radians(90 - b), math.radians(l)
    assert [ex.ang2pix_nest(n, theta, phi) for n in (64, 128, 256, 512, 1024)] == [23376, 93504, 374017, 1496068,
                                                                                   5984274]


def test_pixel_row_searches_each_nside_level():
    l, b = 76.0444, 7.4198
    nside = np.array([64, 64, 128, 128])
    index = np.array([23375, 23377, 93503, 93504])
    assert ex.pixel_row(nside, index, l, b) == 3
    assert ex.pixel_row(nside[:3], index[:3], l, b) is None


def test_the_reddening_at_a_distance_follows_dustmaps():
    edges = np.array([4.0, 5.0, 6.0, 7.0])                       # distance moduli
    samples = np.array([[0.1, 0.2, 0.3, 0.4], [0.2, 0.3, 0.5, 0.6]])
    assert ex.reddening_at(samples, edges, 10 ** (5.5 / 5 + 1)) == pytest.approx([0.25, 0.4])   # linear in dm
    assert ex.reddening_at(samples, edges, 10 ** (3.0 / 5 + 1)) == pytest.approx([0.1 * 10 ** -0.2,
                                                                                  0.2 * 10 ** -0.2])
    assert ex.reddening_at(samples, edges, 1e6) == pytest.approx([0.4, 0.6])      # beyond the map: the last bin
    block = ex.extinction_block(np.array([0.05, 0.06, 0.07, np.nan]), distance_pc=303.0, l_deg=76.04, b_deg=7.42,
                                nside=1024, reliable=False, converged=True, queried_utc="2026-10-23T20:00:00+00:00")
    assert block["ebv"] == pytest.approx(round(0.884 * 0.06, 4)) and block["n_samples"] == 3
    assert block["reliable_distance"] is False and block["map"].startswith("bayestar2019 (doi:10.7910/DVN/2EJ9TX")
    assert set(block) >= {"ebv", "ebv_p16", "ebv_p84", "source", "map", "queried_utc", "distance_pc"}


def test_a_nan_sightline_stops_the_tool_with_the_store_instruction(tmp_path, capsys):
    with pytest.raises(ValueError, match="the map gives no finite reddening on this sightline"):
        ex.extinction_block(np.array([np.nan, np.nan]), distance_pc=303.0, l_deg=0.0, b_deg=0.0, nside=64,
                            reliable=True, converged=None, queried_utc="t")
    data = tmp_path / "data" / "dossiers" / "kic-006146676"
    data.mkdir(parents=True)
    (data / "dossier_data.json").write_text(json.dumps({"target": {"ra": 297.86871, "dec": 41.455567},
                                                        "star": {"route": "mann",
                                                                 "distance_pc": {"median": 303.0}}}),
                                            encoding="utf-8")
    args = ["--target", "kic-006146676", "--data-dir", str(tmp_path / "data"), "--root", str(tmp_path / "c")]
    assert ex.main([*args, "--map", str(tmp_path / "no-such.h5")]) == 2
    err = capsys.readouterr().err
    assert "no-such.h5 is not there" in err and "--store --ebv" in err and "--distance-pc 303" in err


def test_store_by_hand_writes_the_block_and_a_second_run_does_nothing(tmp_path, capsys):
    args = ["--target", "kic-006146676", "--data-dir", str(tmp_path), "--root", str(tmp_path / "c")]
    assert ex.main([*args, "--store", "--ebv", "0.05", "--ebv-p16", "0.03", "--ebv-p84", "0.07",
                    "--distance-pc", "303", "--source", "read off the Bayestar19 web page"]) == 0
    block = json.loads(ex.store_path(tmp_path / "c", "kic-006146676").read_text(encoding="utf-8"))
    assert (block["ebv"], block["map"], block["source"]) == (0.05, "by hand", "read off the Bayestar19 web page")
    assert ex.main([*args, "--map", str(tmp_path / "no-such.h5")]) == 0
    assert "stored; nothing to do" in capsys.readouterr().err
```

- [ ] **Step 3: Write the failing dossier, pack and page tests.**
  - `tests/unit/test_dossier.py`, `_make_env` (lines 139 to 170): after the `CANDIDATES_DIR` lines, set `dossier_module.EXTINCTION_DIR = Path(settings.data_dir) / "extinction"` with `monkeypatch.setattr`. Then write `EXTINCTION_FIXTURE` (the `EXT` block of Step 1 with `"source": "test fixture"`) to `<that>/<target_id>/extinction.json` for every `target_id` of `select(TargetRow.target_id)` on the seeded store.

```python
def test_generate_refuses_a_characterised_star_without_a_stored_extinction(env):
    (dossier_module.EXTINCTION_DIR / STAGE2_TARGET / "extinction.json").unlink()
    with pytest.raises(dossier_module.NoExtinction, match=r"no stored extinction for kic-006146676: run "
                                                          r"tools/extinction.py --target kic-006146676"):
        generate(env.store, env.blob, env.settings, STAGE2_TARGET, fetch=env.fetch, force=True)


def test_a_star_gaia_cannot_characterise_needs_no_extinction(env_no_gaia):
    (dossier_module.EXTINCTION_DIR / STAGE2_TARGET / "extinction.json").unlink()
    d = _data(generate(env_no_gaia.store, env_no_gaia.blob, env_no_gaia.settings, STAGE2_TARGET,
                       fetch=env_no_gaia.fetch, force=True))
    assert d["star"]["route"] == "none" and d["star"]["extinction"] is None


def test_version_6_stores_the_adopted_and_unreddened_star_and_the_extinction_moves_the_fingerprint(env):
    rep = generate(env.store, env.blob, env.settings, STAGE2_TARGET, fetch=env.fetch, force=True)
    star = _data(rep)["star"]
    assert star["extinction"]["ebv"] == 0.05 and star["extinction"]["source"] == "test fixture"
    assert set(star["unreddened"]) >= {"teff", "radius", "mass", "rp_rearth", "insolation_earth", "bp_rp0"}
    assert dossier_module.no_spectrum_title(star) == dossier_module.NO_SPECTRUM
    path = dossier_module.EXTINCTION_DIR / STAGE2_TARGET / "extinction.json"
    path.write_text(json.dumps({**json.loads(path.read_text(encoding="utf-8")), "ebv": 0.06, "ebv_p84": 0.08}),
                    encoding="utf-8")
    again = generate(env.store, env.blob, env.settings, STAGE2_TARGET, fetch=env.fetch)
    assert again.generated is True and _data(again)["star"]["extinction"]["ebv"] == 0.06
```

  - `tests/unit/test_cli.py`: `skysift dossier` on the `_make_env` store with the fixture file removed exits 2 and prints "no stored extinction for kic-006146676".
  - `tests/unit/test_pack.py`, on `env_with_dossier`:
    - `render_note` carries `str(pack_module.reddening_text(data, True))`, which contains `\citep{green2019}` and the fixture's "E(B-V) = 0.050 (0.030 to 0.070)".
    - The note does not carry "unreddened" figures (the note's one clause only).
    - The README carries the header row `| Quantity | Adopted (Bayestar19) | Unreddened (the earlier assumption) | GSP-Phot (biased for cool dwarfs) |` and a Teff row whose unreddened cell is the dossier's `star.unreddened.teff` in `value_text`.
    - `exofop/note.txt` and `archive_email.txt` carry `pack_module.reddening_plain(data)`.
    - `data/stellar_provenance.csv` has the four `EXTINCTION_ROWS`.
    - The README's references name "Cardelli, J. A., Clayton, G. C., and Mathis, J. S. 1989, ApJ, 345, 245", "Fitzpatrick, E. L., Massa, D., Gordon, K. D., Bohlin, R., and Clayton, G. C. 2019, ApJ, 886, 108" and "Riello, M., De Angeli, F., Evans, D. W., et al. 2021, A&A, 649, A3".
  - `tests/unit/test_zenodo.py`: the metadata description carries `pack.reddening_plain(data)` as its own paragraph after the ephemeris.
  - `tests/unit/test_ui_dossiers.py`: the served and standalone pages print `pack.star_texts(data)`, which now leads with `reddening_plain`.

- [ ] **Step 4: Run them to see them fail.**
  - Command: `PYTHONUTF8=1 PYTHONPATH=src C:\Research\skysift\.venv\Scripts\python.exe -m pytest tests/unit/test_stars.py tests/unit/test_extinction.py tests/unit/test_dossier.py tests/unit/test_pack.py tests/unit/test_zenodo.py tests/unit/test_ui_dossiers.py tests/unit/test_cli.py -q -p no:cacheprovider`.
  - Expected: `AttributeError: module 'skysift.stars' has no attribute 'gaia_extinction'` (and `A_KS_OVER_A0`, `load_extinction`); `ModuleNotFoundError: No module named 'tools.extinction'`; `TypeError: derive() got an unexpected keyword argument 'extinction'`; `AttributeError: module 'skysift.dossier' has no attribute 'EXTINCTION_DIR'` in every fixture that writes the file.

- [ ] **Step 5: The law and the draws** in `src/skysift/stars.py`.
  - Implement the constants and functions of the interface block. `gaia_k` evaluates the ten-term polynomial with numpy so that scalars and arrays both work; `gaia_extinction` clips X_0 with `np.clip` and returns Python floats for a scalar `ebv`.
  - `derive(..., extinction=None)` follows the interface comment. Take the unreddened pair with `copy.deepcopy(rng)` before the first draw. The extinction draws come from `np.random.default_rng(EXTINCTION_SEED)`, so the seeded draws of the colour, the parallax, Ks and the Mann scatter are those of version 5.
  - Without Ks (the Mamajek route), only the colour and the spectral type move; the radius and mass come from the dereddened colour through `_interp` as the table route does now.
  - `_unknown` passes `extinction=None, unreddened=None`. The module docstring gains one paragraph on the adopted extinction and its precedent: Green et al. 2019; Andrae et al. 2023 for why GSP-Phot is not adopted.

- [ ] **Step 6: The tool**, `tools/extinction.py`, with a module docstring giving the run commands (Step 8), why it reads the file directly (the preamble's two findings), the map's DOI, size and MD5, and the dustmaps rules it copies.
  - `main` checks, in this order:
    1. The arguments.
    2. The stored file: "stored; nothing to do" and exit 0 unless `--force`.
    3. The dossier JSON at `<data-dir>/dossiers/<target>/dossier_data.json`. Without a distance: "the star has no Gaia distance; nothing to store", exit 0.
    4. `--store` writes the block by hand with `"map": "by hand"`, `queried_utc` now, and exit 0.
    5. The map: missing ("<path> is not there"), or a size other than `MAP_BYTES` ("<path> is <n> bytes, not 727,902,836: download it again (MD5 ab815d2fd3068d1b81a1bd61fb18a722)"), exit 2 with `STORE_HINT`.
    6. `import h5py` (exit 2: "h5py is not importable; run this tool with .venv-triceratops/Scripts/python.exe -I").
    7. The required names in the file (exit 2 naming the first missing).
  - The query itself: `pixel_row`; `samples = f["/samples"][row]`; `reddening_at(samples, DM_bin_edges, distance_pc)`.
  - The flags: `reliable` is `DM_reliable_min[row] <= dm <= DM_reliable_max[row]`, both finite; `converged` is `bool(pixel_info["converged"][row])` when the field exists, else None.
  - Then `extinction_block`, written through a temporary file and `os.replace` to `store_path(root, target)`. Print one line with the target, l, b, the distance and the three E(B-V) values.
  - `--all` runs `LIVE_TARGETS` in turn and returns the largest exit code.

- [ ] **Step 7: Download the map, with Vikram's yes first.** Ask him in chat for permission to download `bayestar2019.h5` from Harvard Dataverse (doi:10.7910/DVN/2EJ9TX, `https://dataverse.harvard.edu/api/access/datafile/3424724`, 727,902,836 bytes) into `C:\Research\skysift\.superpowers\maps\` (git-ignored). Then:
  - `curl -L --fail -o C:\Research\skysift\.superpowers\maps\bayestar2019.h5 https://dataverse.harvard.edu/api/access/datafile/3424724`, in the background at BelowNormal priority.
  - `certutil -hashfile C:\Research\skysift\.superpowers\maps\bayestar2019.h5 MD5`, which must print ab815d2fd3068d1b81a1bd61fb18a722.
  - Without the yes, or if the download fails, store each value by hand with `--store` from a source Vikram names, and say so in the task report.

- [ ] **Step 8: Store the five live values.** From the worktree, `C:\Research\skysift\.venv-triceratops\Scripts\python.exe -I tools/extinction.py --all --data-dir C:\Research\skysift\.superpowers\sample-run --root docs/candidates --map C:\Research\skysift\.superpowers\maps\bayestar2019.h5`.
  - The tool reads only the dossier JSON files, never the database.
  - The distances it reads are 290.7, 303.0, 840.6, 1860.1 and 1149.7 pc, in the order of `LIVE_TARGETS`.
  - Then run the same command with `--data-dir .superpowers/sdd/2026-10-22-skysift-referee-pack/live/data-3480`. It must print "stored; nothing to do" for `kic-006699887`.
  - Record the five E(B-V) values, their percentiles and `reliable_distance` in the task report. A star with `reliable_distance` false is named.

- [ ] **Step 9: The dossier.**
  - `EXTINCTION_DIR`, the re-exported errors and `NO_EXTINCTION`.
  - `generate` reads `extinction = stars.load_extinction(target_id, EXTINCTION_DIR)` before the fingerprint, so a malformed file stops the run before any work, and passes `extinction_ebv=(extinction or {}).get("ebv")` to `fingerprint`.
  - `_lookup(..., extinction)` refuses as specified, right after `stars.query_gaia`, and passes `extinction` to `stars.derive`; `_rederive` passes it too.
  - `no_spectrum_title` keys on `star.extinction`.
  - `cli.py` line 289 catches `(ds.LightCurveReleased, ds.PriorClaimsError, ds.NoExtinction, ds.ExtinctionError)`.
  - The `DOSSIER_VERSION` comment of Task 15 already names the extinction block; the version stays "6".

- [ ] **Step 10: The texts.**
  - `reddening_text` follows `REDDENING_CLAUSE`, with `\citep{green2019}` and `\citep{andrae2023}` in the TeX form and "(Green et al. 2019)" and "(Andrae et al. 2023)" in the plain one. Without the block it falls back to today's GSP-Phot sentence, for a dossier below version 6.
  - `references` sets `green2019` to `star_ok and isinstance(star.get("extinction"), dict)`.
  - `BIBLIOGRAPHY` gains `("green2019", r"{Green} et~al.(2019)", r"Green, G.~M., Schlafly, E., Zucker, C., Speagle, J.~S., and Finkbeiner, D. 2019, ApJ, 887, 93")` in the existing tuple shape.
  - `reddening_plain` and `reddening_rows` are as specified. `numbers` exposes `reddening_plain` and `reddening_rows`, and `star_texts` leads with `reddening_plain`.
  - `README.md.j2` prints the table under the star paragraph, with one caveat line under it: "Adopted: a dust-map sightline average with its 16th to 84th percentile range. Unreddened: the assumption of round 2, E(BP-RP) at or below 0.1. GSP-Phot: Gaia's own fit, biased for cool dwarfs (Andrae et al. 2023)."
  - `exofop_note.txt.j2` prints `n.reddening_plain` after the planet line, and `archive_paragraph` prints it after the ephemeris sentence.
  - `zenodo.metadata_from` adds `pack.reddening_plain(data)` as a paragraph after the ephemeris. Import it inside the function, as `page_texts` does.
  - `provenance_rows` appends the `EXTINCTION_ROWS` when the block exists. The four rows' values are the block's medians, with `minus` and `plus` from the draws (E(B-V) from the stored percentiles).
  - The page's star block (`dossier.html` lines 402 to 404 and `dossier.js` line 629) prints `texts.star` in place of `star.reddening_note` when the block exists.

- [ ] **Step 11: Run the tests** (all pass).
  - `PYTHONUTF8=1 PYTHONPATH=src C:\Research\skysift\.venv\Scripts\python.exe -m pytest tests/unit/test_stars.py tests/unit/test_extinction.py tests/unit/test_dossier.py tests/unit/test_pack.py tests/unit/test_zenodo.py tests/unit/test_ui_dossiers.py tests/unit/test_cli.py tests/unit/test_crosscheck.py -q -p no:cacheprovider`
  - `PYTHONUTF8=1 PYTHONPATH=src C:\Research\skysift\.venv\Scripts\python.exe -m ruff check src tests tools`

- [ ] **Step 12: Regenerate the golden** with the Step 11 command of Task 15. It reads `docs/candidates/kic-006146676/extinction.json` from the worktree, which Step 8 wrote.
  - Run the golden test (`-m golden`). The hand-built comparisons (planet radius, a, Teq within the study's errors) are expected to hold, since the radius barely depends on reddening on the Mann route. If one fails, the task report gives the old and new value; the test's tolerance is not widened.
  - Then run the unit suite once, detached and hidden, at BelowNormal: `PYTHONUTF8=1 PYTHONPATH=src C:\Research\skysift\.venv\Scripts\python.exe -m pytest tests -q -p no:cacheprovider -m "not slow and not golden"`.

- [ ] **Step 13: The word budget.** Run the live-shape limit test (`-k live_shape -vv`). If any of the four counts passes 1,480, cut in this order, re-measuring after each cut, and stop at the first that fits:
  1. The note's reddening clause drops "a sightline average, " and the GSP-Phot half, which stay in the README's table.
  2. The note's short variant sentence becomes a parenthesis inside the cross-check sentence: "(<v.fpp> and <v.fpp_no_stp> without STP with <removed_phrase> removed)".
  3. The dominant-excluded sentence drops "which no imaging constrains, ".
  4. If it still passes 1,480, stop and report the counts. No number of spec 12 leaves the note.
  Record the counts in the commit message.

- [ ] **Step 14: Commit.**

```
git add tools/extinction.py src/skysift/stars.py src/skysift/dossier.py src/skysift/cli.py src/skysift/pack.py src/skysift/pack_rows.py src/skysift/zenodo.py src/skysift/templates/pack/note.tex.j2 src/skysift/templates/pack/README.md.j2 src/skysift/templates/pack/exofop_note.txt.j2 src/skysift/ui/templates/dossier.html src/skysift/ui/static/dossier.js docs/candidates/kic-005689121/extinction.json docs/candidates/kic-006146676/extinction.json docs/candidates/kic-006699887/extinction.json docs/candidates/kic-009243078/extinction.json docs/candidates/kic-010352243/extinction.json tests/golden/kic-006146676-generated.json tests/unit/test_extinction.py tests/unit/test_stars.py tests/unit/test_dossier.py tests/unit/test_pack.py tests/unit/test_zenodo.py tests/unit/test_ui_dossiers.py tests/unit/test_cli.py
git commit -m "feat(stars): Bayestar19 extinction stored per target, through the Gaia DR3 law and the draws, with the adopted, unreddened and GSP-Phot values in every text

Live-shape words: <four counts>.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

The live run of spec 12.4 follows these three tasks, once the controller has read Task 14's note:
1. Store the extinction (Task 16 Step 8, done).
2. Regenerate the five live dossiers and the second period of KIC 6699887 at version 6.
3. Export the four cross-checks at schema 3.
4. Run the tool on each: two run sets per signal, so eight TRICERATOPS processes, four at a time, each with `--resume`, in the background at BelowNormal.
5. Import the four results.
6. Run `tools/leakage_note.py --after` on the KIC 6146676 result, so the note gains the NFPP after.
7. Regenerate the dossiers, rebuild both packs and run the full gate on the tip. The parent merges on the report.

---

## Round 4 preparation (spec 12.5, after Vikram approved the map download)

### Task 17: Bayestar19 from a local map file, adopted (spec 12.5)

The map file `bayestar2019.h5` is downloaded by the controller, not by this task, into a folder outside the repository, with a sidecar `<file>.md5` holding the MD5 it verified against the Dataverse record. This task makes the map usable the moment the file is there: a reader that needs no `healpy`, the tool's local-map path, and the adopted state in every text. The live records stay "not available" until the controller runs the tool, so the golden does not change here.

**The map's layout, verified on 26 October 2026 against `dustmaps/bayestar.py` (GitHub master, read as text; nothing installed from it):**
- `/pixel_info`: a structured dataset, one row per pixel, sorted by (`nside`, `healpix_index`), with the fields `nside`, `healpix_index` (nested ordering), `DM_reliable_min`, `DM_reliable_max` and `converged`. `BayestarQuery.__init__` reads `f['/pixel_info'][:]` and `f['/pixel_info'].attrs['DM_bin_edges']`.
- The distance grid is the attribute `DM_bin_edges` of `/pixel_info`, not a dataset. For bayestar2019, dustmaps' `REPACKED_DSETS` gives `samples` (4,214,070, 5, 120), `best_fit` (4,214,070, 120) and `pixel_info` (4,214,070,): 120 distance moduli and 5 samples. The 31-bin grid of 4.0 to 19.0 in 0.5 steps belongs to bayestar2015 and bayestar2017. The reader takes the edges from the file, never from a constant.
- `/GRDiagnostic` is absent from bayestar2019 ("not every version of the map has it", `h5_repack`), and dustmaps never reads it. The convergence flag is `pixel_info['converged']`, as `query(..., return_flags=True)` returns it. No Gelman-Rubin threshold is applied.
- The pixel search (`_find_data_idx`) tries every nside level, ascending, and a match at a finer level overwrites a coarser one. Searching from the largest nside down and taking the first match gives the same row.
- `query` with a distance: dm = 5 (log10(d_kpc) + 2); the ceiling bin is `searchsorted(DM_bin_edges, dm)`. At bin 0 it returns 10^(0.2 (dm - DM_bin_edges[0])) times the first bin, which is linear in distance, not in DM. Beyond the last edge it returns the last bin. Otherwise it interpolates linearly in DM between the two edges. The percentile mode is `np.nanpercentile` over the samples. The reliable-distance flag is DM_reliable_min <= dm <= DM_reliable_max, with both finite.

**Files:**
- Create: `tools/bayestar_map.py`, the reader. numpy loads at import, astropy inside `galactic`, `astropy_healpix` inside `pixel_index`, and `h5py` inside `main`, so the engine venv imports it too. It runs as `C:\Research\skysift\.venv-dustmaps\Scripts\python.exe -I tools/bayestar_map.py --map <file> --ra <deg> --dec <deg> --distance <pc> [<pc> ...]` and prints its queries as JSON.
- Create: the third venv `C:\Research\skysift\.venv-dustmaps` (`uv venv ... --python 3.12`; `uv pip install --native-tls -p ... h5py astropy astropy-healpix numpy`, plus pytest for the reader's tests). `.venv` and `.venv-triceratops` stay as they are.
- Modify: `tools/extinction.py`.
  - `--map <path>` (or `SKYSIFT_BAYESTAR_MAP`) reads the local file first, in-process when `h5py` and `astropy_healpix` import, otherwise through the reader under `.venv-dustmaps` (`SKYSIFT_DUSTMAPS_PYTHON`, or the first `.venv-dustmaps` above the tool).
  - Argonaut is attempted only without a map path.
  - IRSA stays as the column check: a stored column is kept, and one GET is made only when none is stored.
  - A stored record without a 3D value is read again when a map is given; a stored 3D value needs `--force`.
- Modify: `src/skysift/stars.py`. In the adopted state, `derive` records `outside_mann_range` for the adopted star, and the height clause gains "; the 3D value settles it" where the map constrains the dust at the star's distance, else "; the map does not constrain the dust this near (this far) on this sightline" (fix round 1, ruling 4).
- Modify: `src/skysift/pack.py`, `src/skysift/pack_rows.py`. The adopted state's note sentence, plain paragraph, README table and key, and provenance rows follow the ruling below.
- Not modified: `ui/explain.py`, `dossier.js` and `dossier.html` print no reddening wording of their own (the page prints `pack.star_texts`), and `pack_text.py` needs nothing.
- Test: `tests/unit/test_bayestar_map.py` (new; a synthetic map written with h5py, run under `.venv-dustmaps` with `--noconftest`; the h5py and HEALPix tests skip under `.venv`), `test_extinction.py`, `test_stars.py`, `test_pack.py`, `test_ui_dossiers.py`. No test opens the real map or the network.

**Interfaces:**
- Consumes:
  - The stored dossier's `target.ra`, `target.dec`, and `star.distance_pc` (`median`, `minus`, `plus`).
  - `stars.derive`'s adopted path (Task 16), `stars.outside_mann_range`, `stars.height_fields`.
  - `pack.value_text`, `pack_text.pm_digits`, and Task 16's `reddening_*` functions.
- Produces:
```python
# tools/bayestar_map.py
MAP_VERSION = "bayestar2019"
MAP_DOI = "10.7910/DVN/2EJ9TX"
MAP_MD5 = "ab815d2fd3068d1b81a1bd61fb18a722"     # the Dataverse record's
BAYESTAR_TO_EBV = 0.884                           # E(B-V) = 0.884 x the map's units (Green et al. 2019)
PIXEL_FIELDS = ("nside", "healpix_index", "converged", "DM_reliable_min", "DM_reliable_max")
class MapLayoutError(ValueError): ...             # a required dataset, field or attribute is missing
def galactic(ra_deg, dec_deg) -> tuple[float, float]           # astropy ICRS to Galactic (l, b), degrees
def pixel_index(l_deg, b_deg, pixel_info) -> int | None
    # the row whose (nside, healpix_index) holds (l, b), trying each nside present from the largest down
    # (astropy_healpix.HEALPix(nside, order="nested").lonlat_to_healpix); None outside the footprint
def distance_modulus(distance_pc) -> float                     # 5 log10(d / 10 pc)
def reddening_at(samples_row, dm_grid, distance_pc) -> np.ndarray
    # one value per sample, by dustmaps' rules above (linear in DM inside the grid, scaled by the distance ratio
    # below it, the last bin beyond it)
def check_layout(h5) -> None                                   # MapLayoutError naming the first missing name
def query_many(h5, ra_deg, dec_deg, distances_pc) -> list[dict]
def query(h5, ra_deg, dec_deg, distance_pc) -> dict
    # {"l_deg", "b_deg", "distance_pc", "dm", "in_footprint", "row", "nside", "healpix_index", "converged",
    #  "reliable_distance", "n_samples", "ebv_map_units": {"median", "p16", "p84"}, "ebv": the same x 0.884};
    # outside the footprint in_footprint False and the rest None; no finite sample gives n_samples 0 and None values
def main(argv=None) -> int                                     # 0 the JSON list on stdout; 2 a missing file or name

# tools/extinction.py
MAP_ENV, MAP_PYTHON_ENV = "SKYSIFT_BAYESTAR_MAP", "SKYSIFT_DUSTMAPS_PYTHON"
MAP_SOURCE = "Bayestar19 (Green et al. 2019), local file"
MAP_LABEL = "bayestar2019 (doi:10.7910/DVN/2EJ9TX), local file"
MAP_FAILED = "Bayestar19 (Green et al. 2019) not available: {why}"
class MapError(ValueError): ...
def sidecar_md5(path) -> str            # <file>.md5's 32-hex digest, "unverified" without the file; MapError otherwise
def dustmaps_python() -> Path           # SKYSIFT_DUSTMAPS_PYTHON, else the first .venv-dustmaps above the tool
def read_map(path, ra_deg, dec_deg, distances_pc, *, run=None) -> list[dict]
def map_block(target, queries, *, distance, map_path, md5, queried_utc) -> dict
    # adopted: {"target", "status": "adopted", "ebv", "ebv_p16", "ebv_p84" (the samples at the median distance,
    #  E(B-V), 4 decimals), "ebv_map_units", "ebv_at_distance_p16", "ebv_at_distance_p84" (sample medians at the
    #  distance's percentiles), "source": MAP_SOURCE, "map": MAP_LABEL, "map_file" (the file's name),
    #  "map_md5", "map_version", "queried_utc", "distance_pc", "distance_pc_p16", "distance_pc_p84", "l_deg",
    #  "b_deg", "nside", "healpix_index", "n_samples", "converged": True, "reliable_distance"};
    # outside the footprint, an unconverged pixel or no finite sample: {"status": "not available", "reason":
    #  MAP_FAILED with the why, "map_reason": the why, the map's file, md5 and version, ...}
```

**The adopted state in the texts (the controller's ruling):**
- The note's star sentence becomes "The reddening is E(B-V) = <value> (<p16> to <p84>) from Bayestar19 \citep{green2019} at <d> pc (full column <value> \citep{schlafly2011}, an upper bound)", the value and its range at three decimals as the README prints them (fix round 1, ruling 2); for a star outside the map's reliable range, "at <d> pc, nearer than the map's reliable range (from <N> pc; full column ...)" (or "beyond ... (to <N> pc; ..."). The plain texts and the README say whether the 3D value is consistent with the column: "consistent" means the 3D median is at or below the column's E(B-V) plus its std, and "inconsistent" otherwise. When the adopted star leaves the Mann range, "; the dereddened parameters are outside the Mann relations' range, so indicative" follows. The planet sentence prints the adopted radius with its error, with no bracket.
- In the plain paragraph and the README, each parameter reads "<adopted> (unreddened <value>; full column <value>)". The distance check prints when the record has it: "E(B-V) a to b at the distance's 16th and 84th percentiles". The paragraph also says "unreddened (E(B-V) = 0) is the earlier assumption", and the full column is "an upper bound, consistent (inconsistent) with the 3D value". The height clause stays and ends "; the 3D value settles it" (or, outside the map's reliable range, "; the map does not constrain the dust this near on this sightline"). GSP-Phot is printed as not adopted.
- The README table keeps four rows. The statuses are "adopted" (with the Mann clause when flagged), "the earlier assumption, not adopted", "upper bound, consistent (inconsistent) with the 3D value", and "not adopted".
- The provenance's E(B-V) row names the map version, MD5, file and pixel. The fingerprint is unchanged: it hashes the adopted E(B-V) and the full column's E(B-V) and std (`extinction_ebv`, `column_ebv`, `column_ebv_std`).
- The live shape is measured in both states: the golden's bracket and the same golden with an adopted block. Each stays at or under 1,480 words, and each abstract at or under 148.

**Commits:** `docs(spec): 12.5, Bayestar19 from a local map file`; `feat(extinction): a Bayestar19 map reader without healpy, the tool's --map path, the adopted value's provenance`; `feat(stars): the adopted 3D reddening in every text, the full column as a check, the unreddened values as the earlier assumption` (with the live-shape counts). No golden change.

**After this task (the controller):** download the map with Vikram's yes and write its sidecar MD5. Then run `PYTHONUTF8=1 PYTHONPATH=src C:\Research\skysift\.venv\Scripts\python.exe -m tools.extinction --all --data-dir C:\Research\skysift\.superpowers\sample-run --root docs/candidates --map <file>`, which keeps the stored columns and makes no IRSA call. Then regenerate the dossiers and the golden, and rebuild the packs.
