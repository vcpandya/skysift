# Referee round 2: three independent reviews of the submission packs, with answers and a fix list

Date: 22 October 2026. Packs reviewed: KIC 6146676 (40.29 d) and KIC 5689121 (13.77 d), built from commit fb9b2e1, now the tip of main.

Three reviewers read the packs with no access to the project's internal notes: a journal referee (`2026-10-22-referee-report.md`), a methods auditor who checked every quantitative claim against the code and the stored data (`2026-10-22-methods-audit.md`), and a literature benchmark against twelve RNAAS candidate notes and the Kepler vetting papers (`2026-10-22-accepted-papers-benchmark.md`). All three reports sit beside this note. The controller verified the headline findings against the code and the git history before accepting them.

## Verdict

Both notes: major revision. The arithmetic holds. Every number the reviewers recomputed from the pack files matched the text: the linear ephemeris, the depth, the odd and even depths, the TRICERATOPS medians and percentiles, the flux shares, the TESS dilution and significance, the stellar radius and mass, the next transit windows. The problems are in two code paths and in wording that claims more than the evidence supports. Four of them would stop a referee. The rest are moderate and minor.

The reviewers also agree on what is above the usual standard and should be kept: the provenance (hashes, query log, versions, rebuild commands), the per-transit timing with an inflated linear fit, the twenty-run TRICERATOPS protocol with every run stored, the two-source pixel fit and the leak test in the neighbour's own aperture, the candidate wording, and the TESS prediction with its method written down.

## The four blocking findings

### 1. KIC 5689121 was not a blind detection, and the note never compares with the prior claim

What the note says: "SkySift found it again in a blind sweep, so this is an independent recovery and vetting of that claim". The title says "An independent recovery".

What the history says. Commit 2e32b73 of 14 October 2026, one day after Mehta's Zenodo record of 13 October, added a golden test that pins the published period, epoch and depth (13.769448 d, BJD_TDB 2455630.24543, 1,883 ppm) and asserts the detector recovers them. The sweep's target list was built on 19 October, and the selection script justifies its colour cut by naming this star. The KBonus source rule of 19 October names it as the motivating case. So the detrending, the detector and the target list were developed with this signal as a known answer. The search itself used no prior ephemeris, which makes the detection a genuine re-detection, but not a blind one.

Honest answer: the pipeline was developed with KIC 5689121 as a test case from 14 October; the detection is a re-detection; the vetting ladder is independent in method and was not tuned on this star. The note adds what Mehta's pack does not: seasonal pixel localisation, the null-search calibration, the twenty-run TRICERATOPS protocol and the TESS prediction. It must also compare numbers. The referee did it: period, epoch and depth agree within 0.7, 0.5 and 1.0 sigma, and Mehta's TRICERATOPS FPP of about 0.07 sits beside SkySift's 0.13.

Precedent: Schmitt et al. 2014 (Planet Hunters VI) use "independent" for the analysis and credit every overlapping candidate to its earlier finder in one line. Cabrera et al. 2014 state the overlap with dates in a footnote. Sgro et al. 2024 cite the first public record for priority and the later note only for what it added. The AAS ethics policy asks that prior work be credited accurately, including how it shaped the present work.

Fix: retitle ("SkySift vetting of the 13.769 day transit candidate on KIC 5689121"), remove "blind" and "independent recovery" from every text, add the comparison sentence with sigma differences, and keep the priority statement with the citation in the abstract. The `prior_claims.json` block gains the prior parameters so the pack prints the comparison.

### 2. The abstract's "false-positive probability under 1 percent" is not the number the evidence supports

What the note says: "a scenario estimate puts the false-positive probability at under 1 percent" (0.0035 and 0.0016). The body then prints TRICERATOPS 0.076 and 0.13.

What the code says. SkySift's scenario model has four scenarios: planet, background eclipsing binary, eclipsing binary at P and at 2P. It has no bound-companion channel (the STP and hierarchical-triple scenarios of Morton 2012) and no diluted-planet scenario. Its background term counts only catalogued Gaia neighbours, which the centroid excludes at 26 to 40 sigma, so the term is set by Gaussian tails of order 1e-70, which are not meaningful because centroid systematics are not Gaussian at that level. TRICERATOPS on the same inputs gives 0.076 and 0.13, mostly from an unresolved bound companion, and 0.025 and 0.047 with that channel removed. The note's phrase "the two models answer different questions" hides that, after STP is removed, the rest of the gap is TRICERATOPS's scenarios on the very neighbours the pixels exclude. The note also omits the 30-minute cadence caveat that the pack's own cross-check file carries: Giacalone et al. 2021 found no validation region at that cadence.

