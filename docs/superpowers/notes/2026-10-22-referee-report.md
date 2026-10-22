# Referee report: two SkySift Research Notes of the AAS and their Zenodo packs

Notes reviewed: "A 40.291 day transit candidate on KIC 6146676" and "An independent recovery of the 13.769 day transit candidate on KIC 5689121", with their packs (README, ExoFOP files, archive email, Zenodo metadata, data, provenance, figures), built 22 October 2026 from commit fb9b2e1, and the stored dossiers behind them. Background notes were read only after the packs.

## 1. Summary

Both notes report a transit-like signal of about 2,000 ppm in the KBonus Kepler Background PSF photometry of an M dwarf at about 300 pc that Kepler recorded only in a neighbour's postage stamp: 40.29 d, 33 transits, S/N 30 on KIC 6146676, and 13.77 d, 65 transits, S/N 28 on KIC 5689121. Each note claims (a) a periodic dip with a measured ephemeris and shape, (b) difference imaging that puts the dip on the target and not on the Kepler target that owns the stamp, (c) a planet of about 2.5 to 2.6 Earth radii, and (d) a false-alarm probability against null searches and a false-positive probability "under 1 percent, from a scenario estimate". I reproduce the ephemerides, the depths, the odd-even results, the S/N, the stellar radius and mass, the insolation and equilibrium temperature, the TESS prediction and the next transit windows from the pack files (section 3). The signals are real and periodic, and the neighbour that owns each stamp is excluded as the host at high significance by the pack's own two-source fit and leak test (4.5 and 8 sigma for KIC 6146676, 25 and 20 sigma for KIC 5689121, section 3). Those are solid results. Several claims as worded go further than the evidence. The abstract's "false-positive probability at under 1 percent" rests on the authors' own four-scenario model, while TRICERATOPS, run by the authors themselves and reported two sections later, gives 0.076 and 0.13 (0.025 and 0.047 even with the bound-companion channel removed). The transit shape comes from a uniform-disc model without limb darkening whose a/R* (43.6 and 25.7) implies a stellar density six and three times lower than the star the radius is computed for. My limb-darkened refit shows that b is unconstrained and that the data agree with the stellar density. The README and the ExoFOP file print two different a/R* under the same name. The false-alarm probability is described as coming from "null searches of this source", but the nulls are 999 other stars, and no look-elsewhere correction is applied to a candidate found in a blind sweep. The self-referenced centroid test, quoted as the Bryson et al. (2013) test, has no power for KIC 6146676 because its reference is pulled by a neighbour 23 times brighter.

The KIC 5689121 note has one more problem, the most serious in either submission. It states that SkySift "found it again in a blind sweep, so this is an independent recovery". The repository says otherwise. A golden test pinning the prior claim's period, epoch and depth (`tests/golden/test_kic5689121_lc.py`, `P_PUB, T0_PUB = 13.769448251493595, 2455630.245430646 - 2454833.0`, "the published depth (1883 ppm...)") was committed on 14 October 2026 (2e32b73), the day after Mehta's 13 October record. The target-selection rule that admitted this star to the sweep (41394ee, 19 October) names it as its motivating case. The note also never compares its parameters with Mehta's, which is the main content a recovery note should carry. They agree (section 3). **Recommendations: KIC 6146676, major revision. KIC 5689121, major revision.** The 5689121 note is publishable as a confirmatory analysis that discloses its history and compares with the prior claim. As worded it would be a reject on the independence claim alone. Neither note needs new data. Every request below can be met from the pack's own files and code, though the shape refit and the FPP rewording will cost words that must be found elsewhere (finding 13).

## 2. Findings, ranked by threat to the claim

Severity: **Major** (a claim as worded is not supported), **Moderate** (an analysis gap a referee would insist on), **Minor** (consistency, presentation, completeness). Each finding says which note it applies to: [6146676], [5689121] or [both].

### 1. Major [5689121]. The "blind sweep" and "independent recovery" claims are contradicted by the code history, and the note never compares with the prior claim

- Text: note.tex, signal section: "SkySift found it again in a blind sweep, so this is an independent recovery and vetting of that claim, not a new candidate." The README, the ExoFOP note, the planet file's notes field, the archive email and the Zenodo description say the same.
- What is wrong:
  - `tests/golden/test_kic5689121_lc.py` pins `P_PUB, T0_PUB = 13.769448251493595, 2455630.245430646 - 2454833.0`. It asserts that the detector recovers 13.7695 d within 0.002 d and that the depth agrees with "the published depth (1883 ppm, measured with a 1-day detrend window)". It also keeps a "Regression pin of the engine's own measurement ... update knowingly if detrending changes".
  - The test was added on 14 October 2026 (commit 2e32b73, "golden light-curve test on KIC 5689121"), the day after the prior claim, and it was updated on 19 October (24a8349).
  - `src/skysift/sources/kbonus_bkg.py` names this star as the case behind the selection change of 19 October (41394ee): "a KIC-numbered star extracted from a neighbour's pixels (KIC 5689121 in the pixels of KIC 5689116) was never searched and is kept like a no-KIC star".
  - So the detrending, the detector and the target list were developed with this signal as a known answer. That is legitimate engineering, but it is not a blind search, and "independent" cannot be claimed for the detection. It can still be claimed for the vetting, to the extent that the vetting rungs were not tuned on it.
