# Leakage shares of KIC 6146676 before and after spec 12.1 (23 October 2026)

The shares before are the stored `stars` rows of .superpowers/sdd/2026-10-22-skysift-referee-pack/live/results-r2/kic-006146676.json (the round-2 rule: the contamination budget shared by the package's box fractions in the Kepler band); rung 5's seasons come from .superpowers/sample-run/dossiers/kic-006146676/dossier_data.json. The budget is CROWDSAP 0.85650748, so (1 - CROWDSAP) / CROWDSAP = 0.16753. Rung 5's fitted PSF width is 0.5695906 Kepler pixels, the mean over its 4 voting seasons with a width (0.5822 (season 0), 0.5531 (season 1), 0.5457 (season 2), 0.5973 (season 3)). The rule of spec 12.1 gives each neighbour the fraction of a Gaussian of that width at its separation that falls inside the target's 3 x 3 box, over that of a star at the centre, times its Kepler-band flux relative to the target's (Gaia G as the proxy), and scales the products down to the budget only when their sum passes it. Here the products sum to 0.1549 over the 35 neighbours with a Kepler-band flux, below the budget, so the cap never binds for this star and the shares after the rule are the products themselves.

| TIC | Gaia DR3 | sep (arcsec) | sep (Kepler px) | G | Kepler flux / target | overlap | share before | share after |
|---|---|---|---|---|---|---|---|---|
| 1879962333 | 2073896294004902144 | 6.94 | 1.74 | 19.77 | 0.04 | 0.3375 | 0.002488 | 0.01301 |
| 169822636 | 2073896289692192128 | 10.78 | 2.71 | 17.90 | 0.22 | 0.017 | 0.003244 | 0.003688 |
| 169822655 | 2073896190925687296 | 11.69 | 2.94 | 12.81 | 23.56 | 0.005864 | 0.1615 | 0.1381 |
| 508690015 | 2073896190926955520 | 13.15 | 3.30 | 19.74 | 0.04 | 0.0007749 | 0.0001332 | 3.087e-05 |
| 169822646 | 2073896190921609600 | 13.58 | 3.41 | 19.38 | 0.06 | 0.0003949 | 0.0001355 | 2.185e-05 |
| 169822657 | 2073896289692177024 | 18.37 | 4.62 | 18.88 | 0.09 | 2.277e-08 | 1.595e-06 | 1.989e-09 |
| 1879962327 | not matched | 20.89 | 5.25 | 20.55 | 0.02 | 2.377e-11 | 5.159e-09 | 0 |

NFPP before: 0.022 (16th to 84th percentile 0.016 to 0.032) over 20 runs.

NFPP after: not yet measured (the round-3 run).

The largest share after the rule is 0.138 (TIC 169822655, 11.69 arcsec, 2.94 Kepler pixels): its overlap 0.0059 times its Kepler-band flux of 23.6 times the target's. Spec 12.1 expected a star beyond two Kepler pixels to take effectively nothing; this one keeps most of its round-2 share, so the NFPP it drives is for the controller to read before the live runs.

Both columns, and the NFPP each drove, are an upper bound for the KBonus PSF photometry, since the PRF fit removes a resolved neighbour's light to first order; the third variant of spec 12.2, with the neighbours rung 5's difference image excludes removed, is where those neighbours are taken out.

The shares use a Gaussian of rung 5's fitted width over the target's 3 x 3 box, which has no PRF wings, so each is a floor on the light that neighbour leaks into the KBonus PSF light curve (Bryson et al. 2013 on PRF-fit decontamination; Martinez-Palomera et al. 2023 on the KBonus photometry).
