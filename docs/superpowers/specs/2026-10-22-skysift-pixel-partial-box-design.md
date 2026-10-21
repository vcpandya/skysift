# SkySift pixel rung: voting with a box cut by the stamp edge

Date: 22 October 2026. Amends section 4.4 of `docs/superpowers/specs/2026-10-20-skysift-mission-control-design.md`, which keeps the voting rule and points here for the partial-box part. The history of the rule is in the recheck ledger (`.superpowers/sdd/2026-10-20-skysift-recheck/progress.md`, Task 4 review and the final review).

## 1. Intent and the why

Rung 5 localises a dip with difference images stacked per archive season, and a season votes only when both the target's and the brightest neighbour's 3 x 3 pixel boxes lie complete on valid pixels of the season frame. The rule was written in the Task 4 review of 20 October to stop a truncated box from pulling the centroid towards the frame interior, where it could reject a real candidate or pass a blend.

The live recheck of 21 October showed the cost. Four of the seven stage-1 candidates got no verdict because the target's box is incomplete in three of four seasons: KIC 5689121 (signal 1064) and KIC 6699887 (signals 3479 and 3480). A KBonus background star is, by construction, a star that Kepler did not target; it sits wherever someone else's stamp happened to catch it, often on the first row or column. The strict rule therefore removes exactly the stars SkySift searches. In all three signals the target's rounded pixel is on the stamp edge, the box holds 6 of its 9 pixels with the centre present, and the centroid that was measured anyway sits 9 to 33 sigma on the target's side of the bisector. The hand-built study of KIC 5689121 (`docs/candidates/kic-005689121/localisation/REPORT.md`) localised the same dip on 13 of 13 quarters with the target "roughly half off the stamp" in two seasons, using the S/N-box centroid on the pixels present and a two-source Gaussian fit on every valid pixel.

This change lets a season vote when a box is cut by the stamp edge or by a dead pixel, with an explicit allowance for the cut, and leaves the rule for complete boxes exactly as it is.

## 2. Goals, non-goals, anti-goals

Goals:

- A season whose target or neighbour box is partly off the stamp, or holds a dead pixel, can vote, with a penalty that is computed, stored and printed, never implied.
- The metrics say which pixels of each box were missing, so a reader of the dossier or the signal page can see the cut.
- The three real signals get a verdict with at least two voting seasons, and for KIC 5689121 that verdict agrees with the hand-built localisation.
- The dossier's pixel figure, the explain sentences, the pack's pixel paragraph and the dossier caveats read the new fields.

Non-goals:

- Replacing the Gaussian PSF with the Kepler PRF (mission control spec, section 9, out of scope).
- Changing the pass, fail or undetermined thresholds for complete boxes, or the two-voting-season rule for a pass.
- Correcting the centroid for the cut. The allowance widens the error; it does not move the point.

Anti-goals (what would make this a failure even if the tests pass):

- A season with a complete box votes differently from before.
- A blend passes because a truncated box pulled the centroid across the bisector without the vote carrying the allowance.
- A stored row written before this change stops rendering: every consumer treats the new keys as optional.

## 3. What the data says about the bias

The centroid is the 3 x 3 flux-weighted centroid around the highest-S/N difference pixel. The truncation matters only when that pixel sits at the edge, which is when the dipping star is the edge star. The missing pixels then lie outside the frame, so the centroid moves towards the interior, that is towards the other star. In every geometry the cut weakens the vote for the dipping star; it never strengthens it:

