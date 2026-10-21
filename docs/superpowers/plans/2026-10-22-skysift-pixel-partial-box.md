# SkySift Pixel Rung Partial-Box Voting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a rung 5 season vote when the target's or the neighbour's 3 x 3 box is cut by the stamp edge or a dead pixel (centre present, at least 6 of 9 pixels), with a computed edge allowance added to the centroid error, the missing pixels recorded, and the dossier, figure, explain sentences and pack reading the new fields; complete boxes vote exactly as before.

**Architecture:** The box test in `skysift.lc.pixels` becomes a measured `BoxCoverage` instead of a boolean; `localise_quarters` computes the allowance from a rendered Gaussian at the star's position and inflates the centroid errors in quadrature before the bisector statistic; the verdict code is unchanged apart from eligibility. `rung5_pixels` adds one count. The UI, pack and dossier read the new per-season keys as optional. A small committed tool reruns rung 5 for named signals against a read-only database copy for the real-data check.

**Tech Stack:** Python 3.12, numpy, scipy, astropy, pytest. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-10-22-skysift-pixel-partial-box-design.md` (authority), amending section 4.4 of `docs/superpowers/specs/2026-10-20-skysift-mission-control-design.md`.

## Global Constraints

- Every command is `PYTHONUTF8=1 .venv/Scripts/python.exe -m <module>` run from the worktree root; never bare `python`, never a console `.exe` under `.venv/Scripts`. pytest always with `-p no:cacheprovider`.
- Branch `feat/pixel-partial-box` in the worktree `.worktrees/pixel-partial-box` off `main`. Conventional commits ending with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`. Stage by name (`git add <paths>`), never `git add -A`; no `git stash`; never push.
- `python -m ruff check src tests tools` clean (line length 120). The named test files of each task green before every commit; the full `tests/unit` suite is the controller's gate per task, the full `-m "not slow and not golden"` suite the final gate.
- Copy rules: no em-dashes anywhere (code, docstrings, generated text); British spelling except the `-ize` family; "candidate", never "discovery"; every reason string carries its deciding numbers.
- Stored strings do not change: the non-voting reasons stay `"box incomplete: target"` and `"box incomplete: neighbour"`; the verdict reasons keep their wording and form. New metric keys are optional for every consumer: a row without them renders as today.
- Thresholds unchanged: `SEPARATION_SIGMA = 3.0`, `NEIGHBOUR_DEPTH_SIGMA = 3.0`, `PASS_SEASONS_MIN = 2`, `SEASON_EVENTS_MIN = 2`, `TRANSITS_MIN = 3`. New constants in `skysift.lc.pixels`: `BOX_PIXELS_MIN = 6`, `EDGE_PSF_SIGMA_PIX = 0.6`, `EDGE_PAD = 2`.
- Nothing in this plan writes to `.superpowers/sample-run/engine.db` or to the blob store under `.superpowers/sample-run/blobs`. The real-data check reads a backup-API copy of the database opened read-only and reads blob files only.
- Metrics stay strict JSON (no NaN, inf or arrays); `tests/unit/test_rung5_pixels.py::_strict` must accept them.

## Review Focus

1. **A season with a complete box in every quarter** must produce byte-identical metrics to the committed code (same centroid, errors, separation, verdict). Pinned by Task 2 `test_complete_boxes_vote_exactly_as_before`.
6. **A cut season that cannot decide** (ambiguous, or a neighbour depth near the veto) must abstain with a stored reason rather than block the complete seasons' pass, and must not be counted as voting anywhere. Pinned by Task 2 `test_a_cut_box_votes_only_when_decisive`.
7. **A settled pass or fail, or an undetermined row for another pixel reason**, must not be rerun by the recheck after this change. Pinned by Task 6 `test_the_recheck_reopens_a_pixel_row_the_box_rule_silenced`.
2. **The target in a stamp corner** (4 of 9 pixels) must still not vote, with the old reason, so the relaxation does not reach corners. Pinned by Task 2 `test_a_corner_box_does_not_vote` and Task 3 `test_a_target_in_the_stamp_corner_is_undetermined_with_the_frame_reason`.
3. **The dip on the neighbour while the target's box is cut** must fail, not pass: the allowance widens the error but the sign of the statistic is the data's. Pinned by Task 2 `test_a_season_with_the_target_on_the_stamp_edge_votes_with_an_allowance` (its "neighbour" branch) and Task 3 `test_a_dip_on_the_neighbour_fails_with_the_target_on_the_stamp_edge`.
4. **A rung 5 row stored before this change** (no `box_partial`, no `box_missing_*`, no `edge_bias_pix`) must render on the signal page, in the figure, in the pack and in the caveats exactly as today. Pinned by Task 4 `test_a_season_without_box_keys_renders_as_before` and Task 5 `test_pixel_sentence_without_box_keys_is_unchanged`.
5. **A partial box whose two-source fit fails** (`psf_sigma_pix` NaN) must still get an allowance from the default width, never a NaN error or a vote on an uninflated statistic. Pinned by Task 2 `test_the_allowance_falls_back_to_the_default_width`.

---

## File structure

