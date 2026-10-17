# Long-period SDE normalisation study (17 October 2026)

## Question

Three targets in the sample sweep had first-pass peaks at 22 to 29 d with `snr` 30 to 78 but SDE 6.5 to 7.5, all at the 9 h duration cap. Either the per-band median/MAD normalisation is inflated at long periods by a comb of systematics (the 93 d quarter and its harmonics, `SYSTEMATIC_PERIODS = (93.0, 46.5, 372.5, 186.25)` in rung 1), which would suppress genuine long-period dips, or the peaks are themselves systematics.

## Ruling

Keep the per-band normalisation. Excluding the systematic comb moves the 24.82 d SDE from 6.520 to 6.530. The three peaks are systematics. Each one is a period that lines up two to six deep dips (0.3 to 3.2 percent), and every one of those dips sits 0.16 to 0.86 d after the start of a data segment (tables in section 5). The dips are thermal ramps after a downlink or safe mode, and the 2 d running median in `detrend` leaves them in the flux. The band median is high (snr 30.7 at 21 to 25 d against 4.2 at 0.5 to 0.6 d) because those same dips raise the box snr at every long trial period, not because of the quarter comb. The normalisation reports that noise level correctly.

The current pipeline, which pre-whitens before the search, already lowers the three peaks to SDE 5.79, 3.68 and 2.56. Pre-whitening fitted 47 to 49 of the 50 allowed sinusoids on these stars, so it may be fitting the ramps; Task 9 should re-measure this.

The defect is in the light-curve preparation, not in the normalisation. Masking the first 1.0 d after every gap longer than 0.5 d (5.3 to 5.6 percent of the points) removes all three peaks. It also lifts an injected 1250 ppm, 9 h box at 23.55 d from SDE 6.2 to 24.6 on this star. Section 7 proposes that as a separate task. No detector code changes here. Task 9 put the mask into `detrend` (section 8).

## 1. Reproduction

The peaks come from the 17 October sample run (run 28, started 06:01 UTC, version `1+6564338a`). That run predates the pre-whitening commit `dcba434` (07:04 UTC), so the sweep searched the detrended flux without pre-whitening. A search on the pre-whitened flux does not reproduce the peaks (section 7). Every number below uses `detrend(lc)` without pre-whitening, the 0.5 to 30 d window and `BoxDip(mask_iterations=0, alias_resolution=0)`, with the coarse search and ranking called as `search()` calls them.

| Target | Sweep signal | Reproduced period (d) | t0 (BKJD) | Duration (h) | Depth (ppm) | snr = BLS power | SDE | Band |
|---|---|---|---|---|---|---|---|---|
| gaia-dr3-2053482589315365120 | #77: 24.8226 d, SDE 6.5, snr 78.4 | 24.82263 | 121.08495 | 9.0 | 1250 | 78.357 | 6.5204 | 22 |
| gaia-dr3-2053223345080135040 | #53: 22.6657 d, SDE 7.5, snr 46.9 | 22.66567 | 135.15575 | 9.0 | 1743 | 46.939 | 7.4880 | 22 |
| gaia-dr3-2053295363101998848 | #61: 28.5095 d, SDE 7.1, snr 32.6 | 28.50954 | 134.17033 | 9.0 | 608 | 32.628 | 7.1264 | 23 |

The reproduction matches the stored signals to every printed digit. The 24.82 d curve has 57,550 searched points over 1470.5 d, with a robust per-point scatter of 480.7 ppm.

## 2. The 24.82 d band

| Quantity | Value |
|---|---|
| Band 22 period range | 21.328 to 25.295 d |
| Trial periods | 9847 |
| Trial durations | 1, 1.5, 2, 3, 4, 6, 9 h |
| Trials whose best duration is 9 h | 9580 (97.3 percent) |
| Band median power | 30.665 |
| Band MAD | 4.933 |
| Band MAD-sigma (1.4826 MAD) | 7.314 |
| Peak raw power | 78.357 |
| Peak raw power rank in band | 1 of 9847 |
| Band power at the 50th / 90th / 99th / 99.9th percentile | 30.66 / 41.16 / 64.31 / 77.27 |
| Next separated band peaks (period, power, SDE) | 22.741 d, 56.95, 3.59; 23.252 d, 52.63, 3.00; 21.936 d, 50.97, 2.78 |

