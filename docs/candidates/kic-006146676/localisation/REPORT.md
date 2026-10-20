# KIC 6146676: pixel-level localisation of the P = 40.291 d signal

Date: 2026-10-20. Scripts, data and outputs are all in this folder. Every number below comes from `results.json`.

## Question

The SkySift engine found a transit-like signal on the KBonus PSF light curve of KIC 6146676 (Gaia DR3 2073896294004902272, G 16.24): P = 40.29109 d, t0 = 135.788 BKJD, duration 3.0 h, depth 2165 ppm. The light curve was extracted from the target-pixel files of KIC 6146666 (Kp 12.85, 2.90 Kepler pixels = 11.5 arcsec away, about 23 times brighter). Do the pixels dim at the candidate or at the bright target?

## Data

All 18 long-cadence TPFs of KIC 6146666 (Q0 to Q17) were downloaded from MAST into `tpf/` (none failed; `download_log.json`). The stamps are small (5x7 to 8x10 pixels) and the candidate sits in the first one or two columns of every stamp, about 2.9 px from the target. The candidate shows up as a distinct bright pixel in the direct images at the position the TPF WCS predicts. Q0 and Q17 contain no transit, so 16 quarters and 35 events are usable. All 35 events with in-transit data passed the event cuts.

## Method

Difference imaging in the style of Bryson et al. (2013).

1. Cadences with QUALITY == 0 and finite flux in all valid pixels. A pixel is valid if it is finite in at least 90% of the cadences of the quarter's best-covered pixel.
2. In transit: |t - T_mid| < 0.0625 d (half the duration). Out of transit: 0.125 to 0.375 d from mid-transit, on both sides, per event.
3. An event is used if it has at least 3 in-transit cadences, at least 10 out-of-transit cadences, and at least 3 out-of-transit cadences on each side. The each-side condition is my addition; it removed no events.
4. Per event: difference = mean(out) - mean(in) per pixel. The variance is s_out^2 (1/n_in + 1/n_out), with s_out the per-pixel scatter of the out-of-transit cadences. Events are stacked per quarter by inverse variance. The direct image is the mean of the out-of-transit cadences.
5. Star positions come from the APERTURE-extension WCS, with Gaia DR3 coordinates and no proper-motion correction.
6. **Specified centroid**: flux-weighted centroid of the positive pixels in the 3x3 box around the brightest difference pixel. Its uncertainty comes from 200 event bootstraps and from 200 Monte Carlo draws of the noise image. The adopted 1-sigma is the larger of the two; bootstrap alone is degenerate with 1 to 3 events per quarter.
7. **Supplementary checks**, added because the specified centroid turned out to be noise-driven (see verdict):
   - (a) The same 3x3 centroid, boxed on the highest-S/N difference pixel ("S/N-box").
   - (b) Difference flux summed in the 3x3 box around each star, as a ratio and as a fractional depth (difference sum / direct sum).
   - (c) A fixed-position two-star fit. The direct image is fitted with two Gaussian PSFs at the Gaia/WCS positions, plus a common shift, width and constant. The difference image is then fitted with the same two PSF shapes and free amplitudes. Implied depth = difference amplitude / direct amplitude. This uses a Gaussian, not the Kepler PRF, so it is approximate. The fitted direct-flux ratio is 23.7 (median over quarters), which matches the quoted factor of 23.
8. Season stacks: quarters of the same season (same module/output and orientation) are stacked on the CCD pixel grid. The 4 seasons are Q2/6/10/14, Q3/7/11/15, Q4/8/12/16 and Q1/5/9/13. Q0 shares season 3 with Q1 but has no events. Grouping by q mod 4 gives the same groups for the usable quarters.

WCS sanity check: the direct-image centroid falls a median 0.14 px from the target's predicted position, and part of that offset is the pull of the candidate's own flux.

## Per-quarter results

C = candidate KIC 6146676, T = target KIC 6146666. Distances are in Kepler pixels (1 px = 3.98 arcsec).