- target on the edge, dip on the target: the centroid moves towards the neighbour, the separation statistic drops (the real cases: raw centroid 0.01 to 0.07 px inside the target's position along the edge normal);
- neighbour on the edge, dip on the neighbour: the centroid moves towards the target, the fail statistic drops;
- the dip on the interior star: the highest-S/N pixel is in the interior, no truncation.

For a blend to pass, the cut would have to move the centroid past the bisector by three sigma, which needs the two stars within about twice the bias (0.3 px) of each other, where the centroid test is meaningless anyway and the two-source depth decides. The allowance below still covers that case, because it enters the error the vote divides by.

How large is the bias? A point source with the fitted Gaussian width (0.54 to 0.59 px in the study; the Kepler PRF core) sitting 0.1 px inside the edge row loses about 14 percent of its light off the frame; the centroid of the remaining 3 x 2 box sits about 0.1 px inside the true position. That is one to two times the Monte Carlo centroid error of a good season (0.04 to 0.08 px), so it cannot be ignored and must be carried.

## 4. The rule

Definitions, per season frame and per star (the target and the neighbour):

- The star's box is the 3 x 3 pixels around its rounded (col, row) on the season frame.
- `box_pixels` is the number of those pixels that lie on the frame and are valid, 0 to 9. `box_centre` says whether the star's own pixel is on the frame and valid. `box_missing` lists the missing pixels as (dcol, drow) offsets from the star's pixel, each in -1 to 1, in row-major order; empty for a complete box.
- A box is **complete** at 9. It is **partial** when the centre is present and at least `BOX_PIXELS_MIN = 6` are (the star is on an edge, not in a corner, or one pixel of the box is dead). Anything else is **incomplete**.

A season is eligible to vote when both boxes are complete or partial. With both complete nothing changes. With an incomplete box the season does not vote, `nearer` is "unknown" and `non_voting_reason` stays `"box incomplete: target"` or `"box incomplete: neighbour"` (the stored strings do not change; the count of pixels goes in the new fields).

For an eligible season with at least one partial box:

1. The centroid, its Monte Carlo covariance and the highest-S/N pixel are measured as now, on the valid pixels (the box centroid already clips at the frame and ignores invalid pixels).
2. The two-source fit runs on every valid pixel of the frame, as it does for complete boxes and as the hand-built study did; its PSF width `psf_sigma_pix` is the width used in step 3.
3. **The edge allowance.** For each partial box: a unit Gaussian of width `psf_sigma_pix` (the fitted value when finite and inside the fit bounds, else `EDGE_PSF_SIGMA_PIX = 0.6`) is rendered noiselessly at the star's season-frame position on the frame padded by two pixels of margin on every side; its 3 x 3 flux-weighted centroid around its brightest valid pixel is taken twice, once with the season's valid mask (padding invalid) and once with every pixel valid; the difference is the bias vector the cut gives a point source on that star. The season's `edge_bias_pix` is the largest |bias| over its partial boxes and `edge_bias_axis_pix` the largest |bias projected on the neighbour-to-target axis|. Both are 0 for a season whose boxes are complete.
4. **The penalty.** The allowance is a systematic error added in quadrature: `centroid_sigma_pix` becomes sqrt(sigma_mc^2 + edge_bias_pix^2) and `centroid_sigma_axis_pix` becomes sqrt(sigma_axis^2 + edge_bias_axis_pix^2); `separation_sigma` is the bisector distance over that inflated axis error. The raw statistic is kept as `separation_sigma_raw`. Every threshold (3 sigma either side of the bisector, the neighbour-depth veto, two voting seasons for a pass, a majority for a fail) applies unchanged to the penalised numbers.

5. **A cut box votes only when it is decisive.** A partial-box season whose penalised numbers put the dip on the target (`separation_sigma` above three and the neighbour depth under the veto) votes target; one that puts it on the neighbour (`separation_sigma` below minus three, or a significant neighbour depth with none on the target) votes neighbour; any other partial-box season abstains, with `non_voting_reason` "box cut: ambiguous (1.7 sigma)", "box cut: neighbour depth 200 +/- 60 ppm" or "box cut: neighbour depth not measured", counted in `seasons_silenced` and greyed in the figure with its numbers. The reason: the allowance makes a cut season's vote harder to be decisive by design, and a season pushed into the ambiguous band by the allowance we added must not veto what seasons with complete boxes decide; a season that cannot tell adds nothing either way. A complete-box season keeps today's rule, where an ambiguous season blocks a pass (the Task 4 ruling of 20 October).

Why quadrature and not a shift: the bias is of known direction only under the hypothesis the vote is testing, and the dossier's false-positive estimate reads `centroid_sigma_pix` as the centroid's error. One rule serves both: the cut is carried as an error, the vote needs the usual three sigma over the larger error, and the FPP's confusion radius widens with it.

Verdict reasons do not change their wording. "pixels: no season holds both stars on the frame" now fires when every season has an incomplete box. The pass reason keeps its form ("the dip is on the target in m/m seasons (centroid >= k sigma nearer)") with k the smallest penalised separation; the number of voting seasons with a partial box is a metric, not part of the reason, so `explain`'s matchers stay valid.

## 5. Metrics

Per season, new keys (all strict JSON, None for a missing number):

| Key | Meaning |
|---|---|
| `box_pixels_target`, `box_pixels_neighbour` | valid pixels of the star's box, 0 to 9 |
| `box_missing_target`, `box_missing_neighbour` | the missing pixels as `[[dcol, drow], ...]` offsets from the star's pixel |
| `box_partial` | true when the season votes (or is eligible) with at least one partial box |
| `edge_bias_pix`, `edge_bias_axis_pix` | the allowance, in pixels, 0 for complete boxes |
| `separation_sigma_raw` | the statistic before the allowance (equal to `separation_sigma` for complete boxes) |

`on_frame` (not stored; the verdict's "both stars on the frame") now means both boxes are eligible, complete or partial, and `two_source` follows it (the fit ran). `voting`, `nearer` and `non_voting_reason` keep their meanings. The rung's top-level metrics gain `rule_version` (2 for rows written under this rule; the rows of 21 October carry none and read as 1).

**Reaching the stored signals.** The recheck treats a stored "pixels: ..." reason as final (`recheck.wants_pixels`), so the three signals would never be rerun. A rung 5 row is reopened when its status is undetermined, its `seasons_silenced` holds a "box incomplete" key and its `rule_version` is below the current one; the pixel files come from the blob cache (kept and pinned for every stage-1 signal of the live run) or are fetched again, and the rerun replaces the signal's `pixels.npz` as a rerun on more quarters already does. Passes and fails stand: their voting seasons had complete boxes, whose rule has not changed, and an outage or a crash keeps today's retry rules. Top level gains `n_seasons_partial_box` (voting seasons with a partial box). `seasons_silenced` counts the non-voting reasons as before. `SeasonImage` carries the two `BoxCoverage` values so `pixels.npz` needs no new array: the missing pixels are already the invalid ones of `valid_<s>` around the stored positions.

## 6. What reads the new fields

- `skysift.ui.explain`: a rung 5 pass's headline comes from the stored reason through the `PASSES` matcher, so `_row` appends " (3 of 4 with a box cut by the stamp edge)" to it from the metrics when any voting season is partial; `_pixels` adds the same clause to its fallback headline, and a voting partial season's row adds "this star's box cut, 6 of 9 pixels, 0.10 px allowance in the error" (or "the neighbour's box cut ..."). An abstaining season's row reads "not voting (box cut: ambiguous (1.7 sigma))" through the existing non-voting path. A row without the keys renders as today.
- `skysift.ui.pixel_figure`: a voting partial season's vote line reads "nearer target, +6.0 sigma (box cut, 6 of 9 px)"; the missing pixels of each partial box are drawn as filled cells on the grid (one new rule, `.pf-missing`, in `RULES` and app.css, held by `tools/contrast_check.py` like the others); the figure's `aria-label` names the partial seasons ("archive season 0 box cut by the stamp edge (6 of 9 pixels)"). The error rings already read `centroid_sigma_pix`, so they widen with the allowance.
- `skysift.pack.pixel_sentence` and the RNAAS draft: one sentence when voting seasons are partial: "In seasons 0, 2 and 3 this star's 3 x 3 box is cut by the stamp edge; the centroid there is measured on the pixels present and the shift the cut would give a point source (0.10 px at most) is added to its error."
- `skysift.dossier.caveats`: a caveat with key `partial_box` and the same sentence, placed after the close-call caveat. `centroid_estimate` and `close_call` need no change: they read the penalised numbers.
- The About page's rung sentence for "On this star, not a neighbour" gains nothing; the voting rule lives in the spec.

## 7. Tests

- `tests/unit/test_pixels.py`: `BoxCoverage` on a frame with an edge, a corner and a dead pixel; the allowance of a point source 0.1 px inside the edge at width 0.55 px is 0.05 to 0.2 px towards the interior and 0 for a complete box; `make_quarters` with the target on the stamp's first column (`NO_TARGET`) votes with `box_partial`, a smaller `separation_sigma` than `separation_sigma_raw` and an inflated error, and the verdict is pass with the dip on the target and fail with the dip on the neighbour; a corner layout (4 of 9) stays silent with the old reason; a dead pixel inside a box (8 of 9) votes with a small allowance; "no season holds both stars on the frame" uses the corner layout; metrics stay strict JSON; the verdict unit test gains partial voting seasons.
- `tests/unit/synthetic_tpf.py` gains the stamp layouts `EDGE_TARGET` (the target on the first column of a 9 x 5 stamp, as the real KBonus stars sit) and `CORNER_TARGET`; `tests/unit/test_rung5_pixels.py`: the existing off-stamp test becomes a pass in the seasons that hold the neighbour (the synthetic neighbour turns about the target from season to season and falls off a 9 x 5 stamp in two of the four, so 2 of 2, both partial); the dip on the neighbour with the edge target fails; the corner target is undetermined with the frame reason. The synthetic PSF (0.9 px) is wider than the Kepler core the study fitted (0.54 to 0.59 px) and gives a 0.36 px allowance at the edge, so the edge tests run the scene at 0.55 px through a `psf_sigma` keyword of the fixtures; at 0.9 px every edge season abstains, which one test pins.
- UI, pack and dossier tests with a seeded partial season (a modified copy of `ui_seed.stage2_pixel_metrics`, the seed itself unchanged so the pinned labels stay): the explain row, the figure's vote line, missing cells and label, the pack sentence, the caveat, and a row without the keys rendering as before.
- `tests/unit/test_recheck.py`: `wants_pixels` reopens an undetermined rung 5 row silenced by the box rule and without `rule_version`; the same row with `rule_version` 2, a pass with a silenced season, and an undetermined row for another pixel reason stay settled.
- `tools/pixels_rerun.py` (new, committed): reruns rung 5 for named signals against a copy of a database opened read-only, reading the kept pixel files from the blob store without writing to it or to the archive (a fetch that refuses), storing no `pixels.npz`, and printing the verdict and a per-season table (box pixels, missing, raw and penalised separation, allowance, two-source depths); with `--study <results.json>` it prints the hand-built season values beside the engine's. The real-data check runs it on a backup-API copy of `.superpowers/sample-run/engine.db` for signals 1064, 3479 and 3480 and the already-passing 1032 (which must still pass, with the same numbers in seasons 0 to 2; its season 3, silenced on 21 October, is reported as it comes out).
- The golden test (`tests/golden`), the recheck tests and the full suite (`-m "not slow and not golden"`) pass; the gate runs in its own worktree at below-normal priority.

Agreement with the hand-built study (signal 1064), the acceptance test of section 1: every voting season nearer the target; the S/N-box centroid distance to the target within 0.1 px of the study's season value (0.14, 0.15, 0.13, 0.26 px for seasons 0 to 3); the two-source target depth within three combined sigma of the study's (2002, 2161, 2190, 1813 ppm); the neighbour depth consistent with zero at the veto level in every voting season. Season 3 is Q1 alone with two transits (the study: "weak and reported for completeness", 1.75 sigma on 21 October), so it is expected to abstain under rule 5; the check reports it either way. The real-data check is a verification step after the engine tasks, not a go/no-go before the UI work: a miss is written up for Vikram with the numbers, not tuned away.

## 8. Calls made here (for Vikram to check)

- Option (a) of the brief, the box with at least N valid pixels including the centre and the centroid on the valid pixels, with N = 6, combined with running the two-source fit (option (b)) on every valid pixel as the rule already does for complete boxes. Option (b) alone would have changed the rule for complete boxes.
- The penalty is a computed allowance (the shift the cut gives a point source of the fitted PSF width) added in quadrature to the centroid error, not a fixed sigma penalty and not a correction of the centroid.
- N = 6 excludes a star in a stamp corner. The study's hardest season (the target "roughly half off the stamp") was an edge case with 6 of 9 and is accepted.
- The default PSF width when the fit gives none is 0.6 px, the Kepler PRF core; the fitted width wins when it is finite.
- Stored reason strings and the non-voting reason strings do not change, so the dashboard's matchers, the pack and the tests pinned to them stay valid.
- The real-data check reads the live blob store but writes nothing: the tool opens the database copy read-only and refuses every network fetch.
- A cut box votes only when decisive and abstains otherwise (section 4, rule 5). If this is wrong, a cut season that would have warned of an ambiguous localisation is silent in the verdict; it is still drawn greyed with its sigma, and the explain row names it. The alternative, letting an ambiguous cut season block a pass, lets the allowance we add turn a would-be pass into no verdict, which defeats the change for exactly the stars it is meant for (KIC 5689121's single-quarter season 3).
- The recheck reopens only undetermined rung 5 rows that the box rule silenced, by a `rule_version` in the metrics; passes and fails stand. If this is wrong, a stage-2 candidate whose silenced season would now vote against it keeps its status until a person rechecks it; the rerun tool shows such a season.
- The partial-box clause is appended to the pass headline in the UI's row builder from the metrics, so the stored reason and its matcher stay as they are.

## 9. Known limitations carried

The Gaussian PSF is not the Kepler PRF, so a half-visible star's two-source flux is extrapolated from its visible half under the wrong profile; the study's fitted flux ratios (2.11 to 2.56 against 2.20 in Kp) show the size of that error. The neighbour-depth veto at three sigma is unchanged and could silence a partial-box season through PSF mismatch rather than a real neighbour dip; if the real-data check shows this, it is reported, not tuned away. The bisector test still takes the WCS positions as they are (the fit's common shift is stored, not applied), and the Gaia position is not moved to the TPF epoch; both are earlier follow-ups.

The allowance is applied to every season with a cut box, whichever star the lost light sits on. Section 3 says the cut biases the centroid only when the dipping star is the cut one; when the target's box is cut and the dip is on the complete neighbour, the centroid is unbiased yet its error is still inflated, so a raw statistic that a complete season would have reported as a disagreement (say -5 sigma) can fall inside the abstention band (-2.5 sigma) and go silent. The window is narrow: a raw neighbour-side statistic between -3 sigma and about -3 times the inflation factor, with every complete season on the target. The real cases are outside it (their highest-S/N pixel is the cut edge star). The alternative, applying the allowance only when the highest-S/N pixel's own 3 x 3 box is cut, follows section 3 literally and is a few lines; it is left for Vikram to choose at merge, because changing the rule's numbers after the real-data check means rerunning that check.