| Path | Responsibility |
|---|---|
| `src/skysift/lc/pixels.py` | `BoxCoverage`, `box_coverage`, `edge_allowance`; `SeasonImage.box_target/box_neighbour`; `SeasonResult` new fields; `localise_quarters` eligibility, allowance and inflated errors; `metrics()` new keys; module docstring updated. |
| `src/skysift/vetting/rung5_pixels.py` | `n_seasons_partial_box` and `rule_version` metrics, `PIXEL_RULE_VERSION = 2`. |
| `src/skysift/recheck.py` | `box_rule_reopens`; `wants_pixels` reruns the rows the box rule silenced. |
| `tools/pixels_rerun.py` | Read-only rerun of rung 5 for named signals against a database copy; study comparison. |
| `src/skysift/ui/explain.py` | `_pixels` reads `box_partial`, `box_pixels_*`, `edge_bias_pix`. |
| `src/skysift/ui/pixel_figure.py`, `src/skysift/ui/static/app.css` | Vote line, missing cells (`.pf-missing`), label and `<desc>`. |
| `src/skysift/dossier.py` | `partial_box_seasons`, `partial_box_sentence`, the `partial_box` caveat. |
| `src/skysift/pack.py` | `pixel_sentence` and the RNAAS draft use `partial_box_sentence`. |
| `tests/unit/test_pixels.py`, `tests/unit/synthetic_tpf.py`, `tests/unit/test_rung5_pixels.py`, `tests/unit/test_ui_explain.py`, `tests/unit/test_ui_review.py`, `tests/unit/test_pack.py`, `tests/unit/test_dossier.py` | Tests named in the tasks. |

---

### Task 1: Box coverage and the edge allowance (pure numerics)

**Files:**
- Modify: `src/skysift/lc/pixels.py` (replace `_box_on_frame` at lines 245-253; add the constants next to `SEPARATION_SIGMA`)
- Test: `tests/unit/test_pixels.py`

**Interfaces:**
- Produces:
  - `BOX_PIXELS_MIN = 6`, `EDGE_PSF_SIGMA_PIX = 0.6`, `EDGE_PAD = 2`.
  - `@dataclass(frozen=True) class BoxCoverage: valid: int; centre: bool; missing: tuple[tuple[int, int], ...]` with properties `complete` (`valid == 9`), `partial` (`centre and BOX_PIXELS_MIN <= valid < 9`), `eligible` (`complete or partial`), and `metrics(self) -> dict` returning `{"pixels": int, "missing": [[dcol, drow], ...]}`. `ALL_MISSING = BoxCoverage(0, False, <the nine offsets row-major>)` is the module constant for an unplaced star.
  - `box_coverage(pos, valid: np.ndarray) -> BoxCoverage`: the 3 x 3 box around `(round(col), round(row))`; a pixel is missing when off the frame or `valid` is False there; `missing` lists `(dcol, drow)` offsets in row-major order (drow -1 first, then dcol -1, 0, 1). A non-finite position returns `ALL_MISSING`.
  - `edge_allowance(pos, valid: np.ndarray, psf_sigma: float, pos_other) -> tuple[float, float]`: `(bias, bias_axis)` in pixels. Render `gauss_psf` of width `psf_sigma` at `pos` on a frame padded by `EDGE_PAD` pixels on every side (positions shifted by `EDGE_PAD`); take the 3 x 3 flux-weighted centroid (`_box_centroid`) around the brightest valid pixel twice, with the padded `valid` (padding False) and with every pixel valid; `bias` is the length of the difference, `bias_axis` the absolute projection on the unit vector from `pos_other` to `pos`. Returns `(0.0, 0.0)` when `box_coverage(pos, valid).complete`, and `(nan, nan)` when the position or `pos_other` is non-finite or the two coincide.

- [ ] **Step 1: Write the failing tests** in `tests/unit/test_pixels.py` (import `BoxCoverage`, `box_coverage`, `edge_allowance`, `BOX_PIXELS_MIN`, `ALL_MISSING`)

```python
def test_box_coverage_counts_the_pixels_on_the_frame():
    valid = np.ones(SHAPE, bool)
    assert box_coverage((4.0, 4.0), valid) == BoxCoverage(9, True, ())
    edge = box_coverage((4.0, 0.1), valid)
    assert (edge.valid, edge.centre, edge.missing) == (6, True, ((-1, -1), (0, -1), (1, -1)))
    assert edge.partial and edge.eligible and not edge.complete
    corner = box_coverage((0.2, -0.3), valid)
    assert corner.valid == 4 and corner.centre and not corner.partial and not corner.eligible
    valid[3, 4] = False                                   # row 3, col 4: offset (0, -1) from (4, 4)
    hole = box_coverage((4.0, 4.0), valid)
    assert hole == BoxCoverage(8, True, ((0, -1),)) and hole.partial
    valid[4, 4] = False
    assert not box_coverage((4.0, 4.0), valid).centre and not box_coverage((4.0, 4.0), valid).eligible
    assert box_coverage((math.nan, 4.0), valid) == ALL_MISSING and ALL_MISSING.valid == 0
    assert box_coverage((4.0, 0.1), np.ones(SHAPE, bool)).metrics() == {"pixels": 6, "missing": [[-1, -1], [0, -1], [1, -1]]}


def test_edge_allowance_moves_a_point_source_towards_the_interior():
    valid = np.ones(SHAPE, bool)
    bias, along = edge_allowance((4.0, 0.1), valid, 0.55, (4.0, 3.0))
    assert 0.05 < bias < 0.2 and abs(along - bias) < 1e-9          # the other star is straight up the column
    _, across = edge_allowance((4.0, 0.1), valid, 0.55, (7.0, 0.1))
    assert across < 1e-9                                          # the cut is perpendicular to the star axis
    assert edge_allowance((4.0, 4.0), valid, 0.55, (7.0, 4.0)) == (0.0, 0.0)
    valid[3, 4] = False
    hole, _ = edge_allowance((4.0, 4.0), valid, 0.55, (7.0, 4.0))
    assert 0.0 < hole < 0.15
    assert all(math.isnan(x) for x in edge_allowance((math.nan, 0.1), valid, 0.55, (4.0, 3.0)))
```

- [ ] **Step 2: Run them to verify they fail**