- What is missing: a recovery note's main content is the comparison with the earlier claim, and there is none. The values the golden test labels as published are P = 13.769448 d, T = BJD_TDB 2455630.24543 and 1,883 ± 106 ppm, which I take to be Mehta's. The note's P = 13.769414 ± 0.000048 d, T_ref = 2455630.2462 ± 0.0014 and depth 2,031 ± 113 ppm agree within 0.7, 0.5 and 1.0 sigma (section 3). The cross-check note (section 2.2) also records Mehta's TRICERATOPS FPP of about 0.07, which the note does not mention.
- Ask:
  - Remove "blind" and "independent recovery". Say instead that SkySift's ladder was applied to a signal reported by Mehta (2026), that the pipeline was developed with this star as a test case from 14 October, and what that means for the detection.
  - Add one sentence or a short parenthesis comparing P, T0, depth and FPP with the prior claim, with the differences in sigma.
  - Keep the priority statement in the abstract with a citation (the abstract names "Kuber Mehta" without citing the record).
- Precedent: confirmatory analyses of community or citizen-science candidates state the discovery source and compare parameters, for example the Planet Hunters papers (Schwamb et al. 2013, ApJ 768, 127; Schmitt et al. 2014, AJ 148, 28) and TESS community-TOI follow-up papers. The AAS ethics policy asks that prior work be credited accurately, including how it shaped the present work.

### 2. Major [both]. The false-positive probability headline ("under 1 percent") is not the number the evidence supports

- Text: abstract, "a scenario estimate puts the false-positive probability at under 1 percent". The FPP section: "False-positive probability: under 1 percent, from a scenario estimate", followed by TRICERATOPS FPP 0.076 (0.067 to 0.100) for KIC 6146676 and 0.13 (0.11 to 0.14) for KIC 5689121. The same headline sits in `zenodo_metadata.json`, `exofop/note.txt` and `archive_email.txt`; the Zenodo description omits TRICERATOPS altogether.
- What is wrong:
  - The established tool, run by the authors, gives an FPP 8 to 80 times their own. Even with STP removed it gives 0.025 and 0.047, 7 and 30 times the SkySift value and above 1 percent in both cases.
  - SkySift's model (`src/skysift/fpp.py`) has four scenarios: planet, BEB, EB at P and EB at 2P. It has no planet or binary on an unresolved bound companion (STP, SEB, the hierarchical triple of Morton 2012) and no diluted-planet scenarios.
  - Its BEB term is built "per bright neighbour", that is per Gaia-listed source. With none inside the 2 arcsec confusion radius, the BEB weight comes from Gaussian centroid tails of 1e-68 and 1e-75 at 26 to 40 sigma (dossier `fpp.neighbours[].exclusion`), and fainter unlisted background stars are not counted at all.
  - A probability set by 26-sigma Gaussian tails is not meaningful. Centroid systematics are not Gaussian at that level (finding 6).
  - The note also omits the 30-minute caveat that the pack's own `cross_check.json` carries: Giacalone et al. (2021, Fig. 15) found no validation region at 30-minute cadence. A reader of "the medians fall in none of the paper's classes" is not told that one class cannot apply.
- On the other side, the TRICERATOPS NFPP is conservative, and the authors can say so with their own data:
  - For KIC 6146676 the NFPP is almost all an NEB on KIC 6146666, which needs a 1.35 percent eclipse on that star. For KIC 5689121 it is an NTP on KIC 5689116, which needs a 0.60 percent transit.
  - The leak test sees -9 ± 12 and 130 ± 42 ppm in those stars' own apertures, and the two-source fit gives -23 ± 25 and 76 ± 36 ppm on them, against the 93 and 968 ppm they would need to host the whole dip (section 3).
  - Giacalone et al. (2021) remove stars excluded by follow-up photometry before computing the NFPP. A run with the excluded neighbour removed, labelled as such, would be a fair second number.
- Ask:
  - In the abstract, quote the TRICERATOPS FPP (with and without STP) or no FPP at all. Do not quote the in-house number alone.
  - Present the in-house estimate as a lower bound that omits named scenarios.
  - Add the 30-minute caveat in one clause.
  - Optionally rerun TRICERATOPS with the excluded neighbour removed.
  - Apply the same wording to the Zenodo, ExoFOP and archive texts.
- Precedent: Morton (2012) and Morton et al. (2016, ApJ 822, 86) validate at FPP < 1 percent only with the full scenario set and, where available, imaging constraints. Giacalone et al. (2021) define their classes on FPP and NFPP together.

### 3. Major [6146676], Moderate [5689121]. The transit shape is a uniform-disc artefact, and a/R* is quoted twice with two meanings

