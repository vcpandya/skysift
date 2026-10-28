# SkySift

Archival time-series search and vetting engine. See docs/superpowers/specs/.

## Purpose

SkySift searches archival Kepler and TESS light curves for transit, eclipse and lensing candidates. A sweep fetches each
target's light curve, runs a detector, pushes every signal through a ladder of vetting tests, and stores the outcome, so
that only the targets that survive need follow-up. Milestone 1 ships the transit box-dip detector and vetting ladder
rungs 1 to 4; the eclipsing-binary and lensing branches are planned. The detector keeps its three strongest peaks and, when the strongest clears the detection threshold, masks that signal and searches the remaining points once more, so a dominant variable does not hide a second, independent signal on the same star. Before the search the star's significant sinusoids (pulsation, rotation) are removed by harmonic pre-whitening, with box-shaped eclipses and transits protected; the removed waves are listed on the signal page and subtracted from its plots.

The ladder (version 3, after the published Kepler Robovetter practice, T18 Appendix A) runs cheapest rung first. Rung 1 checks the detection (SDE, SNR, event count) and then its events: coverage of each event (A.3.7.1 and A.3.8), the fraction of gapped events, single-event dominance above 90 d (A.3.5), Chases when at most five events exist (A.3.3) and Zuma, which drops events that brighten (A.3.7.5). Rung 2 tests the shape: the sinusoid contrast, SWEET at P/2, P and 2P (A.3.2, A.4.2), the model-shift uniqueness tests MS2 and MS3 (A.3.4), odd against even, the secondary, and a uniform-disc transit fit that fails V-shaped or too deep events (A.4.3). Two exceptions keep real planets: an equal secondary at phase 0.5 returns UNDETERMINED with half the detected period as `suggested_period` (the planet may have been found at twice its true period), and a faint secondary that fits a planet's occultation passes with its `occultation_albedo` (A.4.1.3). Rung 3 checks stability across seasons and between the SAP and PSF apertures; a disagreement goes to review (UNDETERMINED), not to rejection. Rung 4 matches known objects by period and epoch (A.6); a period match whose epoch differs also goes to review.

## Quick start

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -e ".[dev]"      # on Linux or macOS: .venv/bin/python
.venv/Scripts/python.exe -m skysift sources
.venv/Scripts/python.exe -m skysift run --source kbonus_bkg --limit 50
.venv/Scripts/python.exe -m skysift status
.venv/Scripts/python.exe -m skysift vet --source kbonus_bkg --target-id kic-005689121
```

With the virtual environment activated you can shorten each command to `python -m skysift ...`.

## Running the CLI

Run the CLI as a module: antivirus software sandboxes freshly built console-script executables, which can freeze a
machine, so use `python -m skysift` (the `skysift` entry point still exists for containers).

```bash
python -m skysift --help
python -m skysift sources
python -m skysift run --source kbonus_bkg --limit 100 --min-period 0.5 --max-period 30
python -m skysift status
python -m skysift vet --source kbonus_bkg --target-id <ID>
```

The KBonus registry (`--source kbonus_bkg`) holds the stars Kepler never searched: the Gaia sources without a KIC id and the KIC-numbered stars that were extracted from a neighbour's pixels rather than their own (catalogue `kic` differing from `tpforg`), 118,648 targets after the brightness and PSF-fraction cuts. `--all` adds the 166,215 stars Kepler itself targeted. KIC 5689121, the 13.77 d candidate of the quick start, is one of the 84,487 KIC bystanders.

`--targets FILE` sweeps a named list instead of the priority order: one target id per line (`#` comments allowed), in the file's order, with the registry filters still applied; the summary counts ids the source did not yield as `unknown_targets`. The first real sweep used `docs/superpowers/notes/2026-10-19-cool-dwarfs.txt`, built by `docs/superpowers/notes/scripts/select_cool_dwarfs.py` (see the note of the same date).

