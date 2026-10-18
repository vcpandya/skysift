# SDD ledger — plan: 

Spec: docs/superpowers/specs/2026-10-18-skysift-vetting-best-practice-design.md (written 18 Oct, Vikram: "use best practices ... directly"). Calibration pilot running (task byoccjw4q, 6 workers); rung changes only, search version unchanged.

## Pre-flight scan
| pair | shared | finding |
|---|---|---|
| 1/4 | transit_model API | TransitFit fields k,b,a_over_r,depth_ppm,t14_days,chi2,n_bins,ok; fit_transit(time,fnorm,period,t0,duration) — consistent |
| 2/3 | event_quality, EVENT_COVERAGE_MIN, CHASES_MIN | names consistent |
| 2/4 | phase_significance, sweet | names consistent; sweet takes depth_ppm but returns no verdict (rung compares) — accepted |
| 4/5 | config.py, default.yaml both edited | disjoint fields (ms2_max/ms3_max vs aperture_sigma); Task 5 runs first, Task 4 after; merge by hand if needed |
| 4/6 | pipeline.py | Task 4 edits whitened_flux/_cpu_work only; Task 6 edits build_ladder/DEFAULT_LADDER_VERSION — disjoint regions |
| 1,2,5,7 | none | parallel |
Ruling: Tasks 1,2,5,7 dispatched in parallel (disjoint files), then 3 and 4 in parallel, then 6. Cost if wrong: a merge by hand.

## Dispatch (18 Oct, BASE 668bb38)
Tasks 1, 2, 5, 7 dispatched in parallel (sonnet): briefs task-{1,2,5,7}-brief.md, reports task-N-report.md. Next: review packages per task, then Tasks 3 and 4 (parallel), then 6, then whole-change review and `skysift inject --limit 30 --workers-cpu 2` on the cached sample curves.

### Tasks 1, 2, 5, 7 landed (18 Oct): d146eca (T2), 04b01aa (T5), edac303 (T1), c54da70 (T7); my fixture commit 50ee823 (injectors dip flux_alt too). Rulings: T2 MS2 compares primary with tertiary (T18); the two-equal-dips fixture moved to the eccentric_secondary domain, Task 4 test changed to three equal dips. T1 departures (8 bins/duration, 3 starts, bin-averaged model) accepted. T5 fix round 1: aperture gate needs ratio outside [0.5, 2] as well as 3 sigma (APERTURE_RATIO_MAX, analogue of the T18 ghost ratio 4). T7 one-star-per-job and VersionMismatch guard accepted. Reviews dispatched for T1, T2, T7; Tasks 3 (sonnet) and 4 (opus) dispatched in parallel.

### Reviews (18 Oct, evening)
- T1 review: spec APPROVED (A1 no in-transit bin -> ok=False; A2 sparse test weak), quality REJECTED on wording -> fix round 1 sent to the T1 agent. Informational: k+b in 0.9-1.2 is uncertain for shallow signals (b weakly constrained).
- T2 review: both APPROVED; advisory: phase_significance 16.6 s at P=200 d on 68k points -> fix round (vectorise) to be sent once T3 fix round (also ops.py) lands. Also: guard cadence 0, NaN Chases for ses <= 0, approx assertions on ms2/ms3 formulas.
- T3 (5278b52): fix round 1 sent: event_quality enumerates every expected epoch (T18 Rubble counts expected transits); CHASES_MIN floor accepted as in T18; Chases median cost measured by the injection regression.
- T5 fix round 1 landed (200a421: APERTURE_RATIO_MAX 2.0); review dispatched.
- T7 review: both APPROVED; fix round 1 sent (depth_capped/snr_achieved, periodogram off, load-failure test).

### Task 1: complete (18 Oct) — edac303 + 5575a22 (fix round 1 accepted on inline check: ok=False when no bin centre lies in transit; docstrings). Ruling: no separate re-review for wording-only fixes.

### Task 7: complete (18 Oct) — c54da70 + 7b8ab45 (fix round 1: depth_capped, snr_achieved, periodogram off, load-failure test). Inline check of the fix; no separate re-review.

### Tasks 3 and 5: complete (18 Oct)
- T3: 5278b52 + 4ddd01c (expected epochs) + bc53bee + 6e1c80e (my docstring correction: the median rule IS T18 TCE Chases). Review: both APPROVED. Open for T6: EventQuality field comments in ops.py are stale (epochs now every expected epoch).
- T5: 04b01aa + 200a421 + e57163f (rung 4 scans on for an agreeing epoch; aperture tests in tests/unit/test_rung3_aperture.py). Review: both APPROVED. Its test_rungs_1_3.py docstring touch-up sits in the working tree and lands with T4.
- T7: complete (see above). T1: complete. In flight: T4 (opus), T2 fix round 1 (vectorised phase_significance).