The band median climbs steadily with period, as the detector's comment expects. Long periods differ in degree, not in kind:

| Band | Periods (d) | Median power | MAD-sigma |
|---|---|---|---|
| 0 | 0.500 to 0.593 | 4.24 | 1.08 |
| 6 | 1.392 to 1.650 | 7.53 | 2.32 |
| 12 | 3.873 to 4.593 | 13.70 | 3.74 |
| 18 | 10.779 to 12.784 | 21.79 | 4.85 |
| 21 | 17.983 to 21.328 | 28.96 | 7.14 |
| 22 | 21.328 to 25.295 | 30.67 | 7.31 |
| 23 | 25.295 to 30.000 | 30.93 | 6.41 |

## 3. Excluding the systematic comb

I read "each systematic period and its first five harmonics" as P/k for k = 1 to 5, for each of the four systematic periods. A trial is excluded when it lies within 1 percent of a comb period. Only 23.25 d (93/4, equal to 46.5/2) falls in band 22. Nothing below 30 d reaches band 23, because the shortest comb period above band 22 is 93/3 = 31 d.

| Target (band) | Comb periods in band | Trials before | Excluded | Trials after | Median before / after | MAD-sigma before / after | SDE before / after |
|---|---|---|---|---|---|---|---|
| 24.82 d (22) | 23.25 | 9847 | 1152 | 8695 | 30.665 / 30.234 | 7.314 / 7.369 | 6.520 / 6.530 |
| 22.67 d (22) | 23.25 | 9847 | 1152 | 8695 | 23.472 / 23.369 | 3.134 / 3.127 | 7.488 / 7.539 |
| 28.51 d (23) | none | 6565 | 0 | 6565 | 15.469 / 15.469 | 2.408 / 2.408 | 7.126 / 7.126 |

The excluded trials around 23.25 d have a median power of 32.97, close to the band median. The comb does not inflate the band statistics. The 23.252 d peak (power 52.63) is the one real comb feature in band 22, and it sits well below the 24.82 d peak.

As a sensitivity check I widened the comb to k = 1 to 20. That adds 21.912, 23.281 and 24.833 d to band 22, and 26.607 and 28.654 d to band 23. The SDE becomes 6.87 (24.82 d), 7.27 (22.67 d) and 7.70 (28.51 d). The 24.82 d peak lies 0.04 percent from 372.5/15, so at that order its own trials leave the statistics. On the pre-whitened flux the k = 1 to 5 exclusion moves the 24.82 d SDE from 5.79 to 6.58. None of these reaches the sweep threshold of 9, let alone the current `sde_min` of 11.

## 4. The fold at 24.82 d

The fold uses 100 bins of 5.96 h, with a median of 574 points per bin. Depth is 1 minus the bin mean, in ppm.

| Phase | Points | Depth (ppm) |
|---|---|---|
| -0.035 | 530 | 168 |
| -0.025 | 519 | 315 |
| -0.015 | 592 | 780 |
| -0.005 | 607 | 1232 |
| +0.005 | 605 | 1219 |
| +0.015 | 622 | 540 |
| +0.025 | 600 | 237 |
| +0.035 | 615 | 103 |

| Fold statistic | Value |
|---|---|
| Out-of-transit bin mean | 79 ppm |
| Out-of-transit bin scatter | 104 ppm |
| White-noise expectation for a 574-point bin | 20 ppm |
| Deepest out-of-transit bins | 385, 335, 331 ppm at phases 0.205 to 0.225 |

The full fold, one character per bin from phase -0.5 to 0.5, with depth scaled to the deepest bin:

```
| .:::      ... ..              .        ...   .:+@%-.      .          :::.   ..  ..   .      .      |
```

The out-of-transit scatter is five times the white-noise value. The fold looks like a clean 1250 ppm box only because the stacked mean is dominated by a few epochs.

## 5. Single event or repeating: per-epoch depths

For each epoch with points inside the 9 h window, depth is 1 minus the window mean, and the white error is the per-point scatter over the root of the window count. A segment starts after a time step longer than 0.5 d. The offset is the epoch mid-time minus the first searched time of its segment. The gap is the length of the time step before that first point. The tables list every epoch deeper than 3000 ppm plus the next largest contributor.