Run: `PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/unit/test_pixels.py -q -p no:cacheprovider -k "box_coverage or edge_allowance"`
Expected: FAIL with ImportError.

- [ ] **Step 3: Implement** `BoxCoverage`, `ALL_MISSING`, `box_coverage` and `edge_allowance` in `src/skysift/lc/pixels.py`; delete `_box_on_frame` (its two call sites in `season_images` change in Task 2; for this task keep `season_images` compiling by using `box_coverage(...).complete` where it called `_box_on_frame`).

- [ ] **Step 4: Run the test file**

Run: `PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/unit/test_pixels.py -q -p no:cacheprovider`
Expected: all pass (the existing tests still hold: the complete-box semantics are unchanged in this task).

- [ ] **Step 5: Commit**

```bash
git add src/skysift/lc/pixels.py tests/unit/test_pixels.py
git commit -m "feat(pixels): measured box coverage and the edge allowance of a cut box"
```

---

### Task 2: Partial boxes vote with the allowance

**Files:**
- Modify: `src/skysift/lc/pixels.py` (`SeasonImage`, `SeasonResult`, `Localisation.metrics`, `season_images`, `verdict`, `localise_quarters`, module docstring)
- Test: `tests/unit/test_pixels.py`

**Interfaces:**
- Consumes: Task 1.
- Produces:
  - `SeasonImage.box_target: BoxCoverage = ALL_MISSING`, `SeasonImage.box_neighbour: BoxCoverage = ALL_MISSING`; `target_on_frame` and `neighbour_on_frame` become `box_target.eligible` and `box_neighbour.eligible` (set by `season_images`).
  - `SeasonResult` gains `box_target: BoxCoverage = ALL_MISSING`, `box_neighbour: BoxCoverage = ALL_MISSING`, `box_partial: bool = False`, `edge_bias_pix: float = 0.0`, `edge_bias_axis_pix: float = 0.0`, `separation_sigma_raw: float = math.nan`; `on_frame` means both boxes eligible; `two_source` equals `on_frame`.
  - `Localisation.metrics()` per season adds `"box_pixels_target"`, `"box_pixels_neighbour"` (ints), `"box_missing_target"`, `"box_missing_neighbour"` (`[[dcol, drow], ...]`), `"box_partial"` (bool), `"edge_bias_pix"`, `"edge_bias_axis_pix"`, `"separation_sigma_raw"` (floats or None).
  - In `localise_quarters`: `psf = fit["psf_sigma_pix"] if finite and 0.3 <= psf <= 3.0 else EDGE_PSF_SIGMA_PIX`; the allowance of each partial box via `edge_allowance(pos_star, im.valid, psf, pos_other)`, the season's `edge_bias_pix` and `edge_bias_axis_pix` the maxima over its partial boxes (0 when none); `centroid_sigma_pix = hypot(sigma_mc, edge_bias_pix)`, `centroid_sigma_axis_pix = hypot(sigma_axis, edge_bias_axis_pix)`; `separation_sigma_raw` from the uninflated axis error, `separation_sigma` from the inflated one; `voting = on_frame and finite(separation_sigma) and n_events >= SEASON_EVENTS_MIN and (not box_partial or partial_abstention(s) is None)`; `non_voting_reason` as before with "box incomplete: target" when `not box_target.eligible`, then the neighbour, then the abstention reason for a partial season, then "centroid not measured".
  - `partial_abstention(s: SeasonResult) -> str | None` (module level, after `_on_neighbour`): None when `_on_target(s) or _on_neighbour(s)` (the season is decisive and votes); otherwise the reason it abstains: `f"box cut: ambiguous ({s.separation_sigma:.1f} sigma)"` when `abs(s.separation_sigma) <= SEPARATION_SIGMA`, else `f"box cut: {_disagreement(s)}"` (the neighbour-depth wording of `_disagreement`, which gives "neighbour depth 200 +/- 60 ppm" or "neighbour depth not measured"). Spec section 4 rule 5: a cut box votes only when decisive. A complete-box season never calls it.
  - `verdict`: "pixels: no season holds both stars on the frame" when `seasons and all(not s.on_frame for s in seasons)` (unchanged text, `on_frame` now eligibility).

