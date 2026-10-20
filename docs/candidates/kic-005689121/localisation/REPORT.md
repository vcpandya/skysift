# KIC 5689121: pixel-level localisation of the P = 13.7695 d signal

Date: 2026-10-20. Scripts, data and outputs are all in this folder. Every number below comes from `results.json`. The method is the one used for KIC 6146676 (`docs/candidates/kic-006146676/localisation/`); `diffimage.py` and `make_figure.py` are copies with the ids, ephemeris, star positions and flux ratio changed.

## Question

The SkySift engine found a transit-like signal on the KBonus PSF light curve of KIC 5689121 (Gaia DR3 2103792461826460288, G 16.28, Kp 16.33): P = 13.769509 d, t0 = 136.31664 BKJD, duration 2.0 h, box depth 1983 ppm (rung-2 fit 2124 ppm, k = 0.046, b = 0.86). The light curve was extracted from the target-pixel files of KIC 5689116 (Gaia DR3 2103792427466721792, Kp 15.48, G 15.48, a G star), which lies 7.10 arcsec = 1.79 Kepler pixels away and is 2.2 times brighter in Kp. Do the pixels dim at the candidate or at the Kepler target?

This is a different geometry from KIC 6146676, where the target was 23 times brighter and 2.9 px away. Here the two stars are close to equal in brightness and sit within two pixels of each other, so the test is less about a faint star next to a bright core and more about separating two overlapping PSFs.

## Data