- Text:
  - 6146676 note: "a radius ratio of 0.0478 ... an impact parameter of 0.893+0.030-0.063 and a/R* of 43.6+9.7-6.1". The README table: "scaled semi-major axis a/R* | 77.9 +2.5/-2.3". `exofop/params_planet_20261022_001.txt`: `ar_star 43.6, ar_star_err 9.7`.
  - 5689121: b = 0.86 and a/R* = 25.7 "no error" in the note, and a/R* 38.1 in the README.
- What is wrong:
  - At P = 40.29 d, a/R* = 43.6 implies a circular-orbit stellar density of 0.97 g/cm3, against 5.51 g/cm3 for the star the planet radius is computed for, a ratio of 0.18. For KIC 5689121 the ratio is 0.31 (1.70 against 5.53 g/cm3).
  - With the stellar a/R* (77.9) and the fitted T14 (3.86 h), a circular orbit needs b ≈ 0.38. At the fitted b = 0.89 the transit would last 2.16 h, not 3.86 h. For KIC 5689121 the circular solution is b ≈ 0.57, and at b = 0.86 the duration would be 1.64 h against 2.43 h.
  - Taken at face value the fits say the transit is too long for the host: an eccentric orbit transiting near apastron, or a larger, less dense host, which is a false-positive signature.
  - The likelier explanation is that the data do not constrain b, and the uniform disc has picked one point on a flat valley. I refit the folded light curve (±0.5 d, 5-minute bins, errors from the out-of-transit scatter, 11-point integration over the 29.4-minute cadence) with a uniform disc and with quadratic limb darkening (u1 = u2 = 0.33), with a/R* free and fixed at the stellar value.
    - KIC 6146676: every fit lies within Δchi2 = 2.1 of every other for 280 bins at chi2 = 289. The uniform disc with a/R* free finds b = 0.10 and a/R* = 94, a different minimum from the note's b = 0.89. Limb darkening with a/R* fixed at 77.9 (b = 0.47) fits as well as anything (Δchi2 = -0.13 against limb darkening free).
    - KIC 5689121: the uniform disc reproduces the note's high-b solution (b = 0.93, a/R* = 18). Limb darkening at the stellar a/R* (b = 0.57) fits equally well (Δchi2 = +0.05).
    - So there is no density tension once the model is reasonable, but the note's b and a/R* are not measurements either.
    - k moves with the model: 0.0453 to 0.0473 for KIC 6146676 (the note has 0.0478) and 0.0448 to 0.0485 for KIC 5689121 (the note has 0.0461). With limb darkening and the density fixed, Rp is 2.50 and 2.56 Earth radii against the quoted 2.63+0.11-0.10 and 2.54+0.29-0.11. The model systematic on Rp, about 4 to 5 percent, is as large as the quoted statistical error.
  - The degeneracy flag is applied inconsistently. KIC 5689121 is marked "ingress shorter than the cadence: b and a/R* are degenerate with k". KIC 6146676 escapes the flag only because the fitted b = 0.89 gives a 46-minute ingress. At the density-consistent b ≈ 0.38 its ingress is about 13 minutes, under the 29.4-minute cadence, the same condition. Its quoted b and a/R* errors (+0.030/-0.063, +9.7/-6.1) are therefore not measurements.
  - The planet radius inherits the problem through k.
  - For KIC 5689121 the quoted intervals are centred on the point estimate, while the bootstrap distribution is centred elsewhere: T14 2.43 h against a bootstrap median of 2.79 h, k 0.0461 against 0.0477 (`data/shape.csv`). "2.43+0.34-0.47 h" spans 1.96 to 2.77 h, while the bootstrap's 16th to 84th percentiles span 2.32 to 3.13 h, so the point sits near the bootstrap's 16th percentile.
- Ask:
  - Refit with quadratic limb darkening for the Kepler band (Claret & Bloemen 2011), integrated over the 29.4-minute cadence (Kipping 2010, MNRAS 408, 1758), with and without a prior on the stellar density.
  - Report rho_circ / rho_star as a vetting result (Seager & Mallén-Ornelas 2003, ApJ 585, 1038; Dawson & Johnson 2012, ApJ 756, 122).
  - Quote k, b, T14 and Rp from that fit, apply the degeneracy statement to both stars, and give one a/R* with its meaning stated (fitted, or from the stellar density).
  - Report bootstrap intervals as percentiles of the bootstrap, or explain the offset between the point and the bootstrap median.

### 4. Major [both]. The false-alarm probability is mis-described and is not a survey-level significance

- Text: "The false-alarm probability against 1,998 null searches of this source is 1 in 170" (6146676) and "1 in 51" (5689121).
- What is wrong:
  - The nulls are not of this source. `calibrate.py` describes them as the 1000-star pilot: "for each of the highest-priority targets the ... flux is searched as it is, inverted and scrambled".
  - Both packs use the same `calibration/kbonus_bkg.jsonl` (same SHA-256, 999 rows). "Source" means the KBonus data source, not the star, and a reader will take the opposite meaning.
  - The FAP is per light curve, while the candidate came from a sweep of a large catalogue. The adapter's comment counts 34,161 no-KIC stars and 84,487 KIC bystanders after the cuts. At FAP 0.0195, 1,000 searched stars give about 20 noise peaks at SDE 19.9 or above, and 10,000 give about 195. At FAP 0.006, the same 1,000 give about 6.
  - For KIC 5689121, 37 of the 38 exceedances are inverted-flux nulls, which shows that systematics, not white noise, set the SDE tail.
  - The calibration also uses a different detector configuration from the one that found the signals: masked pass and alias resolution off, calibration version 5+42061727 against signal version 5+6cfe4cf4. `version_matches: true` reflects a mapping in `dossier.calibration_version_for`, not an identical search.
  - The S/N of 28 to 30 is the stronger argument and is not the statistic calibrated.
