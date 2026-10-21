# Dossier comparison: KIC 6146676 generated against the hand-built study

21 October 2026. The dossier generator, the pixel recheck step and the pack builder ran for real on a copy of the
production database (`.superpowers/sample-run/engine.db`, copied with the sqlite backup API to
`.superpowers/dossier-run/engine.db`), with the blob store of the sample run and a snapshot of its null calibration
(999 rows of search version 5+42061727, 1,998 null searches). The generated numbers for the candidate KIC 6146676 are
set beside the hand-built study in `docs/candidates/kic-006146676/`. KIC 5689121 got a dossier only (no pixel step),
for its FPP factor table.

## Headline

Every quantity with a value on both sides agrees with the hand-built study within the study's quoted error: period,
SDE, depth, duration, odd and even depths, the secondary, Teff, stellar radius and mass, distance, Rp, a, a/R*, Teq,
the transit probability, the four rung 3 season depths, the eight per-season centroid distances (all within 0.1
pixel, the largest 0.057 pixel) and the six two-source depths (36 of 38 rows). The two rows without a generated value
are the two-source depths of archive season 3, which did not vote. The habitable-zone flags read as expected
(conservative False, optimistic True) and the insolation is 1.22 (-0.12 +0.14) Earth.

The real pixel verdict for KIC 6146676 is a pass: "the dip is on the target in 3/3 seasons (centroid >= 17.5 sigma
nearer)", with `n_seasons_voting` / `n_seasons` = 3 / 4 and `tpf_missing` empty (18 of 18 pixel files of KIC 6146666
read). The signal moved from stage 1 to stage 2 on the copy.

Four things did not hold on real data and are findings below: the Gaia star query failed with HTTP 400 until it was
fixed, archive season 3 does not vote, the FAP is "1 in 170" and not the "0 of 1,716" the spec quoted, and the pack
builder fails on the real rung 5 row.

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

## Network calls (the copy's `queries` table, rows 3137 to 3167)

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

Nothing else reached the network: no catalogue query (rung 4 was left out of the ladder recheck), no light curve
download (both raw files were in the blob store) and no Zenodo call (no token).

Files written to the shared blob store `.superpowers/sample-run/blobs` (recorded only in the copy, so the live database
does not know them): the 18 pixel files `kplr006146666-*_lpd-targ.fits.gz` (49.4 MB, kind `tpf`, unpinned) and
`7e/7e207f54...ca219e_pixels.npz` (8,904 bytes, kind `pixels`, pinned, the signal's rung 5 arrays).

## Findings

**1. The Gaia star query failed with HTTP 400 on real data.** `stars.Q_SOURCE` asked `gaiadr3.gaia_source` for
`radius_flame` and `mass_flame`, which live in `gaiadr3.astrophysical_parameters`. The unit tests answer every query
from a fake fetch, so the column error never surfaced. The first two dossiers therefore had no star (route "none"), no
planet radius and an FPP built on the 0.5 Rsun fallback (KIC 6146676 at 0.062 percent, KIC 5689121 at 3.1 percent).
The query now joins the two tables (`LEFT OUTER JOIN gaiadr3.astrophysical_parameters AS ap ON ap.source_id =
g.source_id`), and the routing keys of the fake fetch in `test_stars.py` and `test_dossier.py` follow it. The numbers
below are from the second run.

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
the Mann relations at Ks, while the study's baseline is the Mamajek colour route (0.498 -0.040 +0.036 Rsun). The study ran
the Mann route only as a check, and that check gave exactly the engine's values (0.505 +- 0.016 Rsun, 0.503 -0.012
+0.013 Msun). The planet errors shrink with it: Rp 2.63 +- 0.08 against 2.61 -0.21 +0.19 Earth radii, Teq 268 -7 +8
against 267 -12 +13 K. This is the route the spec chose (5.3), not a disagreement.

**6. The insolation is 1.22, not 1.24.** The 1.24 of Task 3 came from the test fixture (Ks 13.030, seed 1, R 0.509
Rsun). The archive gives Ks 13.055 and the dossier seed is 20261021, so R is 0.505 Rsun and the insolation 1.22 (-0.12
+0.14) Earth. The difference is a sixth of the error. The habitable zone edges move with it: conservative 0.2159 to
0.4097 AU, optimistic 0.1634 to 0.4261 AU, with the planet at 0.1829 AU, so conservative False and optimistic True.

