# The systematic floor of the pixel rung's centroid error

Date: 22 October 2026. Spec `docs/superpowers/specs/2026-10-22-skysift-referee-pack-design.md`, section 5. Tool `tools/centroid_floor.py`, run on a read-only copy of the live engine database (`engine-trial.db`, made with the SQLite backup API on 22 October 2026) and the 129 kept pixel files of the eight stars with a rung 5 row. Result: `CENTROID_FLOOR_PIX = 0.0167` px per axis in `src/skysift/lc/pixels.py`, measured `2026-10-22`, pixel rule version 3.

## 1. Why a floor, and why measured here

Rung 5 says which star dims from the difference-image centroid of each archive season. Its error so far came from a 200-draw noise Monte Carlo (plus, since the partial-box change, an allowance for a 3 x 3 box cut by the stamp edge). The Monte Carlo sees photon noise only. It cannot see what changes from one quarter to the next within a season: the pointing, the PRF, and the integer shifts the season stack is built from. A season stacked from four quarters can therefore claim an error of a few hundredths of a pixel that the instrument does not support (KIC 9540248's one voting season claimed 0.007 px and a 62 sigma offset).

The Kepler pipeline met the same problem with a constant. Bryson et al. 2013 (PASP 125, 889, section VI.3) add a floor in quadrature to each offset component of the PRF-fit centroid, so that no offset below 0.2 arcsec can reach 3 sigma, and Twicken et al. 2018 (DV I, section III.4.2) carry it as a 0.0667 arcsec (0.2/3 arcsec) quadrature term. Both numbers belong to their estimator: the PRF-fit difference-image centroid of Kepler targets, in arcsec. SkySift's estimator is different (a 3 x 3 flux-weighted box on the highest signal-to-noise difference pixel, on a season frame stacked from quarters at integer shifts, in pixels), so the floor is measured on its own data and not copied. The precedent decides how the floor enters (a constant per axis, in quadrature, never instead of the formal error). The data decide its size.

## 2. Method

For each of the eight stars the tool takes the star's first localised signal (its ephemeris decides which cadences are out of transit and which quarters are usable) and builds the season frames exactly as the rung does (`season_images`). Within each season, every usable quarter's out-of-transit mean image (the direct image of the cadences one to three durations either side of the transits, as the season builder averages it) is placed on the frame at that quarter's integer offset. On it the tool takes the 3 x 3 flux-weighted centroid around the target's rounded frame position, and the same around the neighbour's. A quarter whose own pixels do not hold the complete box gives no centroid for that star. These images hold no transit and are bright, so their quarter-to-quarter scatter is the systematic the Monte Carlo cannot see.

Per season the scatter is the rms of the quarters' centroids about the season mean, per axis, with one degree of freedom spent on the mean. The pooled number sums the squared deviations of all pooled seasons and divides by their summed degrees of freedom (quarters minus one per season). A season enters the pool only with both stars' boxes complete on the frame (the rung's own `box_pixels` of 9; a cut box is pulled towards the frame interior and reported apart) and at least two quarters with a centroid. The mean of the two axes is their quadratic mean, the same per-axis convention as the Monte Carlo error (the square root of half the trace of its covariance). The scatter is not isotropic (the adopted neighbour value is 0.0141 px in column and 0.0189 px in row), so along a star-to-star axis close to a row the isotropic 0.0167 px sits about 12 percent under the row value.

The neighbour is measured the same way as a check. It is the Kepler target that owns the stamp, bright and well placed. The target's box, on the other hand, also holds a good part of the neighbour's light, and that light moves away from the target's box when the pointing moves the pair towards it. On the synthetic scene of the unit tests a 0.3 px shift of one quarter moved the target's direct-image box centroid by 0.047 px and the neighbour's by 0.168 px. The difference image the rung centroids holds the target's lost light alone, so it follows a pointing shift like the neighbour's box, not like the blended target box.

What the floor contains needs saying plainly, so that it is not read as unexplained pointing or PRF noise. Each quarter's centroid is taken relative to the season-mean position, so the scatter includes each quarter's sub-pixel residual about that mean, which the WCS already knows. The neighbour's scatter follows that spread closely (KIC 5689121 season 2: 0.035 / 0.025 px against a residual spread of 0.040 / 0.024 px; KIC 6146676 season 2: 0.025 / 0.029 against 0.023 / 0.023; KIC 9243078 season 2: 0.015 / 0.013 against 0.017 / 0.014). After the event-weighted correction of section 6 that spread no longer offsets the season's position; it only blurs the stacked image. Further, the floor is a per-quarter rms applied undivided to a season of n quarters, where an independent per-quarter error would shrink by the square root of n. Both make the floor conservative.

The registration residual is measured apart: per season, the event-weighted mean of each quarter's fractional offset from the first quarter's position (`residual_offsets_pix`, which the season builder already computes). It is a shift of the whole season, not a scatter, so it does not belong in the floor.

## 3. Per-season table

Each row is one season frame of the final code (with the registration correction of section 6 in place). The rms columns are per axis in pixels, column then row, with "-" where a star has fewer than two quarters with a complete box. "Live voting" says whether the season voted in any stored rung 5 row of the star.

| Star | Season | Quarters | Complete box | Pooled | Target rms (col / row) | Neighbour rms (col / row) | Registration residual (col / row) | Live voting |
|---|---|---|---|---|---|---|---|---|
| KIC 5689121 | 0 | 2, 6, 10, 14 | no | no, cut box | - | 0.010 / 0.007 | 0.012 / -0.040 | yes |
| KIC 5689121 | 1 | 3, 7, 11, 15 | yes | yes | 0.003 / 0.008 | 0.001 / 0.002 | 0.015 / -0.002 | yes |
| KIC 5689121 | 2 | 4, 8, 12, 16 | no | no, cut box | - | 0.035 / 0.025 | 0.043 / 0.027 | yes |
| KIC 5689121 | 3 | 1 | no | no, cut box | - | - | 0.000 / 0.000 | no |
| KIC 6146676 | 0 | 2, 6, 10, 14 | no | no, cut box | - | 0.020 / 0.023 | -0.020 / -0.006 | yes |
| KIC 6146676 | 1 | 3, 7, 11, 15 | yes | yes | 0.016 / 0.008 | 0.007 / 0.006 | -0.005 / 0.007 | yes |
| KIC 6146676 | 2 | 4, 8, 12, 16 | yes | yes | 0.005 / 0.016 | 0.025 / 0.029 | 0.022 / -0.015 | yes |
| KIC 6146676 | 3 | 5, 9, 13 | no | no, cut box | - | 0.002 / 0.031 | 0.030 / 0.018 | no |
| KIC 6384483 | 0 | 2, 14 | yes | yes | 0.014 / 0.002 | 0.003 / 0.047 | 0.011 / -0.005 | yes |
| KIC 6384483 | 1 | 15 | yes | no, one quarter | - | - | 0.000 / 0.000 | yes |
| KIC 6384483 | 2 | 8 | yes | no, one quarter | - | - | 0.000 / 0.000 | yes |
| KIC 6384483 | 3 | 1, 13 | yes | yes | 0.005 / 0.006 | 0.003 / 0.008 | -0.005 / -0.014 | yes |
| KIC 6699887 | 0 | 2, 6, 10, 14 | yes | yes | 0.014 / 0.009 | 0.015 / 0.014 | -0.031 / 0.011 | yes |
| KIC 6699887 | 1 | 3, 7, 11, 15 | no | no, cut box | - | 0.005 / 0.008 | -0.004 / -0.008 | yes |
| KIC 6699887 | 2 | 4, 8, 12, 16 | no | no, cut box | - | 0.010 / 0.027 | 0.016 / 0.005 | yes |
| KIC 6699887 | 3 | 1, 5, 9, 13 | no | no, cut box | - | 0.022 / 0.006 | -0.037 / 0.025 | yes |
| KIC 9243078 | 0 | 2, 6, 10, 14 | no | no, cut box | - | 0.007 / 0.029 | -0.006 / -0.035 | yes |
| KIC 9243078 | 1 | 3, 7, 11, 15 | no | no, cut box | - | 0.004 / 0.007 | 0.005 / -0.015 | no |
| KIC 9243078 | 2 | 4, 8, 12, 16 | yes | yes | 0.012 / 0.011 | 0.015 / 0.013 | -0.013 / 0.011 | yes |
| KIC 9243078 | 3 | 1, 5, 9, 13, 17 | no | no, cut box | - | 0.010 / 0.014 | -0.004 / 0.010 | yes |
| KIC 9540248 | 0 | 2, 6, 10, 14 | no | no, cut box | - | 0.008 / 0.016 | 0.020 / 0.036 | no |
| KIC 9540248 | 1 | 3 | yes | no, one quarter | - | - | 0.000 / 0.000 | yes |
| KIC 9540248 | 2 | 4, 8, 12, 16 | no | no, cut box | - | 0.016 / 0.023 | 0.008 / -0.023 | no |
| KIC 9540248 | 3 | 1, 5, 9, 13, 17 | no | no, cut box | - | 0.007 / 0.012 | 0.000 / 0.009 | no |
| KIC 9950612 | 0 | 2, 6, 10, 14 | no | no, cut box | - | 0.012 / 0.007 | 0.044 / 0.032 | no |
| KIC 9950612 | 1 | 3, 11, 15 | no | no, cut box | - | 0.010 / 0.003 | -0.001 / -0.001 | no |
| KIC 9950612 | 2 | 4, 8, 12, 16 | no | no, cut box | - | 0.024 / 0.011 | 0.015 / 0.057 | no |
| KIC 9950612 | 3 | 0, 1, 5, 13, 17 | no | no, cut box | - | 0.022 / 0.015 | 0.031 / 0.021 | no |
| KIC 10352243 | 0 | 2, 6, 14 | no | no, cut box | - | 0.012 / 0.031 | -0.015 / 0.034 | yes |
| KIC 10352243 | 1 | 3 | no | no, cut box | - | - | 0.000 / 0.000 | no |
| KIC 10352243 | 2 | 8 | no | no, cut box | - | - | 0.000 / 0.000 | no |
| KIC 10352243 | 3 | 5, 9, 17 | no | no, cut box | - | 0.010 / 0.006 | 0.013 / -0.003 | yes |

Of 32 season frames, 7 enter the pool (24 quarters, 17 degrees of freedom per axis). 22 are left out for a cut box (the KBonus targets sit on or next to the first column or row of their neighbour's stamp, so most of their seasons cut the box) and 3 for a single quarter with a complete box.

## 4. The pooled numbers

| Run | Pooled seasons (quarters) | Target rms, col / row / mean (px) | Neighbour rms, col / row / mean (px) |
|---|---|---|---|
| Final code (positions at the event-weighted mean) | 7 (24) | 0.0110 / 0.0102 / 0.0106 | 0.0141 / 0.0189 / 0.0167 |
| Before the registration correction (positions of the first quarter) | 9 (31) | 0.0160 / 0.0169 / 0.0164 | 0.0150 / 0.0209 / 0.0182 |

The two runs differ in two seasons only. On the first quarter's frame KIC 6146676's target sits at column 0.508 in season 0 and KIC 10352243's at row 3.476 in season 0; at the event-weighted mean they move to 0.488 and 3.510, the rounded box pixel moves one pixel onto the frame edge, and both boxes are cut (6 of 9 pixels). Those two seasons had the largest target scatter of the first run (0.034 px in column and 0.047 px in row), so the target's pooled number falls from 0.016 to 0.011 px when they leave. With 17 to 22 degrees of freedom per axis an rms carries roughly a 15 percent error of its own, so this is a small-sample effect and not a change in the instrument.

The neighbour, the check, is steadier (0.0167 and 0.0182 px) and lies above the target in both runs. Pooled over its own complete seasons (every season with at least two quarters holding the neighbour's complete box, whatever the target's box: 26 seasons, 73 degrees of freedom per axis) it gives 0.0152 / 0.0181 px, quadratic mean 0.0167 px, the same value on three times the data, so the adopted number does not hang on the two seasons that changed class. Section 2 gives the reason: the target's blended box damps a pointing shift that the difference-image centroid does not damp. The adopted value is therefore the larger of the two pooled quadratic means on the final code (in the spirit of Bryson et al. 2013, who take the larger of the formal and the empirical quarter-to-quarter error of the offset), which this time is the neighbour's: 0.0167 px per axis (0.066 arcsec at 3.98 arcsec per pixel). It agrees, by coincidence of two independent measurements, with Kepler DV's 0.0667 arcsec. The tool prints this choice as `adopted` (and `adopted_from`), so a rerun gives the constant directly.

## 5. Validation on KIC 6146676

KIC 6146676 (signals 1032 and 5585, a pass at 17 sigma) is the star with the strongest transit, so its real dip can be localised quarter by quarter. For every voting season with complete boxes the tool measures each quarter's difference-image S/N-box centroid on the season frame with its own 200-draw Monte Carlo error, then compares the quarters' scatter about their weighted mean with those errors and, at the season level, the empirical error of the season centroid (scatter over the square root of the number of quarters) with the season's Monte Carlo error. On the final code seasons 1 and 2 qualify (season 0 is now a cut box, season 3 always was).

| Signal | Season | Quarters | Quarter scatter, col / row (px) | Quarter Monte Carlo rms (px) | Season empirical, col / row (px) | Season Monte Carlo (px) | Season excess |
|---|---|---|---|---|---|---|---|
| 1032 | 1 | 3, 7, 11, 15 | 0.041 / 0.138 | 0.111 | 0.020 / 0.069 | 0.071 | 0 / 0 |
| 1032 | 2 | 4, 8, 12, 16 | 0.123 / 0.114 | 0.456 | 0.062 / 0.057 | 0.075 | 0 / 0 |
| 5585 | 1 | 3, 7, 11, 15 | 0.048 / 0.087 | 0.111 | 0.024 / 0.044 | 0.072 | 0 / 0 |
| 5585 | 2 | 4, 8, 12, 16 | 0.058 / 0.096 | 0.422 | 0.029 / 0.048 | 0.077 | 0 / 0 |

The spec's criterion is that the excess scatter must not exceed the floor. On the pooled number it passes: the pooled quarter-level excess the tool computes is 0 px in both axes for both signals (8 quarters each, 12 degrees of freedom), and the season-level excess is 0 in every season. The first run, with season 0 still complete, gave the same for it (empirical 0.026 / 0.017 px against a Monte Carlo error of 0.047 px). It fails on one axis of one season, without significance: signal 1032, season 1, row, 0.081 px of quarter-level excess against a quarter error of 0.111 px (chi-square 4.6 on 3 degrees of freedom, chi-square per degree of freedom 1.5, p = 0.20).

The test is weak, and its limit should be read as such. The per-quarter Monte Carlo errors (0.11 to 0.46 px) are inflated by draws that jump to another box at a single quarter's signal to noise, so most quarter-level chi-square values sit far below 1. At the season level the comparison is against Monte Carlo errors of 0.071 to 0.077 px with 3 degrees of freedom per season and axis. A per-quarter systematic would have to reach about 0.13 px (about 0.06 px on the season's centroid) before the expected chi-square of either signal crossed its 95 percent point. So the real transit of KIC 6146676 is consistent with the 0.0167 px floor, but it cannot test the floor below about 0.06 px at the season level (0.13 px per quarter). The floor's size rests on the direct-image measurement of sections 3 and 4, and this validation only excludes a much larger excess.

## 6. The registration ruling

The spec's rule: correct a season's star positions to the event-weighted mean of its quarters only if the registration residual exceeds the floor on a live voting season; otherwise report it and leave it.

The residuals on live voting seasons reach 0.043 px (KIC 5689121, season 2, column), and eight live voting seasons exceed 0.0167 px on at least one axis: KIC 5689121 seasons 0 and 2, KIC 6146676 seasons 0 and 2, KIC 6699887 seasons 0 and 3, KIC 9243078 season 0 and KIC 10352243 season 0. The largest over all seasons is 0.057 px (KIC 9950612, season 2, which does not vote). So the condition holds, and the correction is applied.

Ruling: every season's star positions are the event-weighted mean of the quarters' own WCS positions on the frame (each quarter's position less its integer offset), for the target and for the neighbour each from its own positions, in `season_images`. This is an exact correction from data the pipeline already has, with precedent in Bryson et al. 2013 combining per-quarter offsets rather than images. It is applied to every season, including those below the floor, since a threshold on an exact correction would move a 0.017 px residual and leave a 0.016 px one. The target's shift is stored per season as `registration_shift_pix` (zero for a season of one quarter, and for quarters that share their WCS). The box coverage, the edge allowance, the two-source fit and the bisector all use the corrected positions. Two live voting seasons change class as a result (KIC 6146676 season 0 and KIC 10352243 season 0, from a complete box to a box cut to 6 of 9 pixels, section 4): they now carry the edge allowance and vote only when decisive. The live recheck reports what that does to their verdicts.

## 7. The adopted rule

`centroid_sigma_pix` is the quadrature sum of the Monte Carlo error, the edge allowance and `CENTROID_FLOOR_PIX`, per axis, and `centroid_sigma_axis_pix` the same along the star-to-star axis, so `separation_sigma` is the bisector distance over the inflated axis error. Every season stores the raw Monte Carlo error as `centroid_sigma_mc_pix`, the floor as `floor_pix` and the registration shift as `registration_shift_pix`. `separation_sigma_raw` stays the separation over the Monte Carlo axis error alone. The pixel rule version is 3, and the recheck reopens every rung 5 row that localised under an older version, whatever its status (passes, fails and undetermined rows; stage 2 candidates and signals the pixels rejected come back for the pixel step alone). The FPP reads the inflated error through `centroid_estimate`, so its confusion radius widens with it. The dossier's Monte Carlo caveat and the pack's pixel sentence each name the floor in one clause.

The effect is small where the Monte Carlo error is large. Most live seasons have Monte Carlo errors of 0.05 to 0.15 px, which the floor raises by 1 to 6 percent. It is large where the Monte Carlo error is implausibly small: KIC 9540248's voting season (0.007 px) goes to about 0.018 px, so its 62 sigma offset falls to roughly 24 sigma (an estimate from the stored numbers; the live recheck measures it). KIC 6146676's 17 sigma pass keeps a wide margin.

## 8. Recalibration

Rerun the tool whenever the pixel set changes (a new star with a rung 5 row, more quarters, a change to the season builder or the box rule), on a fresh read-only copy of the live database:

```
python -m tools.centroid_floor --db <copy.db> --root C:\Research\skysift --out <out.json> --validate 1032
```

`--root` is the directory the artifact rows' `local_path` is relative to. The tool prints the per-season table, the pooled target and neighbour numbers, the adopted value and the KIC 6146676 validation, and writes them all to the JSON file. Change `CENTROID_FLOOR_PIX` and `CENTROID_FLOOR_MEASURED` by hand with the new value and date, update this note, and raise `PIXEL_RULE_VERSION` in `src/skysift/vetting/rung5_pixels.py` so the recheck measures every stored rung 5 row again under the new floor. The outputs of this measurement are in the workspace folder `.superpowers/sdd/2026-10-22-skysift-referee-pack/floor/` (`floor-final.json`, and `run1` for the first run, before the registration correction).
