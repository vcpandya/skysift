# Leakage shares of KIC 6146676 before and after spec 12.1 (23 October 2026)

The shares before are the stored `stars` rows of .superpowers/sdd/2026-10-22-skysift-referee-pack/live/results-r2/kic-006146676.json (the round-2 rule: the contamination budget shared by the package's box fractions in the Kepler band); rung 5's seasons come from .superpowers/sample-run/dossiers/kic-006146676/dossier_data.json. The budget is CROWDSAP 0.85650748, so (1 - CROWDSAP) / CROWDSAP = 0.16753. Rung 5's fitted PSF width is 0.5695906 Kepler pixels, the mean over its 4 voting seasons with a width (0.5822 (season 0), 0.5531 (season 1), 0.5457 (season 2), 0.5973 (season 3)). The rule of spec 12.1 gives each neighbour the fraction of a Gaussian of that width at its separation that falls inside the target's 3 x 3 box, over that of a star at the centre, times its Kepler-band flux relative to the target's (Gaia G as the proxy), and scales the products down to the budget only when their sum passes it. Here, over the 35 neighbours with a Kepler-band flux, the cap bound (the weights sum to 0.200 against the budget 0.168), so the shares are scaled to the budget, a factor of 0.8372.

The leakage budget behind the nearby false-positive probability has three parts. First, it comes from the catalogue, not from SkySift: (1 - C) / C = 0.1675, with C = 0.8565 the KBonus crowding metric for this extraction (the crowdsap column of the KBonus-bkg v1.0 source catalogue (Martinez-Palomera et al. 2023)), the catalogue's own estimate that 14 percent of the fitted flux belongs to other sources, PRF wings included. Second, its split among the neighbours follows the PRF overlap rule: each neighbour's share is its Kepler-pixel overlap at rung 5's PSF width (0.570 Kepler pixels) times its Kepler-band flux relative to the target's, as an absolute fraction of the target's flux. Third, the cap bound (the weights sum to 0.200 against the budget 0.168), so the shares are scaled to the budget. `src/skysift/sources/kbonus_bkg.py` reads C into the target's `meta["crowdsap"]`; the light-curve file's per-quarter CROWDSAP keywords (`lc/kbonus.py`) are the same metric per quarter.

The overlaps are taken at the Gaia DR3 separations the dossier stores for the matched neighbours (epoch 2016), the separations the exclusion test and the stars block use, not at the TIC's epoch-2000 ones the round-2 rows carry (beside them in the table); a star no Gaia neighbour matched keeps its TIC separation. For TIC 169822655, which takes the largest share, that is 11.45 arcsec against 11.69. At the Gaia separations the products sum to 0.2001 against the budget of 0.1675, so the cap binds; at the TIC separations they sum to 0.1549, and the cap does not bind. That star's overlap sits far down the Gaussian's tail, so it moves with the width too: 0.05 Kepler pixels less or more than 0.570 changes it by -49 and +69 percent. The overlap is one-dimensional, along the separation, a lower bound on the two-dimensional box fraction, so every share here is a floor.

| TIC | Gaia DR3 | sep (arcsec) | TIC sep (arcsec) | sep (Kepler px) | G | Kepler flux / target | overlap | share before | share after |
|---|---|---|---|---|---|---|---|---|---|
| 1879962333 | 2073896294004902144 | 7.14 | 6.94 | 1.79 | 19.77 | 0.04 | 0.3051 | 0.002488 | 0.009845 |
| 169822636 | 2073896289692192128 | 10.94 | 10.78 | 2.75 | 17.90 | 0.22 | 0.01428 | 0.003244 | 0.002594 |
| 169822655 | 2073896190925687296 | 11.45 | 11.69 | 2.88 | 12.81 | 23.56 | 0.007861 | 0.1615 | 0.155 |
| 508690015 | 2073896190926955520 | 12.97 | 13.15 | 3.26 | 19.74 | 0.04 | 0.001023 | 0.0001332 | 3.414e-05 |
| 169822646 | 2073896190921609600 | 13.51 | 13.58 | 3.40 | 19.38 | 0.06 | 0.0004418 | 0.0001355 | 2.046e-05 |
| 169822657 | 2073896289692177024 | 18.62 | 18.37 | 4.68 | 18.88 | 0.09 | 1.197e-08 | 1.595e-06 | 8.753e-10 |
| 1879962327 | not matched | 20.89 | 20.89 | 5.25 | 20.55 | 0.02 | 2.377e-11 | 5.159e-09 | 0 |

NFPP before: 0.022 (16th to 84th percentile 0.016 to 0.032) over 20 runs.

NFPP after: not yet measured (the round-3 run).

The largest share after the rule is 0.155 (TIC 169822655, 11.45 arcsec, 2.88 Kepler pixels): its overlap 0.0079 times its Kepler-band flux of 23.6 times the target's, scaled by 0.8372 to the budget. Spec 12.1 expected a star beyond two Kepler pixels to take effectively nothing; this one keeps most of its round-2 share, so the NFPP it drives is for the controller to read before the live runs.

The shares after the rule are a floor on the light each neighbour puts in the target's 3 x 3 box, and that box light, with the NFPP it drives, is an upper bound for the KBonus PSF photometry, since the PRF fit removes a resolved neighbour's light to first order. The third variant of spec 12.2, with the neighbours rung 5's difference images exclude removed, is where those neighbours are taken out.

The shares use a Gaussian of rung 5's fitted width over the target's 3 x 3 box, taken in one dimension along the separation (a lower bound on the two-dimensional box fraction) and with no PRF wings, so each is a floor on the light that neighbour puts in that box. That box light is in turn an upper bound on what leaks into the KBonus PSF light curve, since the PRF fit removes a resolved neighbour's light to first order (Bryson et al. 2013 on PRF-fit decontamination; Martinez-Palomera et al. 2023 on the KBonus photometry).