A sweep resumes where it stopped. Each target's job key holds the detector version plus a hash of the period window
and search grid, so a run with a different `--min-period` or `--max-period` searches the same targets again. A target
whose job fails three times is not retried; `run` counts it as `gave_up` and `status` reports it under
`error_capped_targets`. A failure caused by a service outage (MAST or a catalogue unreachable) is recorded but never counted as one of the three attempts.

Stopping a sweep has two modes. The first Ctrl+C is the graceful stop: the engine starts no new target, finishes
the targets already fetched or being fetched (at most two per I/O worker plus two per CPU worker, each up to a few
minutes), writes their results and exits with its summary, which then carries `"stopped": "graceful"`. A second
Ctrl+C stops at once: in-flight work is dropped and the interrupted runs are marked superseded when the target is
redone. Either way the same command resumes the sweep, since every finished target is skipped by its job key.

## Dashboard

```bash
python -m skysift ui
```

The dashboard is a local review tool, not a public service. Start it on the machine that holds the data directory.
It opens http://127.0.0.1:8765/ in the browser. It binds to the loopback interface only and has no authentication.
The `ui` command refuses any other `--host`. The one exception is a container, where `SKYSIFT_UI_ALLOW_CONTAINER=1`
allows `0.0.0.0`. To reach it from another machine, use an SSH tunnel
(`ssh -L 8765:127.0.0.1:8765 <host>`). Each page carries a per-process CSRF token, and every POST must send it back.

Pages, in the order of the navigation, and two modes: **simple** (the default) explains each signal in plain words,
**expert** adds the tables, raw metrics and keys. The mode and the light or dark theme are kept in a cookie.

* **Tonight**: the landing page. What the last sweep (or the running one) scanned and found, the candidates waiting
  for a decision as cards with their folded transit, and the signals being rechecked, by what happens next. The expert
  view adds the throughput and the rung funnel.
* **Candidates**: the review queue, filtered by status (waiting for a decision, stage 1 and stage 2 candidates,
  undetermined, rejected, superseded, approved, needs data, all), kept to the current search version unless asked
  otherwise, with a target search. Simple mode shows cards; expert mode a sortable table whose rows open beside it at
  1280 px and wider. Each verdict pill opens a popup that says why, check by check. A signal's page shows the ladder
  with the metrics that matter per rung, the periodogram (per-band SDE envelope with threshold, aliases and systematic
  periods), the detrended curve, the phase fold with the transit model, odd against even, the phase 0.5 window and
  per-season depths. Approve, Reject (a note is required) and Needs data append a row to the `decisions` table.
  Decisions are append-only, so nothing is deleted and the full history stays visible. Keys: `a`, `r`, `n`, `j` for
  next, `k` for previous, `?` lists them.
* **Live**: the running sweep as a progress ring with its stop controls, the last finished targets, targets per state,
  signal outcomes, rung verdicts, throughput, cache against budget, and the sweep's log (expert). The page refreshes
  while a sweep runs. **Stop** asks the sweep to finish the targets it has already started and then exit (the page
  shows "finishing" until it does; nothing started is lost). **Stop now** ends the sweep process at once, and the
  targets it was working on are redone next time. Starting the same sweep again resumes it: finished targets are
  skipped by their job key.
* **Sources**: the registry with yield, CPU hours and GB per 10,000 stars, and progress per live source. It also holds
  the Start sweep form (limit, period window, retention, workers, periodogram storage). The sweep runs as a child
  `python -m skysift run` process. Only one sweep runs per data directory at a time, and it can only be launched from
  this page.
* **Approved**: current decisions by status, kept to the current search version unless asked otherwise. Each signal
  has a copyable summary and a `review.json` download with the signal, every test, every query, the artifacts and the
  decision history.
