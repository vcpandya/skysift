# KIC 5689121: independent context checks

Date: 2026-10-20. Same scripts and method as `docs/candidates/kic-006146676/context/` with the ids, ephemeris, positions and flux ratio changed (plus `c_queries4.py` to `c_queries6.py` for this star's bibliography and prior-claim checks). Numbers are in `results.json`; raw archive responses with URL, size and SHA-256 are in `archive-proof/manifest.json` and in the manifest table at the end of this file.

Candidate: KIC 5689121 = Gaia DR3 2103792461826460288 = TIC 120686465 (RA 285.90782692874626, Dec +40.94198844296448, G 16.282, BP-RP 2.194, Kp 16.332). Signal from the SkySift engine (box_dip v5): P = 13.769509 d, t0 = 136.31664 BKJD, duration 2.0 h, box depth 1983 ppm, rung-2 fit depth 2124 ppm, k = 0.046, b = 0.86, 65 events. Light curve: KBonus-BKG PSF photometry from the pixels of the Kepler target KIC 5689116 (Gaia DR3 2103792427466721792, Kp 15.476, G 15.478, BP-RP 0.92, 7.10 arcsec = 1.78 px away, 2.2 times brighter in Kp).

## Summary

| Check | Result | What it supports |
|---|---|---|
| A. KIC 5689116 own Kepler LC | PDCSAP 110 +- 33 ppm, SAP 127 +- 42 ppm (74 events, 283 in-transit cadences) | Dip not on KIC 5689116: the on-target case needs >= 1983 ppm, excluded at 57 sigma. The 3.4-sigma residual is the expected leak of the candidate's light into the target aperture: the pipeline CROWDSAP of KIC 5689116 (0.93 to 0.98) allows 42 to 141 ppm (mean 76 ppm) if the candidate is its only contaminant. KIC 5689116's own KBonus PSF curve, which models both stars, shows 12 +- 61 ppm. |
| A. Candidate PSF vs SAP | PSF 2136 +- 158, SAP 1595 +- 127 ppm, ratio 0.75 +- 0.08 | Shallower in the larger, more contaminated aperture, as expected for a dip at the candidate's PSF position (the engine's rung-3 ratio is 0.63). |
| A. PSF depth by season | 1844 +- 343, 2251 +- 236, 2361 +- 199, 1785 +- 757 ppm | Season invariant (chi2 2.0, 3 dof). SAP/PSF varies with aperture: 0.49, 0.85, 0.88, 0.64. |
| A.3 Neighbours' own LCs | Only KIC 5689116 has any light curve (mission LLC and KBonus); the 6 faint Gaia neighbours have neither | Only the target can be tested directly, and it is excluded. |
| A.4 Centroids | Not usable | KBonus centroid columns are model positions (identical for both stars to 2e-6 px). Done instead from the TPFs: `../localisation/REPORT.md`. |
| B. Gaia DR3 30 arcsec | 7 neighbours: 6 fainter (dG 2.85 to 4.30, required eclipse 2.7 to 10.4 %), 1 brighter (the target) | All 6 fainter could mimic by brightness alone; none is co-moving; nearest is G 19.79 at 14.7 arcsec (3.7 px). Every KIC source within 30 arcsec has a Gaia counterpart within 1 arcsec, so there is no KIC-only source. |
| B. Candidate in Gaia | RUWE 0.984, ipd_frac_multi_peak 0, astrometric_excess_noise 0, non_single_star 0, no RV, no vari/NSS rows, DSC binary probability 0.004 | No sign of binarity in Gaia. |
| C. Archives | KOI, TCE (DR25, DR24, Q1-Q16, Q1-Q12), TOI, Kepler EB, Morton FPP, Planet Hunters VII (J/ApJ/795/167): all empty. SIMBAD: candidate has 1 reference (2022AJ....164...88B), KIC 5689116 has 4. ExoFOP: no TOI/CTOI, no imaging, no spectroscopy, no companions. | Not in any catalogue of transit candidates. |
| C. Prior independent claim | A public "Planethunt" repository (Kuber Mehta; Zenodo 10.5281/zenodo.23325261; RNAAS submission AAS82222 on 13 Oct 2026, stated as not accepted, revision planned) reports the same star and period (13.769448 d) as an unconfirmed candidate with TRICERATOPS FPP about 0.07 | KIC 5689121 is not new. The claim is self-published and unrefereed, but it predates the SkySift detection and reaches the same localisation verdict (candidate favoured over KIC 5689116 in all 13 quarters with PRF fits). See C below. |
| D. Star | d 290.7 +- 3.6 pc, M_G 8.97, M_Ks 5.65, Teff 3582 +74/-57 K, R 0.459 +0.040/-0.023, M 0.451 +0.046/-0.027 (M2V) from Mamajek at the observed colour; Mann M_Ks gives R 0.504 +- 0.016, M 0.502 +- 0.013; TIC v8.2 3633 K, 0.507 Rsun, 0.505 Msun | Early-to-mid M dwarf at 291 pc. The Mamajek colour track is 10 % smaller than the M_Ks and TIC values, so the baseline below is the conservative (smaller) one. |
| D. Planet | Rp 2.30 +0.20/-0.11 Re (k) or 2.23 +0.19/-0.11 Re (box) baseline, 2.53 +- 0.08 Re (Mann); a 0.086 AU, a/R* 40.4, T_eq 364 K (375 K Mann), P_tr 2.5 % | Sub-Neptune, insolation about 2.9 S_earth (warm, not temperate). |
| D. Duration | Central T14 2.72 +- 0.1 h (2.89 h Mann) vs 2.0 h box: b = 0.72 +0.09/-0.13 (2.0 h = T14) or 0.65 +0.10/-0.14 (2.0 h = (T14+T23)/2). Fitted b = 0.86 implies T14 = 1.55 h. | Consistent with a circular orbit at moderate impact parameter; the rung-2 b is probably high (k-b degeneracy), as for KIC 6146676. |

## A. Neighbour leak test (`a1_download_lc.py`, `a2_leak_test.py`)

Method: the MAST directory https://archive.stsci.edu/missions/kepler/lightcurves/0056/005689116/ was listed and all 13 `*_llc.fits` files downloaded (Q1-Q4, Q6-Q8, Q10-Q12, Q14-Q16; this target has no Q0, Q5, Q9, Q13 or Q17). Only SAP_QUALITY = 0 cadences were kept. Each quarter of PDCSAP_FLUX and SAP_FLUX was divided by a 2.0-day running median with points within one duration of a transit centre excluded. Depth = mean(out) - mean(in), "in" = |t - tc| < 1.0 h, "out" = 2 to 6 h. Formal error = std(out) x sqrt(1/N_in + 1/N_out), inflated by the scatter of 100 control phases. The same code was run on the candidate's cached KBonus file.

Expected depths on KIC 5689116:
- Leak case (dip on the candidate): F_t/F_c = 2.20 (Kp 15.476 vs 16.332), so at most 1983/(1 + 2.2) = 620 ppm if all the candidate's light were in the target aperture (901 ppm as 1983/2.2). The pipeline CROWDSAP of the target is 0.929 to 0.979, so all other stars together supply 2.1 to 7.1 % of its aperture flux, which caps the leak at 42 to 141 ppm (mean 76 ppm).
- On-target case: the target must show more than 1983 ppm.

