# SkySift vetting against published best practice: gap analysis and ladder version 3 (18 October 2026)

Vikram, 18 October 2026: "since I am not expert at this, you can use best practices and expert knowledge available and
documented somewhere directly to ensure we have best possible tests and filters etc."

This document compares SkySift's vetting ladder (rungs 1 to 4, `src/skysift/vetting/`) with the published automated
vetting of the Kepler mission and its successors, test by test, and specifies ladder version 3. Rung changes do not
alter the search version, so they can land while the 1000-star calibration pilot runs; search-side changes (detrending,
templates, thresholds) are listed at the end as deferred, because they invalidate the pilot.

## 1. Sources

| Ref | Work | What it contributes |
|---|---|---|
| T18 | Thompson et al. 2018, ApJS 235, 38 (arXiv 1710.06758), Kepler DR25 KOI catalogue and Robovetter, Appendix A | the complete list of automated tests, thresholds and the completeness / reliability framework (146,294 injections; inverted and scrambled light curves) |
| C16 | Coughlin et al. 2016, ApJS 224, 12 (arXiv 1512.06149), DR24 catalogue | the first fully automated catalogue; model-shift uniqueness test and LPP introduced |
| M16 | Mullally et al. 2016, PASP 128, 074502 (arXiv 1602.03204) | Marshall: per-event transient classification by BIC |
| HH19 | Hippke and Heller 2019, A&A 623, A39 (arXiv 1901.02015) | Transit Least Squares: 93 percent recovery against 76 percent for BLS at SDE about 7 at 1 percent false positives |
| H19 | Hippke et al. 2019, AJ 158, 143 (arXiv 1906.00966) | wotan: the time-windowed biweight slider recovers 99 percent of shallow Kepler planets |
| PZQ06 | Pont, Zucker and Queloz 2006, MNRAS 373, 231 (astro-ph/0608597) | red noise raises detection thresholds; the noise a box sees is the scatter of duration-wide bins |
| K19 | Kostov et al. 2019, AJ 157, 124 (arXiv 1901.07459) | DAVE (K2): most false positives are secondaries, stellar depths and photocentre shifts |
| G20 | Giacalone et al. 2020, AJ 161, 24 (arXiv 2002.00691) | TRICERATOPS: statistical validation at FPP < 0.015 and NFPP < 0.001 |

Threshold values below are quoted from T18 Appendix A (sections A.3 to A.6) unless marked "SkySift".

## 2. Where SkySift already matches the published practice

- **Detection floor.** T18 requires MES >= 7.1 and >= 3 transits; SkySift requires SDE >= 11 (calibrated on inverted
  and scrambled real curves, the same two null constructions T18 uses for reliability), SNR >= 7, >= 3 events and a
  duration within 0.3 to 3 times the circular-orbit duration.
- **Red noise.** PZQ06 and T18's F_red (red to white ratio at the transit duration, from the scatter of best-fit
  depths at other phases): SkySift's `stacked_depth` inflates its error by the robust spread of the same box at nine
  control phases, and the 18 October injection study quotes completeness against the red-noise SNR.
- **Shape.** T18 LPP (a machine-learnt transit-shape metric trained on known planets) and SWEET: SkySift's rung 2
  compares a box with a two-harmonic sinusoid on the binned fold (`shape_contrast`), and pre-whitens significant
  sinusoids before the search, putting back the detection's own harmonics before the second search.
- **Secondary and odd/even.** T18 fails a TCE on a significant secondary (MS4 to MS6) or odd/even difference; SkySift
  fails on a secondary at phase 0.5 above 3 sigma, a free-phase secondary above 5 sigma, and an odd/even difference
  above 3 sigma (per event or on the stacked fold).