* **Dossiers**: one tile per candidate in the final set (reviewer-approved, stage 2 and stage 1 signals, and the
  undetermined ones that only the pixel stage could not decide; a tile without a dossier offers Generate),
  each with its two probability pills (**False alarm** from the null calibration, **False positive** from the
  scenario estimate of spec 4.2, whose priors were signed off on 21 October 2026). A tile opens the target's dossier
  (`/dossiers/<target>`): the signal, whether it repeats, the checks, which star is dimming (the difference images of
  every season), the star and the planet from Gaia, an illustration to scale, the caveats and how to reproduce it. The
  page can generate or refresh the dossier, download it as one standalone HTML file, and build the submission pack
  with its five steps.
* **About** (`/about`, linked from the footer): how SkySift works, what the words mean, the recheck steps, the
  thresholds in force and how to cite it.

HTMX is vendored in the package. Plotly is downloaded once on the first start into `<data_dir>/ui/assets/` and its
SHA-256 is verified. Afterwards the dashboard works offline. If Plotly cannot be fetched, the plots are disabled and
the page shows a notice.

Every sweep stores a periodogram summary per target (the SDE envelope over 2,048 log-period bins, about 8 kB) unless
told otherwise. `skysift run --periodogram all|signals|off` sets this for one sweep. `SKYSIFT_STORE_PERIODOGRAM`
(`all`, `signals` or `off`, default `all`) sets the default. The envelopes of targets without a signal form the null
set for calibrating `sde_min`. `skysift vet --spectrum` also writes the raw BLS spectrum for one target.

## Dossiers and submission packs

```bash
python -m skysift dossier --target kic-006146676 --export kic-006146676.html
python -m skysift pack --target kic-006146676 --tic 169822650
```

`dossier` writes `<data_dir>/dossiers/<target>/dossier_data.json` and its row in the `dossiers` table from the stored
signal, test rows, light curve and rung 5 pixel arrays, plus four Gaia queries (the star, its distance, its 2MASS Ks
and a 20 arcsec cone for the neighbours), each logged in the `queries` table. It downloads nothing else: a light curve
released by retention stops it with exit code 2 and the advice to sweep the target again with `--retention keep`. A
second run is a no-op until the test rows, the status, the reviewer decision or the size of the null calibration
change (`--force` regenerates, `--signal` picks another signal of the target). `--export` also writes the standalone
page, which loads nothing from any other address and names no local folder.

`pack` builds the submission pack from the stored dossier: a README with the five steps (freeze the record on
Zenodo, upload to ExoFOP-TESS, write a Research Note of the AAS, tell the NASA Exoplanet Archive, answer follow-up
requests), the Research Note draft, the data files, the figures, the provenance and the ExoFOP copies, and a zip inside
the folder (default `<data_dir>/packs/<target>/<UTC date>/`). The ExoFOP files are named
`<target>_<yyyymmdd>_<content>`; `--tic` puts the star's TIC id in the ExoFOP upload note and `--tag` starts the
description of the suggested Data Tag. With `--zenodo` it also creates a Zenodo draft deposition holding the zip on the
sandbox, with the personal access token in `SKYSIFT_ZENODO_TOKEN`. `--zenodo-live` makes it on zenodo.org instead, and
is refused before a reviewer approves the candidate, and while `fpp.SIGNED_OFF` is False (the factor tables of
spec 4.2 were signed off on 21 October 2026). The draft is never published: publishing is your click on
Zenodo. With `--zenodo` and no token the pack is still built, without a deposition, and the command exits with code 2.
`--deposition <id>` refills an unpublished draft of an earlier run instead of making a new one: its files are
removed, the zip takes their place, and the DOI it reserved is the one the texts carry (a published record is
refused). The record names the KBonus HLSP it is derived from and, with a `doi` in `CITATION.cff`, the archived
code it was compiled by.

The one-time check of the generated numbers against the hand-built KIC 6146676 study is
`docs/superpowers/notes/2026-10-21-dossier-comparison.md`, made with `python -m tools.dossier_compare`.