| Q | season | events used (in/out cadences) | brightest diff px: dist C / T [px], its S/N | highest-S/N diff px | diff centroid (col,row) +/- 1 sigma [px] | centroid dist C / T [px] | dist C / T [arcsec] | nearer | S/N-box centroid dist C / T [px] | 3x3 diff-sum ratio C/T | 3x3 fractional depth C / T [ppm] | two-star fit depth C / T [ppm] |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 0 | 3 | 0 (no transit in quarter) | - | - | - | - | - | - | - | - | - | - |
| 1 | 3 | 1 (6/25) | 3.26 / 0.88, 0.7 | candidate's pixel, S/N 3.7 | (3.94, 3.03) +/- (0.77, 0.40) [noise-MC] | 3.20 / 0.88 | 12.7 / 3.5 | target | 0.12 / 2.78 | -1.09 | 600 +/- 281 / -54 +/- 211 | 2018 +/- 428 / -9 +/- 181 |
| 2 | 0 | 3 (6/24, 6/24, 4/22) | 2.49 / 0.76, 0.6 | candidate's pixel, S/N 6.4 | (2.88, 4.03) +/- (1.16, 0.26) [noise-MC] | 2.38 / 0.84 | 9.5 / 3.3 | target | 0.18 / 3.05 | -1.20 | 889 +/- 280 / -95 +/- 202 | 2698 +/- 349 / -112 +/- 159 |
| 3 | 1 | 2 (6/20, 6/23) | 3.26 / 0.39, 1.0 | candidate's pixel, S/N 6.7 | (3.95, 1.49) +/- (1.09, 0.62) [noise-MC] | 3.42 / 0.81 | 13.6 / 3.2 | target | 0.08 / 2.82 | -44.21 | 1229 +/- 216 / -2 +/- 81 | 2236 +/- 288 / -51 +/- 82 |
| 4 | 2 | 2 (5/23, 5/18) | 3.27 / 0.38, 1.2 | candidate's pixel, S/N 6.0 | (3.98, 1.94) +/- (1.29, 0.62) [bootstrap] | 3.27 / 0.39 | 13.0 / 1.6 | target | 0.23 / 2.84 | 2.24 | 965 +/- 213 / 44 +/- 146 | 1674 +/- 244 / 85 +/- 118 |
| 5 | 3 | 2 (6/22, 6/21) | 3.45 / 0.55, 0.6 | candidate's pixel, S/N 4.9 | (3.96, 2.03) +/- (1.00, 0.50) [noise-MC] | 3.40 / 0.51 | 13.5 / 2.0 | target | 0.16 / 2.74 | 1.24 | 645 +/- 196 / 55 +/- 171 | 2059 +/- 371 / 82 +/- 137 |
| 6 | 0 | 2 (7/20, 6/21) | 2.76 / 0.39, 0.4 | candidate's pixel, S/N 6.4 | (3.11, 2.87) +/- (1.13, 0.46) [noise-MC] | 2.91 / 0.45 | 11.6 / 1.8 | target | 0.24 / 3.11 | 4.24 | 1922 +/- 363 / 21 +/- 321 | 2777 +/- 436 / -9 +/- 169 |
| 7 | 1 | 2 (3/19, 5/24) | 0.12 / 2.80, 6.4 | candidate's pixel, S/N 6.4 | (1.02, 2.80) +/- (1.40, 0.59) [noise-MC] | 0.30 / 2.73 | 1.2 / 10.9 | candidate | 0.30 / 2.73 | -4.88 | 1471 +/- 262 / -26 +/- 115 | 2551 +/- 340 / -63 +/- 107 |
| 8 | 2 | 2 (4/15, 5/23) | 3.11 / 0.89, 0.9 | candidate's pixel, S/N 5.9 | (3.40, 2.79) +/- (0.76, 0.36) [noise-MC] | 2.52 / 0.67 | 10.0 / 2.7 | target | 0.24 / 2.70 | 6.28 | 1125 +/- 230 / 18 +/- 152 | 2044 +/- 306 / -38 +/- 147 |
| 9 | 3 | 3 (5/19, 6/24, 6/23) | 3.25 / 0.90, 2.3 | candidate's pixel, S/N 6.3 | (3.57, 3.08) +/- (0.82, 0.23) [noise-MC] | 2.81 / 0.85 | 11.2 / 3.4 | target | 0.14 / 2.82 | 3.49 | 727 +/- 160 / 22 +/- 130 | 2383 +/- 321 / 2 +/- 68 |
| 10 | 0 | 2 (6/24, 5/24) | 2.75 / 0.38, 0.4 | candidate's pixel, S/N 6.0 | (3.26, 2.90) +/- (1.24, 0.49) [bootstrap] | 3.03 / 0.38 | 12.1 / 1.5 | target | 0.16 / 3.04 | 4.12 | 2282 +/- 321 / 26 +/- 258 | 3150 +/- 400 / -61 +/- 120 |
| 11 | 1 | 2 (6/20, 4/18) | 3.27 / 0.41, 1.1 | candidate's pixel, S/N 8.1 | (4.06, 1.91) +/- (1.49, 0.55) [noise-MC] | 3.35 / 0.52 | 13.3 / 2.1 | target | 0.14 / 2.85 | 1.66 | 720 +/- 226 / 39 +/- 85 | 2280 +/- 277 / -64 +/- 73 |
| 12 | 2 | 2 (6/21, 6/21) | 0.09 / 2.84, 8.6 | candidate's pixel, S/N 8.6 | (1.06, 2.94) +/- (1.26, 0.51) [noise-MC] | 0.14 / 2.76 | 0.5 / 11.0 | candidate | 0.14 / 2.76 | -4.54 | 860 +/- 210 / -20 +/- 171 | 1924 +/- 208 / 15 +/- 165 |
| 13 | 3 | 3 (6/24, 5/21, 6/24) | 0.26 / 2.64, 7.4 | candidate's pixel, S/N 7.4 | (1.02, 2.92) +/- (1.29, 0.39) [bootstrap] | 0.30 / 2.60 | 1.2 / 10.4 | candidate | 0.30 / 2.60 | -2.58 | 1073 +/- 162 / -43 +/- 135 | 2605 +/- 313 / -71 +/- 77 |
| 14 | 0 | 2 (5/24, 5/23) | 2.52 / 0.76, 0.3 | candidate's pixel, S/N 4.5 | (3.10, 4.02) +/- (1.16, 0.78) [noise-MC] | 2.62 / 0.75 | 10.4 / 3.0 | target | 0.20 / 3.04 | -3.81 | 2312 +/- 384 / -28 +/- 356 | 2869 +/- 446 / 170 +/- 138 |
| 15 | 1 | 3 (4/21, 5/17, 4/18) | 0.12 / 2.80, 5.7 | candidate's pixel, S/N 5.7 | (0.97, 2.77) +/- (1.46, 0.25) [noise-MC] | 0.31 / 2.78 | 1.3 / 11.1 | candidate | 0.31 / 2.78 | -3.28 | 1050 +/- 196 / -28 +/- 86 | 2247 +/- 326 / 17 +/- 81 |
| 16 | 2 | 2 (6/23, 6/22) | 0.08 / 2.84, 4.4 | candidate's pixel, S/N 4.4 | (0.87, 2.95) +/- (1.70, 0.75) [bootstrap] | 0.06 / 2.95 | 0.2 / 11.8 | candidate | 0.06 / 2.95 | -1.68 | 1012 +/- 201 / -58 +/- 109 | 1699 +/- 281 / -47 +/- 74 |
| 17 | 3 | 0 (no transit in quarter) | - | - | - | - | - | - | - | - | - | - |