Honest answer: both tools agree the signal is not a nearby or background eclipsing binary once the pixels are counted. The unresolved bound companion, at 5 to 10 percent, is the open channel, and only imaging or a spectrum closes it. The in-house estimate is a lower bound that omits named scenarios.

Precedent: Morton et al. 2016 validate at FPP below 1 percent only with the full scenario set and, where available, imaging constraints. Giacalone et al. 2021 define their classes on FPP and NFPP together and remove follow-up-excluded stars before computing NFPP. Kunimoto and Matthews 2020 keep candidates as candidates even below 1 percent when follow-up is missing.

Fix: the abstract quotes the TRICERATOPS FPP with and without the bound-companion channel, or no FPP at all; the in-house number comes second, labelled a lower bound with the missing scenarios named; the 30-minute caveat goes in one clause; the same wording goes to the Zenodo, ExoFOP and archive texts. Optionally, a run with the pixel-excluded neighbour removed, labelled as such, as Giacalone et al. do. Later, add the bound-companion scenario to the in-house model or retire the in-house FPP from public texts.

### 3. The transit-shape fit is not what the note says, and its b and a/R* constrain nothing

What the note says: "a uniform-disc model without limb darkening on a circular orbit, averaged over the 29.4-minute cadence", giving b = 0.893 +0.030/-0.063 and a/R* = 43.6 +9.7/-6.1 for KIC 6146676.

What the code says. The shape fit in `lc/transit_model.py` averages the model over fold bins one eighth of a duration wide (22.5 and 15 minutes), not over the exposure. The timing fit in `ephemeris.py` does average over the exposure, and the note's sentence was written from that. Without the smearing the fit needs a long ingress, which it gets from a high impact parameter and a small orbit. Both reviewers refit: with the model integrated over 29.4 minutes, b moves from 0.89 to 0.22 for a chi-square change of 0.2, and fixing a/R* at the stellar value costs 0.1. The fitted a/R* implies a stellar density of 0.97 g/cm3 against 5.5 from the Mann relations, which a referee reads as a blend signature, the opposite of what the pixels show. The note and the ExoFOP planet file print 43.6, the README and `ephemeris.csv` print 77.9 (from the stellar parameters) under the same label. The radius ratio is robust (0.047 and 0.046 in every refit); the planet radius moves by 4 to 5 percent with the model, as much as its quoted error.

Honest answer: at 30-minute cadence the ingress is unresolved, so b and a/R* are degenerate with the radius ratio for both stars; the data are consistent with the host's density; the radius ratio is the measurement.

Precedent: Seager and Mallen-Ornelas 2003 and Kipping 2010 on the density test; the best Kepler-era RNAAS note (Table 1a, R1) uses the per-transit density against the host to argue which star is transited.

Fix: integrate the model over the exposure, add Kepler-band quadratic limb darkening (Claret 2011 or the table already used for TRICERATOPS), fit with and without a stellar-density prior, quote one a/R* with its meaning stated, and apply the degeneracy caveat to both stars. The sentence "averaged over the 29.4-minute cadence" becomes true only after the fix.

### 4. The false-alarm probability is mis-described and is per star, not per sweep

What the note says: "1 in 170 (11 of 1,998 null searches of this source reached the signal's strength)".

What the code says. The nulls are the inverted and scrambled light curves of 999 other stars from the calibration pilot. "Source" means the KBonus data product. Neither target is among the 999, and only 10 of them are in the 1,590-star sweep. The calibration ran a different detector configuration (no masked pass, no alias resolution). For KIC 5689121, 37 of 38 exceedances come from the inverted null, so systematics set the tail. At a per-star FAP of 0.006 and 0.0195, a sweep of 1,590 stars expects about 10 and 30 noise peaks at these strengths. The signal-to-noise ratios of 28 and 30 are the stronger argument and were not the statistic calibrated.

Honest answer: the FAP is a per-light-curve rate from a null population of other stars; the sweep-wide expectation is of order ten to thirty peaks at this SDE; what separates the candidates from those peaks is the vetting ladder, not the SDE alone.