- Ask:
  - Reword as "against 1,998 null searches (inverted and scrambled light curves of 999 other KBonus stars)".
  - State the number of stars searched and the expected number of noise peaks at the signal's statistic in the whole sweep.
  - Calibrate on S/N or MES rather than SDE, or say why SDE is the right statistic.
- Precedent: the DR25 reliability estimate from inverted and scrambled light curves is a catalogue-level quantity (Thompson et al. 2018, ApJS 235, 38; Coughlin 2017, KSCI-19114). Twicken et al. (2018, PASP 130, 064502, section III.5) describe the per-target bootstrap and how it differs.

### 5. Moderate [both]. The dilution numbers are printed but not interpreted, and the aperture depth disagrees with the PSF depth

- Text: "a target light fraction in the aperture (CROWDSAP) of 0.86 [0.75] and a PSF flux fraction inside the aperture (PSFFRAC) of 0.80 [0.67], and in the optimal-aperture flux of the neighbour ... the dip at this ephemeris is -9 ± 12 ppm [130 ± 42 ppm]."
- What is wrong:
  - The note never says which flux the depth comes from or whether a dilution correction was applied. From the code (`lc/kbonus.py` reads `FLUX`), the depth is from the KBonus PSF flux, which is decontaminated to first order, so no CROWDSAP correction applies. A reader cannot know that.
  - The rung 3 metrics in the dossier show the aperture (SAP) depth well below the PSF depth: 1,540 ± 128 against 2,187 ppm (ratio 0.70 ± 0.07, `aperture_z` -3.9) and 1,289 ± 109 against 2,031 ppm (0.635 ± 0.064, -4.7).
  - If SAP_FLUX is not crowding-corrected, CROWDSAP predicts ratios of 0.86 and 0.75. The measured ratios are 2.2 and 1.8 sigma below, in the same direction for both stars. If SAP_FLUX is corrected, they are 4.3 and 5.7 sigma below 1.
  - Either case leaves unexplained light in the aperture (an unlisted source, or a PSF-model leak), which bears directly on the STP and diluted scenarios.
  - The 130 ± 42 ppm dip in KIC 5689116's aperture is a 3.1-sigma detection presented without comment. The cross-check note says it is "at the size the crowding predicts", but no prediction is shown. My crude estimate, a Gaussian PSF (sigma 0.53 to 0.58 px) in a 3 x 3 box on the neighbour, gives 300 to 590 ppm. The real optimal aperture probably excludes the target's pixels, so the expectation depends on the aperture mask.
- Ask:
  - State that the depth is PSF photometry, with no CROWDSAP correction applied, and why.
  - Report the SAP depth and the expected ratio, and discuss the residual.
  - Compute the leak expected if the target hosts the dip, from the neighbour's actual optimal-aperture mask (the TPF APERTURE extension) and a Kepler PRF (Bryson et al. 2010), and compare it with 130 ± 42 ppm.
  - Say that a neighbour host would need about 968 ppm and is excluded at 20 sigma.
- Precedent: Bryson et al. (2013, section 4) and the DR25 ghost diagnostic (Thompson et al. 2018, A.5.2) treat aperture-dependent depth as a vetting result to explain, not a number to print.

### 6. Moderate [6146676, also 5689121]. The centroid test is led by an estimator with no power, and the "measured systematic floor" is weaker than worded

- Text: "Against the star's own out-of-transit image the difference-image centroid is offset by 1.8 ± 1.0 arcsec (1.8 sigma), not an offset by the rule of Bryson et al. (2013) (over 3 sigma and 0.2 arcsec)"; "its error carrying a measured systematic floor of 0.017 px".
- What is wrong:
  - For KIC 6146676 the reference, the direct-image 3 x 3 centroid, sits 0.17, 0.50, 1.09 and 1.31 px from the Gaia position in seasons 0 to 3 (dossier `direct_centroid_colrow` against `pos_target`). It is dragged by KIC 6146666 (G = 12.8, 23 times brighter in the Kepler band, 2.9 px away).
  - A test whose 1-sigma error is 1.0 arcsec cannot flag any source closer than about 3 arcsec. "Not an offset by the rule" is true but says nothing. Bryson et al. (2013) warn that the out-of-transit-centroid offset is biased by crowding and prefer the catalogue-referenced (or PRF-fit) offset in crowded fields.
  - The informative numbers are the Gaia-referenced offsets, 0.25 ± 0.28 and 0.43 ± 0.25 arcsec, which exclude sources beyond about 1.1 and 1.2 arcsec (3 sigma). No Gaia source lies within 7 arcsec of either target (dossier `fpp.neighbours`).
  - On the floor: the per-season difference centroids of KIC 6146676 scatter about the Gaia position by more than their errors (chi-squared 21.8 for 8 dof, p = 0.005, using `centroid_sigma_pix`, which includes the floor). The errors are therefore not complete at the per-season level. The vector-mean scatter error the note adopts (0.28 arcsec, four times the formal error of the mean) is the honest number.
  - The 0.0167 px floor was measured from the quarter-to-quarter scatter of bright direct images (`docs/superpowers/notes/2026-10-22-centroid-floor.md`). It bounds pointing and registration jitter, not the bias of a flux-weighted box centroid on a faint blended difference image. Its validation "cannot test the floor below about 0.06 px at the season level" (section 5 of that note).
  - For KIC 5689121, two of the three voting seasons have a box cut by the stamp edge and the fourth did not vote, so the "3 of 3 seasons that voted" rests on one complete season (season 1, 11 sigma). The abstract wording is correct, but this fact belongs beside it.
