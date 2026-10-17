# SkySift design spec

Date: 13 October 2026. Status: draft for review. Author: Vikram Pandya with Claude.

## 1. Purpose

SkySift is an automation engine that continuously consumes archival time-series photometry (Kepler, K2, TESS, ground surveys, Gaia epoch photometry), searches each star for transit-like dips, periodic brightenings and periodic variability, vets every signal through a cost-ordered ladder of tests, and produces reproducible evidence packs for human review and publication. The rigour bar is the one applied by hand to the KIC 5689121 candidate on 13 October 2026: blind period search, parity and secondary checks, season stability, pixel localisation against Gaia, neighbour light-curve test, catalogue novelty, stellar physics, cross-mission check, TRICERATOPS false-positive probability.

Success means: (a) a sweep of one data source runs unattended for weeks with a bounded disk footprint, (b) every signal has a machine-readable record of which test it failed or passed, (c) a candidate that reaches the review queue has a pack a referee can reproduce from hashes and URLs alone, (d) the three known cases reproduce as golden tests, (e) the same container runs on a Windows PC, a Linux VM and AWS Batch without code changes.

Out of scope for v1: single-transit detectors, TESS FFI pixel extraction (adapter registered as planned), Gaia DR4 (not released), web UI beyond a static report, automatic publishing without human approval.

## 2. Users and decisions

One operator (Vikram). Decisions the operator makes: which sources to sweep (from a table showing size, baseline, depth limit, fraction already searched, known-candidate density, estimated yield, cost per star), retention policy and disk budget, worker counts, and approve or reject per candidate. Everything else is automatic.

## 3. Architecture

Python 3.12 package, src layout, five layers communicating through typed dataclasses (pydantic models):

- `sources/` adapters. Interface: `list_targets(selection) -> Iterator[Target]`, `fetch_lightcurve(target) -> LightCurve`, `fetch_pixels(target) -> PixelStamp | None`, `field_catalog(target, radius) -> GaiaField`. Registry `config/sources.yaml` holds per-source metadata: name, agency, status (live or planned), n_lightcurves, baseline_days, cadence_min, depth_limit_ppm, fraction_searched, known_candidate_density, est_yield_per_10k, bytes_per_star, seconds_per_star, notes. v1 live adapters: `kbonus_bkg` (MAST HLSP, background and target lists), `kepler` (MAST LC and TPF), `k2` (MAST, EVEREST or K2SFF HLSP), `tess_lc` (SPOC 2-min and TESS-SPOC/QLP/TGLC/TARS HLSP light curves via MAST), `exoarchive_bulk` (SuperWASP, KELT, HATNet, CoRoT, TrES, XO via NASA Exoplanet Archive bulk files). Registered as planned: `tess_ffi_pixels`, `gaia_epoch` (DR3 DataLink now, DR4 later), `ztf`, `asassn`, `ngts`, `atlas`.
- `detectors/` plugins. Interface: `detect(lc: LightCurve) -> list[Signal]`; each declares `cost_class`, `ladder` (which vetting branch applies), `version`. v1: `box_dip` (astropy BoxLeastSquares, periods 0.5 to 200 d, durations 1 to 12 h, SDE and SNR), `box_rise` (same engine on inverted flux; self-lensing and flares), `periodic` (Lomb-Scargle 0.1 to 100 d).
- `vetting/` ladder. Interface: `run(signal, data: DataBundle) -> TestResult(status in {pass, fail, undetermined}, metrics: dict, artifacts: list[Path], reason: str)`. Rungs in cost order, listed in section 5. Branches: `planet`, `eb`, `self_lensing`, `variable`.
- `store/`. SQLAlchemy models; SQLite by default, Postgres when `DATABASE_URL` is set. Tables: `targets` (source, id, ra, dec, gaia_id, priority, state), `runs` (job key, versions, started, finished, status, worker), `signals` (target, detector, period, t0, duration, depth, sde, snr), `tests` (signal, rung, status, metrics_json, artifacts, reason, duration_s), `candidates` (signal, branch, status in {review, approved, rejected, published}, pack_path, doi), `artifacts` (sha256, bytes, url, local_path, pinned, deleted_at), `queries` (service, url, params, utc, http_status, sha256, bytes). A `Blob` interface with `LocalBlob` and `S3Blob` backends chosen by URL (`file://` or `s3://`).
- `publish/`. Pack builder, review queue commands, Zenodo client (reserved DOI, upload, publish on approve), submission drafts (ExoFOP, NASA Exoplanet Archive contribution, MAST help desk) rendered from templates with the DOI.
- `cli/` (typer): `sources`, `run`, `status`, `review list|show|approve|reject`, `publish`, `report`, `fetch`, `vet` (single target, for debugging).

