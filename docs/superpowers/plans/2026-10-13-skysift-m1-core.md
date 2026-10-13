# SkySift Milestone 1 (Core) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A runnable `skysift` CLI that sweeps KBonus-BKG light curves with a box-dip detector, vets every signal through rungs 1 to 4 of the ladder, stores every result in SQLite, deletes raw files as it goes (stream retention), and reproduces the KIC 5689121 light-curve result as a golden test.

**Architecture:** Python package in src layout. Typed records (pydantic) flow between five layers: sources (adapters), detectors (plugins), vetting (cost-ordered ladder), store (SQLAlchemy + blob cache + retention), cli. A pipeline module wires them with an I/O thread pool feeding a CPU process pool, checkpointing every rung. Everything is configured by one YAML plus `SKYSIFT_` environment variables so the same code runs on Windows, in Docker and on AWS Batch.

**Tech Stack:** Python 3.12, numpy, scipy, astropy (BoxLeastSquares, FITS), pydantic v2 + pydantic-settings, SQLAlchemy 2.x, typer, requests + truststore, PyYAML, rich; dev: pytest, ruff; optional: boto3.

**Spec:** `docs/superpowers/specs/2026-10-13-skysift-design.md` (this plan covers spec section 12, milestone M1, plus the repo scaffold from section 8; M2 to M5 get their own plans).

## Global Constraints

- Python >= 3.12; src layout; package name `skysift`; CLI entry point `skysift`.
- Configuration: `config/default.yaml` overridden by environment variables with prefix `SKYSIFT_`. Secrets only from environment variables. No local paths baked into code.
- TLS through the system trust store: `truststore.inject_into_ssl()` before any HTTP call.
- Default retention is `stream`. Retention values: `keep`, `stream`, `budget:<GB>`.
- Thresholds (spec section 5): SDE >= 9, SNR >= 7, >= 3 events, duration between 0.3 and 3 times the circular expectation; odd/even difference < 3 sigma; secondary at phase 0.5 < 3 sigma; free-phase secondary flagged above 5 sigma; season depths consistent within 3 sigma.
- An archive outage or malformed response yields `undetermined`, never `pass`.
- Every HTTP request is logged to the `queries` table with URL, params, UTC, status, bytes, SHA-256.
- Job key = `(target_id, detector_name, detector_version, ladder_version)`; reruns with the same key are skipped.
- Line endings LF (`.gitattributes`), MIT licence, no data, caches, packs, databases or `.env` tracked.
- Commits end with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.

## Review Focus

1. A light curve with fewer than 500 good points, or a quarter that is entirely NaN, must produce no signals and no crash (test in Task 7).
2. Periods within 1 percent of Kepler systematics (quarter length ~93 d, orbit 372.5 d, and their halves) must be flagged by rung 1 as `undetermined` with reason `systematic_alias`, not passed (test in Task 9).
3. Zero or non-finite flux errors must be masked out by `LightCurve.good_mask()` rather than producing infinite weights (test in Task 2).
4. An archive that returns HTTP 200 with an error document (the IRSA "invalid TAP query" VOTable) must be recorded as `undetermined`, not as "no known object" (test in Task 10).
5. A run interrupted after a signal row was written but before its tests finished must resume without duplicating the signal row (test in Task 12).
6. When the disk budget is reached and every cached file is pinned, the run must stop with a clear message instead of deleting pinned evidence or filling the disk (test in Task 4).

---

## File structure

```
pyproject.toml
.gitattributes  .gitignore  .env.example  LICENSE  CITATION.cff  README.md
.github/workflows/ci.yml
config/default.yaml
config/sources.yaml
src/skysift/__init__.py            version
src/skysift/config.py              Settings, load_settings()
src/skysift/models.py              Target, LightCurve, Signal, TestStatus, TestResult
src/skysift/net.py                 fetch() with retries, trust store, query logging
src/skysift/store/__init__.py
src/skysift/store/db.py            SQLAlchemy models + Store
src/skysift/store/blob.py          Blob, LocalBlob, S3Blob, blob_from_url()
src/skysift/store/retention.py     RetentionPolicy, Retention
src/skysift/lc/__init__.py
src/skysift/lc/kbonus.py           parse_kbonus_fits()
src/skysift/lc/ops.py              detrend, fold, event_depths, secondary_search
src/skysift/detectors/__init__.py
src/skysift/detectors/base.py      Detector protocol, registry
src/skysift/detectors/box_dip.py   BoxDip
src/skysift/vetting/__init__.py
src/skysift/vetting/base.py        Rung protocol, DataBundle, Ladder
src/skysift/vetting/rung1_detection.py
src/skysift/vetting/rung2_shape.py
src/skysift/vetting/rung3_stability.py
src/skysift/vetting/rung4_novelty.py
src/skysift/sources/__init__.py
src/skysift/sources/base.py        Source protocol, SourceInfo, load_registry()
src/skysift/sources/kbonus_bkg.py  KBonusBkg
src/skysift/pipeline.py            priority, job keys, run()
src/skysift/cli.py                 typer app
tests/conftest.py                  tmp settings, tmp store
tests/synthetic.py                 make_lightcurve(), inject_transit()
tests/unit/test_*.py               one per module
tests/golden/test_kic5689121_lc.py
docker/Dockerfile  docker/docker-compose.yml
deploy/windows/register-task.ps1
deploy/aws/README.md  deploy/aws/batch-job-definition.json
```

---

### Task 1: Repository scaffold and configuration

**Files:**
- Create: `pyproject.toml`, `.gitattributes`, `.env.example`, `LICENSE`, `CITATION.cff`, `.github/workflows/ci.yml`, `config/default.yaml`, `src/skysift/__init__.py`, `src/skysift/config.py`
- Modify: `README.md`, `.gitignore`
- Test: `tests/unit/test_config.py`, `tests/conftest.py`

**Interfaces:**
- Produces: `skysift.config.Settings` (pydantic-settings model) with fields `data_dir: Path`, `blob_url: str`, `database_url: str`, `workers_io: int`, `workers_cpu: int`, `retention: str`, `thresholds: Thresholds`; `skysift.config.load_settings(yaml_path: Path | None = None) -> Settings`; `Thresholds` fields `sde_min, snr_min, min_events, duration_ratio_min, duration_ratio_max, parity_sigma, secondary_sigma, free_secondary_sigma, season_sigma, max_period_days, min_period_days`.

- [ ] **Step 1: Write the failing test**

`tests/unit/test_config.py`:
```python
from pathlib import Path
from skysift.config import load_settings


def test_defaults_load_from_yaml(tmp_path, monkeypatch):
    monkeypatch.delenv("SKYSIFT_RETENTION", raising=False)
    s = load_settings()
    assert s.retention == "stream"
    assert s.thresholds.sde_min == 9.0
    assert s.thresholds.min_events == 3


def test_env_overrides_yaml(monkeypatch, tmp_path):
    monkeypatch.setenv("SKYSIFT_RETENTION", "budget:50")
    monkeypatch.setenv("SKYSIFT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SKYSIFT_THRESHOLDS__SDE_MIN", "11")
    s = load_settings()
    assert s.retention == "budget:50"
    assert s.data_dir == tmp_path
    assert s.thresholds.sde_min == 11.0
```

`tests/conftest.py`:
```python
import pytest
from skysift.config import load_settings


@pytest.fixture
def settings(tmp_path, monkeypatch):
    monkeypatch.setenv("SKYSIFT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SKYSIFT_BLOB_URL", f"file://{tmp_path.as_posix()}/blobs")
    monkeypatch.setenv("SKYSIFT_DATABASE_URL", f"sqlite:///{tmp_path.as_posix()}/engine.db")
    return load_settings()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'skysift'`

- [ ] **Step 3: Write the scaffold**

`pyproject.toml`:
```toml
[build-system]
requires = ["setuptools>=69", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "skysift"
version = "0.1.0"
description = "Archival time-series search and vetting engine for transit, eclipse and lensing candidates"
readme = "README.md"
requires-python = ">=3.12"
license = {text = "MIT"}
dependencies = [
  "numpy>=2.0", "scipy>=1.13", "astropy>=6.1", "pydantic>=2.7", "pydantic-settings>=2.3",
  "SQLAlchemy>=2.0", "typer>=0.12", "requests>=2.32", "truststore>=0.9", "PyYAML>=6.0", "rich>=13.7",
]

[project.optional-dependencies]
s3 = ["boto3>=1.34"]
dev = ["pytest>=8.2", "pytest-cov>=5.0", "ruff>=0.5"]

[project.scripts]
skysift = "skysift.cli:app"

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
testpaths = ["tests"]
markers = ["golden: needs network to fetch cached fixtures", "slow: long running"]

[tool.ruff]
line-length = 120
target-version = "py312"
```

`src/skysift/__init__.py`:
```python
__version__ = "0.1.0"
```

`config/default.yaml`:
```yaml
data_dir: ./data
blob_url: file://./data/blobs
database_url: sqlite:///./data/engine.db
workers_io: 8
workers_cpu: 2
retention: stream
thresholds:
  sde_min: 9.0
  snr_min: 7.0
  min_events: 3
  duration_ratio_min: 0.3
  duration_ratio_max: 3.0
  parity_sigma: 3.0
  secondary_sigma: 3.0
  free_secondary_sigma: 5.0
  season_sigma: 3.0
  min_period_days: 0.5
  max_period_days: 200.0
```

`src/skysift/config.py`:
```python
from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_YAML = Path(__file__).resolve().parents[2] / "config" / "default.yaml"


class Thresholds(BaseModel):
    sde_min: float = 9.0
    snr_min: float = 7.0
    min_events: int = 3
    duration_ratio_min: float = 0.3
    duration_ratio_max: float = 3.0
    parity_sigma: float = 3.0
    secondary_sigma: float = 3.0
    free_secondary_sigma: float = 5.0
    season_sigma: float = 3.0
    min_period_days: float = 0.5
    max_period_days: float = 200.0


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SKYSIFT_", env_nested_delimiter="__", extra="ignore")

    data_dir: Path = Path("./data")
    blob_url: str = "file://./data/blobs"
    database_url: str = "sqlite:///./data/engine.db"
    workers_io: int = 8
    workers_cpu: int = 2
    retention: str = "stream"
    thresholds: Thresholds = Thresholds()


def load_settings(yaml_path: Path | None = None) -> Settings:
    """YAML gives defaults; environment variables (SKYSIFT_*) override them."""
    path = yaml_path or DEFAULT_YAML
    base = yaml.safe_load(path.read_text()) if path.exists() else {}
    env_only = Settings()  # reads environment
    merged = {**base, **env_only.model_dump(exclude_unset=True)}
    if "thresholds" in base and "thresholds" in env_only.model_dump(exclude_unset=True):
        merged["thresholds"] = {**base["thresholds"], **env_only.model_dump(exclude_unset=True)["thresholds"]}
    return Settings(**merged)
```

`.gitattributes`: `* text=auto eol=lf`

`.env.example`:
```
SKYSIFT_DATA_DIR=./data
SKYSIFT_BLOB_URL=file://./data/blobs
SKYSIFT_DATABASE_URL=sqlite:///./data/engine.db
SKYSIFT_RETENTION=stream
# ZENODO_TOKEN=   (milestone 4)
# AWS_ACCESS_KEY_ID= / AWS_SECRET_ACCESS_KEY=  (only when SKYSIFT_BLOB_URL is s3://)
```

`LICENSE`: MIT text with "Copyright (c) 2026 Vikram Pandya".

`CITATION.cff`:
```yaml
cff-version: 1.2.0
title: SkySift
message: If you use this software, please cite it.
authors:
  - family-names: Pandya
    given-names: Vikram
version: 0.1.0
date-released: 2026-10-13
license: MIT
```

`.github/workflows/ci.yml`:
```yaml
name: ci
on: [push, pull_request]
jobs:
  test:
    strategy:
      matrix:
        os: [ubuntu-latest, windows-latest]
    runs-on: ${{ matrix.os }}
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: {python-version: "3.12"}
      - run: pip install -e ".[dev]"
      - run: ruff check src tests
      - run: python -m pytest -m "not golden and not slow" -q
```

Append to `.gitignore`: `data/`, `*.db`, `.env`, `tests/fixtures/cache/`, `packs/`, `.pytest_cache/`, `.ruff_cache/`, `dist/`, `build/`, `*.egg-info/`.

- [ ] **Step 4: Install and run the test**

Run: `pip install -e ".[dev]"` then `python -m pytest tests/unit/test_config.py -v`
Expected: 2 PASS

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat: scaffold package, settings and CI

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: Core models

**Files:**
- Create: `src/skysift/models.py`
- Test: `tests/unit/test_models.py`

**Interfaces:**
- Produces: `Target(source: str, target_id: str, ra: float, dec: float, gaia_id: str | None, kic: int | None, gmag: float | None, meta: dict)`; `LightCurve(time: np.ndarray, flux: np.ndarray, flux_err: np.ndarray, quality: np.ndarray, quarter: np.ndarray, flux_alt: np.ndarray | None, meta: dict)` with `good_mask() -> np.ndarray[bool]`, `cleaned() -> LightCurve`, `n_good -> int`; `Signal(target_id, detector, detector_version, period, t0, duration, depth_ppm, snr, sde, n_events, extra: dict)`; `TestStatus` enum `PASS, FAIL, UNDETERMINED`; `TestResult(rung: str, status: TestStatus, metrics: dict, artifacts: list[str], reason: str)`.

- [ ] **Step 1: Write the failing test**

```python
import numpy as np
from skysift.models import LightCurve, TestStatus, TestResult


def test_good_mask_drops_nonfinite_zero_err_and_bad_quality():
    t = np.arange(10, dtype=float)
    f = np.ones(10); f[2] = np.nan
    e = np.full(10, 1e-3); e[3] = 0.0; e[4] = np.inf
    q = np.zeros(10, dtype=int); q[5] = 16
    lc = LightCurve(time=t, flux=f, flux_err=e, quality=q, quarter=np.zeros(10, dtype=int))
    m = lc.good_mask()
    assert m.sum() == 6 and not m[2] and not m[3] and not m[4] and not m[5]
    assert lc.cleaned().n_good == 6


def test_testresult_defaults():
    r = TestResult(rung="r1", status=TestStatus.UNDETERMINED, reason="outage")
    assert r.metrics == {} and r.artifacts == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_models.py -v` — Expected: FAIL, `No module named 'skysift.models'`

- [ ] **Step 3: Implement**

```python
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

import numpy as np
from pydantic import BaseModel, Field


class Target(BaseModel):
    source: str
    target_id: str
    ra: float
    dec: float
    gaia_id: str | None = None
    kic: int | None = None
    gmag: float | None = None
    meta: dict = Field(default_factory=dict)


@dataclass
class LightCurve:
    time: np.ndarray          # BKJD or BTJD days
    flux: np.ndarray          # primary flux (PSF for KBonus)
    flux_err: np.ndarray
    quality: np.ndarray       # 0 = good
    quarter: np.ndarray       # quarter / sector id per cadence
    flux_alt: np.ndarray | None = None   # secondary flux (SAP for KBonus)
    meta: dict = field(default_factory=dict)

    def good_mask(self) -> np.ndarray:
        m = np.isfinite(self.time) & np.isfinite(self.flux) & np.isfinite(self.flux_err)
        m &= self.flux_err > 0
        m &= self.quality == 0
        return m

    def cleaned(self) -> "LightCurve":
        m = self.good_mask()
        return LightCurve(self.time[m], self.flux[m], self.flux_err[m], self.quality[m], self.quarter[m],
                          None if self.flux_alt is None else self.flux_alt[m], dict(self.meta))

    @property
    def n_good(self) -> int:
        return int(self.good_mask().sum())


class Signal(BaseModel):
    target_id: str
    detector: str
    detector_version: str
    period: float
    t0: float
    duration: float          # days
    depth_ppm: float
    snr: float
    sde: float
    n_events: int
    extra: dict = Field(default_factory=dict)


class TestStatus(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    UNDETERMINED = "undetermined"


class TestResult(BaseModel):
    rung: str
    status: TestStatus
    metrics: dict = Field(default_factory=dict)
    artifacts: list[str] = Field(default_factory=list)
    reason: str = ""
```