- Ask:
  - Lead with the Gaia-referenced offset and its 3-sigma exclusion radius.
  - Say why the self-referenced offset is uninformative here, or drop it.
  - Describe the 0.017 px term as a pointing-jitter floor, not a systematic floor of the estimator, or measure the estimator's bias on injected difference images at the target's position.
- Precedent: Bryson et al. (2013, PASP 125, 889, sections 3 and 6.3) on PRF-fit centroids, the KIC-referenced offset and the quadrature floor.

### 7. Moderate [both]. The odd-even and secondary statements need the significance context the pack already holds

- Text:
  - 5689121: "The odd and even depths are 2,031 ± 178 and 2,031 ± 138 ppm (0.0 sigma apart)"; "the strongest dip elsewhere is 296 ppm at phase 0.73 (4.0 sigma)".
  - 6146676: "342 ppm at phase 0.38 (3.8 sigma)".
- What is wrong:
  - The parity result depends on the estimator. The per-event weighted means reproduce 2,032 and 2,031 ppm. A stacked fold on the linear ephemeris over T14 gives 1,848 ± 93 and 1,973 ± 95 ppm (0.9 sigma). Both are consistent with no difference, but "0.0 sigma" overstates the precision.
  - A 4-sigma dip at phase 0.73 would mean e cos ω ≈ 0.36 for an eccentric binary, and the note gives the reader no way to judge it. The model-shift metrics in the dossier answer that: sigma_ter = 3.1 and 3.4, sigma_pos = 8.0 and 5.2, FA2 = 2.7 and 3.0. Features of 3 to 8 sigma of either sign occur in these folds, so the off-phase dip is consistent with the noise.
  - Two estimators disagree on the 5689121 secondary: the free-phase search gives phase 0.726 at 3.98 sigma, the model-shift search phase 0.80 (-0.198) at 3.24 sigma.
  - The per-event depths are over-dispersed: chi-squared 71.9 for 30 dof and 143.9 for 54 dof, with errors scaled by 1.55 and 1.63. The scaling is correct practice.
  - KIC 6146676 shows a depth trend of +223 ± 59 ppm/yr (3.8 sigma with formal errors, 2.4 sigma with scaled errors), from about 1,700 ppm in Q1 to Q5 to about 2,600 ppm in Q13 to Q16. That is worth a look for a crowded target whose PSF scene changes with time.
- Ask: quote the model-shift numbers (MS1 to MS3, or sigma_sec against sigma_ter and FA2) beside the strongest off-phase dip, give the stacked odd-even alongside the per-event one, and comment on the depth against time.
- Precedent: Thompson et al. (2018), A.3.4 and A.4.1, and Coughlin (2017).

### 8. Moderate [5689121]. Transits with SES below 2.5 enter the timing fit

- Text: "Timed one by one, 57 of 60 transits fit this linear ephemeris with an rms of 16.8 minutes."
- What is wrong:
  - Nine of the 57 timings used come from transits with single-event S/N below 2.5 (`data/transit_times.csv` against `data/events.csv`), for example epochs 59 and 61 at SES 1.37 and 1.29, with formal errors of 15 and 25 minutes.
  - A mid-time fitted at S/N of about 1.5 is not Gaussian-distributed. The likelihood is multi-modal, and the formal error understates it.
  - Six timed epochs (10, 32, 40, 47, 58, 71) have no row in `events.csv`, so the timing and depth samples are selected differently, and the README calls `events.csv` "every transit".
  - The linear fit is robust anyway: I reproduce P, T_ref and the inflation (chi-squared 112.1 for 55 dof, factor 1.43). For KIC 6146676 the timing set is clean (chi-squared 18.5 for 30 dof).
- Ask: time only transits above an SES threshold (Holczer et al. 2016 restrict per-transit timing to adequate S/N), or report the period from the fold. Align the two samples or explain the difference.

### 9. Moderate [both]. The stellar parameters are reproducible but their caveats are incomplete

