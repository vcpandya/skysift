# SkySift

Archival time-series search and vetting engine. See docs/superpowers/specs/.

## Purpose

SkySift searches archival Kepler and TESS light curves for transit, eclipse and lensing candidates. A sweep fetches each
target's light curve, runs a detector, pushes every signal through a ladder of vetting tests, and stores the outcome, so
that only the targets that survive need follow-up. Milestone 1 ships the transit box-dip detector and vetting ladder
rungs 1 to 4; the eclipsing-binary and lensing branches are planned.

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

A sweep resumes where it stopped. Each target's job key holds the detector version plus a hash of the period window
and search grid, so a run with a different `--min-period` or `--max-period` searches the same targets again. A target
whose job fails three times is not retried; `run` counts it as `gave_up` and `status` reports it under
`error_capped_targets`. A failure caused by a service outage (MAST or a catalogue unreachable) is recorded but never counted as one of the three attempts.

## Dashboard

```bash
python -m skysift ui
```

The dashboard is a local review tool, not a public service. Start it on the machine that holds the data directory.
It opens http://127.0.0.1:8765/ in the browser. It binds to the loopback interface only and has no authentication.
The `ui` command refuses any other `--host`. The one exception is a container, where `SKYSIFT_UI_ALLOW_CONTAINER=1`
allows `0.0.0.0`. To reach it from another machine, use an SSH tunnel
(`ssh -L 8765:127.0.0.1:8765 <host>`). Each page carries a per-process CSRF token, and every POST must send it back.

Pages:

* **Sources**: the registry with yield, CPU hours and GB per 10,000 stars, and progress per live source. It also holds
  the Start sweep form (limit, period window, retention, workers, periodogram storage). The sweep runs as a child
  `python -m skysift run` process. Only one sweep runs per data directory at a time, and it can only be launched from
  this page.
* **Live**: targets per state, signal outcomes, rung verdicts, throughput, cache against budget, and the running
  sweep's log. The page refreshes while a sweep runs. A Stop button ends the sweep.
* **Candidates**: the review queue sorted by SDE. A signal's page shows the ladder with the metrics that matter per
  rung. It also shows the periodogram (per-band SDE envelope with threshold, aliases and systematic periods), the
  detrended curve, the phase fold with the box model, odd against even, the phase 0.5 window and per-season depths.
  Approve, Reject (a note is required) and Needs data append a row to the `decisions` table. Decisions are append-only,
  so nothing is deleted and the full history stays visible. Keys: `a`, `r`, `n`, `j` for next, `k` for previous.
* **Approved**: current decisions by status. Each signal has a `review.json` download with the signal, every test,
  every query, the artifacts and the decision history.

HTMX is vendored in the package. Plotly is downloaded once on the first start into `<data_dir>/ui/assets/` and its
SHA-256 is verified. Afterwards the dashboard works offline. If Plotly cannot be fetched, the plots are disabled and
the page shows a notice.

Every sweep stores a periodogram summary per target (the SDE envelope over 2,048 log-period bins, about 8 kB) unless
told otherwise. `skysift run --periodogram all|signals|off` sets this for one sweep. `SKYSIFT_STORE_PERIODOGRAM`
(`all`, `signals` or `off`, default `all`) sets the default. The envelopes of targets without a signal form the null
set for calibrating `sde_min`. `skysift vet --spectrum` also writes the raw BLS spectrum for one target.

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

## Golden tests

The golden tests run the period search and vetting rungs 1 to 3 on real data: a published signal (KIC 5689121,
period 13.77 d) and a quiet neighbour. They download two Kepler light curves from MAST on first use (cached in `tests/fixtures/cache/`) and
skip when offline. The full period search takes several minutes.

```bash
python -m pytest tests/golden -v -m golden
```

The ordinary suite excludes them: `python -m pytest -m "not golden and not slow" -q`.

## Licence and citation

MIT licence, see `LICENSE`. If you use SkySift, please cite it using `CITATION.cff`.