**7. The FAP is "1 in 170", not "0 of 1,716".** 11 of the 1,998 null searches reached SDE 50.5, all of them inverted
light curves (148.1 at the top). None of the 999 scrambled searches came near it, so the scrambled set alone would read
"1 in 1,000". The calibration matched the signal's search: the sweep version 5+6cfe4cf4 maps to calibration version
5+42061727 and every row carries it (`version_matches` true). I think the inverted null is not a fair null for a dip
on a quiet M dwarf: inverting a light curve turns every brightening (flares, or the bumps of a pulsator) into dips, and
11 of the 999 calibration stars give a box-shaped peak above 50 that way. KIC 5689121 reads "1 in 51" (38 of 1,998; 37
inverted, 1 scrambled at 20.2). Which nulls count is a decision for the FAP rule, not something to tune here.

**8. Both FPPs fall below the 1 to 60 percent band of spec 4.2.** KIC 6146676 reads 0.0139 percent ("under 1
percent"), KIC 5689121 0.90 percent without pixel data ("under 1 percent", label "no pixel data yet"). For KIC 6146676
the background binary term vanishes (X_mean 2.5e-266): the season-averaged centroid error is 0.125 arcsec (the inverse
variance mean of the four seasons, 0.031 pixel), so the nearest bright neighbour at 6.36 arcsec from the centroid sits
35 sigma outside the 2.0 arcsec confusion radius. The spec expected about 4.6 sigma, which is the study's multi-quarter
offset error (0.9 arcsec), not the per-season Monte Carlo error. The on-target binary term is held down by the
secondary limit (S_sec 0.168) and the odd/even limit (S_oe 0.0149). This is the sign-off item already in the ledger,
and the factor tables are below. One more point for that review: `dossier.centroid_offset` averages all seasons with a
centroid, including season 3, which did not vote.

**9. The pack builder fails on the real rung 5 row.** `pack.pixel_sentence` takes the median of the per-season
two-source depths, and a season that did not vote stores `depth_target_ppm` as `[null, null]`, so `np.median` raises
`TypeError: '<' not supported between instances of 'NoneType' and 'float'`. The crash leaves a half-written folder,
and the retry then stops with "is not empty and is not a submission pack; choose another --out". `pack.py` is under
the pack fix round, so it is reported here and not changed.

**10. "Season" means two numberings on one page.** Rung 3 groups quarters as quarter mod 4 (season 0 is Q4, Q8, Q12,
Q16), as the study's `lc_analysis.py` did. Rung 5 uses the archive SEASON (season 0 is Q2, Q6, Q10, Q14). Both agree
with their counterpart in the study, but a reader of one dossier sees two different "season 0". The dashboard seed in
progress also marks season 0 of KIC 6146676 as the one that does not vote, while on real data it is season 3.

**11. Smaller points.** The ladder recheck wrote rows at ladder version 4 but left the signal row's `ladder_version`
null, so the dossier's provenance prints a null ladder version. `lc.pixels._epoch_dt` casts NaN times to int64 (16
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
- The golden block lives at `tests/golden/kic-006146676-generated.json` (17.3 kB), as the controller asked, and not
  under `docs/superpowers/notes/`. `python -m tools.dossier_compare --write-golden` writes it from the dossier JSON.

## Reproduce

```bash
python -m tools.dossier_compare --data .superpowers/dossier-run/dossiers/kic-006146676/dossier_data.json \
    --reference docs/candidates/kic-006146676/dossier_data.json \
    --fpp .superpowers/dossier-run/dossiers/kic-005689121/dossier_data.json \
    --out .superpowers/dossier-run/comparison.md --write-golden tests/golden/kic-006146676-generated.json
```

The same tables come out of the committed golden block (`--data tests/golden/kic-006146676-generated.json`). The
tool's output follows.

### Comparison: KIC 6146676 generated against the hand-built study

36 of 38 rows agree; disagreements: none; without a value on one side: two-source depth on KIC 6146676, season 3, two-source depth on KIC 6146666, season 3.

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

### Probabilities

False alarm: "1 in 170", 11 of 1998 null searches at or above SDE 50.5, calibration version 5+42061727 (signal search 5+6cfe4cf4, version matches True).

**KIC 6146676**: FPP 0.000139 ("under 1 percent", label "False positive", pixels True, signed off False), confusion radius 2 arcsec.

| Scenario | Prior | Geometric | Data | Weight | Share of the total |
| --- | --- | --- | --- | --- | --- |
| PL | 0.1604 | 0.01284 | 0.9976 | 0.002054 | 0.9999 |
| BEB | 0.084 | 0.02022 | 2.52e-266 | 4.279e-269 | 2.083e-266 |
| EB | 0.01074 | 0.0106 | 0.002509 | 2.856e-07 | 0.000139 |

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
| BEB | X_mean | 2.52e-266 |
| BEB | r_c_arcsec | 2 |
| BEB | centroid_sigma_arcsec | 0.1249 |
| EB | class | M |
| EB | f_bin | 0.26 |
| EB | g_P | 0.04132 |
| EB | a_2P_au | 0.3324 |
| EB | pair_mass_msun | 0.7545 |
| EB | secondary_predicted_ppm | 218.7 |
| EB | secondary_ppm | 95.43 |
| EB | secondary_err_ppm | 128.2 |
| EB | S_sec | 0.1683 |
| EB | odd_even_predicted_ppm | 546.7 |
| EB | odd_even_ppm | 69.28 |
| EB | odd_even_err_ppm | 219.7 |
| EB | S_oe | 0.01491 |
| EB | fit_b | 0.8933 |
| EB | S_v | 1 |

| Gaia DR3 neighbour | Separation (arcsec) | G | Flux fraction | Bright enough | s from the centroid (arcsec) | Exclusion X_i |
| --- | --- | --- | --- | --- | --- | --- |
| 2073896294004902144 | 7.142 | 19.77 | 0.03711 | yes | 6.362 | 1.512e-265 |
| 2073896289692192128 | 10.94 | 17.9 | 0.1783 | yes | 10.16 | 0 |
| 2073896190925687296 | 11.45 | 12.81 | 0.9593 | yes | 10.67 | 0 |
| 2073896190926955520 | 12.97 | 19.74 | 0.03832 | yes | 12.19 | 0 |
| 2073896190921609600 | 13.51 | 19.38 | 0.05242 | yes | 12.73 | 0 |
| 2073896289692177024 | 18.62 | 18.88 | 0.08034 | yes | 17.84 | 0 |

**KIC 5689121**: FPP 0.008954 ("under 1 percent", label "False positive (no pixel data yet)", pixels False, signed off False), confusion radius 3.98 arcsec.

| Scenario | Prior | Geometric | Data | Weight | Share of the total |
| --- | --- | --- | --- | --- | --- |
| PL | 0.1409 | 0.02623 | 0.9286 | 0.003431 | 0.991 |
| BEB | 0.07 | 0.04136 | 0.01065 | 3.084e-05 | 0.008907 |
| EB | 0.007963 | 0.02165 | 0.0009544 | 1.646e-07 | 4.753e-05 |

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
| EB | class | M |
| EB | f_bin | 0.26 |
| EB | g_P | 0.03063 |
| EB | a_2P_au | 0.1624 |
| EB | pair_mass_msun | 0.753 |
| EB | secondary_predicted_ppm | 203.1 |
| EB | secondary_ppm | 7.054 |
| EB | secondary_err_ppm | 138.4 |
| EB | S_sec | 0.07825 |
| EB | odd_even_predicted_ppm | 507.8 |
| EB | odd_even_ppm | 0.4658 |
| EB | odd_even_err_ppm | 225.4 |
| EB | S_oe | 0.0122 |
| EB | fit_b | 0.8611 |
| EB | S_v | 1 |

| Gaia DR3 neighbour | Separation (arcsec) | G | Flux fraction | Bright enough | s from the centroid (arcsec) | Exclusion X_i |
| --- | --- | --- | --- | --- | --- | --- |
| 2103792427466721792 | 7.128 | 15.48 | 0.6773 | yes | 7.128 | 0.05325 |
| 2103792393102276480 | 14.66 | 19.79 | 0.03807 | yes | 14.66 | 2.249e-15 |
| 2103792393108242176 | 16.34 | 19.13 | 0.06765 | yes | 16.34 | 2.264e-20 |
| 2103792427462012544 | 17.96 | 19.96 | 0.03261 | yes | 17.96 | 7.754e-26 |
| 2103792427462012032 | 18.98 | 20.58 | 0.01878 | yes | 18.98 | 1.23e-29 |
