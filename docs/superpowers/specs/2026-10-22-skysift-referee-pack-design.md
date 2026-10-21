# SkySift referee-ready pack: design

Date: 22 October 2026. Status: decided and recorded (Vikram delegated the scientific calls; human review comes before anything is published). Amends section 5.9 of `docs/superpowers/specs/2026-10-20-skysift-dossiers-design.md` (the pack) and section 4.4 of the mission-control spec (the pixel rung's error). The research behind every row is `docs/superpowers/notes/2026-10-22-referee-checklist.md`; this document says what gets built for the rows that are not met.

## 1. Intent

The submission pack of 21 October holds the candidate's story, its two probabilities and its provenance, but it quotes the ephemeris and the transit shape without errors, carries no independent false-positive estimate, and its centroid error has no systematic floor. A referee, an ExoFOP upload form and the archive's parameter template all ask for those three things first. This work adds them, and the smaller items the checklist found (odd and even depths with errors, a secondary-eclipse limit, stellar provenance, dilution numbers, a code-availability statement, truthful route wording), so that the pack of KIC 6146676 could go to a reviewer as it is.

## 2. Goals, non-goals, anti-goals

Goals
- Period, mid-transit epoch, depth, duration, radius ratio, impact parameter and a/R* each carry a 1-sigma error, from a method with a named precedent, stored on the dossier and printed on the page, in the ExoFOP files, the archive email and the Research Note.
- An independent false-positive probability from TRICERATOPS for KIC 6146676 and KIC 5689121, stored beside SkySift's own, with the comparison written down.
- The pixel rung's per-season centroid error carries a measured systematic floor, and the live verdicts are rerun under it.
- The live Zenodo gate refuses a record that lacks the errors or the cross-check.
- Every change is a stored number the page, the pack and the note read; nothing is typed by hand.

Non-goals
- A full MCMC transit fit with limb darkening (the uniform-disc model stays; its limits are stated).
- Replacing SkySift's scenario FPP with TRICERATOPS. The cross-check is a second opinion, printed beside the first.
- The Kepler PRF, a TIC lookup, an ExoFOP upload, or any network action from the pack.

Anti-goals
- An error that is smaller than the scatter it summarises (every error is inflated by the reduced chi-square when that exceeds one).
- A cross-check that silently shares the engine's numerics: TRICERATOPS runs in its own environment with its own numpy, and the engine's venv is untouched.
- A floor that hides a known bias: a registration shift the metadata already knows is reported as such, not folded into a constant.

## 3. Ephemeris and transit-parameter errors (`skysift/ephemeris.py`)

### 3.1 Method and precedent

Two methods were on the table: a bootstrap over transit times for everything, or a per-transit linear fit for the ephemeris with a bootstrap over transits for the shape. The second is chosen.

- The period and the epoch come from a weighted linear least-squares fit of the per-transit mid-times, with the covariance of that fit. This is how the Kepler transit-timing catalogue quotes linear ephemerides (Holczer et al. 2016) and how Deeg 2015 says they should be quoted: the reference epoch at the centre of the series, where its error is smallest, and the prediction error at a future epoch from the period error and the distance from that centre. A bootstrap over transit times gives the same covariance with more noise and no closed form for predictions, so it is not used for the ephemeris.
- The depth, duration, radius ratio, impact parameter and a/R* come from a bootstrap over transits: the events are resampled with replacement and the uniform-disc fit is run on each resample. This follows the Planet Hunters practice (Schmitt et al. 2014: "the error bars are estimated with a bootstrapping method"). The binned least-squares fit's own covariance is not used because the fold's bins hide red noise and the k-b degeneracy, and because a bootstrap over whole transits carries the transit-to-transit scatter the per-event depths already show.

### 3.2 Per-transit times

For each epoch E whose window holds data (at least `EVENT_IN_MIN` = 3 good cadences within half a duration of the predicted mid-time and `EVENT_OUT_MIN` = 6 within one to three durations, at least 2 on each side, the pixel rung's rules), the mid-time is the shift that minimises the chi-square of the whitened flux against a fixed template: the rung 2 uniform-disc model with its fitted k, b and a/R* when `fit_ok` is true, else the search box of the stored depth and duration. The template's depth is fixed (the per-event depths are a separate measurement). The shift runs over a grid of one minute steps across plus and minus one duration; the chi-square minimum is refined by a parabola through the three lowest points, the error is the half-width at delta chi-square of one, the per-point error is 1.4826 times the median absolute deviation of the window's out-of-transit flux, and the error is inflated by the square root of the reduced chi-square when that exceeds one. An event whose chi-square curve has no minimum inside the grid, or whose shift error exceeds half a duration, is dropped with a reason. Times are BKJD; the stored table carries BKJD and BJD_TDB (plus 2,454,833).

Outliers: after a first linear fit, events more than 5 median absolute deviations from the line (the Holczer et al. 2016 rule) are dropped and the fit is made again; dropped events stay in the table with `used` false and the reason.

### 3.3 The linear ephemeris

Weighted least squares of T_E = T_ref + P (E - E_ref), with E_ref the error-weighted mean epoch of the used events rounded to the nearest integer (Deeg 2015's central epoch). Outputs: P and sigma_P, T_ref and sigma_Tref, their covariance, the chi-square and the number of degrees of freedom, the rms of the residuals in minutes, and the number of events used. Errors are inflated by the square root of the reduced chi-square when it exceeds one. T0 at epoch 0 (the first transit, the epoch ExoFOP and the archive want) is T_ref - P E_ref with sigma squared equal to sigma_Tref squared plus E_ref squared sigma_P squared minus 2 E_ref times the covariance. A prediction at epoch E has sigma squared equal to sigma_Tref squared plus (E - E_ref) squared sigma_P squared (Deeg 2015, Eq. 18, in the general form). With fewer than 3 used events there is no ephemeris error, and the dossier says so.

### 3.4 Bootstrap over transits

`N_BOOT` = 200 resamples (a parameter; tests use 20). Each resample draws the used epochs with replacement, concatenates their points, and runs `transit_model.fit_transit` with the signal's period, T0 and duration. From the resamples that converge: the 16th, 50th and 84th percentiles of k, b, a/R*, T14 (hours) and the model depth (k squared, in ppm). The point estimate stays the rung 2 fit (the dossier does not re-fit the full curve); the errors were first written as the distances from the rung 2 value to the resample percentiles, floored at zero; ruling of 22 October 2026 (Task 1 re-review): a full-curve fit that sits at the edge of the resample distribution then reads "+0.000" on one side, which is not a 1-sigma error a referee can read, so the errors are the resample spread itself, minus = p50 - p16 and plus = p84 - p50, with the bootstrap median stored beside the point value and a dossier caveat when the point lies further from the bootstrap median than the larger half-width. When the rung 2 fit is absent or failed, the shape block carries only the box depth and duration: the depth is the per-event weighted mean with its scaled error (rung 2's `depth_ppm`, `depth_err_ppm`), the duration is the box width that maximises the depth-to-error ratio on each resample over a grid from half to twice the search duration, with its percentiles. Fewer than 20 converged resamples give no shape errors, with a reason. Ruling of 22 October 2026 (Task 1 review): at the 29.4-minute long cadence the uniform-disc fit is degenerate between k, b and a/R* whenever the ingress is not resolved, and a bootstrap over transits then measures the scatter around one local solution, not the width of the valley (on the module's test scene the injected b lay 8 sigma outside the bootstrap interval). The shape block therefore computes the ingress duration from the fitted geometry, (T14 - T23) / 2 with the uniform-disc contact times, and when it is shorter than the cadence (`CADENCE_MIN` = 29.4) marks the block `degenerate`, keeps the b and a/R* point values, quotes no error for them with the reason printed, and keeps the bootstrap errors of k, T14 and the model depth, which the depth and the box width constrain (Kipping 2010, MNRAS 408, 1758, on binning and the ingress).

### 3.5 Odd and even, secondary

- Odd and even depths with errors: from the per-event box depths and errors (`lc.ops.event_depths`), the weighted mean per parity with its error inflated by the square root of the parity's reduced chi-square; the difference in sigma from the two errors in quadrature. Rung 2's `parity_sigma` stays the vetting number; this block is the reporting number and the dossier prints both when they differ by more than 0.5 sigma.
- Secondary: the depth at phase 0.5 with its error (rung 2's `secondary_ppm` and `control_rms_ppm`), its 3-sigma upper limit (max(measured, 0) plus three errors), the strongest secondary at any phase with its phase and sigma (`free_secondary_ppm`, `free_secondary_phase`, `free_secondary_sigma`), and the geometric-albedo limit A_g,max = limit x (a/R* / k) squared when the fit is ok (an occultation depth of A_g (Rp/a) squared).

### 3.6 Where it goes

- Dossier JSON (`DOSSIER_VERSION` "3"): `ephemeris` gains `period_err_days`, `t0_bkjd_err`, `t0_bjd_err`, `t_ref_bkjd`, `t_ref_bjd`, `t_ref_err_days`, `epoch_ref`, `duration_h_err` (`{"minus", "plus"}`), `depth_ppm_err`, `depth_source` ("per-event weighted mean of the box depth"), `ephemeris_method`; new blocks `timing` (the per-transit table: epoch, t_bkjd, t_bjd, err_days, chi2_red, used, reason; the fit numbers; `ok` and `reason`), `shape` (k, b, a_over_r, t14_h, model_depth_ppm, each median, minus, plus; `n_boot`, `n_converged`, `seed`, `ok`, `reason`), `parity` (odd, even: depth_ppm, err_ppm, n; `difference_sigma`), `secondary` (the numbers of 3.5). `planet.rp_rearth` folds the k bootstrap error in quadrature with the stellar term.
- The page: the hero tiles print the period and the epoch with their errors in Expert; section 01's key-value list gains the errors, T_ref and the timing rms; section 02 gains the odd and even depths with errors and the secondary limit; section 03's expert view gains the shape block.
- The pack: `data/ephemeris.csv` rows carry minus and plus for every parameter that has them; `data/transit_times.csv`; `exofop/params_planet_<date>_001.txt` and `exofop/params_stellar_<date>_001.txt` in ExoFOP's pipe-delimited templates (the planet file's `target` is `TIC<tic>.01` when a TIC was given, else `TIC<TIC-TO-FILL>.01`, with `flag` `newctoi`, `disp` `PC`, the paper URL and the notes as placeholders; the README says the file is used only once a community candidate is allowed); the ExoFOP note, the archive email and the README quote P, T0 and the duration with errors; README step 5 gives the next three transits after the pack date with their 1-sigma windows; the Research Note's signal section quotes P, T_ref (BJD_TDB) and the duration with errors and the timing rms.
- The gate: `--zenodo-live` is refused when the stored dossier has no ephemeris error (`timing.ok` false) or no shape errors, with the reason.

## 4. The cross-check (`skysift crosscheck`, `tools/triceratops_check.py`)

### 4.1 Environment

TRICERATOPS 1.1.0 pulls numpy 1.26 and scipy 1.13 (dry run of 22 October), below the engine's numpy 2 requirement, so it cannot go into the engine's venv without changing the numerics the search version stands on. It goes into its own environment under the project, `.venv-triceratops` (git-ignored as `.venv-*/`), made with uv at Python 3.12, and the engine calls it as a subprocess. vespa was the alternative and is unmaintained (Python 2 era, astropy 1); TRICERATOPS is the tool TESS and K2 validations use and has a Kepler mode, so it is the choice.

### 4.2 Inputs and the adapter

`skysift crosscheck --target <id> --export <dir>` writes, from the stored dossier and the loader's whitened flux: `inputs.json` (target id, Gaia id, KIC, RA, Dec, G, the period, T0, duration and depth with errors, the stellar parameters, the Gaia neighbour list with separations and G, the neighbour KIC whose pixel files hold the star, the quarters with pixel files) and `lightcurve.csv` (time BKJD, whitened relative flux, error, quarter, within plus and minus three durations of each transit, the window the fit uses), and copies the stored pixel files of the neighbour KIC for the quarters the rung used (the files are in the blob; TRICERATOPS needs one image and a WCS per quarter).

`tools/triceratops_check.py --inputs <dir> --out result.json`, run with the other interpreter, builds a `triceratops.target` with `mission="Kepler"`, `ra` and `dec` given (which skips the KIC lookup) and `sectors=[]`, then sets the quarter images itself from the copied pixel files (the time-mean flux image, the reference pixel and the WCS-placed pixel coordinates of every star in its TIC list, exactly as the package does for a Kepler target it can download, so that the download step, which would fail for a star Kepler never targeted, is not needed). The aperture passed to `calc_depths` is the 3 x 3 box around the target in each quarter, the footprint of the KBonus PSF photometry; `dilution_corrected=True` because the PSF photometry already shares the light between stars; `exptime` is the Kepler long cadence, 0.0204 days; `P_orb` is the dossier's period; the light curve is the exported window. TRICERATOPS's TIC list is cross-matched with the dossier's Gaia neighbours within 1 arcsec; a dossier neighbour missing from the TIC list is reported in the result and added as a star with `Tmag` set from G (G minus 0.5 for a red star; stated as an estimate). The run is repeated `N_RUNS` = 10 times with different seeds and the result stores every run's FPP and NFPP plus their median, since the KOI-1623 paper found run-to-run variation (its median 1e-6 against a mean 0.0058).

`result.json`: the package version, the run date, the median and the list of FPP and NFPP, the per-scenario probability table of the median run, the stars used (id, Tmag, separation, flux ratio, required depth), the inputs' hash, the caveats (TESS-band limb darkening and magnitudes used for a Kepler light curve; the Gaussian aperture model; the package's own priors). `skysift crosscheck --target <id> --import result.json` stores it in a new `dossiers.cross_check` JSON column (migration `004_cross_check`) with the dossier fingerprint it was made against; the page's section 03 shows a "Cross-check" block; the pack's texts quote it ("An independent estimate with TRICERATOPS (Giacalone et al. 2021) gives a false-positive probability of X and a nearby false-positive probability of Y"); `docs/superpowers/notes/2026-10-22-fpp-cross-check.md` records the two stars' numbers against SkySift's with the reasons for the differences.

### 4.3 The gate

A disagreement above a factor of three does not block the live gate. Reasons: the two estimates answer differently scoped questions (TRICERATOPS counts transiting planets and binaries on nearby and bound stars that SkySift's three scenarios leave out, uses the TESS band and its own priors, and varies between runs by orders of magnitude at low FPP), so a ratio between two small numbers is noise, not a verdict. What does block: no cross-check stored, a cross-check made against another fingerprint (period, depth or neighbours changed), or a cross-check that reads as a likely false positive on the tool's own scale (median FPP at or above 0.5, or median NFPP at or above 0.1, Giacalone et al. 2021's thresholds). The pack always prints both numbers side by side with one sentence on the difference when the ratio exceeds three.

## 5. The centroid systematic floor (pixel rung)

### 5.1 What is measured, and why on this data

Kepler's Data Validation added a constant 0.2/3 arcsec term in quadrature to each offset component so that no offset under 0.2 arcsec could reach 3 sigma (Bryson et al. 2013, VI.3; Twicken et al. 2018, III.4.2). That constant was set for the PRF-fit estimator on Kepler targets; SkySift's estimator is a 3 x 3 flux-weighted centroid on a season frame stacked from several quarters at integer shifts, so its floor has to be measured on its own data.

The floor is measured on the live pixel files (eight stars, 129 quarter files, every star with a rung 5 row), not on the calibration set, which has no pixels. For each season frame the tool measures, per quarter, the 3 x 3 flux-weighted centroid of that quarter's out-of-transit direct image around the target's rounded frame position (data with no transit in it, high signal to noise), and the scatter of those centroids about the season's mean. That scatter is the quarter-to-quarter systematic the Monte Carlo error cannot see: pointing differences between quarters of one season, PRF changes, and the integer co-registration. The same is measured on the neighbour (the Kepler target, bright, always on the frame) as a check. Seasons with a cut box (the partial-box branch's `box_pixels` below 9) are left out of the pooled number and reported apart. The pooled root-mean-square per axis over all complete-box seasons with at least two usable quarters is the candidate floor. A second measurement validates it: on KIC 6146676 (three voting seasons, 17 sigma) the per-quarter difference-image centroids of the real transit are compared with their Monte Carlo errors; the excess scatter must not exceed the floor.

The registration residual is reported separately: per season, the event-weighted mean of each quarter's fractional offset from the first quarter's position (`residual_offsets_pix`, which the season builder already computes). If that mean exceeds the floor for any voting season of a live signal, the season's star positions are corrected to the event-weighted mean position (an exact correction from the quarters' own WCS positions, with precedent in Bryson et al. 2013 combining per-quarter offsets rather than images) and the correction is stored per season as `registration_shift_pix`; otherwise it is left as it is and recorded in the note. This is decided by the measurement, and the ruling goes in the ledger.

### 5.2 The rule

`CENTROID_FLOOR_PIX` in `lc/pixels.py`, with the measurement date and the tool that made it; `centroid_sigma_pix` becomes the quadrature sum of the Monte Carlo error, the edge allowance (from the partial-box branch) and the floor; `centroid_sigma_axis_pix` the same with the floor along the axis; the raw Monte Carlo error is kept as `centroid_sigma_mc_pix` and the floor as `floor_pix` in every season's metrics. `separation_sigma` is the bisector distance over the inflated axis error. The rung's `rule_version` becomes 3. The FPP reads the inflated error through `centroid_estimate`, so the confusion radius widens with it.

Recalibration path: `python -m tools.centroid_floor --db <copy> --root <repo> --out <json>` prints the pooled numbers and the per-season table; the constant is changed by hand with the new date, and the recheck reopens every rung 5 row whose `rule_version` is below the current one (passes and fails included: the floor changes every season's error, so a verdict that was marginal must be measured again; a pass at 17 sigma comes back as a pass with new numbers). The pixel files come from the blob cache.

### 5.3 The live run

Back up `.superpowers/sample-run/engine.db` to `engine.db.bak-<date>.db` (with its WAL checkpointed first), run `skysift recheck`, and report every rung 5 row whose status or reason changed, with the before and after numbers per season. This runs after the partial-box branch is merged into main and brought into this branch with the app's sync tool, since both change `lc/pixels.py`.

## 6. The smaller rows

- Stellar provenance: `logg` joins the star block (from mass and radius draws); `data/stellar_provenance.csv` lists each parameter with value, errors, method, inputs (Gaia DR3 id, parallax, BP-RP, 2MASS Ks) and reference; `exofop/params_stellar_<date>_001.txt`; the note's star paragraph gains "no Kepler stellar catalogue covers a background star, so the parameters come from Gaia colour and parallax".
- Dilution: the KBonus `CROWDSAP` and `PSFFRAC` from the target meta and the rung 5 leak depth go into the ephemeris CSV's parameters, the README's numbers table and one sentence of the note's pixel section.
- Data behind the figure: `note/figure1_data.csv` (phase in hours, binned flux, error, count, the model).
- Software and facilities: the note gains `\software{SkySift <version> (search version <v>, commit <sha>), numpy, scipy, astropy, matplotlib, triceratops}` and `\facilities{Kepler, Gaia, MAST, Exoplanet Archive}`, and the data-availability paragraph gains "the SkySift code at the stated commit is archived at doi:<software DOI placeholder>"; the README's provenance section names the same; the archive email names the search version and commit.
- Model limits: one clause in the note's detection paragraph ("a uniform-disc model without limb darkening on a circular orbit, averaged over the 29.4-minute cadence").
- Routes: README step 2 says files and the note go on the KIC or TIC page now and the planet-parameter file waits for a community candidate, which needs a peer-reviewed paper; step 3 says the Note is editor-moderated, not refereed, and gives the DOI; step 4 says the archive takes parameters from an accepted refereed paper, so the email is sent once such a paper exists, and the Note alone does not open that route; the archive email says the same in its first sentence.
- Word budget: the Research Note stays inside 1,500 words with the additions; `note_problems` is asserted in the tests on the KIC 6146676 dossier.

## 7. Versions and gates

`DOSSIER_VERSION` "3" (regenerates every stored dossier), `PACK_VERSION` "3", pixel `rule_version` 3. The live Zenodo gate: approval, sign-off, not stale, ephemeris errors present, shape errors present, cross-check present for the same fingerprint and not a likely false positive.

## 8. Testing

Unit: `ephemeris` on the synthetic injected planet of `tests/synthetic.py` (the recovered period within 2 sigma of the injected one, the epoch error smallest at the centre, the prediction error growing with distance, an outlier dropped by the MAD rule, the bootstrap percentiles bracketing the injected k, too few events giving no error with a reason); dossier generation carrying the new blocks and version 3; the page in both modes printing the errors; the pack's CSVs, ExoFOP files, texts and the Research Note within its limits; the gate refusals; the cross-check import with a fake result and the gate's three blocking conditions; the floor in the season metrics and the recheck reopening rows below rule version 3; the tool on the synthetic TPF scene. The golden test of KIC 6146676 gains the ephemeris numbers once the live dossier is regenerated.

## 9. Calls made here (for Vikram to check)

- Linear fit for the ephemeris, bootstrap over transits for the shape (3.1), with the reference epoch at the centre of the series.
- Fixed-depth template for the per-transit times; the 5 MAD outlier rule; errors inflated by the reduced chi-square.
- TRICERATOPS in its own environment, driven by a subprocess, with the quarter images taken from the stored pixel files and a 3 x 3 aperture standing for the PSF photometry; ten runs, the median reported.
- A factor-of-three disagreement does not block; a missing, stale or likely-false-positive cross-check does.
- The floor measured on out-of-transit direct images, quarter to quarter within a season, pooled over the live stars; the registration residual reported apart and corrected only if it exceeds the floor on a live voting season.
- The recheck reruns every rung 5 row below rule version 3, passes included.
- The README stops promising the archive route from a Research Note alone.
