# Dossier comparison: KIC 6146676 generated against the hand-built study

21 October 2026. The dossier generator, the pixel recheck step and the pack builder ran for real on a copy of the
production database (`.superpowers/sample-run/engine.db`, copied with the sqlite backup API to
`.superpowers/dossier-run/engine.db`), with the blob store of the sample run and a snapshot of its null calibration
(999 rows of search version 5+42061727, 1,998 null searches). The generated numbers for the candidate KIC 6146676 are
set beside the hand-built study in `docs/candidates/kic-006146676/`. KIC 5689121 got a dossier only (no pixel step),
for its FPP factor table.

## Headline

Every quantity with a value on both sides agrees with the hand-built study within the tolerances the tool applies (the
study's quoted error where it gives one, else the stated tolerance): period, SDE, depth, duration, odd and even
depths, the secondary, Teff, stellar radius and mass, distance, Rp, a, a/R*, Teq, the transit probability, the four
rung 3 season depths, the eight per-season centroid distances (all within 0.1 pixel, the largest 0.057 pixel) and the
six two-source depths (36 of 38 rows). The two rows without a generated value
are the two-source depths of archive season 3, which did not vote. The habitable-zone flags read as expected
(conservative False, optimistic True) and the insolation is 1.22 (-0.12 +0.14) Earth.

Two rung 2 rows differ most from the study, though inside its error. The secondary at phase 0.5 reads 24 ppm above the
study (95.4 ppm, 0.30 of the study's error) because rung 2 takes the mean of the per-event depths at the search t0,
while the study stacked the phase 0.5 window at a t0 refined by 2.6 minutes (135.7901 against 135.7883 BKJD). The even
depth reads 38 ppm above the study (2,153 ppm, 0.22 of its error) for the same reason: per-event means at the search t0
against a stack of the even transits at the refined t0 in a 3.0 h box.

The real pixel verdict for KIC 6146676 is a pass: "the dip is on the target in 3/3 seasons (centroid >= 17.5 sigma
nearer)", with `n_seasons_voting` / `n_seasons` = 3 / 4 and `tpf_missing` empty (18 of 18 pixel files of KIC 6146666
read). The signal moved from stage 1 to stage 2 on the copy.

Four things did not hold on real data and are findings below: the Gaia star query failed with HTTP 400 until it was
fixed, archive season 3 does not vote, the FAP is "1 in 170" and not the "0 of 1,716" the spec quoted, and the pack
builder failed on the real rung 5 row (since fixed in the pack round). The fix round of the same day (appendix) changed
the centroid error and the provenance. After the final review the on-target binary term is two sub-scenarios (finding
8): KIC 6146676 reads 0.78 percent ("under 1 percent") and KIC 5689121 1.2 percent ("about 1 percent"). The numbers in
this note come from that last run.

## What ran

| Step | Command | Wall clock | Result |
| --- | --- | --- | --- |
| Database copy | `sqlite3` backup API, read-only source | 0.9 s | 73.3 MB copy |
| Pixel recheck, signal 1032 | `skysift.recheck.recheck(..., signal_ids=[1032], pixels=True, with_novelty=False)` from a driver script with a MAST-only fetch guard (see deviations) | 77.3 s in the engine, 89.6 s for the process | ladder recheck pass (rungs 1 to 3), pixel step pass, stage 1 to stage 2 |
| Dossier KIC 6146676, first run | `python -m skysift dossier --target kic-006146676 --export .superpowers/dossier-run/kic-006146676.html` | 16.5 s | written, but star route "none" (Gaia HTTP 400) |
| Dossier KIC 5689121, first run | `python -m skysift dossier --target kic-005689121` | 15.1 s | written, star route "none" (Gaia HTTP 400) |
| Dossier KIC 6146676, after the fix | same with `--force` | 24.5 s | written, route "mann", export 459 kB with no external URL |
| Dossier KIC 5689121, after the fix | same with `--force` | 21.3 s | written, route "mann" |
| Pack KIC 6146676 | `python -m skysift pack --target kic-006146676` | 17.0 s | failed (TypeError in `pack.pixel_sentence`) |
| Pack, retried | same, then with `--out .superpowers/dossier-run/packs/kic-006146676/run3` | 24.9 s, 47.5 s | refused (half-written folder), then the same TypeError |

Every engine command ran with `SKYSIFT_DATA_DIR=.superpowers/dossier-run`,
`SKYSIFT_BLOB_URL=file://.superpowers/sample-run/blobs`, `SKYSIFT_DATABASE_URL=sqlite:///.superpowers/dossier-run/engine.db`
and `PYTHONUTF8=1`. The dossier and pack commands ran below normal priority (`start /belownormal`).

## Network calls (the copy's `queries` table, rows 3137 to 3191)

