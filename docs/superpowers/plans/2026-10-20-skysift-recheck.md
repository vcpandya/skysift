# SkySift Stage 2 Recheck Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every undetermined and stage-1 signal a second, more scrutinising pass (`skysift recheck`): re-vet under the current ladder, re-vet at the suggested half period, retry a catalogue outage, and run a new pixel rung that localises the dip on the target or on its neighbour, promoting survivors to `candidate_stage2`.

**Architecture:** A new module `skysift.recheck` walks the recheck queue and applies the four steps of spec section 4.3 in order, writing ordinary test rows plus one `rechecks` history row per step so the dashboard shows them in the same ladder and popup. The light-curve loader moves out of the UI into `skysift.lc.loader` so the engine can re-vet from kept raw files. Pixel work is split into pure numerics (`skysift.lc.pixels`, ported from the 20 October dossier scripts), file access (`skysift.lc.tpf`) and the rung (`skysift.vetting.rung5_pixels`). The ladder version becomes "4"; the search version and job keys of finished targets stay valid.

**Tech Stack:** Python 3.12, numpy, scipy (`least_squares`), astropy (`fits`, `wcs`), SQLAlchemy, typer, pytest with `tests/synthetic.py` and `tests/unit/fake_fetch.py`. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-10-20-skysift-mission-control-design.md` sections 4.1, 4.3, 4.4, 6 and 8 (authority). The UI plan `docs/superpowers/plans/2026-10-20-skysift-mission-control-ui.md` lands first; its `explain` module and templates already accept `candidate_stage2`, `superseded` and a `recheck_history` list.

## Global Constraints

- Never run a console `.exe` from `.venv/Scripts`; every command is `.venv/Scripts/python.exe -m <module>` with `PYTHONUTF8=1`.
- Branch `feat/mission-control` (continuing from the UI plan). Conventional commits ending with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`. Never push.
- `python -m ruff check src tests` clean (line length 120); `python -m pytest tests/unit -q -p no:cacheprovider` green before every commit. Tests stay off the network: every fetch goes through an injectable `fetch` callable (`tests/unit/fake_fetch.py` pattern) and TPFs come from synthetic FITS written by the test.
- The search version (`pipeline.search_version`) does not change. `DEFAULT_LADDER_VERSION` becomes `"4"`; a target whose job key is done under ladder `"3"` with the same detector version counts as done (`LADDER_COMPATIBLE = {"4": ("3",)}`), so no finished target is swept again.
- Rechecks never delete or overwrite a test row; history is append-only. A reviewer decision (`decisions` table) is never changed by the engine, and a signal with a reviewer decision is skipped by the recheck.
- Statuses: `pending`, `candidate_stage1`, `candidate_stage2`, `undetermined`, `rejected`, `superseded`. A signal's `final_status` is always `status_from_tests(latest row per rung)` mapped through `FINAL_STATUS`, except `superseded` (set by step 2) and reviewer rejections.
- Pixel work runs at BelowNormal priority like every engine job (the CLI already lowers the process priority); one signal at a time; TPF bytes go through the blob (`kind="tpf"`, pinned while the signal is a candidate) and every HTTP request is logged through `store.log_query` with the target id.
- Every numeric reason string carries its deciding numbers (the UI's `explain.reason_in_words` maps on prefixes listed in Task 5).

## Review Focus

1. **A signal whose raw light curve was released by retention** (`stream` mode): steps 1 and 2 must re-fetch through the loader, record provenance, and never crash the whole recheck when the fetch fails (that signal is skipped with a `rechecks` row `outcome="skipped"`, reason `"light curve unavailable: ..."`). Pinned by Task 3 `test_recheck_skips_a_signal_whose_curve_cannot_be_loaded`.
2. **A P/2 signal that is itself undetermined for the same reason** (equal secondary again, because the dips alternate): the original must not be marked superseded and the new row must not loop. Pinned by Task 3 `test_halving_does_not_supersede_when_the_half_period_is_undetermined_for_the_same_reason`.
3. **A neighbour-less target** (`tpf_org` is the target itself and Gaia lists no star inside the aperture): the pixel rung has nothing to compare against and must return undetermined with the reason `"pixels: no neighbour to test against"` rather than pass by default. Pinned by Task 5 `test_rung5_without_a_neighbour_is_undetermined`.
4. **A TPF whose quarter has fewer than two transits with data**: that season is excluded, and when fewer than three usable transits remain overall the verdict is undetermined with `"pixels: 2 usable transits < 3"`. Pinned by Task 4 `test_localise_needs_three_usable_transits`.
5. **Running the recheck twice**: the second run must find nothing to do (idempotent), write no new rows, and the CLI must print a zero summary. Pinned by Task 3 `test_recheck_is_idempotent`.

---

## File structure

| Path | Responsibility |
|---|---|
| `src/skysift/store/db.py` | `SignalRow.parent_signal_id`, `SignalRow.ladder_version`; `TestRow.recheck_id`; `RecheckRow`; `status_from_tests`, `final_status_for`; `Store.latest_tests`, `Store.add_recheck`, `Store.recheck_queue`, `Store.add_signal` with parent/ladder; `decide` derives the un-reject status from the tests. |
| `src/skysift/store/migrations.py` | `_step_002_recheck`. |
| `src/skysift/lc/loader.py` | `LightCurveLoader`, `_HoldBlob`, `_ProvenanceStore`, `IntegrityError` moved from `ui/plots.py` (which re-exports them). |
| `src/skysift/lc/ops.py` | `measure_at(time, fnorm, period, t0, duration_days) -> MeasuredDip` (box depth, error, snr, n_events with data). |
| `src/skysift/pipeline.py` | `revet(lc, target, th, sig, ladder, *, fetch, store) -> tuple[str, list[TestResult]]`; `halved_signal(sig, lc, fnorm) -> Signal`; `DEFAULT_LADDER_VERSION = "4"`; `LADDER_COMPATIBLE`; `_select_targets` uses it; `run()` calls the recheck at the end unless `recheck=False`. |
| `src/skysift/recheck.py` | `RecheckReport`, `recheck(store, blob, settings, *, fetch=net.fetch, signal_ids=None, pixels=True, log=None) -> RecheckReport`; the four steps as functions `step_ladder`, `step_period`, `step_catalogue`, `step_pixels`. |
| `src/skysift/lc/pixels.py` | Pure: `difference_images`, `snr_box_centroid`, `two_source_fit`, `leak_depth`, `localise(cube, ...) -> Localisation`, `season_verdicts`, `verdict`. |
| `src/skysift/lc/tpf.py` | `tpf_listing_url(kic) -> str`, `list_tpf_names(listing_html, kic) -> list[str]`, `fetch_tpfs(kic, *, fetch, blob, store, target_id) -> list[TPF]`, `parse_tpf(data) -> TPF` (cube, time, quality, quarter, wcs pixel positions). |
| `src/skysift/vetting/rung5_pixels.py` | `Rung5Pixels(name="rung5_pixels", cost=1000.0)`. |
| `src/skysift/cli.py` | `recheck` command; `run --no-recheck`. |
| `src/skysift/ui/queries.py`, `routes/review.py`, `routes/api.py`, templates | `signal_detail` adds `rechecks` and `latest_tests`; popup `recheck_history`; `GET /api/signals/{id}/pixels.svg`; signal page pixel figures. |
| `tests/unit/test_store.py`, `test_migrations.py`, `test_recheck.py`, `test_pixels.py`, `test_rung5_pixels.py`, `test_tpf.py`, `test_pipeline.py`, `test_cli.py`, `test_ui_review.py`, `tests/unit/ui_seed.py` | Tests named in the tasks. |

---

### Task 1: Status model, schema and store

**Files:**
- Modify: `src/skysift/store/db.py`, `src/skysift/store/migrations.py`
- Test: `tests/unit/test_store.py`, `tests/unit/test_migrations.py`, `tests/unit/test_decisions.py`

**Interfaces:**
- Produces:
```python
# db.py
FINAL_STATUS = {"pass": "candidate_stage1", "pass_pixels": "candidate_stage2", "fail": "rejected",
                "undetermined": "undetermined"}
ENGINE_STATUSES = ("pending", "candidate_stage1", "candidate_stage2", "undetermined", "rejected", "superseded")

class SignalRow:  # new columns
    parent_signal_id: Mapped[int | None] = mapped_column(ForeignKey("signals.id"), index=True)
    ladder_version: Mapped[str | None] = mapped_column(String(16))

class TestRow:
    recheck_id: Mapped[int | None] = mapped_column(ForeignKey("rechecks.id"))

class RecheckRow(Base):
    __tablename__ = "rechecks"
    id, signal_id (FK signals.id, index), step: str  # "ladder" | "period" | "catalogue" | "pixels"
    ladder_version: str, outcome: str  # "pass" | "fail" | "undetermined" | "skipped" | "superseded"
    reason: str = "", new_signal_id: int | None, utc: datetime = utcnow

def status_from_tests(tests: list[dict] | list[TestRow]) -> str
    # latest row per rung (highest id), "fail" if any fail, else "undetermined" if any undetermined, else "pass";
    # "pending" when there are no rows
def final_status_for(outcome: str, tests) -> str
    # "pass" -> "candidate_stage2" when the latest rung5_pixels row passed, else "candidate_stage1"; others via FINAL_STATUS

class Store:
    def latest_tests(self, signal_id: int) -> list[dict]      # one dict per rung, newest row wins, ladder order by id
    def add_recheck(self, signal_id, step, ladder_version, outcome, reason="", new_signal_id=None) -> int
    def rechecks(self, signal_id: int) -> list[dict]            # oldest first: step, ladder_version, outcome, reason, new_signal_id, utc
    def add_test(self, signal_id, r, duration_s, recheck_id=None) -> None
    def add_signal(self, sig, run_id, parent_signal_id=None, ladder_version=None) -> int
    def recheck_queue(self, detector_version: str) -> list[dict]
        # signals with final_status in ("undetermined", "candidate_stage1") and detector_version == given, no decisions row,
        # ordered by id; each dict: id, target_id, final_status, ladder_version, branch, period, t0, duration, depth_ppm,
        # extra, latest_tests
    def refresh_status(self, signal_id: int) -> str            # sets final_status = final_status_for(status_from_tests(latest), latest) unless superseded or reviewer-rejected; returns it
```
`record_results` writes `ladder_version` from the run row onto each new signal. `decide` after a non-reject decision calls the same derivation instead of hard-coding `candidate_stage1`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_store.py
def test_status_from_tests_uses_the_latest_row_per_rung():
    rows = [dict(id=1, rung="rung1_detection", status="pass"), dict(id=2, rung="rung4_novelty", status="undetermined"),
            dict(id=3, rung="rung4_novelty", status="pass")]
    assert status_from_tests(rows) == "pass" and status_from_tests([]) == "pending"
    assert final_status_for("pass", rows) == "candidate_stage1"
    assert final_status_for("pass", rows + [dict(id=4, rung="rung5_pixels", status="pass")]) == "candidate_stage2"

def test_recheck_rows_and_latest_tests(store_with_signal):
    store, sid = store_with_signal                       # one signal with rung1..rung4 rows, rung4 undetermined (outage)
    rid = store.add_recheck(sid, "catalogue", "4", "pass", "retried exoarchive")
    store.add_test(sid, TestResult(rung="rung4_novelty", status=TestStatus.PASS), 0.1, recheck_id=rid)
    latest = store.latest_tests(sid)
    assert [t["rung"] for t in latest] == ["rung1_detection", "rung1_events", "rung2_shape", "rung3_stability", "rung4_novelty"]
    assert latest[-1]["status"] == "pass" and latest[-1]["recheck_id"] == rid
    assert store.refresh_status(sid) == "candidate_stage1"
    assert store.rechecks(sid)[0]["step"] == "catalogue"

def test_recheck_queue_filters_status_version_and_decisions(store_with_signals):
    store, ids = store_with_signals     # candidate, undetermined, rejected, candidate with an approve decision, other version
    q = [r["id"] for r in store.recheck_queue("v+abc")]
    assert ids["candidate"] in q and ids["undetermined"] in q
    assert ids["rejected"] not in q and ids["approved"] not in q and ids["other_version"] not in q

def test_refresh_status_keeps_superseded_and_reviewer_rejections(store_with_signals):
    store, ids = store_with_signals
    store.set_signal_status(ids["candidate"], "superseded")
    assert store.refresh_status(ids["candidate"]) == "superseded"
    store.decide(ids["undetermined"], "reject", "bad")
    assert store.refresh_status(ids["undetermined"]) == "rejected"

# tests/unit/test_migrations.py
def test_step_002_adds_recheck_columns_to_an_old_database(old_schema_engine):
    applied = migrate(old_schema_engine)
    assert "002_recheck" in applied
    cols = {c["name"] for c in inspect(old_schema_engine).get_columns("signals")}
    assert {"parent_signal_id", "ladder_version"} <= cols
    assert "recheck_id" in {c["name"] for c in inspect(old_schema_engine).get_columns("tests")}
    assert "rechecks" in inspect(old_schema_engine).get_table_names()

# tests/unit/test_decisions.py
def test_un_reject_returns_to_the_engine_status_derived_from_tests(store_stage2_signal):
    store, sid = store_stage2_signal
    store.decide(sid, "reject", "mistake"); store.decide(sid, "approve")
    assert store.signal_status(sid) == "candidate_stage2"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/unit/test_store.py tests/unit/test_migrations.py tests/unit/test_decisions.py -q -p no:cacheprovider`
Expected: FAIL (`status_from_tests` undefined, no `rechecks` table).

- [ ] **Step 3: Implement the columns, the table, the migration step and the store methods**

`_step_002_recheck` adds the three columns (`INTEGER`, `VARCHAR(16)`, `INTEGER`) and the index on `signals.parent_signal_id`; `rechecks` itself comes from `create_all`. `latest_tests` groups by rung on `max(id)`. `recheck_queue` outer-joins `DecisionRow` and keeps rows with none.

- [ ] **Step 4: Run the tests, lint, full suite, commit**

`git commit -m "feat(store): recheck history, stage 2 status and latest-test derivation"`

---

### Task 2: Light-curve loader in the engine, re-vet helper, half-period signal

**Files:**
- Create: `src/skysift/lc/loader.py`
- Modify: `src/skysift/ui/plots.py` (import and re-export `LightCurveLoader`, `IntegrityError`, `_HoldBlob`, `_ProvenanceStore` from the new module; no behaviour change), `src/skysift/lc/ops.py`, `src/skysift/pipeline.py`
- Test: `tests/unit/test_plots.py` (existing loader tests keep passing through the re-export), `tests/unit/test_lc_ops.py`, `tests/unit/test_pipeline.py`

**Interfaces:**
- Produces:
```python
# lc/ops.py
@dataclass(frozen=True)
class MeasuredDip:
    depth_ppm: float; depth_err_ppm: float; snr: float; n_events: int; n_in: int; n_out: int
def measure_at(time, fnorm, period, t0, duration_days) -> MeasuredDip
    # in-transit |dt| < 0.5 duration vs out-of-transit duration <= |dt| <= 3 duration (finite points only);
    # error = std(out) * sqrt(1/n_in + 1/n_out); NaNs when n_in < 5 or n_out < 10; n_events = epochs with >= 1 in-transit point

# pipeline.py
def revet(lc: LightCurve, target: Target, th: Thresholds, sig: Signal, ladder: Ladder, *, fetch=net.fetch,
          store=None) -> tuple[str, list[TestResult]]
    # whitened_flux for primary and alt; restore_harmonics(lc.time, fn, record, [(1/sig.period, sig.depth_ppm)]) when
    # sig.extra["prewhitening"] has "restored"; DataBundle like _cpu_work; _vetting_bundle(bundle, sig); ladder.run
def halved_signal(sig: Signal, lc: LightCurve, fnorm) -> Signal
    # period = sig.period / 2, t0 and duration kept, depth/snr/n_events from measure_at, sde = sig.sde,
    # extra = {**sig.extra, "parent_period": sig.period, "sde_inherited": True, "halving": "rung2 suggested_period"}
```

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_lc_ops.py
def test_measure_at_recovers_an_injected_depth():
    lc = inject_transit(make_lightcurve(noise_ppm=300, seed=3), 10.0, 140.0, 0.12, 2000.0)
    fn = detrend(lc)
    m = measure_at(lc.time, fn, 10.0, 140.0, 0.12)
    assert abs(m.depth_ppm - 2000) < 150 and m.snr > 20 and m.n_events >= 100

def test_measure_at_without_points_is_nan():
    m = measure_at(np.array([1.0, 2.0]), np.array([1.0, 1.0]), 10.0, 0.0, 0.1)
    assert math.isnan(m.depth_ppm) and m.n_events == 0

# tests/unit/test_pipeline.py
def test_revet_reproduces_the_sweep_verdict(settings):
    lc = inject_transit(make_lightcurve(noise_ppm=500, seed=5), 12.0, 140.0, 0.1, 3000.0)
    out = json.loads(_cpu_work(pickle.dumps(lc), TARGET.model_dump_json(), settings.thresholds.model_dump_json(),
                               "box_dip", "4", True, fetch_module="tests.unit.fake_fetch"))
    item = out["signals"][0]; sig = Signal(**item["signal"])
    outcome, results = revet(lc, TARGET, settings.thresholds, sig, build_ladder("4"), fetch=fake_fetch.fetch)
    assert outcome == item["outcome"] and [r.rung for r in results] == [t["rung"] for t in item["tests"]]

def test_halved_signal_measures_the_true_period():
    lc = inject_transit(make_lightcurve(noise_ppm=400, seed=7), 8.0, 140.0, 0.1, 2500.0)
    fn = detrend(lc)
    doubled = Signal(target_id="t", detector="box_dip", detector_version="v", period=16.0, t0=140.0, duration=0.1,
                     depth_ppm=2500, snr=30, sde=20, n_events=80)
    half = halved_signal(doubled, lc, fn)
    assert half.period == 8.0 and abs(half.depth_ppm - 2500) < 200 and half.n_events > doubled.n_events
    assert half.extra["parent_period"] == 16.0 and half.sde == 20

def test_build_ladder_version_4_is_the_default_and_three_is_compatible():
    assert DEFAULT_LADDER_VERSION == "4" and LADDER_COMPATIBLE["4"] == ("3",)
```
Keep `test_plots.py` green: it imports `LightCurveLoader` from `skysift.ui.plots`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/unit/test_lc_ops.py tests/unit/test_pipeline.py -q -p no:cacheprovider -k "measure_at or revet or halved or version_4"`
Expected: FAIL.

- [ ] **Step 3: Move the loader, implement `measure_at`, `revet`, `halved_signal`, `LADDER_COMPATIBLE`, and `_select_targets` compatibility**

In `_select_targets`, a target is done when `store.job_done(key)` for the current ladder or for any version in `LADDER_COMPATIBLE.get(ladder_version, ())`. Add `test_select_targets_skips_a_target_done_under_ladder_3` in `test_pipeline.py` (a run row with ladder "3" and the same detector version makes the target skipped).

- [ ] **Step 4: Run the tests, lint, full suite, commit**

`git commit -m "feat(engine): loader in the engine, revet helper, half-period signal, ladder 4 compatibility"`

---

### Task 3: `skysift recheck` steps 1 to 3 and the CLI

**Files:**
- Create: `src/skysift/recheck.py`, `tests/unit/test_recheck.py`
- Modify: `src/skysift/cli.py`, `src/skysift/pipeline.py` (`run(..., recheck: bool = True)` calls `recheck.recheck(...)` after the summary and adds `summary["recheck"]`), `tests/unit/test_cli.py`

**Interfaces:**
- Consumes: Task 1 store methods, Task 2 `revet`, `halved_signal`, `LightCurveLoader`.
- Produces:
```python
@dataclass
class RecheckReport:
    considered: int = 0
    steps: dict[str, int] = field(default_factory=lambda: {"ladder": 0, "period": 0, "catalogue": 0, "pixels": 0})
    outcomes: dict[str, int] = field(default_factory=lambda: {"candidate_stage1": 0, "candidate_stage2": 0,
                                                              "undetermined": 0, "rejected": 0, "superseded": 0, "skipped": 0})
    seconds: float = 0.0
    def as_dict(self) -> dict

PIXEL_REASON_PREFIXES = ("season-dependent depth", "aperture-dependent depth", "systematic_alias", "period matches")

def recheck(store, blob, settings, *, fetch=net.fetch, signal_ids: list[int] | None = None, pixels: bool = True,
            detector_version: str | None = None, log=None) -> RecheckReport
    # queue = store.recheck_queue(detector_version or queries-free equivalent: the detector_version of the latest done run)
    # for each signal: load lc through LightCurveLoader(store, blob); apply step_ladder, step_period, step_catalogue, then
    # step_pixels (Task 5) when pixels and the signal still qualifies; store.refresh_status after each step
def step_ladder(ctx, sig) -> bool       # when sig.ladder_version != DEFAULT_LADDER_VERSION: revet with build_ladder(); rows with recheck_id
def step_period(ctx, sig) -> bool       # when the latest rung2 row has metrics["suggested_period"]: halved_signal, new run row
                                         # (job_key "recheck:P/2:<parent id>"), add_signal(parent_signal_id=...), branch="recheck:P/2",
                                         # ladder.run on it, record tests; parent -> superseded unless the child is undetermined with
                                         # a reason starting "equal secondary at phase 0.5"
def step_catalogue(ctx, sig) -> bool    # when the latest rung4 row's reason starts "outage:": run Rung4Novelty alone, add the row
```
Each step writes one `rechecks` row with the outcome (`"skipped"` with the reason when it could not run) and returns whether it ran. A signal whose curve cannot be loaded gets one `rechecks` row `step="ladder"`, `outcome="skipped"`, reason `"light curve unavailable: <error>"` and is left alone.

CLI: `skysift recheck [--signal-id N ...] [--no-pixels]` prints the report as JSON; `skysift run` gains `--no-recheck` and prints `recheck: ...` after the sweep summary.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_recheck.py  (helpers: a seeded store with a kept raw file through LocalBlob and a FakeSource that
# returns a synthetic KBonus-like LightCurve; `fake_fetch.fetch` for catalogues; `outage_fetch` raising ServiceUnavailable)
def test_ladder_revet_resolves_too_few_events_under_the_new_ladder(env):
    sid = env.seed_signal(ladder_version="3", tests=[..., ("rung2_shape", "undetermined", "too few odd/even events")])
    rep = recheck(env.store, env.blob, env.settings, fetch=fake_fetch.fetch, pixels=False)
    assert rep.steps["ladder"] == 1 and env.store.rechecks(sid)[0]["step"] == "ladder"
    assert env.store.signal_status(sid) in ("candidate_stage1", "rejected", "undetermined")
    assert env.store.latest_tests(sid)[0]["recheck_id"] is not None

def test_halving_supersedes_a_period_doubled_signal(env):
    # synthetic 8 d planet, stored signal at 16 d with rung2 undetermined and suggested_period = 8
    sid = env.seed_doubled_planet()
    rep = recheck(env.store, env.blob, env.settings, fetch=fake_fetch.fetch, pixels=False)
    assert rep.steps["period"] == 1 and env.store.signal_status(sid) == "superseded"
    child = env.store.children(sid)[0]
    assert child["branch"] == "recheck:P/2" and abs(child["period"] - 8.0) < 0.01 and child["parent_signal_id"] == sid
    assert child["final_status"] == "candidate_stage1"

def test_halving_rejects_an_eclipsing_binary_at_half_period(env):
    sid = env.seed_doubled_binary()         # alternating 3000 / 1500 ppm dips at 8 d stored as a 16 d signal
    recheck(env.store, env.blob, env.settings, fetch=fake_fetch.fetch, pixels=False)
    child = env.store.children(sid)[0]
    assert child["final_status"] == "rejected" and "odd_even" in env.store.latest_tests(child["id"])[2]["reason"]

def test_halving_does_not_supersede_when_the_half_period_is_undetermined_for_the_same_reason(env):
    sid = env.seed_doubled_planet(child_reason="equal secondary at phase 0.5: true period may be P / 2 (4 d)")
    recheck(env.store, env.blob, env.settings, fetch=fake_fetch.fetch, pixels=False)
    assert env.store.signal_status(sid) == "undetermined"
    assert env.store.rechecks(sid)[-1]["outcome"] == "undetermined"

def test_catalogue_retry_after_an_outage(env):
    sid = env.seed_signal(tests=[..., ("rung4_novelty", "undetermined", "outage:exoarchive: HTTP 403")])
    rep = recheck(env.store, env.blob, env.settings, fetch=fake_fetch.fetch, pixels=False)
    assert rep.steps["catalogue"] == 1 and env.store.latest_tests(sid)[-1]["status"] == "pass"
    assert env.store.signal_status(sid) == "candidate_stage1"

def test_catalogue_retry_that_fails_again_stays_undetermined(env):
    sid = env.seed_signal(tests=[..., ("rung4_novelty", "undetermined", "outage:exoarchive: HTTP 403")])
    recheck(env.store, env.blob, env.settings, fetch=outage_fetch, pixels=False)
    assert env.store.signal_status(sid) == "undetermined" and len(env.store.rechecks(sid)) == 1

def test_recheck_skips_a_signal_whose_curve_cannot_be_loaded(env):
    sid = env.seed_signal(ladder_version="3", raw_file=False, source_down=True)
    rep = recheck(env.store, env.blob, env.settings, fetch=fake_fetch.fetch, pixels=False)
    assert rep.outcomes["skipped"] == 1 and "light curve unavailable" in env.store.rechecks(sid)[0]["reason"]

def test_recheck_skips_signals_with_a_reviewer_decision(env):
    sid = env.seed_signal(ladder_version="3"); env.store.decide(sid, "approve")
    assert recheck(env.store, env.blob, env.settings, fetch=fake_fetch.fetch, pixels=False).considered == 0

def test_recheck_is_idempotent(env):
    env.seed_signal(ladder_version="3")
    recheck(env.store, env.blob, env.settings, fetch=fake_fetch.fetch, pixels=False)
    before = env.store.counts()
    rep = recheck(env.store, env.blob, env.settings, fetch=fake_fetch.fetch, pixels=False)
    assert rep.steps == {"ladder": 0, "period": 0, "catalogue": 0, "pixels": 0} and env.store.counts() == before

# tests/unit/test_cli.py
def test_recheck_command_prints_a_report(cli_env):
    r = runner.invoke(app, ["recheck", "--no-pixels"])
    assert r.exit_code == 0 and '"considered"' in r.stdout

def test_run_calls_the_recheck_unless_disabled(cli_env, monkeypatch):
    calls = []; monkeypatch.setattr("skysift.recheck.recheck", lambda *a, **k: calls.append(1) or RecheckReport())
    runner.invoke(app, ["run", "--source", "fake", "--limit", "1"]); assert calls
    runner.invoke(app, ["run", "--source", "fake", "--limit", "1", "--no-recheck"]); assert len(calls) == 1
```
`env.seed_doubled_planet()` injects an 8 d, 2500 ppm, 0.1 d transit into `make_lightcurve(noise_ppm=400)`, stores the raw file through the source's parser hook (`PARSERS["fake"]` returning the pickled curve), and stores a 16 d signal with rung rows 1 to 4 where rung 2 is undetermined with `metrics["suggested_period"] = 8.0`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/unit/test_recheck.py -q -p no:cacheprovider`
Expected: FAIL with `ModuleNotFoundError: skysift.recheck`.

- [ ] **Step 3: Implement `recheck.py`, the CLI command and the `run` hook**

The recheck logs one line per signal and step at INFO. `Store.children(signal_id)` and `Store.signal_status(signal_id)` are small helpers added here. The `run` hook passes `settings`, the store, the blob and `fetch_module` resolution (the same `fetch` the sweep used).

- [ ] **Step 4: Run the tests, lint, full suite, commit**

`git commit -m "feat(engine): skysift recheck with ladder re-vet, half-period re-vet and catalogue retry"`

---

### Task 4: Pixel numerics (`skysift.lc.pixels`)

**Files:**
- Create: `src/skysift/lc/pixels.py`, `tests/unit/test_pixels.py`
- Reference (read, do not import): `docs/candidates/kic-006146676/localisation/diffimage.py` (`flux_centroid`, `box_sum`, `gauss_psf`, `two_source_fit`, `stack`, `centroid_errors`, the summary block), `docs/candidates/kic-006146676/context/a2_leak_test.py` (`box`, `measure`).

**Interfaces:**
- Produces:
```python
PIX_ARCSEC = 3.98
N_MC = 200

@dataclass(frozen=True)
class Ephemeris: period: float; t0: float; duration: float          # days, BKJD, days

@dataclass
class SeasonImage:
    season: int; quarters: list[int]; n_events: int
    diff: np.ndarray; var: np.ndarray; direct: np.ndarray; valid: np.ndarray     # 2-D, same shape

@dataclass
class SeasonResult:
    season: int; n_events: int
    centroid_colrow: tuple[float, float]; centroid_sigma_pix: float            # S/N-box 3x3 centroid, MC error
    dist_target_pix: float; dist_neighbour_pix: float; nearer: str              # "target" | "neighbour"
    separation_sigma: float                                                     # (dist_neighbour - dist_target) / sigma
    depth_target_ppm: tuple[float, float]; depth_neighbour_ppm: tuple[float, float]  # two-source implied depth, error
    max_snr: float

@dataclass
class Localisation:
    seasons: list[SeasonResult]; n_events_used: int; n_quarters_usable: int; unusable_quarters: list[int]
    verdict: str          # "pass" | "fail" | "undetermined"
    reason: str
    def metrics(self) -> dict    # JSON-ready, the fields spec 4.4 lists

def difference_images(cube, time, quality, quarter, eph, *, in_half=0.5, out_lo=1.0, out_hi=3.0) -> list[SeasonImage]
    # per quarter: for each event with >= 3 in-transit and >= 6 out-of-transit good cadences, mean(out) - mean(in) per pixel
    # with variance from the out-of-transit scatter; quarters with < 2 events are unusable; stack by season
    # (season_of(quarter)) with inverse-variance weights
def snr_box_centroid(diff, var, valid, rng) -> tuple[tuple[float, float], float, float]
    # 3x3 flux-weighted centroid around argmax(diff / sqrt(var)); N_MC draws of diff + N(0, sqrt(var)) give sigma; also max S/N
def two_source_fit(direct, diff, var, valid, pos_target, pos_neighbour) -> dict
    # Gaussian PSF (free sigma, common shift) on the direct image for amplitudes; the same PSF on the difference image
    # for difference amplitudes; implied depth = diff amp / direct amp per star, errors from the least-squares Jacobian
def leak_depth(time, flux, quality, eph) -> tuple[float, float]         # box depth and error (ppm) at the ephemeris, a2 `box` method
def localise(cube, time, quality, quarter, eph, pos_target, pos_neighbour, *, rng=None) -> Localisation
def verdict(seasons: list[SeasonResult], n_events_used: int) -> tuple[str, str]
    # undetermined "pixels: N usable transits < 3" when n_events_used < 3
    # pass when every season with >= 2 events has nearer == "target", separation_sigma > 3 and |depth_neighbour| < 3 sigma
    # fail when a majority of seasons has nearer == "neighbour" or depth_neighbour > 3 sigma and depth_target < 1 sigma
    # else undetermined "pixels: season S disagrees (centroid nearer <x>, <k> sigma)"
```

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_pixels.py
def make_cube(dip_on="target", n_days=372, depth=3000e-6, seed=0, neighbour_ratio=5.0, n_quarters=4):
    # 9x9 pixels, two Gaussian stars sigma 0.9 px at (4.0, 4.0) target and (6.8, 4.6) neighbour (neighbour brighter by ratio),
    # cadence 29.4 min, white noise per pixel from photon counts, quarters of 93 d, quality 0; a 10 d, 3 h transit of
    # `depth` on the chosen star; returns (cube, time, quality, quarter, eph, pos_target, pos_neighbour)

def test_difference_images_recover_the_dip_pixels():
    cube, time, quality, quarter, eph, pt, pn = make_cube("target")
    seasons = difference_images(cube, time, quality, quarter, eph)
    assert len(seasons) == 4 and all(s.n_events >= 2 for s in seasons)
    s = seasons[0]; r, c = np.unravel_index(np.argmax(s.diff / np.sqrt(s.var)), s.diff.shape)
    assert abs(c - pt[0]) < 1.1 and abs(r - pt[1]) < 1.1

def test_localise_passes_a_dip_on_the_target():
    loc = localise(*make_cube("target"))
    assert loc.verdict == "pass" and all(s.nearer == "target" and s.separation_sigma > 3 for s in loc.seasons)
    assert abs(loc.seasons[0].depth_neighbour_ppm[0]) < 3 * loc.seasons[0].depth_neighbour_ppm[1]

def test_localise_fails_a_dip_on_the_neighbour():
    loc = localise(*make_cube("neighbour"))
    assert loc.verdict == "fail" and "neighbour" in loc.reason

def test_localise_needs_three_usable_transits():
    loc = localise(*make_cube("target", n_days=25))        # two transits only
    assert loc.verdict == "undetermined" and loc.reason.startswith("pixels: 2 usable transits < 3")

def test_localise_is_undetermined_when_a_season_disagrees():
    cube, *rest = make_cube("target"); eph = rest[3]
    # move the dip to the neighbour in season 2 only
    loc = localise(_swap_season(cube, rest, season=2), *rest)
    assert loc.verdict == "undetermined" and "season 2" in loc.reason

def test_leak_depth_sees_an_injected_dip_and_nothing_elsewhere():
    t = np.arange(131.5, 131.5 + 400, 29.4 / 1440); f = 1 + np.random.default_rng(1).normal(0, 3e-4, t.size)
    eph = Ephemeris(10.0, 140.0, 0.125); ph = (t - eph.t0 + 5) % 10 - 5
    d, e = leak_depth(t, f * np.where(np.abs(ph) < 0.0625, 1 - 1e-3, 1), np.zeros(t.size, int), eph)
    assert abs(d - 1000) < 5 * e
    d0, e0 = leak_depth(t, f, np.zeros(t.size, int), eph); assert abs(d0) < 4 * e0

def test_metrics_are_json_ready():
    json.dumps(localise(*make_cube("target")).metrics())
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/unit/test_pixels.py -q -p no:cacheprovider`
Expected: FAIL with `ModuleNotFoundError: skysift.lc.pixels`.

- [ ] **Step 3: Port the numerics**

Keep the dossier's estimator choices: the S/N-box centroid (not the brightest pixel), inverse-variance season stacks, Gaussian PSF two-source fit with a free common shift, `scipy.optimize.least_squares`. Use `season_of` from `lc/ops.py` for season grouping. Every array result is cast to Python floats in `metrics()`.

- [ ] **Step 4: Run the tests, lint, full suite, commit**

`git commit -m "feat(lc): difference-image localisation numerics ported from the KIC 6146676 dossier"`

---

### Task 5: TPF access, `rung5_pixels`, recheck step 4, ladder version 4

**Files:**
- Create: `src/skysift/lc/tpf.py`, `src/skysift/vetting/rung5_pixels.py`, `tests/unit/test_tpf.py`, `tests/unit/test_rung5_pixels.py`
- Modify: `src/skysift/pipeline.py` (`build_ladder(version, with_novelty=True, with_pixels=False)`), `src/skysift/recheck.py` (`step_pixels`), `src/skysift/store/db.py` (`kind="tpf"` artifacts accepted; `pin_target_artifacts` covers them), `tests/unit/test_recheck.py`

**Interfaces:**
- Consumes: Task 4 `localise`, `leak_depth`, `Ephemeris`; Task 3 `recheck` context; `net.fetch` signature `fetch(url, *, params=None, service, store=None, target_id=None, timeout=60.0, ...)`.
- Produces:
```python
# lc/tpf.py
TPF_BASE = "https://archive.stsci.edu/missions/kepler/target_pixel_files/{kic4}/{kic9}/"
@dataclass
class TPF:
    quarter: int; time: np.ndarray; quality: np.ndarray; cube: np.ndarray    # (n, rows, cols) FLUX
    wcs: astropy.wcs.WCS; name: str; sha256: str
    def pixel_of(self, ra: float, dec: float) -> tuple[float, float]         # (col, row) zero-based
def tpf_listing_url(kic: int) -> str
def list_tpf_names(listing_html: str, kic: int) -> list[str]                 # kplr{kic9}-{13 digits}_lpd-targ.fits.gz, sorted
def parse_tpf(data: bytes, name: str = "") -> TPF                           # gzip or plain FITS; quarter from header QUARTER
def fetch_tpfs(kic: int, *, fetch, blob, store, target_id: str, timeout: float = 600.0) -> list[TPF]
    # one listing request (service "mast") then one per file; each file stored through blob.put as kind "tpf" with
    # add_artifact(target_id=..., kind="tpf"); an existing undeleted artifact is read from the blob instead of refetched;
    # raises ServiceUnavailable when the listing fails or no file is reachable

# vetting/rung5_pixels.py
class Rung5Pixels:
    name = "rung5_pixels"; cost = 1000.0
    def __init__(self, tpf_loader=fetch_tpfs, rng_seed: int = 20261020): ...
    def run(self, s: Signal, b: DataBundle) -> TestResult
        # kic = b.target.meta.get("tpf_org") or b.lc.meta.get("tpf_org") or b.target.kic; None -> UNDETERMINED "pixels: no pixel files for this star"
        # neighbour = the Kepler target (kic) when it differs from the target, positioned from its RA/Dec in
        # b.target.meta["tpf_org_ra"/"tpf_org_dec"] when present, else from the TPF header RA_OBJ/DEC_OBJ;
        # when kic == target.kic: UNDETERMINED "pixels: no neighbour to test against"
        # localise per TPF set; leak test on the neighbour's SAP_FLUX summed over the aperture of the TPF (APERTURE bitmask)
        # pass/fail/undetermined from Localisation.verdict; reasons: fail "the dip is on a neighbouring star (<k> sigma, <n>/<m> seasons)",
        # metrics = Localisation.metrics() + {"leak_depth_ppm": [d, e], "tpf_manifest": [names], "n_tpf": n,
        #                                     "pixels_sha256": sha}
        # and the arrays the dossier draws are stored once as a `pixels.npz` artifact (kind "pixels", pinned with the
        # signal, url "skysift://pixels/<signal id>"): per-season diff, var, direct, valid (stacked 3-D arrays), the
        # season ids, pos_target and pos_neighbour (col, row); written through DataBundle.store when it is a Store
        # (the recheck runs in-process), skipped with a logged warning inside a worker recorder
        # ServiceUnavailable -> UNDETERMINED "outage:mast: <e>"
```
`build_ladder("4", with_pixels=True)` appends `Rung5Pixels()`; the sweep keeps `with_pixels=False`. `step_pixels(ctx, sig)` runs when the signal's status after steps 1 to 3 is `candidate_stage1`, or `undetermined` with a latest undetermined reason starting with one of `PIXEL_REASON_PREFIXES`; it runs `Rung5Pixels` alone through the bundle from `revet`'s construction, writes the row with the recheck id and refreshes the status.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_tpf.py
def test_list_tpf_names_parses_the_mast_listing():
    html = '<a href="kplr006146666-2009131105131_lpd-targ.fits.gz">x</a> <a href="kplr006146666-2009131105131_spd-targ.fits.gz">'
    assert list_tpf_names(html, 6146666) == ["kplr006146666-2009131105131_lpd-targ.fits.gz"]
    assert tpf_listing_url(6146666).endswith("/0061/006146666/")

def test_parse_tpf_reads_cube_quarter_and_wcs(synthetic_tpf_bytes):
    tpf = parse_tpf(synthetic_tpf_bytes, "k.fits")
    assert tpf.cube.shape[1:] == (9, 9) and tpf.quarter == 3
    col, row = tpf.pixel_of(*synthetic_tpf_bytes.target_radec); assert abs(col - 4.0) < 0.05 and abs(row - 4.0) < 0.05

def test_fetch_tpfs_logs_stores_and_reuses(store, blob, synthetic_tpf_bytes):
    fetch = FakeMast({"listing": LISTING, "kplr...": synthetic_tpf_bytes})
    tpfs = fetch_tpfs(6146666, fetch=fetch, blob=blob, store=store, target_id="t")
    assert len(tpfs) == 1 and fetch.calls == 2 and store.counts()["artifacts"] == 1
    fetch_tpfs(6146666, fetch=fetch, blob=blob, store=store, target_id="t"); assert fetch.calls == 3   # listing only

def test_fetch_tpfs_raises_service_unavailable_when_mast_is_down(store, blob):
    with pytest.raises(ServiceUnavailable): fetch_tpfs(1, fetch=outage_fetch, blob=blob, store=store, target_id="t")

# tests/unit/test_rung5_pixels.py  (a loader returning synthetic TPFs built from tests/unit/test_pixels.make_cube with a WCS)
def test_rung5_passes_when_the_dip_is_on_the_target(bundle_with_tpfs):
    r = Rung5Pixels(tpf_loader=loader("target")).run(SIGNAL, bundle_with_tpfs)
    assert r.status == TestStatus.PASS and r.metrics["n_events_used"] >= 3 and r.metrics["tpf_manifest"]

def test_rung5_fails_when_the_dip_is_on_the_neighbour(bundle_with_tpfs):
    r = Rung5Pixels(tpf_loader=loader("neighbour")).run(SIGNAL, bundle_with_tpfs)
    assert r.status == TestStatus.FAIL and r.reason.startswith("the dip is on a neighbouring star")

def test_rung5_without_a_neighbour_is_undetermined(bundle_same_star):
    r = Rung5Pixels(tpf_loader=loader("target")).run(SIGNAL, bundle_same_star)
    assert r.status == TestStatus.UNDETERMINED and r.reason == "pixels: no neighbour to test against"

def test_rung5_outage_is_undetermined(bundle_with_tpfs):
    r = Rung5Pixels(tpf_loader=raising(ServiceUnavailable("mast"))).run(SIGNAL, bundle_with_tpfs)
    assert r.status == TestStatus.UNDETERMINED and r.reason.startswith("outage:mast")

def test_build_ladder_with_pixels_appends_rung5():
    assert [r.name for r in build_ladder("4", with_pixels=True).rungs][-1] == "rung5_pixels"
    assert "rung5_pixels" not in [r.name for r in build_ladder("4").rungs]

# tests/unit/test_recheck.py
def test_pixel_step_promotes_a_candidate_to_stage_2(env):
    sid = env.seed_signal(ladder_version="4", status="candidate_stage1", tpfs="target")
    rep = recheck(env.store, env.blob, env.settings, fetch=fake_fetch.fetch, tpf_loader=env.tpf_loader)
    assert rep.steps["pixels"] == 1 and env.store.signal_status(sid) == "candidate_stage2"

def test_pixel_step_rejects_a_neighbour_dip_and_runs_for_pixel_reasons(env):
    sid = env.seed_signal(ladder_version="4", tests=[..., ("rung3_stability", "undetermined", "season-dependent depth (p=5.7e-04)")], tpfs="neighbour")
    recheck(env.store, env.blob, env.settings, fetch=fake_fetch.fetch, tpf_loader=env.tpf_loader)
    assert env.store.signal_status(sid) == "rejected"

def test_pixel_step_does_not_run_for_outage_reasons(env):
    sid = env.seed_signal(ladder_version="4", tests=[..., ("rung4_novelty", "undetermined", "outage:exoarchive: HTTP 403")])
    recheck(env.store, env.blob, env.settings, fetch=outage_fetch, tpf_loader=env.tpf_loader)
    assert [r["step"] for r in env.store.rechecks(sid)] == ["catalogue"]
```
`recheck()` gains the keyword `tpf_loader=fetch_tpfs` passed into `Rung5Pixels`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/unit/test_tpf.py tests/unit/test_rung5_pixels.py tests/unit/test_recheck.py -q -p no:cacheprovider`
Expected: FAIL.

- [ ] **Step 3: Implement `tpf.py`, the rung, `step_pixels`, `build_ladder(with_pixels)`**

The synthetic TPF fixture writes a FITS with a primary header (`KEPLERID`, `QUARTER`, `RA_OBJ`, `DEC_OBJ`), a `TARGETTABLES` binary table (`TIME`, `QUALITY`, `FLUX` cube) and an `APERTURE` image with a TAN WCS whose reference pixel is the target.

- [ ] **Step 4: Run the tests, lint, full suite, commit**

`git commit -m "feat(engine): rung5_pixels difference-image localisation and the pixel recheck step"`

---

### Task 6: Dashboard integration of the recheck

**Files:**
- Modify: `src/skysift/ui/queries.py` (`signal_detail` adds `rechecks: list[dict]`, `latest_tests`, `children`, `parent`), `src/skysift/ui/routes/review.py` (popup passes `recheck_history`; `why` rows built from `latest_tests`), `src/skysift/ui/routes/api.py` (`GET /api/signals/{id}/pixels.svg`), `src/skysift/ui/explain.py` (reason prefixes `pixels:`, `the dip is on a neighbouring star`, `outage:mast`; `RUNG_NAMES["rung5_pixels"]`), `src/skysift/ui/templates/partials/verdict.html`, `signal.html` (pixel figures section when a `rung5_pixels` row exists; "superseded by" / "supersedes" links), `tests/unit/ui_seed.py` (the stage-2 signal gains a `rechecks` row and a `rung5_pixels` row with per-season metrics), `tests/unit/test_ui_review.py`, `tests/unit/test_ui_explain.py`

**Interfaces:**
- Consumes: Task 1 `Store.rechecks`, `latest_tests`, `children`; Task 4 `Localisation.metrics()` shape (`seasons[]` with `centroid_colrow`, `dist_target_pix`, `dist_neighbour_pix`, `centroid_sigma_pix`, `nearer`, `depth_target_ppm`, `depth_neighbour_ppm`).
- Produces: `GET /api/signals/{id}/pixels.svg` renders, from the stored `rung5_pixels` metrics only (no pixel data needed), one panel per season: target and neighbour positions, the centroid with its error circle, the "nearer" word; 404 when no such row; `image/svg+xml`. The popup lists `recheck_history` as "date, step in words, outcome" lines (`step` → "Re-vetted under ladder 4", "Re-vetted at half the period", "Catalogue asked again", "Pixels checked").

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_ui_review.py
def test_verdict_popup_lists_recheck_history_and_uses_latest_rows(seeded):
    client, ids = seeded
    html = client.get(f"/partials/signals/{ids['stage2']}/verdict").text
    assert "Pixels checked" in html and "stage 2" in html and html.count('class="why-row') == 6

def test_pixels_svg_renders_one_panel_per_season(seeded):
    client, ids = seeded
    r = client.get(f"/api/signals/{ids['stage2']}/pixels.svg")
    assert r.status_code == 200 and r.text.count("<g class=\"season") == 4 and "nearer target" in r.text
    assert client.get(f"/api/signals/{ids['candidate']}/pixels.svg").status_code == 404

def test_signal_page_links_superseded_pairs(seeded):
    client, ids = seeded
    assert f"/signals/{ids['halved_child']}" in client.get(f"/signals/{ids['superseded']}").text
    assert "Superseded" in client.get(f"/partials/signals/{ids['superseded']}/verdict").text

# tests/unit/test_ui_explain.py
@pytest.mark.parametrize("reason", ["pixels: 2 usable transits < 3", "pixels: season 2 disagrees (centroid nearer neighbour, 4.1 sigma)",
                                    "pixels: no neighbour to test against", "outage:mast: HTTP 503"])
def test_pixel_reasons_map_to_words(reason):
    missing, nxt = explain.reason_in_words(reason); assert missing != reason and nxt != "needs a person"
```

- [ ] **Step 2: Run to verify they fail; Step 3: implement; Step 4: tests, lint, full suite**

- [ ] **Step 5: Run the recheck for real on the sample database and look**

```
SKYSIFT_DATA_DIR=.superpowers/sample-run SKYSIFT_BLOB_URL=file://.superpowers/sample-run/blobs SKYSIFT_DATABASE_URL=sqlite:///.superpowers/sample-run/engine.db SKYSIFT_RETENTION=keep PYTHONUTF8=1 .venv/Scripts/python.exe -m skysift recheck
```
Run it only while no sweep is writing to that database (the cool-dwarf sweep and the pilot write to it; wait for both or run against a copy of `engine.db` and the blobs). Expected: the kic-006146676 80.58 d signal superseded by a 40.29 d child; kic-006271633 18.19 d child at 9.10 d rejected (odd/even); the exoarchive outages retried; kic-006146676 40.29 d and kic-005689121 13.77 d reaching `candidate_stage2` with centroids matching the dossier (0.10 to 0.20 px from the candidate). Record the report JSON in the SDD workspace and open the two signal pages in the dashboard for screenshots.

- [ ] **Step 6: Commit**

`git commit -m "feat(ui): recheck history, pixel figures and stage 2 in the dashboard"`