| season | quarters | events | diff centroid dist C / T [px] (+/- 1 sigma, bootstrap) | dist C / T [arcsec] | bootstrap draws nearer C | brightest diff px | S/N-box centroid dist C / T [px] (+/- noise MC) | 3x3 fractional depth C / T [ppm] | two-star fit depth C / T [ppm] | chi2 C-only / T-only / both (pixels) |
|---|---|---|---|---|---|---|---|---|---|---|
| 0 | Q2, Q6, Q10, Q14 | 9 | 0.20 +/- 1.23 / 3.05 +/- 1.41 | 0.8 / 12.2 | 0.56 | candidate's pixel, S/N 11.7 | 0.20 +/- 0.06 / 3.05 | 2058 +/- 160 / -32 +/- 132 | 2868 +/- 201 / 12 +/- 71 | 35.3 / 238.4 / 35.2 (43) |
| 1 | Q3, Q7, Q11, Q15 | 9 | 0.19 +/- 0.68 / 2.82 +/- 1.40 | 0.7 / 11.2 | 0.74 | candidate's pixel, S/N 13.6 | 0.19 +/- 0.07 / 2.82 | 1100 +/- 108 / -10 +/- 42 | 2321 +/- 152 / -37 +/- 42 | 49.0 / 280.1 / 48.3 (38) |
| 2 | Q4, Q8, Q12, Q16 | 8 | 0.10 +/- 0.96 / 2.83 +/- 1.30 | 0.4 / 11.3 | 0.84 | candidate's pixel, S/N 12.7 | 0.10 +/- 0.08 / 2.83 | 980 +/- 103 / -32 +/- 61 | 1837 +/- 126 / -17 +/- 55 | 44.4 / 257.8 / 44.3 (38) |
| 3 | Q1, Q5, Q9, Q13 | 9 | 0.16 +/- 1.42 / 2.76 +/- 1.38 | 0.6 / 11.0 | 0.47 | candidate's pixel, S/N 11.4 | 0.16 +/- 0.07 / 2.76 | 832 +/- 91 / 0 +/- 71 | 2323 +/- 175 / -20 +/- 46 | 45.6 / 221.8 / 45.4 (32) |