## Calibrating the detection threshold

Rung 1 passes a peak whose per-band SDE is at least `thresholds.sde_min`. That threshold is read from searches of
curves that cannot hold a real transit, so it has to be measured again after every change to the detrend, the
pre-whitening or the detector: each of them moves the noise peaks. `calibrate` runs that null search.

```bash
python -m skysift calibrate --source kbonus_bkg --limit 1000 --workers-cpu 8 --retention keep
python -m skysift calibrate-report data/calibration/kbonus_bkg.jsonl
```

For each of the `--limit` highest-priority targets (the order `run` uses) the light curve is detrended and
pre-whitened exactly as in a sweep, then searched three times:

* `real`: the flux as it is;
* `inverted`: `2 - f`, so dips become bumps while systematics and stellar variability stay. The detector clips
  upward outliers only (5 MAD), so the former dips are removed while former flares and upward ramps become dips:
  this row is a "systematics as dips" null;
* `scrambled`: the flux permuted within each quarter (seed 1), which leaves noise only.

The flux preparation is the sweep's own code (`pipeline.whitened_flux`), so the calibrated flux cannot drift from the
swept one. The detector is BoxDip without its masked second pass and alias resolution, so the numbers are the raw
first-pass statistics that rung 1 thresholds. The period window is the production one from `thresholds`
(`min_period_days` and `max_period_days`, 0.5 to 200 d today); `--min-period` and `--max-period` override it. The
threshold must be calibrated at the window you sweep with: a wider window holds more trial periods and so higher noise
peaks. The search version records the window, so files from different windows are never pooled by accident.
`--nulls inverted` (or `scrambled`) searches one null only. Nothing is vetted and nothing is written to the results
tables. Each target becomes one JSON line in `<data_dir>/calibration/<source>.jsonl` (or `--out`): `target_id`,
`search_version`, `n_points`, the top three SDEs of each search, `real_top` (period, duration, depth, SDE and snr of the
strongest real signal) and `seconds`.

A line is written and flushed as soon as its target finishes, so Ctrl+C loses only the targets being searched at that
moment, and the same command resumes by skipping every target already in the file. A raw file still in the blob is
reused and left where it is; anything else is downloaded and recorded as in a sweep, and the retention mode then
applies to it. With the default `stream` a downloaded file is deleted once loaded, so every recalibration would download
the curves again. `--retention keep` is the pilot setting: it keeps the files (1000 KBonus curves take about 9 GB), so
the next calibration, after a change to the detrend or the detector, costs CPU only. Under a budget, the files of
targets still being searched are never evicted. The file belongs to one search version (detector, period window,
detrend and pre-whitening settings): after a change, `calibrate` refuses to append and asks for a new `--out`.
`skysift run` prints a one-line warning before a sweep when `<data_dir>/calibration/<source>.jsonl` is missing or was
written by another search version than this sweep's settings need.

Each target costs the pre-whitening plus three searches of a 1400 d curve. Measured on one KBonus curve
(gaia-dr3-2076514437343928064, 17 October 2026): at the production window of 0.5 to 200 d the preparation takes 4 s
and one search 196 s, so a target takes about 10 CPU-minutes. At 0.5 to 30 d the whole target took 264 s. The
1000-star pilot at the production window therefore takes about 20 to 21 hours on eight workers (about 9 hours at 0.5
to 30 d), and an interrupted run continues where it stopped.

`calibrate-report` prints one row per kind: the number of curves that gave a peak, the median and maximum top SDE,
and how many top SDEs sit at or above 7, 8, 9, 10, 11, 12 and 15 (at or above, as rung 1 passes `sde >= sde_min`).
The last line gives the fraction of real curves at or above the configured `sde_min`. It refuses a file that mixes
search versions unless `--all-versions` is given.