| Rows | Service | Status | What | Bytes |
| --- | --- | --- | --- | --- |
| 3137 | mast | 200 | the target pixel file listing of KIC 6146666 | 2,508 |
| 3138 to 3155 | mast | 200 | 18 long-cadence target pixel files of KIC 6146666, Q0 to Q17 (04:06:20 to 04:07:25 UTC) | 49,440,581 |
| 3156, 3158 | gaia | 400 | the `gaia_source` row of each star, old query asking `gaia_source` for the FLAME columns | 757 each |
| 3157, 3159 | gaia | 200 | the 20 arcsec neighbour cone of each star | 608, 527 |
| 3160, 3164 | gaia | 200 | the `gaia_source` row joined to `astrophysical_parameters` (fixed query) | 359, 360 |
| 3161, 3165 | gaia | 200 | the Bailer-Jones distance | 57, 58 |
| 3162, 3166 | gaia | 200 | the 2MASS Ks cross-match | 29, 29 |
| 3163, 3167 | gaia | 200 | the neighbour cone again | 608, 527 |
| 3168 to 3171 | gaia | 200 | fix round: the four queries of KIC 6146676 again, from the clean tree at f431ed0 (06:20:51 to 06:20:57 UTC) | 359, 57, 29, 608 |
| 3172 to 3175 | gaia | 200 | re-review round: the same four queries, from a clean tree at 46db518, a commit later amended away (06:59:23 to 06:59:29 UTC) | 359, 57, 29, 608 |
| 3176 to 3179 | gaia | 200 | re-review round: the same four queries, from the clean tree at 2588897, the run behind the golden block (07:07:11 to 07:07:17 UTC) | 359, 57, 29, 608 |
| 3180 to 3183 | gaia | 200 | final review: the four queries of KIC 6146676 from HEAD 04173f6 with this round's diff applied, for the interim golden block of 0b84077 (08:39:29 to 08:39:37 UTC) | 359, 57, 29, 608 |
| 3184 to 3191 | gaia | 200 | final review: the four queries of each star from the clean tree at 0b84077 (KIC 6146676 08:46:54 to 08:47:01, the golden block; KIC 5689121 08:47:23 to 08:47:29) | 359, 57, 29, 608; 360, 58, 29, 527 |

Nothing else reached the network: no catalogue query (rung 4 was left out of the ladder recheck), no light curve
download (both raw files were in the blob store) and no Zenodo call (no token).

Files written to the shared blob store `.superpowers/sample-run/blobs` (recorded only in the copy, so the live database
does not know them): the 18 pixel files `kplr006146666-*_lpd-targ.fits.gz` (49.4 MB, kind `tpf`, unpinned) and
`7e/7e207f54...ca219e_pixels.npz` (8,904 bytes, kind `pixels`, pinned, the signal's rung 5 arrays).
A note for Vikram: these files stay in the shared blob store. They are content-addressed, so a later live pixel step
for KIC 6146676 writes the same names, but until then the live database's retention and budget accounting do not see
the 49.4 MB.

## Findings

**1. The Gaia star query failed with HTTP 400 on real data.** `stars.Q_SOURCE` asked `gaiadr3.gaia_source` for
`radius_flame` and `mass_flame`, which live in `gaiadr3.astrophysical_parameters`. The unit tests answer every query
from a fake fetch, so the column error never surfaced. The first two dossiers therefore had no star (route "none"), no
planet radius and an FPP built on the 0.5 Rsun fallback (KIC 6146676 at 0.062 percent, KIC 5689121 at 3.1 percent), and
the command exited 0 with a caveat that advised a retry. Since the fix round each failed Gaia query is logged at warning
level and printed ("Gaia query failed: ..."), a refused one (HTTP 4xx) gets its own caveat ("A Gaia query was refused
(HTTP 400): the star block comes from the fallback route"), and `skysift dossier` exits 3 while still writing the
dossier. Since the final review a failed neighbour cone, or a target without a G magnitude, leaves the FPP "not computed
(no neighbour data)" on the tile, the page and in the pack texts, where it used to give a number with the background
binary term silently at zero. The query now joins the two tables (`LEFT OUTER JOIN gaiadr3.astrophysical_parameters AS
ap ON ap.source_id = g.source_id`), and the routing keys of the fake fetch in `test_stars.py` and `test_dossier.py`
follow it. The numbers below are from the second run.

**2. Archive season 3 does not vote.** The engine localizes season 3 (Q5, Q9, Q13) but its 3x3 box around KIC 6146676
(col 0.71, row 3.02 on a 5 by 7 frame) reaches pixel (row 4, col 0), which is invalid on the season's union frame, so
the season reads `non_voting_reason` "box incomplete: target". Its centroid still sits 0.218 pixel from the candidate
and 2.700 pixels from KIC 6146666 (separation 16.1 sigma), the same answer as the voting seasons. Q1 is also left out
(one transit in the quarter, below `QUARTER_EVENTS_MIN` of 2), so the engine uses 34 events and unusable quarters
[0, 1, 17] against the study's 35 events and [0, 17]. The study had no box rule and counted 4 of 4 seasons. The engine
counts 3 of 3 voting seasons out of 4. The verdict is the same.

**3. The two-source depth errors are 3 to 6 times the study's.** The depths agree (season 0: 2,872 ppm against 2,868
ppm; season 1: 2,284 against 2,321; season 2: 1,813 against 1,837), but the engine quotes 1,199, 894 and 606 ppm where
the study quotes 201, 152 and 126 ppm. The engine propagates the error of the fitted direct flux of the faint star
(`lc.pixels._depth`, from the least-squares Jacobian scaled by the reduced chi-square of a Gaussian PSF on a Kepler
PRF), and the study used the difference-amplitude error alone. The depths on KIC 6146666 carry almost no direct-flux
term (it is the bright star), so they match the study to within 1 ppm of error. I think the engine's error is the
honest one. It does not change the verdict, which rests on the centroid.

**4. The centroid distances differ by up to 0.057 pixel, from the star positions more than from the centroids.** In
seasons 0 and 1 the engine's centroid equals the study's to 1e-8 pixel. The distances differ because the engine places
KIC 6146676 from its catalogue RA/Dec and KIC 6146666 from each file's RA_OBJ/DEC_OBJ header through the file's WCS,
while the study placed both from Gaia DR3 (season 0: KIC 6146676 at col 0.508 against 0.486). Season 2 differs in the
centroid itself by 0.016 pixel in column, likely from the event selection (the engine asks for 6 out-of-transit
cadences with 2 per side, the study for 10 with 3 per side). Season 3 lost Q1 (finding 2). All are inside the 0.1
pixel tolerance.

**5. The stellar radius error is half the study's.** The engine reads 0.505 +- 0.016 Rsun and 0.503 +- 0.013 Msun from
the Mann relations at Ks, while the study's baseline is the Mamajek colour route (0.498 -0.040 +0.036 Rsun). The study
ran the Mann route only as a check, and that check gave exactly the engine's values (0.505 +- 0.016 Rsun, 0.503 -0.012
+0.013 Msun). The planet errors shrink with it: Rp 2.63 +- 0.08 against 2.61 -0.21 +0.19 Earth radii, Teq 268 -7 +8
against 267 -12 +13 K. This is the route the spec chose (5.3), not a disagreement.