## 4. Data flow

1. `run --source S --filter F --workers N --retention stream` enumerates targets via the adapter, scores priority from catalogue data (dwarf-ness, magnitude, Gaia RUWE, crowding, searched-before flag), and enqueues the highest first, skipping job keys already complete at the current versions.
2. I/O pool fetches light curves into the blob cache (content addressed, hash recorded in `artifacts` and the archive URL in `queries`).
3. CPU pool runs detectors, then the ladder for each signal, writing a `tests` row per rung and stopping at the first fail. Pixel and field data are fetched lazily at rung 5 and shared across all targets in the same stamp.
4. Signals that clear the branch ladder become `candidates` in state `review`; the pack builder writes the pack and pins every input artifact.
5. Retention: `keep` retains everything; `stream` deletes a target's unpinned raw files when its last run finishes; `budget:<GB>` evicts least-recently-used unpinned files above the cap. Deletions are logged with hash and URL; a later rung that needs a deleted file re-fetches through the adapter and verifies the hash.
6. Operator reviews; `approve` uploads to Zenodo, writes the DOI into the pack and the DB, and renders submission drafts; `reject` records the reason and blocks re-queueing of the same signal.

## 5. Vetting ladder (planet branch; cost ascending; first fail stops)

1. Detection: SDE >= 9 and SNR >= 7 (configurable), >= 3 events, duration between 0.3 and 3 times the circular-orbit expectation.
2. Shape and parity: odd/even depth difference < 3 sigma; secondary at phase 0.5 < 3 sigma; free-phase secondary search over phases 0.1 to 0.9 (an eccentric EB flag if any > 5 sigma); V-shape flag from trapezoid fit; transit-timing scatter versus duration.
3. Stability: per-season (orientation) depth consistent within 3 sigma; PSF versus aperture depth ratio consistent with the crowding metrics in the headers.
4. Novelty and context: NASA Exoplanet Archive KOI, DR25 TCE, TOI tables; Kepler EB catalogue; VSX; Gaia DR3 variability and non-single-star tables; young-association membership tables. Known object -> `fail(reason=known)` unless the period differs. Service outage -> `undetermined` (the ladder continues, the pack records the gap).
5. Localisation: difference-image centroid per quarter or sector; Gaussian-PSF ranking of all Gaia DR3 sources within 40 arcsec (sources within 1.5 px of the stamp edge); fold of each resolved neighbour's own pipeline light curve; leak-ratio test when a rotation signal exists on the target. Pass requires the target to win the ranking in >= 75 percent of quarters and no neighbour to show a dip consistent with being the source.
6. Physics: Gaia and TIC stellar parameters; implied radius ratio and planet radius; stellar density from the transit versus catalogue within 3 sigma; equilibrium temperature. Fail if implied companion radius > 2 Jupiter radii (route to EB branch).
7. Cross-mission: fold every other mission's light curve at the ephemeris, weighted stack, 200 phase controls; result is a score, never a fail on its own.
8. TRICERATOPS: two seeds, 300k draws, target-only when the photometry is PSF-deblended, otherwise with resolved neighbours; report the range. Pass threshold configurable (default: candidate if FPP < 0.5, validated-quality flag if FPP < 0.015 and NFPP < 0.001).