| Light curve | Depth (ppm) | Error | Inflation | N_in/N_out | Events | rms/cadence (ppm) |
|---|---|---|---|---|---|---|
| KIC 5689116 PDCSAP | 110.4 | 32.5 | 1.00 | 283/1114 | 74 | 482 |
| KIC 5689116 SAP | 127.1 | 41.8 | 1.00 | 283/1114 | 74 | 503 |
| KIC 5689116 KBonus PSF | 11.5 | 61.1 | 1.09 | 284/1114 | 74 | 658 |
| KIC 5689121 KBonus PSF | 2136.1 | 157.8 | 1.27 | 284/1114 | 74 | 1646 |
| KIC 5689121 KBonus SAP | 1595.3 | 127.2 | 1.34 | 284/1114 | 74 | 1289 |

The target's aperture photometry shows 110 +- 33 ppm, a 3.4-sigma signal at the candidate's ephemeris. It is 18 times too shallow for the on-target case and sits inside the crowding budget of the leak case, so it is the candidate's light leaking into the target aperture 1.8 px away. The KBonus PSF curve of the target, which fits the two stars separately, removes it (12 +- 61 ppm). The candidate PSF depth here (2136 ppm) agrees with the engine's box depth (1983) and fit depth (2124) within the detrending differences.

| Season | Quarters | PSF (ppm) | SAP (ppm) | SAP/PSF | mean KBonus CROWDSAP |
|---|---|---|---|---|---|
| 0 | 2, 6, 10, 14 | 1844 +- 343 | 908 +- 276 | 0.49 | 0.778 |
| 1 | 3, 7, 11, 15 | 2251 +- 236 | 1917 +- 190 | 0.85 | 0.879 |
| 2 | 4, 8, 12, 16 | 2361 +- 199 | 2072 +- 198 | 0.88 | 0.908 |
| 3 | 1 | 1785 +- 757 | 1139 +- 548 | 0.64 | 0.813 |

The PSF depth is constant across seasons (chi2 2.0 for 3 dof); the SAP depth is not (chi2 14.0). The SAP depth is lowest in season 0, where the KBonus aperture is largest (7 pixels, CROWDSAP 0.75 to 0.78) and reaches into KIC 5689116. More foreign light in the aperture makes the dip shallower, which is the signature of a dip at the candidate's own PSF position. A dip on a resolved neighbour that the PSF model fits separately would give SAP deeper than PSF; the data show the reverse.

### A.3 Own light curves of other neighbours (`a3_neighbour_lcs.py`)

KBonus-BKG files were tried for every Gaia neighbour (by KIC id when a KIC source lies within 2 arcsec, else by Gaia id), and Kepler mission directories for the KIC ids. Only KIC 5689116 has either. The other six (KIC 5689129, 5689108, 5689127 and three Gaia-only sources, G 19.1 to 20.6) returned HTTP 404 from both services, so they can only be constrained by the magnitude argument in B and by the pixel localisation.

| Star | Sep " | G | Quarters | Depth at ephemeris | Required (full blend) |
|---|---|---|---|---|---|
| KIC 5689116 (KBonus PSF) | 7.10 | 15.48 | 13 | 12 +- 61 ppm | > 1983 ppm |
| KIC 5689116 (PDCSAP) | 7.10 | 15.48 | 13 | 110 +- 33 ppm | > 1983 ppm |

### A.4 Centroids (`a4_centroid.py`): not usable

As for KIC 6146676, the CENTROID_COLUMN/ROW of the candidate and of KIC 5689116 in their KBonus files move identically (difference scatter 2e-6 px, correlation 1.000); they are PSF-model positions and carry no information about the dip source. The pixel-level test was done from the TPFs of KIC 5689116 (`../localisation/REPORT.md`): the dip is on the candidate in 13 of 13 quarters, with a two-star fit giving 2124 +- 71 ppm on the candidate and 82 +- 35 ppm on the target.

## B. Gaia DR3 neighbourhood (`bc_queries.py`, `bc_queries2.py`, `b_neighbours.py`)

Required eclipse depth = 1983 ppm x 10^(0.4 dG), assuming full blending; for PSF photometry the real requirement is larger by 1/e.

| source_id | KIC | Sep " | px | G | BP-RP | plx | plx/err | pmRA | pmDec | RUWE | dG | Req. depth | Mimic |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 2103792427466721792 | 5689116 | 7.10 | 1.78 | 15.478 | 0.917 | 0.302 | 1.32 | -9.44 | -7.31 | 8.730 | -0.81 | brighter | excluded (A, localisation) |
| 2103792393102276480 | | 14.68 | 3.69 | 19.789 | 1.055 | 0.346 | 1.13 | -3.38 | -6.69 | 0.981 | +3.51 | 5.0 % | yes |
| 2103792393108242176 | 5689129 | 16.37 | 4.11 | 19.131 | 1.660 | -0.010 | -0.02 | 3.72 | -9.89 | 2.679 | +2.85 | 2.7 % | yes |
| 2103792427462012544 | 5689108 | 17.94 | 4.51 | 19.963 | 1.854 | 0.027 | 0.07 | -3.98 | -8.14 | 0.929 | +3.68 | 5.9 % | yes |
| 2103792427462012032 | | 18.96 | 4.76 | 20.578 | 1.385 | -0.272 | -0.39 | 0.66 | -5.74 | 1.012 | +4.30 | 10.4 % | yes |
| 2103792461821756544 | 5689127 | 22.27 | 5.60 | 19.790 | 1.673 | 1.056 | 3.13 | 1.17 | -1.21 | 1.030 | +3.51 | 5.0 % | yes |
| 2103792393102280576 | | 28.32 | 7.11 | 20.193 | 2.436 | 1.352 | 3.23 | -3.44 | 7.61 | 0.868 | +3.91 | 7.3 % | yes |

All six fainter neighbours are 2.9 to 4.3 mag fainter and 3.7 to 7.1 px away. None matches the candidate's parallax (3.41 mas) and proper motion (-5.40, +2.85 mas/yr). The pixel localisation places the dip within 0.3 px of the candidate and the nearest of these six is 3.7 px away, so they are excluded by position as well as by the required 3 to 10 % eclipses.

KIC 5689116 is a background star (parallax 0.30 +- 0.23 mas, so beyond about 1.5 kpc) with RUWE 8.73, which marks it as a probable unresolved binary or a star with a perturbed astrometric solution. That does not affect the candidate, but it means the target's own photometric and astrometric behaviour should not be used as a clean reference.

All five KIC sources within 30 arcsec (5689121, 5689116, 5689129, 5689108, 5689127) have a Gaia DR3 counterpart within 0.9 arcsec. There is no KIC-only source of the kind found near KIC 6146676.

Gaia tables for the candidate (vari_summary, vari_classifier_result, nss_two_body_orbit, vari_eclipsing_binary) are header-only. The same tables plus nss_non_linear_spectro and nss_acceleration_astro, queried for all 8 cone sources by explicit id list, are empty (the sub-select form gave HTTP 400 again).

Candidate gaia_source:
- parallax 3.4065 +- 0.0397 mas, RUWE 0.984, astrometric_excess_noise 0, ipd_frac_multi_peak 0, ipd_gof_harmonic_amplitude 0.020, phot_bp_rp_excess_factor 1.364
- non_single_star 0, phot_variable_flag NOT_AVAILABLE, has_rvs false
- GSP-Phot: Teff 3753 K, R 0.555, E(BP-RP) 0.27, d 298 pc; FLAME: R 0.542, M 0.545; ESP-HS type M; [M/H] -0.15 to -0.18; DSC binary probability 0.004

## C. Archives