**6. The insolation is 1.22, not 1.24.** The 1.24 of Task 3 came from the test fixture (Ks 13.030, seed 1, R 0.509
Rsun). The archive gives Ks 13.055 and the dossier seed is 20261021, so R is 0.505 Rsun and the insolation 1.22 (-0.12
+0.14) Earth. The difference is a sixth of the error. The habitable zone edges move with it: conservative 0.2159 to
0.4097 AU, optimistic 0.1634 to 0.4261 AU, with the planet at 0.1829 AU, so conservative False and optimistic True.

**7. The FAP is "1 in 170", not "0 of 1,716".** 11 of the 1,998 null searches reached SDE 50.5, all of them inverted
light curves (148.1 at the top). None of the 999 scrambled searches came near it, so the scrambled set alone would read
"1 in 1,000". The calibration matched the signal's search: the sweep version 5+6cfe4cf4 maps to calibration version
5+42061727 and every row carries it (`version_matches` true). The dossier's FAP block now carries the split (`k_by_kind`
inverted 11, scrambled 0; `n_by_kind` 999 each). Since the final review the page, the verdict popup and the standalone
export show it in one line under the pooled number ("inverted nulls 11 of 999, scrambled 0 of 999"), and the pooled
number stays the headline, since it can only overstate the false-alarm rate. I think the inverted null is not a fair
null for a dip on a quiet M dwarf: inverting a light curve turns every brightening (flares, or the bumps of a pulsator)
into dips, and 11 of the 999 calibration stars give a box-shaped peak above 50 that way. KIC 5689121 reads "1 in 51" (38
of 1,998; 37 inverted, 1 scrambled at 20.2). Which nulls count is a decision for the FAP rule, not something to tune
here.

**8. With the EB term corrected, KIC 6146676 is still below the 1 to 60 percent band of spec 4.2 and KIC 5689121
sits at its lower edge.** The final review found that spec 4.2 multiplied the secondary limit and the odd/even limit
into one on-target binary scenario, though the two tests belong to exclusive hypotheses: an eclipsing binary at P shows
a secondary at phase 0.5, one at 2P shows odd and even eclipses of different depth, and no binary is both. As ruled,
the on-target binary is now two sub-scenarios sharing the EB prior equally, EB-P (geometry at P, data S_sec x S_v) and
EB-2P (geometry at 2P, data S_oe x S_v), summed with PL and BEB. Both numbers, for the sign-off packet:

| Target | FPP, spec 4.2 as first written | FPP, two sub-scenarios (as ruled) | Words now |
| --- | --- | --- | --- |
| KIC 6146676 | 0.000139 (0.0139 percent; EB weight 2.86e-7) | 0.00775 (0.78 percent; EB-P 1.52e-5, EB-2P 8.5e-7, PL 2.05e-3) | under 1 percent |
| KIC 5689121, no pixel data | 0.00895 (0.90 percent; EB weight 1.65e-7) | 0.0123 (1.2 percent; EB-P 1.07e-5, EB-2P 1.05e-6, BEB 3.08e-5, PL 3.43e-3) | about 1 percent |

The reviewer's scratch estimate of 1.3 percent for KIC 6146676 gave each hypothesis the full binary fraction and its
own period window (g 0.0342 over P/2 to 2P for EB-P, 0.0413 over P to 4P for EB-2P); the same arithmetic gives 1.29
percent here (1.44 percent for KIC 5689121). The ruling's equal share halves the prior of each and keeps the one window
of the original EB prior (0.0413), which gives the 0.78 percent above. That choice decides whether KIC 6146676 enters
the band, so it belongs in the sign-off, and `SIGNED_OFF` stays False.

The background binary term still vanishes for KIC 6146676 (X_mean 2.2e-222). Since the fix round the averaged centroid
uses the three voting seasons only, and its error sigma_c is the larger of two numbers: the error of the
inverse-variance weighted mean (0.1373 arcsec, 0.0345 pixel) and the seasons' sample scatter (ddof 1) about that mean
over sqrt(3) (0.1103 arcsec). The statistical error wins, so sigma_c is 0.1373 arcsec and the confusion radius stays at
its 2.0 arcsec floor. The target sits 0.762 arcsec (about 5.5 sigma_c) from the averaged centroid, and the nearest
bright neighbour, 6.38 arcsec from it, sits 32 sigma outside the confusion radius. The spec expected about 4.6 sigma,
which is the study's multi-quarter offset error (0.9 arcsec), not the per-season Monte Carlo error; with 0.9 arcsec
the product FPP would have been 0.0174 percent, so the miss of the band was an on-target binary matter, which the
correction above addresses. Rung 5's own `centroid_sigma_pix_mean` (0.0641 pixel) is a different number on purpose:
the plain mean of the voting seasons' errors, kept as the rung reports it, while the FPP needs the error of the
averaged centroid.

**9. The pack builder failed on the real rung 5 row (fixed in the pack round).** `pack.pixel_sentence` takes the median
of the per-season two-source depths, and a season that did not vote stores `depth_target_ppm` as `[null, null]`, so
`np.median` raises `TypeError: '<' not supported between instances of 'NoneType' and 'float'`. The crash leaves a
half-written folder, and the retry then stops with "is not empty and is not a submission pack; choose another --out".
The pack round (581be67, e2d334a) now leaves the non-voting seasons out of the median and swaps the pack folder into
place.