EB branch: period (allowing 2P aliases), primary and secondary depths, secondary phase and implied eccentricity, localisation (rung 5), catalogue novelty. Self-lensing branch: pulse symmetry, duration versus period for the Gaia-derived primary mass, absence of a secondary dip, Gaia astrometric companion mass if a non-single-star solution exists, localisation. Variable branch: period, amplitude, stability across quarters, catalogue novelty.

## 6. Evidence pack

Structure identical to the 13 October KIC 5689121 pack: `README.txt`, `evidence_ledger.csv` and `.json` (one row per rung with claim, result, status, files, method file, limitation, primary source URL), `target_parameters.csv`, `archive_availability.json` (every query with URL, params, UTC, status, bytes, SHA-256), `MANIFEST.json` with SHA-256 of every file, `verify_manifest.py`, figures per rung, scripts snapshot at the git commit hash that ran, `ATTRIBUTION.txt` with data credits. Packs are written through the `Blob` interface so they can live in S3.

## 7. Configuration and secrets

One `config/default.yaml` (thresholds, worker counts, retention, blob URL, database URL) overridden by environment variables with prefix `SKYSIFT_`. Secrets (Zenodo token, AWS credentials, ATLAS token) only via environment variables or AWS Secrets Manager; never in files. TLS uses the system trust store (truststore) so corporate or antivirus proxies work.

## 8. Repository, container, AWS

```
skysift/
  pyproject.toml  src/skysift/{sources,detectors,vetting,store,publish,cli}/
  config/{default.yaml,sources.yaml}
  tests/{unit,golden,fixtures}/
  docker/{Dockerfile,docker-compose.yml}
  deploy/aws/{batch-job-definition.json,ecs-task-definition.json,iam-policy.json,README.md}
  deploy/windows/{register-task.ps1}
  docs/{superpowers/specs,runbook.md,sources.md}
  .github/workflows/ci.yml
  LICENSE (MIT)  README.md  CITATION.cff  CONTRIBUTING.md  SECURITY.md  .gitignore  .env.example
```

Container: python:3.12-slim, non-root user, entrypoint `skysift`, one command per container run (what Task Scheduler and AWS Batch both expect). Locally: `docker compose run skysift run --source kbonus_bkg`. AWS path: image in ECR, Batch job queue on Fargate or EC2 spot, S3 for blobs and packs, RDS Postgres optional (SQLite on an EFS volume is acceptable for a single worker). No code change between environments; only `SKYSIFT_BLOB_URL`, `DATABASE_URL` and worker counts differ.

Repo is private on GitHub from the first commit. `.gitignore` excludes data, caches, packs, `.env`, databases. CI runs ruff and pytest on push.

## 9. Error handling

Network: retries with exponential backoff (5 attempts), per-request timeout, system trust store, every request logged to `queries` with status. An unavailable service yields `undetermined`, never `pass`. Stage exceptions are caught per target, logged with traceback into `runs.status = error`, and do not stop the sweep. A target errors out after three attempts and is reported in `status`. The process checkpoints after every rung so a kill or scheduled stop resumes without repeating work. Disk budget breaches pause the I/O pool until eviction frees space; if nothing is evictable the run stops with a clear message rather than filling the disk.

## 10. Testing

Unit tests per detector and rung with synthetic injections (transits, eclipsing binaries, self-lensing pulses, sinusoids) over noise models from real KBonus residuals; injection-recovery completeness per source populates `est_yield_per_10k` and the detection-threshold calibration. Golden tests from cached fixtures: KIC 5689121 (box_dip must find 13.7695 d, depth 1883 +/- 150 ppm, rung 5 must pick the M dwarf in >= 12 of 13 quarters, rung 2 and 3 pass); KIC 12164160 (rung 2 must flag the free-phase secondary at 0.529, rung 3 must fail on season dependence); KIC 8743279 (rung 5 must rank Gaia DR3 2106193997080428800 first in >= 14 of 17 quarters). Adapter tests use recorded HTTP responses. CI runs everything except TRICERATOPS, which has a weekly job.