### gaia-dr3-2053482589315365120, 24.82 d (57 epochs with window points)

| Epoch | Mid-time (BKJD) | Quarter | Points | Depth (ppm) | White error (ppm) | Share of the stacked depth | Offset after segment start (d) | Preceding gap (d) |
|---|---|---|---|---|---|---|---|---|
| 38 | 1064.345 | 11 | 18 | 20648 | 113 | 0.298 | 0.18 | 0.94 |
| 49 | 1337.394 | 14 | 15 | 19332 | 124 | 0.233 | 0.17 | 0.92 |
| 53 | 1436.684 | 15 | 17 | 14210 | 117 | 0.194 | 0.43 | 0.90 |
| 18 | 567.892 | 6 | 18 | 6651 | 113 | 0.096 | 0.49 | 0.92 |
| 0 | 121.085 | 0 | 18 | 3157 | 113 | 0.046 | 0.55 | data start |
| 23 | 692.005 | 7 | 17 | 3103 | 117 | 0.042 | 0.86 | 0.94 |
| 5 | 245.198 | 2 | 18 | 2034 | 113 | 0.029 | 13.25 | 0.51 |

| Stacked depth | Value (ppm) | White snr |
|---|---|---|
| All 57 epochs (point-weighted) | 1349 | 85.3 |
| Without the deepest epoch | 965 | 60.4 |
| Without the deepest 2 | 656 | 40.7 |
| Without the deepest 3 | 392 | 24.1 |
| Without the deepest 5 | 198 | 11.9 |
| Median single-epoch depth (`event_depths`, linear local baseline) | 84 | |