| Query | Result |
|---|---|
| NEA cumulative, +-30 arcsec box and kepid 5689116/5689121 | empty |
| q1_q17_dr25_tce, box and kepid; q1_q17_dr24_tce, q1_q16_tce, q1_q12_tce, kepid | empty |
| toi, box | empty |
| keplerstellar, kepid | 5 rows, all KIC 5689116 (Teff 5957-5982 K, R 0.92-0.96, logg 4.5) |
| q1_q17_dr25_stellar, fpwg | HTTP 400 (both forms, as before) |
| VizieR J/AJ/151/68/catalog (Kirk 2016 EB) | empty |
| VizieR J/ApJ/822/86 (Morton 2016 FPP) | empty |
| VizieR J/ApJ/795/167 (Planet Hunters VII, Schmitt 2014) | empty |
| VizieR J/ApJ/754/129, J/AJ/145/151, J/ApJ/776/10, J/AJ/148/28 (Planet Hunters II, Lintott 2013, V, VI), by cone and by KIC | `Table or Catalog not found` for all four: these Planet Hunters papers have no VizieR tables |
| VizieR V/133/kic / IV/39/tic82 | 5 KIC / 8 TIC sources; candidate = TIC 120686465 (0.12 arcsec) |
| SIMBAD 30 arcsec | 2 objects: Gaia DR3 2103792461826460288 (the candidate, 1 reference: 2022AJ....164...88B) and 2MASS J19033726+4056316 = KIC 5689116 = TIC 120686466 (4 references: 2014ApJS..211...24M, 2015ApJ...809....8B, 2021ApJS..255...17S, 2023ApJ...952..131M). The sim-tap bibliography ADQL gave HTTP 400 twice; the sim-id ASCII page worked. |
| ExoFOP TIC 120686465 JSON | `"tois": []`, `"ctois": []`, `"imaging": []`, `"spectroscopy": []`, `"stellar_companions": []`; TIC v8.2 Teff 3633 +- 157, R 0.507 +- 0.016, M 0.505 +- 0.021, d 298 pc |

KIC 5689116 went through the Kepler pipeline up to DR25 with no TCE, which agrees with check A: the leaked 110 ppm signal is below the pipeline's detection threshold for a Kp 15.5 star.