**10. "Season" means two numberings on one page.** Rung 3 groups quarters as quarter mod 4 (season 0 is Q4, Q8, Q12,
Q16), as the study's `lc_analysis.py` did. Rung 5 uses the archive SEASON (season 0 is Q2, Q6, Q10, Q14). Both agree
with their counterpart in the study, but a reader of one dossier sees two different "season 0". The committed
dashboard seed now marks season 3 as the one that does not vote, as on real data, and the dossier's pixel section takes
the season and its quarters from rung 5 (the npz `seasons` key and the rung metrics), never from the quarter numbers.

**11. Smaller points.** The ladder recheck wrote rows at ladder version 4 but left the signal row's `ladder_version`
null, so the first dossiers printed a null ladder version. The provenance now gives the ladder of each rung (from the
rechecks row a test row points to, else the signal's, else the run's) and the effective one, "4"; the rung 4 row is
from the 19 October sweep under ladder 3 (table below). `lc.pixels._epoch_dt` casts NaN times to int64 (16
RuntimeWarnings "invalid value encountered in cast" in the recheck log). The cadences involved have NaN time and are
dropped later, but the cast is noisy.

## Stellar and planet parameters

| Quantity | KIC 6146676 generated | KIC 6146676 hand-built | KIC 5689121 generated |
| --- | --- | --- | --- |
| Route, spectral type | Mann (Ks), M1V | Mamajek colour table (Mann as a check), M1V | Mann (Ks), M2V |
| Teff (K) | 3,653 -73 +90 | 3,653 -73 +91 | 3,582 |
| Radius (Rsun) | 0.505 +- 0.016 | 0.498 -0.040 +0.036 | 0.504 |
| Mass (Msun) | 0.503 +- 0.013 | 0.495 -0.045 +0.036 | 0.502 |
| Distance (pc) | 303.0 -3.5 +3.6 | 303.0 -3.5 +3.6 | 290.7 |
| Rp (Earth radii) | 2.63 +- 0.08 | 2.61 -0.21 +0.19 | 2.54 |
| a (AU) | 0.1829 +- 0.0015 | 0.1819 -0.0057 +0.0043 | 0.0894 |
| Teq (K, A 0.3) | 268 -7 +8 | 267 -12 +13 | 375 |
| Insolation (Earth) | 1.22 -0.12 +0.14 | about 1.2 | 4.73 |
| Conservative HZ (AU), in it | 0.2159 to 0.4097, False | not stated | 0.2076 to 0.3953, False |
| Optimistic HZ (AU), in it | 0.1634 to 0.4261, True | not stated | 0.1572 to 0.4111, False |
| Gaia cross-check | GSP-Phot Teff 3,855 K, FLAME 0.539 Rsun and 0.560 Msun | GSP-Phot 3,855 K, FLAME 0.539 Rsun | |

## Deviations from the brief

- The pixel step ran through `skysift.recheck.recheck` with `with_novelty=False` from a driver script, not through
  `skysift recheck --signal-id 1032`. The signal had never been rechecked, so the recheck runs the ladder step first,
  and with rung 4 it queries the catalogues, which the network ruling for this task excludes. Rungs 1 to 3 were
  re-vetted offline and the rung 4 row of 19 October stays the latest. The driver's fetch refused any service other than
  MAST. Its below-normal priority call did not take effect (wrong ctypes handle type), so the recheck ran at normal
  priority. The dossier and pack commands ran below normal.
- `stars.py` changed (finding 1). Without it the comparison of Rp, a, Teq and the stellar parameters was impossible.
- Step 4 of the brief (the dashboard on port 8769 with screenshots) was not run: the controller ruled the ports 8765 to
  8769 out of bounds for this task.
- The golden block lives at `tests/golden/kic-006146676-generated.json` (17.7 kB), as the controller asked, and not
  under `docs/superpowers/notes/`. `python -m tools.dossier_compare --write-golden` writes it from the dossier JSON.

## Reproduce

```bash
python -m tools.dossier_compare --data .superpowers/dossier-run/dossiers/kic-006146676/dossier_data.json \
    --reference docs/candidates/kic-006146676/dossier_data.json \
    --fpp .superpowers/dossier-run/dossiers/kic-005689121/dossier_data.json \
    --out .superpowers/dossier-run/comparison.md --write-golden tests/golden/kic-006146676-generated.json
```

The same tables come out of the committed golden block (`--data tests/golden/kic-006146676-generated.json --out <scratch
file>`; without `--out` the tool prints the tables and writes nothing). The tool's output follows.

### Comparison: KIC 6146676 generated against the hand-built study