KIC 5689116 was observed in 13 long-cadence quarters: Q1 to Q4, Q6 to Q8, Q10 to Q12 and Q14 to Q16 (no Q0, Q5, Q9, Q13 or Q17; MAST lists 13 files and all 13 downloaded, `download_log.json`). The stamps are tiny: 4x5 pixels in seasons 0, 2 and 3 and 5x5 in season 1. The target sits near the stamp centre (row 1.5 to 2.1). The candidate sits on the bottom edge of every stamp, at row 0.07 to 0.17 in seasons 0 and 2, row 0.33 in Q1 and row 0.69 in season 1, so roughly half of its PSF falls outside the stamp in seasons 0 and 2. In the direct images the candidate is nonetheless a distinct bright pixel at the position the TPF WCS predicts (for example 2013 e/s in Q4 against 2684 e/s at the target's brightest pixel). The white corner pixels in the direct-image panels of the figure are near-zero or negative background-subtracted pixels below the log-scale floor, or (in the 5x5 stamps) the two invalid corner pixels; they are not bright sources.

All 13 quarters hold transits. 74 events had in-transit data; 68 passed the event cuts and 6 were rejected at quarter edges for lacking out-of-transit cadences on one side (Q3: 2, Q4, Q7, Q11, Q14: 1 each). With a 2.0 h duration each event has 3 to 5 in-transit cadences and 12 to 17 out-of-transit cadences.

Season 3 is Q1 alone (2 events), because Q5, Q9 and Q13 do not exist for this target. Its numbers are weak and are reported for completeness.

## Method

Difference imaging in the style of Bryson et al. (2013), unchanged from the KIC 6146676 run except for the inputs.

1. Cadences with QUALITY == 0 and finite flux in all valid pixels. A pixel is valid if it is finite in at least 90% of the cadences of the quarter's best-covered pixel.
2. In transit: |t - T_mid| < 0.0417 d (half the duration). Out of transit: 0.083 to 0.25 d from mid-transit, on both sides, per event.
3. An event is used if it has at least 3 in-transit cadences, at least 10 out-of-transit cadences, and at least 3 on each side.
4. Per event: difference = mean(out) - mean(in) per pixel, variance s_out^2 (1/n_in + 1/n_out). Events are stacked per quarter by inverse variance. The direct image is the mean of the out-of-transit cadences.
5. Star positions come from the APERTURE-extension WCS at the Gaia DR3 coordinates, no proper-motion correction.
6. **Specified centroid**: flux-weighted centroid of the positive pixels in the 3x3 box around the brightest difference pixel; uncertainty from 200 event bootstraps and 200 noise Monte Carlo draws, the larger adopted.
7. **Supplementary checks**: (a) the same centroid boxed on the highest-S/N difference pixel; (b) difference flux summed in the 3x3 box around each star as a fractional depth; (c) a fixed-position two-star fit with Gaussian PSFs at the Gaia/WCS positions (direct image fitted for amplitudes, common shift, width and constant; difference image fitted with the same PSFs and free amplitudes; implied depth = difference amplitude / direct amplitude).
8. Season stacks on the CCD pixel grid: season 0 = Q2/6/10/14, season 1 = Q3/7/11/15, season 2 = Q4/8/12/16, season 3 = Q1.

Two consequences of the geometry:

- The 3x3 boxes around the two stars overlap (centres 1.79 px apart), so the "target box" fractional depth includes the candidate's wings and cannot be read as the target's own depth. The two-star fit is the test that separates the stars.
- The brightest-pixel estimator, which was bimodal for KIC 6146676, is well behaved here, because the target core is not bright enough to produce noise excursions that beat the candidate's real signal. The brightest difference pixel and the highest-S/N pixel coincide in 11 of 13 quarters and both are the candidate's pixel in all 13. The S/N-box centroid is still reported and is the estimator quoted in the verdict, for consistency with the other star.

WCS sanity check: the direct-image centroid falls 0.14 to 0.58 px from the target's predicted position (median 0.18 px). The larger values are in seasons 0 and 3, where the centroid is pulled toward the candidate, which holds about 30% of the light in the stamp. The two-star fit absorbs any residual WCS error through its common shift. The fitted direct-flux ratio target/candidate is 2.11 to 2.56 (median 2.53), consistent with the Kp ratio of 2.20 once the candidate's edge truncation is allowed for.

## Per-quarter results

C = candidate KIC 5689121, T = target KIC 5689116. Distances are in Kepler pixels (1 px = 3.98 arcsec).

| Q | season | events used (in/out cadences) | brightest diff px: dist C / T [px], its S/N | highest-S/N diff px (S/N) | diff centroid (col,row) +/- 1 sigma [px] | centroid dist C / T [px] | dist C / T [arcsec] | nearer | S/N-box centroid dist C / T [px] | 3x3 fractional depth C / T [ppm] | two-star fit depth C / T [ppm] |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 3 | 2 (4/16, 4/15) | 0.49 / 2.28, 2.9 | candidate pixel, S/N 2.9 | (1.10, 0.35) +/- (0.62, 0.32) [bootstrap] | 0.26 / 1.95 | 1.0 / 7.8 | candidate | 0.26 / 1.95 | 1322 +/- 432 / 377 +/- 278 | 1813 +/- 465 / 182 +/- 240 |
| 2 | 0 | 6 (4/14, 4/13, 4/13, 4/16, 4/16, 5/12) | 0.19 / 1.98, 6.6 | candidate pixel, S/N 6.6 | (1.23, 0.14) +/- (0.09, 0.07) [noise-MC] | 0.10 / 1.73 | 0.4 / 6.9 | candidate | 0.10 / 1.73 | 840 +/- 132 / 187 +/- 129 | 2538 +/- 313 / 136 +/- 125 |
| 3 | 1 | 5 (3/14, 3/15, 4/15, 3/16, 4/14) | 0.51 / 1.30, 5.0 | candidate pixel, S/N 5.1 | (1.73, 0.79) +/- (0.12, 0.12) [noise-MC] | 0.17 / 1.63 | 0.7 / 6.5 | candidate | 0.13 / 1.71 | 661 +/- 109 / 277 +/- 114 | 2274 +/- 265 / 70 +/- 98 |
| 4 | 2 | 4 (4/17, 5/13, 4/14, 4/15) | 0.26 / 2.03, 11.1 | candidate pixel, S/N 11.1 | (1.12, 0.09) +/- (0.09, 0.07) [noise-MC] | 0.12 / 1.88 | 0.5 / 7.5 | candidate | 0.12 / 1.88 | 1258 +/- 182 / 50 +/- 183 | 2304 +/- 191 / -110 +/- 185 |
| 6 | 0 | 6 (5/16, 4/15, 3/16, 3/16, 4/15, 4/16) | 0.18 / 1.95, 8.3 | candidate pixel, S/N 8.3 | (1.31, 0.24) +/- (0.12, 0.06) [bootstrap] | 0.23 / 1.57 | 0.9 / 6.2 | candidate | 0.23 / 1.57 | 1072 +/- 128 / 722 +/- 110 | 2669 +/- 276 / 250 +/- 126 |
| 7 | 1 | 6 (4/15, 4/15, 5/15, 3/16, 4/15, 4/16) | 0.50 / 1.31, 5.9 | candidate pixel, S/N 5.9 | (1.83, 0.94) +/- (0.14, 0.09) [bootstrap] | 0.33 / 1.46 | 1.3 / 5.8 | candidate | 0.33 / 1.46 | 609 +/- 107 / 328 +/- 108 | 1657 +/- 245 / 63 +/- 106 |
| 8 | 2 | 5 (4/15, 4/15, 4/15, 3/15, 4/17) | 0.27 / 2.04, 10.8 | candidate pixel, S/N 10.8 | (1.12, 0.15) +/- (0.09, 0.08) [noise-MC] | 0.12 / 1.84 | 0.5 / 7.3 | candidate | 0.12 / 1.84 | 1380 +/- 183 / 280 +/- 180 | 2373 +/- 217 / 152 +/- 187 |
| 10 | 0 | 7 (4/14, 4/15, 3/16, 4/14, 5/16, 4/17, 3/14) | 0.17 / 1.95, 4.2 | candidate pixel, S/N 4.2 | (1.21, 0.18) +/- (0.30, 0.10) [noise-MC] | 0.12 / 1.68 | 0.5 / 6.7 | candidate | 0.12 / 1.68 | 647 +/- 119 / 508 +/- 101 | 1522 +/- 269 / 194 +/- 112 |
| 11 | 1 | 5 (4/15, 4/16, 4/14, 4/16, 5/16) | 0.49 / 1.32, 6.3 | candidate pixel, S/N 6.9 | (1.79, 0.77) +/- (0.19, 0.19) [bootstrap] | 0.19 / 1.62 | 0.8 / 6.5 | candidate | 0.09 / 1.78 | 828 +/- 103 / 321 +/- 106 | 2352 +/- 216 / 118 +/- 102 |
| 12 | 2 | 6 (4/16, 3/16, 4/16, 4/16, 4/16, 4/16) | 0.34 / 2.11, 4.5 | candidate pixel, S/N 4.5 | (1.12, 0.20) +/- (0.23, 0.14) [noise-MC] | 0.18 / 1.88 | 0.7 / 7.5 | candidate | 0.18 / 1.88 | 1106 +/- 234 / 11 +/- 192 | 1622 +/- 314 / -120 +/- 199 |
| 14 | 0 | 5 (4/16, 4/16, 4/16, 4/15, 4/16) | 0.16 / 1.94, 3.6 | candidate pixel, S/N 4.1 | (1.35, 0.00) +/- (0.30, 0.20) [bootstrap] | 0.21 / 1.73 | 0.8 / 6.9 | candidate | 0.41 / 1.59 | 419 +/- 140 / 235 +/- 117 | 1389 +/- 293 / -32 +/- 134 |
| 15 | 1 | 6 (5/16, 4/15, 4/17, 4/16, 4/15, 3/16) | 0.50 / 1.31, 6.4 | candidate pixel, S/N 6.4 | (1.66, 0.70) +/- (0.09, 0.12) [noise-MC] | 0.05 / 1.75 | 0.2 / 7.0 | candidate | 0.05 / 1.75 | 747 +/- 111 / 342 +/- 106 | 2355 +/- 265 / 29 +/- 97 |
| 16 | 2 | 5 (5/15, 3/17, 4/16, 4/15, 4/14) | 0.35 / 2.11, 8.2 | candidate pixel, S/N 8.2 | (1.17, 0.27) +/- (0.10, 0.08) [noise-MC] | 0.19 / 1.80 | 0.7 / 7.2 | candidate | 0.19 / 1.80 | 1458 +/- 186 / 118 +/- 156 | 2087 +/- 233 / -35 +/- 158 |

| season | quarters | events | valid px | diff centroid dist C / T [px] (+/- 1 sigma) | dist C / T [arcsec] | bootstrap draws nearer C | highest-S/N diff px | S/N-box centroid dist C / T [px] (+/- noise MC) | 3x3 fractional depth C / T [ppm] | two-star fit depth C / T [ppm] | fitted direct flux ratio T/C | chi2 C-only / T-only / both (pixels) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 0 | Q2, Q6, Q10, Q14 | 24 | 20 | 0.14 +/- 0.07 / 1.67 +/- 0.06 [bootstrap] | 0.6 / 6.6 | 1.00 | candidate pixel, S/N 11.2 | 0.14 +/- 0.08 / 1.67 | 750 +/- 64 / 540 +/- 55 | 2002 +/- 143 / 147 +/- 62 | 2.54 | 48.2 / 238.5 / 42.6 (20) |
| 1 | Q3, Q7, Q11, Q15 | 22 | 23 | 0.15 +/- 0.07 / 1.64 +/- 0.07 [bootstrap] | 0.6 / 6.5 | 1.00 | candidate pixel, S/N 11.8 | 0.15 +/- 0.06 / 1.64 | 709 +/- 53 / 312 +/- 53 | 2161 +/- 123 / 68 +/- 50 | 2.53 | 53.5 / 362.6 / 51.7 (23) |
| 2 | Q4, Q8, Q12, Q16 | 20 | 20 | 0.13 +/- 0.04 / 1.84 +/- 0.06 [noise-MC] | 0.5 / 7.3 | 1.00 | candidate pixel, S/N 18.0 | 0.13 +/- 0.05 / 1.84 | 1330 +/- 94 / 119 +/- 86 | 2190 +/- 114 / -28 +/- 89 | 2.15 | 43.5 / 409.7 / 43.4 (20) |
| 3 | Q1 | 2 | 20 | 0.26 +/- 0.65 / 1.95 +/- 0.51 [bootstrap] | 1.0 / 7.8 | 0.70 | candidate pixel, S/N 2.9 | 0.26 +/- 0.62 / 1.95 | 1322 +/- 432 / 377 +/- 278 | 1813 +/- 465 / 182 +/- 240 | 2.33 | 22.0 / 36.6 / 21.4 (20) |

## Summary

- **Specified centroid**: nearer the candidate in **13 of 13** quarters. Median distance 0.18 px (0.7 arcsec) to the candidate and 1.73 px (6.9 arcsec) to the target. Unlike the KIC 6146676 case, this estimator is not noise-driven here: the brightest difference pixel is the candidate's pixel in every quarter, with S/N 2.9 (Q1) to 11.1.
- **Highest-S/N difference pixel**: the candidate's pixel in **13 of 13** quarters (S/N 2.9 to 11.1; 4.1 to 11.1 outside Q1). The S/N-box centroid is nearer the candidate in 13 of 13 quarters, median 0.13 px (0.5 arcsec) from the candidate and 1.75 px (7.0 arcsec) from the target.
- **Season stacks**: the highest-S/N pixel is the candidate's in all 4 seasons, at S/N 11.2, 11.8 and 18.0 in the three full seasons (2.9 in the single-quarter season 3). The centroid lies 0.13 to 0.15 px (0.5 to 0.6 arcsec) from the candidate and 1.64 to 1.84 px (6.5 to 7.3 arcsec) from the target, with 1-sigma 0.04 to 0.07 px. 100% of bootstrap draws land nearer the candidate in seasons 0 to 2 (70% in season 3).
- **Multi-quarter mean offset** (inverse-variance mean of the per-quarter sky offsets, the Kepler DV statistic): 0.33 +/- 0.14 arcsec from the candidate (3.4 sigma, see caveats) and 6.82 +/- 0.14 arcsec from the target (53 sigma).
- **Fractional depth in the 3x3 boxes**: candidate box 710 to 1330 ppm per season, target box 120 to 540 ppm. The candidate box exceeds the target box by more than 3 sigma in 6 of 13 single quarters and in seasons 1 and 2 (5.3 and 9.5 sigma); in season 0 the margin is 2.5 sigma. Both numbers are diluted: the candidate box is half off the stamp and the target box contains the candidate's wings, so this check is weaker here than the fit.
- **Two-star fit**: weighted mean over 13 quarters gives **2124 +/- 71 ppm on the candidate** and **82 +/- 35 ppm on the target**. The light-curve box depth is 1983 ppm and the rung-2 fitted depth 2124 ppm. For the target to be the source it would need at least 1983 / 2.2 = 901 ppm (784 ppm with the fitted flux ratio 2.53), which the fit excludes at 20 to 23 sigma.
  - In every full season the candidate-only fit matches the two-star chi2 (48.2 vs 42.6, 53.5 vs 51.7, 43.5 vs 43.4) and the target-only fit is worse by delta chi2 = 196, 311 and 366. In season 3 (Q1 only) the target-only fit is worse by 15.
  - The small positive target amplitude (2.3 sigma in the weighted mean) is consistent with the 110 +/- 33 ppm seen in the target's own PDCSAP light curve (`../context/REPORT.md`) and with Gaussian-PSF mismatch assigning some of the candidate's wing to the target. It is an order of magnitude below what an on-target source needs.

## Verdict

The in-transit flux loss sits on KIC 5689121, not on KIC 5689116. The evidence:

- In every quarter and every season the strongest difference signal falls on the candidate's pixel, and all centroid estimators land within 0.3 px (about 1 arcsec) of the candidate's Gaia position and 1.5 to 2.0 px (6 to 8 arcsec) from the target.
- The depth measured at the candidate's position (2124 +/- 71 ppm) matches the light-curve depth.
- The target shows at most about 150 ppm, an order of magnitude below the 900 ppm it would need as the source.

The difference images are not too noisy for a verdict: the candidate's pixel reaches S/N 4 to 11 per quarter and 11 to 18 per full season stack.

## Caveats

- The candidate sits on the bottom edge of every stamp (row 0.07 to 0.70), so roughly half of its PSF is missing in seasons 0, 2 and 3. Centroids are pulled toward the stamp interior (the 0.33 arcsec multi-quarter offset from the candidate is in that direction and is an edge effect, not evidence of a displaced source), box sums under-count its flux, and the Gaussian fit must extrapolate the truncated profile. The candidate-vs-target contrast does not depend on this.
- Kepler pixels are 4 arcsec. This test cannot exclude a fainter star within about 1 px (4 arcsec) of the candidate, or blended with it, as the true host. The Gaia DR3 neighbour check is in `../context/REPORT.md`; the nearest Gaia source other than the target is 14.7 arcsec away.
- With only 1.79 px separation the 3x3 boxes overlap, so the box fractional depths are not independent measures of each star; only the two-star fit separates them, and that fit uses a Gaussian PSF (sigma 0.53 to 0.59 px), not the Kepler PRF.
- Season 3 is a single quarter (Q1) with 2 events; its bootstrap is not meaningful.
- Stamps are 4x5 and 5x5 pixels, so the fits have 20 to 23 pixels and a constant background term; there is no room to test for a third source.

## Files

- `download_tpfs.py`: lists the MAST directory and downloads the 13 TPFs into `tpf/`, writing `download_log.json`.
- `inspect_tpfs.py`: prints stamp shapes and star pixel positions.
- `diffimage.py`: the analysis. Writes `results.json`, `diffimage_products.npz` and (via the command below) `run_log.txt`.
- `make_figure.py`: makes `centroids.png` and `centroids.svg`. Rows are seasons; each row has the season's quarters plus the season stack, direct image beside difference image. Bottom row: centroids on the sky, and the per-quarter depth from the two-star fit.
- `tables.txt`: the two tables above as generated from `results.json`.

## Reproduce

```powershell
$env:PYTHONUTF8 = "1"; $env:PYTHONPATH = "C:\Research\skysift"
cd C:\Research\skysift\.superpowers\dossier\kic5689121\localisation
C:\Research\skysift\.venv\Scripts\python.exe download_tpfs.py
C:\Research\skysift\.venv\Scripts\python.exe -I diffimage.py tpf . > run_log.txt
C:\Research\skysift\.venv\Scripts\python.exe -I make_figure.py tpf .
```

`diffimage.py` uses a fixed random seed (20261020), so reruns give the same numbers.