**Prior independent claim (`c_queries5.py`, saved as `C_planethunt_*`).** The coordinating session described this star as a Planet Hunters find. The Zooniverse Planet Hunters tables that exist in VizieR do not list it, and the others are not in VizieR. A web search for the KIC id instead returns a self-published project, "Planethunt" (Kuber Mehta; https://kuber.studio/planethunt/ and https://github.com/Kuberwastaken/planethunt). Its README, fetched and hashed on 2026-10-20, states (quoting the page as data, not as a verified result): a Zenodo record 10.5281/zenodo.23325261, an RNAAS manuscript submitted as AAS82222 on 13 October 2026 "not accepted; revision planned", the candidate "remains unconfirmed", a TRICERATOPS false-positive probability of about 0.07 with "a planet around an unresolved bound companion" as the open loophole, PRF-based pixel fits that favour the candidate over KIC 5689116 in all 13 quarters, a period of 13.769448 d, Spitzer images that resolve the pair but do not cover a transit, and a possible membership in the MELANGE-3 candidate-member catalogue. None of this is refereed, but it is a public, dated and citable claim on the same star, period and host assignment, made before the SkySift detection. For the novelty ledger, KIC 5689121 is therefore a rediscovery, whereas KIC 6146676 has no prior claim of any kind.

## D. Star and planet (`d_dust.py`, `d_stellar_planet.py`)

Distance: the Lindegren et al. (2021) zero point is -0.033 mas, giving a corrected parallax of 3.4395 +- 0.0425 mas and d = 290.7 +- 3.6 pc (DM 7.32). Before extinction, M_G = 8.97 and M_Ks = 5.65 (Ks 12.969).

Extinction:
- Bayestar19 timed out over both http and https, as for the other star.
- IRSA DUST gives SF11 E(B-V) = 0.088 (SFD 0.102) along the full sightline at l 71.50, b +15.25, i.e. E(BP-RP) < 0.12 even with no distance scaling, and about 0.04 scaled to 291 pc. GSP-Phot's 0.27 is not supported by the dust map at this latitude.
- M_Ks = 5.65 matches the Mamajek value of 5.67 at BP-RP 2.10 and 5.90 at the observed 2.19; the Mann relations at M_Ks 5.62 give 0.50 Rsun. The observed colour therefore sits about 0.1 mag redder than the M_Ks luminosity suggests, which is within the colour scatter of the table plus a small reddening.
- Baseline: Mamajek at the observed BP-RP 2.19 +- 0.10 with E(BP-RP) <= 0.1 assumed; E ~ 0.2 (colour 1.99) as a sensitivity case; Mann M_Ks as the extinction-insensitive cross-check.

| Source | Teff | R | M |
|---|---|---|---|
| Mamajek BP-RP 2.19 +- 0.10 (baseline MC) | 3582 +74/-57 | 0.459 +0.040/-0.023 | 0.451 +0.046/-0.027 |
| Mamajek at 2.10 / 2.20 / 2.30 | 3650 / 3578 / 3521 | 0.496 / 0.457 / 0.435 | 0.492 / 0.449 / 0.423 |
| Mann 2015 R(M_Ks), Mann 2019 M(M_Ks) | | 0.504 +- 0.016 | 0.502 +- 0.013 |
| TIC v8.2 | 3633 +- 157 | 0.507 +- 0.016 | 0.505 +- 0.021 |
| Gaia GSP-Phot / FLAME | 3753 | 0.555 / 0.542 | 0.545 |
| Mamajek 1.99 +- 0.10 (if E ~ 0.2) | 3748 +68/-92 | 0.535 +- 0.035 | 0.532 +0.025/-0.035 |

Planet properties from a Monte Carlo (N = 200 000):

| Quantity | Baseline (colour 2.19) | Mann M_Ks | E ~ 0.2 |
|---|---|---|---|
| Rp from k (Re) | 2.30 +0.20/-0.11 | 2.53 +- 0.08 | 2.69 +- 0.18 |
| Rp from box depth (Re) | 2.23 +0.19/-0.11 | 2.45 +- 0.08 | 2.60 +- 0.17 |
| a (AU) | 0.0862 +0.0028/-0.0017 | 0.0894 +- 0.0007 | 0.0911 +- 0.002 |
| a/R* | 40.4 +1.3/-2.0 | 38.1 +- 1.2 | 36.6 +- 1.7 |
| T_eq (A 0.3, full redistribution) (K) | 364 +17/-11 | 375 +- 12 | 401 +- 18 |
| R*/a | 2.48 +- 0.1 % | 2.62 +- 0.08 % | 2.73 +- 0.13 % |
| Central T14 (h) | 2.72 +0.14/-0.08 | 2.89 +- 0.09 | 3.01 +- 0.14 |
| b if 2.0 +- 0.25 h = T14 | 0.72 +0.09/-0.13 | 0.75 +0.08/-0.10 | 0.78 +0.07/-0.10 |
| b if 2.0 h = (T14+T23)/2 | 0.65 +0.10/-0.14 | 0.69 +0.08/-0.11 | 0.72 +0.08/-0.10 |
| T14 for fitted b 0.86 (h) | 1.55 +0.08/-0.05 | 1.64 +- 0.05 | 1.71 +- 0.08 |

Stellar density is 6.6 +0.6/-0.9 g/cm3 (5.5 with Mann) and the insolation about 2.9 S_earth (T_eq 364 to 400 K). The Mann and TIC values (0.50 Rsun) are probably closer to the truth than the colour-track baseline (0.46 Rsun), so Rp ~ 2.5 Re is the better estimate; the spread of 2.2 to 2.7 Re across the three cases is the systematic. An unresolved companion would dilute the depth and make the planet larger.

## What the checks support and cannot exclude

Supported:
- The dip is not on KIC 5689116: its aperture photometry shows only the expected leak (110 ppm against the > 1983 ppm it would need), its PSF photometry shows nothing, and the pixel fit puts 82 +- 35 ppm on it.
- The dip behaves as a source at the candidate's PSF position: the PSF depth is season invariant and SAP is shallower than PSF in step with aperture size and crowding.
- The host is an early-to-mid M dwarf (M2V, about 0.46 to 0.50 Rsun, 0.45 to 0.50 Msun) at 291 pc with clean Gaia astrometry and no Gaia variability or NSS solution.
- A 2.2 to 2.7 Re planet at 0.086 AU (P 13.77 d) fits the observed duration with b ~ 0.7.
- The signal is in no KOI, TCE, TOI, CTOI, EB or community catalogue reachable from VizieR, NEA, ExoFOP or SIMBAD.

Cannot exclude:
- An eclipsing binary within about one Kepler pixel (4 arcsec) of the candidate that Gaia does not resolve. It would need 1983 ppm x 10^(0.4 dm), for example 2.0 % at dm 2.5 or 12.5 % at dm 4.5.
- A bound companion. RUWE 0.984 and ipd_frac_multi_peak 0 disfavour bright companions at 0.1 to 1 arcsec, not faint or very close ones. There is no RV, imaging or spectroscopy in ExoFOP. The Planethunt pages state that Spitzer resolves the pair with KIC 5689116 but give no close-companion contrast curve.
- The six faint Gaia neighbours individually by their own light curves (none exist); they are excluded by the pixel localisation (3.7 to 7.1 px away from a dip localised to 0.3 px) and by the 3 to 10 % eclipses they would need.
- Extinction is uncertain until Bayestar is reachable, but the dust map at b = +15 deg bounds it at E(BP-RP) < 0.12, so the Rp range is 2.2 to 2.7 Re.

Not new: an independent, public, unrefereed claim of this candidate with the same period and the same host assignment exists (Planethunt, 11 to 13 October 2026, see C).

## Things that did not work

- Gaia: IN (sub-select) gave HTTP 400; redone with an explicit id list.
- Exoplanet Archive: q1_q17_dr25_stellar (both forms) and fpwg gave HTTP 400.
- VizieR: J/ApJ/754/129, J/AJ/145/151, J/ApJ/776/10 and J/AJ/148/28 "Table or Catalog not found" (by cone and by KIC id).
- SIMBAD TAP bibliography ADQL: HTTP 400 in both forms tried; the sim-id ASCII endpoint was used instead.
- Bayestar19: timed out (http and https, 5 attempts each).
- KBonus and Kepler mission light curves: HTTP 404 for all 6 faint neighbours.

## Files

Scripts, in run order (all with the venv python, PYTHONUTF8=1, PYTHONPATH=C:\Research\skysift): a1_download_lc.py, bc_queries.py, bc_queries2.py, c_queries3.py, d_dust.py, c_queries4.py, c_queries5.py, c_queries6.py, a2_leak_test.py, a3_neighbour_lcs.py, a4_centroid.py, b_neighbours.py, d_stellar_planet.py, e_assemble.py. Outputs: results.json (plus results_A/A3/A4/B/D.json), archive-proof/ (raw responses and manifest.json), downloads/kic005689116_llc/ (13 LLC files), downloads/neighbour_lcs/ (1 KBonus file), chain2_log.txt, manifest_table.md. The candidate's cached KBonus file in `.superpowers/sample-run/blobs/44/` was only read.

## Exact URLs queried

Kepler light curves of KIC 5689116: https://archive.stsci.edu/missions/kepler/lightcurves/0056/005689116/ and the 13 files listed at the end of the table. All other queries, with their full URL, size (or error) and SHA-256, are in archive-proof/manifest.json; the table below is generated from it (`manifest_table.md`).

| key | bytes | SHA-256 | URL |
|---|---|---|---|
| B_gaia_cone30 | 1732 B | 1652b8bb6386390022e4f40574c8e7d3847517a90d9269cab3ba6b8c815fb985 | https://gea.esac.esa.int/tap-server/tap/sync?request=doQuery&lang=ADQL&format=csv&query=SELECT+source_id%2Cra%2Cdec%2CDISTANCE%28POINT%28ra%2Cdec%29%2CPOINT%28285.90782692874626%2C40.94198844296448%29%29%2A3600+AS+sep_arcsec%2Cphot_g_mean_mag%2Cbp_rp%2Cparallax%2Cparallax_error%2Cparallax_over_error%2Cpmra%2Cpmdec%2Cruwe%2Cphot_variable_flag%2Cnon_single_star+FROM+gaiadr3.gaia_source+WHERE+1%3DCONTAINS%28POINT%28ra%2Cdec%29%2CCIRCLE%28285.90782692874626%2C40.94198844296448%2C0.00833333%29%29+ORDER+BY+sep_arcsec |
| B_gaia_source_candidate | 3915 B | 18d7212ec6d74e5c5fb68b92cd00d4a633111e750b5a175adf37b96ab3c52668 | https://gea.esac.esa.int/tap-server/tap/sync?request=doQuery&lang=ADQL&format=csv&query=SELECT+%2A+FROM+gaiadr3.gaia_source+WHERE+source_id%3D2103792461826460288 |
| B_gaia_source_neighbour | 273 B | a46763acbcef30c2b285d62aba60d6b7412b298d6b887320475129d5d32ef77e | https://gea.esac.esa.int/tap-server/tap/sync?request=doQuery&lang=ADQL&format=csv&query=SELECT+source_id%2Cra%2Cdec%2Cphot_g_mean_mag%2Cbp_rp%2Cparallax%2Cparallax_error%2Cruwe%2Cradial_velocity%2Cteff_gspphot%2Cphot_variable_flag%2Cnon_single_star+FROM+gaiadr3.gaia_source+WHERE+source_id%3D2103792427466721792 |
| B_gaia_astrophys_candidate | 5269 B | 9863454b67c1a48f070f13d9887b86aec4040682af428371da089e47d0c2a588 | https://gea.esac.esa.int/tap-server/tap/sync?request=doQuery&lang=ADQL&format=csv&query=SELECT+%2A+FROM+gaiadr3.astrophysical_parameters+WHERE+source_id%3D2103792461826460288 |
| B_gaia_vari_summary_candidate | 1173 B | 541a3503e473b547a20e01cabb5dd4892130e9960516bc17ebf57dad817cf555 | https://gea.esac.esa.int/tap-server/tap/sync?request=doQuery&lang=ADQL&format=csv&query=SELECT+%2A+FROM+gaiadr3.vari_summary+WHERE+source_id%3D2103792461826460288 |
| B_gaia_vari_classifier_result_candidate | 71 B | 47a46b8eaa3b4742d6e4549cac67d6f5fe2fa3fa5806b9830a409c26cd73d841 | https://gea.esac.esa.int/tap-server/tap/sync?request=doQuery&lang=ADQL&format=csv&query=SELECT+%2A+FROM+gaiadr3.vari_classifier_result+WHERE+source_id%3D2103792461826460288 |
| B_gaia_nss_two_body_orbit_candidate | 1273 B | 94624a15e17c05bdf5000d96375440e83eca594ad8fd6a175ca97630095c9aa2 | https://gea.esac.esa.int/tap-server/tap/sync?request=doQuery&lang=ADQL&format=csv&query=SELECT+%2A+FROM+gaiadr3.nss_two_body_orbit+WHERE+source_id%3D2103792461826460288 |
| B_gaia_vari_eclipsing_binary_candidate | 1079 B | 93cbc676ff20b178c1f3468781d80b3b7f64421f5892f788a105b39b9e210bcb | https://gea.esac.esa.int/tap-server/tap/sync?request=doQuery&lang=ADQL&format=csv&query=SELECT+%2A+FROM+gaiadr3.vari_eclipsing_binary+WHERE+source_id%3D2103792461826460288 |
| B_gaia_vari_summary_cone30 | HTTP error/timeout: gaia-tap https://gea.esac.esa.int/tap-server/tap/sync -> HTT | e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 | https://gea.esac.esa.int/tap-server/tap/sync?request=doQuery&lang=ADQL&format=csv&query=SELECT+t.%2A+FROM+gaiadr3.vari_summary+AS+t+WHERE+t.source_id+IN+%28SELECT+source_id+FROM+gaiadr3.gaia_source+WHERE+1%3DCONTAINS%28POINT%28ra%2Cdec%29%2CCIRCLE%28285.90782692874626%2C40.94198844296448%2C0.00833333%29%29%29 |
| B_gaia_vari_classifier_result_cone30 | HTTP error/timeout: gaia-tap https://gea.esac.esa.int/tap-server/tap/sync -> HTT | e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 | https://gea.esac.esa.int/tap-server/tap/sync?request=doQuery&lang=ADQL&format=csv&query=SELECT+t.%2A+FROM+gaiadr3.vari_classifier_result+AS+t+WHERE+t.source_id+IN+%28SELECT+source_id+FROM+gaiadr3.gaia_source+WHERE+1%3DCONTAINS%28POINT%28ra%2Cdec%29%2CCIRCLE%28285.90782692874626%2C40.94198844296448%2C0.00833333%29%29%29 |
| B_gaia_nss_two_body_orbit_cone30 | HTTP error/timeout: gaia-tap https://gea.esac.esa.int/tap-server/tap/sync -> HTT | e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 | https://gea.esac.esa.int/tap-server/tap/sync?request=doQuery&lang=ADQL&format=csv&query=SELECT+t.%2A+FROM+gaiadr3.nss_two_body_orbit+AS+t+WHERE+t.source_id+IN+%28SELECT+source_id+FROM+gaiadr3.gaia_source+WHERE+1%3DCONTAINS%28POINT%28ra%2Cdec%29%2CCIRCLE%28285.90782692874626%2C40.94198844296448%2C0.00833333%29%29%29 |
| B_gaia_vari_eclipsing_binary_cone30 | HTTP error/timeout: gaia-tap https://gea.esac.esa.int/tap-server/tap/sync -> HTT | e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 | https://gea.esac.esa.int/tap-server/tap/sync?request=doQuery&lang=ADQL&format=csv&query=SELECT+t.%2A+FROM+gaiadr3.vari_eclipsing_binary+AS+t+WHERE+t.source_id+IN+%28SELECT+source_id+FROM+gaiadr3.gaia_source+WHERE+1%3DCONTAINS%28POINT%28ra%2Cdec%29%2CCIRCLE%28285.90782692874626%2C40.94198844296448%2C0.00833333%29%29%29 |
| C_nea_cumulative_box | 115 B | 945d6a8d971a45d5281703ee3c18591c94d4a8a7e41effd2a0044e87812d86ed | https://exoplanetarchive.ipac.caltech.edu/TAP/sync?query=select+kepid%2Ckepoi_name%2Ckepler_name%2Ckoi_disposition%2Ckoi_pdisposition%2Ckoi_period%2Ckoi_time0bk%2Ckoi_depth%2Ckoi_duration%2Cra%2Cdec+from+cumulative+where+ra+between+285.896795+and+285.918859+and+dec+between+40.933655+and+40.950322&format=csv |
| C_nea_cumulative_kepid | 73 B | 20fff5165ebaf3f57da69990c409960c33fd44fa97e1fd9472b1ec0d283643e8 | https://exoplanetarchive.ipac.caltech.edu/TAP/sync?query=select+kepid%2Ckepoi_name%2Ckepler_name%2Ckoi_disposition%2Ckoi_period%2Ckoi_depth%2Cra%2Cdec+from+cumulative+where+kepid+in+%285689116%2C5689121%29&format=csv |
| C_nea_dr25tce_box | 2101 B | ed1d6952fb50da5cc9e5cd78c7eca0b44d6aaef613c1f18aee2a1953e8674d15 | https://exoplanetarchive.ipac.caltech.edu/TAP/sync?query=select+%2A+from+q1_q17_dr25_tce+where+ra+between+285.896795+and+285.918859+and+dec+between+40.933655+and+40.950322&format=csv |
| C_nea_dr25tce_kepid | 2101 B | ed1d6952fb50da5cc9e5cd78c7eca0b44d6aaef613c1f18aee2a1953e8674d15 | https://exoplanetarchive.ipac.caltech.edu/TAP/sync?query=select+%2A+from+q1_q17_dr25_tce+where+kepid+in+%285689116%2C5689121%29&format=csv |
| C_nea_dr24tce_kepid | 2101 B | ed1d6952fb50da5cc9e5cd78c7eca0b44d6aaef613c1f18aee2a1953e8674d15 | https://exoplanetarchive.ipac.caltech.edu/TAP/sync?query=select+%2A+from+q1_q17_dr24_tce+where+kepid+in+%285689116%2C5689121%29&format=csv |
| C_nea_toi_box | 1027 B | 7c6a6990c18ec203ed68512b56149fa3e1487e04d654ad13dcce2506f8986689 | https://exoplanetarchive.ipac.caltech.edu/TAP/sync?query=select+%2A+from+toi+where+ra+between+285.896795+and+285.918859+and+dec+between+40.933655+and+40.950322&format=csv |
| C_nea_dr25stellar_kepid | HTTP error/timeout: nasa-exoplanet-archive https://exoplanetarchive.ipac.caltech | e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 | https://exoplanetarchive.ipac.caltech.edu/TAP/sync?query=select+%2A+from+q1_q17_dr25_stellar+where+kepid+in+%285689116%2C5689121%29&format=csv |
| C_nea_keplerstellar_kepid | 336 B | 0017b402b3c07d64925dfc4b711bca4d45ac9bb79402c3f863f3247fb0909e6c | https://exoplanetarchive.ipac.caltech.edu/TAP/sync?query=select+kepid%2Cteff%2Clogg%2Cradius%2Cmass%2Ckepmag%2Cra%2Cdec+from+keplerstellar+where+kepid+in+%285689116%2C5689121%29&format=csv |
| C_vizier_keplerEB_kirk2016 | 4040 B | 8428c64f193442f7fbcb844a40163bf7649b41a4ae726ee9c52fbd9e1dfe33d7 | https://vizier.cds.unistra.fr/viz-bin/asu-tsv?-source=J%2FAJ%2F151%2F68%2Fcatalog&-c=285.90782692874626+%2B40.94198844296448&-c.rs=30&-out.all=&-out.add=_r&-sort=_r |
| C_vizier_KIC | 3512 B | 761132314e31a70738f5d65aafd7f711c197dae949bcc0ba2ef8f5825e675465 | https://vizier.cds.unistra.fr/viz-bin/asu-tsv?-source=V%2F133%2Fkic&-c=285.90782692874626+%2B40.94198844296448&-c.rs=30&-out.all=&-out.add=_r&-sort=_r |
| C_vizier_TICv82 | 11438 B | 0233e69553b75ed980fb13222091ceb6626a4d354c4657f8e32a4724dc884237 | https://vizier.cds.unistra.fr/viz-bin/asu-tsv?-source=IV%2F39%2Ftic82&-c=285.90782692874626+%2B40.94198844296448&-c.rs=30&-out.all=&-out.add=_r&-sort=_r |
| C_vizier_planethunters_schwamb2012 | 776 B | 520a012122159d9554e23fa7366d6c0423a0984a0a6a4ba95e453701fe9e9ca3 | https://vizier.cds.unistra.fr/viz-bin/asu-tsv?-source=J%2FApJ%2F754%2F129&-c=285.90782692874626+%2B40.94198844296448&-c.rs=30&-out.all=&-out.add=_r&-sort=_r |
| C_vizier_planethunters_lintott2013 | 774 B | cc07c7b890b58dacb58e7c4d8cbbec4d6c70e084eaedd334be50942771f58c14 | https://vizier.cds.unistra.fr/viz-bin/asu-tsv?-source=J%2FAJ%2F145%2F151&-c=285.90782692874626+%2B40.94198844296448&-c.rs=30&-out.all=&-out.add=_r&-sort=_r |
| C_vizier_planethunters_wang2013 | 774 B | 0be9713e6cad22aa7abff93bad907eb690d9952c2c1ec52e20b7d00aa2122868 | https://vizier.cds.unistra.fr/viz-bin/asu-tsv?-source=J%2FApJ%2F776%2F10&-c=285.90782692874626+%2B40.94198844296448&-c.rs=30&-out.all=&-out.add=_r&-sort=_r |
| C_vizier_planethunters_schmitt2014_PH6 | 772 B | fe37b8708ad5f72372770fd6d57c1f266c2fb986c6a440f046a6e2df29a4b425 | https://vizier.cds.unistra.fr/viz-bin/asu-tsv?-source=J%2FAJ%2F148%2F28&-c=285.90782692874626+%2B40.94198844296448&-c.rs=30&-out.all=&-out.add=_r&-sort=_r |
| C_vizier_planethunters_schmitt2014 | 771 B | 299b0798a0d35db6449407aae40905a3ca2c3219a369442b200154fbaed46dd4 | https://vizier.cds.unistra.fr/viz-bin/asu-tsv?-source=J%2FApJ%2F795%2F167&-c=285.90782692874626+%2B40.94198844296448&-c.rs=30&-out.all=&-out.add=_r&-sort=_r |
| C_vizier_kepler_fpp_morton2016 | 4325 B | 5158c5e188f5f3316ecfa4cb9da30cecfb97b095c4aa66c159677da529091255 | https://vizier.cds.unistra.fr/viz-bin/asu-tsv?-source=J%2FApJ%2F822%2F86&-c=285.90782692874626+%2B40.94198844296448&-c.rs=30&-out.all=&-out.add=_r&-sort=_r |
| C_simbad_cone30 | 947 B | 502cd5e00fe42db4aa9518e1c8f523373f7425fde9d6c99b15fc754f42d58bb4 | https://simbad.cds.unistra.fr/simbad/sim-coo?Coord=285.90782692874626+%2B40.94198844296448&Radius=30&Radius.unit=arcsec&output.format=ASCII&list.idsel=on |
| B_gaia_vari_summary_cone30_ids | 10 B | f6db5247aff1b01ec5aa88f24c7204a524a23efcdf81fb1bd13ca234533c090f | https://gea.esac.esa.int/tap-server/tap/sync?request=doQuery&lang=ADQL&format=csv&query=SELECT+source_id+FROM+gaiadr3.vari_summary+WHERE+source_id+IN+%282103792461826460288%2C2103792427466721792%2C2103792393102276480%2C2103792393108242176%2C2103792427462012544%2C2103792427462012032%2C2103792461821756544%2C2103792393102280576%29 |
| B_gaia_vari_classifier_result_cone30_ids | 10 B | f6db5247aff1b01ec5aa88f24c7204a524a23efcdf81fb1bd13ca234533c090f | https://gea.esac.esa.int/tap-server/tap/sync?request=doQuery&lang=ADQL&format=csv&query=SELECT+source_id+FROM+gaiadr3.vari_classifier_result+WHERE+source_id+IN+%282103792461826460288%2C2103792427466721792%2C2103792393102276480%2C2103792393108242176%2C2103792427462012544%2C2103792427462012032%2C2103792461821756544%2C2103792393102280576%29 |
| B_gaia_nss_two_body_orbit_cone30_ids | 10 B | f6db5247aff1b01ec5aa88f24c7204a524a23efcdf81fb1bd13ca234533c090f | https://gea.esac.esa.int/tap-server/tap/sync?request=doQuery&lang=ADQL&format=csv&query=SELECT+source_id+FROM+gaiadr3.nss_two_body_orbit+WHERE+source_id+IN+%282103792461826460288%2C2103792427466721792%2C2103792393102276480%2C2103792393108242176%2C2103792427462012544%2C2103792427462012032%2C2103792461821756544%2C2103792393102280576%29 |
| B_gaia_vari_eclipsing_binary_cone30_ids | 10 B | f6db5247aff1b01ec5aa88f24c7204a524a23efcdf81fb1bd13ca234533c090f | https://gea.esac.esa.int/tap-server/tap/sync?request=doQuery&lang=ADQL&format=csv&query=SELECT+source_id+FROM+gaiadr3.vari_eclipsing_binary+WHERE+source_id+IN+%282103792461826460288%2C2103792427466721792%2C2103792393102276480%2C2103792393108242176%2C2103792427462012544%2C2103792427462012032%2C2103792461821756544%2C2103792393102280576%29 |
| B_gaia_nss_non_linear_spectro_cone30_ids | 10 B | f6db5247aff1b01ec5aa88f24c7204a524a23efcdf81fb1bd13ca234533c090f | https://gea.esac.esa.int/tap-server/tap/sync?request=doQuery&lang=ADQL&format=csv&query=SELECT+source_id+FROM+gaiadr3.nss_non_linear_spectro+WHERE+source_id+IN+%282103792461826460288%2C2103792427466721792%2C2103792393102276480%2C2103792393108242176%2C2103792427462012544%2C2103792427462012032%2C2103792461821756544%2C2103792393102280576%29 |
| B_gaia_nss_acceleration_astro_cone30_ids | 10 B | f6db5247aff1b01ec5aa88f24c7204a524a23efcdf81fb1bd13ca234533c090f | https://gea.esac.esa.int/tap-server/tap/sync?request=doQuery&lang=ADQL&format=csv&query=SELECT+source_id+FROM+gaiadr3.nss_acceleration_astro+WHERE+source_id+IN+%282103792461826460288%2C2103792427466721792%2C2103792393102276480%2C2103792393108242176%2C2103792427462012544%2C2103792427462012032%2C2103792461821756544%2C2103792393102280576%29 |
| D_exofop_tic120686465 | 7431 B | d84fb27850877bd05491d7ede91670cbf5809cf2d2af0152e907e74f6c649b96 | https://exofop.ipac.caltech.edu/tess/target.php?id=120686465&json= |
| D_mamajek_dwarf_table | 55680 B | 1de2edeec17bb3346e0e4e70b999de5ee29947df38474e64cddb7cfacc164b7f | https://www.pas.rochester.edu/~emamajek/EEM_dwarf_UBVIJHK_colors_Teff.txt |
| D_bayestar19_a | HTTP error/timeout: bayestar http://argonaut.skymaps.info/api/v2/bayestar19/quer | e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 | http://argonaut.skymaps.info/api/v2/bayestar19/query?l=71.50258&b=15.25130&mode=percentile |
| D_bayestar19_b | HTTP error/timeout: bayestar https://argonaut.skymaps.info/api/v2/bayestar19/que | e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 | https://argonaut.skymaps.info/api/v2/bayestar19/query?lon=71.50258&lat=15.25130&coordsys=gal&mode=percentile |
| C_nea_dr25stellar_kepid_retry | HTTP error/timeout: nasa-exoplanet-archive https://exoplanetarchive.ipac.caltech | e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 | https://exoplanetarchive.ipac.caltech.edu/TAP/sync?query=select+kepid%2Cteff%2Clogg%2Cradius%2Cmass%2Ckepmag+from+q1_q17_dr25_stellar+where+kepid+in+%285689116%2C5689121%29&format=csv |
| C_nea_q1q16tce_kepid | 40 B | a7568f8c3f654f214c19f1bc88aaca28a1999d8e36ae3fefffb8cb527381bdfc | https://exoplanetarchive.ipac.caltech.edu/TAP/sync?query=select+kepid%2Ctce_plnt_num%2Ctce_period%2Ctce_depth+from+q1_q16_tce+where+kepid+in+%285689116%2C5689121%29&format=csv |
| C_nea_q1q12tce_kepid | 40 B | a7568f8c3f654f214c19f1bc88aaca28a1999d8e36ae3fefffb8cb527381bdfc | https://exoplanetarchive.ipac.caltech.edu/TAP/sync?query=select+kepid%2Ctce_plnt_num%2Ctce_period%2Ctce_depth+from+q1_q12_tce+where+kepid+in+%285689116%2C5689121%29&format=csv |
| C_nea_fpwg_kepid | HTTP error/timeout: nasa-exoplanet-archive https://exoplanetarchive.ipac.caltech | e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 | https://exoplanetarchive.ipac.caltech.edu/TAP/sync?query=select+kepid%2Ckepoi_name%2Cfpwg_disp_status+from+fpwg+where+kepid+in+%285689116%2C5689121%29&format=csv |
| D_irsa_dust | 2773 B | d4ee766cce3c044ca797ae25e448c1ef80c4f69c6a62158cf589a8950f816e82 | https://irsa.ipac.caltech.edu/cgi-bin/DUST/nph-dust?locstr=285.90782692874626+40.94198844296448+equ+j2000&regSize=2.0 |
| C_simbad_refs_candidate | HTTP error/timeout: simbad https://simbad.cds.unistra.fr/simbad/sim-tap/sync ->  | e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 | https://simbad.cds.unistra.fr/simbad/sim-tap/sync?request=doQuery&lang=ADQL&format=csv&query=SELECT+DISTINCT+r.bibcode%2C+r.title%2C+r.journal%2C+r.year+FROM+ref+AS+r+JOIN+has_ref+AS+h+ON+h.oidbibref+%3D+r.oidbib+JOIN+ident+AS+i+ON+i.oidref+%3D+h.oidref+WHERE+i.id+%3D+%27Gaia+DR3+2103792461826460288%27+ORDER+BY+r.year |
| C_simbad_ids_candidate | 34 B | 27ecffba05a78fcd0edef0bc1dc1a77bfb7b07ff1eec6f4be2072393a8b25122 | https://simbad.cds.unistra.fr/simbad/sim-tap/sync?request=doQuery&lang=ADQL&format=csv&query=SELECT+i2.id+FROM+ident+AS+i+JOIN+ident+AS+i2+ON+i.oidref+%3D+i2.oidref+WHERE+i.id+%3D+%27Gaia+DR3+2103792461826460288%27 |
| C_simbad_refs_neighbour | HTTP error/timeout: simbad https://simbad.cds.unistra.fr/simbad/sim-tap/sync ->  | e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 | https://simbad.cds.unistra.fr/simbad/sim-tap/sync?request=doQuery&lang=ADQL&format=csv&query=SELECT+DISTINCT+r.bibcode%2C+r.title%2C+r.journal%2C+r.year+FROM+ref+AS+r+JOIN+has_ref+AS+h+ON+h.oidbibref+%3D+r.oidbib+JOIN+ident+AS+i+ON+i.oidref+%3D+h.oidref+WHERE+i.id+%3D+%272MASS+J19033726%2B4056316%27+ORDER+BY+r.year |
| C_simbad_ids_neighbour | 121 B | f0a16821cd9a229d29540e8a9c5acab4de914b62c462f0d97ddc5e56213672f7 | https://simbad.cds.unistra.fr/simbad/sim-tap/sync?request=doQuery&lang=ADQL&format=csv&query=SELECT+i2.id+FROM+ident+AS+i+JOIN+ident+AS+i2+ON+i.oidref+%3D+i2.oidref+WHERE+i.id+%3D+%272MASS+J19033726%2B4056316%27 |
| C_vizier_planethunters_lintott2013_bykic | 716 B | 1cca1bbe5cd1429dd94441ec7997157e23e94005db321ad948991289f4962b99 | https://vizier.cds.unistra.fr/viz-bin/asu-tsv?-source=J%2FAJ%2F145%2F151&KIC=%3D5689121&-out.all= |
| C_vizier_planethunters_wang2013_bykic | 716 B | ff49383a17b2ce5dfd064045e24cd0447ee9da868f2d12e2c73552720fc7c95e | https://vizier.cds.unistra.fr/viz-bin/asu-tsv?-source=J%2FApJ%2F776%2F10&KIC=%3D5689121&-out.all= |
| C_vizier_planethunters_schmitt2014_PH6_bykic | 714 B | 6706ff547da863c615ed498b4bda1229ce99727b9de52524f4c1a588b37c31f4 | https://vizier.cds.unistra.fr/viz-bin/asu-tsv?-source=J%2FAJ%2F148%2F28&KIC=%3D5689121&-out.all= |
| C_vizier_planethunters_schwamb2012_bykic | 718 B | a3546fe0f19fe39f896de4af1f475d9a2374da76c66fc92277839f9d08047358 | https://vizier.cds.unistra.fr/viz-bin/asu-tsv?-source=J%2FApJ%2F754%2F129&KIC=%3D5689121&-out.all= |
| C_planethunt_site | 420060 B | 429e8ac2c0bac598e26703f4140319386baea0682e10b1c491f99f14caa97c4c | https://kuber.studio/planethunt/ |
| C_planethunt_github_release | 192630 B | b1cdcbecd593eb709dd6547d20d7b2238072a63309410e263c6d4a070a52598a | https://github.com/Kuberwastaken/planethunt/releases/tag/evidence-20261013 |
| C_planethunt_github_readme | 9580 B | 1898e457adfd8b88f681ad62b7c927e9daceb855e18658c8b3e54b79b4c2c93b | https://raw.githubusercontent.com/Kuberwastaken/planethunt/main/README.md |
| C_simbad_refs_candidate_retry | HTTP error/timeout: simbad https://simbad.cds.unistra.fr/simbad/sim-tap/sync ->  | e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 | https://simbad.cds.unistra.fr/simbad/sim-tap/sync?request=doQuery&lang=ADQL&format=csv&query=SELECT+ref.bibcode%2C+ref.year%2C+ref.journal%2C+ref.title+FROM+ref+JOIN+has_ref+ON+ref.oidbib+%3D+has_ref.oidbibref+JOIN+ident+ON+ident.oidref+%3D+has_ref.oidref+WHERE+ident.id+%3D+%27Gaia+DR3+2103792461826460288%27 |
| C_simbad_refs_neighbour_retry | HTTP error/timeout: simbad https://simbad.cds.unistra.fr/simbad/sim-tap/sync ->  | e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 | https://simbad.cds.unistra.fr/simbad/sim-tap/sync?request=doQuery&lang=ADQL&format=csv&query=SELECT+ref.bibcode%2C+ref.year%2C+ref.journal%2C+ref.title+FROM+ref+JOIN+has_ref+ON+ref.oidbib+%3D+has_ref.oidbibref+JOIN+ident+ON+ident.oidref+%3D+has_ref.oidref+WHERE+ident.id+%3D+%27KIC+5689116%27 |
| A3_kbonus_kic-005689116 | 8847360 B | 67ca0e7545f2ddb8bf051b30dabdf63a87fa6acc86d513f2ade5b1e6812382d3 | https://archive.stsci.edu/hlsps/kbonus-bkg/lcs/0056/005689116/hlsp_kbonus-bkg_kepler_kepler_kic-005689116_kepler_v1.0_lc.fits |
| A3_kbonus_gaia-dr3-2103792393102276480 | HTTP error/timeout: mast-neighbour-lc https://archive.stsci.edu/hlsps/kbonus-bkg |  | https://archive.stsci.edu/hlsps/kbonus-bkg/lcs/2103/2103792393102276480/hlsp_kbonus-bkg_kepler_kepler_gaia-dr3-2103792393102276480_kepler_v1.0_lc.fits |
| A3_kbonus_kic-005689129 | HTTP error/timeout: mast-neighbour-lc https://archive.stsci.edu/hlsps/kbonus-bkg |  | https://archive.stsci.edu/hlsps/kbonus-bkg/lcs/0056/005689129/hlsp_kbonus-bkg_kepler_kepler_kic-005689129_kepler_v1.0_lc.fits |
| A3_mast_dirlisting_005689129 | HTTP error/timeout: mast-kepler-lc https://archive.stsci.edu/missions/kepler/lig |  | https://archive.stsci.edu/missions/kepler/lightcurves/0056/005689129/ |
| A3_kbonus_kic-005689108 | HTTP error/timeout: mast-neighbour-lc https://archive.stsci.edu/hlsps/kbonus-bkg |  | https://archive.stsci.edu/hlsps/kbonus-bkg/lcs/0056/005689108/hlsp_kbonus-bkg_kepler_kepler_kic-005689108_kepler_v1.0_lc.fits |
| A3_mast_dirlisting_005689108 | HTTP error/timeout: mast-kepler-lc https://archive.stsci.edu/missions/kepler/lig |  | https://archive.stsci.edu/missions/kepler/lightcurves/0056/005689108/ |
| A3_kbonus_gaia-dr3-2103792427462012032 | HTTP error/timeout: mast-neighbour-lc https://archive.stsci.edu/hlsps/kbonus-bkg |  | https://archive.stsci.edu/hlsps/kbonus-bkg/lcs/2103/2103792427462012032/hlsp_kbonus-bkg_kepler_kepler_gaia-dr3-2103792427462012032_kepler_v1.0_lc.fits |
| A3_kbonus_kic-005689127 | HTTP error/timeout: mast-neighbour-lc https://archive.stsci.edu/hlsps/kbonus-bkg |  | https://archive.stsci.edu/hlsps/kbonus-bkg/lcs/0056/005689127/hlsp_kbonus-bkg_kepler_kepler_kic-005689127_kepler_v1.0_lc.fits |
| A3_mast_dirlisting_005689127 | HTTP error/timeout: mast-kepler-lc https://archive.stsci.edu/missions/kepler/lig |  | https://archive.stsci.edu/missions/kepler/lightcurves/0056/005689127/ |
| A3_kbonus_gaia-dr3-2103792393102280576 | HTTP error/timeout: mast-neighbour-lc https://archive.stsci.edu/hlsps/kbonus-bkg |  | https://archive.stsci.edu/hlsps/kbonus-bkg/lcs/2103/2103792393102280576/hlsp_kbonus-bkg_kepler_kepler_gaia-dr3-2103792393102280576_kepler_v1.0_lc.fits |
| C_simbad_bib_candidate | 1674 B | d31fc13553248299ccfb7f0f984989408dcbc5c75525b0fbd7f6618bd581111d | https://simbad.cds.unistra.fr/simbad/sim-id?Ident=Gaia+DR3+2103792461826460288&output.format=ASCII&obj.bibsel=on&list.bibsel=on&bibdisplay=refsum&bibyear1=1850&bibyear2=%24currentYear |
| C_simbad_bib_neighbour | 2967 B | 6fa94ac43ff02330b37e14e69b2ad9cd8c03b5944e568fc5a4130fdb101eb3fe | https://simbad.cds.unistra.fr/simbad/sim-id?Ident=KIC+5689116&output.format=ASCII&obj.bibsel=on&list.bibsel=on&bibdisplay=refsum&bibyear1=1850&bibyear2=%24currentYear |

Kepler light curves of KIC 5689116 (A_downloads_manifest.json):

| file | bytes | SHA-256 |
|---|---|---|
| https://archive.stsci.edu/missions/kepler/lightcurves/0056/005689116/kplr005689116-2009166043257_llc.fits | 192960 | 133d2bae45a0b6af5722cfe754c6a3098e510afb71a790b37e356de37d6def4d |
| https://archive.stsci.edu/missions/kepler/lightcurves/0056/005689116/kplr005689116-2009259160929_llc.fits | 466560 | 868f08f797d11d89439d6aa1e4579ff6ecc73b65d6bd7416a22271589efdb86f |
| https://archive.stsci.edu/missions/kepler/lightcurves/0056/005689116/kplr005689116-2009350155506_llc.fits | 466560 | 0a576a43a499a07f3dec2c8f723d2cf21ac211d2ae12dd1a6f9754601f89c687 |
| https://archive.stsci.edu/missions/kepler/lightcurves/0056/005689116/kplr005689116-2010078095331_llc.fits | 469440 | 912d9df8451f4bce2fc45f0950b21a8b26cf7a1ed96084f3f0930072dcba169e |
| https://archive.stsci.edu/missions/kepler/lightcurves/0056/005689116/kplr005689116-2010265121752_llc.fits | 469440 | 245cce470e83b5b90a8df910cfa7be63d86fe64d460f54856d5fe05bba966a2f |
| https://archive.stsci.edu/missions/kepler/lightcurves/0056/005689116/kplr005689116-2010355172524_llc.fits | 466560 | df203c17ed1ad2972bd400ae0a7e7ce8b5c4e81424c94b6b23539f325165f323 |
| https://archive.stsci.edu/missions/kepler/lightcurves/0056/005689116/kplr005689116-2011073133259_llc.fits | 357120 | 8c818dcbd0f97e9b82174c02ed07ce8933539d3b6dc2184bb2bcb484c8a69e4c |
| https://archive.stsci.edu/missions/kepler/lightcurves/0056/005689116/kplr005689116-2011271113734_llc.fits | 486720 | 118eefdf8412786dce044e0f2d6e3d6f5c0a049061837b0ba4b59fafa53a5f73 |
| https://archive.stsci.edu/missions/kepler/lightcurves/0056/005689116/kplr005689116-2012004120508_llc.fits | 506880 | 03ca8af46571eff44735bcc60e28d822b7267f695702ee0200a8e25fecfec4ba |
| https://archive.stsci.edu/missions/kepler/lightcurves/0056/005689116/kplr005689116-2012088054726_llc.fits | 434880 | 374ff6b2b9f0651a2c7520149dc5242b80e6a1d1524f4ab6924d761665e1ef76 |
| https://archive.stsci.edu/missions/kepler/lightcurves/0056/005689116/kplr005689116-2012277125453_llc.fits | 506880 | cbc2625f4fceb04f89b71ed659bcee11d3c9f9d5f8d3062e297a10ed4e54451f |
| https://archive.stsci.edu/missions/kepler/lightcurves/0056/005689116/kplr005689116-2013011073258_llc.fits | 506880 | f3b615da0beed34e20d4130b1fd9e1d2bd9bd4abfdaf09d7180a7b7f45d9c8cf |
| https://archive.stsci.edu/missions/kepler/lightcurves/0056/005689116/kplr005689116-2013098041711_llc.fits | 449280 | c6071bdeb29bab24cd223e6cfcb755ac8bcdd06007365846c31675e97dc1fa38 |