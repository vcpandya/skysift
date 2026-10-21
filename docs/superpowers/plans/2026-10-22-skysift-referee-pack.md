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
