# SkySift

Archival time-series search and vetting engine. See docs/superpowers/specs/.


## Quick start

```bash
python -m venv .venv
pip install -e ".[dev]"
python -m pytest -m "not golden and not slow" -q
ruff check src tests
```

Configuration lives in `config/default.yaml`; any value can be overridden with a `SKYSIFT_` environment
variable (nested keys use `__`, e.g. `SKYSIFT_THRESHOLDS__SDE_MIN=11`). See `.env.example`.

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