36 of 38 rows agree within the tolerances the tool applies (the study's quoted error where it gives one, else the stated tolerance); disagreements: none; compared without an error to judge by: none; missing a value on one side: two-source depth on KIC 6146676, season 3, two-source depth on KIC 6146666, season 3.

| Quantity | Unit | Generated | Hand-built | Difference | Error used | Difference / error | Agree |
| --- | --- | --- | --- | --- | --- | --- | --- |
| period | d | 40.291087 | 40.29109 | -3e-06 | 5e-06 (rounding of the study's five decimals) | -0.60 | yes |
| SDE of the search peak |  | 50.501758 | 50.501758 | -1.41e-07 | 0.01 (same search and version; 0.01 tolerance) | -0.00 | yes |
| depth, box (search) | ppm | 2165.2784 | 2165.2784 | 2.569e-05 | 106.1 (hand-built event-mean error) | +0.00 | yes |
| depth, mean of the events (rung 2) | ppm | 2186.63 | 2196.6 | -9.97 | 106.1 (hand-built event-mean error) | -0.09 | yes |
| duration T14 from the transit fit | h | 3.8647124 | 3.918 | -0.05329 | 0.3918 (none quoted; 10 percent fit-duration error of skysift.fpp) | -0.14 | yes |
| odd-transit depth | ppm | 2222.4768 | 2202.2 | 20.28 | 104.5 (hand-built odd error) | +0.19 | yes |
| even-transit depth | ppm | 2153.1942 | 2115.1 | 38.09 | 169.7 (hand-built even error) | +0.22 | yes |
| secondary depth at phase 0.5 | ppm | 95.430951 | 71.6 | 23.83 | 80.5 (hand-built secondary error) | +0.30 | yes |
| stellar Teff | K | 3653 | 3653 | 0 | 91 (hand-built 16th to 84th percentile) | +0.00 | yes |
| stellar radius | Rsun | 0.505 | 0.498 | 0.007 | 0.036 (hand-built 16th to 84th percentile) | +0.19 | yes |
| stellar mass | Msun | 0.503 | 0.495 | 0.008 | 0.036 (hand-built 16th to 84th percentile) | +0.22 | yes |
| distance | pc | 303 | 303 | 0 | 3.6 (hand-built 16th to 84th percentile) | +0.00 | yes |
| planet radius Rp (from k) | Rearth | 2.63 | 2.61 | 0.02 | 0.19 (hand-built 16th to 84th percentile) | +0.11 | yes |
| semi-major axis a | AU | 0.1829 | 0.1819 | 0.001 | 0.0043 (hand-built 16th to 84th percentile) | +0.23 | yes |
| a / R* |  | 77.9 | 78.6 | -0.7 | 3.6 (hand-built 16th to 84th percentile) | -0.19 | yes |
| equilibrium temperature Teq (A 0.3) | K | 268 | 267 | 1 | 13 (hand-built 16th to 84th percentile) | +0.08 | yes |
| transit probability R*/a |  | 0.0128 | 0.0127 | 0.0001 | 0.0006 (hand-built 16th to 84th percentile) | +0.17 | yes |
| rung 3 depth, season 0 (quarter mod 4) | ppm | 2164.035 | 2173.8 | -9.765 | 477.2 (both errors in quadrature) | -0.02 | yes |
| rung 3 depth, season 1 (quarter mod 4) | ppm | 2119.9006 | 2099.9 | 20 | 267.5 (both errors in quadrature) | +0.07 | yes |
| rung 3 depth, season 2 (quarter mod 4) | ppm | 2298.6642 | 2257.2 | 41.46 | 295.1 (both errors in quadrature) | +0.14 | yes |
| rung 3 depth, season 3 (quarter mod 4) | ppm | 2107.7664 | 2098.8 | 8.966 | 328.9 (both errors in quadrature) | +0.03 | yes |
| centroid to KIC 6146676, season 0 | pix | 0.21998905 | 0.19716829 | 0.02282 | 0.1 (0.1 pixel tolerance) | +0.23 | yes |
| centroid to KIC 6146666, season 0 | pix | 3.0716897 | 3.0533061 | 0.01838 | 0.1 (0.1 pixel tolerance) | +0.18 | yes |
| centroid to KIC 6146676, season 1 | pix | 0.17923154 | 0.18763315 | -0.008402 | 0.1 (0.1 pixel tolerance) | -0.08 | yes |
| centroid to KIC 6146666, season 1 | pix | 2.8219736 | 2.8177304 | 0.004243 | 0.1 (0.1 pixel tolerance) | +0.04 | yes |
| centroid to KIC 6146676, season 2 | pix | 0.13109303 | 0.10449825 | 0.02659 | 0.1 (0.1 pixel tolerance) | +0.27 | yes |
| centroid to KIC 6146666, season 2 | pix | 2.7937402 | 2.8341234 | -0.04038 | 0.1 (0.1 pixel tolerance) | -0.40 | yes |
| centroid to KIC 6146676, season 3 | pix | 0.21781932 | 0.16084456 | 0.05697 | 0.1 (0.1 pixel tolerance) | +0.57 | yes |
| centroid to KIC 6146666, season 3 | pix | 2.6998826 | 2.7568709 | -0.05699 | 0.1 (0.1 pixel tolerance) | -0.57 | yes |
| two-source depth on KIC 6146676, season 0 | ppm | 2871.5144 | 2868.1033 | 3.411 | 201.2 (hand-built fit error) | +0.02 | yes |
| two-source depth on KIC 6146666, season 0 | ppm | 12.053605 | 12.03878 | 0.01482 | 70.74 (hand-built fit error) | +0.00 | yes |
| two-source depth on KIC 6146676, season 1 | ppm | 2284.1047 | 2320.8304 | -36.73 | 152.4 (hand-built fit error) | -0.24 | yes |
| two-source depth on KIC 6146666, season 1 | ppm | -36.918121 | -37.083055 | 0.1649 | 41.86 (hand-built fit error) | +0.00 | yes |
| two-source depth on KIC 6146676, season 2 | ppm | 1813.0299 | 1836.6003 | -23.57 | 125.7 (hand-built fit error) | -0.19 | yes |
| two-source depth on KIC 6146666, season 2 | ppm | -22.873909 | -16.963695 | -5.91 | 54.54 (hand-built fit error) | -0.11 | yes |
| two-source depth on KIC 6146676, season 3 | ppm | n/a | 2322.517 | n/a | 174.9 (hand-built fit error) | n/a | n/a |
| two-source depth on KIC 6146666, season 3 | ppm | n/a | -20.188134 | n/a | 45.59 (hand-built fit error) | n/a | n/a |
| leak test: dip in KIC 6146666's own flux | ppm | -9.48671 | -8.9 | -0.5867 | 12.4 (hand-built SAP leak-test error) | -0.05 | yes |

### Pixel step per season

| Season | Quarters (generated / hand-built) | Events | Nearer | separation_sigma | voting | non_voting_reason | Centroid sigma (pix) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 0 | 2, 6, 10, 14 / 2, 6, 10, 14 | 9 / 9 | target | 40.9 | yes | none | 0.04652 |
| 1 | 3, 7, 11, 15 / 3, 7, 11, 15 | 9 / 9 | target | 20.5 | yes | none | 0.07079 |
| 2 | 4, 8, 12, 16 / 4, 8, 12, 16 | 8 / 8 | target | 17.5 | yes | none | 0.07493 |
| 3 | 5, 9, 13 / 1, 5, 9, 13 | 8 / 9 | unknown | 16.1 | no | box incomplete: target | 0.07549 |

Rung 5: pass, "the dip is on the target in 3/3 seasons (centroid >= 17.5 sigma nearer)"; n_seasons_voting / n_seasons = 3 / 4; tpf_missing = []; 18 pixel files of KIC 6146666; unusable quarters [0, 1, 17] (hand-built [0, 17]); events used 34 (hand-built 35).

### Provenance

Search version 5+6cfe4cf4, effective ladder version 4, git 0b84077.

| Rung | Status | Ladder version | Written |
| --- | --- | --- | --- |
| rung1_detection | pass | 4 | 2026-10-21T04:06:15.455019 |
| rung1_events | pass | 4 | 2026-10-21T04:06:15.455019 |
| rung2_shape | pass | 4 | 2026-10-21T04:06:15.455019 |
| rung3_stability | pass | 4 | 2026-10-21T04:06:15.455019 |
| rung4_novelty | pass | 3 | 2026-10-19T21:08:04.353497 |
| rung5_pixels | pass | 4 | 2026-10-21T04:07:27.287189 |

### Probabilities

False alarm: "1 in 170", 11 of 1998 null searches at or above SDE 50.5 (by null kind: inverted 11 of 999, scrambled 0 of 999), calibration version 5+42061727 (signal search 5+6cfe4cf4, version matches True).

**KIC 6146676**: FPP 0.007754 ("under 1 percent", label "False positive", pixels True, signed off False), confusion radius 2 arcsec. Centroid error sigma_c 0.1373 arcsec, the statistical one of statistical 0.1373 and scatter 0.1103 arcsec. Spec 4.2 as first written (one EB at 2P weighted by S_sec x S_oe): EB weight 2.856e-07, FPP 0.000139.

| Scenario | Prior | Geometric | Data | Weight | Share of the total |
| --- | --- | --- | --- | --- | --- |
| PL | 0.1604 | 0.01284 | 0.9976 | 0.002054 | 0.9922 |
| BEB | 0.084 | 0.02022 | 2.234e-222 | 3.795e-225 | 1.833e-222 |
| EB-P | 0.005371 | 0.01682 | 0.1683 | 1.52e-05 | 0.007344 |
| EB-2P | 0.005371 | 0.0106 | 0.01491 | 8.487e-07 | 0.00041 |

| Scenario | Factor | Value |
| --- | --- | --- |
| PL | class | M |
| PL | rp_rearth | 2.63 |
| PL | f_pl | 0.1604 |
| PL | a_au | 0.1829 |
| PL | R_star_over_a | 0.01284 |
| PL | T14_circ | 0.1647 |
| PL | T14_fit | 0.161 |
| PL | sigma | 0.05196 |
| PL | z | -0.06989 |
| PL | D_shape | 0.9976 |
| BEB | f_EB | 0.014 |
| BEB | n_bright | 6 |
| BEB | a_2P_au | 0.46 |
| BEB | X_mean | 2.234e-222 |
| BEB | r_c_arcsec | 2 |
| BEB | centroid_sigma_arcsec | 0.1373 |
| EB-P | class | M |
| EB-P | f_bin | 0.26 |
| EB-P | g_P | 0.04132 |
| EB-P | prior_share | 0.5 |
| EB-P | pair_mass_msun | 0.7545 |
| EB-P | fit_b | 0.8933 |
| EB-P | S_v | 1 |
| EB-P | a_P_au | 0.2094 |
| EB-P | secondary_predicted_ppm | 218.7 |
| EB-P | secondary_ppm | 95.43 |
| EB-P | secondary_err_ppm | 128.2 |
| EB-P | S_sec | 0.1683 |
| EB-2P | class | M |
| EB-2P | f_bin | 0.26 |
| EB-2P | g_P | 0.04132 |
| EB-2P | prior_share | 0.5 |
| EB-2P | pair_mass_msun | 0.7545 |
| EB-2P | fit_b | 0.8933 |
| EB-2P | S_v | 1 |
| EB-2P | a_2P_au | 0.3324 |
| EB-2P | odd_even_predicted_ppm | 546.7 |
| EB-2P | odd_even_ppm | 69.28 |
| EB-2P | odd_even_err_ppm | 219.7 |
| EB-2P | S_oe | 0.01491 |

| Gaia DR3 neighbour | Separation (arcsec) | G | Flux fraction | Bright enough | s from the centroid (arcsec) | Exclusion X_i |
| --- | --- | --- | --- | --- | --- | --- |
| 2073896294004902144 | 7.142 | 19.77 | 0.03711 | yes | 6.38 | 1.341e-221 |
| 2073896289692192128 | 10.94 | 17.9 | 0.1783 | yes | 10.18 | 0 |
| 2073896190925687296 | 11.45 | 12.81 | 0.9593 | yes | 10.69 | 0 |
| 2073896190926955520 | 12.97 | 19.74 | 0.03832 | yes | 12.2 | 0 |
| 2073896190921609600 | 13.51 | 19.38 | 0.05242 | yes | 12.75 | 0 |
| 2073896289692177024 | 18.62 | 18.88 | 0.08034 | yes | 17.86 | 0 |

**KIC 5689121**: FPP 0.01226 ("about 1 percent", label "False positive (no pixel data yet)", pixels False, signed off False), confusion radius 3.98 arcsec. Spec 4.2 as first written (one EB at 2P weighted by S_sec x S_oe): EB weight 1.646e-07, FPP 0.008954.

| Scenario | Prior | Geometric | Data | Weight | Share of the total |
| --- | --- | --- | --- | --- | --- |
| PL | 0.1409 | 0.02623 | 0.9286 | 0.003431 | 0.9877 |
| BEB | 0.07 | 0.04136 | 0.01065 | 3.084e-05 | 0.008877 |
| EB-P | 0.003981 | 0.03437 | 0.07825 | 1.071e-05 | 0.003083 |
| EB-2P | 0.003981 | 0.02165 | 0.0122 | 1.051e-06 | 0.0003027 |

| Scenario | Factor | Value |
| --- | --- | --- |
| PL | class | M |
| PL | rp_rearth | 2.54 |
| PL | f_pl | 0.1409 |
| PL | a_au | 0.08936 |
| PL | R_star_over_a | 0.02623 |
| PL | T14_circ | 0.115 |
| PL | T14_fit | 0.1011 |
| PL | sigma | 0.03595 |
| PL | z | -0.385 |
| PL | D_shape | 0.9286 |
| BEB | f_EB | 0.014 |
| BEB | n_bright | 5 |
| BEB | a_2P_au | 0.2249 |
| BEB | X_mean | 0.01065 |
| BEB | r_c_arcsec | 3.98 |
| BEB | centroid_sigma_arcsec | n/a |
| EB-P | class | M |
| EB-P | f_bin | 0.26 |
| EB-P | g_P | 0.03063 |
| EB-P | prior_share | 0.5 |
| EB-P | pair_mass_msun | 0.753 |
| EB-P | fit_b | 0.8611 |
| EB-P | S_v | 1 |
| EB-P | a_P_au | 0.1023 |
| EB-P | secondary_predicted_ppm | 203.1 |
| EB-P | secondary_ppm | 7.054 |
| EB-P | secondary_err_ppm | 138.4 |
| EB-P | S_sec | 0.07825 |
| EB-2P | class | M |
| EB-2P | f_bin | 0.26 |
| EB-2P | g_P | 0.03063 |
| EB-2P | prior_share | 0.5 |
| EB-2P | pair_mass_msun | 0.753 |
| EB-2P | fit_b | 0.8611 |
| EB-2P | S_v | 1 |
| EB-2P | a_2P_au | 0.1624 |
| EB-2P | odd_even_predicted_ppm | 507.8 |
| EB-2P | odd_even_ppm | 0.4658 |
| EB-2P | odd_even_err_ppm | 225.4 |
| EB-2P | S_oe | 0.0122 |

| Gaia DR3 neighbour | Separation (arcsec) | G | Flux fraction | Bright enough | s from the centroid (arcsec) | Exclusion X_i |
| --- | --- | --- | --- | --- | --- | --- |
| 2103792427466721792 | 7.128 | 15.48 | 0.6773 | yes | 7.128 | 0.05325 |
| 2103792393102276480 | 14.66 | 19.79 | 0.03807 | yes | 14.66 | 2.249e-15 |
| 2103792393108242176 | 16.34 | 19.13 | 0.06765 | yes | 16.34 | 2.264e-20 |
| 2103792427462012544 | 17.96 | 19.96 | 0.03261 | yes | 17.96 | 7.754e-26 |
| 2103792427462012032 | 18.98 | 20.58 | 0.01878 | yes | 18.98 | 1.23e-29 |

## Appendix: fix round 1 (21 October 2026)

The Task 7 review asked for five changes and a set of minors. All of them are in the commit that carries this note.

1. The centroid uses the voting seasons only. `dossier.centroid_estimate` takes the inverse-variance weighted mean of
   `dist_target_pix` over the seasons that voted (a legacy row without the `voting` flag votes when `nearer` names a
   star), and the FPP inputs come from it. For KIC 6146676 that is seasons 0, 1 and 2: offset 0.762 arcsec.
2. The centroid error is the larger of the error of the weighted mean (0.1373 arcsec) and the sample scatter
   (ddof 1) of the voting seasons about the mean over sqrt(n) (0.1103 arcsec; 0.0900 arcsec with ddof 0 before the
   re-review). `fpp.inputs` records `sigma_c_stat_arcsec`,
   `sigma_c_scatter_arcsec` and `sigma_c_source` ("statistical" here); `FppInputs` accepts the three keys with
   defaults and the FPP ignores them. r_c = max(3 sigma_c, 2.0) is unchanged, so r_c is 2.0 arcsec. Result: sigma_c
   0.1373 arcsec, X_mean 2.23e-222, BEB weight 3.8e-225, FPP 0.000139 (0.0139 percent, "under 1 percent"), with the
   EB term at 2.86e-7 (S_sec 0.168, S_oe 0.0149). The miss of the 1 to 60 percent band is an EB-term matter, and the
   sign-off item stands.
3. The provenance gives the ladder version of each rung from the test row's `recheck_id` (the rechecks row's version,
   else the signal's, else the run's), and the dossiers row stores the effective version ("4"). Rung 4's row is from
   the 19 October sweep under ladder 3.
4. `calibrate.FalseAlarm` gains `k_by_kind` and `n_by_kind`, and the dossier's FAP block carries both (inverted 11 of
   999, scrambled 0 of 999). The page text is unchanged, and the dashboard (`ui/`) does not show the split yet.