- **Ephemeris matching.** T18 A.6 matches period and epoch against every known KOI, TCE and EB; SkySift rung 4
  matches period (integer ratios to 8 for the star's own rows, 0.5/1/2 for neighbours in a 120 arcsec cone) and
  records the epoch agreement.
- **Completeness and reliability.** T18 measures completeness by injection and reliability from inverted and
  scrambled curves; SkySift has the 120-injection study on real curves and `skysift calibrate` with both nulls.

## 3. Gaps, test by test

| T18 test (section) | T18 rule | SkySift today | Gap and ruling |
|---|---|---|---|
| Rubble (A.3.7.1) | an event counts only if the cadences present across two transit durations are >= 0.75 of the expected number | `n_events` counts events whose box holds >= 3 points | **Adopt.** Events at gap edges count as full events today. |
| Fraction of gapped events (A.3.8) | fail if the fraction of events with data is <= 0.5 | none | **Adopt** with the Rubble coverage. |
| Dominated by single event (A.3.5) | fail if SES_max / MES > 0.8 for P > 90 d | none | **Adopt.** Three equidistant systematics with one dominant event pass rung 1 today. |
| Chases (A.3.3, A.3.7.3) | per event, C_i = min(dt, P/10) / (P/10), dt the distance to the nearest feature with abs(SES) > f x SES_max outside 1.5 durations; applied only when <= 5 events; events with C_i < 0.01 dropped; fail if the median C_i < 0.8 | none | **Adopt** with a sliding box of the detected duration as the SES proxy (SkySift has no wavelet SES series); f is "tunable" in T18 and SkySift takes 0.8. |
| Marshall (A.3.7.2) | per event, GP continuum plus sigmoid / SPSD / box models; bad if a non-transit model wins by BIC >= 10 | none | **Deferred.** Needs a GP per event (minutes per target); Chases catches the asymmetric SPSD shape through the abs(SES) rule. |
| Skye (A.3.7.4) | events of many targets on one CCD channel clustered in 1 d bins above R + 3 sqrt(R) | none | **Deferred to a post-sweep stage**: it needs the population of a whole run; SkySift stores every event time, so a sweep-level report can do it later. |
| Zuma (A.3.7.5) | an event with SES < 0 is bad | box depths are signed; a brightening event lowers the mean | **Adopt** inside the event quality rung: an event with a negative depth beyond 1 sigma is bad. |
| Tracker (A.3.7.6) | fail when the fit ephemeris slips > 0.5 duration from the search ephemeris | not applicable (one ephemeris) | none |
| Model-shift uniqueness (A.3.4) | FA2 = sqrt(2) erfcinv(T_dur / P); MS2 = FA2 - (sigma_Pri - sigma_Ter); MS3 = FA2 - (sigma_Pri - sigma_Pos); fail if MS2 > 2.0 or MS3 > 4.0 (DV detrending) | none: a fold with two or three dips of equal depth passes rung 2 if none sits at phase 0.5 | **Adopt** MS2 and MS3 at the DV thresholds (the looser pair, chosen to protect completeness; human review follows). MS1 (sigma_Pri / F_red against FA1 with N_TCE) is the catalogue-level significance that `sde_min` already calibrates; not adopted. |
| Secondary by model-shift (A.4.1.2) | MS4 = sigma_Sec / F_red - FA1 > 1 and MS5 = (sigma_Sec - sigma_Ter) - FA2 > 0 and MS6 = (sigma_Sec - sigma_Pos) - FA2 > 0 | phase-0.5 box at 3 sigma; free-phase search at 5 sigma | **Keep SkySift's rules** (exercised on real binaries on 18 October) and **add the two T18 exceptions** below. |
| Planet at twice the period (A.4.1.3) | PC when sigma_Pri - sigma_Sec < FA2 and the secondary is within 1/4 duration of phase 0.5 | the detector's parity resolver doubles the period when odd and even differ; when it does not, rung 2 fails the signal as `secondary_at_0.5` | **Adopt** as UNDETERMINED "equal secondary, period may be twice" (the signal goes to review with the doubled period suggested, instead of being rejected). |
| Planet occultation (A.4.1.3) | keep as PC when geometric albedo < 1, R_p < 30 R_earth, secondary depth < 10 percent of primary, b < 0.95 | any secondary above threshold fails | **Adopt.** A hot Jupiter with a 100 ppm occultation is rejected today. b and R_p come from the transit fit below. |
| Odd/even (A.4.1.4) | sigma_OE = abs(d_odd - d_even) / sqrt(s_odd^2 + s_even^2) > 1.1 (median of central points) or > 11.2 / 19.8 (model-shift depths) | 3 sigma with chi-square-scaled errors | **Keep.** The statistics differ; 3 sigma on scaled errors sits between T18's two methods and no injected box failed it. |
| V shape (A.4.3) | fail if R_p / R_star + b > 1.04 from the transit model fit | none (no transit model) | **Adopt** with a uniform-disc transit fit on the binned fold (k, b, a / R_star). The fit also gives a depth without the box bias (the injection study measured the box depth 10 percent low). |
| SWEET (A.3.2, A.4.2) | sine at P/2, P, 2P on the PDC flux; not transit-like if SNR > 50, amplitude > depth and P < 5 d; stellar if SNR > 50, amplitude < depth, amplitude > 5000 ppm and P < 10 d | shape contrast on the fold; pre-whitening | **Adopt** on the detrended flux before pre-whitening (the pre-whitened flux has the wave removed): the ellipsoidal (SWEET EB) case is new. |
| Seasonal depth (informational flag SEASONAL_DEPTH) | informational only: a seasonal depth difference can be a contaminated planet on the target as well as a neighbour's eclipse | rung 3 FAILS on p < 0.0027 with one season beyond 3 sigma | **Change to UNDETERMINED** (review) with the same statistic. |
| Centroid (A.5) | difference-image centroid offsets; ghost halo/core ratio > 4 | `sap_psf_ratio` recorded, no verdict | **Adopt a verdict**: when the depth in the PSF flux differs from the SAP depth beyond 3 sigma (combined errors) the signal is UNDETERMINED "aperture-dependent depth". KBonus PSF photometry is the only pixel-level handle SkySift has. |
| Ephemeris match (A.6) | S_P < 5 and S_T < 5 both required; separation rules by magnitude | period only; epoch recorded | **Adopt the epoch in the verdict**: a period match whose catalogue epoch is present and disagrees is UNDETERMINED "period matches but epoch differs" instead of FAIL known. |
| Disposition score (section 3.2) | fraction of 10,000 perturbations of the metrics that keep the PC verdict | none | **Deferred** to the dashboard polish plan (a ranking aid). |
| Completeness as a standing regression | 146,294 injections per release | one-off study scripts | **Adopt** `skysift inject` (section 5). |

Not adopted: LPP (needs a training set of known transits in SkySift's own flux; the shape contrast and the transit
fit cover its purpose), Tracker, Marshall and Skye as above.

## 4. Ladder version 3

`DEFAULT_LADDER_VERSION = "3"`. Cost order: rung 1 detection (0.01), rung 1 events (0.02, new), rung 2 shape (0.05),
rung 3 stability (0.05), rung 4 novelty (network). The search version is unchanged.

### 4.1 Rung 1 events (new module `vetting/rung1_events.py`, class `Rung1Events`, name `rung1_events`)
Per event (epochs `round((t - t0) / P)` with any point within one duration of mid-transit), on the vetting flux:
- coverage = points present within +-1 duration of mid-transit / (2 x duration / median cadence); good if >= 0.75
  (`EVENT_COVERAGE_MIN`).
- depth and error from `_box_depth` as `event_depths` computes them; SES_i = depth_i / err_i; an event with
  SES_i < -1 is bad (Zuma).
- Chases, only when the number of events with data is <= 5 (`CHASES_MAX_EVENTS`): slide a box of the detected
  duration across +-P/10 of mid-transit (step one cadence), significance at each offset = (baseline - box mean) /
  (scatter / sqrt(n_in)) with the baseline and scatter from the points of the window outside the box; ignore offsets
  within 1.5 durations of mid-transit; dt = the nearest offset with abs(significance) > 0.8 x SES_i
  (`CHASES_FRACTION`); C_i = min(dt, P/10) / (P/10); C_i < 0.01 marks the event bad.
Verdict: n_good = events that are covered and not bad; FAIL "gapped events" if the covered fraction <= 0.5; FAIL
"n_good < min_events"; FAIL "single event dominates" if P > 90 d (`SES_DOMINANCE_MIN_PERIOD`) and SES_max /
sqrt(sum SES_i^2) > 0.8 (`SES_DOMINANCE_MAX`); FAIL "chases" if Chases applied and the median C_i < 0.8
(`CHASES_MEDIAN_MIN`). Metrics: `n_events_with_data`, `n_good`, `coverage` (list), `ses` (list), `ses_max_over_mes`,
`chases` (list or None), `chases_median`. Sub-day periods at 30-minute cadence hold about two points per event: an
event whose box holds fewer than 3 points has no SES; when fewer than `min_events` events have an SES the SES-based
rules (Zuma, dominance, Chases) are skipped and recorded as not applied, and the coverage rules alone decide (the
stacked fold of rung 2 vets such signals).

### 4.2 Rung 2 shape: uniqueness, SWEET, transit fit, secondary exceptions
New helpers in `lc/ops.py` and a new module `lc/transit_model.py`.
- `phase_significance(time, fnorm, period, t0, duration) -> (phases, sigma)`: the box of the detected duration slid
  around the fold at `SHAPE_BINS_PER_DURATION` steps per duration; depth significance per step against the median
  and robust scatter of the points at least 1.5 durations from the box (the `_window_depth` baseline rule). From it:
  sigma_Pri at phase 0; sigma_Sec = max over abs(phase) >= 1.5 durations; sigma_Ter = max excluding 1.5 durations
  around the primary and the secondary; sigma_Pos = max of the brightening significance (negative depth) excluding the
  primary; F_red = `StackedDepth.inflation` of the primary. FA2 = sqrt(2) x erfcinv(duration / period) (scipy
  `erfcinv`). MS2 = FA2 - (sigma_Pri - sigma_Ter); MS3 = FA2 - (sigma_Pri - sigma_Pos). FAIL "not unique (MS2)" if
  MS2 > `Thresholds.ms2_max` = 2.0; FAIL "positive event as significant (MS3)" if MS3 > `Thresholds.ms3_max` = 4.0.
  Metrics `sigma_pri`, `sigma_sec`, `sigma_ter`, `sigma_pos`, `f_red`, `fa2`, `ms2`, `ms3`.
- `sweet(time, flux, period, depth_ppm) -> SweetResult(factor, amplitude_ppm, snr)`: linear fit of offset + cosine +
  sine at P/2, P and 2P to the detrended flux before pre-whitening (`DataBundle.flux_detrended`, new field, filled by
  the pipeline from `detrend(lc)`); SNR = amplitude / its least-squares error; the strongest of the three is kept.
  FAIL "sinusoidal (SWEET)" if snr > 50 and amplitude > depth and P < 5 d; FAIL "ellipsoidal (SWEET EB)" if snr > 50
  and amplitude < depth and amplitude > 5000 ppm and P < 10 d. Constants `SWEET_SNR = 50`, `SWEET_PLANET_MAX_PERIOD =
  5.0`, `SWEET_EB_MIN_AMPLITUDE_PPM = 5000`, `SWEET_EB_MAX_PERIOD = 10.0`.
- `transit_model.fit_transit(time, fnorm, period, t0, duration) -> TransitFit(k, b, a_over_r, depth_ppm, t14_days,
  chi2, n_bins, ok)`: uniform-disc (no limb darkening) overlap model of two discs, fitted by
  `scipy.optimize.least_squares` to the binned fold within +-3 durations (bins as in `shape_contrast`), bounds k in
  [1e-3, 2], b in [0, 1 + k], a / R_star in [1.5, 1e4]; start k = sqrt(depth), b = 0.3, a / R_star from the box
  duration. `ok` False when the fold has fewer than 20 bins or the solver does not converge. FAIL "V-shaped or deep
  (k + b > 1.04)" when ok and k + b > `V_SHAPE_MAX` = 1.04. Metrics `fit_k`, `fit_b`, `fit_a_over_r`,
  `fit_depth_ppm`, `fit_t14_h`, `fit_chi2`, `fit_ok`.
- Secondary exceptions, evaluated before the existing `secondary_at_0.5` / `eccentric_secondary` FAILs:
  (a) period doubling: when abs(sigma_Pri - sigma_Sec) < FA2 and the secondary phase is within 0.25 duration of 0.5,
  UNDETERMINED "equal secondary at phase 0.5: period may be 2 x", metric `suggested_period = 2 P`;
  (b) planet occultation: R_p = k x R_star (meta radius or the 0.5 R_sun fallback of rung 1); a = (G M P^2 / 4 pi^2)^(1/3)
  with the meta mass or 0.5 M_sun; geometric albedo A_g = (d_sec / d_pri) x (a / R_p)^2; when the fit is ok, A_g < 1,
  R_p < 30 R_earth, d_sec < 0.1 d_pri and b < 0.95, the secondary does not fail the signal: PASS continues with
  metrics `occultation_albedo`, `planet_radius_rearth`, reason "secondary consistent with planet occultation".
Order inside rung 2: sinusoidal (shape contrast) -> SWEET -> uniqueness MS2/MS3 -> odd/even -> secondary (with the
exceptions) -> V shape. The transit fit runs once and feeds the V-shape rule and exception (b).

### 4.3 Rung 3 stability
- Season-dependent depth: status UNDETERMINED (was FAIL), reason unchanged.
- Aperture-dependent depth: when `fnorm_alt` is present, depth and error in both fluxes (per event or stacked, the
  path already chosen); z = (d_alt - d) / sqrt(e_alt^2 + e^2); UNDETERMINED "aperture-dependent depth (z sigma)" when
  abs(z) > `Thresholds.aperture_sigma` = 3.0. Metrics `alt_depth_ppm`, `alt_depth_err_ppm`, `aperture_z`.

### 4.4 Rung 4 novelty
A period-matched own or neighbour row whose catalogue epoch is present and `epoch_agrees` is False for every matched
row: UNDETERMINED "period matches <catalogue id> but epoch differs" (was FAIL known). Rows without an epoch keep the
present FAIL.

### 4.5 Dashboard
The signal page already renders every rung's metrics generically; reasons are plain strings. One sentence is added
for `suggested_period` ("the secondary matches the primary; the true period may be 2 x = ... d") and for
`occultation_albedo`.

## 5. `skysift inject`: completeness as a standing regression
`skysift inject --source kbonus_bkg --limit N --per-star 4 --snr 7,10,15,25 --workers-cpu W --out <jsonl>`: for each
cached or fetched curve, inject box transits as `docs/superpowers/notes/scripts/inject_recovery.py` does (log-uniform
periods in the search window avoiding the star's own peaks, solar-ish durations, depth from the target stacked SNR,
capped at 5000 ppm), run `pipeline._cpu_work` with rung 4 off, and write one JSON line per injection (`found`,
`ranked_first`, `hit` rung results, `snr_red` from duration-wide bins as `inject_rednoise.py` computes it).
`skysift inject-report <jsonl>` prints the completeness table against nominal and red-noise SNR and by period, and the
depth ratio. Search version and ladder version are stamped on every line.

## 6. Deferred (search-side; after the calibration pilot, each with a re-calibration)
- Transit Least Squares templates (HH19): a limb-darkened template search instead of the box; 17 percent more
  recoveries at the same false-positive rate in HH19's test. Changes `BoxDip.version`.
- Biweight time-windowed detrending (H19) in place of the running median; window about three times the longest
  transit duration searched. Changes `DETREND_VERSION`.
- A V-shaped trial in the pre-whitening guard (the 4 percent V eclipse partly removed, recorded on 18 October).
- Marshall-style per-event transient fits; Skye at sweep level; disposition score.

## 7. What a verdict change costs
Ladder version 3 moves four outcomes from FAIL to UNDETERMINED (seasonal depth, aperture depth, period doubling,
epoch mismatch): more signals reach human review, none is lost. It adds seven FAIL rules (coverage, gapped, single
event, Chases, MS2/MS3, SWEET, V shape); each is a published rule with its published threshold, and the injection
regression of section 5 measures what they cost in completeness before any sweep is published.