- [ ] **Step 4: Run tests** — `python -m pytest tests/unit/test_models.py -v` — Expected: 2 PASS

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat: core models" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"`

---

### Task 3: Store (SQLAlchemy)

**Files:**
- Create: `src/skysift/store/__init__.py`, `src/skysift/store/db.py`
- Test: `tests/unit/test_store.py`

**Interfaces:**
- Produces: `Store(database_url: str)` with `init_schema()`, `session()` context manager, `upsert_target(Target) -> None`, `set_target_state(target_id, state: str)`, `job_done(job_key: str) -> bool`, `start_run(job_key, target_id, detector, detector_version, ladder_version) -> int`, `finish_run(run_id, status: str, error: str = "")`, `add_signal(Signal, run_id) -> int` (returns existing id if same target/detector/period within 1e-4 already exists for that run), `add_test(signal_id, TestResult, duration_s: float)`, `add_artifact(sha256, nbytes, url, local_path, target_id, pinned=False)`, `mark_artifact_deleted(sha256)`, `unpinned_artifacts(target_id=None) -> list[ArtifactRow]`, `pin_target_artifacts(target_id)`, `log_query(service, url, params: dict, status: int | None, nbytes: int, sha256: str | None, error: str = "")`, `counts() -> dict`.
- Job key helper: `make_job_key(target_id, detector, detector_version, ladder_version) -> str` = `f"{target_id}|{detector}|{detector_version}|{ladder_version}"`.

- [ ] **Step 1: Write the failing test**

```python
from skysift.models import Target, Signal, TestResult, TestStatus
from skysift.store.db import Store, make_job_key


def make_store(tmp_path):
    s = Store(f"sqlite:///{tmp_path.as_posix()}/t.db"); s.init_schema(); return s


def test_run_and_signal_roundtrip(tmp_path):
    s = make_store(tmp_path)
    s.upsert_target(Target(source="kbonus_bkg", target_id="kic-005689121", ra=285.9, dec=40.9))
    key = make_job_key("kic-005689121", "box_dip", "1", "1")
    assert not s.job_done(key)
    rid = s.start_run(key, "kic-005689121", "box_dip", "1", "1")
    sig = Signal(target_id="kic-005689121", detector="box_dip", detector_version="1", period=13.77, t0=136.3,
                 duration=0.09, depth_ppm=1883, snr=18, sde=14, n_events=69)
    sid1 = s.add_signal(sig, rid); sid2 = s.add_signal(sig, rid)
    assert sid1 == sid2  # idempotent
    s.add_test(sid1, TestResult(rung="rung1", status=TestStatus.PASS, metrics={"sde": 14}), 0.01)
    s.finish_run(rid, "done")
    assert s.job_done(key)
    c = s.counts(); assert c["signals"] == 1 and c["tests"] == 1


def test_artifacts_and_queries(tmp_path):
    s = make_store(tmp_path)
    s.add_artifact("ab" * 32, 10, "https://x/y.fits", "/tmp/y.fits", "kic-1")
    assert len(s.unpinned_artifacts("kic-1")) == 1
    s.pin_target_artifacts("kic-1")
    assert s.unpinned_artifacts("kic-1") == []
    s.log_query("mast", "https://x", {"a": 1}, 200, 10, "ab" * 32)
    assert s.counts()["queries"] == 1
```

- [ ] **Step 2: Run to verify it fails** — Expected: `No module named 'skysift.store'`

- [ ] **Step 3: Implement `store/db.py`**

```python
from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, create_engine, select, func
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from skysift.models import Signal, Target, TestResult


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def make_job_key(target_id: str, detector: str, detector_version: str, ladder_version: str) -> str:
    return f"{target_id}|{detector}|{detector_version}|{ladder_version}"


class Base(DeclarativeBase):
    pass


class TargetRow(Base):
    __tablename__ = "targets"
    target_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    source: Mapped[str] = mapped_column(String(32), index=True)
    ra: Mapped[float] = mapped_column(Float); dec: Mapped[float] = mapped_column(Float)
    gaia_id: Mapped[str | None] = mapped_column(String(32)); kic: Mapped[int | None] = mapped_column(Integer)
    gmag: Mapped[float | None] = mapped_column(Float)
    priority: Mapped[float] = mapped_column(Float, default=0.0)
    state: Mapped[str] = mapped_column(String(16), default="new", index=True)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)


class RunRow(Base):
    __tablename__ = "runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_key: Mapped[str] = mapped_column(String(160), index=True)
    target_id: Mapped[str] = mapped_column(String(64), index=True)
    detector: Mapped[str] = mapped_column(String(32)); detector_version: Mapped[str] = mapped_column(String(16))
    ladder_version: Mapped[str] = mapped_column(String(16))
    started: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished: Mapped[datetime | None] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(16), default="running")   # running|done|error
    error: Mapped[str] = mapped_column(Text, default="")


class SignalRow(Base):
    __tablename__ = "signals"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id"), index=True)
    target_id: Mapped[str] = mapped_column(String(64), index=True)
    detector: Mapped[str] = mapped_column(String(32)); detector_version: Mapped[str] = mapped_column(String(16))
    period: Mapped[float] = mapped_column(Float); t0: Mapped[float] = mapped_column(Float)
    duration: Mapped[float] = mapped_column(Float); depth_ppm: Mapped[float] = mapped_column(Float)
    snr: Mapped[float] = mapped_column(Float); sde: Mapped[float] = mapped_column(Float)
    n_events: Mapped[int] = mapped_column(Integer)
    extra: Mapped[dict] = mapped_column(JSON, default=dict)
    branch: Mapped[str] = mapped_column(String(16), default="planet")
    final_status: Mapped[str] = mapped_column(String(16), default="pending")   # pending|rejected|candidate


class TestRow(Base):
    __tablename__ = "tests"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    signal_id: Mapped[int] = mapped_column(ForeignKey("signals.id"), index=True)
    rung: Mapped[str] = mapped_column(String(32)); status: Mapped[str] = mapped_column(String(16))
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    artifacts: Mapped[list] = mapped_column(JSON, default=list)
    reason: Mapped[str] = mapped_column(Text, default="")
    duration_s: Mapped[float] = mapped_column(Float, default=0.0)
    created: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class CandidateRow(Base):
    __tablename__ = "candidates"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    signal_id: Mapped[int] = mapped_column(ForeignKey("signals.id"), unique=True)
    branch: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), default="review")   # review|approved|rejected|published
    pack_path: Mapped[str] = mapped_column(Text, default=""); doi: Mapped[str] = mapped_column(String(64), default="")
    note: Mapped[str] = mapped_column(Text, default="")


class ArtifactRow(Base):
    __tablename__ = "artifacts"
    sha256: Mapped[str] = mapped_column(String(64), primary_key=True)
    nbytes: Mapped[int] = mapped_column(Integer); url: Mapped[str] = mapped_column(Text)
    local_path: Mapped[str] = mapped_column(Text); target_id: Mapped[str | None] = mapped_column(String(64), index=True)
    pinned: Mapped[bool] = mapped_column(Boolean, default=False)
    created: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_used: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime)


class QueryRow(Base):
    __tablename__ = "queries"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    service: Mapped[str] = mapped_column(String(32)); url: Mapped[str] = mapped_column(Text)
    params: Mapped[str] = mapped_column(Text); utc: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    status: Mapped[int | None] = mapped_column(Integer); nbytes: Mapped[int] = mapped_column(Integer, default=0)
    sha256: Mapped[str | None] = mapped_column(String(64)); error: Mapped[str] = mapped_column(Text, default="")


class Store:
    def __init__(self, database_url: str):
        self.engine = create_engine(database_url, future=True)

    def init_schema(self) -> None:
        Base.metadata.create_all(self.engine)

    @contextmanager
    def session(self):
        with Session(self.engine) as s, s.begin():
            yield s

    def upsert_target(self, t: Target) -> None:
        with self.session() as s:
            row = s.get(TargetRow, t.target_id)
            if row is None:
                s.add(TargetRow(target_id=t.target_id, source=t.source, ra=t.ra, dec=t.dec, gaia_id=t.gaia_id,
                                kic=t.kic, gmag=t.gmag, meta=t.meta))
            else:
                row.ra, row.dec, row.gaia_id, row.kic, row.gmag, row.meta = t.ra, t.dec, t.gaia_id, t.kic, t.gmag, t.meta

    def set_target_state(self, target_id: str, state: str) -> None:
        with self.session() as s:
            s.get(TargetRow, target_id).state = state

    def job_done(self, job_key: str) -> bool:
        with self.session() as s:
            return s.scalar(select(func.count()).select_from(RunRow).where(RunRow.job_key == job_key, RunRow.status == "done")) > 0

    def start_run(self, job_key, target_id, detector, detector_version, ladder_version) -> int:
        with self.session() as s:
            r = RunRow(job_key=job_key, target_id=target_id, detector=detector, detector_version=detector_version,
                       ladder_version=ladder_version)
            s.add(r); s.flush(); return r.id

    def finish_run(self, run_id: int, status: str, error: str = "") -> None:
        with self.session() as s:
            r = s.get(RunRow, run_id); r.status, r.error, r.finished = status, error, utcnow()

    def add_signal(self, sig: Signal, run_id: int) -> int:
        with self.session() as s:
            existing = s.scalars(select(SignalRow).where(SignalRow.run_id == run_id, SignalRow.detector == sig.detector,
                                                         SignalRow.period.between(sig.period - 1e-4, sig.period + 1e-4))).first()
            if existing:
                return existing.id
            row = SignalRow(run_id=run_id, **sig.model_dump())
            s.add(row); s.flush(); return row.id

    def set_signal_status(self, signal_id: int, final_status: str, branch: str | None = None) -> None:
        with self.session() as s:
            row = s.get(SignalRow, signal_id); row.final_status = final_status
            if branch: row.branch = branch

    def add_test(self, signal_id: int, r: TestResult, duration_s: float) -> None:
        with self.session() as s:
            s.add(TestRow(signal_id=signal_id, rung=r.rung, status=r.status.value, metrics=r.metrics,
                          artifacts=r.artifacts, reason=r.reason, duration_s=duration_s))

    def add_artifact(self, sha256, nbytes, url, local_path, target_id=None, pinned=False) -> None:
        with self.session() as s:
            if s.get(ArtifactRow, sha256) is None:
                s.add(ArtifactRow(sha256=sha256, nbytes=nbytes, url=url, local_path=local_path, target_id=target_id, pinned=pinned))

    def touch_artifact(self, sha256: str) -> None:
        with self.session() as s:
            row = s.get(ArtifactRow, sha256)
            if row: row.last_used = utcnow()

    def mark_artifact_deleted(self, sha256: str) -> None:
        with self.session() as s:
            s.get(ArtifactRow, sha256).deleted_at = utcnow()

    def unpinned_artifacts(self, target_id: str | None = None) -> list[dict]:
        with self.session() as s:
            q = select(ArtifactRow).where(ArtifactRow.pinned.is_(False), ArtifactRow.deleted_at.is_(None))
            if target_id: q = q.where(ArtifactRow.target_id == target_id)
            return [dict(sha256=a.sha256, nbytes=a.nbytes, local_path=a.local_path, url=a.url, last_used=a.last_used)
                    for a in s.scalars(q.order_by(ArtifactRow.last_used))]

    def pin_target_artifacts(self, target_id: str) -> None:
        with self.session() as s:
            for a in s.scalars(select(ArtifactRow).where(ArtifactRow.target_id == target_id)):
                a.pinned = True

    def log_query(self, service, url, params: dict, status, nbytes, sha256=None, error="") -> None:
        with self.session() as s:
            s.add(QueryRow(service=service, url=url, params=json.dumps(params, default=str), status=status,
                           nbytes=nbytes, sha256=sha256, error=error))

    def counts(self) -> dict:
        with self.session() as s:
            out = {}
            for name, model in [("targets", TargetRow), ("runs", RunRow), ("signals", SignalRow), ("tests", TestRow),
                                ("candidates", CandidateRow), ("artifacts", ArtifactRow), ("queries", QueryRow)]:
                out[name] = s.scalar(select(func.count()).select_from(model))
            return out
```

`store/__init__.py`: `from .db import Store, make_job_key  # noqa: F401`

- [ ] **Step 4: Run tests** — `python -m pytest tests/unit/test_store.py -v` — Expected: 2 PASS

- [ ] **Step 5: Commit** — `git commit -am "feat: SQLAlchemy store" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"`

---

### Task 4: Blob cache and retention

**Files:**
- Create: `src/skysift/store/blob.py`, `src/skysift/store/retention.py`
- Test: `tests/unit/test_blob.py`, `tests/unit/test_retention.py`

**Interfaces:**
- Produces: `Blob` protocol: `put(data: bytes, name_hint: str) -> tuple[str sha256, str location]`, `get(location) -> bytes`, `exists(location) -> bool`, `delete(location) -> None`, `size(location) -> int`; `LocalBlob(root: Path)`, `S3Blob(bucket, prefix, client)`; `blob_from_url(url: str) -> Blob` (`file://` or `s3://`).
- `RetentionPolicy.parse("keep"|"stream"|"budget:50") -> RetentionPolicy(mode, budget_bytes)`; `Retention(policy, store, blob)` with `release_target(target_id) -> int bytes_freed` (stream mode: delete all unpinned artifacts of the target) and `enforce_budget() -> int` (budget mode: evict least-recently-used unpinned until under cap; raises `DiskBudgetExceeded` if still over).

- [ ] **Step 1: Failing tests**

`tests/unit/test_blob.py`:
```python
import hashlib
from skysift.store.blob import LocalBlob, S3Blob, blob_from_url


def test_local_put_get_delete(tmp_path):
    b = LocalBlob(tmp_path)
    sha, loc = b.put(b"hello", "x.fits")
    assert sha == hashlib.sha256(b"hello").hexdigest()
    assert b.exists(loc) and b.get(loc) == b"hello" and b.size(loc) == 5
    b.delete(loc); assert not b.exists(loc)


class FakeS3:
    def __init__(self): self.objs = {}
    def put_object(self, Bucket, Key, Body): self.objs[(Bucket, Key)] = Body
    def get_object(self, Bucket, Key):
        import io; return {"Body": io.BytesIO(self.objs[(Bucket, Key)])}
    def head_object(self, Bucket, Key):
        if (Bucket, Key) not in self.objs: raise KeyError
        return {"ContentLength": len(self.objs[(Bucket, Key)])}
    def delete_object(self, Bucket, Key): self.objs.pop((Bucket, Key), None)


def test_s3_blob_with_fake_client():
    b = S3Blob("bkt", "pre", client=FakeS3())
    sha, loc = b.put(b"abc", "y.fits")
    assert loc.startswith("s3://bkt/pre/") and b.get(loc) == b"abc" and b.size(loc) == 3
    b.delete(loc); assert not b.exists(loc)


def test_blob_from_url(tmp_path):
    assert isinstance(blob_from_url(f"file://{tmp_path.as_posix()}"), LocalBlob)
```