## Summary

- **Specified centroid**: nearer the candidate in 5 of 16 quarters (Q7, Q12, Q13, Q15, Q16). Median distance to the candidate is 2.72 px (10.8 arcsec); median distance to the target is 0.83 px (3.3 arcsec).
  - This estimator is unreliable here. In the 11 quarters where the brightest difference pixel lies at the target, that pixel's own S/N is only 0.3 to 2.3. It is noise, or a +/- dipole from small image motion, in the core of a star 23 times brighter. In all 5 quarters where the box lands on the candidate, the brightest pixel is also the highest-S/N pixel (S/N 4.4 to 8.6).
  - The `multi_quarter_mean_offset` in `results.json` is built from this bimodal estimator. It does not mean anything and should not be quoted.
- **Highest-S/N difference pixel**: the candidate's pixel in **16 of 16** quarters (S/N 3.7 to 8.6). The S/N-box centroid is nearer the candidate in 16 of 16 quarters. Median distances: 0.17 px (0.7 arcsec) to the candidate, 2.82 px (11.2 arcsec) to the target.
- **Season stacks**, one per detector orientation: the brightest difference pixel is the candidate's pixel in all 4 seasons, at S/N 11.4 to 13.6. The specified centroid lies 0.10 to 0.20 px (0.4 to 0.8 arcsec) from the candidate and 2.76 to 3.05 px (11.0 to 12.2 arcsec) from the target; it is nearer the candidate in all 4 seasons.
  - Its bootstrap 1-sigma is large (0.7 to 1.4 px), because some bootstrap draws still jump to the target's core: 47% to 84% of draws stay nearer the candidate. The noise-MC sigma of the S/N-box centroid is 0.06 to 0.08 px.