There are three depth values for this peak, and they measure different things. The epoch stack (1349 ppm) is the mean of all 923 window points relative to 1.0. The BLS depth (1250 ppm) is the window mean relative to the mean of the points outside the window, which sits about 79 ppm below 1.0 (the fold's out-of-transit bin mean): 1349 - 79 = 1270 ppm, within 20 ppm of the BLS value. The two central fold bins (about 1230 ppm) together span 11.9 h, wider than the 9 h box, so they take in shallower points from the wings.

The signal is not one event, and it is not a repeating transit either. The three deepest epochs carry 72 percent of the stacked depth. Their depths run from 14,000 to 21,000 ppm, while 49 of the 54 epochs outside the deepest three sit within 750 ppm of zero (43 within 500 ppm). A transit at 24.82 d deep enough to give 2 percent in epoch 38 would show in every epoch. `event_depths` gives a chi-square of 657 for 54 degrees of freedom about its weighted mean. It measured 55 of the 57 epochs: epoch 13 has too few points inside its 0.8-width core window, and epoch 38 sits 0.18 d after a gap, which leaves fewer than 20 points for its local baseline.

All six epochs deeper than 3000 ppm sit 0.17 to 0.86 d after the start of a segment. Five follow a gap of 0.90 to 0.94 d, which is the monthly downlink, and one is the first day of the light curve. The 24.82 d period is one of many trial periods that line up several of these segment starts.

### gaia-dr3-2053223345080135040, 22.67 d (49 epochs with window points)

| Epoch | Mid-time (BKJD) | Quarter | Points | Depth (ppm) | White error (ppm) | Share of the stacked depth | Offset after segment start (d) | Preceding gap (d) |
|---|---|---|---|---|---|---|---|---|
| 45 | 1155.111 | 12 | 19 | 31917 | 215 | 0.520 | 0.19 | 0.94 |
| 11 | 384.478 | 4 | 14 | 17007 | 250 | 0.204 | 0.16 | 0.92 |
| 41 | 1064.448 | 11 | 14 | 11201 | 250 | 0.134 | 0.31 | 0.94 |
| 59 | 1472.430 | 16 | 15 | 4441 | 241 | 0.057 | 0.31 | 1.00 |
| 13 | 429.809 | 4 | 15 | 1414 | 241 | 0.018 | 28.99 | 3.98 |

Stacked depth: 1809 ppm, white snr 49.1. Without the deepest 3 epochs: 277 ppm. Of the 46 epochs outside the deepest three, 34 lie within 750 ppm of zero. `event_depths` could not measure epochs 41 and 45, the two gap-edge dips 0.31 and 0.19 d after a segment start, among 8 unmeasured epochs.

### gaia-dr3-2053295363101998848, 28.51 d (39 epochs with window points)

| Epoch | Mid-time (BKJD) | Quarter | Points | Depth (ppm) | White error (ppm) | Share of the stacked depth | Offset after segment start (d) | Preceding gap (d) |
|---|---|---|---|---|---|---|---|---|
| 39 | 1246.042 | 13 | 18 | 8688 | 109 | 0.409 | 0.16 | 0.96 |
| 13 | 504.794 | 5 | 19 | 8175 | 106 | 0.406 | 0.18 | 0.72 |
| 26 | 875.418 | 9 | 19 | 874 | 106 | 0.043 | 0.63 | 0.67 |

Stacked depth: 615 ppm, white snr 33.2. Without the deepest 2 epochs: 75 ppm. Of the 36 epochs outside the deepest three, 35 lie within 750 ppm of zero.

### Gap-edge dips across the 24.82 d curve

| 9 h window | Windows | Median depth (ppm) | Deeper than 1000 ppm | Deeper than 5000 ppm |
|---|---|---|---|---|
| First 9 h of a segment | 65 | 30 | 18 (27.7 percent) | 13 (20.0 percent) |
| Last 9 h of a segment | 65 | -12 | 0 | 0 |
| Interior | 3308 | 0 | 109 (3.3 percent) | 12 (0.36 percent) |

A segment start is 55 times more likely than an interior window to hold a dip deeper than 5000 ppm. Segment ends show no such dips. The other two curves agree: 9 of 56 and 4 of 54 segment starts are deeper than 5000 ppm, against 0 segment ends in each. That asymmetry is the signature of a thermal ramp after a downlink or safe mode. The 2 d running median follows the stable level, not the first few hours of a ramp, so `detrend` leaves the ramp in the flux. For the 24.82 d and 28.51 d targets, KBonus `FLUX` is an exact multiple of `SAP_FLUX`. For gaia-dr3-2053223345080135040 the two columns differ, and both show the dips (12,105, 24,112 and 27,844 ppm in the detrended SAP flux at its three deepest epochs). The dips are instrumental, not an artefact of the PSF fit.

## 6. What `snr` measures and why it disagrees with SDE

`Signal.snr` is `depth / depth_err` from astropy's BLS at the chosen period, duration and t0. The search sets `dy` to a single robust per-point scatter, so `depth_err` is about that scatter over the root of the in-window point count: 480.7 / sqrt(923) = 15.8 ppm, against the reported 15.9 ppm. With `objective="snr"` the BLS power is the same number, so snr 78.36 is the peak's raw power. SDE is that power measured against every other trial period in the band: (78.357 - 30.665) / 7.314 = 6.52.

The two disagree at long periods for three reasons, each measured on the 24.82 d curve:

| Reason | Measurement |
|---|---|
| The depth is a mean, so a few extreme points dominate it | 3 of 57 epochs carry 72 percent of the stacked depth |
| Red noise at the 9 h scale | Robust scatter of 18-point (9 h) bins is 1.97 times the white value. snr / 1.97 = 39.8. The box depth at 91 control phases of the same period has a robust spread of 96 ppm against the BLS error of 15.9 ppm |
| Few events and the longest box | About 57 events and a 9 h box at 21 to 25 d. Many trial periods can line up two or three of the 13 deep segment-start dips with a single t0, so the typical best box in band 22 already has snr 30.7, 7 times the 4.2 typical of 0.5 to 0.6 d |

A large `snr` therefore says that the in-window mean is far below 1 in white-noise units. It does not say that the dip repeats. SDE asks the right question: whether this period stands out from the other long periods, which suffer the same dips.

## 7. Masking segment starts, and the sensitivity the dips cost

These are single-band searches on the band's own trial grid (see Method). "Masked" drops the first 1.0 d after every gap longer than 0.5 d. The injected box has the peak's duration and 1, 0.5 and 0.25 times its depth, at a period clear of the band's top peaks.

| Target | Flux | Points masked | Band median | Band MAD-sigma | Original peak SDE | Injected P (d) | SDE at 1x depth | SDE at 0.5x | SDE at 0.25x |
|---|---|---|---|---|---|---|---|---|---|
| 24.82 d (1250 ppm) | as searched | 0 | 30.66 | 7.31 | 6.52 | 23.55 | 6.22 | 1.41 | 1.45 |
| 24.82 d (1250 ppm) | masked | 3040 (5.3 percent) | 11.07 | 2.29 | 1.18 | 23.55 | 24.59 | 10.69 | 3.09 |
| 22.67 d (1743 ppm) | as searched | 0 | 23.47 | 3.13 | 7.44 | 21.75 | 6.52 | -0.13 | -0.08 |
| 22.67 d (1743 ppm) | masked | 2211 (5.5 percent) | 6.42 | 1.07 | 3.05 | 21.75 | 33.82 | 14.63 | 3.98 |
| 28.51 d (608 ppm) | as searched | 0 | 15.47 | 2.41 | 7.13 | 27.00 | 5.92 | -0.30 | -0.25 |
| 28.51 d (608 ppm) | masked | 2588 (5.6 percent) | 8.37 | 1.51 | 0.82 | 27.00 | 14.74 | 4.59 | 1.24 |

Two results follow. Masking removes all three peaks: their SDE falls to 0.8 to 3.1. The same dips also cost real sensitivity. On the flux as searched, a genuine periodic box as deep as the 24.82 d peak scores SDE 6.2, below the systematic it competes with. At half that depth, BLS prefers to line up the gap-edge dips over the injected box, and the injected period gains no power at all. The 0.5x and 0.25x scores on the flux as searched (1.41 and 1.45, -0.13 and -0.08, -0.30 and -0.25) are therefore the power the period already had before injection, and their order is noise. The band normalisation is not the cause. Its median and MAD reflect the dips. Once the dips are masked, the band median falls by a factor of 1.8 to 3.7 and the MAD-sigma by 1.6 to 3.2.

The current pipeline pre-whitens before the search (47 to 49 sinusoids on these three stars). That lowers the three peaks to SDE 5.79, 3.68 and 2.56. It does not remove the dips:

| 24.82 d curve, pre-whitened | Band median | Band MAD-sigma | Peak SDE | Injected 1250 ppm SDE | Injected 625 ppm SDE |
|---|---|---|---|---|---|
| As searched | 18.58 | 4.31 | 5.79 | 7.68 | 2.39 |
| First 1.0 d after gaps masked | 8.64 | 1.58 | 1.99 | 24.75 | 10.14 |

Proposed follow-up, as a separate task because it changes the flux every rung sees: drop or down-weight the first 1.0 d after every gap longer than 0.5 d before the search, or repair the segment edges in `detrend`. Accept it on injection-recovery across the null set. It must not lower the 13.769 d synthetic recovery, and it must lift long-period recovery at a fixed SDE threshold. A real transit that falls in the first day of a segment would be lost, which is about 5 percent of the data.

## 8. The gap-edge mask in `detrend` (Task 9, 17 October 2026)

`detrend` now drops every point less than 1.0 d (`GAP_EDGE_MASK_DAYS`) after the first point that follows a time step longer than 0.5 d (`GAP_EDGE_TRIGGER_DAYS`) between consecutive good points, before the running median, and returns NaN there. The first point of the light curve is not a gap edge. `DETREND_VERSION` is 2 and both constants are part of `search_version`, so runs made before the mask are searched again rather than mixed.

### Why the trigger is 0.5 d and not the 1.0 d segment split

The running median splits segments at steps longer than `gap_days` (1.0 d). The monthly downlink gaps are shorter than that, so a mask tied to `gap_days` would miss every ramp this study found. Gap census of the good points:

| Star | Gaps 0.5 to 0.7 d | Gaps 0.7 to 1.0 d | Gaps over 1.0 d | Deep dips caught, trigger 0.5 d | Deep dips caught, trigger 1.0 d | Points masked, trigger 0.5 d | Points masked, trigger 1.0 d |
|---|---|---|---|---|---|---|---|
| gaia-dr3-2053482589315365120 | 7 | 25 | 39 | 5 of 5 | 0 of 5 | 5.06 percent | 2.78 percent |
| gaia-dr3-2053223345080135040 | 9 | 19 | 35 | 4 of 4 | 0 of 4 | 5.33 percent | 3.06 percent |
| gaia-dr3-2053295363101998848 | 7 | 18 | 35 | 2 of 2 | 0 of 2 | 5.33 percent | 3.15 percent |

The deep dips are the gap-following epochs of section 5 (the data-start epoch excluded). Their preceding gaps are 0.715 to 0.981 d.

### Measurements with the mask

All on gaia-dr3-2053482589315365120 with the script of the Method section, the 0.5 to 30 d window, and the studied peak pinned to the section 1 ephemeris (24.82263 d, t0 121.08495, 9 h) so the injected box at 23.55 d lands on the same epochs as in section 7. Run with `--edge-mask-days 0`, the same command reproduces section 7 exactly (SDE 6.52 and 6.22, and 24.60 with the post-hoc mask).

| Flux | Points searched | Top signal (d) | Top SDE | SDE at 24.82263 d | Band 22 median / MAD-sigma | Injected 1250 ppm, 9 h at 23.55 d: SDE 1x / 0.5x / 0.25x |
|---|---|---|---|---|---|---|
| Detrended, before the mask (section 7) | 57,550 | 24.82263 | 6.52 | 6.52 | 30.66 / 7.31 | 6.22 / 1.41 / 1.45 |
| Detrended, masked | 54,793 | 1.68816 | 9.36 | 1.05 | 11.33 / 2.28 | 25.64 / 11.18 / 3.22 |
| Pre-whitened, before the mask (section 7) | not recorded | not recorded | not recorded | 5.79 | 18.58 / 4.31 | 7.68 / 2.39 at 0.5x |
| Pre-whitened, masked | 54,867 | 0.84663 | 9.95 | 1.37 | 9.22 / 1.87 | 32.98 / 14.75 / 5.05 |

The 24.82 d peak is gone, and a genuine 1250 ppm, 9 h box now scores SDE 25.6 (33.0 after pre-whitening) instead of 6.2. That is slightly better than the post-hoc mask of section 7 (24.6), because the running median no longer sees the ramp. The masked top signals (1.688 d and its half, 0.848 d) are short-period variability that pre-whitening reduces, and both stay below `sde_min` (11). After the mask the first 9 h of a segment hold 1 window deeper than 1000 ppm out of 63 and none deeper than 5000 ppm. Before the mask the counts were 18 and 13 out of 65.

Pre-whitening components (`max_components=50`, `fap_max=1e-6`) without and with the mask: 48 and 49 on gaia-dr3-2053482589315365120, 47 and 32 on gaia-dr3-2053223345080135040, 49 and 49 on gaia-dr3-2053295363101998848. The ramps account for the near-cap fit on one of the three stars only. On the other two the components are stellar or instrumental power that the mask does not touch.

Finite detrended points lost (the mask, plus the few segments it leaves shorter than the 10 points the running median needs): 2939 of 57,935 (5.07 percent), 2172 of 40,625 (5.35 percent) and 2529 of 47,294 (5.35 percent).

Two side effects to carry forward. First, the mask shrinks every band's median and MAD, so short-period noise and variability peaks score higher: the top SDE on this star rose from 6.5 to 9.4 (9.95 pre-whitened). The `sde_min` = 11 null calibration (spec section 13) was made without the mask and should be repeated. Second, the mask turns every downlink gap into a running-median segment boundary (a 0.9 d gap plus the 1.0 d mask exceeds `gap_days`). The median filter pads a segment with copies of its first point (`mode="nearest"`), so the first day of a segment follows that one point. On white noise this is small (the gap-edge counts above). On a synthetic delta Scuti with a 9.9 d, 900 ppm transit, the whitened SDE over the clean SDE averages 0.79 over nine seeds with the mask against 0.86 without. With `mode="mirror"` the seed-12 case gives 35.7 against 36.2 for the clean curve, compared with 25.4 against 36.0 with `mode="nearest"`.

## Method

Script: `docs/superpowers/notes/scripts/long_period_sde.py`. It takes a cached KBonus FITS path and prints every table above for that target. It sits outside `tests/`, so pytest does not collect it. The note's numbers come from these four runs, each costing about 2 to 4 minutes:

```
.venv/Scripts/python.exe docs/superpowers/notes/scripts/long_period_sde.py <2053482589315365120 fits> --inject 23.55
.venv/Scripts/python.exe docs/superpowers/notes/scripts/long_period_sde.py <2053223345080135040 fits> --inject 21.75
.venv/Scripts/python.exe docs/superpowers/notes/scripts/long_period_sde.py <2053295363101998848 fits> --period 28.50954 --inject 27.0
.venv/Scripts/python.exe docs/superpowers/notes/scripts/long_period_sde.py <2053482589315365120 fits> --prewhiten --period 24.82263 --inject 23.55 --inject-depth 1250
```

The FITS files are the cached blobs in `.superpowers/sample-run/blobs` (36/36f003de..., e9/e94b7070... and 67/67e3e098...).

Sections 1 to 7 used the detrend of before the gap-edge mask: add `--edge-mask-days 0` to the four commands above to reproduce them. Section 8 comes from two runs on the 24.82 d star with the studied peak pinned to the section 1 ephemeris; the first again with `--edge-mask-days 0` is the reproduction check:

```
.venv/Scripts/python.exe docs/superpowers/notes/scripts/long_period_sde.py <2053482589315365120 fits> --period 24.82263 --t0 121.08495 --duration 9 --inject 23.55 --inject-depth 1250
.venv/Scripts/python.exe docs/superpowers/notes/scripts/long_period_sde.py <2053482589315365120 fits> --period 24.82263 --t0 121.08495 --duration 9 --inject 23.55 --inject-depth 1250 --prewhiten
```

The gap census, the component counts and the masked fractions come from short scripts calling `detrend` and `prewhiten` directly on the three files.

| Step | Definition |
|---|---|
| Flux | `parse_kbonus_fits`, then `detrend(lc)`. With `--prewhiten`, `prewhiten(time, fnorm, max_components=50, fap_max=1e-6).residual`. Then `BoxDip.search`'s own preprocessing: the finite mask and the upward 5-MAD clip. Per-point sigma is 1.4826 MAD of the clipped flux |
| Search | `BoxDip(mask_iterations=0, alias_resolution=0)`, `Thresholds(min_period_days=0.5, max_period_days=30.0)`. `_coarse_search` builds 24 log bands from 0.5 to min(30, baseline/3) d; `_rank` refines the 40 best by SDE; the top three follow the 2 percent separation |
| Grid and durations | Per band, the coarse grid of `_coarse_search`: frequency step = shortest band duration / (1.5 x baseline x longest band period). Durations come from `_band_durations` (1 to 12 h, 0.3 to 3 times the circular-orbit duration, below 0.9 times the shortest band period). Band 22 searches 1, 1.5, 2, 3, 4, 6 and 9 h; band 23 searches 1.5 to 9 h. 9 h is the longest trial in both bands: 12 h exceeds 3 times the circular-orbit duration (3.3 h at 23 d, 3.5 h at 28 d) |
| Band statistics | Median and 1.4826 MAD of the band's coarse powers, as in `_coarse_search`. Peak SDE uses the refined peak power |
| Comb | P/k for k = 1 to 5 (and 1 to 20 as a sensitivity) for each of `SYSTEMATIC_PERIODS`; trials within 1 percent of any comb period are dropped from the band's median and MAD, and the peak's SDE is recomputed |
| Gaps and masking | A segment starts at the first point after a time step longer than 0.5 d, and at the first point of the data. Masking drops every point within 1.0 d after a segment start |
| Injection | A box of the peak's duration (9 h), subtracted additively from the searched flux after the upward clip, and after pre-whitening when `--prewhiten` is set. Its depth is the peak's BLS depth (or `--inject-depth`) times 1, 0.5 and 0.25. Its t0 is the peak t0 + 0.37 x the peak period, a fixed offset chosen to keep the injected events away from the peak's own events. The injected period sits between the band's strongest separated peaks (23.55, 21.75 and 27.0 d). In the masked rows the mask is applied first and the box is injected into the remaining points |
| SDE of the injected box | Single-band BLS on the band's coarse grid and durations. The injected curve gets its own band median and 1.4826 MAD. The score is the maximum power within 0.1 percent of the injected period, minus that median, over that MAD-sigma. The original peak in the same table is read the same way |
| Per-epoch and fold | Epoch = round((t - t0) / P). The window is \|phase\| < duration / 2. The fold uses 100 equal phase bins |