Precedent: Thompson et al. 2018 and Coughlin 2017 treat inverted and scrambled nulls as a catalogue-level reliability; Burke et al. 2019 combine effectiveness and reliability; Twicken et al. 2018 describe the per-target bootstrap and how it differs.

Fix: reword the nulls honestly, state the sweep size and the expected noise-peak count, and report the fraction of nulls that pass the whole ladder (effectiveness, then reliability, as DR25 does). Calibrate on S/N or the multiple-event statistic, or say why SDE is the right statistic.

## Moderate findings, with the honest answer and the fix

| # | Finding | Honest answer today | Fix | Precedent |
|---|---|---|---|---|
| M1 | The aperture depth is 0.70 and 0.63 of the PSF depth (3.9 and 4.7 sigma), unexplained; the note never says the depth is PSF photometry with no CROWDSAP correction | The KBonus PSF flux is decontaminated to first order, so no correction applies; the aperture deficit is unmodelled light or a PSF-model leak, which bears on the dilution scenarios | State the flux product; report the ratio against CROWDSAP corrected for the partial PSF; discuss the residual | Bryson 2013 section 4; Thompson 2018 A.5.2; Martinez-Palomera 2023 limitations 2 and 6 |
| M2 | KIC 5689121's leak test is a 3.1 sigma dip (130 ± 42 ppm) in the neighbour's aperture with no predicted leak beside it | It is probably the target's PSF wing (1.7 px away, 2.1 times brighter in G); a neighbour host would need about 968 ppm and is excluded at 20 sigma | Predict the leak from the neighbour's aperture mask and the Kepler PRF and print it beside the measurement | Bryson 2010 (PRF); Eisner 2021 |
| M3 | For KIC 6146676 the self-referenced centroid test leads with an estimator of no power (error 1.0 arcsec, reference dragged 0.2 to 1.3 px by a G 12.8 neighbour); the "measured systematic floor" bounds pointing jitter, not estimator bias | The Gaia-referenced offsets (0.25 ± 0.28 and 0.43 ± 0.25 arcsec) are the informative numbers; they exclude sources beyond about 1.1 and 1.2 arcsec; no Gaia source lies within 7 arcsec | Lead with the Gaia-referenced offset and its 3-sigma exclusion radius; call the 0.017 px term a jitter floor; pass the radius to both FPP models | Bryson 2013 sections 3 and 6.3; Morton 2016 |
| M4 | Odd-even "0.0 sigma" overstates precision; the 3.8 and 4.0 sigma off-phase dips are given no red-noise context; KIC 6146676 shows a depth trend of about +220 ppm/yr | The model-shift metrics already in the dossier (positive excursions reach 5.2 and 8.0 sigma) show the dips are noise; a stacked odd-even gives 0.9 sigma | Quote the model-shift margins beside the strongest dip; give both parity estimators; comment on depth against time for a crowded target | Thompson 2018 A.3.4 and A.4.1; Coughlin 2017; Kostov 2019 |
| M5 | Nine of KIC 5689121's 57 timed transits have single-event S/N below 2.5; six timed epochs have no row in `events.csv`, which the README calls "every transit" | The linear fit reproduces with or without them (chi-square 112 for 55 dof, inflation 1.43) | Time only transits above an S/N threshold, or report the fold period; align the two samples | Holczer 2016 |
| M6 | No RUWE, no extinction estimate, Gaia's alternative Teff and radius omitted; habitable-zone language on a 2.6 Earth-radius planet | RUWE is already in the query log; reddening changes the radius from 0.505 to 0.569 at E(BP-RP) = 0.2 | Quote RUWE and the NSS result; add a 3D dust-map extinction; give the Gaia radius as a systematic; caveat or cut the habitable-zone sentence | Belokurov 2020; Christiansen 2022; Green 2019; Rogers 2015; Fulton 2017 |
| M7 | Missing citations (Gaia DR3, 2MASS, Kepler, the matched catalogues, the TIC, numpy, scipy, astropy, matplotlib); placeholders for affiliation, email and both Zenodo DOIs; a repository path cited as evidence that is not in the pack | The versions are already in `provenance/versions.json` | Add the citations; mint both DOIs before submission; put the localisation file in the pack or drop the sentence | AAS software-citation policy |
| M8 | Depths, parity and the bootstrap fold on the search ephemeris, not the linear fit (drift up to 17 min at the last epochs of KIC 5689121; depth changes by 5 percent, 1.4 sigma) | Disclosed in the README | Refold on the linear ephemeris | Deeg 2015 |
| M9 | The quoted point values sit outside the middle of their own bootstrap for KIC 5689121 (k at the 16th percentile) | The interval printed is point minus p16 to point plus p84, not the resample interval | Quote the bootstrap median as the centre, the point value beside it | standard practice |
| M10 | "Independent TRICERATOPS cross-check" with shared inputs (whitened flux, ephemeris, box depth, neighbour list, flux shares) | The package and its priors are independent; the inputs are not | Say so in the README's sentence | Giacalone 2021 |
| M11 | "Agrees with the hand-built localisation within 0.03 px" compares scalar distances, season by season, from the same pixel files | It shows the engine reproduces the script, not an independent localisation | Reword or drop | none needed |
| M12 | Novelty checked only against mission catalogues; no ephemeris match across other stars on the same CCD channel | The DR25 "EC" class catches column bleed and crosstalk in other targets' stamps, which is where these two stars live | Add the ExoFOP and literature check; search all KBonus and Kepler light curves on the channel for the same P and T | Coughlin 2014; Zink 2019; Guerrero 2021 |