The rule used on 17 October 2026: `sde_min` sits above the maximum scrambled-null top SDE of a few hundred curves,
with margin. The scrambled row is pure noise, so its tail is the false-alarm rate on a quiet star. The inverted row
keeps systematics and variability; its tail shows what pre-whitening and the later rungs must still reject. On 30
curves the scrambled maximum was 10.1, hence the interim value of 11.

The rule used on 21 October 2026, on the 999-curve pilot at the production window (search version 5+42061727):
`sde_min` is the lowest level at which the scrambled null gives at most 1 percent of curves. The 1 percent per-star
level is SkySift's own rule, set for the ladder behind rung 1 (Hippke and Heller 2019 recommend SDE 9 for TLS, where
white noise gives about 0.01 percent; real Kepler curves give far more at every level, which is why the null is
measured on them). The scrambled row gave 18 of 999 at 11 and 8 of 999 at 12 (3 at 15), so the default is 12. The cost in completeness, from the injection set
(`inject_recovery.jsonl`, 30 injections per SNR): 2 of 30 recoveries at SNR 10 and 1 of 30 at SNR 15 sit between 11
and 12. The inverted row hardly falls with the level (114 at 11, 96 at 12, 65 at 15): its tail is strong systematic
features (inverted flares and ramps) that no threshold removes and rungs 2 to 5 must reject. `sde_min` is not part
of the search version, so changing it keeps the calibration file and the stored signals valid; a stage 1 signal below
the new value fails rung 1 at its next recheck. The false-alarm probability on a dossier counts both null kinds
together (the conservative reading) and shows the split, so a candidate whose only null hits are inverted curves can
be read either way.

## Production sweep checklist

A production sweep is a `skysift run` over a whole source at the production window. Before starting one, every line
below holds; each is a measurement, not a feeling.

1. **Suite green on the exact tree.** `python -m pytest tests/unit -q -p no:cacheprovider` passes on the commit the
   sweep will run (about 30 minutes). The golden tests (`tests/golden`) pass too when the detector or the detrend
   changed.
2. **Calibration matches the search version.** `<data_dir>/calibration/<source>.jsonl` was written by the same search
   version `skysift run` reports (`skysift run` warns otherwise). `calibrate-report` shows the scrambled-null maximum
   below `thresholds.sde_min` with margin and gives the real-curve fraction at or above it; a change to the detector,
   detrend, pre-whitening or thresholds after the calibration means a new calibration before the sweep.
3. **Completeness is known.** The injection-recovery study (`docs/superpowers/notes/scripts/inject_recovery.py`, run on
   a few dozen cached curves of the source, then `inject_rednoise.py`) states completeness against red-noise SNR; on
   18 October 2026 it was about 95 percent above red-noise SNR 9 and independent of period, with the box depth about
   10 percent low (use the stacked-fold depth of rung 2 for radii). Re-run it after any change to the search.
   `skysift inject --source <name> --limit <N>` repeats the study as a standing regression: it injects box transits into the cached curves, runs the production search and rungs 1 to 3, and appends one line per injection to a JSON-lines file (resumable; every line carries the search and ladder versions). `skysift inject-report <file>` prints completeness against nominal and red-noise SNR and against period, with the recovered-to-injected depth ratio. Run both before publishing: a ladder or search change that lowers completeness, or a ladder version whose rungs 1 to 3 reject more injections than the previous one, is a regression to explain first.
4. **Known objects are matched.** Rung 4 answers on one live target (a KOI host of the source) before the sweep, so a
   renamed archive column cannot turn every verdict UNDETERMINED (outage).
5. **The review queue starts clean.** The dashboard's Candidates page shows the current search version only by
   default; older signals stay reachable through "show all". Decisions are append-only, so nothing from earlier sweeps
   is lost.
