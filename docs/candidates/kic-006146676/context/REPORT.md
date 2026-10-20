# KIC 6146676: independent context checks

Report of the context-check subagent, 20 October 2026, saved verbatim by the coordinating session (the subagent's own write of this file was refused by the harness). Numbers are in `results.json`; raw archive responses with URL, size and SHA-256 are in `archive-proof/manifest.json`.

Candidate: KIC 6146676 = Gaia DR3 2073896294004902272 = TIC 169822650 (RA 297.868708651717, Dec +41.45556694086529, G 16.237, BP-RP 2.097, Kp 16.26). Signal from the SkySift engine: P = 40.29109 d, t0 = 135.788 BKJD, duration 3.0 h, box depth 2165 ppm, k = 0.048, b = 0.89, 33 events. Light curve: KBonus-BKG PSF photometry (Martinez-Palomera, Hedges & Dotson 2023) from the pixels of the Kepler target KIC 6146666 (Gaia DR3 2073896190925687296, Kp 12.85, 11.45 arcsec away).

## Summary

| Check | Result | What it supports |
|---|---|---|
| A. KIC 6146666 own Kepler LC | PDCSAP -10.5 +- 9.4 ppm, SAP -8.9 +- 12.4 ppm (35 events, 188 in-transit cadences) | Dip not on KIC 6146666 (on-target case needs >= 2165 ppm, excluded at > 200 sigma). Little candidate light reaches the target aperture (< 8 ppm at 2 sigma vs full-mixing bound 90 to 94 ppm). |
| A. Candidate PSF vs SAP | PSF 2330 +- 103, SAP 1721 +- 93 ppm, ratio 0.74 +- 0.05 | Shallower in the larger, more contaminated aperture, as expected for a dip at the candidate's PSF position. A resolved, separately modelled neighbour would give SAP > PSF. |
| A. PSF depth by season | 2436 +- 179, 2300 +- 185, 2256 +- 229, 2306 +- 186 ppm | Season invariant (chi2 ~ 0.6, 3 dof). SAP/PSF varies with aperture: 0.95, 0.55, 0.56, 0.83. |
| A.3 Neighbours' own KBonus LCs | KIC 6146682 -0.37 +- 0.66 %, KIC 6146659 -0.85 +- 0.70 %, KIC 6146651 +0.05 +- 0.22 %, KIC 6146666 -28 +- 50 ppm | No dip at the ephemeris. KIC 6146659 excluded as full-blend host at > 3 sigma, KIC 6146651 excluded (see A.3). |
| A.4 Centroids | Not usable | KBonus centroid columns are model positions. |
| B. Gaia DR3 30 arcsec | 17 neighbours: 15 fainter (dG 1.66 to 4.31, required eclipse 1.0 to 11.4 %), 2 brighter | All 15 fainter could mimic by brightness. None co-moving. Closest G 19.77 at 7.14 arcsec (1.8 px). |
| B. Candidate in Gaia | RUWE 0.945, ipd_frac_multi_peak 0, non_single_star 0, no RV, no vari/NSS rows | No sign of binarity in Gaia. |
| C. Archives | KOI, TCE (DR25, DR24, Q1-Q16, Q1-Q12), TOI, Kepler EB, Morton FPP, Planet Hunters VI, ExoFOP TOI/CTOI: all empty. SIMBAD: KIC 6146666 only | Not previously catalogued. |
| D. Star | d 303.0 +- 3.6 pc, M_G 8.83, Teff 3653 +91/-73 K, R 0.498 +0.036/-0.040, M 0.495 +0.036/-0.045 (M1V) | Early M dwarf. TIC v8.2 agrees (3694 K, 0.494 Rsun, 0.492 Msun). |
| D. Planet | Rp 2.61 +0.19/-0.21 Re (k), 2.53 +0.18/-0.20 Re (box), a 0.182 AU, a/R* 78.6 +4.2/-3.6, T_eq 267 +- 13 K, P_tr 1.27 % | Sub-Neptune at about Earth insolation (~1.2 S_earth). |
| D. Duration | Central T14 4.10 +- 0.21 h vs 3.0 h: b = 0.72 +0.07/-0.09 (3.0 h = T14) or 0.64 +0.08/-0.10 (3.0 h = (T14+T23)/2) | Consistent with a circular orbit. Fitted b = 0.89 implies T14 = 2.17 h, so that b is probably too high (k-b degeneracy). |

## A. Neighbour leak test (`a1_download_lc.py`, `a2_leak_test.py`)

Method: the MAST directory https://archive.stsci.edu/missions/kepler/lightcurves/0061/006146666/ was listed over HTTP and all 18 `*_llc.fits` files (Q0-Q17) downloaded (`archive-proof/A_downloads_manifest.json`). Only SAP_QUALITY = 0 cadences were kept. Each quarter of PDCSAP_FLUX and SAP_FLUX was divided by a 2.0-day running median, with points within one duration of a transit centre excluded from the median. Depth = mean(out) - mean(in), where "in" is |t - tc| < 1.5 h and "out" is 3 to 9 h (1 to 3 durations). Formal error = std(out) x sqrt(1/N_in + 1/N_out), inflated by max(1, scatter of 100 control-phase depths at phases 0.03 to 0.97 / median control error). The same code was run on the candidate's cached KBonus file (FLUX = PSF, SAP_FLUX = SAP).

Expected depths on KIC 6146666:
- Leak case (dip on the candidate): F_t/F_c = 23.1 (Kp 12.85 vs 16.26), so at most 2165/(1 + 23.1) = 90 ppm (94 ppm as 2165/23.1). The real value is lower, because the target's optimal aperture holds only part of the candidate's light at 2.9 px. The pipeline CROWDSAP of the target is 0.992-0.999, so all other stars supply at most ~0.5 % of its aperture flux, which caps the leak at ~11 ppm.
- On-target case: D_cand = D_t x eF_t/(F_c + eF_t) < D_t, so the target must show more than 2165 ppm.

| Light curve | Depth (ppm) | Error | Inflation | N_in/N_out | Events | rms/cadence (ppm) |
|---|---|---|---|---|---|---|
| KIC 6146666 PDCSAP | -10.5 | 9.4 | 1.01 | 188/755 | 35 | 113 |
| KIC 6146666 SAP | -8.9 | 12.4 | 1.23 | 188/755 | 35 | 138 |
| KIC 6146666 KBonus PSF | -28.0 | 49.6 | | 188 | 35 | 316 |
| KIC 6146676 KBonus PSF | 2330.4 | 102.7 | 1.19 | 188/755 | 35 | 1055 |
| KIC 6146676 KBonus SAP | 1720.9 | 92.7 | 1.28 | 188/755 | 35 | 1015 |

The target shows no dip (2-sigma upper limit 8 ppm), so the on-target case is excluded. The measurement is also below the full-mixing bound; this only says little of the candidate's light enters the target aperture, and it agrees with the dip being on the candidate. The candidate PSF depth here (2330 ppm) is 1.6 sigma above the engine's 2165 ppm because of different detrending and out-of-transit windows.

| Season | Quarters | PSF (ppm) | SAP (ppm) | SAP/PSF | mean KBonus CROWDSAP |
|---|---|---|---|---|---|
| 0 | 2, 6, 10, 14 | 2436 +- 179 | 2307 +- 171 | 0.95 | 0.922 |
| 1 | 3, 7, 11, 15 | 2300 +- 185 | 1254 +- 204 | 0.55 | 0.886 |
| 2 | 4, 8, 12, 16 | 2256 +- 229 | 1271 +- 225 | 0.56 | 0.915 |
| 3 | 0, 1, 5, 9, 13, 17 | 2306 +- 186 | 1904 +- 160 | 0.83 | 0.951 |

The PSF depth is constant. The SAP depth is lowest in the seasons with 8-pixel apertures (Q3, Q7, Q11, Q12), which reach towards the 23 times brighter KIC 6146666. The KBonus CROWDSAP values (0.86 to 1.0; Q4 = 1.0 is not credible) understate this dilution. For a dip at the candidate's position, more foreign light in the aperture makes the dip shallower. For a dip on a resolved neighbour that the PSF model fits separately, the PSF suppresses that light while the aperture collects it, so SAP would be deeper. The data show the reverse.

### A.3 Own light curves of other neighbours (`a3_neighbour_lcs.py`)

KBonus-BKG files were tried for every Gaia neighbour (by KIC id when a KIC source lies within 2 arcsec, else by Gaia id), and Kepler mission directories for the KIC ids. Only KIC 6146666 has mission light curves. KBonus files exist for 4 neighbours; their stitched FLUX is NaN in many quarters, so the per-quarter extensions were used.

| Star | Sep | G | Quarters | Depth at ephemeris | Required (full blend) |
|---|---|---|---|---|---|
| KIC 6146666 | 11.45 | 12.81 | 18 | -28 +- 50 ppm | > 2165 ppm |
| KIC 6146682 | 10.94 | 17.90 | 11 | -3669 +- 6601 ppm | >= 1.0 % |
| KIC 6146659 | 23.31 | 18.42 | 6 | -8472 +- 7037 ppm | >= 1.6 % |
| KIC 6146651 | 25.29 | 15.10 | 6 | 521 +- 2159 ppm | 7.6e-4/e |

KIC 6146651 is brighter than the candidate but 6.4 px away. To produce the dip it needs D = 7.6e-4/e (e = fraction of its light assigned to the candidate), and its own curve gives D < 0.70 % at 3 sigma, which would need e > 0.11. A PSF fit at that distance cannot assign that much light, so it is excluded. The other neighbours returned HTTP 404.

### A.4 Centroids (`a4_centroid.py`): not usable

The CENTROID_COLUMN/ROW of the candidate and of KIC 6146666 move identically (difference scatter ~1e-6 px, correlation 1.000). They are PSF-model positions, not flux-weighted centroids, so their in-transit shifts carry no information. A centroid or difference-image test needs the target pixel files of KIC 6146666 (done separately: see `../localisation/REPORT.md`).

## B. Gaia DR3 neighbourhood (`bc_queries.py`, `bc_queries2.py`, `b_neighbours.py`)

Required eclipse depth = 2165 ppm x 10^(0.4 dG). This assumes full blending; for PSF photometry the real requirement is larger by 1/e.

| source_id | KIC | Sep " | px | G | BP-RP | plx | plx/err | pmRA | pmDec | RUWE | dG | Req. depth | Mimic |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 2073896294004902144 | | 7.14 | 1.79 | 19.772 | 1.814 | 0.165 | 0.49 | -3.59 | -5.37 | 1.129 | +3.54 | 5.6 % | yes |
| 2073896289692192128 | 6146682 | 10.94 | 2.75 | 17.896 | 1.664 | 0.158 | 1.83 | -3.40 | -8.72 | 0.996 | +1.66 | 1.0 % | yes |
| 2073896190925687296 | 6146666 | 11.45 | 2.88 | 12.806 | 0.802 | 1.118 | 90.7 | 4.90 | -9.53 | 1.026 | -3.43 | brighter | excluded (A) |
| 2073896190926955520 | | 12.97 | 3.26 | 19.736 | | 1.237 | 3.23 | 11.82 | 4.47 | 1.070 | +3.50 | 5.4 % | yes |
| 2073896190921609600 | 6146662 | 13.51 | 3.40 | 19.380 | 1.533 | 0.007 | 0.03 | -2.66 | -3.28 | 1.071 | +3.14 | 3.9 % | yes |
| 2073896289692177024 | 6146700 | 18.62 | 4.68 | 18.883 | 2.623 | 2.498 | 15.6 | 9.36 | -11.08 | 0.997 | +2.65 | 2.5 % | yes |
| 2073896293999229696 | | 21.00 | 5.28 | 20.478 | 1.777 | 0.271 | 0.44 | -5.18 | -6.81 | 0.996 | +4.24 | 10.8 % | yes |
| 2073896293999228800 | | 22.62 | 5.68 | 20.284 | 1.145 | -0.679 | -1.29 | -1.43 | -3.54 | 1.015 | +4.05 | 9.0 % | yes |
| 2073896186612950272 | 6146659 | 23.31 | 5.86 | 18.415 | 2.126 | 0.066 | 0.55 | -1.49 | -1.97 | 0.991 | +2.18 | 1.6 % | yes (own LC disfavours) |
| 2073896293999233024 | 6146706 | 24.32 | 6.11 | 20.424 | 1.949 | -0.544 | -0.90 | -4.46 | -9.58 | 0.994 | +4.19 | 10.2 % | yes |
| 2073896190925362816 | 6146651 | 25.29 | 6.35 | 15.100 | 2.046 | 0.254 | 12.4 | -1.56 | -5.68 | 1.000 | -1.14 | brighter | excluded (A.3) |
| 2073896190926870400 | | 25.53 | 6.41 | 20.542 | 1.897 | 0.033 | 0.04 | -7.59 | -7.90 | 1.074 | +4.31 | 11.4 % | yes |
| 2073896289692204928 | 6146670 | 25.91 | 6.51 | 18.806 | 1.686 | 0.190 | 1.24 | 0.89 | 0.04 | 0.995 | +2.57 | 2.3 % | yes |
| 2073896294000822656 | | 26.61 | 6.69 | 19.665 | 1.863 | 0.815 | 3.02 | 2.82 | -5.25 | 0.978 | +3.43 | 5.1 % | yes |
| 2073896293999228160 | | 27.05 | 6.80 | 20.153 | 1.351 | 0.628 | 1.29 | -2.45 | -3.27 | 1.066 | +3.92 | 8.0 % | yes |
| 2073896225279754240 | | 27.25 | 6.85 | 19.933 | 1.862 | 0.065 | 0.19 | 0.78 | -3.04 | 0.957 | +3.70 | 6.5 % | yes |
| 2073896293999215744 | | 29.58 | 7.43 | 20.499 | 1.687 | -0.241 | -0.40 | -2.29 | -6.21 | 1.049 | +4.26 | 11.0 % | yes |

All fainter neighbours are within 6.7 mag (maximum dG 4.31). None matches the candidate's parallax (3.27 mas) and proper motion (-12.35, -25.13 mas/yr).

The KIC also lists KIC 6146680 at 3.99 arcsec (Kp 18.85, g 18.54, r 18.79, i 18.99, no 2MASS). Gaia has nothing within 3 arcsec of it; the nearest Gaia source (G 19.77) is 3.2 arcsec away. It is probably the same object with poor KIC astrometry, or a spurious entry. If real, it would need a 2.4 % eclipse.

Gaia tables for the candidate (vari_summary, vari_classifier_result, nss_two_body_orbit, vari_eclipsing_binary) are all header-only. The same tables plus nss_non_linear_spectro and nss_acceleration_astro, queried for all 18 cone sources by explicit id list, are empty too (the sub-select form gave HTTP 400).

Candidate gaia_source:
- parallax 3.2686 +- 0.0358 mas, RUWE 0.945, astrometric_excess_noise 0, ipd_frac_multi_peak 0, ipd_gof_harmonic_amplitude 0.017
- non_single_star 0, phot_variable_flag NOT_AVAILABLE, has_rvs false
- GSP-Phot: Teff 3855 K, R 0.547, E(BP-RP) 0.27; FLAME: R 0.539, M 0.560; ESP-HS type M; DSC binary probability 0.002

## C. Archives

| Query | Result |
|---|---|
| NEA cumulative, +-30 arcsec box | empty (header only) |
| NEA cumulative, kepid 6146666/6146676 | empty |
| q1_q17_dr25_tce, box and kepid | empty (header only, 2101 B) |
| q1_q17_dr24_tce, q1_q16_tce, q1_q12_tce, kepid | empty |
| toi, box | empty |
| keplerstellar, kepid | 5 rows, all KIC 6146666 (Teff 6220-6259 K, R 1.42-2.08) |
| q1_q17_dr25_stellar, fpwg | HTTP 400 |
| VizieR J/AJ/151/68/catalog (Kirk 2016 EB) | empty (INFO header only) |
| VizieR J/ApJ/822/86 (Morton 2016 FPP) | empty |
| VizieR J/ApJ/795/167 (Planet Hunters VI) | empty |
| VizieR J/ApJ/776/10 | `Error=Table or Catalog not found: J/ApJ/776/10` (skipped) |
| VizieR V/133/kic / IV/39/tic82 | 10 KIC / 18 TIC sources; candidate = TIC 169822650 |
| SIMBAD 30 arcsec | one object: 2MASS J19512748+4127178 = KIC 6146666 = TIC 169822655 (F6V, plx 1.1177). Candidate not in SIMBAD. |
| ExoFOP TIC 169822650 JSON | `"tois": []`, `"ctois": []`, `"imaging": []`, `"spectroscopy": []`, `"stellar_companions": []` |

KIC 6146666 went through the Kepler pipeline up to DR25 with no TCE, which agrees with check A.

## D. Star and planet (`d_dust.py`, `d_stellar_planet.py`)

Distance: the Lindegren et al. (2021) zero point is -0.0317 mas, giving a corrected parallax of 3.3004 +- 0.0388 mas and d = 303.0 +- 3.6 pc (DM 7.407). Before extinction, M_G = 8.83 and M_Ks = 5.65 (Ks 13.055).

Extinction:
- Bayestar19 timed out over both http and https.
- IRSA DUST (2-D, full sightline) gives SF11 E(B-V) = 0.674 (SFD 0.784) at l 76.04, b +7.42. Scaled to 303 pc with a 125 pc scale height this is ~0.18, i.e. E(BP-RP) ~ 0.2-0.25. GSP-Phot gives 0.27.
- M_Ks = 5.65 matches the Mamajek value of 5.67 at the observed colour 2.10. The dereddened colour 1.90 would predict 5.25.
- So the baseline uses E(BP-RP) <= 0.1, with E ~ 0.2 as a sensitivity case.

| Source | Teff | R | M |
|---|---|---|---|
| Mamajek BP-RP 2.10 +- 0.10 (baseline MC) | 3653 +91/-73 | 0.498 +0.036/-0.040 | 0.495 +0.036/-0.045 |
| Mamajek at 2.00 / 2.10 / 2.20 | 3743 / 3650 / 3578 | 0.533 / 0.496 / 0.457 | 0.530 / 0.492 / 0.449 |
| Mann 2015 R(M_Ks), Mann 2019 M(M_Ks) | | 0.505 +- 0.016 | 0.503 +- 0.013 |
| TIC v8.2 | 3694 +- 157 | 0.494 +- 0.016 | 0.492 +- 0.021 |
| Gaia GSP-Phot / FLAME | 3855 | 0.547 / 0.539 | 0.560 |
| Mamajek 1.90 +- 0.10 (if E ~ 0.2) | 3813 +100/-70 | 0.568 +- 0.035 | 0.556 +0.030/-0.026 |

Planet properties from a Monte Carlo (N = 200 000):

| Quantity | Baseline | Mann M_Ks | E ~ 0.2 |
|---|---|---|---|
| Rp from k (Re) | 2.61 +0.19/-0.21 | 2.64 +- 0.08 | 2.97 +- 0.19 |
| Rp from box depth (Re) | 2.53 +0.18/-0.20 | 2.56 +- 0.08 | 2.88 +- 0.18 |
| a (AU) | 0.1819 +0.0043/-0.0057 | 0.1829 +- 0.0015 | 0.1892 +- 0.0032 |
| a/R* | 78.6 +4.2/-3.6 | 77.9 +2.5/-2.3 | 71.7 +3.4/-3.1 |
| T_eq (A 0.3, full redistribution) (K) | 267 +13/-12 | 267 +- 8 | 291 +14/-12 |
| R*/a | 1.27 +- 0.06 % | 1.28 +- 0.04 % | 1.40 +- 0.06 % |
| Central T14 (h) | 4.10 +0.20/-0.21 | 4.14 +- 0.13 | 4.50 +- 0.20 |
| b if 3.0 +- 0.25 h = T14 | 0.72 +0.07/-0.09 | 0.72 +0.06/-0.08 | 0.78 +0.05/-0.06 |
| b if 3.0 h = (T14+T23)/2 | 0.64 +0.08/-0.10 | 0.65 +0.07/-0.09 | 0.71 +0.06/-0.07 |
| T14 for fitted b 0.89 (h) | 2.17 +0.10/-0.11 | 2.19 +- 0.07 | 2.38 +- 0.11 |

Stellar density is 5.7 +0.9/-0.8 g/cm3 and the insolation ~1.2 S_earth. The depth has a systematic of roughly 10 % (2165 vs 2330 ppm, and the SAP/PSF difference), about 5 % in radius. An unresolved companion would dilute the depth and make the planet larger. Eccentricity can change the duration by tens of percent, so the b estimate is a consistency check, not a constraint.

## What the checks support and cannot exclude

Supported:
- The dip is not on KIC 6146666.
- The dip behaves as a source at the candidate's PSF position: the PSF depth is season invariant, and SAP is shallower than PSF in step with aperture size.
- The host is an early M dwarf (M1V, ~0.50 Rsun, ~0.50 Msun) at 303 pc with clean Gaia astrometry and no Gaia variability or NSS solution.
- A ~2.6 Re planet at 0.18 AU fits the observed duration with b ~ 0.7.
- The signal is in no reachable KOI, TCE, TOI, CTOI, EB or community catalogue.

Cannot exclude:
- An eclipsing binary within about one Kepler pixel (4 arcsec) that Gaia does not resolve (Gaia is incomplete inside ~1-2 arcsec for faint companions). It would behave exactly like the candidate in the SAP/PSF and season tests, and would need 2165 ppm x 10^(0.4 dm), for example 2.2 % at dm 2.5 or 13.7 % at dm 4.5.
- A bound companion. RUWE 0.945 and ipd_frac_multi_peak 0 disfavour bright companions at ~0.1-1 arcsec, not faint (dG >~ 3) or very close ones. There is no RV, imaging or spectroscopy.
- Most faint neighbours individually. Only KIC 6146666, KIC 6146651 and KIC 6146659 are tested by their own curves; the rest rest on the PSF-model and SAP/PSF arguments plus their 1-11 % required eclipses.
- A centroid or difference-image test from the KBonus products alone (done instead from the KIC 6146666 target pixel files, see `../localisation/`).
- Extinction is uncertain by 0.1-0.2 mag in BP-RP until Bayestar is reachable, which moves Rp between 2.6 and 3.0 Re.

## Things that did not work

- Gaia: IN (sub-select) gave HTTP 400; redone with an explicit id list.
- Exoplanet Archive: q1_q17_dr25_stellar and fpwg gave HTTP 400.
- VizieR: J/ApJ/776/10 "Table or Catalog not found".
- Bayestar19: timed out.
- KBonus: HTTP 404 for 12 of the 16 faint neighbours.
- The subagent installed `gaiadr3-zeropoint` into the project venv with `python.exe -m pip`.

## Files

Scripts, in run order (all with the venv python, PYTHONUTF8=1, PYTHONPATH=C:\Research\skysift): a1_download_lc.py, bc_queries.py, bc_queries2.py, c_queries3.py, d_dust.py, a2_leak_test.py, a3_neighbour_lcs.py, a4_centroid.py, b_neighbours.py, d_stellar_planet.py, e_assemble.py. Outputs: results.json (plus results_A/A3/A4/B/D.json), archive-proof/ (raw responses and manifest.json), downloads/kic006146666_llc/ (18 LLC files), downloads/neighbour_lcs/ (4 KBonus files). The candidate's cached KBonus file was only read.

## Exact URLs queried

Kepler LCs of KIC 6146666: https://archive.stsci.edu/missions/kepler/lightcurves/0061/006146666/ and the 18 files listed in archive-proof/A_downloads_manifest.json. All other queries, with their full URL, size or error, are in archive-proof/manifest.json; the table below is generated from it.

| B_gaia_cone30 | 3734 B | https://gea.esac.esa.int/tap-server/tap/sync?request=doQuery&lang=ADQL&format=csv&query=SELECT+source_id%2Cra%2Cdec%2CDISTANCE%28POINT%28ra%2Cdec%29%2CPOINT%28297.868708651717%2C41.45556694086529%29%29%2A3600+AS+sep_arcsec%2Cphot_g_mean_mag%2Cbp_rp%2Cparallax%2Cparallax_error%2Cparallax_over_error%2Cpmra%2Cpmdec%2Cruwe%2Cphot_variable_flag%2Cnon_single_star+FROM+gaiadr3.gaia_source+WHERE+1%3DCONTAINS%28POINT%28ra%2Cdec%29%2CCIRCLE%28297.868708651717%2C41.45556694086529%2C0.00833333%29%29+ORDER+BY+sep_arcsec |
| B_gaia_source_candidate | 3918 B | https://gea.esac.esa.int/tap-server/tap/sync?request=doQuery&lang=ADQL&format=csv&query=SELECT+%2A+FROM+gaiadr3.gaia_source+WHERE+source_id%3D2073896294004902272 |
| B_gaia_source_neighbour | 288 B | https://gea.esac.esa.int/tap-server/tap/sync?request=doQuery&lang=ADQL&format=csv&query=SELECT+source_id%2Cra%2Cdec%2Cphot_g_mean_mag%2Cbp_rp%2Cparallax%2Cparallax_error%2Cruwe%2Cradial_velocity%2Cteff_gspphot%2Cphot_variable_flag%2Cnon_single_star+FROM+gaiadr3.gaia_source+WHERE+source_id%3D2073896190925687296 |
| B_gaia_astrophys_candidate | 5284 B | https://gea.esac.esa.int/tap-server/tap/sync?request=doQuery&lang=ADQL&format=csv&query=SELECT+%2A+FROM+gaiadr3.astrophysical_parameters+WHERE+source_id%3D2073896294004902272 |
| B_gaia_vari_summary_candidate | 1173 B | https://gea.esac.esa.int/tap-server/tap/sync?request=doQuery&lang=ADQL&format=csv&query=SELECT+%2A+FROM+gaiadr3.vari_summary+WHERE+source_id%3D2073896294004902272 |
| B_gaia_vari_classifier_result_candidate | 71 B | https://gea.esac.esa.int/tap-server/tap/sync?request=doQuery&lang=ADQL&format=csv&query=SELECT+%2A+FROM+gaiadr3.vari_classifier_result+WHERE+source_id%3D2073896294004902272 |
| B_gaia_nss_two_body_orbit_candidate | 1273 B | https://gea.esac.esa.int/tap-server/tap/sync?request=doQuery&lang=ADQL&format=csv&query=SELECT+%2A+FROM+gaiadr3.nss_two_body_orbit+WHERE+source_id%3D2073896294004902272 |
| B_gaia_vari_eclipsing_binary_candidate | 1079 B | https://gea.esac.esa.int/tap-server/tap/sync?request=doQuery&lang=ADQL&format=csv&query=SELECT+%2A+FROM+gaiadr3.vari_eclipsing_binary+WHERE+source_id%3D2073896294004902272 |
| B_gaia_vari_summary_cone30 | HTTP error/timeout: HTTP 400 | https://gea.esac.esa.int/tap-server/tap/sync?request=doQuery&lang=ADQL&format=csv&query=SELECT+t.%2A+FROM+gaiadr3.vari_summary+AS+t+WHERE+t.source_id+IN+%28SELECT+source_id+FROM+gaiadr3.gaia_source+WHERE+1%3DCONTAINS%28POINT%28ra%2Cdec%29%2CCIRCLE%28297.868708651717%2C41.45556694086529%2C0.00833333%29%29%29 |
| B_gaia_vari_classifier_result_cone30 | HTTP error/timeout: HTTP 400 | https://gea.esac.esa.int/tap-server/tap/sync?request=doQuery&lang=ADQL&format=csv&query=SELECT+t.%2A+FROM+gaiadr3.vari_classifier_result+AS+t+WHERE+t.source_id+IN+%28SELECT+source_id+FROM+gaiadr3.gaia_source+WHERE+1%3DCONTAINS%28POINT%28ra%2Cdec%29%2CCIRCLE%28297.868708651717%2C41.45556694086529%2C0.00833333%29%29%29 |
| B_gaia_nss_two_body_orbit_cone30 | HTTP error/timeout: HTTP 400 | https://gea.esac.esa.int/tap-server/tap/sync?request=doQuery&lang=ADQL&format=csv&query=SELECT+t.%2A+FROM+gaiadr3.nss_two_body_orbit+AS+t+WHERE+t.source_id+IN+%28SELECT+source_id+FROM+gaiadr3.gaia_source+WHERE+1%3DCONTAINS%28POINT%28ra%2Cdec%29%2CCIRCLE%28297.868708651717%2C41.45556694086529%2C0.00833333%29%29%29 |
| B_gaia_vari_eclipsing_binary_cone30 | HTTP error/timeout: HTTP 400 | https://gea.esac.esa.int/tap-server/tap/sync?request=doQuery&lang=ADQL&format=csv&query=SELECT+t.%2A+FROM+gaiadr3.vari_eclipsing_binary+AS+t+WHERE+t.source_id+IN+%28SELECT+source_id+FROM+gaiadr3.gaia_source+WHERE+1%3DCONTAINS%28POINT%28ra%2Cdec%29%2CCIRCLE%28297.868708651717%2C41.45556694086529%2C0.00833333%29%29%29 |
| C_nea_cumulative_box | 115 B | https://exoplanetarchive.ipac.caltech.edu/TAP/sync?query=select+kepid%2Ckepoi_name%2Ckepler_name%2Ckoi_disposition%2Ckoi_pdisposition%2Ckoi_period%2Ckoi_time0bk%2Ckoi_depth%2Ckoi_duration%2Cra%2Cdec+from+cumulative+where+ra+between+297.857589+and+297.879829+and+dec+between+41.447234+and+41.463900&format=csv |
| C_nea_cumulative_kepid | 73 B | https://exoplanetarchive.ipac.caltech.edu/TAP/sync?query=select+kepid%2Ckepoi_name%2Ckepler_name%2Ckoi_disposition%2Ckoi_period%2Ckoi_depth%2Cra%2Cdec+from+cumulative+where+kepid+in+%286146666%2C6146676%29&format=csv |
| C_nea_dr25tce_box | 2101 B | https://exoplanetarchive.ipac.caltech.edu/TAP/sync?query=select+%2A+from+q1_q17_dr25_tce+where+ra+between+297.857589+and+297.879829+and+dec+between+41.447234+and+41.463900&format=csv |
| C_nea_dr25tce_kepid | 2101 B | https://exoplanetarchive.ipac.caltech.edu/TAP/sync?query=select+%2A+from+q1_q17_dr25_tce+where+kepid+in+%286146666%2C6146676%29&format=csv |
| C_nea_dr24tce_kepid | 2101 B | https://exoplanetarchive.ipac.caltech.edu/TAP/sync?query=select+%2A+from+q1_q17_dr24_tce+where+kepid+in+%286146666%2C6146676%29&format=csv |
| C_nea_toi_box | 1027 B | https://exoplanetarchive.ipac.caltech.edu/TAP/sync?query=select+%2A+from+toi+where+ra+between+297.857589+and+297.879829+and+dec+between+41.447234+and+41.463900&format=csv |
| C_nea_dr25stellar_kepid | HTTP error/timeout: HTTP 400 | https://exoplanetarchive.ipac.caltech.edu/TAP/sync?query=select+%2A+from+q1_q17_dr25_stellar+where+kepid+in+%286146666%2C6146676%29&format=csv |
| C_nea_keplerstellar_kepid | 336 B | https://exoplanetarchive.ipac.caltech.edu/TAP/sync?query=select+kepid%2Cteff%2Clogg%2Cradius%2Cmass%2Ckepmag%2Cra%2Cdec+from+keplerstellar+where+kepid+in+%286146666%2C6146676%29&format=csv |
| C_vizier_keplerEB_kirk2016 | 4038 B | https://vizier.cds.unistra.fr/viz-bin/asu-tsv?-source=J%2FAJ%2F151%2F68%2Fcatalog&-c=297.868708651717+%2B41.45556694086529&-c.rs=30&-out.all=&-out.add=_r&-sort=_r |
| C_vizier_KIC | 3985 B | https://vizier.cds.unistra.fr/viz-bin/asu-tsv?-source=V%2F133%2Fkic&-c=297.868708651717+%2B41.45556694086529&-c.rs=30&-out.all=&-out.add=_r&-sort=_r |
| C_vizier_TICv82 | 16416 B | https://vizier.cds.unistra.fr/viz-bin/asu-tsv?-source=IV%2F39%2Ftic82&-c=297.868708651717+%2B41.45556694086529&-c.rs=30&-out.all=&-out.add=_r&-sort=_r |
| C_vizier_planethunters_schwamb2013 | 772 B | https://vizier.cds.unistra.fr/viz-bin/asu-tsv?-source=J%2FApJ%2F776%2F10&-c=297.868708651717+%2B41.45556694086529&-c.rs=30&-out.all=&-out.add=_r&-sort=_r |
| C_vizier_planethunters_schmitt2014 | 769 B | https://vizier.cds.unistra.fr/viz-bin/asu-tsv?-source=J%2FApJ%2F795%2F167&-c=297.868708651717+%2B41.45556694086529&-c.rs=30&-out.all=&-out.add=_r&-sort=_r |
| C_vizier_kepler_fpp_morton2016 | 4323 B | https://vizier.cds.unistra.fr/viz-bin/asu-tsv?-source=J%2FApJ%2F822%2F86&-c=297.868708651717+%2B41.45556694086529&-c.rs=30&-out.all=&-out.add=_r&-sort=_r |
| C_simbad_cone30 | 1745 B | https://simbad.cds.unistra.fr/simbad/sim-coo?Coord=297.868708651717+%2B41.45556694086529&Radius=30&Radius.unit=arcsec&output.format=ASCII&list.idsel=on |
| B_gaia_vari_summary_cone30_ids | 10 B | https://gea.esac.esa.int/tap-server/tap/sync?request=doQuery&lang=ADQL&format=csv&query=SELECT+source_id+FROM+gaiadr3.vari_summary+WHERE+source_id+IN+%282073896294004902272%2C2073896294004902144%2C2073896289692192128%2C2073896190925687296%2C2073896190926955520%2C2073896190921609600%2C2073896289692177024%2C2073896293999229696%2C2073896293999228800%2C2073896186612950272%2C2073896293999233024%2C2073896190925362816%2C2073896190926870400%2C2073896289692204928%2C2073896294000822656%2C2073896293999228160%2C2073896225279754240%2C2073896293999215744%29 |
| B_gaia_vari_classifier_result_cone30_ids | 10 B | https://gea.esac.esa.int/tap-server/tap/sync?request=doQuery&lang=ADQL&format=csv&query=SELECT+source_id+FROM+gaiadr3.vari_classifier_result+WHERE+source_id+IN+%282073896294004902272%2C2073896294004902144%2C2073896289692192128%2C2073896190925687296%2C2073896190926955520%2C2073896190921609600%2C2073896289692177024%2C2073896293999229696%2C2073896293999228800%2C2073896186612950272%2C2073896293999233024%2C2073896190925362816%2C2073896190926870400%2C2073896289692204928%2C2073896294000822656%2C2073896293999228160%2C2073896225279754240%2C2073896293999215744%29 |
| B_gaia_nss_two_body_orbit_cone30_ids | 10 B | https://gea.esac.esa.int/tap-server/tap/sync?request=doQuery&lang=ADQL&format=csv&query=SELECT+source_id+FROM+gaiadr3.nss_two_body_orbit+WHERE+source_id+IN+%282073896294004902272%2C2073896294004902144%2C2073896289692192128%2C2073896190925687296%2C2073896190926955520%2C2073896190921609600%2C2073896289692177024%2C2073896293999229696%2C2073896293999228800%2C2073896186612950272%2C2073896293999233024%2C2073896190925362816%2C2073896190926870400%2C2073896289692204928%2C2073896294000822656%2C2073896293999228160%2C2073896225279754240%2C2073896293999215744%29 |
| B_gaia_vari_eclipsing_binary_cone30_ids | 10 B | https://gea.esac.esa.int/tap-server/tap/sync?request=doQuery&lang=ADQL&format=csv&query=SELECT+source_id+FROM+gaiadr3.vari_eclipsing_binary+WHERE+source_id+IN+%282073896294004902272%2C2073896294004902144%2C2073896289692192128%2C2073896190925687296%2C2073896190926955520%2C2073896190921609600%2C2073896289692177024%2C2073896293999229696%2C2073896293999228800%2C2073896186612950272%2C2073896293999233024%2C2073896190925362816%2C2073896190926870400%2C2073896289692204928%2C2073896294000822656%2C2073896293999228160%2C2073896225279754240%2C2073896293999215744%29 |
| B_gaia_nss_non_linear_spectro_cone30_ids | 10 B | https://gea.esac.esa.int/tap-server/tap/sync?request=doQuery&lang=ADQL&format=csv&query=SELECT+source_id+FROM+gaiadr3.nss_non_linear_spectro+WHERE+source_id+IN+%282073896294004902272%2C2073896294004902144%2C2073896289692192128%2C2073896190925687296%2C2073896190926955520%2C2073896190921609600%2C2073896289692177024%2C2073896293999229696%2C2073896293999228800%2C2073896186612950272%2C2073896293999233024%2C2073896190925362816%2C2073896190926870400%2C2073896289692204928%2C2073896294000822656%2C2073896293999228160%2C2073896225279754240%2C2073896293999215744%29 |
| B_gaia_nss_acceleration_astro_cone30_ids | 10 B | https://gea.esac.esa.int/tap-server/tap/sync?request=doQuery&lang=ADQL&format=csv&query=SELECT+source_id+FROM+gaiadr3.nss_acceleration_astro+WHERE+source_id+IN+%282073896294004902272%2C2073896294004902144%2C2073896289692192128%2C2073896190925687296%2C2073896190926955520%2C2073896190921609600%2C2073896289692177024%2C2073896293999229696%2C2073896293999228800%2C2073896186612950272%2C2073896293999233024%2C2073896190925362816%2C2073896190926870400%2C2073896289692204928%2C2073896294000822656%2C2073896293999228160%2C2073896225279754240%2C2073896293999215744%29 |
| D_exofop_tic169822650 | 7434 B | https://exofop.ipac.caltech.edu/tess/target.php?id=169822650&json= |
| D_mamajek_dwarf_table | 55680 B | https://www.pas.rochester.edu/~emamajek/EEM_dwarf_UBVIJHK_colors_Teff.txt |
| D_bayestar19_a | HTTP error/timeout: bayestar http://argonaut.skymaps.info/ap | http://argonaut.skymaps.info/api/v2/bayestar19/query?l=76.04442&b=7.41979&mode=percentile |
| D_bayestar19_b | HTTP error/timeout: bayestar https://argonaut.skymaps.info/a | https://argonaut.skymaps.info/api/v2/bayestar19/query?lon=76.04442&lat=7.41979&coordsys=gal&mode=percentile |
| D_irsa_dust | 2765 B | https://irsa.ipac.caltech.edu/cgi-bin/DUST/nph-dust?locstr=297.868708651717+41.45556694086529+equ+j2000&regSize=2.0 |
| A3_kbonus_gaia-dr3-2073896294004902144 | HTTP error/timeout: HTTP 404 | https://archive.stsci.edu/hlsps/kbonus-bkg/lcs/2073/2073896294004902144/hlsp_kbonus-bkg_kepler_kepler_gaia-dr3-2073896294004902144_kepler_v1.0_lc.fits |
| A3_kbonus_kic-006146682 | 11514240 B | https://archive.stsci.edu/hlsps/kbonus-bkg/lcs/0061/006146682/hlsp_kbonus-bkg_kepler_kepler_kic-006146682_kepler_v1.0_lc.fits |
| A3_mast_dirlisting_006146682 | HTTP error/timeout: HTTP 404 | https://archive.stsci.edu/missions/kepler/lightcurves/0061/006146682/ |
| A3_kbonus_kic-006146666 | 11514240 B | https://archive.stsci.edu/hlsps/kbonus-bkg/lcs/0061/006146666/hlsp_kbonus-bkg_kepler_kepler_kic-006146666_kepler_v1.0_lc.fits |
| A3_kbonus_kic-006146662 | HTTP error/timeout: HTTP 404 | https://archive.stsci.edu/hlsps/kbonus-bkg/lcs/0061/006146662/hlsp_kbonus-bkg_kepler_kepler_kic-006146662_kepler_v1.0_lc.fits |
| A3_mast_dirlisting_006146662 | HTTP error/timeout: HTTP 404 | https://archive.stsci.edu/missions/kepler/lightcurves/0061/006146662/ |
| A3_kbonus_kic-006146700 | HTTP error/timeout: HTTP 404 | https://archive.stsci.edu/hlsps/kbonus-bkg/lcs/0061/006146700/hlsp_kbonus-bkg_kepler_kepler_kic-006146700_kepler_v1.0_lc.fits |
| A3_mast_dirlisting_006146700 | HTTP error/timeout: HTTP 404 | https://archive.stsci.edu/missions/kepler/lightcurves/0061/006146700/ |
| A3_kbonus_gaia-dr3-2073896293999229696 | HTTP error/timeout: HTTP 404 | https://archive.stsci.edu/hlsps/kbonus-bkg/lcs/2073/2073896293999229696/hlsp_kbonus-bkg_kepler_kepler_gaia-dr3-2073896293999229696_kepler_v1.0_lc.fits |
| A3_kbonus_gaia-dr3-2073896293999228800 | HTTP error/timeout: HTTP 404 | https://archive.stsci.edu/hlsps/kbonus-bkg/lcs/2073/2073896293999228800/hlsp_kbonus-bkg_kepler_kepler_gaia-dr3-2073896293999228800_kepler_v1.0_lc.fits |
| A3_kbonus_kic-006146659 | 11514240 B | https://archive.stsci.edu/hlsps/kbonus-bkg/lcs/0061/006146659/hlsp_kbonus-bkg_kepler_kepler_kic-006146659_kepler_v1.0_lc.fits |
| A3_mast_dirlisting_006146659 | HTTP error/timeout: HTTP 404 | https://archive.stsci.edu/missions/kepler/lightcurves/0061/006146659/ |
| A3_kbonus_kic-006146706 | HTTP error/timeout: HTTP 404 | https://archive.stsci.edu/hlsps/kbonus-bkg/lcs/0061/006146706/hlsp_kbonus-bkg_kepler_kepler_kic-006146706_kepler_v1.0_lc.fits |
| A3_mast_dirlisting_006146706 | HTTP error/timeout: HTTP 404 | https://archive.stsci.edu/missions/kepler/lightcurves/0061/006146706/ |
| A3_kbonus_kic-006146651 | 11514240 B | https://archive.stsci.edu/hlsps/kbonus-bkg/lcs/0061/006146651/hlsp_kbonus-bkg_kepler_kepler_kic-006146651_kepler_v1.0_lc.fits |
| A3_mast_dirlisting_006146651 | HTTP error/timeout: HTTP 404 | https://archive.stsci.edu/missions/kepler/lightcurves/0061/006146651/ |
| A3_kbonus_gaia-dr3-2073896190926870400 | HTTP error/timeout: HTTP 404 | https://archive.stsci.edu/hlsps/kbonus-bkg/lcs/2073/2073896190926870400/hlsp_kbonus-bkg_kepler_kepler_gaia-dr3-2073896190926870400_kepler_v1.0_lc.fits |
| A3_kbonus_kic-006146670 | HTTP error/timeout: HTTP 404 | https://archive.stsci.edu/hlsps/kbonus-bkg/lcs/0061/006146670/hlsp_kbonus-bkg_kepler_kepler_kic-006146670_kepler_v1.0_lc.fits |
| A3_mast_dirlisting_006146670 | HTTP error/timeout: HTTP 404 | https://archive.stsci.edu/missions/kepler/lightcurves/0061/006146670/ |
| A3_kbonus_gaia-dr3-2073896294000822656 | HTTP error/timeout: HTTP 404 | https://archive.stsci.edu/hlsps/kbonus-bkg/lcs/2073/2073896294000822656/hlsp_kbonus-bkg_kepler_kepler_gaia-dr3-2073896294000822656_kepler_v1.0_lc.fits |
| A3_kbonus_gaia-dr3-2073896293999228160 | HTTP error/timeout: HTTP 404 | https://archive.stsci.edu/hlsps/kbonus-bkg/lcs/2073/2073896293999228160/hlsp_kbonus-bkg_kepler_kepler_gaia-dr3-2073896293999228160_kepler_v1.0_lc.fits |
| A3_kbonus_gaia-dr3-2073896225279754240 | HTTP error/timeout: HTTP 404 | https://archive.stsci.edu/hlsps/kbonus-bkg/lcs/2073/2073896225279754240/hlsp_kbonus-bkg_kepler_kepler_gaia-dr3-2073896225279754240_kepler_v1.0_lc.fits |
| A3_kbonus_gaia-dr3-2073896293999215744 | HTTP error/timeout: HTTP 404 | https://archive.stsci.edu/hlsps/kbonus-bkg/lcs/2073/2073896293999215744/hlsp_kbonus-bkg_kepler_kepler_gaia-dr3-2073896293999215744_kepler_v1.0_lc.fits |
| A3_kbonus_gaia-dr3-2073896190926955520 | HTTP error/timeout: HTTP 404 | https://archive.stsci.edu/hlsps/kbonus-bkg/lcs/2073/2073896190926955520/hlsp_kbonus-bkg_kepler_kepler_gaia-dr3-2073896190926955520_kepler_v1.0_lc.fits |
| C_nea_dr25stellar_kepid_retry | HTTP error/timeout: HTTP 400 | https://exoplanetarchive.ipac.caltech.edu/TAP/sync?query=select+kepid%2Cteff%2Clogg%2Cradius%2Cmass%2Ckepmag+from+q1_q17_dr25_stellar+where+kepid+in+%286146666%2C6146676%29&format=csv |
| C_nea_q1q16tce_kepid | 40 B | https://exoplanetarchive.ipac.caltech.edu/TAP/sync?query=select+kepid%2Ctce_plnt_num%2Ctce_period%2Ctce_depth+from+q1_q16_tce+where+kepid+in+%286146666%2C6146676%29&format=csv |
| C_nea_q1q12tce_kepid | 40 B | https://exoplanetarchive.ipac.caltech.edu/TAP/sync?query=select+kepid%2Ctce_plnt_num%2Ctce_period%2Ctce_depth+from+q1_q12_tce+where+kepid+in+%286146666%2C6146676%29&format=csv |
| C_nea_fpwg_kepid | HTTP error/timeout: HTTP 400 | https://exoplanetarchive.ipac.caltech.edu/TAP/sync?query=select+kepid%2Ckepoi_name%2Cfpwg_disp_status+from+fpwg+where+kepid+in+%286146666%2C6146676%29&format=csv |
