# Long-period SDE normalisation study (17 October 2026)

## Question

Three targets in the sample sweep had first-pass peaks at 22 to 29 d with `snr` 30 to 78 but SDE 6.5 to 7.5, all at the 9 h duration cap. Either the per-band median/MAD normalisation is inflated at long periods by a comb of systematics (the 93 d quarter and its harmonics, `SYSTEMATIC_PERIODS = (93.0, 46.5, 372.5, 186.25)` in rung 1), which would suppress genuine long-period dips, or the peaks are themselves systematics.

## Ruling

Keep the per-band normalisation. Excluding the systematic comb moves the 24.82 d SDE from 6.520 to 6.531. The three peaks are systematics. Each one is a period that lines up two to six deep dips (0.4 to 3.2 percent) sitting in the first day after a data gap. Those dips are thermal ramps after a downlink or safe mode, and the 2 d running median in `detrend` leaves them in the flux. The band median is high (snr 30.7 at 21 to 25 d against 4.2 at 0.5 to 0.6 d) because those same dips raise the box snr at every long trial period, not because of the quarter comb. The normalisation reports that noise level correctly.

The defect is in the light-curve preparation, not in the normalisation. Masking the first 1.0 d after every gap longer than 0.5 d (5.3 to 5.6 percent of the points) removes all three peaks. It also lifts an injected 1250 ppm, 9 h box at 23.55 d from SDE 6.2 to 24.6 on this star. Section 7 proposes that as a separate task. No detector code changes here.

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

The comb is P/k for k = 1 to 5 for each of the four systematic periods. A trial is excluded when it lies within 1 percent of a comb period. Only 23.25 d (93/4, equal to 46.5/2) falls in band 22. Nothing below 30 d reaches band 23, because the shortest comb period above band 22 is 93/3 = 31 d.

| Target (band) | Comb periods in band | Trials before | Excluded | Trials after | Median before / after | MAD-sigma before / after | SDE before / after |
|---|---|---|---|---|---|---|---|
| 24.82 d (22) | 23.25 | 9847 | 1152 | 8695 | 30.665 / 30.234 | 7.314 / 7.369 | 6.520 / 6.531 |
| 22.67 d (22) | 23.25 | 9847 | 1152 | 8695 | 23.472 / 23.369 | 3.134 / 3.127 | 7.488 / 7.539 |
| 28.51 d (23) | none | 6565 | 0 | 6565 | 15.469 / 15.469 | 2.408 / 2.408 | 7.126 / 7.126 |

The excluded trials around 23.25 d have a median power of 32.97, close to the band median. The comb does not inflate the band statistics. The 23.252 d peak (power 52.63) is the one real comb feature in band 22, and it sits well below the 24.82 d peak.

As a sensitivity check I widened the comb to k = 1 to 20. That adds 21.912, 23.281 and 24.833 d to band 22, and 26.607 and 28.654 d to band 23. The SDE becomes 6.87 (24.82 d), 7.27 (22.67 d) and 7.70 (28.51 d). The 24.82 d peak lies 0.04 percent from 372.5/15, so at that order its own trials leave the statistics. None of the three reaches the sweep threshold of 9, let alone the current `sde_min` of 11.

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

57 epochs have points inside the 9 h window. Depth is 1 minus the window mean, and the error is the white scatter over the root of the window count.

| Epoch | Mid-time (BKJD) | Quarter | Points | Depth (ppm) | White error (ppm) | Share of the stacked depth | Days after the nearest gap |
|---|---|---|---|---|---|---|---|
| 38 | 1064.345 | 11 | 18 | 20648 | 113 | 0.298 | 0.18 |
| 49 | 1337.394 | 14 | 15 | 19332 | 124 | 0.233 | 0.17 |
| 53 | 1436.684 | 15 | 17 | 14210 | 117 | 0.194 | 0.43 |
| 18 | 567.892 | 6 | 18 | 6651 | 113 | 0.096 | 0.49 |
| 0 | 121.085 | 0 | 18 | 3157 | 113 | 0.046 | 0.55 |
| 23 | 692.005 | 7 | 17 | 3103 | 117 | 0.042 | 0.86 |
| 5 | 245.198 | 2 | 18 | 2034 | 113 | 0.029 | 5.55 |

| Stacked depth | Value (ppm) | White snr |
|---|---|---|
| All 57 epochs (point-weighted) | 1349 | 78 |
| Without the deepest epoch | 965 | 60 |
| Without the deepest 2 | 656 | 41 |
| Without the deepest 3 | 392 | 24 |
| Without the deepest 5 | 198 | 12 |
| Median single-epoch depth (`event_depths`, linear local baseline) | 84 | |

The signal is not one event, and it is not a repeating transit either. The three deepest epochs carry 72 percent of the stacked depth. Their depths run from 14,000 to 21,000 ppm, while 49 of the other 54 epochs sit within 750 ppm of zero (43 within 500 ppm). A transit at 24.82 d deep enough to give 2 percent in epoch 38 would show in every epoch. `event_depths` gives a chi-square of 657 for 54 degrees of freedom about its weighted mean.