Minor items the reviewers list, for the fix round to sweep up:

- ExoFOP `imp_unc` carries only the lower error.
- The ExoFOP epoch is epoch 0 rather than the central epoch.
- "no single transit carries more than 28 percent of the signal" describes max(SES)/MES, not a share of chi-square.
- `events.csv` has 31 and 55 rows against "33" and "65 transits".
- The abstract's period digits differ from the text for KIC 5689121.
- "season" means two things.
- The TESS clause should say no TESS data were examined and "not expected at the nominal noise" for KIC 5689121 (2.1 sigma).
- The figure could carry the odd-even fold and the per-season difference images at no word cost.
- One sentence on the follow-up window (about 30 minutes at 1 sigma in 2026 to 2027, about 2,000 ppm on a G 16 star, within reach of a 1 to 2 m telescope).

## What accepted papers do, and where the notes stand

RNAAS is moderated, not refereed, so the bar comes from the vetting papers. An accepted candidate note is a periodic signal plus the minimum vetting needed to make it worth following up, written as a candidate, with a citable ephemeris. Most give no FPP at all. The SkySift notes already exceed that norm on ephemeris errors, stellar parameters, null calibration, TRICERATOPS and provenance. Their exposure is with an expert reader who checks the numbers against each other and against the Kepler-era standard, which is exactly where the four blocking findings sit: a claim of independence the history contradicts, an FPP headline the authors' own stronger tool contradicts, a shape fit that contradicts the host's density, and a false-alarm rate that is not what it is called.

Of the twenty-six expectations in the benchmark's gap table, the notes meet nine, partly meet eleven and miss six. The six missed are the four above plus the aperture-depth deficit (M1) and the absent RUWE (M6).

## Fix list, ranked

Blocks submission:
1. Rewording of independence, FPP, false alarms and the shared-input sentences across note, README, ExoFOP, email and Zenodo texts (findings 1, 2, 4, M10, M11). The prior-claim comparison sentence.
2. Shape fit: exposure integration, limb darkening, a density prior and one a/R* (finding 3).
3. Fold on the linear ephemeris and quote bootstrap medians (M8, M9).
4. Aperture-depth ratio and the predicted leak (M1, M2).
5. Gaia-referenced centroid first, the exclusion radius stated and passed to both FPP models (M3).
6. RUWE, extinction, Gaia radius, habitable-zone caveat, citations, DOIs minted, placeholders filled (M6, M7).

Strengthens the case, can follow:
7. Ladder effectiveness on the nulls and the sweep-wide expectation (finding 4, second half).
8. Channel-wide ephemeris match and the ExoFOP check (M12).
9. Model-shift metrics, both parity estimators, depth against time (M4); timing threshold (M5).
10. Bound-companion scenario in the in-house model, or retire the in-house FPP from public texts.
11. One TESS FFI fold per target and the TCE check within two TESS pixels.

Every item above is a change to the engine or the pack templates with a test, followed by regenerating the dossiers and rebuilding the packs. Nothing has been published, so nothing needs retracting.
