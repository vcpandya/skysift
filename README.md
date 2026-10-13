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
python -m skysift run --source kbonus_bkg --limit 100 --min-period 0.2 --max-period 30
python -m skysift status
python -m skysift vet --source kbonus_bkg --target-id <ID>
```

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
  unpinned ones; the run stops if only pinned evidence is left and the budget is still exceeded.

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

The golden tests run the full pipeline on a real, published signal (KIC 5689121, period 13.77 d) and a
quiet neighbour. They download two Kepler light curves from MAST on first use (cached in `tests/fixtures/cache/`) and
skip when offline. The full period search takes several minutes.

```bash
python -m pytest tests/golden -v -m golden
```

The ordinary suite excludes them: `python -m pytest -m "not golden and not slow" -q`.

## Licence and citation

MIT licence, see `LICENSE`. If you use SkySift, please cite it using `CITATION.cff`.