- Text: "From the absolute Ks magnitude the relations of Mann et al. (2015) and Mann et al. (2019) give a radius of 0.505 ± 0.016 [0.504] R_sun and a mass of 0.503 ± 0.013 [0.502] M_sun."
- What I checked: I reproduce R and M from Ks, the zero-point-corrected parallax and the midpoint A_Ks = 0.035, and L, a, S, Teq and the Kopparapu edges (section 3).
- What is missing:
  - No RUWE or other binarity indicator is given. An unresolved companion would bias M_Ks bright and R high, and it is exactly the STP channel that dominates the TRICERATOPS FPP.
  - No extinction estimate supports the assumption "E(BP-RP) <= 0.1" at 290 to 300 pc toward Lyra and Cygnus. A 3D dust map (Green et al. 2019, ApJ 887, 93) would supply one.
  - The dossier's Gaia cross-check is omitted: GSP-Phot Teff 3,753 K (171 K hotter) and FLAME R = 0.542 R_sun (8 percent larger) for KIC 5689121.
  - Teff from the Pecaut & Mamajek table at a single colour is coarser than the Mann et al. (2015) colour-Teff relations the note already cites.
  - The habitable-zone sentence on a 2.6 Earth-radius planet ("inside the optimistic habitable zone") applies a rocky-planet concept to a likely volatile-rich sub-Neptune (Rogers 2015, ApJ 801, 41; Fulton et al. 2017, AJ 154, 109). Either caveat it or cut it, which also saves words.
- Ask: add RUWE, an extinction estimate, the Gaia alternative radius as a systematic, and a sub-Neptune caveat on the habitable zone or no habitable-zone sentence.

### 10. Moderate [both]. The citation, software and data statements are incomplete, and the notes still hold placeholders

- Missing citations for data the notes use:
  - Gaia DR3: Gaia Collaboration 2016, A&A 595, A1; Vallenari et al. 2023, A&A 674, A1.
  - 2MASS Ks: Skrutskie et al. 2006, AJ 131, 1163.
  - The Kepler mission: Borucki et al. 2010, Science 327, 977.
  - The catalogues matched in rung 4: DR25 TCEs (Twicken et al. 2016, AJ 152, 158), the KOI table (Thompson et al. 2018), the Kepler EB catalogue (Kirk et al. 2016, AJ 151, 68), the TOI list (Guerrero et al. 2021, ApJS 254, 39).
  - The TIC (Stassun et al. 2019), used for the TESS prediction.
  - The software: numpy (Harris et al. 2020), scipy (Virtanen et al. 2020), astropy (2013, 2018, 2022), matplotlib (Hunter 2007). AAS journals ask for software citations, and `provenance/versions.json` already holds the versions (numpy 2.5.4, matplotlib 3.11.2).
- Placeholders remain: "AFFILIATION TO FILL IN", "EMAIL TO FILL IN", "doi:10.5281/zenodo.XXXXXXX" and "doi:10.5281/zenodo.SOFTWARE (to be minted)".
- An unpublished path is cited as evidence: "The centroid agrees with the hand-built localisation within 0.03 px over 4 seasons (docs/candidates/kic-006146676/localisation/results.json)". That path is in the authors' repository and not in the Zenodo pack's file list, so a reader cannot check it. Put the file in the pack or drop the sentence.

### 11. Minor [both]. The word count is right but leaves no headroom

- Text: the README gives "1394 words in all" and "1473 words in all".
- My count: 1,377 (6146676) and 1,454 (5689121) with each citation counted as one word, and 1,406 and 1,484 with citations rendered as author-year. Both are within the 1,500-word limit, and the authors' counts fall between my two conventions. The one-figure rule is met.
- The problem: the 5689121 note has 16 to 46 words of headroom, so findings 1, 2, 3 and 10 cannot be met without cuts.
- Candidates for cutting: the repository path (finding 10); the "What is not yet known" section, which repeats the cut-box caveat and the candidate status; the habitable-zone sentence (finding 9); the two-source medians without errors (replace them with the combined neighbour depth and its significance); and the TESS parentheticals.
- Check the current RNAAS guidelines on whether references count, as the frame comment advises.

### 12. Minor [5689121, both]. The word "season" means two things

- `lc/ops.py: season_of` uses quarter mod 4 for the depth test. The pixel test uses the archive SEASON keyword.
- For KIC 5689121 the depth test's "season 0" (2,295 ppm) is Q4, Q8, Q12 and Q16, which the pixel test calls season 2. A reader relating "the depth agrees across 4 seasons" to "in seasons 0 and 2 this star's box is cut" will mix them up. Use one convention.

### 13. Minor [both]. The numbers are not quite consistent across the note, README, ExoFOP files and dossier

- a/R*: 43.6 in the note and the ExoFOP planet file against 77.9 in `ephemeris.csv` and the README (finding 3).
- ExoFOP `imp_unc` carries only the lower error (0.063 for +0.030/-0.063).
- The ExoFOP and archive epoch is epoch 0 (±0.0031 d), not the central epoch (±0.0014 d), which observers and ExoFOP would prefer.
- The semi-major axis uses the search period ("P 40.29109 d") instead of the linear-fit 40.29081 d. The effect is negligible, but state it once.
- `events.csv` has 31 and 55 rows against "33 transits" and "65 transits", while the README calls it "every transit".
- "No single transit carries more than 28 [33] percent of the signal": the metric is max(SES) over the root sum of squares of SES (`vetting/rung1_events.py`). The largest event's share of the total chi-squared is 8 and 11 percent. Reword, for example as "the largest single-event statistic is 0.28 of the multiple-event statistic".
- Otherwise the period, T0, T_ref, duration, depth, S/N, SDE, parity, secondary, CROWDSAP, PSFFRAC and leak values match exactly between the note, the README, both ExoFOP files, the archive email, `ephemeris.csv` and `dossier_data.json`.