`tests/unit/test_retention.py`:
```python
import pytest
from skysift.store.db import Store
from skysift.store.blob import LocalBlob
from skysift.store.retention import Retention, RetentionPolicy, DiskBudgetExceeded


def setup(tmp_path, policy):
    st = Store(f"sqlite:///{tmp_path.as_posix()}/t.db"); st.init_schema()
    b = LocalBlob(tmp_path / "blobs")
    return st, b, Retention(RetentionPolicy.parse(policy), st, b)


def put(st, b, target, payload):
    sha, loc = b.put(payload, "f.fits"); st.add_artifact(sha, len(payload), "https://x", loc, target); return sha, loc


def test_parse():
    assert RetentionPolicy.parse("budget:2").budget_bytes == 2 * 1024**3
    assert RetentionPolicy.parse("stream").mode == "stream"


def test_stream_releases_unpinned_only(tmp_path):
    st, b, r = setup(tmp_path, "stream")
    s1, l1 = put(st, b, "t1", b"a" * 10); s2, l2 = put(st, b, "t1", b"b" * 10)
    st.pin_target_artifacts("t1")
    assert r.release_target("t1") == 0 and b.exists(l1)
    s3, l3 = put(st, b, "t2", b"c" * 10)
    assert r.release_target("t2") == 10 and not b.exists(l3)


def test_budget_evicts_lru_and_raises_when_all_pinned(tmp_path):
    st, b, r = setup(tmp_path, "budget:1")
    r.policy.budget_bytes = 25
    put(st, b, "t1", b"a" * 10); put(st, b, "t2", b"b" * 10); put(st, b, "t3", b"c" * 10)
    assert r.enforce_budget() >= 10
    for t in ("t1", "t2", "t3"): st.pin_target_artifacts(t)
    put(st, b, "t4", b"d" * 20); st.pin_target_artifacts("t4")
    with pytest.raises(DiskBudgetExceeded):
        r.enforce_budget()
```

- [ ] **Step 2: Run to verify failure** — Expected: import errors.

- [ ] **Step 3: Implement**

`store/blob.py`:
```python
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Protocol
from urllib.parse import urlparse


class Blob(Protocol):
    def put(self, data: bytes, name_hint: str) -> tuple[str, str]: ...
    def get(self, location: str) -> bytes: ...
    def exists(self, location: str) -> bool: ...
    def delete(self, location: str) -> None: ...
    def size(self, location: str) -> int: ...


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class LocalBlob:
    def __init__(self, root: Path):
        self.root = Path(root); self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, sha: str, name_hint: str) -> Path:
        d = self.root / sha[:2]; d.mkdir(exist_ok=True)
        return d / f"{sha}_{Path(name_hint).name}"

    def put(self, data, name_hint):
        sha = _sha(data); p = self._path(sha, name_hint)
        if not p.exists(): p.write_bytes(data)
        return sha, str(p)

    def get(self, location): return Path(location).read_bytes()
    def exists(self, location): return Path(location).exists()
    def delete(self, location): Path(location).unlink(missing_ok=True)
    def size(self, location): return Path(location).stat().st_size


class S3Blob:
    def __init__(self, bucket: str, prefix: str = "", client=None):
        if client is None:
            import boto3; client = boto3.client("s3")
        self.bucket, self.prefix, self.client = bucket, prefix.strip("/"), client

    def _key(self, location: str) -> str:
        return urlparse(location).path.lstrip("/")

    def put(self, data, name_hint):
        sha = _sha(data); key = f"{self.prefix}/{sha[:2]}/{sha}_{Path(name_hint).name}".lstrip("/")
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data)
        return sha, f"s3://{self.bucket}/{key}"

    def get(self, location): return self.client.get_object(Bucket=self.bucket, Key=self._key(location))["Body"].read()
    def exists(self, location):
        try: self.client.head_object(Bucket=self.bucket, Key=self._key(location)); return True
        except Exception: return False
    def delete(self, location): self.client.delete_object(Bucket=self.bucket, Key=self._key(location))
    def size(self, location): return int(self.client.head_object(Bucket=self.bucket, Key=self._key(location))["ContentLength"])


def blob_from_url(url: str) -> Blob:
    u = urlparse(url)
    if u.scheme == "file":
        return LocalBlob(Path((u.netloc + u.path) if u.netloc else u.path))
    if u.scheme == "s3":
        return S3Blob(u.netloc, u.path)
    raise ValueError(f"unsupported blob url: {url}")
```

`store/retention.py`:
```python
from __future__ import annotations

from dataclasses import dataclass

from skysift.store.blob import Blob
from skysift.store.db import Store


class DiskBudgetExceeded(RuntimeError):
    pass


@dataclass
class RetentionPolicy:
    mode: str                 # keep | stream | budget
    budget_bytes: int = 0

    @classmethod
    def parse(cls, text: str) -> "RetentionPolicy":
        text = text.strip().lower()
        if text in ("keep", "stream"):
            return cls(text)
        if text.startswith("budget:"):
            return cls("budget", int(float(text.split(":", 1)[1]) * 1024**3))
        raise ValueError(f"bad retention: {text}")


class Retention:
    def __init__(self, policy: RetentionPolicy, store: Store, blob: Blob):
        self.policy, self.store, self.blob = policy, store, blob

    def _delete(self, art: dict) -> int:
        if self.blob.exists(art["local_path"]):
            self.blob.delete(art["local_path"])
        self.store.mark_artifact_deleted(art["sha256"])
        return art["nbytes"]

    def release_target(self, target_id: str) -> int:
        if self.policy.mode != "stream":
            return 0
        return sum(self._delete(a) for a in self.store.unpinned_artifacts(target_id))

    def cached_bytes(self) -> int:
        return sum(a["nbytes"] for a in self.store.unpinned_artifacts()) + self._pinned_bytes()

    def _pinned_bytes(self) -> int:
        with self.store.session() as s:
            from sqlalchemy import select, func
            from skysift.store.db import ArtifactRow
            return s.scalar(select(func.coalesce(func.sum(ArtifactRow.nbytes), 0)).where(
                ArtifactRow.pinned.is_(True), ArtifactRow.deleted_at.is_(None)))

    def enforce_budget(self) -> int:
        if self.policy.mode != "budget":
            return 0
        freed = 0
        for art in self.store.unpinned_artifacts():        # least recently used first
            if self.cached_bytes() <= self.policy.budget_bytes:
                break
            freed += self._delete(art)
        if self.cached_bytes() > self.policy.budget_bytes:
            raise DiskBudgetExceeded(
                f"cache {self.cached_bytes()} B over budget {self.policy.budget_bytes} B and everything left is pinned evidence; "
                "raise SKYSIFT_RETENTION budget or move packs to S3")
        return freed
```

- [ ] **Step 4: Run** — `python -m pytest tests/unit/test_blob.py tests/unit/test_retention.py -v` — Expected: 6 PASS

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat: blob cache and retention policies" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"`

---

### Task 5: Network layer with retries and query logging

**Files:**
- Create: `src/skysift/net.py`
- Test: `tests/unit/test_net.py`

**Interfaces:**
- Produces: `class ServiceUnavailable(RuntimeError)`; `fetch(url: str, *, params: dict | None = None, service: str, store: Store | None, session=None, timeout: float = 60, retries: int = 5, backoff: float = 1.5) -> bytes`; `looks_like_error_document(data: bytes) -> str | None` (returns the error text when a 200 response body is a VOTable or HTML error, else None).

- [ ] **Step 1: Failing test**

```python
import pytest
from skysift.net import fetch, ServiceUnavailable, looks_like_error_document
from skysift.store.db import Store


class Resp:
    def __init__(self, status, content): self.status_code, self.content = status, content


class FlakySession:
    def __init__(self, fails, final=Resp(200, b"ok")): self.fails, self.final, self.calls = fails, final, 0
    def get(self, url, params=None, timeout=None):
        self.calls += 1
        if self.calls <= self.fails: raise ConnectionError("boom")
        return self.final


def test_fetch_retries_then_succeeds(tmp_path):
    st = Store(f"sqlite:///{tmp_path.as_posix()}/t.db"); st.init_schema()
    s = FlakySession(2)
    assert fetch("https://x/y", service="t", store=st, session=s, backoff=0.0) == b"ok"
    assert s.calls == 3 and st.counts()["queries"] == 3


def test_fetch_gives_up(tmp_path):
    st = Store(f"sqlite:///{tmp_path.as_posix()}/t.db"); st.init_schema()
    with pytest.raises(ServiceUnavailable):
        fetch("https://x/y", service="t", store=st, session=FlakySession(99), retries=2, backoff=0.0)


def test_error_document_detection():
    vot = b'<?xml version="1.0"?><VOTABLE><RESOURCE><INFO name="QUERY_STATUS" value="ERROR">UsageFault: bad</INFO></RESOURCE></VOTABLE>'
    assert "UsageFault" in looks_like_error_document(vot)
    assert looks_like_error_document(b"a,b\n1,2\n") is None
```

- [ ] **Step 2: Run** — Expected: import error.

- [ ] **Step 3: Implement `net.py`**

```python
from __future__ import annotations

import hashlib
import re
import time

import truststore

truststore.inject_into_ssl()
import requests  # noqa: E402  (after trust-store injection on purpose)


class ServiceUnavailable(RuntimeError):
    pass


_ERR_VOT = re.compile(rb'QUERY_STATUS"\s+value="ERROR">([^<]*)', re.I)
_ERR_HTML = re.compile(rb"<title>\s*(\d{3}[^<]*)</title>", re.I)


def looks_like_error_document(data: bytes) -> str | None:
    head = data[:4000]
    m = _ERR_VOT.search(head)
    if m: return m.group(1).decode(errors="replace").strip()
    if b"ORA-" in head and b"ORACLE" in head: return head.decode(errors="replace")[:200]
    m = _ERR_HTML.search(head)
    if m and head.lstrip().lower().startswith((b"<!doctype html", b"<html")): return m.group(1).decode(errors="replace")
    return None


def fetch(url, *, params=None, service, store=None, session=None, timeout=60.0, retries=5, backoff=1.5) -> bytes:
    sess = session or requests.Session()
    last = ""
    for attempt in range(retries + 1):
        try:
            r = sess.get(url, params=params, timeout=timeout)
            data = r.content
            sha = hashlib.sha256(data).hexdigest()
            if store: store.log_query(service, url, params or {}, r.status_code, len(data), sha)
            if r.status_code == 200:
                return data
            if r.status_code in (400, 401, 403, 404):
                raise ServiceUnavailable(f"{service} {url} -> HTTP {r.status_code}")
            last = f"HTTP {r.status_code}"
        except ServiceUnavailable:
            raise
        except Exception as e:  # connection errors, timeouts
            last = repr(e)
            if store: store.log_query(service, url, params or {}, None, 0, None, error=last)
        if attempt < retries:
            time.sleep(backoff * (2 ** attempt))
    raise ServiceUnavailable(f"{service} {url} failed after {retries + 1} attempts: {last}")
```

- [ ] **Step 4: Run** — Expected: 3 PASS

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat: resilient fetch with query log" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"`

---

### Task 6: Light-curve parsing and operations

**Files:**
- Create: `src/skysift/lc/__init__.py`, `src/skysift/lc/kbonus.py`, `src/skysift/lc/ops.py`
- Test: `tests/synthetic.py`, `tests/unit/test_lc_ops.py`

**Interfaces:**
- Produces: `parse_kbonus_fits(data: bytes) -> LightCurve` (flux = PSF `FLUX`, flux_alt = `SAP_FLUX`, quality = `SAP_QUALITY`, quarter = `QUARTER`, meta = {kic, gaia_id, gmag, tpf_org, per_quarter: {q: {flfrcsap, crowdsap, npixsap, psffrac}}, ra, dec}); `detrend(lc: LightCurve, window_days: float = 1.0, gap_days: float = 1.0) -> np.ndarray` (normalised flux, NaN where undefined); `fold_phase(time, period, t0) -> np.ndarray` (days from nearest transit centre, in [-P/2, P/2)); `EventDepths` dataclass (`epochs, quarters, depths, errors, mean, err_formal, err_scaled, control_rms, n`); `event_depths(time, fnorm, quarter, period, t0, duration_days, local_baseline="linear") -> EventDepths`; `secondary_search(time, fnorm, period, t0, duration_days, phases=np.linspace(0.1, 0.9, 81)) -> tuple[float best_phase, float depth, float sigma]`.
- `tests/synthetic.py`: `make_lightcurve(n_days=1400, cadence_min=29.4, noise_ppm=1500, seed=0, quarters=True, gaps=True) -> LightCurve` with per-quarter offsets and 1.5 d gaps every 93 d; `inject_transit(lc, period, t0, duration_days, depth_ppm) -> LightCurve` (box shape, in place on a copy).

- [ ] **Step 1: Failing tests**

`tests/synthetic.py`:
```python
import numpy as np
from skysift.models import LightCurve


def make_lightcurve(n_days=1400, cadence_min=29.4, noise_ppm=1500, seed=0, quarters=True, gaps=True):
    rng = np.random.default_rng(seed)
    t = np.arange(131.5, 131.5 + n_days, cadence_min / 1440.0)
    q = ((t - 131.5) // 93.0).astype(int) + 1 if quarters else np.ones_like(t, dtype=int)
    f = 1.0 + rng.normal(0, noise_ppm * 1e-6, t.size)
    if quarters:
        f *= 1.0 + 0.01 * (q % 3)          # per-quarter offsets
    if gaps:
        keep = ((t - 131.5) % 93.0) > 1.5
        t, f, q = t[keep], f[keep], q[keep]
    e = np.full(t.size, noise_ppm * 1e-6)
    return LightCurve(time=t, flux=f, flux_err=e, quality=np.zeros(t.size, int), quarter=q, flux_alt=f.copy())


def inject_transit(lc: LightCurve, period, t0, duration_days, depth_ppm):
    ph = (lc.time - t0 + 0.5 * period) % period - 0.5 * period
    f = lc.flux.copy(); f[np.abs(ph) < 0.5 * duration_days] *= 1 - depth_ppm * 1e-6
    return LightCurve(lc.time, f, lc.flux_err, lc.quality, lc.quarter, lc.flux_alt, dict(lc.meta))
```

`tests/unit/test_lc_ops.py`:
```python
import numpy as np
from skysift.lc.ops import detrend, event_depths, fold_phase, secondary_search
from tests.synthetic import make_lightcurve, inject_transit

P, T0, DUR, DEPTH = 13.769, 136.3, 0.09, 2000.0


def test_detrend_removes_quarter_offsets():
    lc = make_lightcurve()
    fn = detrend(lc)
    good = np.isfinite(fn)
    assert abs(np.nanmedian(fn[lc.quarter == 1]) - np.nanmedian(fn[lc.quarter == 2])) < 2e-4
    assert good.sum() > 0.9 * lc.time.size


def test_event_depths_recovers_injection():
    lc = inject_transit(make_lightcurve(), P, T0, DUR, DEPTH)
    fn = detrend(lc)
    ed = event_depths(lc.time, fn, lc.quarter, P, T0, DUR)
    assert ed.n >= 80
    assert abs(ed.mean - DEPTH) < 3 * ed.err_scaled
    assert ed.err_scaled < 120


def test_fold_phase_range():
    ph = fold_phase(np.array([0.0, 6.9, 13.769, 20.0]), P, 0.0)
    assert np.all(ph >= -P / 2) and np.all(ph < P / 2) and abs(ph[2]) < 1e-9


def test_secondary_search_finds_offset_eclipse():
    lc = inject_transit(make_lightcurve(seed=3), P, T0, DUR, 3000)
    lc = inject_transit(lc, P, T0 + 0.529 * P, 0.06, 2500)      # eccentric secondary
    fn = detrend(lc)
    phase, depth, sigma = secondary_search(lc.time, fn, P, T0, DUR)
    assert abs(phase - 0.529) < 0.02 and sigma > 5
```

- [ ] **Step 2: Run** — Expected: import errors.

- [ ] **Step 3: Implement**

`lc/kbonus.py`:
```python
from __future__ import annotations

import io

import numpy as np
from astropy.io import fits

from skysift.models import LightCurve


def parse_kbonus_fits(data: bytes) -> LightCurve:
    with fits.open(io.BytesIO(data), memmap=False) as h:
        d = h[1].data; p = h[0].header
        per_q = {}
        for hd in h[2:]:
            if hd.name.startswith("LIGHTCURVE_Q"):
                q = int(hd.header["QUARTER"])
                per_q[q] = {k.lower(): hd.header.get(k) for k in ("FLFRCSAP", "CROWDSAP", "NPIXSAP", "PSFFRAC")}
        meta = dict(kic=p.get("KEPLERID"), gaia_id=str(p.get("GAIAID", "")).replace("Gaia DR3 ", ""),
                    gmag=p.get("GMAG"), tpf_org=p.get("TPFORG"), ra=p.get("RA_OBJ"), dec=p.get("DEC_OBJ"),
                    per_quarter=per_q, time_system="BKJD")
        return LightCurve(time=np.asarray(d["TIME"], float), flux=np.asarray(d["FLUX"], float),
                          flux_err=np.asarray(d["FLUX_ERR"], float), quality=np.asarray(d["SAP_QUALITY"], int),
                          quarter=np.asarray(d["QUARTER"], int), flux_alt=np.asarray(d["SAP_FLUX"], float), meta=meta)
```

