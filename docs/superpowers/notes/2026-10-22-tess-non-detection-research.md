# Would TESS have seen this Kepler candidate? Research for the non-detection cross-check

Date: 22 October 2026. This is a research note, not a design. It gathers the sources, formulas and numbers an engineer needs to state, for each Kepler candidate, whether TESS should have detected the same transit: a predicted per-transit and folded significance in TESS, built from the star's TESS magnitude, the Kepler depth diluted by neighbours in a TESS aperture, a published TESS noise model, and the number of transits that fall in the sectors that observed the field. Every number carries a link (reference-style, listed in section 9). Where a number is my own arithmetic on a cited input, the text says "computed here" and names the inputs. Where I could not confirm something, it is listed in section 8.

Three targets are used as test cases. Target A is at RA 297.87, Dec +41.46. Target B is at RA 285.91, Dec +40.94. Target C is KIC 6699887, which the MAST TIC gives at RA 294.1456, Dec +42.1880, Tmag 15.5468, TIC 270864056 ([MAST TIC query][mast-api], run on 22 October 2026). The field centre (RA 290, Dec +41) is a fourth case.

## 1. TESS sectors that covered the Kepler field

### 1.1 Which sectors, by cycle

The TESS Science Support Center page lists every sector with its pointing and cycle boundaries ([sector table][heasarc-sector]); the MIT page lists the same sectors by year ([MIT observations][mit-obs]). Cycle 1 is Sectors 1 to 13, Cycle 2 is 14 to 26, Cycle 3 starts at Sector 27, Cycle 4 at 40, Cycle 5 at 56, Cycle 6 at 70, Cycle 7 at 84, Cycle 8 at 97, Cycle 9 at 108 and Cycle 10 at 122 ([sector table][heasarc-sector]). As of 22 October 2026 TESS is in Orbit 229, Sector 109, and Sector 110 begins on 31 October 2026 ([MIT observations][mit-obs]). The most recent completed cycle is therefore Cycle 8 (Sectors 97 to 107, ending 7 September 2026 per the [sector table][heasarc-sector]). Cycle 8 pointed at the southern hemisphere and does not cover the Kepler field. Cycle 9 is in progress and Cycle 10 pointings were published on 26 August 2026 ([TSSC news][cycle10]).

I ran tess-point (version 0.9.5.post1, pointing table for Sectors 1 to 134, [tess-point README][tp-gh]) for the four positions and kept only sectors that have been observed by 22 October 2026. I also queried the TESScut sector endpoint for the same positions ([TESScut docs][tesscut]). The two methods agree exactly for A, B and C.

| Position | Observed sectors (tess-point, and TESScut) | Count | Total FFI days (computed here from [FFI times][mit-ffi]) |
|---|---|---|---|
| A (297.87, +41.46) | 14, 15, 41, 54, 55, 74, 75, 81, 82 | 9 | 232 |
| B (285.91, +40.94) | 14, 26, 40, 41, 53, 54, 74, 75, 80, 81 | 10 | 256 |
| C = KIC 6699887 (294.1456, +42.1880) | 14, 15, 40, 41, 54, 55, 74, 75, 81, 82 | 10 | 259 |
| Field centre (290.0, +41.0) | 14, 40, 41, 54, 55, 74, 75, 80, 82 | 9 | 234 |

By cycle, the field was observed in Cycle 2 (Sectors 14, 15, 26), Cycle 4 (40, 41, 53, 54, 55) and Cycle 6 (74, 75, 80, 81, 82). Cycles 1, 3, 5, 7 and 8 do not cover the field for these positions. Future coverage that tess-point predicts and the [sector table][heasarc-sector] dates are: Sectors 119 and 120 in Cycle 9 (3 July to 23 August 2027), then Sectors 125, 126, 127 and 132 in Cycle 10 (2027 December to 2028 July). These are provisional and must not be counted as observed. The script must therefore cut tess-point output at the last completed sector, and today that cut is Sector 108 (Sector 109 is in progress).

### 1.2 Dates, windows and cadence for the observed sectors

Dates below are the actual FFI data accumulation windows from the MIT FFI times file ([FFI times][mit-ffi]), with the ground-contact orbit file used for Sector 26 orbit 2 ([orbit times][mit-orbit]). The BTJD column is computed here by converting UTC to Julian date and subtracting 2457000, using the BTJD definition of [BJD minus 2457000][btjd]. It has no barycentric correction (up to about 0.006 d) and no UTC to TDB offset (about 0.0008 d), which is fine for deciding whether a transit falls in a window but not for timing.

| Sector | Cycle | Start to end (UTC) | BTJD start to end | FFI cadence |
|---|---|---|---|---|
| 14 | 2 | 2019-07-18 20:29 to 2019-08-14 16:29 | 1683.354 to 1710.187 | 30 min |
| 15 | 2 | 2019-08-15 20:59 to 2019-09-10 21:29 | 1711.375 to 1737.395 | 30 min |
| 26 | 2 | 2020-06-09 18:29 to 2020-07-04 15:25 | 2010.270 to 2035.142 | 30 min |
| 40 | 4 | 2021-06-25 03:39 to 2021-07-23 08:29 | 2390.652 to 2418.853 | 10 min |
| 41 | 4 | 2021-07-24 11:39 to 2021-08-20 01:49 | 2419.985 to 2446.576 | 10 min |
| 53 | 4 | 2022-06-13 11:48 to 2022-07-08 11:28 | 2743.992 to 2768.978 | 10 min |
| 54 | 4 | 2022-07-09 09:28 to 2022-08-04 14:58 | 2769.895 to 2796.124 | 10 min |
| 55 | 4 | 2022-08-05 14:18 to 2022-09-01 18:18 | 2797.096 to 2824.263 | 10 min |
| 74 | 6 | 2024-01-03 08:35 to 2024-01-30 01:38 | 3312.858 to 3339.568 | 200 s |
| 75 | 6 | 2024-01-30 06:41 to 2024-02-26 23:31 | 3339.779 to 3367.480 | 200 s |
| 80 | 6 | 2024-06-18 09:09 to 2024-07-14 20:02 | 3479.882 to 3506.335 | 200 s |
| 81 | 6 | 2024-07-15 01:06 to 2024-08-10 16:12 | 3506.546 to 3533.176 | 200 s |
| 82 | 6 | 2024-08-10 21:16 to 2024-09-05 17:52 | 3533.386 to 3559.245 | 200 s |