6. **Capacity and retention.** A 4-year KBonus curve costs about 250 CPU-seconds at 0.5 to 200 d (plus one more
   search on stars with a masked second pass or a harmonic restore); choose `--workers-cpu` and `--retention` for the
   machine and disk at hand, and keep the Live page open: it shows run time per target, the pool state and the
   stop control.

## Development setup

```bash
python -m venv .venv
pip install -e ".[dev]"
python -m pytest -m "not golden and not slow" -q
ruff check src tests
```

Configuration lives in `config/default.yaml`; any value can be overridden with a `SKYSIFT_` environment
variable (nested keys use `__`, e.g. `SKYSIFT_THRESHOLDS__SDE_MIN=11`). See `.env.example`.

## Configuration

Defaults come from `config/default.yaml`; any value can be overridden by an environment variable with the `SKYSIFT_`
prefix (nested keys use `__`). Optional extras: `s3` (boto3, for an `s3://` blob store) and `pg` (psycopg, for a
`postgresql` database URL), for example `pip install -e ".[s3,pg]"`.

| Setting | Environment variable | Default | Meaning |
| --- | --- | --- | --- |
| `data_dir` | `SKYSIFT_DATA_DIR` | `./data` | Working directory for local data |
| `blob_url` | `SKYSIFT_BLOB_URL` | `file://./data/blobs` | Blob store (`file://` or `s3://bucket/prefix`) |
| `database_url` | `SKYSIFT_DATABASE_URL` | `sqlite:///./data/engine.db` | SQLAlchemy URL for the results database |
| `workers_io` | `SKYSIFT_WORKERS_IO` | `8` | Download workers |
| `workers_cpu` | `SKYSIFT_WORKERS_CPU` | `2` | Search and vetting workers |
| `retention` | `SKYSIFT_RETENTION` | `stream` | Light-curve retention mode (see below) |
| `thresholds.*` | `SKYSIFT_THRESHOLDS__SDE_MIN` etc. | see `config/default.yaml` | Detection and vetting thresholds |
| (environment only) | `SKYSIFT_ZENODO_TOKEN` | unset | Zenodo personal access token for `skysift pack --zenodo` |

## Retention modes

* `stream`: delete each light-curve file once its target is vetted; evidence for kept signals is pinned.
* `keep`: keep every downloaded file.
* `budget:<GiB>` (for example `budget:50`): keep files until the cache exceeds the budget, then evict the least recently used
  unpinned ones; the run stops with exit code 3 if only pinned evidence (or files the OS would not let it delete) is left and the budget is still exceeded.

## Docker and AWS

`docker/Dockerfile` builds an image whose entry point is `python -m skysift`; `docker/docker-compose.yml` runs a
100-target sweep with `./data` mounted at `/data`. For AWS Batch, S3 storage and IAM permissions see
`deploy/aws/README.md`. On Windows, `deploy/windows/register-task.ps1` registers a nightly scheduled task that runs
`.venv\Scripts\python.exe -m skysift run ...`.

```bash
docker build -f docker/Dockerfile -t skysift:dev .
docker run --rm -v skysift-data:/data skysift:dev sources
```

The image carries the stored extinction records of `docs/candidates` as they were at build time, and not `tools/`, so
a new candidate needs its record from `tools/extinction.py`, run outside the image, and a rebuild before it can get a
dossier.

## Golden tests

The golden tests run the period search and vetting rungs 1 to 3 on real data: a published signal (KIC 5689121,
period 13.77 d) and a quiet neighbour. They download two Kepler light curves from MAST on first use (cached in `tests/fixtures/cache/`) and
skip when offline. The full period search takes several minutes.

```bash
python -m pytest tests/golden -v -m golden
```

The ordinary suite excludes them: `python -m pytest -m "not golden and not slow" -q`.

## Licence and citation

MIT licence, see `LICENSE`. If you use SkySift, please cite it using `CITATION.cff`; the v0.1.0 release is archived
at [doi:10.5281/zenodo.23717586](https://doi.org/10.5281/zenodo.23717586).