`lc/ops.py`:
```python
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.ndimage import median_filter

from skysift.models import LightCurve


def _segments(time: np.ndarray, gap_days: float):
    breaks = np.where(np.diff(time) > gap_days)[0] + 1
    return np.split(np.arange(time.size), breaks)


def detrend(lc: LightCurve, window_days: float = 1.0, gap_days: float = 1.0, flux: np.ndarray | None = None) -> np.ndarray:
    """Divide by a running median per contiguous segment. Returns normalised flux aligned with lc.time (NaN where bad)."""
    m = lc.good_mask()
    f = (lc.flux if flux is None else flux)
    out = np.full(lc.time.size, np.nan)
    t, y, idx = lc.time[m], f[m], np.where(m)[0]
    for seg in _segments(t, gap_days):
        if seg.size < 10:
            continue
        dt = np.nanmedian(np.diff(t[seg]))
        n = max(5, int(round(window_days / dt)) | 1)
        trend = median_filter(y[seg], size=n, mode="nearest")
        out[idx[seg]] = y[seg] / trend
    return out


def fold_phase(time: np.ndarray, period: float, t0: float) -> np.ndarray:
    return (time - t0 + 0.5 * period) % period - 0.5 * period


@dataclass
class EventDepths:
    epochs: np.ndarray; quarters: np.ndarray; depths: np.ndarray; errors: np.ndarray
    mean: float; err_formal: float; err_scaled: float; control_rms: float; n: int


def _box_depth(ph, fn, half, baseline="linear"):
    i = np.abs(ph) < 0.8 * half
    o = (np.abs(ph) > 1.5 * half) & (np.abs(ph) < 0.5)
    if i.sum() < 3 or o.sum() < 20:
        return None
    if baseline == "linear":
        c = np.polyfit(ph[o], fn[o], 1); base_i = np.polyval(c, ph[i]); base_o = np.polyval(c, ph[o])
        resid = fn[o] - base_o
    else:
        base_i = np.full(i.sum(), np.median(fn[o])); resid = fn[o] - np.median(fn[o])
    scat = 1.4826 * np.median(np.abs(resid))
    d = float(np.mean(1 - fn[i] / base_i))
    return d, scat / np.sqrt(i.sum())


def event_depths(time, fnorm, quarter, period, t0, duration_days, local_baseline="linear") -> EventDepths:
    good = np.isfinite(fnorm); time, fnorm, quarter = time[good], fnorm[good], quarter[good]
    half = duration_days / 2.0
    ph = fold_phase(time, period, t0); ep = np.round((time - t0) / period).astype(int)
    rows = []
    for e in np.unique(ep):
        s = ep == e
        r = _box_depth(ph[s], fnorm[s], half, local_baseline)
        if r: rows.append((e, int(np.median(quarter[s])), r[0], r[1]))
    if not rows:
        return EventDepths(np.array([]), np.array([]), np.array([]), np.array([]), np.nan, np.nan, np.nan, np.nan, 0)
    a = np.array(rows, dtype=float)
    w = 1 / a[:, 3] ** 2; mean = float(np.sum(w * a[:, 2]) / np.sum(w)); err = float(1 / np.sqrt(np.sum(w)))
    chi2 = float(np.sum(w * (a[:, 2] - mean) ** 2)); scaled = err * max(1.0, np.sqrt(chi2 / max(1, len(rows) - 1)))
    ctrl = []
    for off in np.linspace(0.1, 0.9, 9):
        p2 = fold_phase(time, period, t0 + off * period); r = _box_depth(p2, fnorm, half, local_baseline)
        if r: ctrl.append(r[0])
    return EventDepths(a[:, 0].astype(int), a[:, 1].astype(int), a[:, 2] * 1e6, a[:, 3] * 1e6, mean * 1e6, err * 1e6,
                       scaled * 1e6, float(np.std(ctrl) * 1e6) if ctrl else np.nan, len(rows))


def secondary_search(time, fnorm, period, t0, duration_days, phases=None):
    """Box depth at each trial phase; returns (best_phase, depth_ppm, sigma) excluding +-1.5 durations of the primary."""
    phases = np.linspace(0.1, 0.9, 81) if phases is None else phases
    good = np.isfinite(fnorm); time, fnorm = time[good], fnorm[good]
    half = duration_days / 2.0
    out = []
    for p in phases:
        ph = fold_phase(time, period, t0 + p * period)
        i = np.abs(ph) < 0.8 * half; o = (np.abs(ph) > 1.5 * half) & (np.abs(ph) < 0.5)
        if i.sum() < 5 or o.sum() < 50:
            out.append((p, np.nan, np.nan)); continue
        base = np.median(fnorm[o]); scat = 1.4826 * np.median(np.abs(fnorm[o] - base))
        d = 1 - np.mean(fnorm[i]) / base
        out.append((p, d * 1e6, d / (scat / np.sqrt(i.sum()))))
    out = np.array(out); k = np.nanargmax(out[:, 2])
    return float(out[k, 0]), float(out[k, 1]), float(out[k, 2])
```

- [ ] **Step 4: Run** — `python -m pytest tests/unit/test_lc_ops.py -v` — Expected: 4 PASS

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat: KBonus parser and light-curve operations" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"`

---

### Task 7: Box-dip detector

**Files:**
- Create: `src/skysift/detectors/__init__.py`, `src/skysift/detectors/base.py`, `src/skysift/detectors/box_dip.py`
- Test: `tests/unit/test_box_dip.py`

**Interfaces:**
- Produces: `Detector` protocol: `name: str`, `version: str`, `cost_class: str`, `detect(lc: LightCurve, fnorm: np.ndarray, thresholds: Thresholds) -> list[Signal]`; `DETECTORS: dict[str, Detector]` registry with `get_detector(name)`; `BoxDip(top_k=3, oversample=5)` using astropy `BoxLeastSquares`, log-spaced periods from `min_period_days` to min(`max_period_days`, baseline/3), durations `[1, 1.5, 2, 3, 4, 6, 9, 12] h`, objective `snr`; SDE = (peak power - median)/MAD-std of the power spectrum; keeps top_k peaks separated by > 2 percent in period; `Signal.extra` holds `{"baseline_days", "sde_spectrum_std", "alias_of": None}`.

- [ ] **Step 1: Failing tests**

```python
import numpy as np
from skysift.config import Thresholds
from skysift.detectors.box_dip import BoxDip
from skysift.lc.ops import detrend
from skysift.models import LightCurve
from tests.synthetic import make_lightcurve, inject_transit


def test_recovers_injected_transit():
    lc = inject_transit(make_lightcurve(seed=1), 13.769, 136.3, 0.09, 2000)
    sig = BoxDip().detect(lc, detrend(lc), Thresholds())
    assert sig and abs(sig[0].period - 13.769) < 0.002 and sig[0].sde > 9 and sig[0].n_events >= 60
    assert 1500 < sig[0].depth_ppm < 2500


def test_pure_noise_stays_below_threshold():
    lc = make_lightcurve(seed=7)
    sig = BoxDip().detect(lc, detrend(lc), Thresholds())
    assert all(s.sde < 9 for s in sig)


def test_short_or_empty_lightcurve_returns_nothing():
    lc = make_lightcurve(n_days=5, seed=2)
    assert BoxDip().detect(lc, detrend(lc), Thresholds()) == []
    nan_lc = LightCurve(np.arange(1000.0), np.full(1000, np.nan), np.ones(1000), np.zeros(1000, int), np.ones(1000, int))
    assert BoxDip().detect(nan_lc, detrend(nan_lc), Thresholds()) == []
```

- [ ] **Step 2: Run** — Expected: import errors.

- [ ] **Step 3: Implement**

`detectors/base.py`:
```python
from __future__ import annotations

from typing import Protocol

import numpy as np

from skysift.config import Thresholds
from skysift.models import LightCurve, Signal


class Detector(Protocol):
    name: str
    version: str
    cost_class: str

    def detect(self, lc: LightCurve, fnorm: np.ndarray, thresholds: Thresholds) -> list[Signal]: ...


DETECTORS: dict[str, Detector] = {}


def register(det: Detector) -> Detector:
    DETECTORS[det.name] = det
    return det


def get_detector(name: str) -> Detector:
    import skysift.detectors.box_dip  # noqa: F401  ensure registration
    return DETECTORS[name]
```

`detectors/box_dip.py`:
```python
from __future__ import annotations

import numpy as np
from astropy.timeseries import BoxLeastSquares

from skysift.config import Thresholds
from skysift.detectors.base import register
from skysift.models import LightCurve, Signal

MIN_POINTS = 500
DURATIONS_H = np.array([1, 1.5, 2, 3, 4, 6, 9, 12])


class BoxDip:
    name = "box_dip"
    version = "1"
    cost_class = "cpu_medium"

    def __init__(self, top_k: int = 3, oversample: int = 5):
        self.top_k, self.oversample = top_k, oversample

    def detect(self, lc: LightCurve, fnorm: np.ndarray, th: Thresholds) -> list[Signal]:
        m = np.isfinite(fnorm) & np.isfinite(lc.time)
        if m.sum() < MIN_POINTS:
            return []
        t, y = lc.time[m], fnorm[m]
        med = np.median(y); mad = 1.4826 * np.median(np.abs(y - med))
        k = np.abs(y - med) < 5 * mad
        t, y = t[k], y[k]
        baseline = float(t.max() - t.min())
        pmax = min(th.max_period_days, baseline / 3.0)
        if pmax <= th.min_period_days:
            return []
        durations = DURATIONS_H / 24.0
        # frequency grid fine enough that a transit drifts < duration/oversample across the baseline
        q = durations.min() / baseline / self.oversample
        freqs = np.arange(1 / pmax, 1 / th.min_period_days, q)
        periods = 1 / freqs[::-1]
        model = BoxLeastSquares(t, y)
        res = model.power(periods, durations, objective="snr")
        power = np.asarray(res.power)
        pmed = np.median(power); pstd = 1.4826 * np.median(np.abs(power - pmed)) or 1e-12
        order = np.argsort(power)[::-1]
        out: list[Signal] = []
        for i in order:
            p = float(periods[i])
            if any(abs(p - s.period) / s.period < 0.02 for s in out):
                continue
            dur = float(res.duration[i]); t0 = float(res.transit_time[i]); depth = float(res.depth[i])
            ph = (t - t0 + 0.5 * p) % p - 0.5 * p
            n_events = int(np.unique(np.round((t[np.abs(ph) < 0.5 * dur] - t0) / p)).size)
            snr = float(depth / max(res.depth_err[i], 1e-12))
            out.append(Signal(target_id=str(lc.meta.get("target_id", "")), detector=self.name, detector_version=self.version,
                              period=p, t0=t0, duration=dur, depth_ppm=depth * 1e6, snr=snr,
                              sde=float((power[i] - pmed) / pstd), n_events=n_events,
                              extra={"baseline_days": baseline, "power_median": float(pmed), "power_std": float(pstd)}))
            if len(out) >= self.top_k:
                break
        return out


register(BoxDip())
```

`detectors/__init__.py`: `from .base import DETECTORS, get_detector, register  # noqa: F401`

