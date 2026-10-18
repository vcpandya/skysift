# Injection and recovery on real KBonus light curves (18 October 2026)

Scripts: `scripts/inject_recovery.py` (the study), `scripts/inject_misses.py` (full signal lists for chosen misses),
`scripts/inject_longp.py` (gap-edge mask widths on long-period misses), `scripts/inject_rednoise.py` (red-noise SNR).
Data: the 30 cached KBonus background light curves of the sample sweep (`.superpowers/sample-run`, git-ignored),
engine at main 604ecb7 plus the rung 4 and dashboard commits of the same day (search version 2+fe1bc418: BoxDip 2,
pre-whitening 3, detrend 3, sde_min 11, window 0.5 to 30 d). 120 box transits: four per star at nominal white-noise
stacked SNR 7, 10, 15 and 25, periods log-uniform in 0.8 to 25 d avoiding the star's own peaks, a solar-ish duration
(13 h at one year scaling as P^(1/3), floor 1.5 h), a random epoch, depth capped at 5000 ppm. Each injected curve went
through the production worker (`pipeline._cpu_work`: detrend, pre-whiten, search, restore, rungs 1 to 3; rung 4 off).
"Found" means a returned signal within 1 percent of the injected period or of its 2, 1/2, 3 or 1/3 multiple.

## Completeness against the nominal (white-noise) SNR

| nominal SNR | n | found | ranked first | depth ratio (median) | rung 2 fail | rung 3 fail | pass rungs 1-3 |
|---|---|---|---|---|---|---|---|
| 7 | 30 | 10 | 8 | 1.06 | 1 | 0 | 2 |
| 10 | 30 | 17 | 15 | 0.96 | 0 | 0 | 9 |
| 15 | 30 | 22 | 21 | 0.86 | 0 | 0 | 16 |
| 25 | 30 | 29 | 28 | 0.91 | 2 | 0 | 24 |

By period (nominal SNR 10 and above): 0.8 to 3 d 33 of 36 found, 3 to 10 d 25 of 33, 10 to 25 d 10 of 21.

## The nominal SNR overstates the signal: red noise

The nominal SNR uses the per-point robust scatter. The noise a box of one transit duration actually sees is the
scatter of duration-wide bin means, which on these faint background stars (G about 16 to 19) exceeds the white
expectation by a median factor 1.26 (10 to 90 percent: 1.13 to 1.67), and the 1 d scatter by 2 to 3. Against the
red-noise SNR the picture is clean:

| red-noise SNR | n | found | pass rungs 1-3 | median SDE of found |
|---|---|---|---|---|
| 5 to 7 | 34 | 8 | 0 | 8.7 |
| 7 to 9 | 21 | 11 | 5 | 10.4 |
| 9 to 11 | 9 | 8 | 4 | 13.9 |
| 11 to 14 | 22 | 21 | 16 | 18.7 |
| 14 to 20 | 13 | 12 | 10 | 20.0 |
| above 20 | 18 | 18 | 16 | 35.5 |

At red-noise SNR 9 and above: 59 of 62 found (95 percent), and the period dependence disappears (0.8 to 3 d 28 of
29, 3 to 10 d 23 of 23, 10 to 25 d 8 of 10). The detection threshold `sde_min` 11 corresponds to a red-noise SNR of
about 9 to 11. The long-period deficit in the first table is therefore a property of the stars, not of the search.

## Gap-edge mask widths on long-period misses (`inject_longp.py`)

Five nominal-SNR-15 misses with P between 13.7 and 24.3 d, searched with gap-edge masks of 0, 1.5 (production) and
3.0 d: the SDE at the injected period moves from 1.5 to 6.6 (no mask) to 1.5 to 4.9 (1.5 d) to 2.0 to 7.3 (3.0 d),
never near 11, at a 10 percent point cost for 3.0 d. Post-gap ramps are not what hides these transits; red noise is.
Ruling: the ramp-tail model proposed on 17 October is not pursued.

## Depth bias

Measured over injected depth is 0.86 to 0.96 (median per SNR bin; 10 to 90 percent 0.83 to 1.05) for recoveries at
the right period: the BLS box depth is about 10 percent low, because the trial durations (1, 1.5, 2, 3, 4, 6, 9, 12 h)
rarely equal the true one and a wider box dilutes the depth. The stacked-fold depth of rung 2 is the better estimator;
candidate radii should use it. Not changed in this pass.

## The three misses that were defects, and what changed

1. **Companions of eclipsing binaries were never recovered.** On the K06356.01 binary (24.09 d, V-shaped 11 h
   eclipses) all four injections failed, including 2.108 d at 271 ppm with first-pass SDE 11.3. Two causes: the
   top-3 first-pass slots were the binary's 24.09, 12.045 and 8.03 d sub-harmonics, which the resolver collapses to
   one signal, so the planet never entered the list; and the masked pass dropped only 0.75 durations (4.5 h) of a 6 h
   box around an 11 h eclipse, so the wings dominated the masked spectrum and the 24.09/7 d residue (SDE 35.9) came
   out as the second-pass signal and passed rungs 1 to 3 (a false candidate on any binary without a catalogue entry).
   Fix (detector version 3): the mask covers the eclipse extent measured on the fold, the second-pass alias filter goes
   to order 8, and the first-pass list is refilled until it holds top_k independent signals.
2. **Aliases of a kept signal consumed the list.** On the K04802.01 star (0.6056 d) an injected 10.65 d transit at
   first-pass SDE 11.7 never appeared because the slots went to 0.6056 d and its 1.2111 and 1.8167 d aliases. Same
   fix: aliases of a kept signal are skipped and counted, not returned.
3. **A half-harmonic restore faked an odd/even difference.** On the 0.721 d binary gaia-dr3-2076894352958341376 an
   injected 1.057 d transit had the binary's fundamental restored (1.387 per day is about 1.5 times the detection
   frequency) and rung 2 rejected it as odd_even 8.8 sigma. Fix: the restore puts back integer harmonics only.

Two rung 2 failures were not defects: one injection was ranked at P/2 with the true period second in the list (the
alias row fails odd_even, the true row passes; the resolver did not double it because the control-phase error on that
red star is inflated), and one 23.5 d injection on the K04209 companion star (a 0.72 d, 3000 ppm binary) failed
`eccentric_secondary` through contamination by the binary's eclipses. No injected box was ever rejected as
`sinusoidal`, so the shape contrast of rung 2 needs no parameter penalty (the 17 October follow-up is closed). Rung 3
rejected no injection.

## Reading the result for production

- Completeness is about 95 percent above red-noise SNR 9 and about 35 percent between 5 and 9, independent of period.
- The published completeness of a sweep should be quoted against red-noise SNR, which the engine can compute per star
  from duration-wide bins; a planned `skysift inject` command would make this study a standing regression.
- Depth is 10 percent low from the box; use the stacked-fold depth for radii.
- The three defects above are fixed on main; the study should be re-run after the detector version 3 change to
  confirm the binary companions are recovered.