5. Gaia errors are loud: each is logged at warning level and printed by the CLI, a 4xx gets the "gaia_refused" caveat,
   and `skysift dossier` exits 3 when any Gaia query failed, while still writing the dossier (also on a later run that
   finds the dossier current).

Minors: the compare tool writes only to `--out` and otherwise prints the tables; a rung 3 season with no
generated value is listed as missing; rows with both values but no error are counted as compared without an error;
the headline uses the tool's tolerance wording; the two rung 2 differences have a sentence each; `git_commit` carries
a "-dirty" suffix when the tree has changes; a test pins `stars.Q_SOURCE` to the DR3 tables; "localized" is spelled
with a z; the 18 unpinned pixel files are listed above as a note for Vikram. Two additions from the dashboard task:
`tests/unit/ui_seed.py` writes the same FAP keys as `generate` and the sigma_c keys, and `dossier.pixels_section` takes
the season and its quarters from rung 5 (archive SEASON, so Q1 and Q5 are season 3) instead of the quarter number.

The golden block was regenerated from a clean worktree at f431ed0 (the same code as this commit, without the note and
the golden itself), so its `git_commit` reads f431ed0 with no "-dirty" suffix. f431ed0 is the pre-amend hash of the
same tree as 0ee90d6: the code is identical and only the note and the golden block differ, but f431ed0 itself is not
on the branch. The re-review round below regenerates the golden block from a committed tree, so that its `git_commit`
names a reachable commit. The fix-round run made 4 Gaia calls (rows 3168 to
3171, all 200) for KIC 6146676 only. The KIC 5689121 dossier was not regenerated: it has no pixel data, so its FPP
does not depend on the centroid, and its FAP split is 37 inverted and 1 scrambled of 38, with 999 searches of each kind.