### 14. Minor [both]. The figure is clear but could carry more

- The figure is clear, honest (unbinned points shown, binned errors, data behind it in `note/figure1_data.csv`) and within the one-figure rule.
- The model drawn is the uniform disc of finding 3, with sharp contact points that the limb-darkened refit would change.
- In the KIC 5689121 fold, the bins from -8 to -2 h sit about 200 to 400 ppm above zero, a baseline slope worth a sentence or a local baseline in the plot.
- RNAAS allows panels within one figure. A residual panel, or the odd and even folds, would show the reader the evidence of findings 3 and 7 at no word cost.

### 15. Minor [both]. The TESS statement is correct, with two wording points

- I reproduce the diluted depths (132 and 746 ppm) and the folded nominal S/N (0.89 and 2.07).
- "Not expected to have detected" is supported, and the README's thresholds (Christ et al. 2019; Sullivan et al. 2015) are appropriate.
- Two wording points:
  - The note should say in a clause that no TESS data were examined. The README says it ("a prediction, not a search") and the note does not.
  - For KIC 5689121, 2.1 sigma is closer to the "marginal" band (3 to 7.1) than "not expected" suggests, so "not expected at the nominal noise" would be more exact.

## 3. Numerical cross-checks I performed

Scripts in my scratch area read only the pack files and the dossiers. The light curves are the packs' `data/lightcurve.csv` (51,495 and 42,829 points).