- **Fractional depth in the 3x3 boxes**: candidate box 830 to 2060 ppm per season, target box -32 to 0 ppm (+/- 42 to 132). Candidate exceeds target by more than 3 sigma in 12 of 16 single quarters.
  - The candidate-box value is below 2165 ppm because that box includes target wings and, in season 0, is half off the stamp.
  - The plain 3x3 difference-sum ratio C/T swings in sign (-44 to +631), because the target-box sum is consistent with zero. The ratio is ill-defined here; the fractional depths carry the same information in a usable form.
- **Two-star fit**: weighted mean over 16 quarters gives **2213 +/- 78 ppm on the candidate** and **-21 +/- 25 ppm on the target**. The light-curve depth is 2165 ppm. For the target to be the source it would need at least 2165/23 = 94 ppm, which this fit excludes at about 4.6 sigma.
  - In every season stack, the candidate-only fit matches the two-star chi2 (35.3 vs 35.2, 49.0 vs 48.3, 44.4 vs 44.3, 45.6 vs 45.4). The target-only fit is worse by delta chi2 = 176 to 232.

## Verdict

The in-transit flux loss sits on KIC 6146676, not on KIC 6146666. The evidence:

- In every quarter and every season the strongest difference signal falls on the candidate's pixel.
- The S/N-weighted centroids land within about 0.2 px (under 1 arcsec) of the candidate's Gaia position.
- The depth measured at the candidate's position matches the light-curve depth.
- The bright target shows no dip, with an upper limit well below what it would need to be the source.

The difference images are not too noisy for a verdict. Per quarter the candidate's pixel reaches S/N 4 to 9, and per season stack 11 to 14.

## Caveats

- The specified estimator (centroid boxed on the brightest difference pixel) points at the target in 11 of 16 quarters. Those are noise picks in the bright star's core (pixel S/N under 2.4), not a detection there. With a 12.8 mag star 2.9 px away, a flux-based box choice is the wrong tool; S/N-based selection or a PRF fit is needed.
- Kepler pixels are 4 arcsec, so this test cannot exclude a fainter star within about 1 px (4 arcsec) of the candidate, or blended with it, as the true host of the eclipse. That needs a Gaia/high-resolution neighbour check around the candidate.
- The candidate lies at the stamp edge (column 0.5 to 0.9), so its PSF is clipped. Centroids are pulled toward the stamp interior and box sums under-count its flux.
- The two-star fit uses a Gaussian PSF (fitted sigma 0.54 to 0.60 px), not the Kepler PRF. Its depths are approximate, though the candidate-vs-target contrast does not depend on this.
- The bootstrap uses 1 to 3 events per quarter, so the per-quarter uncertainties are rough.

## Files

- `download_tpfs.py`: lists the MAST directory and downloads the 18 TPFs into `tpf/`, writing `download_log.json`.
- `inspect_tpfs.py`: prints stamp shapes and star pixel positions.
- `diffimage.py`: the analysis. Writes `results.json`, `diffimage_products.npz` and (via the command below) `run_log.txt`.
- `make_figure.py`: makes `centroids.png` and `centroids.svg`. Rows are seasons; each row has 4 quarters plus the season stack, direct image beside difference image. Bottom row: centroids on the sky, and the per-quarter depth from the two-star fit.

## Reproduce

The venv lacked matplotlib and pip. I bootstrapped pip with `python.exe -m ensurepip` and then ran `python.exe -m pip install matplotlib` (3.11.2). That added matplotlib and its dependencies only; numpy and astropy were not touched.

```powershell
$env:PYTHONUTF8 = "1"; $env:PYTHONPATH = "C:\Research\skysift"
cd C:\Research\skysift\.superpowers\dossier\kic6146676\localisation
C:\Research\skysift\.venv\Scripts\python.exe download_tpfs.py
C:\Research\skysift\.venv\Scripts\python.exe -I diffimage.py tpf . > run_log.txt
C:\Research\skysift\.venv\Scripts\python.exe -I make_figure.py tpf .
```

`diffimage.py` uses a fixed random seed (20261020), so reruns give the same numbers.