- [ ] **Step 1: Write the failing tests** in `tests/unit/test_pixels.py`. Add the layout `CORNER_TARGET = ((5, 5), (4, 4))` (stamp columns 4 to 8 and rows 4 to 8: the target on the stamp's corner pixel, the neighbour at (2.8, 0.6) with a complete box). Pin the committed behaviour of complete boxes with a copy of today's numbers taken before any change to `localise_quarters`:

```python
def test_complete_boxes_vote_exactly_as_before():
    """Run on the committed code first and paste its numbers: complete boxes must not move."""
    loc = localise(*make_cube("target"))
    by = _by_season(loc)
    assert all(not s.box_partial and s.edge_bias_pix == 0.0 and s.separation_sigma == s.separation_sigma_raw
               for s in loc.seasons)
    assert all(s.box_target.complete and s.box_neighbour.complete for s in loc.seasons)
    # the implementer pastes the committed values here, to 6 decimals, before changing localise_quarters:
    assert [round(by[s].separation_sigma, 6) for s in (0, 1, 2, 3)] == [<committed values>]
    assert [round(by[s].centroid_sigma_pix, 6) for s in (0, 1, 2, 3)] == [<committed values>]


def test_a_season_with_the_target_on_the_stamp_edge_votes_with_an_allowance():
    for dip_on, expected in (("target", "pass"), ("neighbour", "fail")):
        loc = localise_quarters(make_quarters(dip_on, layout={1: NO_TARGET, 5: NO_TARGET}), EPH)
        s1 = _by_season(loc)[3]; im = next(i for i in loc.images if i.season == 3)
        assert im.pos_target == (0.0, 4.0) and im.box_target.partial and im.box_neighbour.complete
        assert im.target_on_frame and s1.on_frame and s1.voting and s1.two_source and s1.box_partial
        assert s1.box_target.metrics() == {"pixels": 6, "missing": [[-1, -1], [-1, 0], [-1, 1]]}
        assert 0.0 < s1.edge_bias_pix < 0.6 and s1.edge_bias_axis_pix <= s1.edge_bias_pix + 1e-12
        assert s1.centroid_sigma_pix > s1.edge_bias_pix and abs(s1.separation_sigma) < abs(s1.separation_sigma_raw)
        assert s1.non_voting_reason is None
        assert loc.verdict == expected and "4/4 seasons" in loc.reason
        if dip_on == "target":
            assert s1.nearer == "target" and s1.separation_sigma > SEPARATION_SIGMA
        else:
            assert s1.nearer == "neighbour" and s1.separation_sigma < -SEPARATION_SIGMA
        m = loc.metrics(); json.dumps(m, allow_nan=False)
        row = next(x for x in m["seasons"] if x["season"] == 3)
        assert row["box_partial"] is True and row["box_pixels_target"] == 6 and row["box_pixels_neighbour"] == 9
        assert row["box_missing_target"] == [[-1, -1], [-1, 0], [-1, 1]] and row["box_missing_neighbour"] == []
        assert row["edge_bias_pix"] > 0 and row["separation_sigma_raw"] is not None


def test_every_season_with_the_target_on_the_edge_passes_as_partial():
    loc = localise_quarters(make_quarters(layout={q: NO_TARGET for q in range(1, 6)}), EPH)
    assert loc.verdict == "pass" and "4/4 seasons" in loc.reason
    assert all(s.voting and s.box_partial for s in loc.seasons)


def test_a_corner_box_does_not_vote():
    loc = localise_quarters(make_quarters(layout={1: CORNER_TARGET, 5: CORNER_TARGET}), EPH)
    s1 = _by_season(loc)[3]
    assert s1.box_target.valid == 4 and not s1.on_frame and not s1.voting and not s1.two_source
    assert s1.non_voting_reason == "box incomplete: target" and s1.nearer == "unknown"
    assert loc.verdict == "pass" and "3/3 seasons" in loc.reason


def test_a_dead_pixel_in_the_box_votes_with_a_small_allowance():
    qs = make_quarters()
    for qd in qs:
        if qd.quarter in (1, 5):
            qd.cube[:, 3, 4] = math.nan                  # row 3, col 4: offset (0, -1) from the target's pixel
    s1 = _by_season(localise_quarters(qs, EPH))[3]
    assert s1.voting and s1.box_partial and s1.box_target.metrics() == {"pixels": 8, "missing": [[0, -1]]}
    assert 0.0 < s1.edge_bias_pix < 0.15


def test_a_cut_box_votes_only_when_decisive():
    from dataclasses import replace
    from skysift.lc.pixels import partial_abstention
    partial = replace(_season(0), box_partial=True, edge_bias_pix=0.1)
    assert partial_abstention(partial) is None                                     # 10 sigma on the target
    assert partial_abstention(replace(_season(0, sep=-8.0), box_partial=True)) is None          # 8 sigma on the neighbour
    assert partial_abstention(replace(_season(0, sep=1.7), box_partial=True)) == "box cut: ambiguous (1.7 sigma)"
    assert partial_abstention(replace(_season(0, sep=-2.9), box_partial=True)) == "box cut: ambiguous (-2.9 sigma)"
    assert partial_abstention(replace(_season(0, d_n=(200.0, 60.0)), box_partial=True)) == "box cut: neighbour depth 200 +/- 60 ppm"
    assert partial_abstention(replace(_season(0, d_n=(math.nan, math.nan)), box_partial=True)) == "box cut: neighbour depth not measured"
    # the verdict: an abstaining cut season is silent, the complete seasons decide
    silent = replace(_season(3, sep=1.7, voting=False), box_partial=True, non_voting_reason="box cut: ambiguous (1.7 sigma)")
    assert verdict([_season(0), _season(1), _season(2), silent], 20)[0] == "pass"
    assert verdict([_season(0), silent], 20) == ("undetermined", "pixels: only one season could vote (season 3: box cut: ambiguous (1.7 sigma))")


def test_the_allowance_falls_back_to_the_default_width(monkeypatch):
    import skysift.lc.pixels as px
    monkeypatch.setattr(px, "two_source_fit", lambda *a, **k: px._nan_fit())
    s1 = _by_season(localise_quarters(make_quarters(layout={1: NO_TARGET, 5: NO_TARGET}), EPH))[3]
    assert s1.voting and s1.box_partial and math.isfinite(s1.separation_sigma) and s1.edge_bias_pix > 0
    assert math.isnan(s1.psf_sigma_pix)
```

Also change the existing tests: `test_a_season_with_the_target_on_the_stamp_edge_does_not_vote` is deleted (replaced above); `test_no_season_with_both_stars_on_the_frame_is_undetermined` uses `CORNER_TARGET` for every quarter; `test_a_pass_needs_two_voting_seasons` keeps its wording (the helper `_season` sets `on_frame=False` for the silenced seasons, which is the ineligible case).

- [ ] **Step 2: Paste the committed numbers** into `test_complete_boxes_vote_exactly_as_before` from a run of the committed code (the implementer runs the two expressions in `python -c` on the unmodified `localise_quarters` before editing it), then run the new tests to verify they fail

Run: `PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/unit/test_pixels.py -q -p no:cacheprovider`
Expected: the new tests FAIL (AttributeError on `box_partial`, old verdicts), the pinned-numbers test passes once the attributes exist.

- [ ] **Step 3: Implement** the Interfaces block in `src/skysift/lc/pixels.py`, and rewrite the module docstring's sentence "A season votes only when both stars' 3x3 boxes lie complete on valid pixels..." to state the partial-box rule (centre and at least `BOX_PIXELS_MIN` of 9, the allowance in quadrature, complete boxes unchanged) in two sentences.

- [ ] **Step 4: Run the test file and ruff**

Run: `PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/unit/test_pixels.py -q -p no:cacheprovider` and `PYTHONUTF8=1 .venv/Scripts/python.exe -m ruff check src tests`
Expected: all pass, ruff clean.

- [ ] **Step 5: Commit**

```bash
git add src/skysift/lc/pixels.py tests/unit/test_pixels.py
git commit -m "feat(pixels): a box cut by the stamp edge votes with the edge allowance in its error"
```

---

### Task 3: The rung, the synthetic pixel files and the read-only rerun tool

**Files:**
- Modify: `src/skysift/vetting/rung5_pixels.py` (the `metrics` dict in `run`), `tests/unit/synthetic_tpf.py`, `tests/unit/test_rung5_pixels.py`
- Create: `tools/pixels_rerun.py`

**Interfaces:**
- Consumes: Task 2's `SeasonResult.box_partial`, `voting`.
- Produces:
  - `rung5_pixels.run` metrics gain `"n_seasons_partial_box": int` (voting seasons with `box_partial`) and `"rule_version": PIXEL_RULE_VERSION` with the module constant `PIXEL_RULE_VERSION = 2` (the rows of 21 October carry no key and read as 1). Both keys go into every result that carries `loc.metrics()`, including the undetermined ones.
  - `synthetic_tpf.EDGE_TARGET = ((9, 5), (4, 0))` (columns 4 to 8 of the 9 x 9 grid: the target on the stamp's first column, as the real KBonus stars sit) and `synthetic_tpf.CORNER_TARGET = ((5, 5), (4, 4))`.
  - `tools/pixels_rerun.py`: `python -m tools.pixels_rerun --db <copy.db> --root <repo root the artifact paths are relative to> --signal <id> [--signal ...] [--study <results.json>] [--json <out>]`. Opens the database read-only (`sqlite:///file:<absolute path>?mode=ro&uri=true`), loads the signal row and its target as `recheck.py` does (`Signal(**{k: getattr(row, k) for k in Signal.model_fields})`, `Target(**...)` with the target's `meta`), lists the target's undeleted `kind="tpf"` artifact rows whose `url` starts with `tpf_listing_url(kic)` for the neighbour KIC (`meta["tpf_org"]`), reads each through `Path(root, local_path).read_bytes()` and `parse_tpf`, and runs `Rung5Pixels(tpf_loader=lambda kic, **kw: TpfSet(tpfs), blob=None, signal_id=None).run(signal, bundle)` with a `DataBundle` whose `lc` is an empty `LightCurve` with `meta={}`, `store=None` and `fetch` a callable that raises `ServiceUnavailable("network refused by pixels_rerun")`. Prints, per signal, the status, the reason and one row per season: season, quarters, n_events, voting, non_voting_reason, box_pixels_target/neighbour, box_missing_target, separation_sigma_raw, separation_sigma, edge_bias_pix, centroid_sigma_pix, dist_target_pix, dist_neighbour_pix, depth_target_ppm, depth_neighbour_ppm. With `--study`, prints beside each season the study's `seasons[<s>].snrbox_diff_centroid_dist_to_candidate_pix`, `snrbox_diff_centroid_dist_to_target_pix`, `two_source_fit.implied_depth_ppm_candidate` (+/- `_err`) and `implied_depth_ppm_target` (+/- `_err`) (the study calls our target "candidate" and our neighbour "target"). With `--json`, writes the full metrics per signal. Exit code 0; nothing is written to the database or the blob store.

- [ ] **Step 1: Write the failing tests** in `tests/unit/test_rung5_pixels.py` (replace `test_a_target_off_the_stamp_is_undetermined_with_the_frame_reason`):

```python
def test_a_target_on_the_stamp_edge_votes_in_the_seasons_that_hold_the_neighbour(bundle_with_tpfs):
    # columns 4 to 8 only: the KBonus target sits on the stamp's first column in every quarter (6 of 9 box pixels);
    # the neighbour turns about it from season to season and falls off the stamp in two of the four
    r = Rung5Pixels(tpf_loader=loader("target", stamp=EDGE_TARGET)).run(SIGNAL, bundle_with_tpfs)
    assert r.status == TestStatus.PASS and r.reason.startswith("the dip is on the target in 2/2 seasons")
    assert r.metrics["n_seasons_voting"] == 2 and r.metrics["n_seasons_partial_box"] == 2
    assert r.metrics["seasons_silenced"] == {"box incomplete: neighbour": 2}
    voting = [s for s in r.metrics["seasons"] if s["voting"]]
    assert all(s["box_partial"] and s["box_pixels_target"] == 6 and s["edge_bias_pix"] > 0 for s in voting)
    assert all(s["box_missing_target"] == [[-1, -1], [-1, 0], [-1, 1]] for s in voting)
    _strict(r.metrics)


def test_a_dip_on_the_neighbour_fails_with_the_target_on_the_stamp_edge(bundle_with_tpfs):
    r = Rung5Pixels(tpf_loader=loader("neighbour", stamp=EDGE_TARGET)).run(SIGNAL, bundle_with_tpfs)
    assert r.status == TestStatus.FAIL and "2/2 seasons" in r.reason
    _strict(r.metrics)


def test_a_target_in_the_stamp_corner_is_undetermined_with_the_frame_reason(bundle_with_tpfs):
    r = Rung5Pixels(tpf_loader=loader("target", stamp=CORNER_TARGET)).run(SIGNAL, bundle_with_tpfs)
    assert r.status == TestStatus.UNDETERMINED and r.reason == "pixels: no season holds both stars on the frame"
    assert r.metrics["n_seasons_voting"] == 0 and r.metrics["n_seasons_partial_box"] == 0
    assert r.metrics["seasons_silenced"] == {"box incomplete: target": 4}
    _strict(r.metrics)
```

and in the test that passes on the default stamp (the first PASS test of the file) add `assert r.metrics["n_seasons_partial_box"] == 0 and r.metrics["rule_version"] == 2`. If the synthetic rotation puts the neighbour on the stamp in a number of seasons other than two, the implementer reports the number and the controller rules; the assertions must then still show at least two voting partial seasons and a pass.

- [ ] **Step 2: Run them to verify they fail**

Run: `PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/unit/test_rung5_pixels.py -q -p no:cacheprovider`
Expected: the three new tests FAIL (ImportError on the layouts, KeyError on the metric).

- [ ] **Step 3: Implement** the layouts in `synthetic_tpf.py`, the metric in `rung5_pixels.py`, and `tools/pixels_rerun.py` per the Interfaces block (argparse, no typer; a module docstring saying what it reads and that it writes nothing).

- [ ] **Step 4: Run the tests and ruff**

Run: `PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/unit/test_rung5_pixels.py tests/unit/test_pixels.py tests/unit/test_recheck.py -q -p no:cacheprovider` and `PYTHONUTF8=1 .venv/Scripts/python.exe -m ruff check src tests tools`
Expected: all pass, ruff clean.

- [ ] **Step 5: The real-data check.** Make a consistent copy of the live database with the sqlite backup API into the plan workspace (never under `.superpowers/sample-run`):

```
PYTHONUTF8=1 .venv/Scripts/python.exe -c "import sqlite3; s=sqlite3.connect('file:C:/Research/skysift/.superpowers/sample-run/engine.db?mode=ro', uri=True); d=sqlite3.connect('<workspace>/engine-copy.db'); s.backup(d); d.close(); s.close()"
```

then run `PYTHONUTF8=1 .venv/Scripts/python.exe -m tools.pixels_rerun --db <workspace>/engine-copy.db --root C:/Research/skysift --signal 1032 --signal 1064 --signal 3479 --signal 3480 --study docs/candidates/kic-005689121/localisation/results.json --json <workspace>/pixels-rerun.json > <workspace>/pixels-rerun.txt`. Record in the report: each signal's status and reason; for 1032 that the same three seasons vote with unchanged separation values and that season 3 now also votes or does not, with its box pixels; for 1064 the agreement with the study (section 7 of the spec: every voting season nearer the target, S/N-box centroid distance to the target within 0.1 px of 0.14, 0.15, 0.13, 0.26 for seasons 0 to 3, two-source target depth within three combined sigma of 2002 +/- 143, 2161 +/- 123, 2190 +/- 114, 1813 +/- 465, neighbour depth under the veto in every voting season); for 3479 and 3480 the number of voting seasons and the verdict. Confirm with `git status` in the live repository and the file sizes and mtimes of `.superpowers/sample-run/engine.db*` that nothing there changed.

- [ ] **Step 6: Commit**

```bash
git add src/skysift/vetting/rung5_pixels.py tests/unit/synthetic_tpf.py tests/unit/test_rung5_pixels.py tools/pixels_rerun.py
git commit -m "feat(rung5): count partial-box votes, synthetic edge and corner stamps, read-only rerun tool"
```

---

### Task 4: The signal page reads the cut: explain sentences and the pixel figure

**Files:**
- Modify: `src/skysift/ui/explain.py` (`_pixels`, lines 326-357), `src/skysift/ui/pixel_figure.py` (`RULES`, `figure_label`, `_words`, `_panel`, `render_svg`'s `<desc>`), `src/skysift/ui/static/app.css` (the `.pf-` block at lines 431-441)
- Test: `tests/unit/test_ui_explain.py`, `tests/unit/test_ui_review.py`

**Interfaces:**
- Consumes: the per-season keys of Task 2 (`box_partial`, `box_pixels_target`, `box_pixels_neighbour`, `box_missing_target`, `box_missing_neighbour`, `edge_bias_pix`), all optional.
- Produces:
  - `explain.partial_box_clause(m: dict) -> str`: `""` when no voting season has `box_partial` true, else ` ({n} of {v} with a box cut by the stamp edge)` with n the partial voting seasons and v the voting seasons. `_row` appends it to the sentence of a rung 5 row whose status is pass (the `PASSES` match); `_pixels` appends it to its fallback headline too. A voting partial season's row in `_pixels` gains, after the sigma clause, `this star's box cut, {k} of 9 pixels` (when `box_pixels_target < 9`) and/or `the neighbour's box cut, {k} of 9 pixels`, then `{edge_bias_pix:.2f} px allowance in the error`. An abstaining season needs nothing new: it renders through the non-voting path with its stored reason.
  - `pixel_figure.RULES` gains `".pf-missing{fill:<muted>;fill-opacity:.3;stroke:none}"` and app.css the same on `var(--muted)`; `_panel` draws, for a voting season, one `<rect class="pf-missing">` per entry of `box_missing_target` and `box_missing_neighbour` covering the pixel `(round(col) + dcol, round(row) + drow)` of that star (one pixel wide and high in panel units, inside the clip); `_words` vote line gains ` (box cut, {k} of 9 px)` with k the smaller of the two counts below 9; `figure_label` adds `{season_name} box cut by the stamp edge ({k} of 9 pixels)` per voting partial season after the nearer-counts; the `<desc>` gains "A shaded cell is a box pixel missing from the stamp."
- A season without the keys renders exactly as today (no suffix, no cells, same label).

- [ ] **Step 1: Write the failing tests.** In `tests/unit/test_ui_explain.py`, build a partial season by copying a voting season of `ui_seed.stage2_pixel_metrics()` and setting `box_partial=True, box_pixels_target=6, box_pixels_neighbour=9, box_missing_target=[[-1, -1], [0, -1], [1, -1]], box_missing_neighbour=[], edge_bias_pix=0.10, separation_sigma_raw=9.6`:

```python
def test_pixels_sentence_names_a_box_cut_by_the_stamp_edge():
    m = stage2_pixel_metrics(); m["seasons"][0] = {**m["seasons"][0], <the partial keys>}
    sentence, numbers = explain._pixels(m)
    assert sentence == "The dip sits on this star in 3 of 3 seasons that could vote (1 of 3 with a box cut by the stamp edge); 1 could not"
    assert "archive season 0: " in numbers and "this star's box cut, 6 of 9 pixels" in numbers
    assert "0.10 px allowance in the error" in numbers and "neighbour's box cut" not in numbers
    row = {"rung": "rung5_pixels", "status": "pass", "reason": "the dip is on the target in 3/3 seasons (centroid >= 17.5 sigma nearer)", "metrics": m}
    assert explain._row("rung5_pixels", row).sentence.endswith("than the neighbour (1 of 3 with a box cut by the stamp edge)")


def test_a_season_without_box_keys_renders_as_before():
    assert explain._pixels(stage2_pixel_metrics()) == <the committed output, captured first>
```

In `tests/unit/test_ui_review.py`, with `_set_metrics` on the stage-2 signal and the same modified season 0:

```python
def test_pixels_svg_draws_the_missing_box_pixels_and_names_the_cut(seeded):
    ...
    panels = _season_panels(r.text)
    assert "nearer target, +40.9 sigma (box cut, 6 of 9 px)" in panels["0"] and panels["0"].count('class="pf-missing"') == 3
    assert 'class="pf-missing"' not in panels["1"]
    label = re.search(r'aria-label="([^"]*)"', r.text).group(1)
    assert "archive season 0 box cut by the stamp edge (6 of 9 pixels)" in label
    assert "A shaded cell is a box pixel missing from the stamp." in r.text
```

and extend the existing app.css/`css_rules` assertion so the new rule is covered (it is, if the test iterates `css_rules()`); add a run of `python -m tools.contrast_check` as a test step below.

- [ ] **Step 2: Run them to verify they fail**

Run: `PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/unit/test_ui_explain.py tests/unit/test_ui_review.py -q -p no:cacheprovider -k "box or missing or as_before"`
Expected: FAIL on the new assertions.

- [ ] **Step 3: Implement** per the Interfaces block.

- [ ] **Step 4: Run the tests, the contrast check and ruff**

Run: `PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/unit/test_ui_explain.py tests/unit/test_ui_review.py tests/unit/test_ui_pages.py -q -p no:cacheprovider`; `PYTHONUTF8=1 .venv/Scripts/python.exe -m tools.contrast_check`; `PYTHONUTF8=1 .venv/Scripts/python.exe -m ruff check src tests tools`
Expected: all pass, exit 0, clean.

- [ ] **Step 5: Commit**

```bash
git add src/skysift/ui/explain.py src/skysift/ui/pixel_figure.py src/skysift/ui/static/app.css tests/unit/test_ui_explain.py tests/unit/test_ui_review.py
git commit -m "feat(ui): the pixel figure and the explain sentences name a box cut by the stamp edge"
```

---

### Task 5: The dossier caveat and the pack sentence

**Files:**
- Modify: `src/skysift/dossier.py` (after `close_call`, lines 650-660; `caveats` after the close-call entry), `src/skysift/pack.py` (`pixel_sentence`, lines 397-420; the RNAAS draft near line 572)
- Test: `tests/unit/test_dossier.py`, `tests/unit/test_pack.py`

**Interfaces:**
- Consumes: Task 2's keys.
- Produces:
  - `dossier.partial_box_seasons(seasons: list[dict]) -> list[dict]`: the seasons with `voting is True` and `box_partial is True`.
  - `dossier.partial_box_sentence(seasons: list[dict]) -> str`: `""` when none; else "In season 0 this star's 3 x 3 box is cut by the stamp edge; the centroid there is measured on the pixels present and the shift the cut would give a point source (0.10 px at most) is added to its error." with "seasons 0, 2 and 3" for several, "the neighbour's" when only neighbour boxes are partial, "both stars' 3 x 3 boxes are" when both occur, and the number the largest `edge_bias_pix` to two decimals.
  - `dossier.caveats` appends `{"key": "partial_box", "title": "A season's box is cut by the stamp edge", "text": partial_box_sentence(...)}` right after the close-call caveat when the sentence is not empty.
  - `pack.pixel_sentence` appends ` ` + the sentence after the centroid-median sentence when not empty; the RNAAS draft appends the same sentence after its difference-image sentence.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_dossier.py
def test_partial_box_sentence_names_the_seasons_and_the_largest_allowance():
    seasons = [<voting season 0 with box_partial True, box_pixels_target 6, edge_bias_pix 0.10>,
               <voting season 1 complete>, <voting season 2 partial, edge_bias_pix 0.07>, <non-voting season 3 partial>]
    assert dossier.partial_box_sentence(seasons) == ("In seasons 0 and 2 this star's 3 x 3 box is cut by the stamp edge; the "
        "centroid there is measured on the pixels present and the shift the cut would give a point source (0.10 px at "
        "most) is added to its error.")
    assert dossier.partial_box_sentence([<voting season 1 complete>]) == ""


def test_caveats_list_a_partial_box_after_the_close_call():
    <data with a rung5 pass whose season 0 is partial>
    keys = [c.get("key") for c in dossier.caveats(data)]
    assert "partial_box" in keys and (("close_call" not in keys) or keys.index("close_call") < keys.index("partial_box"))


# tests/unit/test_pack.py
def test_pixel_sentence_names_a_box_cut_by_the_stamp_edge():
    <row with voting season 0 partial> -> assert "In season 0 this star's 3 x 3 box is cut by the stamp edge" in pack.pixel_sentence(data)


def test_pixel_sentence_without_box_keys_is_unchanged():
    assert pack.pixel_sentence(<the existing fixture>) == <the committed output, captured first>
```

- [ ] **Step 2: Run them to verify they fail**

Run: `PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/unit/test_dossier.py tests/unit/test_pack.py -q -p no:cacheprovider -k "partial_box or box_keys"`
Expected: FAIL with AttributeError.

- [ ] **Step 3: Implement** per the Interfaces block (`pack` imports the sentence from `dossier` as it already imports `centroid_offset`).

- [ ] **Step 4: Run the tests and ruff**

Run: `PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/unit/test_dossier.py tests/unit/test_pack.py tests/unit/test_ui_dossiers.py -q -p no:cacheprovider` and `PYTHONUTF8=1 .venv/Scripts/python.exe -m ruff check src tests tools`
Expected: all pass, clean.

- [ ] **Step 5: Commit**

```bash
git add src/skysift/dossier.py src/skysift/pack.py tests/unit/test_dossier.py tests/unit/test_pack.py
git commit -m "feat(dossier): a caveat and a pack sentence for a season whose box is cut by the stamp edge"
```

---

### Task 6: The recheck reopens the rows the box rule silenced

**Files:**
- Modify: `src/skysift/recheck.py` (`wants_pixels`, lines 174-192, and its docstring)
- Test: `tests/unit/test_recheck.py`

**Interfaces:**
- Consumes: `rung5_pixels.PIXEL_RULE_VERSION` (Task 3) and the `seasons_silenced` and `rule_version` metrics.
- Produces: `recheck.box_rule_reopens(rung5: dict) -> bool`: True when the row's status is "undetermined", its reason starts with "pixels:", its metrics' `seasons_silenced` (a dict) has a key starting with "box incomplete", and `int(metrics.get("rule_version") or 1) < PIXEL_RULE_VERSION`. `wants_pixels` treats such a row as not settled (the step runs again, the pixel files from the blob cache or fetched again); every other rule of `wants_pixels` is unchanged.

- [ ] **Step 1: Write the failing tests** in `tests/unit/test_recheck.py`, next to `test_a_settled_pixel_step_is_not_run_again`, using that test's fixtures to seed a signal with a rung 5 row:

```python
def test_the_recheck_reopens_a_pixel_row_the_box_rule_silenced(env):
    silenced = {"seasons_silenced": {"box incomplete: target": 3}, "n_seasons_voting": 1}
    <seed an undetermined rung5 row: reason "pixels: only one season could vote (season 0: box incomplete: target)", metrics silenced>
    assert wants_pixels(store, sig, tests) is True
    <the same row with metrics {**silenced, "rule_version": 2}> -> assert wants_pixels(...) is False
    <a pass row with metrics silenced and rule_version absent> -> False
    <an undetermined row "pixels: season 2 ambiguous (1.0 sigma)" with seasons_silenced {}> -> False
    assert recheck.box_rule_reopens({"status": "undetermined", "reason": "pixels: x", "metrics": silenced}) is True
    assert recheck.box_rule_reopens({"status": "undetermined", "reason": "pixels: x", "metrics": {}}) is False
```

and an end-to-end check that `recheck()` with a fake-MAST loader (the pattern of the file's existing end-to-end test) runs the pixel step for such a signal and writes a new rung 5 row carrying `rule_version` 2, after which a second `recheck()` writes nothing.

- [ ] **Step 2: Run them to verify they fail**

Run: `PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/unit/test_recheck.py -q -p no:cacheprovider -k "reopens"`
Expected: FAIL (AttributeError, then the assertion on `wants_pixels`).

- [ ] **Step 3: Implement** per the Interfaces block; the docstring of `wants_pixels` gains one sentence on the reopen rule and the spec reference.

- [ ] **Step 4: Run the recheck tests and ruff**

Run: `PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/unit/test_recheck.py tests/unit/test_rung5_pixels.py -q -p no:cacheprovider` and `PYTHONUTF8=1 .venv/Scripts/python.exe -m ruff check src tests tools`
Expected: all pass, clean.

- [ ] **Step 5: Commit**

```bash
git add src/skysift/recheck.py tests/unit/test_recheck.py
git commit -m "feat(recheck): rerun the pixel step for the rows the complete-box rule silenced"
```

---

### Task 7 (controller): final review, ledger and gate

- [ ] Final whole-branch review per superpowers:subagent-driven-development; one fix wave; one scoped re-review.
- [ ] Append to `.superpowers/sdd/2026-10-20-skysift-recheck/progress.md` one line pointing to the new spec, plan and ledger as the next ruling on the voting rule (the brief names that ledger as the rule's history).
- [ ] Run the full gate in the plan's own worktree at below-normal priority, in the background: `PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests -q -p no:cacheprovider -m "not slow and not golden"` (about two hours), then the golden test `PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/golden -q -p no:cacheprovider` (its fixtures are cached under `tests/fixtures/cache`; if it needs the network, report that instead of fetching).
- [ ] Report to Vikram with the real-data table, the rulings made, and the branch name; no merge, push or publish.