| Check | KIC 6146676: mine | KIC 6146676: text | KIC 5689121: mine | KIC 5689121: text | Agrees? |
|---|---|---|---|---|---|
| Period from `transit_times.csv` (weighted linear fit, used rows, errors inflated by sqrt(chi2/dof) when above 1) | 40.290807 ± 0.000148 d; chi2 18.5/30 | 40.29081 ± 0.00015 | 13.769414 ± 0.000048 d; chi2 112.1/55, inflation 1.428 | 13.769414 ± 0.000048 | yes |
| Reference mid-time | BKJD 901.3205 ± 0.0014 (E = 19) | 2455734.3205 ± 0.0014 | BKJD 797.2462 ± 0.0014 (E = 48) | 2455630.2462 ± 0.0014 | yes |
| Epoch-0 mid-time | 135.7952 ± 0.0031 | 135.7952 ± 0.0031 | 136.3143 ± 0.0027 | 136.3143 ± 0.0027 | yes |
| Timing rms | 9.4 min | 9.4 min | 16.8 min | 16.8 min | yes |
| Search period against linear fit | 1.9 sigma apart, 7 min drift at the series ends | not stated | 2.0 sigma, 7 min | not stated | acceptable, worth one clause |
| Fold depth, box of the search duration | 2,247 ± 75 ppm (search ephemeris), 2,238 (linear) | 2,187 ± 108 (per-event), box 2,192 | 2,093 ± 77 (search), 2,064 (linear) | 2,031 ± 113, box 2,065 | yes, within 1 sigma |
| Fold depth over T14 (includes ingress and egress) | 1,924 ± 65 | | 1,913 ± 71 | | as expected for a partial box |
| S/N of the fold (scatter-based error) | 29.4 to 30.0 | 30.0 | 26.9 to 27.2 | 28.1 | yes |
| Per-event weighted mean from `events.csv` | 2,187 ± 70, scaled to ± 108 (chi2 71.9/30) | 2,187 ± 108 | 2,031 ± 70, scaled to ± 113 (chi2 143.9/54) | 2,031 ± 113 | yes; over-dispersion not reported |
| Odd-even, per event | 2,222 ± 162 / 2,154 ± 149 | same, 0.3 sigma | 2,032 ± 178 / 2,031 ± 138 | same, 0.0 sigma | yes |
| Odd-even, stacked fold | 1,925 ± 83 / 1,922 ± 88 (0.02 sigma) | | 1,848 ± 93 / 1,973 ± 95 (0.9 sigma) | | consistent; see finding 7 |
| Phase-0.5 depth (my fold, scatter error) | 28 ± 72 ppm | 95 ± 128 | -102 ± 65 | 7 ± 138 | consistent; the text's error is the conservative control rms |
| Strongest off-transit dip (my scan) | 282 ppm at 0.463, 3.9 sigma | 342 at 0.38, 3.8 sigma | 274 ppm at 0.727, 3.6 sigma | 296 at 0.73, 4.0 sigma | same level; the 6146676 phase differs by estimator |
| Depth against time | +223 ± 59 ppm/yr (3.8 sigma formal) | not reported | -105 ± 61 ppm/yr (1.7 sigma) | not reported | see finding 7 |
| max SES / sqrt(sum SES^2); largest event's share of chi2 | 0.283; 8.0 percent | "28 percent of the signal" | 0.330; 10.9 percent | "33 percent" | metric reproduced, wording wrong |
| rho_circ from fitted a/R*, against rho_star | 0.97 against 5.51 g/cm3 (0.18) | not discussed | 1.70 against 5.53 (0.31) | not discussed | see finding 3 |
| b for a circular orbit from T14 and stellar a/R* | 0.38 | 0.893 fitted | 0.57 | 0.86 fitted | see finding 3 |
| Refit of the fold, uniform and quadratic limb darkening (u1 = u2 = 0.33), 11-point cadence integration, a/R* free or at the stellar value | all four fits within Δchi2 2.1 (chi2 289, 280 bins); b from 0.10 to 0.57 equally good; k 0.0453 to 0.0473 | b 0.893, k 0.0478 | all four within Δchi2 2.1 (chi2 622, 288 bins); b from 0.09 to 0.93; k 0.0448 to 0.0485 | b 0.86, k 0.0461 | b unconstrained; data consistent with the stellar density; Rp model-dependent by about 4 to 5 percent |
| Mann et al. 2015 R and Mann et al. 2019 M at A_Ks = 0.035 | 0.505, 0.503 | 0.505, 0.503 | 0.504, 0.502 | 0.504, 0.502 | yes |
| L, a, S, Teq | 0.0409 Lsun, 0.1829 AU, 1.22, 268 K | same | 0.0377, 0.0894 AU, 4.72, 375 K | 4.73, 375 K | yes |
| Kopparapu runaway and maximum greenhouse | 0.2160 and 0.4047 AU | 0.2159, 0.4097 | 0.2074 and 0.3901 AU | 0.2076, 0.3953 | inner edge exact, outer within 1.3 percent (likely the coefficient set) |
| Rp = k R* | 2.63 Re | 2.63 | 2.53 Re | 2.54 | yes |
| FAP (k+1)/(n+1) | 0.0060, 1 in 167 | 1 in 170 | 0.0195, 1 in 51 | 1 in 51 | yes; see finding 4 for meaning |
| TESS diluted depth and folded nominal S/N | 132 ppm, 0.89 | 132, 0.54 to 0.89 | 746 ppm, 2.07 | 746, 1.1 to 2.1 | yes |
| Next transits (README) | 2461375.0334, 2461415.3242, 2461455.6150, ± 30 min | same to 1e-4 d | 2461344.5532, 2461358.3226, 2461372.0920, ± 29 min | same to 2e-4 d | yes |
| Neighbour as host: depth it would need (target depth / Kepler-band flux ratio 23.6 and 2.10) | 93 ppm | | 968 ppm | | |
| Two-source fit on the neighbour, weighted over voting seasons | -23 ± 25 ppm (4.5 sigma below 93) | "-21 ppm" median | 76 ± 36 (24.6 sigma below 968) | "65 ppm" median | supports the host claim; quote the combined value with its error |
| Leak test against the neighbour-host prediction | -9 ± 12 (8.3 sigma below 93) | -9 ± 12 | 130 ± 42 (20 sigma below 968) | 130 ± 42 | supports the host claim |
| Aperture/PSF depth ratio against CROWDSAP | 0.70 ± 0.07 against 0.86 (2.2 sigma) | not reported | 0.635 ± 0.064 against 0.75 (1.8 sigma) | not reported | see finding 5 |
| Per-season difference centroids about Gaia (chi2) | 21.8/8, p = 0.005 | | 9.9/6, p = 0.13 | | see finding 6 |
| Prior claim against this note (P, T, depth) | | | dP = 0.7 sigma, dT = 0.5 sigma, d(depth) = 1.0 sigma | not reported | agrees; see finding 1 |
| Word count (citations as one word / rendered) | 1,377 / 1,406 | 1,394 | 1,454 / 1,484 | 1,473 | yes, under 1,500 |

## 4. What the authors should keep

- The provenance: SHA-256 for every file, the query log, the versions and commit, the calibration counts and the rebuild commands. A referee can recompute nearly every number from the pack, and I did.
- Per-transit times with errors, a linear ephemeris at the central epoch with an honest inflation for excess scatter, and prediction windows that reproduce to 1e-4 d.
- The TRICERATOPS cross-check, run 20 times with percentiles, every run kept, the settings and caveats recorded, the light-sharing protocol written out, and the full FPP reported beside the like-for-like one. That is better practice than most published uses of the tool. Promote its result to the abstract.
- The pixel work: the two-source fit and the leak test in the neighbour's own aperture together exclude the stamp's owner as the host at high significance. That is the strongest evidence in both notes and deserves a clearer, quantified sentence.
- Candidate wording throughout, the "What is not yet known" honesty, the stated model limits, and the prior claim cited with its DOI and date in every text for KIC 5689121.
- The TESS non-detection prediction, with its method and thresholds written down and correctly called a prediction.
- The data behind the figure, the figure itself (unbinned points shown) and the one-figure discipline.
