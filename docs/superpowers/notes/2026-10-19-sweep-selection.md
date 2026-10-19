# First real vetted sweep: which stars, and why (19 October 2026)

The calibration pilot (detector version 5, 1,000 stars, three searches each) sets the detection threshold and runs for
about 35 hours on six of this machine's 24 cores. A vetted sweep can run beside it on another six without touching the
pilot or the laptop's headroom, so the question was which stars give the most for the least time. The registry after
the 19 October rule change holds 118,648 stars Kepler never targeted; a full pass costs about 1,800 hours on six
workers, so the first sweep takes a list.

## The list: `2026-10-19-cool-dwarfs.txt` (1,590 stars)

Built by `scripts/select_cool_dwarfs.py` from the cached v1.0 KBonus catalogue and Gaia DR3.

| Step | Stars left | Reason |
|---|---|---|
| Registry (not a Kepler target, G <= 19, psffrac >= 0.5) | 118,648 | the unsearched population |
| Quarters >= 6 | | three or more transits out to about 150 d; the season test works |
| G <= 17 | | per-cadence scatter under about 2,000 ppm |
| BP - RP >= 1.2 (K and M dwarfs) | | a given planet makes a deeper transit on a smaller star; KIC 5689121 is at 2.19 |
| crowdsap >= 0.7 | 2,758 | most of the aperture flux is the star's own, so depths are not diluted |
| Gaia parallax_over_error >= 5 and M_G >= 4 | 1,590 | 699 giants removed (a quarter of the red sample), 469 without a usable parallax dropped |

Kept: 1,541 KIC-numbered bystanders and 49 no-KIC stars; median G 16.29, median 17 quarters, median BP - RP 1.40,
median M_G 6.65; 302 M dwarfs (BP - RP >= 1.8). Order in the file: M dwarfs first, then K dwarfs, brightest first
within each, so the sweep does the best stars first and can be stopped at any point.

Cost: about 7 minutes per star for pre-whitening, the first pass and the masked pass when a signal is found, so
about 30 hours for the whole list on six workers; the 302 M dwarfs take the first six hours.

## How it runs

```
SKYSIFT_THRESHOLDS__SDE_MIN=13 python -m skysift run --source kbonus_bkg \
    --targets docs/superpowers/notes/2026-10-19-cool-dwarfs.txt --workers-cpu 6 --workers-io 4 --retention keep
```

- Threshold 13 is provisional: at 300 pilot stars the scrambled null's maximum is 16.8 and its 99th percentile 12.0.
  The threshold is not part of the search version, so when the pilot sets the final value the stored signals are
  vetted again, not searched again.
- The sweep shares the pilot's data directory (raw files cached once, results on the dashboard) and runs at
  below-normal process priority with the pilot, leaving twelve cores and the foreground to normal work.
- `--targets` is new (commit of this date): the listed ids in the file's order, registry filters still applied,
  `unknown_targets` in the summary for ids the source did not yield.

## What success looks like

Signals that pass all five rungs appear on the Candidates page. Expect most strong detections on this sample to be
eclipsing binaries and contact systems rejected by rung 2; a planet-like survivor on a cool dwarf is the target.
