# TRICERATOPS cross-check of SkySift's false-positive probabilities

Date: 22 October 2026. Spec `docs/superpowers/specs/2026-10-22-skysift-referee-pack-design.md`, section 4 and rulings (n) to (v). Tool `tools/triceratops_check.py` (commits d5bced3, 379ed2d, e9fcb25, 9b2a0e4 and 64eac25), run from the 64eac25 snapshot on the 64eac25 exports. Results in `.superpowers/sdd/2026-10-22-skysift-referee-pack/live/results/`; the 47.69 d result of KIC 6699887 is copied beside this note as `2026-10-22-fpp-cross-check-kic-006699887-3480.json`.

## 1. What was run and why

SkySift computes its own false-positive probability (FPP) from four scenarios (planet, background eclipsing binary, eclipsing binary at P and at 2P) with simple priors. A referee will ask for a number from a tool the community already uses, so TRICERATOPS 1.1.0 (Giacalone et al. 2021) was run in Kepler mode as an independent cross-check.

The first version of the tool (57a0465) let the package split the light by its Gaussian PSF over the 3 x 3 Kepler box in the TESS band, as for aperture photometry. That does not describe KBonus PSF photometry (Martinez-Palomera et al. 2023). After the parent session's audit the protocol is:

- **Flux shares.** The neighbours' leak into the target's light curve is bounded by the KBonus contamination budget, (1 - C)/C of the target's own flux, with C the CROWDSAP of the target's KBonus file (the target's flux ratio is C). The budget is shared among the neighbours by the package's Gaussian-box fractions, re-weighted into the Kepler band by Gaia G. The PSF fit removes this light to first order, so every share, and the nearby false-positive probability (NFPP) with it, is an upper bound. No star is removed on the strength of SkySift's own centroid test, which would make the check circular.
- **Two FPPs.** The full FPP and the FPP without STP (a planet on an unresolved bound companion), (FPP - P_STP)/(1 - P_STP). With no imaging the package lets such a companion sit anywhere inside 2.2 arcsec. SkySift's model has no STP scenario, so the second number is the like-for-like one.
- **Neighbour parameters.** Gaps in the TIC are filled from Gaia DR3 (GSP-Phot Teff and radius, FLAME mass where present, else the Pecaut and Mamajek 2013 dwarf table by BP-RP), the source recorded per star.
- **Convergence.** 20 runs (seeds 1 to 20) at 1e6 draws on the package's `parallel=True` path, one seed checked against the serial loop at 20,000 draws. The median and the 16th to 84th percentiles are quoted. No run was degenerate.
- **Light curve.** The whitened KBonus flux within three durations of each transit, points beyond 5 raw MAD from a box model clipped (7, 40, 18 and 10 for the four signals), with the robust out-of-transit scatter (1.4826 x MAD) as the error.
- **Fixed settings.** Kepler limb darkening (the package's table), long cadence 0.0204 d, dilution-corrected depths, a 3 x 3 aperture, a 10-pixel search radius, the package's priors and its default Gaia DR3 field-star population.

Each result file keeps every run, the median run's scenario table, the stars with their shares and parameter sources, the caveats and these settings.

Giacalone et al. 2021 (abstract and section 4) draw three lines: validated (FPP < 0.015 and NFPP < 1e-3), likely planet (FPP < 0.5 and NFPP < 1e-3) and likely nearby false positive (NFPP > 0.1). Anything else is a candidate. These are labels only and never block the gate.

## 2. Results per star

The medians and ranges, with SkySift's own FPP from the live dossier, are summarised in the table below:

| Signal | Full FPP | FPP without STP | NFPP | SkySift FPP | Class |
|---|---|---|---|---|---|
| KIC 6146676, 40.29 d | 0.076 (0.067 to 0.100) | 0.025 (0.016 to 0.035) | 0.023 (0.015 to 0.033) | 0.0035 | candidate |
| KIC 5689121, 13.77 d | 0.127 (0.110 to 0.141) | 0.047 (0.034 to 0.054) | 0.043 (0.030 to 0.049) | 0.0016 | candidate |
| KIC 6699887, 15.11 d (3479) | 0.030 (0.024 to 0.037) | 0.014 (0.011 to 0.016) | 0.010 (0.008 to 0.012) | 0.0039 | candidate |
| KIC 6699887, 47.69 d (3480) | 0.042 (0.034 to 0.046) | 0.020 (0.016 to 0.024) | 0.017 (0.012 to 0.020) | 0.064 | candidate |

Every FPP is below 0.5 and every NFPP sits between 1e-3 and 0.1, so all four are candidates: not likely planets and not likely nearby false positives. The shares behind the NFPP are given in the table below:

| Signal | CROWDSAP C | Budget (1 - C)/C | Dominant neighbour | Its share | Its flux ratio | Eclipse it would need |
|---|---|---|---|---|---|---|
| KIC 6146676 | 0.8565 | 0.1675 | KIC 6146666 (TIC 169822655), 11.5 arcsec, G 12.81 | 0.1615 (96 per cent) | 0.138 | 1.35 per cent |
| KIC 5689121 | 0.7472 | 0.3382 | KIC 5689116 (TIC 120686466), 7.1 arcsec, G 15.48 | 0.3382 (all) | 0.253 | 0.60 per cent |
| KIC 6699887 (both) | 0.8849 | 0.1301 | TIC 270864061 (Gaia DR3 2077554781499349120), 9.7 arcsec, G 15.09 | 0.1291 (99 per cent) | 0.114 | 0.90 (3479), 0.77 (3480) per cent |

### 2.1 KIC 6146676 (40.29 d)

STP is most of the full FPP (0.055 of 0.076). The NFPP of 0.023 is almost entirely an eclipsing binary on KIC 6146666 (NEB, 0.020 in the median run), the F star 23 times brighter than the target: a 1.35 per cent eclipse on it would make the dip if 96 per cent of the budget really leaked. SkySift's leak test sees nothing in KIC 6146666's aperture (-9 +- 12 ppm), but it looks in the opposite direction, so it stays a consistency check (ruling n).

### 2.2 KIC 5689121 (13.77 d)

This star has the largest budget (a quarter of the aperture light is contaminating), and all of it goes to KIC 5689116, the G star 7.1 arcsec away and 2.2 times brighter. The NFPP of 0.043 is a planet transiting that star (NTP, 0.030 in the median run), and STP takes 0.083 of the 0.127. The 130 +- 42 ppm dip in KIC 5689116's own aperture is the candidate's light leaking outward, at the size the crowding predicts.

The PlanetHunt pack (Kuber Mehta, DOI 10.5281/zenodo.23325261, 13 October 2026) records a TRICERATOPS FPP of about 0.07 (`docs/candidates/kic-005689121/comparison.md`), with no settings, band, shares or run count recorded. It sits below this 16th percentile (0.110) and above the FPP without STP (0.047), and without its settings the gap cannot be traced further. The pixel tests agree with its host assignment in every estimator. SkySift's result for this star is an independent blind recovery and vetting of Kuber Mehta's prior claim, never a new candidate.

### 2.3 KIC 6699887 (15.11 d and 47.69 d)

Both periods share the star, the budget and the dominant neighbour (TIC 270864061, 2.8 times brighter in G). At 15.11 d the NFPP of 0.010 is a planet on that neighbour (NTP, 0.008 in the median run) and STP is 0.016. At 47.69 d the NFPP is 0.017 (NTP 0.020) and STP 0.021. Under the old protocol the single 15.11 d run gave an NFPP of 0.135 (all of it NTP on TIC 270864061), which would have put the star in the "likely nearby false positive" class: the old box gave that neighbour 0.303 of the light, so a 0.27 per cent transit on it would do, while its share of the 0.130 budget leaves it a flux ratio of 0.114 and needs 0.90 per cent.

SkySift's FPP for 3480 (0.064) comes from a dossier generated for that signal on a database copy and regenerated at version 4 from the branch's final code, where it stays at 0.064. It is the one case where SkySift's number is the higher: over 28 transits its secondary (89 +- 77 ppm) and odd-even difference (236 +- 168 ppm) cannot yet rule out an eclipsing binary at P or 2P.

## 3. Why the full FPP sits far above SkySift's own

For the three live dossiers the full FPP is 8 to 79 times SkySift's FPP, and the FPP without STP still 3.5 to 30 times. The contamination budget is an upper bound (the PSF fit removes most of that light), yet the package's NEB and NTP scenarios take the whole of it, almost all on one bright neighbour 7 to 12 arcsec away. STP, the largest single term, has no counterpart in SkySift's model. Further, SkySift's number answers a different question: its pixel rung has already placed the dip on the target within a 2 arcsec confusion radius, so its background-binary scenario is confined to that radius, while TRICERATOPS here keeps every neighbour in play. No result is "validated": Giacalone et al. 2021 found no validation region at 30-minute cadence (section 4.2 and figure 15), and Kepler long cadence is 29.4 minutes.

## 4. Before and after

The before numbers are one run each (seed 1, serial loop, 1e6 draws) with the tool at 57a0465: the package's Gaussian-box shares in the TESS band and TIC parameters only (the package's estimate for the gaps). They ran on the current exports, so the window, clip and error match the after runs. The two are compared in the table below:

| Signal | Before FPP | Before FPP without STP | Before NFPP | Before neighbour light (box) | After FPP | After FPP without STP | After NFPP | After budget |
|---|---|---|---|---|---|---|---|---|
| KIC 6146676 | 0.033 | 0.0001 | 2.4e-7 | 0.384 (KIC 6146666 0.365, eclipse 0.37 per cent) | 0.076 (0.067 to 0.100) | 0.025 | 0.023 (0.015 to 0.033) | 0.168 (eclipse 1.35 per cent) |
| KIC 5689121 | 0.135 | 0.050 | 0.045 | 0.352 (KIC 5689116 0.352, eclipse 0.37 per cent) | 0.127 (0.110 to 0.141) | 0.047 | 0.043 (0.030 to 0.049) | 0.338 (eclipse 0.60 per cent) |
| KIC 6699887 (3479) | 0.147 | 0.139 | 0.135 | 0.305 (TIC 270864061 0.303, eclipse 0.27 per cent) | 0.030 (0.024 to 0.037) | 0.014 | 0.010 (0.008 to 0.012) | 0.130 (eclipse 0.90 per cent) |
| KIC 6699887 (3480) | not run | | | | 0.042 (0.034 to 0.046) | 0.020 | 0.017 (0.012 to 0.020) | 0.130 |

For KIC 6146676 the new shares raised the NFPP. The old box gave KIC 6146666 0.365 of the light, so it needed only a 0.37 per cent eclipse, and no binary on it fitted (NEB below 1.4e-7). The budget leaves it 0.138, the eclipse becomes 1.35 per cent, and at that depth the NEB carries 0.020. The single before run is also low on STP (0.032 against a median of 0.055). For KIC 5689121 before and after agree within the after range. The audit's trial NFPPs of 0.13 to 1.0 came with the pipeline error on version-2 exports; with the robust error and the clip the old shares already give 0.045.

## 5. Caveats and follow-ups

- **One dossier for two signals.** The dossier is one per target (checklist row 27), so only signal 3479's result is stored on KIC 6699887's dossier. The 3480 result lives beside this note as `docs/superpowers/notes/2026-10-22-fpp-cross-check-kic-006699887-3480.json` (a byte copy, SHA-256 d2feb33c...).
- **Seed reproducibility.** Before 64eac25 one seed did not repeat across starts (seed 2 NFPP 3.22e-10 in three starts, 3.129e-10 in one). With a sorted copy of the field-star population written per result, two fresh starts stored identical values to every digit (run 2 FPP 5.2159456378597646e-05, NFPP 3.219358914357731e-10). Each copy's row count and hash are in `settings.field_stars` (8,883, 3,165, 6,782 and 6,782 rows).
- **The parallel path is numpy vectorisation**, not multiprocessing, about 1.5 times as fast as the serial loop (19 s against 29 s at 20,000 draws). The check at seed 1 and 20,000 draws is given in the table below:

| Signal | FPP serial / parallel | NFPP serial / parallel | NFPP noise (sd over 4 seeds) | Gap |
|---|---|---|---|---|
| KIC 6146676 | 0.01745 / 0.01740 | 2.56e-4 / 2.04e-4 | 9.8e-5 | 0.5 sd |
| KIC 5689121 | 0.08446 / 0.08446 | 1.79e-5 / 1.79e-5 | 0.020 | 0.0 sd |
| KIC 6699887 (3479) | 0.000389 / 0.000389 | 3.81e-4 / 3.81e-4 | 0.015 | 0.0 sd |
| KIC 6699887 (3480) | 0.02412 / 0.02412 | 0.0227 / 0.0227 | 0.015 | 0.0 sd |

  The FPPs agree to 3 decimals and the NFPPs within the Monte Carlo noise, which at 20,000 draws is as large as the values.
- **Bands.** The target's KIC Kp is not stored, so the Gaia G proxy is unchecked, and the package's bound-companion and background dilution stay in the TESS band.
- **What would lower the NFPP.** A measured leak per neighbour (a dip injected into the neighbour in the KBonus PSF scene) in place of the budget, and a contrast curve (Gaia-based or from imaging) to bound STP.

## 6. References

- Giacalone, S., Dressing, C. D., Jensen, E. L. N., et al. 2021, AJ 161, 24 (TRICERATOPS; classes in the abstract and section 4, cadence in section 4.2 and figure 15).
- Martinez-Palomera, J., Hedges, C., and Dotson, J. 2023, AJ 166, 265 (arXiv:2310.17733; the KBonus light curves, CROWDSAP and PSFFRAC).
- Gaia Collaboration, Vallenari, A., et al. 2023, A&A 674, A1 (Gaia DR3: neighbour photometry and parameters, and the field-star population).
- Pecaut, M. J., and Mamajek, E. E. 2013, ApJS 208, 9 (dwarf table for parameters filled by colour).