Nominal sector start and stop dates from the [sector table][heasarc-sector] agree to within a day (for example Sector 14 is 18 July to 14 August 2019, Sector 80 is 18 June to 15 July 2024).

The data gaps matter for counting transits. Each orbit has a gap near perigee (and from Sector 56 a second one at apogee, which splits each orbit into two segments) ([MIT observations][mit-obs]). The observing windows in BTJD, one segment per row of the MIT FFI file, are:

```
S14: 1683.354-1696.375; 1697.354-1710.187
S15: 1711.375-1723.854; 1724.937-1737.395
S26: 2010.270-2022.187; 2023.124-2035.142
S40: 2390.652-2404.395; 2405.326-2418.853
S41: 2419.985-2432.597; 2433.715-2446.576
S53: 2743.992-2755.992; 2756.909-2768.978
S54: 2769.895-2782.332; 2783.527-2796.124
S55: 2797.096-2809.957; 2810.867-2824.263
S74: 3312.858-3319.256; 3319.467-3325.846; 3326.057-3332.946; 3333.115-3339.568
S75: 3339.779-3346.175; 3346.385-3353.554; 3353.765-3360.119; 3360.330-3367.480
S80: 3479.882-3486.453; 3486.664-3493.342; 3493.553-3499.995; 3500.201-3506.335
S81: 3506.546-3513.384; 3513.595-3519.213; 3519.782-3526.106; 3526.317-3533.176
S82: 3533.386-3539.259; 3539.470-3545.460; 3545.671-3552.162; 3552.372-3559.245
```

