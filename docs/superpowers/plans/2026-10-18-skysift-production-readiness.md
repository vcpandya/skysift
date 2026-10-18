# SkySift production readiness (quality and accuracy) — plan, 18 October 2026

**Goal:** close the accuracy and quality follow-ups left open after the M3 merge and the sub-harmonic / pre-whitening fixes (main 604ecb7), measure the search end to end on real light curves, and start the calibration pilot that gates a production sweep. Vikram, 18 October 2026: "finish whatever we need to do to ensure production readiness in terms of quality and accuracy", "also finishing follow ups as you have mentioned".

**Spec:** docs/superpowers/specs/2026-10-13-skysift-design.md (section 13 carries dated amendments; each task adds one). The dashboard polish spec (2026-10-17) is out of scope until Vikram reviews it.

**Method:** measure first (Task 0), fix what the measurement shows (Tasks 4 and 5 are conditional), then calibrate (Task 7) on the final search version. Tasks 1 to 3 touch no search-version input and run while Task 0 computes.

## Global constraints
- Only `.venv/Scripts/python.exe -m <module>`; never a venv `.exe` launcher; never bare `python`. `PYTHONUTF8=1`; pytest with `-p no:cacheprovider`.
- Dashboard stays loopback-only without auth. No push, no publish. Commit trailer `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.
- Every change to the detector, detrend, pre-whitening or thresholds changes `search_version`; such changes land before Task 7 starts, never after.

## Tasks
0. **Injection and recovery on real curves** (`docs/superpowers/notes/scripts/inject_recovery.py`, running): 120 box transits (expected stacked SNR 7/10/15/25, periods 0.8 to 25 d) into the 30 cached KBonus curves through the production worker. Outputs completeness against SNR, depth bias, and the rung 2 / rung 3 verdicts on real box transits. Note: `docs/superpowers/notes/2026-10-18-injection-recovery.md`.
1. **Review queue shows the current search version by default** (ui/queries.py, ui/routes/review.py, templates/candidates.html, test_ui_review.py): stale signals from earlier search versions are hidden unless the operator toggles "all versions"; the count of hidden signals is shown. Brief: `.superpowers/sdd/2026-10-18-skysift-production-readiness/ui-brief.md`.
2. **Signal page lists restored waves and the epoch check** (templates/signal.html, routes/review.py): "n wave(s) put back at the detection's harmonics" and, for a period-matched known object, whether the epoch agrees. Same brief as Task 1.
3. **Rung 4 epoch check** (vetting/rung4_novelty.py, test_rung4.py): fetch the catalogue epoch (koi_time0bk, tce_time0bk, pl_tranmid converted to BKJD), record `epoch_offset_h` and `epoch_agrees` on the matched row; the verdict is unchanged (information for the reviewer; a resonant second planet shows as "epoch differs"). Brief: `rung4-epoch-brief.md`.
4. **Conditional: rung 2 shape contrast parameter penalty.** Only if Task 0 shows recovered real box transits rejected as `sinusoidal`: compare box and two-harmonic sine by BIC (chi-square plus parameters times ln n) in the `lc/ops.shape_contrast` callers, bump the pre-whitening version (the guard shares the test), re-run Task 0 on the affected injections.
5. **Conditional: ramp tails after gaps.** Only if Task 0 shows long-period (> 10 d) completeness clearly below the short-period one: model the post-gap thermal ramp (exponential fit per segment start) instead of widening the mask.
6. **Pre-whitening cap and flag:** run 8 shows one star at 47 of the 50-component cap; raise `prewhiten_max_components` to 100 and record `capped: true` in the prewhitening record when the loop stops at the cap (dashboard sentence). Lands with the Task 4/5 decisions, before Task 7.
7. **Calibration pilot:** `skysift calibrate --source kbonus_bkg --limit 1000 --workers-cpu 8 --retention keep` at the production window (about 20 h), then `skysift calibrate-report`; the report fixes `sde_min` for the final search version. Production runbook paragraph in README.
8. **Final review:** independent code review of everything merged since a3e7bec (running for the first six commits), full suite, memory and ledger.
