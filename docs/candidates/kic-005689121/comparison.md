# KIC 5689121 versus KIC 6146676: the same tests, side by side

Date: 2026-10-20. Sources: `docs/candidates/kic-006146676/{localisation,context}/REPORT.md` for KIC 6146676 and `.superpowers/dossier/kic5689121/{localisation,context}/REPORT.md` for KIC 5689121. Both packages were produced with the same scripts, so differences are in the stars, not the method.

| | KIC 5689121 | KIC 6146676 |
|---|---|---|
| Signal | P 13.7695 d, depth 1983 ppm (fit 2124), 2.0 h, 65 events, SDE 19.9 | P 40.2911 d, depth 2165 ppm, 3.0 h, 33 events |
| Host | M2V, G 16.28, 291 pc, 0.46 to 0.50 Rsun | M1V, G 16.24, 303 pc, 0.50 Rsun |
| Kepler target whose pixels hold it | KIC 5689116, Kp 15.48 (G star, background, RUWE 8.7), 7.1 arcsec = 1.8 px, 2.2x brighter | KIC 6146666, Kp 12.85 (F6V), 11.5 arcsec = 2.9 px, 23x brighter |
| TPF data | 13 quarters (no Q0, Q5, Q9, Q13, Q17), 4x5 and 5x5 stamps, candidate on the bottom stamp edge, 68 events used | 16 usable quarters of 18, 5x7 to 8x10 stamps, candidate on the left stamp edge, 35 events used |

## Test by test

| Test | KIC 5689121 | KIC 6146676 | Which is stronger |
|---|---|---|---|
| Highest-S/N difference pixel on the candidate | 13 of 13 quarters (S/N 2.9 to 11.1), 4 of 4 seasons (S/N 11.2, 11.8, 18.0 and 2.9 for the one-quarter season 3) | 16 of 16 quarters (S/N 3.7 to 8.6), 4 of 4 seasons (S/N 11.4 to 13.6) | Equal; 5689121 has higher per-season S/N in its three full seasons |
| Specified (brightest-pixel) centroid | Nearer the candidate in 13 of 13; median 0.18 px from candidate, 1.73 px from target | Nearer the candidate in only 5 of 16; noise picks in the bright target's core, estimator declared unreliable | 5689121: the estimator works because the target is not bright enough to swamp it |
| S/N-box centroid | 13 of 13 nearer candidate, median 0.13 px (0.5 arcsec) from it, 1.75 px (7.0 arcsec) from target | 16 of 16, median 0.17 px (0.7 arcsec), 2.82 px (11.2 arcsec) from target | Equal |
| Season-stack centroid | 0.13 to 0.15 px from candidate (+/- 0.04 to 0.07), 100 % of bootstrap draws nearer candidate in the full seasons | 0.10 to 0.20 px from candidate, but bootstrap sigma 0.7 to 1.4 px and only 47 to 84 % of draws nearer candidate | 5689121: tighter and unimodal |
| Two-star fit depth on candidate | 2124 +/- 71 ppm (light curve 1983 box / 2124 fit) | 2213 +/- 78 ppm (light curve 2165) | Equal |
| Two-star fit depth on target | 82 +/- 35 ppm; target needs >= 901 ppm (784 with the fitted flux ratio); excluded at 20 to 23 sigma | -21 +/- 25 ppm; target needs >= 94 ppm; excluded at 4.6 sigma | 5689121: a much larger margin because the target is only 2.2x brighter |
| Delta chi2, target-only minus two-star, per full season | 196, 311, 366 | 176 to 232 | 5689121 |
| 3x3 box fractional depth | Candidate 710 to 1330 ppm, target 120 to 540 ppm; boxes overlap, so the target value is contaminated | Candidate 830 to 2060 ppm, target -32 to 0 ppm; boxes do not overlap | 6146676: a cleaner null on the target |
| Target's own Kepler light curve | PDCSAP 110 +/- 33 ppm, SAP 127 +/- 42 ppm: 3.4-sigma leak of the candidate's light, inside the 42 to 141 ppm crowding budget; on-target case (>= 1983 ppm) excluded at 57 sigma; target's KBonus PSF 12 +/- 61 ppm | PDCSAP -10.5 +/- 9.4 ppm: no signal at all; on-target case excluded at > 200 sigma | 6146676 has a cleaner null; 5689121 has a positive consistency check (the leak is seen and has the predicted size) |
| Candidate SAP/PSF depth ratio | 0.75 +/- 0.08, PSF season invariant (chi2 2.0/3), SAP tracks aperture crowding | 0.74 +/- 0.05, PSF season invariant (chi2 0.6/3), SAP tracks aperture size | Equal |
| Gaia DR3 neighbours within 30 arcsec | 7 (6 fainter by 2.9 to 4.3 mag, nearest at 14.7 arcsec = 3.7 px) | 17 (15 fainter by 1.7 to 4.3 mag, nearest at 7.1 arcsec = 1.8 px) | 5689121: a sparser field and every neighbour is well outside the localisation error |
| Neighbours with their own light curves | 1 of 7 (the target only); all 6 faint ones 404 | 4 of 17 (target plus KIC 6146682, 6146659, 6146651, two of them excluded directly) | 6146676 has more direct exclusions, 5689121 needs fewer |
| KIC-only sources (no Gaia counterpart) | None | KIC 6146680 at 4.0 arcsec, probably spurious or poor astrometry | 5689121 |
| Candidate in Gaia | RUWE 0.984, ipd_frac_multi_peak 0, non_single_star 0, no vari/NSS rows, DSC binary 0.004 | RUWE 0.945, ipd_frac_multi_peak 0, non_single_star 0, no vari/NSS rows, DSC binary 0.002 | Equal |
| KOI / TCE (DR25, DR24, Q1-Q16, Q1-Q12) / TOI / Kepler EB / Morton FPP / ExoFOP | All empty | All empty | Equal |
| Planet Hunters (Zooniverse) tables | PH VII empty; PH II, Lintott 2013, PH V, PH VI not in VizieR | PH V not in VizieR; PH VII empty | Equal (inconclusive for both, the tables mostly do not exist in VizieR) |
| SIMBAD | Candidate present (Gaia DR3 id, 1 reference: 2022AJ....164...88B) | Candidate absent | Neither bears on the signal |
| Prior claim of the transit signal | Yes: "Planethunt" (Kuber Mehta), public GitHub repository, Zenodo 10.5281/zenodo.23325261, RNAAS submitted 13 Oct 2026 (stated not accepted), same star, same period, same host assignment, TRICERATOPS FPP ~ 0.07, unconfirmed | None found | 6146676 is new; 5689121 is a rediscovery |
| Stellar parameters | Gaia parallax + Mamajek 3582 K / 0.46 Rsun; Mann 0.50 Rsun; TIC 3633 K / 0.51 Rsun; dust map E(B-V) 0.09 (b = +15 deg) | Gaia parallax + Mamajek 3653 K / 0.50 Rsun; Mann 0.51 Rsun; TIC 3694 K / 0.49 Rsun; dust map E(B-V) 0.67 total (b = +7 deg), ~0.2 at 303 pc | 5689121: lower and better-bounded extinction |
| Planet | 2.2 to 2.7 Re, a 0.086 AU, T_eq 364 to 400 K, ~2.9 S_earth | 2.5 to 3.0 Re, a 0.18 AU, T_eq 267 to 291 K, ~1.2 S_earth | 6146676 is the more interesting orbit (temperate); 5689121 has the tighter radius |
| Duration consistency | Central T14 2.7 to 2.9 h vs 2.0 h box: b ~ 0.7; fitted b 0.86 implies 1.55 h | Central T14 4.1 h vs 3.0 h: b ~ 0.7; fitted b 0.89 implies 2.17 h | Equal |