The engineer should read these at run time from the [FFI times file][mit-ffi] rather than hard-code them, because the file is updated each sector. Two defects in the file as fetched on 22 October 2026: Sector 26 orbit 60 has a placeholder end time of 1971-12-31 (use the orbit file's 2020-07-04 15:25 instead, [orbit times][mit-orbit]), and the rows for orbit 229 are labelled Sector 111 where the orbit file says Sector 109.

### 1.3 Cadence modes

The FFI cadence is 30 minutes in Cycles 1 and 2, 10 minutes in Cycles 3 and 4, and 200 seconds from Cycle 5 ([TESS data products][heasarc-dp]). The 20-second target-pixel mode was added in the first extended mission and the 2-minute mode ran throughout ([TESS data products][heasarc-dp]; [Guide to TESS for extended-mission planning][em-guide]). The 20-second and 2-minute modes cover only pre-selected targets, about 20,000 per sector at 2 minutes and about 1,500 at 20 seconds in the first extended mission, then about 13,000 at 2 minutes and 2,500 at 20 seconds in the second ([Guide, Table 1][em-guide]). Only 962 Kepler stars received 2-minute data in the primary mission ([Battley et al. 2021][battley]). A faint Kepler candidate (Tmag 14 to 16) will therefore almost always be an FFI-only star, with 30-minute cadence in Sectors 14, 15, 26, 10-minute in Sectors 40, 41, 53 to 55, and 200 s in Sectors 74 to 82. The noise model in section 2 should be evaluated at the 1-hour timescale and scaled, so the cadence matters only for the transit-shape resolution, not for the predicted significance.

### 1.4 Computing coverage for any RA and Dec offline with tess-point

tess-point (Burke et al. 2020, [ascl:2003.001][ascl-tp]) is a pip package: `pip install tess-point`, or download the single file `tess_stars2px.py` ([tess-point README][tp-gh]). The wheel metadata requires numpy, astropy and scipy ([PyPI wheel metadata, version 0.9.5.post1][pypi-tp]). Lookups by RA and Dec work offline; lookups by TIC ID need MAST and lookups by name need SESAME ([tess-point README][tp-gh]). The pointing table is embedded in the file and, in version 0.9.5.post1, runs to Sector 134, so it includes sectors that have not yet been observed. The author calls the table unofficial and estimates about 2 pixels of accuracy without the aberration option ([tess-point README][tp-gh]). In my runs the sector lists matched TESScut exactly, so this accuracy is not a concern for sector membership, only for targets within a couple of pixels of a CCD edge.

Python call, as run here (positional arguments are star ID, RA, Dec; the result tuple holds ID, ecliptic longitude, ecliptic latitude, sector, camera, CCD, column, row):

```python
from tess_stars2px import tess_stars2px_function_entry, TESS_Spacecraft_Pointing_Data
sc = TESS_Spacecraft_Pointing_Data()            # builds the pointing table once; reuse for all stars
res = tess_stars2px_function_entry(1, ra_deg, dec_deg, scInfo=sc)
sectors = [int(s) for s in res[3]]               # keep only s <= LAST_COMPLETED_SECTOR
```

A `sectorOverrideFile` argument restricts the search to a user-supplied list of sectors, which speeds up bulk runs ([tess-point README][tp-gh]; the argument is in the source of `tess_stars2px.py`). The networked equivalent is the TESScut sector listing, `GET https://mast.stsci.edu/tesscut/api/v0.1/sector?ra=RA&dec=DEC` ([TESScut docs][tesscut]), which returned the same sectors, cameras and CCDs for A, B and C.

## 2. TESS photometric noise model

### 2.1 The Sullivan et al. 2015 model, in terms the engineer can code

[Sullivan et al. 2015][sullivan] (ApJ 809, 77) assume photon noise from the star and the zodiacal light, a read noise of 10 electrons per pixel per 2-second exposure, and a systematic floor of 60 ppm per square root hour, all added in quadrature ([Sullivan Sections 2 and 6.4][sullivan]). The inputs I verified in the paper:

- Photon flux at T = 0 is 1.514 x 10^6 photons per second per cm^2 ([Sullivan Table 1, note b][sullivan]).
- Effective collecting area is 69 cm^2 and the pixel scale is 21.1 arcsec ([Sullivan Section 2][sullivan]).
- Zodiacal photon flux is 2.56 x 10^-3 x 10^(-0.4 (V - 22.8)) photons per second per cm^2 per arcsec^2, with V the zodiacal surface brightness in mag per arcsec^2 ([Sullivan Eq. 21][sullivan]).
- V = Vmax - dV x ((|b| - 90) / 90)^2 with Vmax = 23.345 and dV = 1.148, b the ecliptic latitude ([Sullivan Eq. 22 and fit values][sullivan]). I use the absolute value of b by symmetry. Sullivan quotes 95 to 270 electrons per pixel per 2-second image across latitude ([Sullivan Section 6.4.1][sullivan]), which my implementation reproduces at both ends.
- Read noise 10 electrons per pixel per 2 s exposure and the 60 ppm per square root hour floor ([Sullivan Section 6.4.2][sullivan]). The floor is assumed uncorrelated and scaling as t^-1/2 ([Sullivan Section 6.4.2][sullivan]).
- A 3 per cent loss in signal-to-noise for cosmic-ray mitigation ([Sullivan Section 6.4.4][sullivan]).
- Detection threshold in that paper: signal-to-noise of 7.3 ([Sullivan Section 2.3][sullivan]).
- The optimal aperture is found per star; Figure 14 of the paper shows its pixel count against magnitude but gives no table ([Sullivan Figure 14][sullivan]).

My implementation, checked against the TIC polynomial below (it is mine, not the paper's code; `n_pix` and `flux_frac` are inputs the paper does not tabulate):

```python
import math
def sigma_1h_sullivan_ppm(T, ecl_lat_deg, n_pix=4, flux_frac=0.7, sys_ppm=60.0, cr_loss=0.03):
    A_EFF, PIX, F0 = 69.0, 21.1, 1.514e6                 # cm^2, arcsec, ph/s/cm^2 at T=0
    star = F0 * A_EFF * 10 ** (-0.4 * T) * flux_frac      # e-/s in the aperture
    V = 23.345 - 1.148 * ((abs(ecl_lat_deg) - 90.0) / 90.0) ** 2
    sky = 2.56e-3 * 10 ** (-0.4 * (V - 22.8)) * A_EFF * PIX ** 2   # e-/s/pixel
    t = 3600.0
    var = star * t + n_pix * sky * t + n_pix * (t / 2.0) * 10.0 ** 2
    return math.hypot((math.sqrt(var) / (star * t) * 1e6) * (1 + cr_loss), sys_ppm)
```

The Kepler field is at ecliptic latitude about +62 degrees (tess-point gives 60.6 degrees for A, 62.9 for B and 62.1 for the centre, as run here; the TIC gives 62.3 for C), where the zodiacal term is about 53 electrons per pixel per second (computed here from Eq. 21 and 22 above).

### 2.2 The TIC and ticgen form

The TIC paper gives the 1-hour precision used for the Candidate Target List priority as a fifth-order polynomial in Tmag: ln(sigma_ppm) = lnA + B T + C T^2 + D T^3 + E T^4 + F T^5, with F = +4.73508403525e-5, E = -0.0022308015894, D = +0.0395908321369, C = -0.285041632435, B = +0.850021465753 and lnA = +3.29685004771 ([Stassun et al. 2018, arXiv v1, Section 3.3, page 24][stassun18-v1]). The paper says the form was adapted from a private communication and is based on Figure 8 of [Ricker et al. 2015][ricker] for I_C magnitudes. The 2019 revision says its priority uses the Sullivan methodology ([Stassun et al. 2019][stassun19]) and the CTL release notes call sigma "the expected photometric precision of the star based on the TESS magnitude calculated using the methodology of Sullivan et al. (2015)" ([Stassun et al. 2018, Appendix A, notes on column 88][stassun18]). The ticgen package implements the same polynomial and scales it by 1/sqrt(integration time / 60 min) ([ticgen source][ticgen-src]; [ticgen README][ticgen-readme]). I could not find this polynomial in the final arXiv version 6 text of Stassun et al. 2018; it is in version 1 and in the ticgen source. Cite both.

```python
def sigma_1h_ticgen_ppm(T):
    F=4.73508403525e-5; E=-0.0022308015894; D=0.0395908321369
    C=-0.285041632435;  B=0.850021465753;   lnA=3.29685004771
    return math.exp(lnA + B*T + C*T**2 + D*T**3 + E*T**4 + F*T**5)
```

### 2.3 Values at the magnitudes that matter, with an empirical check

All values are 1-hour noise in ppm. The first two columns are computed here from the formulas above. The empirical column is computed here from the SPOC robust RMS CDPP tables for the 13 Kepler-field sectors of section 1.2 (222,498 two-minute light curves, narrow bins of plus or minus 0.25 mag; the files are listed on the [MAST CDPP bulk-download page][mast-cdpp] and described in [Twicken et al. 2025][twicken]).

| Tmag | ticgen polynomial | Sullivan model (4 pixels, 62 deg latitude) | SPOC 2-min CDPP at 1 h, median (16th to 84th percentile) | Empirical divided by ticgen |
|---|---|---|---|---|
| 12 | 595 | 590 | 790 (654 to 1081) | 1.33 |
| 13 | 1122 | 1101 | 1625 (1306 to 2254) | 1.45 |
| 14 | 2285 | 2280 | 3810 (2865 to 5731) | 1.67 |
| 15 | 5190 | 5165 | 8804 (6550 to 13866) | 1.70 |
| 15.5 | 8293 | not run | 14449 (10123 to 26934) | 1.74 |
| 16 | 13957 | 12370 | 24151 (16179 to 43653) | 1.73 |
| 15.5468 (KIC 6699887) | 8687 | 8284 | about 15,000 (interpolated) | about 1.7 |

The key numbers: **at Tmag 15 the 1-hour noise is 5190 ppm from the TIC polynomial and 8804 ppm empirically, and at Tmag 16 it is 13957 ppm from the polynomial and 24151 ppm empirically.** The Sullivan model with four pixels, a 0.7 flux fraction and 62 degrees ecliptic latitude reproduces the polynomial to within 2 per cent at Tmag 12 to 15 and 11 per cent at Tmag 16, which supports using either as the nominal model. The extra factor of 1.3 to 1.7 in the SPOC tables is real: those light curves include stellar variability, crowding, background errors and residual systematics, and the faint 2-minute targets are Guest Investigator choices that may be unrepresentative. The robust-RMS CDPP tables are the SPOC's own statement of achieved precision ([Twicken et al. 2025][twicken]). My recommendation is to carry both: the polynomial as the nominal (optimistic) noise and the empirical column as the conservative noise, and to call a non-detection secure only if the folded significance is low under the nominal noise, and a missed detection only if it is high under the conservative noise.

### 2.4 Scaling from 1 hour to a transit duration

The white-noise rule is sigma(T_dur) = sigma_1h / sqrt(T_dur / 1 h), the rule used by ticgen ([ticgen README][ticgen-readme]) and by Sullivan for the floor ([Sullivan Section 6.4.2][sullivan]). The SPOC's own CDPP says real light curves fall more slowly. Computed here from the same CDPP tables, the per-star median ratio of CDPP at duration tau to CDPP at 1 hour for Tmag 15 to 16 is 1.366 at 0.5 h, 0.752 at 2 h, 0.647 at 3 h, 0.544 at 5 h and 0.514 at 6 h. The white-noise values are 1.414, 0.707, 0.577, 0.447 and 0.408. Using white noise therefore overstates the significance by about 12 per cent at 3 h and about 25 per cent at 6 h. The empirical ratios for the Tmag 10 to 12 bin differ from these by up to 7 per cent (for example 0.550 against 0.514 at 6 h). I recommend the empirical ratios (interpolated in log duration) for the conservative case.

The SPOC states the signal-to-noise formula it recommends for these tables: S = (depth / CDPP(tau)) x sqrt(N) for N independent transits of duration tau, and for transits spread over sectors S = depth x sqrt(sum_i N_i / CDPP_i(tau)^2) ([Twicken et al. 2025, Eqs. 2 and 3][twicken]). [Giacalone et al. 2021][giacalone] use the same form, SNR = depth_obs / CDPP x sqrt(n_transits) (their Eq. 17, page 18).

### 2.5 Does the TIC or MAST expose a per-star noise estimate?

- The TIC itself does not. The 125 columns returned by the MAST TIC service for KIC 6699887 contain Tmag, GAIAmag, contratio, numcont and priority but no noise column (checked here via the [MAST API][mast-api]).
- The Candidate Target List on the Filtergraph portal carries noise-term columns: "Noise Star", "Noise Sky", "Noise Contaminates", "Noise Readout" and "Noise Systematics" ([Stassun et al. 2018, Appendix F, Table 9][stassun18]). That covers only CTL stars, which are bright dwarfs, so it will not include most faint Kepler candidates.
- The SPOC publishes achieved 1-hour and other-duration robust RMS CDPP for every 2-minute SPOC light curve as CSV files at MAST (the paper covers Sectors 1 to 90; the MAST page listed files through Sector 107 on 22 October 2026), and as the header keywords CDPP0_5, CDPP1_0 and CDPP2_0 in each SPOC light curve file ([Twicken et al. 2025][twicken]; [MAST CDPP page][mast-cdpp]). This is the best per-star source for a Kepler star that happened to be a 2-minute target (962 in the primary mission, [Battley et al. 2021][battley]). For FFI-only faint stars there is no per-star noise product I could find.

## 3. Dilution

### 3.1 Pixel scale, PSF and aperture sizes

- Pixel scale is 21 arcsec ([Ricker et al. 2015, Table 1][ricker]: one pixel is 0.35 arcmin). Sullivan quotes 21.1 arcsec ([Sullivan Section 2][sullivan]); the TIC contamination calculation used 20.25 arcsec ([Stassun et al. 2018 v1, Section 3.2.3][stassun18-v1]).
- The ensquared energy is 50 per cent within one pixel and 90 per cent within 4 x 4 pixels (1.4 arcmin) ([Ricker et al. 2015, Table 1][ricker]).
- A circular 2D Gaussian PSF fitted to the TESS PRF models has a standard deviation of 0.6 to 0.9 pixel, and [Giacalone et al. 2021][giacalone] adopt 0.75 pixel for all stars (Section 2.1, page 6).
- Optimal aperture sizes for 2-minute SPOC photometry in Year 1: about 20 pixels at Tmag 7, 11 at Tmag 10, and 6 at Tmag 13 ([Guerrero et al. 2021][guerrero]). I found no published figure for Tmag above 13. SPOC chooses the aperture by maximising signal-to-noise with the TIC and the PRF ([Guerrero et al. 2021][guerrero]), so for faint, crowded stars it can be smaller than 6 pixels; this is an inference, not a cited number. Treat the aperture size as an adjustable assumption of 4 to 6 pixels for Tmag 14 to 16.
- The TIC's own contamination calculation uses a deliberately small, magnitude-dependent aperture: Npix = c3 T^3 + c2 T^2 + c1 T + c0 with c3 = -0.2592, c2 = 7.7410, c1 = -77.7918, c0 = 274.2898, floored at one pixel, searching for contaminants out to 10 pixels, with a circular 2D Gaussian PSF ([Stassun et al. 2018 v1, Section 3.2.3][stassun18-v1]; [Stassun et al. 2019, Section 3.2.1][stassun19]). Computed here, that gives 10.3 pixels at Tmag 11, 7.6 at 12, 1.8 at 13 and zero or below (so one pixel) from about Tmag 13.2. This is much smaller than the SPOC's 6 pixels at Tmag 13, so the TIC contamination ratio of a faint star is a lower bound.

### 3.2 A rule for the radius, and whether "about 42 arcsec" is justified

I computed, for a circular aperture centred on the target and a Gaussian PSF with sigma 0.75 pixel (the [Giacalone value][giacalone]), the fraction of a neighbour's flux that falls inside the aperture:

| Neighbour distance | 2 pixels aperture | 4 pixels | 6 pixels | 9 pixels |
|---|---|---|---|---|
| 0 | 0.432 | 0.678 | 0.817 | 0.922 |
| 21 arcsec (1 pixel) | 0.222 | 0.411 | 0.565 | 0.732 |
| 32 arcsec (1.5 pixels) | 0.094 | 0.211 | 0.333 | 0.505 |
| 42 arcsec (2 pixels) | 0.028 | 0.078 | 0.145 | 0.268 |
| 63 arcsec (3 pixels) | 0.001 | 0.003 | 0.010 | 0.028 |
| 84 arcsec (4 pixels) | 0.000 | 0.000 | 0.000 | 0.001 |

The distance 0 row is the target's own captured fraction. These numbers are computed here; the aperture radius is r = sqrt(Npix / pi) pixels, and the same values come from the non-central chi-square distribution, fraction = ncx2.cdf((r / sigma)^2, df = 2, nc = (d / sigma)^2) in scipy, which I checked against my integration.

"About 42 arcsec" (2 pixels) is defensible as a simple inclusion radius. It is where 90 per cent of a star's light is already inside the 4 x 4 pixel box ([Ricker et al. 2015, Table 1][ricker]), and it is aperture radius (24 to 29 arcsec for 4 to 6 pixels) plus about one PSF sigma (16 arcsec). But a hard cut with weight one is wrong in both directions: it counts too much of a neighbour at 42 arcsec (3 to 27 per cent of its flux enters, per the table) and misses nothing important only because the flux is already small beyond it. My recommendation is:

1. Query neighbours out to 5 pixels (105 arcsec). The table shows the contribution is below 0.1 per cent beyond 84 arcsec for apertures up to 9 pixels. ([Giacalone et al.][giacalone] consider all stars within 10 pixels, and the TIC searches 10 pixels, [Stassun et al. 2018 v1][stassun18-v1]; 5 pixels is enough for the aperture fractions above, but increase it if a very bright star lies just outside.)
2. Weight each neighbour by its Gaussian aperture fraction, not by one.
3. Report the result for 4 and 6 pixel apertures as a range, and also report the hard 42 arcsec result as an upper bound on dilution.

### 3.3 Flux fraction from the target and neighbours

Tmag from Gaia G and BP minus RP is the TIC's own relation: T = G - 0.00522555 (BP-RP)^3 + 0.0891337 (BP-RP)^2 - 0.633923 (BP-RP) + 0.0324473, valid for -0.2 < BP-RP < 3.5 and extrapolated to -1 to 6, scatter 0.006 mag ([Stassun et al. 2019, Eq. 1, Section 2.3.1][stassun19]). The TIC de-reddens G and the colour first, applies the relation, then adds the extinction back ([Stassun et al. 2019, Section 2.3.1][stassun19]). When BP-RP is missing the offset is T = G - 0.430 ([Stassun et al. 2019, Eq. 2][stassun19]). For KIC 6699887 the relation gives 15.552 from G = 16.2329, BP = 16.8599, RP = 15.4928 (computed here), against the TIC's 15.5468; the 0.005 mag difference is the reddening step. Simplest is to use the TIC `Tmag` as returned for every neighbour, and the Gaia relation only for a neighbour with no Tmag.

With flux in the Tmag band F_i proportional to 10^(-0.4 T_i):

```python
c = sum( (10**(-0.4*(T_i - T_target))) * frac_in_aperture(d_i)  for i in neighbours ) / frac_in_aperture(0)
depth_tess = depth_kepler / (1 + c)
T_total    = T_target - 2.5*math.log10(1 + c)      # brightness that sets the photon noise
```

Here c is the contamination ratio in the sense the TIC uses: contaminant flux in the aperture divided by the target's flux in the aperture ([Stassun et al. 2019, Section 3.2.1][stassun19]). The depth rule is how [Christ et al. 2019][christ] diluted Kepler depths ("contaminate the transit depths" with the TIC ratio). The noise should be evaluated at T_total, not at the target's Tmag. Reason: the signal is depth x F_target, the noise in a background-dominated aperture is set by the total flux, so SNR = depth / ((1 + c) x sigma(T_total)). I derived this and it is not in a cited paper; it matches Christ et al. only if their noise used the target Tmag alone, which would overstate the significance for blended stars. This also assumes the Kepler depth is already the target's undiluted depth (Kepler's catalogue depth is corrected for Kepler crowding). If the transit is really on a neighbour, the TESS depth would differ; that is a false-positive scenario for TRICERATOPS (section 7), not a dilution question.

Worked example, computed here, with an invented 1000 ppm Kepler depth and a 3-hour duration: for KIC 6699887 the TIC cone (208 sources within 108 arcsec) gives a Tmag 14.39 neighbour 10 arcsec away, which is 1.15 mag brighter than the target. The Gaussian-weighted contamination is c = 5.2 for a 4-pixel aperture and 6.2 for a 6-pixel aperture, and the hard 42 arcsec sum gives 8.5. The TESS depth is then about 161 ppm, T_total is 13.56, the ticgen noise at T_total is 1655 ppm per hour, and the per-transit significance is 0.17, folded 0.38 for 5 transits and 0.53 for 10. A non-detection is expected for such a star whatever the exact noise model. The example is arithmetic, not a claim about KIC 6699887's real candidate.

### 3.4 Is the TIC `contratio` enough?

No, for this use. The column is the published contamination ratio, defined above ([Stassun et al. 2019, Section 3.2.1][stassun19]; [Stassun et al. 2018, Appendix A, notes on column 85][stassun18]), computed with a small magnitude-dependent aperture (section 3.1). It is filled only for Candidate Target List stars. Checked here with the [MAST API][mast-api]: for KIC 6699887, A's neighbourhood and B's neighbourhood it is null; in a 0.25 degree cone at the field centre, 65 of 9,772 TIC sources have a value, the faintest at Tmag 15.95; of the 132 sources brighter than Tmag 13.5, 41 have one. [Christ et al. 2019][christ] used it for Kepler planets because they were limited to hosts that are mostly brighter. SkySift candidates fainter than about Tmag 13 should compute c themselves from neighbours (section 4 gives the query), and use `contratio` only as a check where it exists.

## 4. Getting Tmag, neighbours and contratio for a KIC star without astroquery

The service is the MAST "Mashup" API: an HTTP POST to `https://mast.stsci.edu/api/v0/invoke` whose form field `request` holds a JSON object ([MAST API services][mast-api], [MashupRequest properties][mast-req]). The older path `https://mast.stsci.edu/api/v0.1/Catalogs/filtered/Tic` returns HTTP 404 (checked here on 22 October 2026), so do not use it. The TIC services are `Mast.Catalogs.Filtered.Tic`, `Mast.Catalogs.Filtered.Tic.Position` and `Mast.Catalogs.Tic.Cone` ([MAST API services][mast-api]).

By KIC number (verified here; the `columns` value was ignored and all 125 columns came back):

```python
import json, urllib.parse, urllib.request
def mast(req):
    data = urllib.parse.urlencode({"request": json.dumps(req)}).encode()
    r = urllib.request.Request("https://mast.stsci.edu/api/v0/invoke", data=data,
        headers={"Content-type": "application/x-www-form-urlencoded", "Accept": "text/plain"})
    return json.loads(urllib.request.urlopen(r, timeout=90).read())

star = mast({"service": "Mast.Catalogs.Filtered.Tic", "format": "json",
             "params": {"columns": "*", "filters": [{"paramName": "KIC", "values": ["6699887"]}]}})
```

Cone search for the neighbours (radius is in decimal degrees, default 0.02, [MAST API services][mast-api]; 0.03 degrees is 108 arcsec and returned 208 sources for KIC 6699887):

```python
nb = mast({"service": "Mast.Catalogs.Tic.Cone", "format": "json",
           "params": {"ra": ra, "dec": dec, "radius": 0.03}})   # data rows include ID, ra, dec, Tmag, GAIAmag, gaiabp, gaiarp, contratio, numcont, KIC, objType
```

`Mast.Catalogs.Filtered.Tic.Position` also worked with a Tmag range filter plus `ra`, `dec` and `radius` ([MAST API services][mast-api]; verified here). The rows come back as `data` (list of dicts) with `fields` giving column names. The request object also has `page` and `pagesize` properties (default 1 and 1000, [MashupRequest][mast-req]); a 0.25 degree cone returned 9,772 rows in one response in my test without them, but use paging for wide cones. TIC version in the returned rows is "20190415", which is TIC 8 built on Gaia DR2 ([MAST API][mast-api]; [Stassun et al. 2019][stassun19]); Gaia DR3 neighbours are available from `Mast.Catalogs.GaiaDR3.Cone` if proper-motion accuracy matters. Filter on `objType == "STAR"` and drop the target by `ID`. `Mast.Catalogs.Tic.Cone` returns the TIC row of the target as well as neighbours, so match the target by KIC or ID, not by being nearest.

## 5. Can a folded TESS light curve be had cheaply?

TESScut gives FFI cutouts for a position and a sector as a ZIP of FITS files ([TESScut docs][tesscut]):

- Base URL `https://mast.stsci.edu/tesscut/api/v0.1/`.
- Cutout: `GET /astrocut?ra=RA&dec=DEC&x=NX&y=NY&units=px&sector=S`. Units may be `d`, `s`, `m` or `px`, default `px` at 21 arcsec per pixel. The response is `application/zip`; `{"results": []}` if nothing is made.
- Sector list: `GET /sector?ra=RA&dec=DEC` returns JSON with `sectorName`, `sector`, `camera`, `ccd`.
- Limits: a request above 10,000 pixels returns 400; 5 requests per second per user, beyond which the service returns 503; asking for a sector that does not cover the position returns 400 ([TESScut docs][tesscut]). The [astroquery documentation][astroquery-cut] adds that each user may make at most 10 simultaneous calls and that the client warns at 30 pixels in either dimension (a client warning, not a server limit).
- Only SPOC-based cutouts are served; TICA cutouts ended in August 2025 ([TESScut docs][tesscut]).

"Cheap" here would mean one `/sector` call per target (or the offline tess-point result) and one `/astrocut` call per observed sector per target, so 9 or 10 downloads for the Kepler-field targets. A 15 x 15 pixel cutout is far below the 10,000 pixel limit. I did not download a cutout (not needed for this research and I did not want to pull data files unasked), so I have not measured file sizes or latency. The cutout is raw calibrated FFI pixels, so the engineer still has to do aperture photometry, background subtraction, systematics correction and detrending, and then fold on the Kepler ephemeris. The pipeline products are no help for faint stars: the TESS-SPOC FFI light curves are selected to Tmag at most 13.5, 2-minute targets, bright-in-the-infrared targets and stars within 100 pc ([MAST TESS-SPOC page][spoc-hlsp], as reported by a search summary; I did not read the page text myself). Two routes exist for the photometry: eleanor ([Feinstein et al. 2019][eleanor]) and lightkurve's `search_tesscut`. [Giacalone et al. 2021][giacalone] used eleanor to produce 30-minute light curves for TRICERATOPS tests (Section 4, page 21).

My view for this project: the predicted significance can be computed with no downloads at all (sections 1 to 4), and the TESScut route is a second-stage confirmation that is only worth the effort for candidates where the prediction says TESS should have seen the transit. For a star like the worked example in section 3.3 (c above 5 and Tmag 15.5) a 1000 ppm candidate has a folded significance well below 1 even for ten transits, and a light curve could not show anything.

## 6. Precedent

- [Christ, Montet and Fabrycky 2019][christ] (AJ 157, 235) did this for every Kepler confirmed and candidate planet before TESS observed the field. Method: Kepler depths "contaminated" with the TIC ratio, noise "from Fig. 14 of Sullivan et al. (2015)" at the star's TESS magnitude, a 30-minute exposure, the Kepler duration and period, one or two sectors, and the sentence "we compute the expected signal-to-noise ratios (S/N) for each planet". Detection probability is a logistic function centred on a 7.1 sigma threshold, with 3 sigma also reported as adequate for characterising known planets. They list three caveats: that Sullivan's noise model is right, that the noise is white, and that the TIC contamination ratios are right. Result: 260 planets above 50 per cent probability at 3 sigma in one sector, 120 more in two ([Christ et al. 2019][christ]). This is the closest precedent for SkySift's statement; its noise assumption is the optimistic one.
- [Battley, Kunimoto, Armstrong and Pollacco 2021][battley] (MNRAS 503, 4092) report recovered transits and use the word "shallow" for the failures. Their own wording: many of the smallest Kepler planets were "indiscernible from noise in the TESS data" alone. Their Table A1 gives per-system exclusion reasons "outside" (epochs outside TESS windows) and "shallow". That is the same two-way split SkySift needs: transits not in the windows, and transits in the windows but too small.
- [Jontof-Hutter, Dalba and Livingston 2022][jontof] fitted TESS photometry of Kepler transit-timing-variation systems at the Kepler ephemerides. For non-detections they report that "the depth posteriors were typically within 2σ of zero", and they required a depth "inconsistent with zero at the 2σ level" for a detection.

These three give the wording and the logic; none states a per-candidate predicted significance for a Kepler candidate that was not recovered, so the SkySift statement is a modest extension of Christ et al.

## 7. TRICERATOPS validation thresholds, for citing beside each cross-check

From [Giacalone et al. 2021][giacalone] (AJ 161, 24; page numbers below are the arXiv v2 pagination; the journal's differ):

- Abstract: the criteria are "validated planets (FPP < 0.015 and NFPP < 10^-3), likely planets (FPP < 0.5 and NFPP < 10^-3), and likely nearby false positives (NFPP > 10^-1)".
- Section 4 (Planet Vetting and Validation), pages 18 to 19, near Figure 12: the sentence "We designate TOIs that exist within this region as likely planets" refers to NFPP < 10^-3 and FPP < 0.5; and "We therefore define validated planets as TOIs with NFPP < 10^-3 and FPP < 0.015 (or FPP ≲ 0.01, when rounding to the nearest percent)". The 1 per cent standard they cite is the VESPA convention.
- Figure 12 caption (page 20): for SNR above and below 15 the same regions apply.
- Figure 15 caption (page 21): with 30-minute FFI data no validation region could be identified; "likely planet" (NFPP < 10^-3 and FPP < 0.5) and "likely nearby false positive" (NFPP > 10^-1) still work. This matters because most faint SkySift candidates are FFI-only, and 30-minute cadence (Sectors 14, 15, 26) cannot support a "validated" label.
- Section 2.1 (pages 6 to 7): TRICERATOPS considers every star within 10 pixels, uses a 2D Gaussian PSF with sigma 0.75 pixel and the SPOC aperture, and its flux ratios agree with the SPOC's (their Figure 1). This is the dilution method of section 3 with one difference: they use the real SPOC aperture of each target.

## 8. What I could not confirm

- The ASCL record for ticgen and the "Jaffe and Barclay 2017" citation in the brief. An ASCL search for ticgen and for the two surnames returned nothing. ticgen is the GitHub repository `tessgi/ticgen` by Tom Barclay with a Zenodo DOI badge, last commit October 2017 ([ticgen README][ticgen-readme]). Cite the repository and Stassun et al. 2018.
- A published SPOC aperture size above Tmag 13, and Sullivan's Figure 14 pixel counts as numbers. I used 4 to 6 pixels as an assumption.
- TESScut cutout file sizes and latency (not downloaded).
- The 1.3 to 1.7 empirical excess in section 2.3 is a pooled median over faint Guest Investigator 2-minute targets, which are not a random sample of the Kepler field; it is an upper-end realism check, not a calibration.
- The Sector 109 pointing and the future Cycle 9 and 10 sectors are provisional per the [MIT page][mit-obs].
- Kepler ephemeris propagation. Transit epochs must be moved from Kepler time (BKJD, BJD minus 2454833, [Kepler archive manual][kepler-manual]) to TESS time by BTJD = BKJD - 2167 (since 2454833 - 2457000 = -2167). The timing uncertainty at the TESS epoch is the period uncertainty times the number of cycles from the Kepler reference epoch (about 2,000 to 5,600 days later for the sectors above), which can exceed the transit duration for long periods. The sensible output is an expected number of transits (the fraction of phase covered by windows, times the baseline over the period) with the window-edge uncertainty stated, not a claim of which transit falls where. This is my reasoning; Battley et al. 2021 report the same problem as "outside" cases.

## 9. What the engineer needs

Inputs per candidate: KIC number (or RA and Dec), Kepler period P, transit epoch T0 in BKJD with its uncertainties, duration T_dur in hours, Kepler depth (ppm, undiluted).

Steps and sources, in order:

1. **Resolve the star.** POST `Mast.Catalogs.Filtered.Tic` with the KIC filter ([section 4][mast-api]). Take `ID`, `ra`, `dec`, `Tmag`, `GAIAmag`, `gaiabp`, `gaiarp`, `contratio`. If `Tmag` is null, compute it from Gaia with the relation of section 3.3 ([Stassun et al. 2019, Eq. 1][stassun19]).
2. **Find the sectors.** Run tess-point offline on RA and Dec ([tess-point][tp-gh], section 1.4), keep sectors at or below the last completed sector (today 108), or call the TESScut `/sector` endpoint ([TESScut][tesscut]) as a check.
3. **Get the observing windows.** Read `TESS_FFI_observation_times.csv` ([MIT][mit-ffi]), repair the Sector 26 placeholder from `TESS_orbit_times.csv` ([MIT][mit-orbit]), convert UTC to BTJD ([BTJD definition][btjd]). The windows for the 13 Kepler-field sectors are in section 1.2.
4. **Count transits.** Convert T0 to BTJD (BTJD = BKJD - 2167, [Kepler manual][kepler-manual]); count epochs T0 + nP that fall inside a window with at least half the duration covered. Record N per sector. Treat edge cases and long periods per section 8.
5. **Dilute.** Cone-search the TIC for neighbours (0.03 degrees, section 4), compute Gaussian-weighted aperture fractions with sigma 0.75 pixel ([Giacalone et al.][giacalone]) for 4 and 6 pixel apertures ([Guerrero et al.][guerrero] for aperture sizes), give c, depth_tess = depth_kepler / (1 + c), T_total = Tmag - 2.5 log10(1 + c) (section 3.3). Report the hard 42 arcsec case as an upper bound on dilution ([Ricker et al.][ricker]).
6. **Noise.** sigma_1h at T_total from the ticgen polynomial (nominal, [Stassun et al. 2018 v1][stassun18-v1] and [ticgen][ticgen-src]) and times the empirical factor of 1.3 to 1.7 for the conservative case ([Twicken et al. 2025 CDPP tables][twicken]; table in section 2.3). Scale to T_dur with the empirical CDPP ratios of section 2.4 for the conservative case and 1/sqrt(T_dur) for nominal.
7. **Significance.** Per transit: SNR_1 = depth_tess / sigma(T_dur). Folded: SNR = sqrt(sum over sectors of N_s) x depth_tess / sigma(T_dur) in the same noise band, or the multi-sector form of [Twicken et al. 2025, Eq. 3][twicken] if sectors differ in noise ([Giacalone et al. Eq. 17][giacalone] is the same).
8. **State the verdict.** Suggested wording (my proposal, not sourced): "TESS should have detected" if the conservative folded SNR is at least 7.1 (the Kepler threshold [Christ et al.][christ] centre their logistic on; [Sullivan][sullivan] use 7.3); "TESS would not be expected to detect" if the nominal folded SNR is below 3 ([Christ et al.][christ] use 3 for characterisation); otherwise "marginal". Zero transits in the windows means "TESS did not observe a transit", not a non-detection.
9. **Cross-check labels.** Where TRICERATOPS is run, cite beside the result: validated is FPP < 0.015 and NFPP < 10^-3, likely planet is FPP < 0.5 and NFPP < 10^-3, likely nearby false positive is NFPP > 10^-1 ([Giacalone et al., abstract and Section 4][giacalone]); FFI-only 30-minute data can reach "likely planet" but not "validated" (Figure 15 caption).
10. **Optional confirmation.** TESScut `/astrocut` per sector, then eleanor or lightkurve photometry, fold on the Kepler ephemeris ([TESScut][tesscut]; [eleanor][eleanor]), only for candidates where step 8 says TESS should have seen the transit.

Working files from this research (scripts and downloaded tables) are in the session scratchpad and are not part of the repository.

## 10. Sources

[heasarc-sector]: https://heasarc.gsfc.nasa.gov/docs/tess/sector.html
[mit-obs]: https://tess.mit.edu/observations/
[mit-orbit]: https://tess.mit.edu/public/files/TESS_orbit_times.csv
[mit-ffi]: https://tess.mit.edu/public/files/TESS_FFI_observation_times.csv
[heasarc-dp]: https://heasarc.gsfc.nasa.gov/docs/tess/data-products.html
[em-guide]: https://heasarc.gsfc.nasa.gov/docs/tess/docs/Guide-to-TESS-for-EM-planning.pdf
[cycle10]: https://heasarc.gsfc.nasa.gov/docs/tess/tess-cycle-10-pointings-are-available-now.html
[tp-gh]: https://github.com/christopherburke/tess-point
[pypi-tp]: https://pypi.org/project/tess-point/
[ascl-tp]: https://ascl.net/2003.001
[tesscut]: https://mast.stsci.edu/tesscut/docs/getting_started.html
[astroquery-cut]: https://astroquery.readthedocs.io/en/latest/mast/mast_cut.html
[sullivan]: https://arxiv.org/abs/1506.03845
[stassun18]: https://arxiv.org/abs/1706.00495
[stassun18-v1]: https://arxiv.org/pdf/1706.00495v1
[stassun19]: https://arxiv.org/abs/1905.10694
[ticgen-src]: https://github.com/tessgi/ticgen/blob/master/ticgen/ticgen.py
[ticgen-readme]: https://github.com/tessgi/ticgen
[ricker]: https://arxiv.org/abs/1406.0151
[guerrero]: https://arxiv.org/abs/2103.12538
[twicken]: https://arxiv.org/abs/2506.04136
[mast-cdpp]: https://archive.stsci.edu/tess/bulk_downloads/bulk_downloads_tce.html
[mast-api]: https://mast.stsci.edu/api/v0/_services.html
[mast-req]: https://mast.stsci.edu/api/v0/class_mashup_1_1_mashup_request.html
[giacalone]: https://arxiv.org/abs/2002.00691
[christ]: https://arxiv.org/abs/1810.02826
[battley]: https://arxiv.org/abs/2103.03259
[jontof]: https://arxiv.org/abs/2207.08917
[btjd]: https://arxiv.org/abs/2106.08341
[kepler-manual]: https://archive.stsci.edu/kepler/manuals/archive_manual.pdf
[eleanor]: https://arxiv.org/abs/1903.09152
[spoc-hlsp]: https://archive.stsci.edu/hlsp/tess-spoc