All six epochs deeper than 3000 ppm sit 0.17 to 0.86 d after the start of a segment, meaning the first data after a gap longer than 0.5 d. The 24.82 d period is one of many trial periods that line up several of these segment starts.

### Gap-edge dips across the curve

| 9 h window | Windows | Median depth (ppm) | Deeper than 1000 ppm | Deeper than 5000 ppm |
|---|---|---|---|---|
| First 9 h of a segment | 65 | 30 | 18 (27.7 percent) | 13 (20.0 percent) |
| Last 9 h of a segment | 65 | -12 | 0 | 0 |
| Interior | 3308 | 0 | 109 (3.3 percent) | 12 (0.36 percent) |

A segment start is 55 times more likely than an interior window to hold a dip deeper than 5000 ppm. Segment ends show no such dips. That asymmetry is the signature of a thermal ramp after a downlink or safe mode. The 2 d running median follows the stable level, not the first few hours of a ramp, so `detrend` leaves the ramp in the flux. For this target KBonus `FLUX` is an exact multiple of `SAP_FLUX`. For gaia-dr3-2053223345080135040 the two columns differ, and both show the dips (12,105, 24,112 and 27,844 ppm in the detrended SAP flux at its three deepest epochs). The dips are instrumental, not an artefact of the PSF fit.

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

Search band only, at the band's own trial grid. "Masked" drops the first 1.0 d after every gap longer than 0.5 d. The injected box has the peak's duration and 1, 0.5 and 0.25 times its depth, at a period clear of the band's top peaks.

| Target | Flux | Points masked | Band median | Band MAD-sigma | Original peak SDE | Injected P (d) | SDE at 1x depth | SDE at 0.5x | SDE at 0.25x |
|---|---|---|---|---|---|---|---|---|---|
| 24.82 d (1250 ppm) | as searched | 0 | 30.66 | 7.31 | 6.52 | 23.55 | 6.22 | 1.41 | 1.45 |
| 24.82 d (1250 ppm) | masked | 3040 (5.3 percent) | 11.07 | 2.29 | 1.18 | 23.55 | 24.59 | 10.69 | 3.09 |
| 22.67 d (1743 ppm) | as searched | 0 | 23.47 | 3.13 | 7.44 | 21.75 | 6.52 | -0.13 | -0.08 |
| 22.67 d (1743 ppm) | masked | 2211 (5.5 percent) | 6.42 | 1.07 | 3.05 | 21.75 | 33.82 | 14.63 | 3.98 |
| 28.51 d (608 ppm) | as searched | 0 | 15.47 | 2.41 | 7.13 | 27.00 | 5.92 | -0.30 | -0.25 |
| 28.51 d (608 ppm) | masked | 2588 (5.6 percent) | 8.37 | 1.51 | 0.82 | 27.00 | 14.74 | 4.59 | 1.24 |

Two results follow. Masking removes all three peaks: their SDE falls to 0.8 to 3.1. The same dips also cost real sensitivity. On the flux as searched, a genuine periodic box as deep as the 24.82 d peak scores SDE 6.2, below the systematic it competes with. At half that depth, BLS prefers to line up the gap-edge dips over the injected box, and the injected period gains no power at all. The band normalisation is not the cause. Its median and MAD reflect the dips. Once the dips are masked, the band median falls by a factor of 1.8 to 3.7 and the MAD-sigma by 1.6 to 3.2.

The current pipeline pre-whitens before the search (47 to 49 sinusoids on these three stars). That lowers the three peaks to SDE 5.79, 3.68 and 2.56. It does not remove the dips:

| 24.82 d curve, pre-whitened | Band median | Band MAD-sigma | Peak SDE | Injected 1250 ppm SDE | Injected 625 ppm SDE |
|---|---|---|---|---|---|
| As searched | 18.58 | 4.31 | 5.79 | 7.67 | 2.39 |
| First 1.0 d after gaps masked | 8.64 | 1.58 | 1.99 | 24.74 | 10.14 |

Proposed follow-up, as a separate task because it changes the flux every rung sees: drop or down-weight the first 1.0 d after every gap longer than 0.5 d before the search, or repair the segment edges in `detrend`. Accept it on injection-recovery across the null set. It must not lower the 13.769 d synthetic recovery, and it must lift long-period recovery at a fixed SDE threshold. A real transit that falls in the first day of a segment would be lost, which is about 5 percent of the data.

## Method

Scratch scripts, run with the project venv on the cached KBonus files in `.superpowers/sample-run/blobs`:

1. Stage 1: parse, detrend, optionally pre-whiten, apply `search()`'s finite mask and upward 5-MAD clip, build `BoxLeastSquares(t, y, dy=sigma)`, run `_coarse_search` and `_rank`, and keep the top 3 by SDE after the 2 percent separation. About 95 to 115 s per curve.
2. Stage 2: band statistics, the comb exclusion with k = 1 to 5 and 1 to 20, the 100-bin fold, `event_depths`, red noise from 9 h bins and 91 control phases.
3. Per-epoch window depths with distance to the nearest gap; segment-start against segment-end against interior 9 h windows.
4. Band-only BLS on the band's own grid for the masking and injection table.