Tests: `tests/unit/test_dossier.py`, `test_stars.py`, `test_fap.py`, `test_fpp.py` and `test_cli.py -k dossier` pass
(the `test_cli.py` selection is empty; the dossier CLI tests live in `test_dossier.py`). Ruff is clean.

## Appendix: re-review round (21 October 2026)

The re-review approved the fix round and asked for four minors, in two commits.

1. The scatter term of `centroid_estimate` is the sample scatter (ddof 1) over sqrt(n), and 0 for a single season.
   For KIC 6146676 it reads 0.1103 arcsec against the statistical 0.1373 arcsec, so the statistical error still wins:
   sigma_c 0.1373 arcsec, r_c 2.0 arcsec, X_mean 2.23e-222 and FPP 0.000139 (0.0139 percent) are unchanged. The
   golden test now checks that the stored `sigma_c_*` keys are the ones the code computes from the stored seasons.
2. The compare tool builds the provenance table from `provenance.ladder_versions`, so the golden block gives the six
   rows too, with the status and date where the block keeps the test row.
3. The golden block was regenerated from a clean worktree at the code commit 2588897, so its `git_commit` reads
   2588897, a commit on the branch. A first run from HEAD named the dashboard task's 46db518, which that task then
   amended away, so the block was made again from 2588897, which no later amend touches. Each run made 4 Gaia calls
   (rows 3172 to 3175 and 3176 to 3179, all 200). Against the fix-round golden only three fields changed:
   `generated`, `sigma_c_scatter_arcsec` and `git_commit`.