### Task 2: complete (18 Oct) — d146eca + bc62ab5 (vectorised: 14.1 s -> 0.19 s at P = 200 d) + 072d132 (zero-MAD snap to NaN after the scoped re-review REJECTED bc62ab5; oracle parametrised incl. sub-day and constant flux). Inline check of the snap; accepted.

### Task 4 landed (14b71db, opus): departures accepted: three-dip fixture at 1800 ppm (equal depths put MS2 at FA2 by construction); period doubling compares DEPTHS within fa2 x hypot(errors) (the sigma rule fails at high SNR because the primary baseline includes the secondary points); phase wrap abs(abs(phase_sec) - 0.5); occultation host mass 0.012. Concern "clean 6000 ppm box at 1.4 d gives free_secondary 5.7 sigma": reproduced by me — identical (phase 0.820, 51 ppm, 6.0 sigma) for 6000 and 2000 ppm depths, so it is the seed-19 synthetic quarter-offset structure folding coherently, not a transit effect; real-curve runs and the 120 injections showed no such false secondary. Noted, no change. SWEET sees the 2 d-window detrended flux (waves above ~2 d attenuated): accepted limit, to be stated in the spec by Task 6. Review dispatched (opus).

### Task 4 review (opus, 18 Oct): spec REJECTED on two SPEC defects, quality APPROVED. Rulings: (A1) albedo A_g = d_sec x (a/R_p)^2 with the FRACTIONAL secondary depth (my spec divided by d_pri: wrong by 1/d_pri; HAT-P-7 b would have scored 34); (A2) the equal-secondary exception is a planet detected at TWICE its period (T18 A.4.1.3), so suggested_period = P/2 (my spec said 2P: wrong). Spec amended (eeb6028); fix round 1 sent to the T4 agent (also helpers, citations, guard tests); Task 6 agent told to word the dashboard sentence and the section 13 bullet accordingly. Cost if these had shipped: hot Jupiters with occultations rejected; reviewers sent to 4x the true period.

### Task 4: complete (18 Oct) — 14b71db + 3291e40 + 1c1dc4d; scoped re-review APPROVED (albedo 0.356 hand-checked). Non-blocking notes parked: planet fixture a/R* unphysical for its host (harmless), last-writer-wins albedo metrics on the free-phase path, no fixture isolates the 0.1 ratio or b gates. Plan/spec wording fixed.

### Task 6: complete (18 Oct) — 1773ed4 (ladder "3", Rung1Events registered, two signal-page sentences keyed on suggested_period / occultation_exception, spec section 13 bullet, README). Gated by the final review (no separate task review; ruling: small, disjoint, test_pipeline 48 passed in 904 s).
### Final stage (18 Oct, ~19:50): whole-change review (opus) on 668bb38..HEAD dispatched; focused suite (19 files, not detector/pipeline) running -> focused_suite.txt; `skysift inject --limit 10 --per-star 4 --workers-cpu 2` on cached real curves running -> .superpowers/sample-run/inject_ladder3.log and injections/kbonus_bkg.jsonl (ladder 3 completeness cost).
Focused suite at 1773ed4: 343 passed in 271 s (19 files; plus test_pipeline 48 passed earlier at the same tree bar docs). Detector tests untouched by this plan.

### Final review (opus, 18 Oct ~20:10): APPROVED WITH FIXES. Important: (1) expected epochs counted unobserved quarters -> a 4-quarter target fails gapped events; (2) second-pass signals count the detector mask as missing data -> gapped events. Minor: inject never reaches dominance/Chases/V-shape (3, accepted: long-period stratum later), new metrics hidden by RUNG_METRICS whitelist (4), unbounded per-epoch lists (5), Chases 1.34 s at P=190 d (6, accepted), docs drift (7), two T18 departures to record (8). Rulings in final-fix-brief.md; fix agent (opus) dispatched. The ladder-3 inject run (10 stars) was STOPPED and its partial output deleted: it ran on the buggy gapped rule; re-run after the fix.

### Departures from T18 recorded in the final fix round (18 Oct, final review finding 8)
- Gapped-fraction numerator: an event counts towards the gapped fraction when its coverage is >= 0.75 (`EVENT_COVERAGE_MIN`). T18 A.3.8 counts events with "SES != 0 or Rubble > 0.75". The final fix round restricts the expected epochs to the observed spans (3 d rule) and counts masked cadences as observed, which removes the two data-gap paths the T18 numerator was proposed to cover; the numerator itself stays as built.
- Zuma fires at SES < -1 (`ZUMA_SES_MIN`). T18 A.3.7.5 uses SES < 0. SkySift's per-event box depths are noisy, so an event one sigma bright is the floor; the spec states this choice.