## 11. Observability

`skysift status`: targets per state, signals per detector, rung pass/fail/undetermined counts, throughput per hour, queue depth, disk used versus budget, error count with last five tracebacks. `skysift report`: static HTML with the same plus candidate cards and per-source yield so far. Logs are structured JSON to stdout (CloudWatch friendly) and a rotating file locally.

## 12. Milestones

M1 core: store, blob, kbonus_bkg adapter, box_dip, rungs 1 to 4, CLI run and status, stream retention, golden test 1 partially (light-curve part). M2 localisation: pixels, rung 5, kepler adapter, golden tests 1 to 3 complete. M3 physics and FPP: rungs 6 to 8, tess_lc adapter, pack builder, review queue. M4 publish and deploy: Zenodo, drafts, Dockerfile, AWS templates, CI, README. M5 breadth: k2, exoarchive_bulk, box_rise, periodic, EB and self-lensing branches, injection-recovery yields in sources.yaml.

## 13. Amendments (14 October 2026, after the milestone 1 review)

- Section 1, scope: a local web dashboard is in scope as milestone 2 (`2026-10-14-skysift-dashboard-design.md`); "web UI beyond a static report" is no longer excluded. Automatic publishing without human approval stays excluded.
- Section 3, detectors: a complete drift-correct Box Least Squares search over 0.5 to 200 d on the 1430 d Kepler baseline costs about 250 CPU-s per star with astropy, not 4 s; throughput comes from the CPU pool and AWS scale-out, and `sources.yaml` carries the measured value.
- Section 5, rung 1: the detection statistic is the per-band normalised SDE (band median and MAD over 24 logarithmic period bands); the threshold of 9 is uncalibrated and must be calibrated on inverted and scrambled real curves before any production sweep (a 1000-star pilot is the interim check).
- Section 5, rung 2: the free-phase secondary search fails the signal whenever it exceeds 5 sigma, with reason `secondary_at_0.5` when the phase is within one duration of 0.5 and `eccentric_secondary` otherwise; points within 1.5 durations of the primary are excluded from every off-primary measurement.
- Section 5, rung 3: season stability uses a leave-one-out z per season; one deviant season fails the signal on purpose (season-dependent depth is the signature of off-target contamination).
- Section 9: a target's work is checkpointed per target (one transaction holding its signals, tests, queries, pins and run status), not per rung; detection dominates the cost and the rungs total seconds. The three-attempt cap excludes failures caused by service outages. The job key's version field carries a hash of the search configuration, so a changed period window searches a target again.
- Section 10: the golden depth for KIC 5689121 is asserted as consistency with the published 1883 ppm within three combined sigmas plus a regression pin on the engine's own 2037 ppm measurement under the 2-day detrend window; the 150 ppm hand-set tolerance is withdrawn.
- Section 12: milestones are renumbered. M2 dashboard; M3 localisation (pixels, rung 5, kepler adapter, golden tests 1 to 3) plus the pre-sweep gates (SDE calibration, stacked-fold depths for transits shorter than about 1.8 h, wider rung 4 period-match radius). Deep signals (about 2 percent, e.g. a hot Jupiter around an M dwarf or an eclipsing binary) are ranked at 2P by the per-band SDE, and rung 2 then rejects the true period's transits as secondary_at_0.5; BoxDip needs alias resolution (prefer P over 2P when the phase-0.5 depth matches the primary) with injection-recovery of deep transits before the full-catalogue sweep. M4 physics and FPP; M5 publish and deploy; M6 breadth.
- Section 3, detectors (15 October 2026, after the second real sweep): BoxDip runs a masked second pass. When the first pass's strongest peak clears `sde_min`, the points within 0.75 durations of each of its primaries and of each phase-0.5 secondary are dropped and the same period grid is searched once more on the remaining points; the strongest second-pass peak is stored as one extra signal (`extra.pass = 2`, `extra.masked_period`, `extra.masked_points`) when it clears `sde_min` on the masked spectrum and is not within 1 percent of a p/q ratio (p, q up to 4) of any first-pass period. One iteration only, so the extra cost is bounded by one coarse search on fewer points; flat targets pay nothing. The periodogram summary and its peaks describe the first pass only. Motivation: gaia-dr3-2075988698983795072 showed a 0.72 d, 3000 ppm variable whose three aliases filled the stored peaks while the archive's KOI K04209.01 (9.91 d, 942 ppm) on the same star went unseen. `mask_iterations` is part of the search configuration hash, so earlier runs are searched again under the new version. A second-pass peak is also rejected when its frequency lies within 2/baseline of a combination of two first-pass frequencies (m f_i - n f_j with m, n up to 2, or f_i + f_j): a multi-periodic variable raises sidelobes exactly there. Measured on gaia-dr3-2075988698983795072 (0.5 to 30 d window): the second pass costs 71 s on top of an 88 s first pass, the masked spectrum near K04209.01 peaks at SDE 7.4, so masking cannot recover a 942 ppm transit under 3000 ppm sub-day variability; that needs harmonic pre-whitening (as Kepler TPS does) before the BLS, which is a pre-sweep gate. The masked pass is for strictly periodic eclipsers, where it recovers an injected 900 ppm, 9.9 d transit behind a 3000 ppm, 0.7 d binary.
- Section 5, rung 2 (15 October 2026): a shape contrast runs first on the stacked fold of every signal. A box (one level inside the transit window, one outside) and a two-harmonic sinusoid are both weighted least-squares fitted to the binned fold (four bins per transit duration, 50 to 2000 bins, each weighted by the standard error of its own points, so variability at other periods averages out); when the sinusoid's chi-square is at or below the box's the signal fails with reason `sinusoidal` (metrics `box_chi2`, `sine_chi2`, `shape_n`). This verdict uses every point, so it holds where the per-event odd/even and secondary tests are undetermined. Motivation: the masked second pass on gaia-dr3-2075988698983795072 promoted a 0.5677 d sidelobe of the star's 0.64 d variability (the three peaks are equally spaced in frequency by 0.188 per day) and it passed all four rungs; a smooth wave is variability, not a dip, whatever period the search lands on.
- Section 4, pre-processing (17 October 2026): harmonic pre-whitening runs between the detrend and the search (`skysift.lc.prewhiten`). The strongest Lomb-Scargle peak up to the Nyquist frequency is fitted with a two-harmonic sinusoid and subtracted, repeatedly, while its false-alarm probability stays below `thresholds.prewhiten_fap` (1e-6), up to `thresholds.prewhiten_max_components` (50; 0 turns the step off), stopping once a wave's amplitude falls below 6 percent of the point scatter. A peak whose fold is better fitted by a box of any width from 0.15 to 50 percent of the period than by the sinusoid is refused, together with its harmonic family and leakage sidelobes, so eclipsing binaries and transits stay in the data. The detector and every rung see the whitened flux; the removed components (frequency, amplitude, coefficients) are stored in each signal's `extra.prewhitening` and the dashboard subtracts the same model before plotting. The step is part of the search-configuration hash. Motivation: gaia-dr3-2052626821369997696 is a 15.05 per day delta Scuti whose BLS peaks sat at exactly 8, 9, 10 and 11 pulsation periods; whitened, its top peak drops below the threshold. On a quiet star with an injected 9.9 d, 942 ppm transit the first version removed the transit's own harmonics (SDE 79 to 24), which is why the guard tests transit-like box widths and the amplitude floor exists.