## What each has and lacks

KIC 5689121 has:
- A pixel localisation with no ambiguity in any estimator: every quarter, every season and every centroid definition points at the candidate, and the target is excluded at 20 sigma rather than 5.
- A positive consistency check in the target's own light curve: the predicted leak of candidate light is seen at the predicted size.
- A sparse neighbourhood with no neighbour inside 3.7 px and no KIC-only source.
- Low, bounded extinction and three concordant stellar-parameter routes.

KIC 5689121 lacks:
- Novelty. A public, dated, citable (if unrefereed and reportedly not accepted) claim of the same candidate with the same host assignment predates SkySift's detection by about a week.
- A clean null on the target in the box test, because the two stars' 3x3 boxes overlap at 1.8 px separation; only the two-star fit separates them.
- Any direct light-curve test of the six faint neighbours (none have light curves); they rest on position and brightness arguments.
- Q0, Q5, Q9, Q13 and Q17 (the target was not observed), so season 3 is a single quarter.

KIC 6146676 has:
- No prior claim anywhere.
- A clean null on the bright target in every test (box, fit, own light curve) with no leak to explain.
- Direct light-curve exclusions of three neighbours besides the target.
- A temperate orbit (1.2 S_earth), which makes it the more valuable object if confirmed.

KIC 6146676 lacks:
- A working brightest-pixel centroid (bimodal because the target is 23 times brighter); its verdict rests on the S/N-selected centroid and the two-star fit.
- A tight season-stack centroid (bootstrap 1-sigma 0.7 to 1.4 px, with 16 to 53 % of draws jumping to the target).
- A strong exclusion margin on the target in the fit (4.6 sigma, limited by the 94 ppm the bright target would need).
- A crowded field: 15 fainter Gaia neighbours within 30 arcsec, the nearest at 1.8 px, plus a KIC-only source at 4 arcsec, 12 of them without any light curve.
- A bounded extinction (dust map 0.67 along the sightline, Bayestar unreachable), which spreads Rp over 2.6 to 3.0 Re.

## Shared gaps

Neither star has a high-resolution image, a spectrum, a radial velocity or a contrast curve, so neither excludes an eclipsing binary within about 1 Kepler pixel (4 arcsec) or a bound companion. Both two-star fits use a Gaussian PSF rather than the Kepler PRF. Both candidates sit on a stamp edge, so their PSFs are truncated in the pixel data.

## Bottom line

On the pixel and leak tests KIC 5689121 is the more securely localised of the two: the geometry (a target only 2.2 times brighter) lets every estimator agree and gives a 20-sigma exclusion of the target. KIC 6146676 is localised just as confidently in the end, but only after discarding the brightest-pixel estimator, and with a 5-sigma margin on the target. On everything else KIC 6146676 is the better candidate to pursue: it is new, its field tests are cleaner where they exist, and its orbit is temperate. KIC 5689121 is a sound confirmation that the pipeline recovers a candidate someone else already found and localised the same way.