4. Without `--out` the compare tool prints the tables and writes nothing; with `--out` it writes the file and prints
   nothing.

## Appendix: final review round (21 October 2026)

The whole-branch review asked for four fixes and a set of minors, in one code commit (0b84077) and this note's commit.

1. `FppInputs` and the FPP result carry `neighbours_known`, False when the Gaia cone failed or the target has no G.
   The result then reads "not computed (no neighbour data)", its value is None on the dossiers row (so the tile sorts
   it last) and in the JSON (`value_without_beb` keeps the other terms for the factor table), a caveat says why, and
   the pack's probability sentence and the Research Note abstract say it is not computed.
2. The FAP split by null kind is shown under the pooled number on the page, in the verdict popup and in the standalone
   export.
3. The on-target binary is two sub-scenarios, EB-P and EB-2P (finding 8). The result keeps the spec's product and its
   FPP (`spec_product`) beside the new sum, the factor tables list both sub-scenarios with their weights, spec 4.2
   records the change in one line, and `SIGNED_OFF` stays False.
4. `skysift pack --zenodo-live` and a live Zenodo client in `build_pack` are refused while `SIGNED_OFF` is False; the
   sandbox stays allowed.

Minors: the dashboard sets the nice of a POSIX child after `Popen` (`os.setpriority`) instead of in `preexec_fn`; the
README says the zip is inside the pack folder and describes the ExoFOP file names as they are; the standalone export
cuts local paths as the pack does (`dossier.public_paths`, which now also finds a path inside a sentence, quoted or
bare), and the stored provenance names the calibration file relative to the data folder; the fingerprint includes the
number of rows in the source's null calibration, so a "not yet calibrated" dossier goes stale as nulls arrive; a
regeneration clears the pack link and the ticked steps, and a pack older than its dossier reads "the pack was built
from an earlier dossier"; one temperature scale (`planet_svg.STAR_SCALE`, edges 3,900, 5,200 and 6,000 K) gives the
thumbnail colour, its sentence and the dossier's `star_colour`, so the Sun reads yellow; the export download answers
409 with a toast while a dossier job for the target runs; and one test renders the dossier page in the dark theme in
both modes. The golden block was written twice: once from HEAD 04173f6 with the diff applied (its `git_commit`
"04173f6-dirty", so the code commit is green on its own), then from the clean tree at 0b84077, so its `git_commit`
names that commit. The only differences between the two are `generated` and `git_commit`. The runs made 12 Gaia calls
(rows 3180 to 3191, all 200).