- [ ] **Step 4: Run** — `python -m pytest tests/unit/test_box_dip.py -v` — Expected: 3 PASS (if the first test's SDE sits below 9 on this synthetic noise, lower `noise_ppm` in `make_lightcurve` call to 1000 rather than touching thresholds).

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat: box-dip detector with SDE" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"`

---

### Task 8: Vetting framework (ladder runner)

**Files:**
- Create: `src/skysift/vetting/__init__.py`, `src/skysift/vetting/base.py`
- Test: `tests/unit/test_ladder.py`

**Interfaces:**
- Produces: `DataBundle(lc: LightCurve, fnorm: np.ndarray, fnorm_alt: np.ndarray | None, target: Target, thresholds: Thresholds, store: Store | None, fetch=skysift.net.fetch)`; `Rung` protocol: `name: str`, `cost: float` (seconds, for ordering and reporting), `run(signal: Signal, bundle: DataBundle) -> TestResult`; `Ladder(rungs: list[Rung], version: str)` with `run(signal, bundle, on_result: Callable[[TestResult, float], None] | None = None) -> tuple[str outcome, list[TestResult]]` where outcome is `"pass"`, `"fail"` or `"undetermined"`: rungs execute in `cost` order; FAIL stops; UNDETERMINED continues and the final outcome is `undetermined` if nothing failed but at least one rung was undetermined; exceptions inside a rung become UNDETERMINED with reason `exception: ...` (and are logged), never a crash.

- [ ] **Step 1: Failing test**

```python
from skysift.models import Signal, TestResult, TestStatus
from skysift.vetting.base import Ladder


class R:
    def __init__(self, name, cost, status, boom=False): self.name, self.cost, self.status, self.boom = name, cost, status, boom
    def run(self, signal, bundle):
        if self.boom: raise ValueError("kaboom")
        return TestResult(rung=self.name, status=self.status)


SIG = Signal(target_id="t", detector="d", detector_version="1", period=1, t0=0, duration=0.1, depth_ppm=1, snr=1, sde=1, n_events=3)


def test_runs_in_cost_order_and_stops_at_fail():
    seen = []
    ladder = Ladder([R("slow", 10, TestStatus.PASS), R("fast", 1, TestStatus.FAIL), R("mid", 5, TestStatus.PASS)], "1")
    outcome, results = ladder.run(SIG, None, on_result=lambda r, dt: seen.append(r.rung))
    assert outcome == "fail" and seen == ["fast"]


def test_undetermined_continues_and_marks_outcome():
    ladder = Ladder([R("a", 1, TestStatus.UNDETERMINED), R("b", 2, TestStatus.PASS)], "1")
    outcome, results = ladder.run(SIG, None)
    assert outcome == "undetermined" and [r.rung for r in results] == ["a", "b"]


def test_exception_becomes_undetermined():
    ladder = Ladder([R("a", 1, TestStatus.PASS, boom=True)], "1")
    outcome, results = ladder.run(SIG, None)
    assert outcome == "undetermined" and results[0].reason.startswith("exception")
```

- [ ] **Step 2: Run** — Expected: import error.

- [ ] **Step 3: Implement `vetting/base.py`**

```python
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Callable, Protocol

import numpy as np

from skysift import net
from skysift.config import Thresholds
from skysift.models import LightCurve, Signal, Target, TestResult, TestStatus

log = logging.getLogger(__name__)


@dataclass
class DataBundle:
    lc: LightCurve
    fnorm: np.ndarray
    target: Target
    thresholds: Thresholds
    fnorm_alt: np.ndarray | None = None
    store: object | None = None
    fetch: Callable = net.fetch
    cache: dict = field(default_factory=dict)   # per-field shared data (catalogue cones, etc.)


class Rung(Protocol):
    name: str
    cost: float

    def run(self, signal: Signal, bundle: DataBundle) -> TestResult: ...


class Ladder:
    def __init__(self, rungs: list[Rung], version: str):
        self.rungs = sorted(rungs, key=lambda r: r.cost)
        self.version = version

    def run(self, signal: Signal, bundle: DataBundle | None, on_result=None) -> tuple[str, list[TestResult]]:
        results: list[TestResult] = []
        any_undetermined = False
        for rung in self.rungs:
            t0 = time.perf_counter()
            try:
                r = rung.run(signal, bundle)
            except Exception as e:  # a broken test must never pass a signal or kill the sweep
                log.exception("rung %s raised", rung.name)
                r = TestResult(rung=rung.name, status=TestStatus.UNDETERMINED, reason=f"exception: {e!r}")
            dt = time.perf_counter() - t0
            results.append(r)
            if on_result:
                on_result(r, dt)
            if r.status == TestStatus.FAIL:
                return "fail", results
            if r.status == TestStatus.UNDETERMINED:
                any_undetermined = True
        return ("undetermined" if any_undetermined else "pass"), results
```

`vetting/__init__.py`: `from .base import DataBundle, Ladder, Rung  # noqa: F401`

- [ ] **Step 4: Run** — Expected: 3 PASS

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat: cost-ordered vetting ladder" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"`

---

### Task 9: Rungs 1 to 3 (detection, shape and parity, stability)

**Files:**
- Create: `src/skysift/vetting/rung1_detection.py`, `src/skysift/vetting/rung2_shape.py`, `src/skysift/vetting/rung3_stability.py`
- Test: `tests/unit/test_rungs_1_3.py`

**Interfaces:**
- Produces: `Rung1Detection()` (name `rung1_detection`, cost 0.01): FAIL if `sde < sde_min` or `snr < snr_min` or `n_events < min_events` or duration outside `[duration_ratio_min, duration_ratio_max]` times the circular-orbit duration for a 0.5 Msun, 0.5 Rsun star (fallback when no stellar parameters: `T_circ = 13 h * (P / 365 d)^(1/3)` scaled to the star when `bundle.target.meta` has `mass`, `radius`); UNDETERMINED with reason `systematic_alias` when the period is within 1 percent of 93.0, 46.5, 372.5 or 186.25 days.
- `Rung2Shape()` (name `rung2_shape`, cost 0.05): computes odd and even `event_depths`, the phase-0.5 secondary depth, the free-phase `secondary_search`, and the transit-timing scatter; FAIL on odd/even difference > `parity_sigma`; FAIL on secondary at 0.5 > `secondary_sigma`; FAIL with reason `eccentric_secondary` when the free-phase search exceeds `free_secondary_sigma` (this routes the signal to the EB branch in M5); metrics carry all numbers.
- `Rung3Stability()` (name `rung3_stability`, cost 0.05): per-season (quarter mod 4) weighted mean depths; FAIL when chi-square of seasons around the global mean has p < 0.0027 (3 sigma) **and** at least two seasons individually differ from the mean by more than `season_sigma`; metrics include `season_depths`, `sap_psf_ratio` (from `fnorm_alt`, informational only).

- [ ] **Step 1: Failing tests**

```python
import numpy as np
from skysift.config import Thresholds
from skysift.detectors.box_dip import BoxDip
from skysift.lc.ops import detrend
from skysift.models import Signal, Target, TestStatus
from skysift.vetting.base import DataBundle
from skysift.vetting.rung1_detection import Rung1Detection
from skysift.vetting.rung2_shape import Rung2Shape
from skysift.vetting.rung3_stability import Rung3Stability
from tests.synthetic import make_lightcurve, inject_transit

P, T0, DUR = 13.769, 136.3, 0.09
TGT = Target(source="test", target_id="t", ra=0, dec=0)


def bundle(lc):
    return DataBundle(lc=lc, fnorm=detrend(lc), fnorm_alt=detrend(lc, flux=lc.flux_alt), target=TGT, thresholds=Thresholds())


def signal(lc):
    return BoxDip().detect(lc, detrend(lc), Thresholds())[0]


def test_rung1_passes_good_signal_and_flags_alias():
    lc = inject_transit(make_lightcurve(seed=1), P, T0, DUR, 2000)
    assert Rung1Detection().run(signal(lc), bundle(lc)).status == TestStatus.PASS
    s = signal(lc).model_copy(update={"period": 93.2})
    r = Rung1Detection().run(s, bundle(lc))
    assert r.status == TestStatus.UNDETERMINED and r.reason == "systematic_alias"


def test_rung1_fails_low_sde():
    lc = inject_transit(make_lightcurve(seed=1), P, T0, DUR, 2000)
    s = signal(lc).model_copy(update={"sde": 4.0})
    assert Rung1Detection().run(s, bundle(lc)).status == TestStatus.FAIL


def test_rung2_pass_then_fail_on_alternating_depths():
    lc = inject_transit(make_lightcurve(seed=2, noise_ppm=800), P, T0, DUR, 2000)
    assert Rung2Shape().run(signal(lc), bundle(lc)).status == TestStatus.PASS
    eb = inject_transit(make_lightcurve(seed=2, noise_ppm=800), 2 * P, T0, DUR, 6000)   # odd events deep
    eb = inject_transit(eb, 2 * P, T0 + P, DUR, 2000)                                    # even events shallow
    s = signal(lc).model_copy(update={"period": P})
    r = Rung2Shape().run(s, bundle(eb))
    assert r.status == TestStatus.FAIL and "odd_even" in r.reason


def test_rung2_flags_eccentric_secondary():
    lc = inject_transit(make_lightcurve(seed=3, noise_ppm=800), P, T0, DUR, 3000)
    lc = inject_transit(lc, P, T0 + 0.529 * P, 0.06, 2500)
    r = Rung2Shape().run(signal(lc), bundle(lc))
    assert r.status == TestStatus.FAIL and r.reason == "eccentric_secondary"
    assert abs(r.metrics["free_secondary_phase"] - 0.529) < 0.02


def test_rung3_fails_on_season_dependence():
    lc = make_lightcurve(seed=4, noise_ppm=800)
    season3 = (lc.quarter % 4) == 3
    f = lc.flux.copy()
    ph = (lc.time - T0 + 0.5 * P) % P - 0.5 * P
    f[(np.abs(ph) < DUR / 2) & season3] *= 1 - 12000e-6
    f[(np.abs(ph) < DUR / 2) & ~season3] *= 1 - 500e-6
    from skysift.models import LightCurve
    lc2 = LightCurve(lc.time, f, lc.flux_err, lc.quality, lc.quarter, lc.flux_alt)
    s = Signal(target_id="t", detector="box_dip", detector_version="1", period=P, t0=T0, duration=DUR, depth_ppm=3000, snr=20, sde=15, n_events=90)
    r = Rung3Stability().run(s, bundle(lc2))
    assert r.status == TestStatus.FAIL and "season" in r.reason
    good = inject_transit(make_lightcurve(seed=4, noise_ppm=800), P, T0, DUR, 2000)
    assert Rung3Stability().run(s, bundle(good)).status == TestStatus.PASS
```

- [ ] **Step 2: Run** — Expected: import errors.

- [ ] **Step 3: Implement**

`vetting/rung1_detection.py`:
```python
from __future__ import annotations

import numpy as np

from skysift.models import Signal, TestResult, TestStatus
from skysift.vetting.base import DataBundle

SYSTEMATIC_PERIODS = (93.0, 46.5, 372.5, 186.25)


def circular_duration_days(period_days: float, mass_msun: float = 0.5, radius_rsun: float = 0.5) -> float:
    """Central-transit duration for a circular orbit: T = P/pi * asin(R*/a) with a from Kepler's third law."""
    a_au = (mass_msun * (period_days / 365.25) ** 2) ** (1 / 3)
    a_rsun = a_au * 215.032
    return period_days / np.pi * np.arcsin(min(1.0, radius_rsun / a_rsun))


class Rung1Detection:
    name = "rung1_detection"
    cost = 0.01

    def run(self, s: Signal, b: DataBundle) -> TestResult:
        th = b.thresholds
        if any(abs(s.period - p) / p < 0.01 for p in SYSTEMATIC_PERIODS):
            return TestResult(rung=self.name, status=TestStatus.UNDETERMINED, reason="systematic_alias",
                              metrics={"period": s.period})
        mass = b.target.meta.get("mass", 0.5); radius = b.target.meta.get("radius", 0.5)
        t_circ = circular_duration_days(s.period, mass, radius)
        ratio = s.duration / t_circ
        m = {"sde": s.sde, "snr": s.snr, "n_events": s.n_events, "duration_ratio": ratio, "t_circ_days": t_circ}
        if s.sde < th.sde_min: return TestResult(rung=self.name, status=TestStatus.FAIL, metrics=m, reason=f"sde {s.sde:.1f} < {th.sde_min}")
        if s.snr < th.snr_min: return TestResult(rung=self.name, status=TestStatus.FAIL, metrics=m, reason=f"snr {s.snr:.1f} < {th.snr_min}")
        if s.n_events < th.min_events: return TestResult(rung=self.name, status=TestStatus.FAIL, metrics=m, reason=f"n_events {s.n_events} < {th.min_events}")
        if not (th.duration_ratio_min <= ratio <= th.duration_ratio_max):
            return TestResult(rung=self.name, status=TestStatus.FAIL, metrics=m, reason=f"duration ratio {ratio:.2f} outside range")
        return TestResult(rung=self.name, status=TestStatus.PASS, metrics=m)
```

`vetting/rung2_shape.py`:
```python
from __future__ import annotations

import numpy as np

from skysift.lc.ops import event_depths, secondary_search
from skysift.models import Signal, TestResult, TestStatus
from skysift.vetting.base import DataBundle


class Rung2Shape:
    name = "rung2_shape"
    cost = 0.05

    def run(self, s: Signal, b: DataBundle) -> TestResult:
        th = b.thresholds
        lc, fn = b.lc, b.fnorm
        ep = np.round((lc.time - s.t0) / s.period).astype(int)
        odd = ep % 2 == 1; even = ~odd
        d_odd = event_depths(lc.time[odd], fn[odd], lc.quarter[odd], s.period, s.t0, s.duration)
        d_even = event_depths(lc.time[even], fn[even], lc.quarter[even], s.period, s.t0, s.duration)
        d_sec = event_depths(lc.time, fn, lc.quarter, s.period, s.t0 + 0.5 * s.period, s.duration)
        all_d = event_depths(lc.time, fn, lc.quarter, s.period, s.t0, s.duration)
        free_phase, free_depth, free_sigma = secondary_search(lc.time, fn, s.period, s.t0, s.duration)
        parity_sigma = abs(d_odd.mean - d_even.mean) / np.hypot(d_odd.err_scaled, d_even.err_scaled)
        sec_sigma = d_sec.mean / d_sec.err_scaled if d_sec.n else 0.0
        timing_rms_min = float(np.nan)
        m = dict(depth_ppm=all_d.mean, depth_err_ppm=all_d.err_scaled, n_events=all_d.n, odd_ppm=d_odd.mean, even_ppm=d_even.mean,
                 parity_sigma=float(parity_sigma), secondary_ppm=d_sec.mean, secondary_sigma=float(sec_sigma),
                 free_secondary_phase=free_phase, free_secondary_ppm=free_depth, free_secondary_sigma=free_sigma,
                 control_rms_ppm=all_d.control_rms, timing_rms_min=timing_rms_min)
        if not np.isfinite(parity_sigma) or d_odd.n < 2 or d_even.n < 2:
            return TestResult(rung=self.name, status=TestStatus.UNDETERMINED, metrics=m, reason="too few odd/even events")
        if parity_sigma > th.parity_sigma:
            return TestResult(rung=self.name, status=TestStatus.FAIL, metrics=m, reason=f"odd_even {parity_sigma:.1f} sigma")
        if sec_sigma > th.secondary_sigma:
            return TestResult(rung=self.name, status=TestStatus.FAIL, metrics=m, reason=f"secondary_at_0.5 {sec_sigma:.1f} sigma")
        if free_sigma > th.free_secondary_sigma and abs(free_phase - 0.5) > 0.02:
            return TestResult(rung=self.name, status=TestStatus.FAIL, metrics=m, reason="eccentric_secondary")
        return TestResult(rung=self.name, status=TestStatus.PASS, metrics=m)
```

`vetting/rung3_stability.py`:
```python
from __future__ import annotations

import numpy as np
from scipy.stats import chi2 as chi2_dist

from skysift.lc.ops import event_depths
from skysift.models import Signal, TestResult, TestStatus
from skysift.vetting.base import DataBundle


class Rung3Stability:
    name = "rung3_stability"
    cost = 0.05

    def run(self, s: Signal, b: DataBundle) -> TestResult:
        th = b.thresholds
        lc, fn = b.lc, b.fnorm
        ed = event_depths(lc.time, fn, lc.quarter, s.period, s.t0, s.duration)
        if ed.n < 4:
            return TestResult(rung=self.name, status=TestStatus.UNDETERMINED, reason="too few events")
        seasons = ed.quarters % 4
        means, errs = {}, {}
        for sn in np.unique(seasons):
            k = seasons == sn; w = 1 / ed.errors[k] ** 2
            mu = np.sum(w * ed.depths[k]) / np.sum(w); e = 1 / np.sqrt(np.sum(w))
            c2 = np.sum(w * (ed.depths[k] - mu) ** 2); e *= max(1.0, np.sqrt(c2 / max(1, k.sum() - 1)))
            means[int(sn)], errs[int(sn)] = float(mu), float(e)
        mu_all = ed.mean
        z = {k: (means[k] - mu_all) / errs[k] for k in means}
        chi2 = float(sum(v ** 2 for v in z.values())); dof = max(1, len(z) - 1)
        p = float(chi2_dist.sf(chi2, dof))
        n_outliers = sum(abs(v) > th.season_sigma for v in z.values())
        ratio = np.nan
        if b.fnorm_alt is not None:
            alt = event_depths(lc.time, b.fnorm_alt, lc.quarter, s.period, s.t0, s.duration)
            if alt.n and ed.mean: ratio = float(alt.mean / ed.mean)
        m = dict(season_depths=means, season_errs=errs, season_z=z, chi2=chi2, dof=dof, p_value=p, sap_psf_ratio=ratio)
        if p < 0.0027 and n_outliers >= 2 and len(z) >= 3:
            return TestResult(rung=self.name, status=TestStatus.FAIL, metrics=m, reason=f"season-dependent depth (p={p:.1e})")
        return TestResult(rung=self.name, status=TestStatus.PASS, metrics=m)
```

- [ ] **Step 4: Run** — `python -m pytest tests/unit/test_rungs_1_3.py -v` — Expected: 5 PASS

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat: rungs 1-3 detection, shape, stability" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"`

---

### Task 10: Rung 4 (catalogue novelty and context)

**Files:**
- Create: `src/skysift/vetting/rung4_novelty.py`
- Test: `tests/unit/test_rung4.py`

**Interfaces:**
- Produces: `Rung4Novelty(radius_arcsec=20.0)` (name `rung4_novelty`, cost 1.0). Queries, each through `bundle.fetch` with `service` names `exoarchive`, `vizier`, `gaia`: (a) NASA Exoplanet Archive TAP `cumulative` by `kepid` when the target has a KIC, else by cone; (b) `q1_q17_dr25_tce` likewise; (c) `toi` by cone; (d) Kepler EB catalogue VizieR `J/AJ/151/68` cone; (e) Gaia DR3 `vari_summary` and `nss_two_body_orbit` by `source_id`. FAIL with reason `known:<table>:<identifier>` when a row exists whose period matches the signal within 1 percent (or its 2x/0.5x alias); PASS with metrics listing rows found at other periods; UNDETERMINED with reason `outage:<service>` when any query raises `ServiceUnavailable` or `looks_like_error_document` returns text. Results are cached in `bundle.cache` keyed by `(service, identifier)` so neighbouring targets in one field share them.

- [ ] **Step 1: Failing tests**

```python
import pytest
from skysift.config import Thresholds
from skysift.models import LightCurve, Signal, Target, TestStatus
from skysift.net import ServiceUnavailable
from skysift.vetting.base import DataBundle
from skysift.vetting.rung4_novelty import Rung4Novelty
import numpy as np

SIG = Signal(target_id="kic-005689121", detector="box_dip", detector_version="1", period=13.7695, t0=136.3, duration=0.09, depth_ppm=1883, snr=18, sde=14, n_events=69)
TGT = Target(source="kbonus_bkg", target_id="kic-005689121", ra=285.907827, dec=40.941988, gaia_id="2103792461826460288", kic=5689121)
EMPTY = {"cumulative": b"kepid,kepoi_name,koi_period\n", "q1_q17_dr25_tce": b"kepid,tce_plnt_num,tce_period\n", "toi": b"tid,toi,pl_orbper\n",
         "J/AJ/151/68": b"", "vari_summary": b"source_id\n", "nss_two_body_orbit": b"source_id,period\n"}


def bundle(responses, raise_for=None):
    def fetch(url, *, params=None, service, **kw):
        if raise_for and raise_for in (service, (params or {}).get("query", ""), url):
            raise ServiceUnavailable(service)
        key = next(k for k in responses if k in (params or {}).get("query", "") or k in (params or {}).get("-source", "") or k in url)
        return responses[key]
    lc = LightCurve(np.arange(10.0), np.ones(10), np.ones(10), np.zeros(10, int), np.ones(10, int))
    return DataBundle(lc=lc, fnorm=np.ones(10), target=TGT, thresholds=Thresholds(), fetch=fetch)


def test_novel_when_all_empty():
    assert Rung4Novelty().run(SIG, bundle(EMPTY)).status == TestStatus.PASS


def test_known_koi_at_same_period_fails():
    resp = dict(EMPTY); resp["cumulative"] = b"kepid,kepoi_name,koi_period\n5689121,K99999.01,13.77\n"
    r = Rung4Novelty().run(SIG, bundle(resp))
    assert r.status == TestStatus.FAIL and r.reason.startswith("known:cumulative")


def test_outage_is_undetermined():
    r = Rung4Novelty().run(SIG, bundle(EMPTY, raise_for="vizier"))
    assert r.status == TestStatus.UNDETERMINED and r.reason.startswith("outage")


def test_error_document_is_undetermined():
    resp = dict(EMPTY); resp["toi"] = b'<VOTABLE><INFO name="QUERY_STATUS" value="ERROR">UsageFault: Internal error</INFO></VOTABLE>'
    r = Rung4Novelty().run(SIG, bundle(resp))
    assert r.status == TestStatus.UNDETERMINED and "UsageFault" in r.reason
```

- [ ] **Step 2: Run** — Expected: import error.

- [ ] **Step 3: Implement `vetting/rung4_novelty.py`**

```python
from __future__ import annotations

import csv
import io

from skysift.models import Signal, TestResult, TestStatus
from skysift.net import ServiceUnavailable, looks_like_error_document
from skysift.vetting.base import DataBundle

EXO_TAP = "https://exoplanetarchive.ipac.caltech.edu/TAP/sync"
VIZIER = "https://vizier.cds.unistra.fr/viz-bin/asu-tsv"
GAIA_TAP = "https://gea.esac.esa.int/tap-server/tap/sync"


def _csv_rows(data: bytes) -> list[dict]:
    text = data.decode(errors="replace")
    lines = [l for l in text.splitlines() if l and not l.startswith("#")]
    return list(csv.DictReader(io.StringIO("\n".join(lines))))


def _tsv_rows(data: bytes) -> list[dict]:
    lines = [l for l in data.decode(errors="replace").splitlines() if l and not l.startswith("#")]
    if len(lines) < 3: return []
    header = lines[0].split("\t")
    return [dict(zip(header, l.split("\t"))) for l in lines[3:] if l.strip()]


def _period_match(p_signal: float, p_cat: float, tol=0.01) -> bool:
    return any(abs(p_signal - k * p_cat) / (k * p_cat) < tol for k in (0.5, 1.0, 2.0)) if p_cat and p_cat > 0 else False


class Rung4Novelty:
    name = "rung4_novelty"
    cost = 1.0

    def __init__(self, radius_arcsec: float = 20.0):
        self.radius_deg = radius_arcsec / 3600.0

    def _get(self, b: DataBundle, service, url, params, key):
        if key in b.cache:
            return b.cache[key]
        data = b.fetch(url, params=params, service=service, store=b.store)
        err = looks_like_error_document(data)
        if err:
            raise ServiceUnavailable(f"{service}: {err}")
        b.cache[key] = data
        return data

    def run(self, s: Signal, b: DataBundle) -> TestResult:
        t = b.target
        cone = f"CONTAINS(POINT('ICRS',ra,dec),CIRCLE('ICRS',{t.ra},{t.dec},{self.radius_deg}))=1"
        checks = []
        if t.kic:
            checks.append(("exoarchive", "cumulative", f"select kepid,kepoi_name,koi_period from cumulative where kepid={t.kic}", "koi_period", "kepoi_name"))
            checks.append(("exoarchive", "q1_q17_dr25_tce", f"select kepid,tce_plnt_num,tce_period from q1_q17_dr25_tce where kepid={t.kic}", "tce_period", "kepid"))
        else:
            checks.append(("exoarchive", "cumulative", f"select kepid,kepoi_name,koi_period from cumulative where {cone}", "koi_period", "kepoi_name"))
            checks.append(("exoarchive", "q1_q17_dr25_tce", f"select kepid,tce_plnt_num,tce_period from q1_q17_dr25_tce where {cone}", "tce_period", "kepid"))
        checks.append(("exoarchive", "toi", f"select tid,toi,pl_orbper from toi where {cone}", "pl_orbper", "toi"))
        found, metrics = [], {}
        try:
            for service, table, query, pcol, idcol in checks:
                rows = _csv_rows(self._get(b, service, EXO_TAP, {"query": query, "format": "csv"}, (service, table, t.target_id)))
                metrics[table] = len(rows)
                for r in rows:
                    try: p = float(r.get(pcol) or 0)
                    except ValueError: p = 0.0
                    if _period_match(s.period, p):
                        return TestResult(rung=self.name, status=TestStatus.FAIL, metrics=metrics, reason=f"known:{table}:{r.get(idcol)}")
                    found.append((table, r.get(idcol), p))
            eb = _tsv_rows(self._get(b, "vizier", VIZIER, {"-source": "J/AJ/151/68", "-c": f"{t.ra} {t.dec:+}", "-c.rs": self.radius_deg * 3600, "-out.max": 50},
                                     ("vizier", "J/AJ/151/68", t.target_id)))
            metrics["kepler_eb"] = len(eb)
            for r in eb:
                try: p = float(r.get("Per") or r.get("Period") or 0)
                except ValueError: p = 0.0
                if _period_match(s.period, p):
                    return TestResult(rung=self.name, status=TestStatus.FAIL, metrics=metrics, reason=f"known:kepler_eb:{r.get('KIC')}")
            if t.gaia_id:
                for table in ("vari_summary", "nss_two_body_orbit"):
                    rows = _csv_rows(self._get(b, "gaia", GAIA_TAP, {"REQUEST": "doQuery", "LANG": "ADQL", "FORMAT": "csv",
                                                                     "QUERY": f"select * from gaiadr3.{table} where source_id={t.gaia_id}"},
                                               ("gaia", table, t.gaia_id)))
                    metrics[f"gaia_{table}"] = len(rows)
                    for r in rows:
                        try: p = float(r.get("period") or 0)
                        except ValueError: p = 0.0
                        if _period_match(s.period, p):
                            return TestResult(rung=self.name, status=TestStatus.FAIL, metrics=metrics, reason=f"known:gaia_{table}:{t.gaia_id}")
        except ServiceUnavailable as e:
            return TestResult(rung=self.name, status=TestStatus.UNDETERMINED, metrics=metrics, reason=f"outage: {e}")
        metrics["other_period_objects"] = found
        return TestResult(rung=self.name, status=TestStatus.PASS, metrics=metrics)
```

- [ ] **Step 4: Run** — `python -m pytest tests/unit/test_rung4.py -v` — Expected: 4 PASS

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat: rung 4 catalogue novelty with outage handling" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"`

---

### Task 11: Source registry and the KBonus-BKG adapter

**Files:**
- Create: `config/sources.yaml`, `src/skysift/sources/__init__.py`, `src/skysift/sources/base.py`, `src/skysift/sources/kbonus_bkg.py`
- Test: `tests/unit/test_sources.py`

**Interfaces:**
- Produces: `SourceInfo` (pydantic: `name, agency, status, n_lightcurves, baseline_days, cadence_min, depth_limit_ppm, fraction_searched, known_candidate_density, est_yield_per_10k, bytes_per_star, seconds_per_star, notes`); `load_registry(path=config/sources.yaml) -> dict[str, SourceInfo]`; `Source` protocol: `name`, `list_targets(filter: dict) -> Iterator[Target]`, `fetch_lightcurve(target, fetch, store) -> tuple[LightCurve, str sha256, str url]`; `KBonusBkg(blob, store, fetch=net.fetch)`: `catalog()` downloads and caches `hlsp_kbonus-bkg_kepler_kepler_source-catalog_kepler_v1.0_cat.fits` once (pinned artifact); `list_targets(filter)` honours `gmag_max`, `background_only` (kic is null), `psffrac_min`, `limit`; `lc_url(target) -> str` = `https://archive.stsci.edu/hlsps/kbonus-bkg/lcs/{id[:4]}/{id}/{file_name}` where `id` is `f"{kic:09d}"` or the Gaia source id string; `target_id` is `kic-NNNNNNNNN` or `gaia-dr3-SOURCEID`.

- [ ] **Step 1: Failing tests**

```python
import numpy as np
from astropy.io import fits
from astropy.table import Table
from skysift.models import Target
from skysift.sources.base import load_registry
from skysift.sources.kbonus_bkg import KBonusBkg, lc_url


def test_registry_has_live_kbonus():
    reg = load_registry()
    assert reg["kbonus_bkg"].status == "live" and reg["kbonus_bkg"].n_lightcurves == 606900
    assert all(v.est_yield_per_10k >= 0 for v in reg.values())


def test_lc_url_for_kic_and_gaia():
    assert lc_url(Target(source="kbonus_bkg", target_id="kic-005689121", ra=0, dec=0, kic=5689121,
                         meta={"file_name": "hlsp_kbonus-bkg_kepler_kepler_kic-005689121_kepler_v1.0_lc.fits"})) == \
        "https://archive.stsci.edu/hlsps/kbonus-bkg/lcs/0056/005689121/hlsp_kbonus-bkg_kepler_kepler_kic-005689121_kepler_v1.0_lc.fits"
    assert lc_url(Target(source="kbonus_bkg", target_id="gaia-dr3-2049134501869730432", ra=0, dec=0, gaia_id="2049134501869730432",
                         meta={"file_name": "hlsp_kbonus-bkg_kepler_kepler_gaia-dr3-2049134501869730432_kepler_v1.0_lc.fits"})) == \
        "https://archive.stsci.edu/hlsps/kbonus-bkg/lcs/2049/2049134501869730432/hlsp_kbonus-bkg_kepler_kepler_gaia-dr3-2049134501869730432_kepler_v1.0_lc.fits"


def test_list_targets_filters(tmp_path):
    tab = Table({"gaia_designation": ["Gaia DR3 1", "Gaia DR3 2", "Gaia DR3 3"], "ra": [1.0, 2.0, 3.0], "dec": [1.0, 2.0, 3.0],
                 "phot_g_mean_mag": [15.0, 18.5, 17.0], "psffrac": [0.9, 0.9, 0.3], "kic": [123, -1, -1],
                 "file_name": ["a.fits", "b.fits", "c.fits"], "tpf_org": [1, 1, 1], "crowdsap": [0.9, 0.8, 0.7]})
    p = tmp_path / "cat.fits"; tab.write(p, format="fits")
    src = KBonusBkg(blob=None, store=None, catalog_path=p)
    ids = [t.target_id for t in src.list_targets({"gmag_max": 18.0, "background_only": True, "psffrac_min": 0.5})]
    assert ids == []   # 2 is too faint, 3 has low psffrac, 1 is a KIC target
    ids = [t.target_id for t in src.list_targets({"gmag_max": 19.0, "background_only": True, "psffrac_min": 0.5})]
    assert ids == ["gaia-dr3-2"]
```

- [ ] **Step 2: Run** — Expected: import errors.

- [ ] **Step 3: Implement**

`config/sources.yaml` (numbers from the KBonus-BKG README, NASA Exoplanet Archive holdings page and the 13 October verification; `est_yield_per_10k` is a prior to be replaced by injection-recovery in M5):
```yaml
kbonus_bkg:
  agency: NASA/MAST HLSP (Martinez-Palomera, Hedges, Dotson 2023)
  status: live
  n_lightcurves: 606900
  baseline_days: 1430
  cadence_min: 29.4
  depth_limit_ppm: 300
  fraction_searched: 0.33      # the 200,352 Kepler targets were searched by the Kepler pipeline; 406,548 background sources were not
  known_candidate_density: 0.012
  est_yield_per_10k: 2.0
  bytes_per_star: 9000000
  seconds_per_star: 4
  notes: PSF-deblended; pixel files available for localisation; KIC 5689121 found here.
kepler:
  agency: NASA/MAST
  status: planned
  n_lightcurves: 200000
  baseline_days: 1430
  cadence_min: 29.4
  depth_limit_ppm: 100
  fraction_searched: 1.0
  known_candidate_density: 0.02
  est_yield_per_10k: 0.2
  bytes_per_star: 6000000
  seconds_per_star: 4
  notes: Fully searched; useful for EB and variability catalogues and cross-checks.
k2:
  agency: NASA/MAST (EVEREST, K2SFF HLSP)
  status: planned
  n_lightcurves: 400000
  baseline_days: 80
  cadence_min: 29.4
  depth_limit_ppm: 300
  fraction_searched: 0.8
  known_candidate_density: 0.003
  est_yield_per_10k: 0.5
  bytes_per_star: 2000000
  seconds_per_star: 2
  notes: Short campaigns; period range limited to < 40 d.
tess_lc:
  agency: NASA/MAST (SPOC, TESS-SPOC, QLP, TGLC, TARS)
  status: planned
  n_lightcurves: 20000000
  baseline_days: 27
  cadence_min: 10
  depth_limit_ppm: 1000
  fraction_searched: 0.6
  known_candidate_density: 0.0004
  est_yield_per_10k: 0.3
  bytes_per_star: 2000000
  seconds_per_star: 1
  notes: Multi-sector stacking needed; faint stars noisy.
exoarchive_bulk:
  agency: NASA Exoplanet Archive bulk (SuperWASP 18M, KELT 229k, HATNet, CoRoT 149k, TrES, XO)
  status: planned
  n_lightcurves: 18500000
  baseline_days: 1500
  cadence_min: 10
  depth_limit_ppm: 5000
  fraction_searched: 0.9
  known_candidate_density: 0.0002
  est_yield_per_10k: 0.1
  bytes_per_star: 500000
  seconds_per_star: 1
  notes: Ground-based; giant planets and EBs only.
gaia_epoch:
  agency: ESA Gaia (DR3 epoch photometry for 10M variables; DR4 all sources, expected December 2026)
  status: planned
  n_lightcurves: 10000000
  baseline_days: 1000
  cadence_min: 43200
  depth_limit_ppm: 10000
  fraction_searched: 0.1
  known_candidate_density: 0.0001
  est_yield_per_10k: 0.05
  bytes_per_star: 20000
  seconds_per_star: 0.5
  notes: Sparse sampling; EBs and deep transits only.
ztf:
  agency: Caltech/IPAC IRSA
  status: planned
  n_lightcurves: 1000000000
  baseline_days: 2500
  cadence_min: 2880
  depth_limit_ppm: 10000
  fraction_searched: 0.2
  known_candidate_density: 0.0001
  est_yield_per_10k: 0.02
  bytes_per_star: 100000
  seconds_per_star: 0.5
  notes: Cone API unreliable as of October 2026; use object-id retrieval.
```

`sources/base.py`:
```python
from __future__ import annotations

from pathlib import Path
from typing import Iterator, Protocol

import yaml
from pydantic import BaseModel

from skysift.models import LightCurve, Target

REGISTRY_YAML = Path(__file__).resolve().parents[3] / "config" / "sources.yaml"


class SourceInfo(BaseModel):
    name: str
    agency: str
    status: str
    n_lightcurves: int
    baseline_days: float
    cadence_min: float
    depth_limit_ppm: float
    fraction_searched: float
    known_candidate_density: float
    est_yield_per_10k: float
    bytes_per_star: int
    seconds_per_star: float
    notes: str = ""


def load_registry(path: Path = REGISTRY_YAML) -> dict[str, SourceInfo]:
    raw = yaml.safe_load(path.read_text())
    return {k: SourceInfo(name=k, **v) for k, v in raw.items()}


class Source(Protocol):
    name: str

    def list_targets(self, filter: dict) -> Iterator[Target]: ...
    def fetch_lightcurve(self, target: Target) -> tuple[LightCurve, str, str]: ...
```

`sources/kbonus_bkg.py`:
```python
from __future__ import annotations

import hashlib
import io
from pathlib import Path
from typing import Iterator

from astropy.table import Table

from skysift import net
from skysift.lc.kbonus import parse_kbonus_fits
from skysift.models import LightCurve, Target

BASE = "https://archive.stsci.edu/hlsps/kbonus-bkg"
CATALOG_URL = f"{BASE}/catalogs/hlsp_kbonus-bkg_kepler_kepler_source-catalog_kepler_v1.0_cat.fits"


def _id_string(t: Target) -> str:
    return f"{t.kic:09d}" if t.kic and t.kic > 0 else str(t.gaia_id)


def lc_url(t: Target) -> str:
    sid = _id_string(t)
    return f"{BASE}/lcs/{sid[:4]}/{sid}/{t.meta['file_name']}"


class KBonusBkg:
    name = "kbonus_bkg"

    def __init__(self, blob, store, fetch=net.fetch, catalog_path: Path | None = None):
        self.blob, self.store, self.fetch, self._catalog_path = blob, store, fetch, catalog_path

    def catalog(self) -> Table:
        if self._catalog_path is None:
            data = self.fetch(CATALOG_URL, service="mast", store=self.store, timeout=600)
            sha, loc = self.blob.put(data, "kbonus_source_catalog.fits")
            if self.store: self.store.add_artifact(sha, len(data), CATALOG_URL, loc, None, pinned=True)
            self._catalog_path = Path(loc)
        return Table.read(self._catalog_path, format="fits")

    def list_targets(self, filter: dict) -> Iterator[Target]:
        tab = self.catalog()
        gmag_max = filter.get("gmag_max", 19.0); psffrac_min = filter.get("psffrac_min", 0.5)
        background_only = filter.get("background_only", True); limit = filter.get("limit")
        n = 0
        for row in tab:
            kic = int(row["kic"]) if row["kic"] is not None and int(row["kic"]) > 0 else None
            if background_only and kic: continue
            if float(row["phot_g_mean_mag"]) > gmag_max: continue
            if float(row["psffrac"]) < psffrac_min: continue
            gaia = str(row["gaia_designation"]).replace("Gaia DR3 ", "").strip()
            tid = f"kic-{kic:09d}" if kic else f"gaia-dr3-{gaia}"
            yield Target(source=self.name, target_id=tid, ra=float(row["ra"]), dec=float(row["dec"]), gaia_id=gaia, kic=kic,
                         gmag=float(row["phot_g_mean_mag"]),
                         meta={"file_name": str(row["file_name"]), "tpf_org": int(row["tpf_org"]), "crowdsap": float(row["crowdsap"]),
                               "psffrac": float(row["psffrac"])})
            n += 1
            if limit and n >= limit: return

    def fetch_lightcurve(self, target: Target) -> tuple[LightCurve, str, str]:
        url = lc_url(target)
        data = self.fetch(url, service="mast", store=self.store, timeout=300)
        sha = hashlib.sha256(data).hexdigest()
        if self.blob is not None:
            _, loc = self.blob.put(data, Path(url).name)
            if self.store: self.store.add_artifact(sha, len(data), url, loc, target.target_id)
        lc = parse_kbonus_fits(data); lc.meta["target_id"] = target.target_id
        return lc, sha, url
```

`sources/__init__.py`: `from .base import Source, SourceInfo, load_registry  # noqa: F401`

- [ ] **Step 4: Run** — `python -m pytest tests/unit/test_sources.py -v` — Expected: 3 PASS

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat: source registry and KBonus-BKG adapter" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"`

---

### Task 12: Pipeline (priority, job keys, worker pools, checkpointing, retention)

**Files:**
- Create: `src/skysift/pipeline.py`
- Test: `tests/unit/test_pipeline.py`

**Interfaces:**
- Produces: `priority_score(target: Target) -> float` (higher first: +2 if 15 < gmag < 18.5, +1 if `psffrac` >= 0.8, +1 if `crowdsap` >= 0.8, +1 if no KIC (never searched), -3 if gmag > 19); `DEFAULT_LADDER_VERSION = "1"`; `build_ladder(version="1") -> Ladder` (rungs 1 to 4); `process_target(target, source, store, blob, settings, detector_name="box_dip", ladder=None) -> dict` (fetch, detrend, detect, vet every signal, write rows, set signal `final_status`, release retention; returns summary dict with `signals`, `outcome_counts`, `error`); `run(source, store, blob, settings, filter: dict, detector_name="box_dip", max_targets=None, progress=None) -> dict` (enumerates, scores, skips done job keys, runs an I/O ThreadPoolExecutor of `workers_io` for fetches feeding a ProcessPoolExecutor of `workers_cpu` for detect+vet; on Windows the process pool uses `spawn`, so `process_target` takes only picklable arguments: the function `_cpu_work(payload: bytes, target_json: str, settings_json: str)` runs detect and vet and returns JSON; the main process writes rows). `DiskBudgetExceeded` propagates and stops the run.

- [ ] **Step 1: Failing test**

```python
import json
import numpy as np
from skysift.config import Thresholds, load_settings
from skysift.models import Target
from skysift.pipeline import build_ladder, priority_score, run, _cpu_work
from skysift.store.blob import LocalBlob
from skysift.store.db import Store, make_job_key
from tests.synthetic import make_lightcurve, inject_transit


class FakeSource:
    name = "fake"
    def __init__(self, lcs): self.lcs = lcs
    def list_targets(self, filter):
        for tid in self.lcs:
            yield Target(source="fake", target_id=tid, ra=0, dec=0, gmag=16.5, meta={"psffrac": 0.9, "crowdsap": 0.9})
    def fetch_lightcurve(self, target):
        return self.lcs[target.target_id], "0" * 64, f"https://fake/{target.target_id}"


def test_priority_prefers_faint_unsearched_clean():
    a = Target(source="x", target_id="a", ra=0, dec=0, gmag=16.5, meta={"psffrac": 0.9, "crowdsap": 0.9})
    b = Target(source="x", target_id="b", ra=0, dec=0, gmag=19.5, kic=1, meta={"psffrac": 0.3, "crowdsap": 0.3})
    assert priority_score(a) > priority_score(b)


def test_run_end_to_end_with_fake_source_and_resume(settings, monkeypatch):
    monkeypatch.setenv("SKYSIFT_WORKERS_CPU", "1"); s = load_settings(); s.data_dir.mkdir(exist_ok=True)
    store = Store(s.database_url); store.init_schema(); blob = LocalBlob(s.data_dir / "blobs")
    lcs = {"planet": inject_transit(make_lightcurve(seed=1, noise_ppm=800), 13.769, 136.3, 0.09, 2000), "noise": make_lightcurve(seed=9)}
    src = FakeSource(lcs)
    # rung 4 would hit the network: with_novelty=False runs rungs 1-3 only (a monkeypatch would not reach the worker process)
    out = run(src, store, blob, s, filter={}, max_targets=2, with_novelty=False)
    c = store.counts()
    assert c["targets"] == 2 and c["runs"] == 2 and c["signals"] >= 1 and c["tests"] >= 3
    assert store.job_done(make_job_key("planet", "box_dip", "1", "1"))
    # stream retention: nothing unpinned is left on disk
    assert store.unpinned_artifacts() == []
    # resume: second run skips both
    out2 = run(src, store, blob, s, filter={}, max_targets=2)
    assert out2["skipped"] == 2 and store.counts()["signals"] == c["signals"]
```

- [ ] **Step 2: Run** — Expected: import error.

- [ ] **Step 3: Implement `pipeline.py`**

```python
from __future__ import annotations

import json
import logging
import pickle
import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed

from skysift.config import Settings, Thresholds
from skysift.detectors.base import get_detector
from skysift.lc.ops import detrend
from skysift.models import LightCurve, Signal, Target, TestResult
from skysift.store.blob import Blob
from skysift.store.db import Store, make_job_key
from skysift.store.retention import Retention, RetentionPolicy
from skysift.vetting.base import DataBundle, Ladder
from skysift.vetting.rung1_detection import Rung1Detection
from skysift.vetting.rung2_shape import Rung2Shape
from skysift.vetting.rung3_stability import Rung3Stability
from skysift.vetting.rung4_novelty import Rung4Novelty

log = logging.getLogger(__name__)
DEFAULT_LADDER_VERSION = "1"


def priority_score(t: Target) -> float:
    s = 0.0
    g = t.gmag if t.gmag is not None else 17.0
    if 15 < g < 18.5: s += 2
    if g > 19: s -= 3
    if t.meta.get("psffrac", 0) >= 0.8: s += 1
    if t.meta.get("crowdsap", 0) >= 0.8: s += 1
    if not t.kic: s += 1
    return s


def build_ladder(version: str = DEFAULT_LADDER_VERSION, with_novelty: bool = True) -> Ladder:
    rungs = [Rung1Detection(), Rung2Shape(), Rung3Stability()]
    if with_novelty: rungs.append(Rung4Novelty())
    return Ladder(rungs, version)


def _cpu_work(lc_pickle: bytes, target_json: str, thresholds_json: str, detector_name: str, ladder_version: str,
              with_novelty: bool) -> str:
    """Runs in a worker process: detect + vet. Returns JSON of signals and their test results."""
    lc: LightCurve = pickle.loads(lc_pickle)
    target = Target.model_validate_json(target_json); th = Thresholds.model_validate_json(thresholds_json)
    fn = detrend(lc); fn_alt = detrend(lc, flux=lc.flux_alt) if lc.flux_alt is not None else None
    det = get_detector(detector_name)
    signals = det.detect(lc, fn, th)
    ladder = build_ladder(ladder_version, with_novelty=with_novelty)
    bundle = DataBundle(lc=lc, fnorm=fn, fnorm_alt=fn_alt, target=target, thresholds=th)
    out = []
    for sig in signals:
        sig.target_id = target.target_id
        timings = []
        outcome, results = ladder.run(sig, bundle, on_result=lambda r, dt: timings.append(dt))
        out.append({"signal": sig.model_dump(), "outcome": outcome,
                    "tests": [r.model_dump() for r in results], "timings": timings})
    return json.dumps(out, default=_json_default)


def _json_default(o):
    """numpy scalars and arrays inside rung metrics must survive the trip back to the main process."""
    import numpy as np
    if isinstance(o, np.generic): return o.item()
    if isinstance(o, np.ndarray): return o.tolist()
    return str(o)


def _write_results(store: Store, run_id: int, payload: str) -> dict:
    counts = {"pass": 0, "fail": 0, "undetermined": 0}
    for item in json.loads(payload):
        sig = Signal(**item["signal"]); sid = store.add_signal(sig, run_id)
        for r, dt in zip(item["tests"], item["timings"]):
            store.add_test(sid, TestResult(**r), dt)
        final = {"pass": "candidate_stage1", "fail": "rejected", "undetermined": "undetermined"}[item["outcome"]]
        store.set_signal_status(sid, final)
        counts[item["outcome"]] += 1
    return counts


def run(source, store: Store, blob: Blob, settings: Settings, filter: dict, detector_name: str = "box_dip",
        max_targets: int | None = None, progress=None, with_novelty: bool = True) -> dict:
    det = get_detector(detector_name)
    retention = Retention(RetentionPolicy.parse(settings.retention), store, blob)
    ladder_version = DEFAULT_LADDER_VERSION
    th_json = settings.thresholds.model_dump_json()
    targets = []
    for t in source.list_targets(filter):
        key = make_job_key(t.target_id, det.name, det.version, ladder_version)
        store.upsert_target(t)
        if store.job_done(key):
            targets.append((t, key, True)); continue
        targets.append((t, key, False))
        if max_targets and sum(1 for _, _, d in targets if not d) >= max_targets: break
    todo = sorted([x for x in targets if not x[2]], key=lambda x: -priority_score(x[0]))
    skipped = sum(1 for x in targets if x[2])
    summary = {"processed": 0, "skipped": skipped, "errors": 0, "signals": 0, "outcomes": {"pass": 0, "fail": 0, "undetermined": 0}}
    start = time.time()

    def fetch_one(t: Target):
        lc, sha, url = source.fetch_lightcurve(t)
        return t, pickle.dumps(lc)

    with ThreadPoolExecutor(settings.workers_io) as io_pool, ProcessPoolExecutor(settings.workers_cpu) as cpu_pool:
        fetches = {io_pool.submit(fetch_one, t): (t, key) for t, key, _ in todo}
        cpu_jobs = {}
        for fut in as_completed(fetches):
            t, key = fetches[fut]
            try:
                t, lc_pickle = fut.result()
            except Exception as e:
                log.warning("fetch failed for %s: %r", t.target_id, e)
                rid = store.start_run(key, t.target_id, det.name, det.version, ladder_version); store.finish_run(rid, "error", repr(e))
                summary["errors"] += 1; continue
            rid = store.start_run(key, t.target_id, det.name, det.version, ladder_version)
            cpu_jobs[cpu_pool.submit(_cpu_work, lc_pickle, t.model_dump_json(), th_json, detector_name, ladder_version, with_novelty)] = (t, rid)
        for fut in as_completed(cpu_jobs):
            t, rid = cpu_jobs[fut]
            try:
                counts = _write_results(store, rid, fut.result())
                for k, v in counts.items(): summary["outcomes"][k] += v
                summary["signals"] += sum(counts.values())
                store.finish_run(rid, "done"); store.set_target_state(t.target_id, "done")
                if counts["pass"] or counts["undetermined"]:
                    store.pin_target_artifacts(t.target_id)      # keep evidence for anything still alive
                retention.release_target(t.target_id)
                retention.enforce_budget()                      # raises DiskBudgetExceeded -> stops the run
                summary["processed"] += 1
            except Exception as e:
                from skysift.store.retention import DiskBudgetExceeded
                if isinstance(e, DiskBudgetExceeded): raise
                log.exception("processing failed for %s", t.target_id)
                store.finish_run(rid, "error", repr(e)); summary["errors"] += 1
            if progress: progress(summary)
    summary["seconds"] = time.time() - start
    return summary
```

- [ ] **Step 4: Run** — `python -m pytest tests/unit/test_pipeline.py -v` — Expected: 2 PASS (on Windows the process pool needs the test module importable; `tests/__init__.py` must exist: create it empty, and `tests/synthetic.py` is imported inside the worker only through pickled data, so no issue).

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat: pipeline with worker pools, checkpointing and retention" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"`

---

### Task 13: CLI

**Files:**
- Create: `src/skysift/cli.py`
- Test: `tests/unit/test_cli.py`

**Interfaces:**
- Produces: typer app `skysift` with commands: `sources` (table of registry with status, n_lightcurves, baseline, depth limit, fraction searched, est yield per 10k, s per star, GB per 10k); `run --source NAME [--gmag-max 19] [--background-only/--all] [--psffrac-min 0.5] [--limit N] [--detector box_dip] [--workers-io N] [--workers-cpu N] [--retention stream]`; `status` (counts, outcomes, throughput from `runs`, cache bytes vs budget, last five errors); `vet --source NAME --target-id ID` (single target, prints every rung's metrics as JSON, keeps files).

- [ ] **Step 1: Failing test**

```python
from typer.testing import CliRunner
from skysift.cli import app


def test_sources_lists_registry():
    r = CliRunner().invoke(app, ["sources"])
    assert r.exit_code == 0 and "kbonus_bkg" in r.stdout and "live" in r.stdout


def test_status_on_empty_db(settings):
    r = CliRunner().invoke(app, ["status"])
    assert r.exit_code == 0 and "targets" in r.stdout
```

- [ ] **Step 2: Run** — Expected: import error.

- [ ] **Step 3: Implement `cli.py`**

```python
from __future__ import annotations

import json
import logging

import typer
from rich.console import Console
from rich.table import Table as RTable

from skysift.config import load_settings
from skysift.sources.base import load_registry
from skysift.store.blob import blob_from_url
from skysift.store.db import Store
from skysift.store.retention import Retention, RetentionPolicy

app = typer.Typer(help="SkySift: archival time-series search and vetting engine")
console = Console()


def _ctx(retention: str | None = None, workers_io: int | None = None, workers_cpu: int | None = None):
    s = load_settings()
    if retention: s.retention = retention
    if workers_io: s.workers_io = workers_io
    if workers_cpu: s.workers_cpu = workers_cpu
    s.data_dir.mkdir(parents=True, exist_ok=True)
    store = Store(s.database_url); store.init_schema()
    return s, store, blob_from_url(s.blob_url)


def _source(name: str, store, blob):
    if name == "kbonus_bkg":
        from skysift.sources.kbonus_bkg import KBonusBkg
        return KBonusBkg(blob, store)
    raise typer.BadParameter(f"source {name} is not live yet; see `skysift sources`")


@app.command()
def sources():
    """List data sources with size, depth limit, prior yield and cost."""
    reg = load_registry()
    t = RTable(title="SkySift data sources")
    for col in ("name", "status", "agency", "light curves", "baseline d", "depth ppm", "searched", "yield/10k", "s/star", "GB/10k"):
        t.add_column(col)
    for s in reg.values():
        t.add_row(s.name, s.status, s.agency, f"{s.n_lightcurves:,}", f"{s.baseline_days:.0f}", f"{s.depth_limit_ppm:.0f}",
                  f"{s.fraction_searched:.0%}", f"{s.est_yield_per_10k:.1f}", f"{s.seconds_per_star:.1f}", f"{s.bytes_per_star * 1e4 / 1e9:.1f}")
    console.print(t)


@app.command()
def run(source: str = typer.Option(...), gmag_max: float = 19.0, background_only: bool = True, psffrac_min: float = 0.5,
        limit: int | None = None, detector: str = "box_dip", workers_io: int | None = None, workers_cpu: int | None = None,
        retention: str | None = None, verbose: bool = False):
    """Sweep a source: fetch, detect, vet, store, release files."""
    logging.basicConfig(level=logging.INFO if verbose else logging.WARNING, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    s, store, blob = _ctx(retention, workers_io, workers_cpu)
    from skysift import pipeline
    src = _source(source, store, blob)
    summary = pipeline.run(src, store, blob, s, filter={"gmag_max": gmag_max, "background_only": background_only,
                                                       "psffrac_min": psffrac_min, "limit": limit},
                           detector_name=detector, max_targets=limit,
                           progress=lambda sm: console.print(f"processed {sm['processed']} signals {sm['signals']} outcomes {sm['outcomes']}", end="\r"))
    console.print(json.dumps(summary, indent=1))


@app.command()
def status():
    """Counts, outcomes, throughput, cache usage, recent errors."""
    s, store, blob = _ctx()
    c = store.counts()
    ret = Retention(RetentionPolicy.parse(s.retention), store, blob)
    from sqlalchemy import select
    from skysift.store.db import RunRow, SignalRow
    with store.session() as ses:
        errors = ses.scalars(select(RunRow).where(RunRow.status == "error").order_by(RunRow.id.desc()).limit(5)).all()
        done = ses.scalars(select(RunRow).where(RunRow.status == "done")).all()
        outcomes = {}
        for row in ses.scalars(select(SignalRow)): outcomes[row.final_status] = outcomes.get(row.final_status, 0) + 1
        hours = max(1e-9, sum((r.finished - r.started).total_seconds() for r in done if r.finished) / 3600)
        console.print(json.dumps({"counts": c, "signal_outcomes": outcomes, "targets_per_hour": round(len(done) / hours, 1),
                                  "cache_bytes": ret.cached_bytes(), "retention": s.retention,
                                  "last_errors": [{"target": e.target_id, "error": e.error[:200]} for e in errors]}, indent=1, default=str))


@app.command()
def vet(source: str = typer.Option(...), target_id: str = typer.Option(...), detector: str = "box_dip"):
    """Run detection and the full ladder on one target and print every metric (files are kept)."""
    s, store, blob = _ctx(retention="keep")
    src = _source(source, store, blob)
    from skysift.pipeline import _cpu_work
    import pickle
    target = next(t for t in src.list_targets({"gmag_max": 99, "background_only": False, "psffrac_min": 0}) if t.target_id == target_id)
    lc, sha, url = src.fetch_lightcurve(target)
    console.print(f"fetched {url} sha256={sha}")
    out = _cpu_work(pickle.dumps(lc), target.model_dump_json(), s.thresholds.model_dump_json(), detector, "1", True)
    console.print(json.dumps(json.loads(out), indent=1))


if __name__ == "__main__":
    app()
```

- [ ] **Step 4: Run** — `python -m pytest tests/unit/test_cli.py -v` — Expected: 2 PASS; also `skysift sources` prints the table.

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat: CLI sources/run/status/vet" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"`

---

### Task 14: Golden test on KIC 5689121 (light-curve part)

**Files:**
- Create: `tests/golden/__init__.py`, `tests/golden/fixtures.py`, `tests/golden/test_kic5689121_lc.py`

**Interfaces:**
- Produces: `fixture_bytes(name) -> bytes` that downloads a known file to `tests/fixtures/cache/<name>` on first use, verifies SHA-256 against a hard-coded table, and `pytest.skip`s when offline. Table: `kbonus_005689121.fits` sha `44980afb7843a4507c898eb1ce5d5733029a0f4658f6b96d6196db49df37c06f` from `https://archive.stsci.edu/hlsps/kbonus-bkg/lcs/0056/005689121/hlsp_kbonus-bkg_kepler_kepler_kic-005689121_kepler_v1.0_lc.fits`; `kbonus_005689116.fits` sha `67ca0e7545f2ddb8bf051b30dabdf63a87fa6acc86d513f2ade5b1e6812382d3`.

- [ ] **Step 1: Write the golden test (it is the spec; it should fail only if something regressed)**

`tests/golden/fixtures.py`:
```python
import hashlib
from pathlib import Path

import pytest

CACHE = Path(__file__).resolve().parents[1] / "fixtures" / "cache"
FIXTURES = {
    "kbonus_005689121.fits": ("https://archive.stsci.edu/hlsps/kbonus-bkg/lcs/0056/005689121/hlsp_kbonus-bkg_kepler_kepler_kic-005689121_kepler_v1.0_lc.fits",
                              "44980afb7843a4507c898eb1ce5d5733029a0f4658f6b96d6196db49df37c06f"),
    "kbonus_005689116.fits": ("https://archive.stsci.edu/hlsps/kbonus-bkg/lcs/0056/005689116/hlsp_kbonus-bkg_kepler_kepler_kic-005689116_kepler_v1.0_lc.fits",
                              "67ca0e7545f2ddb8bf051b30dabdf63a87fa6acc86d513f2ade5b1e6812382d3"),
}


def fixture_bytes(name: str) -> bytes:
    url, sha = FIXTURES[name]
    CACHE.mkdir(parents=True, exist_ok=True); p = CACHE / name
    if not p.exists():
        try:
            from skysift.net import fetch
            p.write_bytes(fetch(url, service="mast", timeout=300))
        except Exception as e:
            pytest.skip(f"fixture {name} unavailable offline: {e!r}")
    data = p.read_bytes()
    assert hashlib.sha256(data).hexdigest() == sha, f"fixture {name} hash mismatch"
    return data
```

`tests/golden/test_kic5689121_lc.py`:
```python
import pytest
from skysift.config import Thresholds
from skysift.detectors.box_dip import BoxDip
from skysift.lc.kbonus import parse_kbonus_fits
from skysift.lc.ops import detrend, event_depths
from skysift.models import Target, TestStatus
from skysift.vetting.base import DataBundle
from skysift.vetting.rung1_detection import Rung1Detection
from skysift.vetting.rung2_shape import Rung2Shape
from skysift.vetting.rung3_stability import Rung3Stability
from tests.golden.fixtures import fixture_bytes

pytestmark = pytest.mark.golden
P_PUB, T0_PUB = 13.769448251493595, 2455630.245430646 - 2454833.0


def test_kic5689121_signal_and_rungs_1_to_3():
    lc = parse_kbonus_fits(fixture_bytes("kbonus_005689121.fits"))
    assert lc.meta["kic"] == 5689121 and lc.meta["gaia_id"] == "2103792461826460288"
    fn = detrend(lc)
    sig = BoxDip().detect(lc, fn, Thresholds())[0]
    assert abs(sig.period - 13.7695) < 0.002 and sig.sde > 9
    ed = event_depths(lc.time, fn, lc.quarter, P_PUB, T0_PUB, 0.0958)
    assert abs(ed.mean - 1883) < 150 and ed.n >= 65
    tgt = Target(source="kbonus_bkg", target_id="kic-005689121", ra=285.907827, dec=40.941988, gaia_id="2103792461826460288", kic=5689121,
                 meta={"mass": 0.505, "radius": 0.507})
    b = DataBundle(lc=lc, fnorm=fn, fnorm_alt=detrend(lc, flux=lc.flux_alt), target=tgt, thresholds=Thresholds())
    assert Rung1Detection().run(sig, b).status == TestStatus.PASS
    r2 = Rung2Shape().run(sig, b); assert r2.status == TestStatus.PASS and r2.metrics["parity_sigma"] < 2
    r3 = Rung3Stability().run(sig, b); assert r3.status == TestStatus.PASS


def test_neighbour_kic5689116_shows_no_dip():
    lc = parse_kbonus_fits(fixture_bytes("kbonus_005689116.fits"))
    ed = event_depths(lc.time, detrend(lc), lc.quarter, P_PUB, T0_PUB, 0.0958)
    assert abs(ed.mean) < 3 * ed.err_scaled and ed.mean < 150
```

- [ ] **Step 2: Run** — `python -m pytest tests/golden -v -m golden` — Expected: 2 PASS online (2 SKIP offline). If the first assertion on depth fails by a small margin, the cause is the duration window: use `sig.duration` from the detector instead of 0.0958 and re-check; do not widen the tolerance.

- [ ] **Step 3: Commit** — `git add -A && git commit -m "test: golden light-curve test on KIC 5689121" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"`

---

### Task 15: Container, scheduling and README

**Files:**
- Create: `docker/Dockerfile`, `docker/docker-compose.yml`, `deploy/windows/register-task.ps1`, `deploy/aws/README.md`, `deploy/aws/batch-job-definition.json`
- Modify: `README.md`
- Test: `tests/unit/test_cli.py` gains `test_help_runs`; the Docker build is verified manually with the command in step 4.

- [ ] **Step 1: Add the test**

```python
def test_help_runs():
    r = CliRunner().invoke(app, ["--help"])
    assert r.exit_code == 0 and "run" in r.stdout and "status" in r.stdout
```

- [ ] **Step 2: Write the files**

`docker/Dockerfile`:
```dockerfile
FROM python:3.12-slim
RUN useradd -m skysift
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
COPY config ./config
RUN pip install --no-cache-dir ".[s3]"
USER skysift
ENV SKYSIFT_DATA_DIR=/data SKYSIFT_BLOB_URL=file:///data/blobs SKYSIFT_DATABASE_URL=sqlite:////data/engine.db SKYSIFT_RETENTION=stream
VOLUME ["/data"]
ENTRYPOINT ["skysift"]
CMD ["--help"]
```

`docker/docker-compose.yml`:
```yaml
services:
  skysift:
    build: {context: .., dockerfile: docker/Dockerfile}
    volumes: ["../data:/data"]
    env_file: ["../.env"]
    command: ["run", "--source", "kbonus_bkg", "--limit", "100"]
```

`deploy/windows/register-task.ps1`:
```powershell
param([string]$Source = "kbonus_bkg", [int]$Limit = 2000, [string]$At = "02:00")
$py = Join-Path $PSScriptRoot "..\..\.venv\Scripts\skysift.exe"
$action = New-ScheduledTaskAction -Execute $py -Argument "run --source $Source --limit $Limit" -WorkingDirectory (Resolve-Path "$PSScriptRoot\..\..")
$trigger = New-ScheduledTaskTrigger -Daily -At $At
Register-ScheduledTask -TaskName "SkySift nightly sweep" -Action $action -Trigger $trigger -Description "SkySift archival sweep" -Force
```

`deploy/aws/batch-job-definition.json`:
```json
{
  "jobDefinitionName": "skysift-sweep",
  "type": "container",
  "platformCapabilities": ["FARGATE"],
  "containerProperties": {
    "image": "ACCOUNT.dkr.ecr.REGION.amazonaws.com/skysift:latest",
    "command": ["run", "--source", "kbonus_bkg", "--limit", "5000", "--workers-cpu", "4"],
    "resourceRequirements": [{"type": "VCPU", "value": "4"}, {"type": "MEMORY", "value": "8192"}],
    "environment": [
      {"name": "SKYSIFT_BLOB_URL", "value": "s3://YOUR-BUCKET/blobs"},
      {"name": "SKYSIFT_DATABASE_URL", "value": "postgresql+psycopg://USER:PASS@HOST/skysift"},
      {"name": "SKYSIFT_RETENTION", "value": "stream"}
    ],
    "executionRoleArn": "arn:aws:iam::ACCOUNT:role/ecsTaskExecutionRole",
    "jobRoleArn": "arn:aws:iam::ACCOUNT:role/skysift-job"
  }
}
```

`deploy/aws/README.md`: steps: build and push image to ECR; create S3 bucket; optional RDS Postgres (`pip install psycopg` is included when `DATABASE_URL` starts with `postgresql`); register the job definition above; submit with `aws batch submit-job`; the job role needs `s3:GetObject/PutObject/DeleteObject/ListBucket` on the bucket and nothing else; secrets via Secrets Manager injected as environment variables.

`README.md`: purpose, quick start (`python -m venv .venv`, `pip install -e ".[dev]"`, `skysift sources`, `skysift run --source kbonus_bkg --limit 50`, `skysift status`, `skysift vet --source kbonus_bkg --target-id kic-005689121`), configuration table, retention modes, Docker and AWS pointers, the golden tests, licence and citation.

- [ ] **Step 3: Run tests** — `python -m pytest -q -m "not golden"` — Expected: all PASS; `ruff check src tests` clean.

- [ ] **Step 4: Build the image manually** — `docker build -f docker/Dockerfile -t skysift:dev .` then `docker run --rm skysift:dev sources` — Expected: the sources table.

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat: container, scheduler script, AWS templates, README" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"`

---

## Self-review notes

- Spec coverage for M1: sources registry with yield metadata (Task 11), box_dip (7), rungs 1 to 4 (9, 10), store and queries (3, 5), blob with S3 (4), retention keep/stream/budget (4, 12), priority queue and two pools and checkpointing (12), CLI sources/run/status/vet (13), golden test light-curve part (14), container and AWS and Windows scheduling (15), CI and repo hygiene (1). Not in M1 by design: pixel localisation, rungs 6 to 8, pack builder, review queue, Zenodo, other adapters, `box_rise`, `periodic`, injection-recovery yields, HTML report.
- Review Focus coverage: item 1 in Task 7 test 3; item 2 in Task 9 test 1; item 3 in Task 2 test 1; item 4 in Task 10 test 4; item 5 in Task 12 test 2 (resume skips completed job keys and `add_signal` is idempotent per run); item 6 in Task 4 test 3.
- Type consistency: `TestResult.status` is `TestStatus` everywhere; `Store.add_test` stores `status.value`; `_cpu_work` round-trips `TestResult` through `model_dump` and `TestResult(**r)` which accepts the string value because `TestStatus` is a `StrEnum`.