### Final fix round: 771817b, scoped re-review APPROVED (notes: epochs within 3 d past the data ends are not expected, stricter than the brief, kept; plan 4.1 paragraph superseded by the spec). Plan complete pending the ladder-3 inject run.
Focused suite at 771817b: 349 passed in 289 s. Ladder-3 inject run (10 stars, 2 workers) restarted 20:18 on 771817b; results via `skysift inject-report .superpowers/sample-run/injections/kbonus_bkg.jsonl`.

### Real-curve check of ladder 3 (18 Oct, 22:10): inject --limit 10 (28 injections): 19 found (as the study), only 2 pass. 9 of 19 failed rung1_events "gapped events" (35-45 percent of epochs covered): good_mask (quality == 0) keeps 64-93 percent of cadences on real stars (ManualExclude 8.6 percent of all, CollateralCosmic 6.7, RollingBand 7.7, ZeroCrossing 2.6) plus the 7 percent gap-edge mask, so coverage against the regular cadence sits near 0.6. Ruling: coverage against the star own duty cycle (SkySift adaptation of Rubble). 1 of 19 failed V-shape with k pinned at 2.0 on a 185 ppm box: ruling: V-shape only on an unpinned fit whose depth agrees within 2x. Quality-mask relaxation (lightkurve-default style, about +7 percent of cadences) is search-side: deferred with a re-calibration. Fix agent (opus) dispatched: real-curve-fix-brief.md. The inject file will be deleted and the run repeated on the fix.
Pre-fix ladder-3 inject (771817b, 10 stars, 40 injections) kept as injections/kbonus_bkg_ladder3_771817b.jsonl: 22 found (55 percent; 15 of 16 above red-noise SNR 9), 17 ranked first, 4 pass; median depth ratio 0.94.

### Real-curve fix landed: 2ae6a13 (duty-cycle coverage, V-shape gates). Accepted departures: distributional coverage assertions (binomial counts), mask test asserts lower duty instead of FAIL. Open follow-up: why the real 185 ppm, 0.96 d fit pinned at k = 2.0 (the synthetic converges); a pinned fit on a true V binary now skips the V rule and relies on secondary/odd-even. Re-review (sonnet), focused suite + two pipeline mask tests, and inject re-run (fresh file, 10 stars) started 22:40.
Re-review of 2ae6a13 APPROVED; my follow-up commit clamps duty at 1 and points the gap table at sections 4.1/4.2.
Focused suite at 2ae6a13 (+ the two pipeline mask tests): 275 passed in 500.60s (0:08:20)

### Second inject run (2ae6a13, 10 stars, 40 injections, kept as injections/kbonus_bkg_ladder3_2ae6a13.jsonl): 22 found, 17 ranked first, 13 PASS (was 4): of the 9 non-passing found, 5 sit below sde_min (alias ranks), 2 are "too few seasons" (short spans), 1 eccentric_secondary (contamination, as in the study), 1 still "V-shaped or deep (k + b = 2.06)" on the 185 ppm, 0.96 d injection: grazing-degenerate fit with k < 1.9 passed the 2ae6a13 gates. Ruling: V-shape needs the free fit to beat the best non-grazing fit by delta chi2 >= 25 (vshape-fix-brief.md; agent dispatched, opus). Pass rate among found with SDE >= 11 and >= 3 seasons: 13 of 15.

### V-shape fix: 69baffa + 2ad3411 (non-grazing comparison fit, delta chi2 >= 25, the fit returns the better minimum; the real case was a grazing LOCAL MINIMUM k 0.53 / b 1.52, not a pinned k; now k 0.015, b 0.96, delta 0, PASS through rung 3). Re-review (sonnet), focused tests and a third 10-star inject run started 01:25 (19 Oct).
Focused tests at 2ad3411: 143 passed in 408.47s (0:06:48)

### V-shape fix complete (19 Oct): 69baffa + 2ad3411 + 1e78575 (tests pin the delta gate and the fit fallbacks; mutation-checked). Re-review APPROVED. The 18 Oct "fit pinned at k = 2.0" follow-up is CLOSED: the real cause was a grazing local minimum (k 0.53, b 1.52), now resolved by the non-grazing comparison. Note for reviewers: on shallow, cadence-smeared folds fit_k/fit_b/fit_a_over_r sit at model corners; only fit depth and t14 are meaningful.

### Third inject run (2ad3411, 10 stars, 40 injections, kept as injections/kbonus_bkg_ladder3_2ad3411.jsonl, 03:30 19 Oct): 22 found, 17 ranked first, 14 PASS. The 8 found-but-not-passing: 5 below sde_min (alias ranks, not detections), 2 "too few seasons" (short spans; pre-existing rule), 1 eccentric_secondary (contamination by a neighbouring binary, seen in the 18 Oct study too). Among genuine detections (SDE >= 11) with >= 3 seasons: 14 of 15 pass, so ladder 3 costs no measurable completeness on this sample while adding the Robovetter not-transit-like and stellar-eclipse rules. Depth ratio median 0.94 (box). PLAN COMPLETE. Pilot: 293 of 1000 at 03:30.
