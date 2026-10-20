# SkySift Dossiers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One engine command (`skysift dossier`) turns a candidate's stored results into a dossier with two labelled probabilities, the dashboard shows the final set as tiles and renders each dossier at Normal or Expert level, and `skysift pack` builds the submission folder with step-by-step instructions and an unpublished Zenodo draft.

**Architecture:** Four pure-plus-fetch engine modules (`calibrate.false_alarm`, `skysift.fpp` with `skysift.priors`, `skysift.stars`, `skysift.dossier`) write one `dossier_data.json` per target and a `dossiers` row; the dashboard renders that JSON through the existing base shell and mode cookie, and a background job launcher (the sweep launcher with another command) runs generation and packing one at a time. `skysift.pack` and `skysift.zenodo` build the submission folder from the stored JSON and stop before publishing.

**Tech Stack:** Python 3.12, numpy, SQLAlchemy, typer, FastAPI + Jinja2 + htmx (the dashboard), requests (Zenodo, through an injectable session), pytest. No new dependencies; the Gaia zero-point coefficient tables and the Mamajek dwarf table are vendored as package data.

**Spec:** `docs/superpowers/specs/2026-10-20-skysift-dossiers-design.md` (authority), which builds on `docs/superpowers/specs/2026-10-20-skysift-mission-control-design.md` and the recheck plan `docs/superpowers/plans/2026-10-20-skysift-recheck.md` (whose interfaces this plan consumes: `Store.latest_tests`, `Store.rechecks`, `skysift.lc.loader.LightCurveLoader`, `rung5_pixels` metrics and its `pixels.npz` artifact at `skysift://pixels/<signal id>`, ladder version "4").

## Global Constraints

- Never run a console `.exe` from `.venv/Scripts`; every command is `PYTHONUTF8=1 .venv/Scripts/python.exe -m <module>`.
- Branch `feat/mission-control` (after the recheck plan). Conventional commits ending with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`. Never push, never publish (a Zenodo draft is created, never published).
- `python -m ruff check src tests tools` clean (line length 120); `python -m pytest tests/unit -q -p no:cacheprovider` green before every commit. Tests stay off the network: every catalogue request goes through an injectable `fetch` callable (`tests/unit/fake_fetch.py` pattern) and every Zenodo request through an injectable session.
- A dossier never changes a status (spec section 6). Decisions stay append-only through `Store.decide`.
- Copy rules from the dashboard spec: no em-dashes anywhere (code, templates, generated text, LaTeX); British spelling except the `-ize` family; "candidate" wording throughout, never "discovery" or "confirmed" before a reviewer approval; every probability carries its name, its formula and the sentence saying what it does not cover (spec 4.1 and 4.2, verbatim).
- Every number on a dossier traces to a stored row or a logged query: the generator reads the database, the blob and the calibration file only, and the only network calls are the Gaia TAP queries of spec 5.3 and 4.2, logged with `store.log_query(..., target_id=...)`.
- Rounding on the page: FAP as "1 in N" with N to two significant figures; FPP as a percentage to one significant figure, floored at "under 1 percent" and capped at "over 90 percent"; stellar and planet values to the digits of spec section 8's examples (Rp 2.61, a 0.182, Teq 267).
- Package data: every new data file under `src/skysift/data/` is listed in `pyproject.toml` the way the fonts are, and a test imports it through `importlib.resources`.
- The FPP numbers appear on a tile only after the sign-off gate of spec 4.2 (Vikram's review of the two factor tables). Until then the tile shows the FPP pill with the word "provisional" and the Expert table; the flag is one constant, `fpp.SIGNED_OFF = False`, flipped in a one-line commit after the review.

## Review Focus

1. **A candidate whose calibration file is missing or has fewer than 200 null searches** (a new source, or the pilot still running): the tile reads "not yet calibrated", the dossier explains it, and generation does not fail. Pinned by Task 1 `test_false_alarm_below_two_hundred_nulls_is_not_calibrated` and Task 4 `test_generate_without_a_calibration_file`.
2. **A target with no Gaia DR3 id or with a Gaia row lacking parallax or colour** (KIC-only targets, very faint stars): `stars.lookup` returns a `StarParams` with `route="none"` and every value `None`, the dossier's section 05 says the star cannot be characterised, section 06 is replaced by one sentence, and the FPP uses the fallback priors (FGK, 0.5 Msun and Rsun as rung 1 does). Pinned by Task 3 `test_lookup_without_parallax_returns_an_unknown_star` and Task 4 `test_generate_for_a_star_without_gaia_parameters`.
3. **A signal with no rung 5 row and no `pixels.npz`** (a stage-1 candidate given a dossier on demand): the FPP runs the no-pixel branch (r_c 3.98 arcsec, label "FPP (no pixel data yet)"), section 04 says the pixel check has not run, and the caveats list it first. Pinned by Task 2 `test_compute_without_pixels_uses_one_pixel_confusion_radius` and Task 4 `test_generate_without_pixels_labels_the_fpp`.
4. **Regenerating a dossier after new test rows** (the recheck wrote a rung 5 row since the dossier was made): the tile shows "stale, regenerate", the page shows the banner, `skysift dossier` without `--force` regenerates it because the fingerprint differs, and the old JSON is replaced, not appended. Pinned by Task 4 `test_generate_again_is_a_no_op_until_the_tests_change`.
5. **A pack built for an unapproved candidate, or a Zenodo call without a token**: the pack still builds (the README says the record should not be frozen before approval), `--zenodo` without `SKYSIFT_ZENODO_TOKEN` exits with a plain message and no network call, and the Zenodo client never calls the publish endpoint. Pinned by Task 6 `test_pack_without_a_token_does_not_touch_zenodo` and `test_zenodo_client_never_publishes`.

---

## File structure

| Path | Responsibility |
|---|---|
| `src/skysift/calibrate.py` | `FalseAlarm`, `false_alarm(rows, sde, version) -> FalseAlarm`, `MIN_NULLS = 200`. |
| `src/skysift/priors.py` | Every constant of spec 4.2 with its citation string: occurrence grids, `F_EB`, binary fractions, the Raghavan period distribution, `CONFUSION_FLOOR_ARCSEC`, `PIXEL_ARCSEC`, `PSF_BLEND_ARCSEC`. |
| `src/skysift/fpp.py` | Pure: `Neighbour`, `FppInputs`, `ScenarioWeight`, `Fpp`, `compute(inputs) -> Fpp`, `fpp_words(fpp) -> str`, `SIGNED_OFF`. |
| `src/skysift/stars.py` | `GaiaRow`, `StarParams`, `PlanetParams`, `query_gaia(target, *, fetch, store) -> GaiaRow`, `query_neighbours(target, depth, *, fetch, store) -> list[Neighbour]`, `derive(gaia, period, fit_k, depth, rng) -> tuple[StarParams, PlanetParams]`, `lookup(...)`; vendored zero-point and Mamajek table readers. |
| `src/skysift/data/` | `mamajek_dwarf_table.txt` (Mamajek EEM table, with its header line naming the version and date), `gaia_zpt/z5.csv`, `gaia_zpt/z6.csv` (from the `gaiadr3-zeropoint` package, with `LICENSE`), `rnaas/aastex_template.tex`. |
| `src/skysift/dossier.py` | `DOSSIER_VERSION = "1"`, `pick_signal`, `plot_arrays`, `caveats`, `look_numbers`, `fingerprint`, `generate(store, blob, settings, target_id, *, signal_id=None, force=False, fetch=net.fetch, log=None) -> DossierReport`, `export_html(data, assets) -> str`. |
| `src/skysift/store/db.py`, `migrations.py` | `DossierRow`, `Store.upsert_dossier`, `Store.dossier`, `Store.dossiers`, `Store.set_pack`, `Store.set_pack_steps`; migration `003_dossiers`. |
| `src/skysift/pack.py`, `src/skysift/zenodo.py` | `build_pack(...) -> PackReport`, templates under `src/skysift/templates/pack/`; `ZenodoClient`. |
| `src/skysift/cli.py` | `dossier` and `pack` commands. |
| `src/skysift/ui/queries.py` | `final_set(store, version) -> list[dict]`, `dossier_for(store, target_id)`. |
| `src/skysift/ui/jobs.py` | `DossierRequest`, `DossierJobs` (the sweep launcher in `<data_dir>/ui/dossier_jobs` with the dossier or pack command). |
| `src/skysift/ui/planet_svg.py` | `planet_svg(star, planet, size=96) -> str`, `look_sentence(star, planet) -> str`. |
| `src/skysift/ui/routes/dossiers.py`, templates `dossiers.html`, `dossier.html`, `partials/dossier_tile.html`, `partials/probabilities.html`, `static/dossier.js`, `static/dossier.css` | The two pages, the export download, the generate and pack buttons, the pack step ticks. |
| `tests/unit/test_fap.py`, `test_fpp.py`, `test_stars.py`, `test_dossier.py`, `test_pack.py`, `test_zenodo.py`, `test_ui_dossiers.py`, `test_store.py`, `test_migrations.py`, `tests/unit/ui_seed.py` | Tests named in the tasks. |
| `docs/superpowers/notes/2026-10-21-dossier-comparison.md` | Task 7's numeric comparison with the hand-built page and the two factor tables for the sign-off. |

---

### Task 1: False-alarm probability and the priors module

**Files:**
- Modify: `src/skysift/calibrate.py`
- Create: `src/skysift/priors.py`
- Test: `tests/unit/test_fap.py`, `tests/unit/test_priors.py`

**Interfaces:**
- Consumes: `calibrate.read_rows(path)` rows (keys `search_version`, `inverted`, `scrambled`: lists of top SDEs, a null for a non-finite one), `calibrate._top(row, kind)`, `calibrate.NULLS`.
- Produces:
```python
# calibrate.py
MIN_NULLS = 200

@dataclass(frozen=True)
class FalseAlarm:
    k: int                      # null searches with top SDE >= sde
    n: int                      # null searches counted (both null kinds, rows with a finite top only)
    fap: float | None           # (k + 1) / (n + 1); None when n < MIN_NULLS
    calibrated: bool            # n >= MIN_NULLS
    version_matches: bool       # every row's search_version == version
    versions: tuple[str, ...]   # the search versions seen, sorted
    def one_in(self) -> int | None        # round(1 / fap) to two significant figures; None when not calibrated
    def words(self) -> str                # "1 in 1,700" | "not yet calibrated" | "calibration is for an older search"

def false_alarm(rows: list[dict], sde: float, version: str) -> FalseAlarm

# priors.py (every constant is a module attribute next to a CITATIONS[name] string)
F_EB = 0.014                              # Kirk et al. 2016
F_BIN = {"M": 0.26, "FGK": 0.44}          # Winters et al. 2019; Raghavan et al. 2010
RAGHAVAN_LOGP = (5.03, 2.28)              # mean and sigma of log10(P/day), Raghavan et al. 2010
PIXEL_ARCSEC = 3.98
CONFUSION_FLOOR_ARCSEC = 2.0              # half a Kepler pixel
PSF_BLEND_ARCSEC = 1.3                    # KBonus PSF photometry blend scale
ECCENTRICITY_ALLOWANCE = 0.30
OCCURRENCE = {"M": OccurrenceGrid(...), "FGK": OccurrenceGrid(...)}

@dataclass(frozen=True)
class OccurrenceGrid:
    period_edges: tuple[float, ...]       # days
    radius_edges: tuple[float, ...]       # Earth radii
    rate: tuple[tuple[float, ...], ...]   # planets per star per bin, [radius bin][period bin]
    citation: str
    def f_pl(self, rp: float, period: float) -> float    # bilinear in (log P, log Rp) on bin centres, held at the edges

def spectral_class(teff: float | None) -> str            # "M" when teff < 4000 else "FGK"; "FGK" for None
def binary_period_fraction(period: float) -> float       # fraction of the Raghavan log-normal within a factor of two of 2 * period
```
- `CITATIONS` strings, verbatim: `"Dressing, C. D. and Charbonneau, D. 2015, ApJ 807, 45"`, `"Fressin, F. et al. 2013, ApJ 766, 81"`, `"Kirk, B. et al. 2016, AJ 151, 68"`, `"Winters, J. G. et al. 2019, AJ 157, 216"`, `"Raghavan, D. et al. 2010, ApJS 190, 1"`, `"Morton, T. D. 2012, ApJ 761, 6"`, `"Lindegren, L. et al. 2021, A&A 649, A4"`, `"Bailer-Jones, C. A. L. et al. 2021, AJ 161, 147"`, `"Mann, A. W. et al. 2015, ApJ 804, 64"`, `"Mann, A. W. et al. 2019, ApJ 871, 63"`, `"Pecaut, M. J. and Mamajek, E. E. 2013, ApJS 208, 9"`, `"Kopparapu, R. K. et al. 2013, ApJ 765, 131"`.
- The grid values are transcribed from Table 2 of Fressin et al. 2013 (the per-bin table; five radius classes 0.8-1.25, 1.25-2, 2-4, 4-6, 6-22 Re; eleven period bins with edges 0.8, 2.0, 3.4, 5.9, 10, 17, 29, 50, 85, 145, 245, 418 d; percent converted to planets per star) and from Table 4, second block, of Dressing and Charbonneau 2015 (the revised-radius grid Figure 16 plots; radius edges 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0 Re; period edges 0.5, 1.7, 5.5, 18.2, 60.3, 200 d). The transcription with the handling of blank cells and upper limits is `.superpowers/sdd/2026-10-21-skysift-dossiers/occurrence-tables.md`, which the controller passes in the task brief; a value the file does not carry is `float("nan")` in the grid with a comment, never guessed. For an M dwarf with `rp > 4` Re, `f_pl` falls back to the FGK grid and the factor table says so.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_fap.py
def rows(n, tops, version="5+abc"):
    # n rows; row i has inverted top tops[2*i] and scrambled top tops[2*i+1]
    ...

def test_false_alarm_uses_the_add_one_rule():
    r = rows(150, [8.0] * 299 + [52.0], version="v")
    fa = false_alarm(r, sde=50.0, version="v")
    assert (fa.k, fa.n) == (1, 300) and fa.fap == pytest.approx(2 / 301) and fa.calibrated
    assert fa.one_in() == 150 and fa.words() == "1 in 150"

def test_false_alarm_above_every_null_is_never_zero():
    fa = false_alarm(rows(200, [9.0] * 400, "v"), sde=50.0, version="v")
    assert fa.k == 0 and fa.fap == pytest.approx(1 / 401) and fa.words() == "1 in 400"

def test_false_alarm_below_two_hundred_nulls_is_not_calibrated():
    fa = false_alarm(rows(99, [9.0] * 198, "v"), sde=50.0, version="v")
    assert not fa.calibrated and fa.fap is None and fa.one_in() is None and fa.words() == "not yet calibrated"

def test_false_alarm_notes_a_version_mismatch_but_still_counts():
    fa = false_alarm(rows(200, [9.0] * 400, "old"), sde=50.0, version="new")
    assert fa.calibrated and not fa.version_matches and fa.versions == ("old",)
    assert fa.words() == "calibration is for an older search"

def test_false_alarm_ignores_null_tops_that_are_null():
    r = rows(200, [9.0] * 400, "v"); r[0]["inverted"] = [None]
    assert false_alarm(r, 50.0, "v").n == 399

# tests/unit/test_priors.py
def test_f_pl_interpolates_in_log_space_and_holds_at_the_edges():
    g = OCCURRENCE["FGK"]
    inside = g.f_pl(2.6, 40.0); assert 0 < inside < 1
    assert g.f_pl(2.6, 10_000.0) == g.f_pl(2.6, g.period_edges[-1] * 0.999)
    assert g.f_pl(0.1, 40.0) == g.f_pl(g.radius_edges[0] * 1.001, 40.0)

def test_every_constant_has_a_citation():
    for name in ("F_EB", "F_BIN", "RAGHAVAN_LOGP", "OCCURRENCE"):
        assert CITATIONS[name]

def test_binary_period_fraction_is_a_probability_that_peaks_near_the_mode():
    assert 0 < binary_period_fraction(1.0) < binary_period_fraction(10 ** 5.03 / 2) < 1

def test_spectral_class_boundary():
    assert spectral_class(3999.0) == "M" and spectral_class(4000.0) == "FGK" and spectral_class(None) == "FGK"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/unit/test_fap.py tests/unit/test_priors.py -q -p no:cacheprovider`
Expected: FAIL with `ImportError` (`false_alarm`, `skysift.priors`).

- [ ] **Step 3: Implement `false_alarm` in `calibrate.py` and the `priors` module**

`false_alarm`: for each row and each kind in `NULLS`, `_top(row, kind)`; `n` counts the finite tops, `k` those `>= sde`; `versions = tuple(sorted({str(r.get("search_version")) for r in rows}))`. `one_in` rounds `1/fap` to two significant figures with `round(x, 1 - int(floor(log10(x))))`. `binary_period_fraction` integrates the normal CDF of `log10 P` between `log10(2P) ± log10(2)` with `scipy.stats.norm` (already a dependency) or `math.erf`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: the Step 2 command. Expected: PASS.

- [ ] **Step 5: Ruff, full suite, commit**

```bash
git add src/skysift/calibrate.py src/skysift/priors.py tests/unit/test_fap.py tests/unit/test_priors.py
git commit -m "feat(calibrate): false-alarm probability from the null searches and the FPP priors module"
```

---

### Task 2: Astrophysical false-positive probability

**Files:**
- Create: `src/skysift/fpp.py`
- Test: `tests/unit/test_fpp.py`

**Interfaces:**
- Consumes: Task 1 `priors` (`OCCURRENCE`, `F_EB`, `F_BIN`, `binary_period_fraction`, `spectral_class`, `PIXEL_ARCSEC`, `CONFUSION_FLOOR_ARCSEC`, `PSF_BLEND_ARCSEC`, `ECCENTRICITY_ALLOWANCE`); `lc.ops.circular_duration_days(period, mass, radius)`.
- Produces:
```python
SIGNED_OFF = False          # flipped after the review of spec 4.2; the tile says "provisional" while False

@dataclass(frozen=True)
class Neighbour:
    source_id: str; sep_arcsec: float; gmag: float; bp_rp: float | None
    # from the pixel stage, when present: distance from the season-averaged difference centroid
    centroid_sep_arcsec: float | None = None

@dataclass(frozen=True)
class FppInputs:
    period: float; depth: float                      # days; fraction (ppm / 1e6)
    gmag: float                                      # target G
    teff: float | None; radius: float | None; mass: float | None       # Rsun, Msun; None -> 0.5 and the FGK priors
    rp_rearth: float | None                          # None -> from sqrt(depth) * radius
    fit_k: float | None; fit_b: float | None; fit_a_over_r: float | None     # rung 2 fit; None when fit_ok is False
    secondary_ppm: float | None; secondary_err_ppm: float | None             # rung 2 secondary and control scatter
    odd_ppm: float | None; even_ppm: float | None; odd_even_err_ppm: float | None
    neighbours: tuple[Neighbour, ...]
    centroid_sigma_arcsec: float | None              # season-averaged centroid error; None without a pixel stage

@dataclass(frozen=True)
class ScenarioWeight:
    name: str                 # "PL" | "BEB" | "EB"
    prior: float; geometric: float; data: float
    factors: dict[str, float] # every named factor: {"f_pl": ..} | {"f_EB": .., "n_bright": .., "X_mean": ..} | {"f_bin": .., "g_P": .., "S_sec": .., "S_oe": .., "S_v": ..}
    @property
    def weight(self) -> float          # prior * geometric * data

@dataclass(frozen=True)
class Fpp:
    value: float                       # (w_BEB + w_EB) / (w_PL + w_BEB + w_EB)
    scenarios: tuple[ScenarioWeight, ScenarioWeight, ScenarioWeight]
    pixels: bool                       # False -> label "FPP (no pixel data yet)"
    confusion_radius_arcsec: float
    neighbours: tuple[dict, ...]       # per neighbour: source_id, sep_arcsec, gmag, bright_enough (bool), exclusion X_i
    def words(self) -> str             # "about 3 percent" | "under 1 percent" | "over 90 percent"
    def label(self) -> str             # "False positive" | "False positive (no pixel data yet)"
    def as_dict(self) -> dict          # JSON-ready, the factor table of spec 4.2

def compute(inputs: FppInputs) -> Fpp
```
- Formulas (spec 4.2, made exact):
  - Stellar fallbacks: `radius = inputs.radius or 0.5`, `mass = inputs.mass or 0.5`, class from `spectral_class(teff)`.
  - `a` from Kepler's third law in AU: `a_AU = (mass * (period / 365.25) ** 2) ** (1 / 3)`; `R_star / a = radius * 0.00465047 / a_AU`.
  - PL: prior `f_pl(rp, period)` of the class grid; geometric `R_star / a`; data `D_shape = exp(-z^2 / 2)`, `z = (T14_fit - T14_circ) / sigma`, `T14_circ = circular_duration_days(period, mass, radius)`, `T14_fit = period / pi * asin(sqrt((1 + k)^2 - b^2) / a_over_r)` from the fit (1.0 when any of `fit_k`, `fit_b`, `fit_a_over_r` is None), `sigma = hypot(0.1 * T14_fit, ECCENTRICITY_ALLOWANCE * T14_circ)`.
  - BEB: for each neighbour, `r_i = 10 ** (-0.4 * (gmag_i - gmag))`, flux fraction `f_i = r_i / (1 + r_i)`, `bright_enough = f_i >= 2 * depth`; prior `F_EB * n_bright`; geometric `(2 * 1.0 Rsun) / a_2P` for an equal-mass solar pair (`mass 2.0`) at `2 * period`; data `mean over bright neighbours of X_i`, where with pixels `r_c = max(3 * centroid_sigma_arcsec, CONFUSION_FLOOR_ARCSEC)`, `s_i = centroid_sep_arcsec if not None else sep_arcsec`, `k_i = max(0, (s_i - r_c) / centroid_sigma_arcsec)`, `X_i = exp(-k_i^2 / 2)`; without pixels `r_c = PIXEL_ARCSEC`, `X_i = 1` inside `r_c`, else `exp(-(s_i - r_c)^2 / (2 * PSF_BLEND_ARCSEC^2))`. With no bright neighbour the BEB weight is 0 and `factors["n_bright"] = 0`.
  - EB: prior `F_BIN[class] * binary_period_fraction(period)`; geometric `(radius + 0.5 * radius) * 0.00465047 / a_AU(2 * period, mass * 1.5)`; data `S_sec * S_oe * S_v`: `S_sec = 1 - Phi((0.1 * depth * 1e6 - secondary_ppm) / secondary_err_ppm)` (the Gaussian tail of the predicted secondary against the measured one; 1.0 when either is None), `S_oe = 1 - Phi((0.25 * depth * 1e6 - |odd_ppm - even_ppm|) / odd_even_err_ppm)` (1.0 when None), `S_v = 1.0 if (fit_b is None or fit_b < 0.9) else 0.5`.
  - `words()`: `v = value`; `< 0.01` → "under 1 percent"; `> 0.9` → "over 90 percent"; else one significant figure as "about N percent" (so 0.034 → "about 3 percent", 0.27 → "about 30 percent").

- [ ] **Step 1: Write the failing tests**

```python
def base(**over) -> FppInputs:
    # KIC 6146676-like: P 40.29, depth 2165e-6, G 16.24, Teff 3650, R 0.498, M 0.495, rp 2.61, k 0.048, b 0.885,
    # a/R 44.0, secondary 72 +- 80 ppm, odd 2202 even 2115 err 199, two neighbours (G 19.77 at 7.1", G 12.8 at 10.8"
    # with centroid separations 0.19 and 2.8 pixels), centroid sigma 0.05 pixel
    ...

def test_three_scenarios_sum_to_the_fpp():
    f = compute(base())
    w = {s.name: s.weight for s in f.scenarios}
    assert f.value == pytest.approx((w["BEB"] + w["EB"]) / sum(w.values()))
    assert 0.01 <= f.value <= 0.6                       # the expected scale of spec 4.2 for this star

def test_bright_neighbour_far_from_the_centroid_is_excluded():
    f = compute(base())
    near, far = f.neighbours
    assert far["bright_enough"] and far["exclusion"] < 1e-3
    assert not near["bright_enough"]                    # G 19.77 cannot make a 2165 ppm dip through a 16.2 star

def test_compute_without_pixels_uses_one_pixel_confusion_radius():
    f = compute(base(centroid_sigma_arcsec=None, neighbours=tuple(n.__class__(**{**n.__dict__, "centroid_sep_arcsec": None}) for n in base().neighbours)))
    assert not f.pixels and f.confusion_radius_arcsec == 3.98 and f.label() == "False positive (no pixel data yet)"
    assert f.neighbours[1]["exclusion"] == pytest.approx(math.exp(-(10.8 - 3.98) ** 2 / (2 * 1.3 ** 2)))

def test_a_deep_secondary_raises_the_eb_weight():
    clean, dirty = compute(base()), compute(base(secondary_ppm=400.0, secondary_err_ppm=80.0))
    assert dirty.scenarios[2].factors["S_sec"] > clean.scenarios[2].factors["S_sec"] and dirty.value > clean.value

def test_a_grazing_fit_halves_the_v_shape_factor():
    assert compute(base(fit_b=0.95)).scenarios[2].factors["S_v"] == 0.5
    assert compute(base(fit_b=0.3)).scenarios[2].factors["S_v"] == 1.0

def test_missing_fit_and_star_use_the_fallbacks():
    f = compute(base(teff=None, radius=None, mass=None, fit_k=None, fit_b=None, fit_a_over_r=None, rp_rearth=None))
    assert f.scenarios[0].factors["D_shape"] == 1.0 and f.scenarios[0].factors["class"] == "FGK"

@pytest.mark.parametrize("value,words", [(0.004, "under 1 percent"), (0.034, "about 3 percent"), (0.27, "about 30 percent"), (0.95, "over 90 percent")])
def test_words(value, words):
    f = compute(base()); assert Fpp(**{**f.__dict__, "value": value}).words() == words

def test_as_dict_is_json_ready_and_carries_every_factor():
    d = compute(base()).as_dict(); json.dumps(d)
    assert {s["name"] for s in d["scenarios"]} == {"PL", "BEB", "EB"} and "S_sec" in d["scenarios"][2]["factors"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/unit/test_fpp.py -q -p no:cacheprovider`
Expected: FAIL with `ModuleNotFoundError: skysift.fpp`.

- [ ] **Step 3: Implement `fpp.py`** with the dataclasses and `compute` exactly as the formulas above; `Phi` from `math.erf`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: the Step 2 command. Expected: PASS. If `test_three_scenarios_sum_to_the_fpp` lands outside 1 to 60 percent, report the factor table as a concern (spec 4.2 says the priors are re-examined, not the test loosened).

- [ ] **Step 5: Ruff, full suite, commit**

```bash
git add src/skysift/fpp.py tests/unit/test_fpp.py
git commit -m "feat(fpp): three-scenario astrophysical false-positive probability with printed factors"
```

---

### Task 3: Stellar and planet parameters from Gaia

**Files:**
- Create: `src/skysift/stars.py`, `src/skysift/data/mamajek_dwarf_table.txt`, `src/skysift/data/gaia_zpt/z5.csv`, `src/skysift/data/gaia_zpt/z6.csv`, `src/skysift/data/gaia_zpt/LICENSE`
- Modify: `pyproject.toml` (package data)
- Reference (read, do not import): `docs/candidates/kic-006146676/context/d_stellar_planet.py`, `context/b_neighbours.py`, `context/archive-proof/D_mamajek_dwarf_table.txt` (copy it), the installed `zero_point` package in `.venv/Lib/site-packages/zero_point/` (port `get_zpt` and copy its two coefficient CSVs with the LICENSE).
- Test: `tests/unit/test_stars.py`

**Interfaces:**
- Consumes: `net.fetch(url, *, params, service, store, target_id, timeout, attempts)`; `vetting.rung4_novelty.GAIA_TAP`; `Target` (`ra`, `dec`, `gaia_id`, `kic`, `gmag`); Task 2 `Neighbour`.
- Produces:
```python
N_DRAWS = 20_000
MAMAJEK_COLOUR_RANGE = (1.3, 3.0)          # BP-RP span of the table rows used (the hand-built route)

@dataclass(frozen=True)
class GaiaRow:                               # None where Gaia has no value
    source_id: str; ra: float; dec: float
    parallax: float | None; parallax_error: float | None; zero_point: float | None
    gmag: float | None; bp_rp: float | None; ruwe: float | None
    nu_eff: float | None; pseudocolour: float | None; ecl_lat: float | None; params_solved: int | None
    teff_gspphot: float | None; radius_flame: float | None; mass_flame: float | None
    r_med_geo: float | None; r_lo_geo: float | None; r_hi_geo: float | None     # Bailer-Jones 2021
    ks: float | None; ks_err: float | None                                        # 2MASS

@dataclass(frozen=True)
class Value:                                 # median and the 16th to 84th percentile half-widths, with its source
    median: float; minus: float; plus: float; source: str
    def as_dict(self) -> dict

@dataclass(frozen=True)
class StarParams:
    route: str                   # "mann" | "mamajek" | "none"
    spectral_type: str | None    # Mamajek row label at the colour, e.g. "M1V"
    teff: Value | None; radius: Value | None; mass: Value | None; density_gcc: Value | None
    distance_pc: Value | None; abs_g: float | None; abs_ks: float | None
    gaia_cross_check: dict       # teff_gspphot, radius_flame, mass_flame and a sentence on the difference
    reddening_note: str          # the E(BP-RP) assumption and the alternative radius at E = 0.2
    alt_radius_e02: Value | None
    caveats: tuple[str, ...]     # "Ks missing: Mamajek radius with doubled scatter", "no parallax", ...

@dataclass(frozen=True)
class PlanetParams:
    rp_rearth: Value | None; a_au: Value | None; a_over_r: Value | None
    teq_k: Value | None; insolation_earth: Value | None; transit_probability: Value | None
    hz_inner_au: float | None; hz_outer_au: float | None       # Kopparapu 2013 runaway greenhouse and maximum greenhouse
    in_habitable_zone: bool | None

def query_gaia(target: Target, *, fetch, store) -> GaiaRow | None        # None when target.gaia_id is not numeric
def query_neighbours(target: Target, depth: float, *, fetch, store, radius_arcsec: float = 20.0) -> list[Neighbour]
def derive(gaia: GaiaRow | None, period: float, fit_k: float | None, depth: float, rng: np.random.Generator) -> tuple[StarParams, PlanetParams]
def lookup(target: Target, period: float, fit_k: float | None, depth: float, *, fetch, store, seed: int = 20261021) -> tuple[StarParams, PlanetParams, list[Neighbour], GaiaRow | None]
def zero_point(gmag, nu_eff, pseudocolour, ecl_lat, params_solved) -> float    # the ported Lindegren 2021 correction
def mamajek_table() -> np.ndarray                                              # columns BP-RP, Teff, R, M, M_G, M_Ks; plus a parallel list of SpT labels
```
- Queries (each one `fetch(GAIA_TAP, params={"request": "doQuery", "lang": "ADQL", "format": "csv", "query": ...}, service="gaia", store=store, target_id=target.target_id)`):
  1. `SELECT source_id, ra, dec, parallax, parallax_error, phot_g_mean_mag, bp_rp, ruwe, nu_eff_used_in_astrometry, pseudocolour, ecl_lat, astrometric_params_solved, teff_gspphot, radius_flame, mass_flame FROM gaiadr3.gaia_source WHERE source_id = {id}`
  2. `SELECT r_med_geo, r_lo_geo, r_hi_geo FROM external.gaiaedr3_distance WHERE source_id = {id}`
  3. `SELECT t.ks_m, t.ks_msigcom FROM gaiadr3.tmass_psc_xsc_best_neighbour AS x JOIN gaiadr1.tmass_original_valid AS t ON x.original_ext_source_id = t.designation WHERE x.source_id = {id}`
  4. neighbours: `SELECT source_id, ra, dec, phot_g_mean_mag, bp_rp, DISTANCE(POINT('ICRS', ra, dec), POINT('ICRS', {ra}, {dec})) * 3600 AS sep_arcsec FROM gaiadr3.gaia_source WHERE 1 = CONTAINS(POINT('ICRS', ra, dec), CIRCLE('ICRS', {ra}, {dec}, {radius_deg})) AND source_id != {id}`, then keep rows with `G <= G_target + 2.5 * log10(1 / (2 * depth))` (spec 4.2); when the target has no Gaia id, the cone is centred on `target.ra, target.dec` and the `source_id !=` clause is dropped.
- `derive` route (spec 5.3): corrected parallax `plx - zero_point` with `0.015` mas added in quadrature to the error; distance draws `1000 / N(plx, err)`; colour draws `N(bp_rp, 0.10)`; Mamajek interpolation for Teff, R, M, M_G, M_Ks at the colour; for `teff < 4000` and `ks` present, radius from Mann 2015 `(1.9515 - 0.3520 M_Ks + 0.01680 M_Ks^2) * N(1, 0.0289)` and mass from Mann 2019 (the degree-5 polynomial of `d_stellar_planet.py`, `x = M_Ks - 7.5`, scatter `0.021`), `M_Ks` from `ks - DM - U(0, 0.07)`; otherwise the Mamajek radius and mass (scatter doubled when `ks` is missing below 4000 K, with the caveat). Planet: `a` from Kepler's third law, `rp = fit_k * R` (or `sqrt(depth) * R` when `fit_k` is None, source string says so), `Teq = Teff * sqrt(R / (2a)) * 0.7 ** 0.25`, insolation `L / a^2` with `L = R^2 (Teff / 5772)^4`, HZ edges from the Kopparapu 2013 (erratum) coefficients of the hand-built template (runaway greenhouse `[1.0512, 1.3242e-4, 1.5418e-8, -7.9895e-12, -1.8328e-15]`, maximum greenhouse `[0.3438, 5.8942e-5, 1.6558e-9, -3.0045e-12, -5.2983e-16]`, `t = Teff - 5780`, `d = sqrt(L / S_eff)`). Values are 16/50/84 percentiles rounded to: Teff 0 d.p., R and M 3, a 4, Rp 2, Teq 0, distance 1.
- Without a parallax or colour: `route="none"`, every `Value` None, caveat `"no Gaia parallax or colour: the star cannot be characterised from photometry"`.

- [ ] **Step 1: Write the failing tests**

```python
GAIA_CSV = (b"source_id,ra,dec,parallax,parallax_error,phot_g_mean_mag,bp_rp,ruwe,nu_eff_used_in_astrometry,pseudocolour,"
            b"ecl_lat,astrometric_params_solved,teff_gspphot,radius_flame,mass_flame\n"
            b"2073896294004902272,297.8687,41.4556,3.268646,0.035821,16.236654,2.0971918,0.9449,1.3598,,62.1,31,3855.0,0.5394,0.5595\n")
DIST_CSV = b"r_med_geo,r_lo_geo,r_hi_geo\n301.9,298.6,305.3\n"
TMASS_CSV = b"ks_m,ks_msigcom\n13.030,0.034\n"
CONE_CSV = (b"source_id,ra,dec,phot_g_mean_mag,bp_rp,sep_arcsec\n"
            b"2073896294004902144,297.8704,41.4571,19.772,1.814,7.14\n2073896294004902400,297.8660,41.4535,12.80,1.1,10.8\n"
            b"2073896294004903000,297.8690,41.4590,21.5,1.5,12.0\n")

def fake_fetch(responses):                    # routes on the ADQL text: "gaia_source WHERE source_id", "gaiaedr3_distance", "tmass", "CONTAINS"
    ...

def test_query_gaia_parses_the_three_tables_and_logs_each(store):
    row = query_gaia(target_6146676(), fetch=fake_fetch(...), store=store)
    assert row.parallax == pytest.approx(3.268646) and row.ks == pytest.approx(13.03) and row.r_med_geo == pytest.approx(301.9)
    assert store.counts()["queries"] == 3

def test_query_gaia_without_a_numeric_id_returns_none_and_asks_nothing(store):
    assert query_gaia(target_kic_only(), fetch=explode, store=store) is None

def test_neighbours_keep_only_stars_bright_enough_for_the_depth(store):
    ns = query_neighbours(target_6146676(), depth=2165e-6, fetch=fake_fetch(...), store=store)
    assert [n.gmag for n in ns] == [19.772, 12.8]                 # G 21.5 is fainter than 16.24 + 2.5 log10(1 / 0.00433)

def test_zero_point_matches_the_published_value_for_kic_6146676():
    assert zero_point(16.236654, 1.3598, None, 62.1, 31) == pytest.approx(-0.0317, abs=0.001)

def test_derive_reproduces_the_hand_built_dossier_within_its_errors():
    star, planet = derive(gaia_row_6146676(), period=40.29109, fit_k=0.048, depth=2165e-6, rng=np.random.default_rng(1))
    assert star.route == "mann" and star.spectral_type.startswith("M1")
    assert star.teff.median == pytest.approx(3650, abs=90) and star.radius.median == pytest.approx(0.50, abs=0.03)
    assert star.distance_pc.median == pytest.approx(303, abs=4)
    assert planet.rp_rearth.median == pytest.approx(2.61, abs=0.15) and planet.a_au.median == pytest.approx(0.182, abs=0.004)
    assert planet.teq_k.median == pytest.approx(267, abs=12) and planet.in_habitable_zone is True

def test_derive_without_ks_uses_mamajek_with_a_caveat():
    star, _ = derive(replace(gaia_row_6146676(), ks=None, ks_err=None), 40.29, 0.048, 2165e-6, np.random.default_rng(1))
    assert star.route == "mamajek" and any("Ks" in c for c in star.caveats)

def test_lookup_without_parallax_returns_an_unknown_star():
    star, planet = derive(replace(gaia_row_6146676(), parallax=None), 40.29, 0.048, 2165e-6, np.random.default_rng(1))
    assert star.route == "none" and star.radius is None and planet.rp_rearth is None

def test_hot_star_uses_the_fgk_route_without_mann():
    star, _ = derive(replace(gaia_row_6146676(), bp_rp=0.82, parallax=10.0), 10.0, 0.05, 1000e-6, np.random.default_rng(1))
    assert star.route == "mamajek" and star.teff.median > 5000

def test_data_files_ship_with_the_package():
    assert mamajek_table().shape[1] == 6
    assert importlib.resources.files("skysift.data").joinpath("gaia_zpt/z5.csv").is_file()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/unit/test_stars.py -q -p no:cacheprovider`
Expected: FAIL with `ModuleNotFoundError: skysift.stars`.

- [ ] **Step 3: Vendor the data files and implement `stars.py`**

Copy the Mamajek table and the two zero-point CSVs; `src/skysift/data/__init__.py` empty; add `"skysift.data": ["*.txt", "gaia_zpt/*", "rnaas/*"]` to the package data in `pyproject.toml`. The Mamajek reader parses the table rows the way `d_stellar_planet.py` does (columns 11, 1, 6, 30, 13, 22; rows matching `^[OBAFGKML]\d`), keeps rows within `MAMAJEK_COLOUR_RANGE` with a finite mass, sorted by colour.

- [ ] **Step 4: Run the tests to verify they pass**

Run: the Step 2 command. Expected: PASS.

- [ ] **Step 5: Ruff, full suite, commit**

```bash
git add src/skysift/stars.py src/skysift/data pyproject.toml tests/unit/test_stars.py
git commit -m "feat(stars): Gaia-based stellar and planet parameters with Monte Carlo errors"
```

---

### Task 4: Dossier generation, the `dossiers` table and the CLI

**Files:**
- Create: `src/skysift/dossier.py`
- Modify: `src/skysift/store/db.py`, `src/skysift/store/migrations.py`, `src/skysift/cli.py`, `tests/unit/ui_seed.py`
- Reference (read, do not import): `docs/candidates/kic-006146676/prep_data.py` (bin sizes), `lc_analysis.py` (the plot arrays), `dossier_template.html` lines 887 to 900 (the caveat paragraphs).
- Test: `tests/unit/test_dossier.py`, `tests/unit/test_store.py`, `tests/unit/test_migrations.py`

**Interfaces:**
- Consumes: Task 1 `false_alarm`, `calibrate.read_rows`, `calibrate.default_path(data_dir, source)`; Task 2 `compute`, `FppInputs`, `Neighbour`; Task 3 `lookup`; recheck plan `lc.loader.LightCurveLoader(store, blob).load(target_dict, pin=True)`, `Store.latest_tests(signal_id)`, `Store.rechecks(signal_id)`, `Store.children`; `ui.plots.whitened_fnorm(lc, signal)`; `lc.ops.event_depths`, `event_quality`, `fold_phase`; `ui.explain.why`, `verdict`, `summary_sentence`; `pipeline.search_version`; the `pixels.npz` artifact (`ArtifactRow.url == f"skysift://pixels/{signal_id}"`, keys `diff`, `var`, `direct`, `valid` as 3-D arrays, `seasons`, `pos_target`, `pos_neighbour`).
- Produces:
```python
# db.py
class DossierRow(Base):
    __tablename__ = "dossiers"
    target_id: str (PK); signal_id: int (FK signals.id, index); generated: datetime; data_path: str
    search_version: str; ladder_version: str | None; dossier_version: str
    fap: float | None; fpp: float | None; fap_detail: dict (JSON); fpp_detail: dict (JSON)
    tests_fingerprint: str; status_at_generation: str; export_path: str = ""
    pack_path: str = ""; pack_steps: dict (JSON, {"1": true, ...}); pack_built: datetime | None

class Store:
    def upsert_dossier(self, **fields) -> None
    def dossier(self, target_id: str) -> dict | None
    def dossiers(self) -> list[dict]                      # every row, with the signal's final_status and the latest decision joined in
    def set_pack(self, target_id: str, pack_path: str, built: datetime) -> None
    def set_pack_steps(self, target_id: str, steps: dict) -> None

# migrations.py: _step_003_dossiers creates the table when missing (SQLite and Postgres DDL), idempotent; STEPS gains ("003_dossiers", ...)

# dossier.py
DOSSIER_VERSION = "1"
STATUS_ORDER = ("candidate_stage2", "approved", "candidate_stage1")
SERIES_BIN_DAYS = 0.25; GALLERY_HALF_HOURS = 8.0; SPECTRUM_BINS = 1500

@dataclass
class DossierReport:
    target_id: str; signal_id: int; data_path: Path; generated: bool      # False when the fingerprint matched and not force
    fap: FalseAlarm | None; fpp: Fpp; stale_reason: str | None
    comparison: dict                                                       # filled by Task 7's tool; {} here
    def as_dict(self) -> dict

def pick_signal(store, target_id, signal_id=None) -> dict                 # KeyError when none; status order then SDE
def fingerprint(latest_tests: list[dict], final_status: str, decision: str | None) -> str     # sha256 of sorted test ids + status + decision
def plot_arrays(lc, fnorm, signal, tests) -> dict                          # series_binned, series_bin_days, fold_points, fold_bins, model, odd_bins, even_bins, secondary_bins, event_gallery, events, spectrum (from the stored periodogram summary envelope, up to SPECTRUM_BINS), quarters
def caveats(data: dict) -> list[dict]                                      # [{"title", "text"}] by the rules below, in this order
def look_numbers(star: StarParams, planet: PlanetParams) -> dict | None    # the inputs of section 06: teff, radius, mass, luminosity, a, rp, teq, hz edges, angular size, star colour word
def generate(store, blob, settings, target_id, *, signal_id=None, force=False, fetch=net.fetch, log=None) -> DossierReport
def export_html(data: dict, static_dir: Path) -> str                       # one file: dossier.css + app tokens, fonts as data URIs, dossier.js, the JSON, its own level toggle; no external URL
```
- `dossier_data.json` top-level keys (every float rounded to what the page prints, arrays as lists): `dossier_version`, `generated` (ISO UTC), `target` (target row fields plus `name` such as "KIC 6146676" or "Gaia DR3 ..."), `signal` (row fields), `status` (`final_status`, latest decision, `label` from `explain.STATUS_GLOSSARY`), `summary` (`explain.summary_sentence`), `ephemeris`, `plots` (Task 4 `plot_arrays`), `tests` (latest per rung: rung, status, reason, metrics, created), `why` (the `explain.why` list as dicts), `rechecks`, `children`, `fap` (`FalseAlarm` fields plus `words`, `sentence_not_covered` verbatim from spec 4.1), `fpp` (`Fpp.as_dict()` plus `words`, `label`, `signed_off`, `sentence_not_covered` verbatim from spec 4.2), `pixels` (None, or per season: `season`, `quarters`, `snr` 2-D list with None, `direct`, `valid`, `pos_target`, `pos_neighbour`, `centroid`, `centroid_sigma`, `nearer`, `depth_target_ppm`, `depth_neighbour_ppm`, from the npz and the rung 5 metrics), `star`, `planet`, `look`, `caveats`, `provenance` (`search_version`, `ladder_version`, `git_commit`, `queries` for this target from the `queries` table, `files` with url and sha256 for the target's artifacts, `calibration_file`, `commands` the three commands to reproduce).
- Caveat rules, in order: no rung 5 row → "Which star is dimming has not been tested with pixel data"; rung 5 undetermined → its reason in words; `fpp.pixels` False → "The false-positive estimate has no pixel exclusion"; FAP not calibrated → "The false-alarm probability is not yet calibrated for this source"; `fit_b >= 0.9` or `fit_ok` False → "A grazing or diluted eclipsing binary is not fully excluded by photometry alone"; star route "none" → "The star cannot be characterised from Gaia photometry"; route "mamajek" below 4000 K → "The radius comes from the colour table because the 2MASS Ks magnitude is missing"; always → "No spectrum: the planet radius depends on the stellar radius from Gaia colour and parallax" and the reddening note with the alternative radius; `n_seasons < 2` in rung 3 → "The depth is measured in one season only"; always last → "This is a candidate until independent follow-up confirms it".
- Staleness: `generate` recomputes the fingerprint; when it equals the stored one and `force` is False, it returns `generated=False` without touching the file. The dashboard's stale test is the same comparison (`queries.dossier_for` in Task 5 computes it).
- CLI: `skysift dossier --target <id> [--signal <id>] [--export <file>] [--force]` prints the report as a Rich table in a terminal and strict JSON when piped (as `calibrate-report` does); exit code 1 with the message when the target has no signal.
- Seed: `tests/unit/ui_seed.py` gains `seed_dossier(store, data_dir, target_id=STAGE2_TARGET) -> dict` which writes a hand-made `dossier_data.json` (small arrays, every key present, star and planet of a cool dwarf, FPP 0.03, FAP 1 in 1,700) and the `dossiers` row, used by the Task 5 page tests.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_dossier.py (fixtures: settings, a store with the seed, a blob holding a synthetic KBonus-like light curve for
# the stage-2 target through tests/synthetic.py + inject_model, a fake fetch answering the four Gaia queries and a calibration
# file of 220 rows written with calibrate's row shape)
def test_pick_signal_prefers_stage_two_then_approved_then_sde(seeded_store): ...

def test_generate_writes_the_json_and_the_row(env):
    rep = generate(env.store, env.blob, env.settings, STAGE2_TARGET, fetch=env.fetch)
    data = json.loads(rep.data_path.read_text(encoding="utf-8"))
    assert rep.generated and set(data) >= {"signal", "plots", "tests", "why", "fap", "fpp", "pixels", "star", "planet", "look", "caveats", "provenance"}
    row = env.store.dossier(STAGE2_TARGET)
    assert row["signal_id"] == rep.signal_id and row["fap"] == pytest.approx(rep.fap.fap) and row["fpp"] == pytest.approx(rep.fpp.value)
    assert data["fap"]["sentence_not_covered"].startswith("This is about noise.")
    assert "discover" not in json.dumps(data).lower() and "\u2014" not in json.dumps(data)

def test_generate_again_is_a_no_op_until_the_tests_change(env):
    first = generate(...); second = generate(...)
    assert not second.generated and second.data_path.stat().st_mtime == first.data_path.stat().st_mtime
    env.store.add_test(first.signal_id, TestResult(rung="rung5_pixels", status="undetermined", reason="pixels: 2 usable transits < 3"), 1.0)
    assert generate(...).generated
    assert generate(..., force=True).generated

def test_generate_without_a_calibration_file(env):
    env.calibration_path.unlink()
    rep = generate(...); data = json.loads(rep.data_path.read_text(encoding="utf-8"))
    assert data["fap"]["words"] == "not yet calibrated" and any("not yet calibrated" in c["text"] for c in data["caveats"])

def test_generate_without_pixels_labels_the_fpp(env_stage1):
    data = json.loads(generate(..., STAGE1_TARGET, ...).data_path.read_text(encoding="utf-8"))
    assert data["pixels"] is None and data["fpp"]["label"] == "False positive (no pixel data yet)"
    assert data["caveats"][0]["title"].startswith("Which star is dimming")

def test_generate_for_a_star_without_gaia_parameters(env_no_gaia):
    data = ...; assert data["star"]["route"] == "none" and data["look"] is None and data["planet"]["rp_rearth"] is None

def test_pixels_section_comes_from_the_npz_artifact(env_with_npz):
    data = ...; assert len(data["pixels"]) == 2 and data["pixels"][0]["snr"][0][0] is None   # an invalid pixel

def test_every_query_in_provenance_belongs_to_the_target(env): ...

def test_export_is_one_file_without_external_urls(env):
    html = export_html(data, STATIC_DIR)
    assert "<script src=" not in html and "<link " not in html and "data:font/woff2;base64," in html
    assert "https://" not in re.sub(r'<a [^>]*href="https://[^"]*"', "", html)        # only citation links remain
    assert "data-level" in html                                                        # its own toggle

def test_cli_dossier_prints_json_when_piped(cli_env): ...
def test_cli_dossier_fails_plainly_for_a_target_without_signals(cli_env): ...

# tests/unit/test_store.py
def test_dossier_rows_round_trip_and_join_status(store_with_signals): ...
# tests/unit/test_migrations.py
def test_step_003_creates_the_dossiers_table_on_an_old_database(old_schema_engine): ...
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/unit/test_dossier.py tests/unit/test_store.py tests/unit/test_migrations.py -q -p no:cacheprovider`
Expected: FAIL with `ModuleNotFoundError: skysift.dossier` and `AttributeError: upsert_dossier`.

- [ ] **Step 3: Implement the row, the migration, `dossier.py` and the CLI command**

`export_html` embeds `static/app.css` (tokens and components), `static/dossier.css`, `static/dossier.js` (Task 5 writes them; this task ships minimal versions that render the eight section headings, the tiles and the caveats from the JSON, and Task 5 replaces them) and the woff2 files as `@font-face` data URIs; `<script id="dossier-data" type="application/json">` holds the JSON with `</` escaped as `<\/`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: the Step 2 command. Expected: PASS.

- [ ] **Step 5: Ruff, full suite, commit**

```bash
git add src/skysift/dossier.py src/skysift/store src/skysift/cli.py src/skysift/ui/static tests/unit
git commit -m "feat(dossier): generate dossier_data.json, the dossiers table and the skysift dossier command"
```

---

### Task 5: The Dossiers pages

**Files:**
- Create: `src/skysift/ui/routes/dossiers.py`, `src/skysift/ui/jobs.py`, `src/skysift/ui/planet_svg.py`, templates `dossiers.html`, `dossier.html`, `partials/dossier_tile.html`, `partials/probabilities.html`, `static/dossier.js`, `static/dossier.css`
- Modify: `src/skysift/ui/app.py` (`NAV` gains `("/dossiers", "Dossiers")` between Candidates and Live; include the router; `/about` glossary gains "False alarm" and "False positive"), `src/skysift/ui/queries.py`, `src/skysift/ui/explain.py` (`GLOSSARY` two entries), `src/skysift/ui/routes/review.py` (verdict popup gains the Probabilities block when a dossier exists), `templates/approved.html` (the Dossier link), `templates/partials/verdict.html`
- Reference (read, do not import): `docs/candidates/kic-006146676/dossier_template.html` (the chart helpers, the gallery, the pixel maps, the "How it might look" panel: port them to `dossier.js` with CSS tokens instead of its own palette), `src/skysift/ui/sweeps.py` (`SweepLauncher`).
- Test: `tests/unit/test_ui_dossiers.py`, `tests/unit/test_ui_review.py`

**Interfaces:**
- Consumes: Task 4 `Store.dossiers`, `Store.dossier`, `dossier.fingerprint`, `dossier.export_html`, `dossier.generate` (through the CLI in a job); `ui.sweeps.SweepLauncher(data_dir, command=[...])`; `explain.verdict`; the base shell (`render`, `mode`, `theme`, `token`, `require_token`).
- Produces:
```python
# queries.py
def final_set(store: Store, version: str | None) -> list[dict]
    # one row per target: every candidate_stage2 signal under version, every signal whose latest decision is approve,
    # plus every target with a dossiers row; fields: target_id, signal_id, final_status, decision, dossier (row or None),
    # stale (bool, fingerprint comparison), group ("approved" | "stage2" | "stage1"), fpp, fap; sorted approved, stage2, stage1,
    # then fpp ascending with None last
def dossier_for(store: Store, target_id: str) -> dict | None       # the row + stale + the parsed JSON (data) + signal + decision

# jobs.py
@dataclass
class DossierRequest:
    kind: str            # "dossier" | "pack"
    target_id: str
    force: bool = False
    def validate(self, thresholds=None) -> None        # kind in the two words; target_id matches SEARCH of review.py
    def argv(self, python=sys.executable) -> list[str] # [python, "-m", "skysift", kind, "--target", target_id, (+"--force")]
    def env(self) -> dict                              # {}

class DossierJobs:                                     # SweepLauncher(data_dir / "ui" / "dossier_jobs"); one job at a time
    def __init__(self, data_dir: Path, command: list[str] | None = None)
    def start(self, req: DossierRequest) -> dict
    def active(self) -> dict | None                    # includes request.kind and request.target_id
    def tail(self, n=50) -> list[str]

# planet_svg.py
def planet_svg(look: dict, size: int = 96) -> str      # star disc colour by Teff (> 5500 "#fff3d6", > 4500 "#ffd9a0", > 3900 "#ffbe78", else "#ff9d5c"),
                                                        # planet disc radius log-scaled between 1 and 12 Re, colour by Teq (< 200 "#cfe6ff", < 320 "#7fb7a3", < 700 "#c9a96e", else "#e0784a"),
                                                        # an HZ bar below with the orbit mark; role="img" and aria-label=look_sentence
def look_sentence(star: dict, planet: dict) -> str     # "A cool red star with a planet about two and a half times the size of Earth, in the habitable zone"
                                                        # words: Teff < 3900 "cool red", < 5200 "orange", < 6000 "yellow", else "white"; size as a fraction of Earth in words to one decimal
                                                        # ("about 2.6 times the size of Earth"); "in", "inside" (hotter) or "beyond" the habitable zone
```
- Routes (`routes/dossiers.py`): `GET /dossiers` (tiles; `?versions=all` as the queue; empty state with the spec's sentence and links to `/sources` and `/candidates`), `GET /dossiers/{target_id}` (404 page when no dossier; the eight sections from the JSON; stale banner; sticky `nav.sections` at `>= 1080px`; decision bar partial for the signal; "Open in the candidate queue" → `/signals/{id}`; "Download standalone page" → `GET /dossiers/{target_id}/export.html`, which renders `export_html` on demand, caches at `export_path` and serves it as an attachment named `<target_id>_dossier.html`), `POST /dossiers/{target_id}/generate` and `POST /dossiers/{target_id}/pack` (token; start a `DossierRequest`; 409 toast "another dossier job is running" when busy; the tile swaps to a "generating" state that polls `GET /partials/dossiers/{target_id}/tile` every 5 s until the job ends), `POST /dossiers/{target_id}/steps` (token; form `step` 1 to 5 and `done` 0/1; `Store.set_pack_steps`; returns the step list partial).
- Templates: a tile shows the thumbnail (`planet_svg` inline, or the placeholder "no star parameters" box), `target.name`, the spectral type and distance line, `summary`, the two pills (`False alarm 1 in 1,700` and the FPP pill with `label` and `words`; "provisional" chip while not signed off; each pill is a button opening the verdict dialog at the Probabilities block), the status pill, the generated date or the Generate button, the stale note. `partials/probabilities.html`: the two names, the two numbers, the two "what it does not cover" sentences, a link to the dossier's section 03. `dossier.html`: sections `signal`, `repeat`, `checks`, `pixels`, `star`, `look`, `caveats`, `reproduce` with the Normal and Expert visibility through the existing `.only-simple` / `.only-expert` classes; `dossier.js` reads `#dossier-data`, draws the charts with the ported helpers and CSS variables (`--fg`, `--muted`, `--ion`, `--line`), the gallery, the S/N maps (with a `<table>` fallback of the per-season numbers for screen readers), the "How it might look" panel from `look`.
- Verdict popup: when `queries.dossier_for` finds a dossier for the signal's target, the popup appends the Probabilities block; otherwise nothing changes.
- Accessibility (spec 7): the grid is 1/2/3 columns at 360/720/1080 px; each pill carries its word in text; the thumbnail has `role="img"` and the sentence as `aria-label`; the section index is a `<nav aria-label="Sections">`; `tools/contrast_check.py` gains the four planet colours against both surfaces.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_ui_dossiers.py (fixture: make_seeded_app + seed_dossier for the stage-2 target)
def test_dossiers_page_lists_the_final_set_in_order(seeded_dossier):
    html = client.get("/dossiers").text
    assert html.index(APPROVED_TARGET) < html.index(STAGE2_TARGET)      # approved first
    assert "False alarm" in html and "1 in 1,700" in html and "about 3 percent" in html and "provisional" in html
    assert 'role="img"' in html and "times the size of Earth" in html

def test_dossiers_page_empty_state(seeded):
    assert "No dossiers yet" in client.get("/dossiers").text

def test_tile_without_a_dossier_offers_generate_and_marks_stale_ones(seeded_dossier): ...

def test_dossier_page_renders_the_eight_sections_in_both_modes(seeded_dossier):
    for mode in ("simple", "expert"):
        html = client.get(f"/dossiers/{STAGE2_TARGET}", cookies={"skysift_mode": mode}).text
        for sid in ("signal", "repeat", "checks", "pixels", "star", "look", "caveats", "reproduce"): assert f'id="{sid}"' in html
        assert "This is about noise." in html and "not a vespa" in html
    assert "stale" not in html

def test_dossier_page_404_and_stale_banner(seeded_dossier): ...

def test_export_download_is_one_file(seeded_dossier):
    r = client.get(f"/dossiers/{STAGE2_TARGET}/export.html")
    assert r.headers["content-disposition"].endswith('_dossier.html"') and "<script src=" not in r.text

def test_generate_button_starts_one_job_and_refuses_a_second(seeded_dossier, fake_jobs): ...
def test_pack_steps_toggle_with_the_token(seeded_dossier): ...
def test_approved_page_links_the_dossier(seeded_dossier): ...
def test_verdict_popup_has_the_probabilities_block_only_with_a_dossier(seeded_dossier): ...
def test_dossier_routes_reject_a_bad_target_id(seeded_dossier):
    assert client.get("/dossiers/../etc").status_code in (400, 404)

# planet_svg
@pytest.mark.parametrize("teff,rp,hz,words", [(3650, 2.61, "in", "A cool red star with a planet about 2.6 times the size of Earth, in the habitable zone"),
                                                (5800, 1.0, "inside", "A yellow star with a planet about the size of Earth, inside the habitable zone")])
def test_look_sentence(teff, rp, hz, words): ...
def test_planet_svg_is_valid_and_labelled(): xml.etree.ElementTree.fromstring(planet_svg(look)) ; assert 'aria-label=' in planet_svg(look)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/unit/test_ui_dossiers.py -q -p no:cacheprovider`
Expected: FAIL with 404 on `/dossiers` and `ImportError`.

- [ ] **Step 3: Implement the routes, queries, jobs, templates, script and styles**

- [ ] **Step 4: Run the tests, the contrast tool and look at the pages**

Run: the Step 2 command plus `tests/unit/test_ui_review.py`, then `PYTHONUTF8=1 .venv/Scripts/python.exe tools/contrast_check.py`. Expected: PASS; every pair AA. Start the seed dashboard (`make_ui_seed.py`, then `skysift ui --port 8766`) and check `/dossiers` and the dossier page at 360, 720, 1080 and 1440 px in both themes; save the screenshots to the plan workspace.

- [ ] **Step 5: Ruff, full suite, commit**

```bash
git add src/skysift/ui tests/unit tools/contrast_check.py
git commit -m "feat(ui): Dossiers tiles, the dossier page, the export download and dossier jobs"
```

---

### Task 6: The submission pack and the Zenodo draft

**Files:**
- Create: `src/skysift/pack.py`, `src/skysift/zenodo.py`, `src/skysift/templates/pack/README.md.j2`, `note.tex.j2`, `exofop_note.txt.j2`, `archive_email.txt.j2`, `src/skysift/data/rnaas/aastex_template.tex`
- Modify: `src/skysift/cli.py` (`pack` command), `src/skysift/ui/routes/dossiers.py` (the pack link and step list already wired in Task 5 read `pack_path` and `pack_steps`)
- Test: `tests/unit/test_pack.py`, `tests/unit/test_zenodo.py`

**Interfaces:**
- Consumes: Task 4 `Store.dossier`, `Store.set_pack`, the `dossier_data.json` keys, `dossier.export_html`; `CITATION.cff` (`yaml.safe_load`); `jsonutil.dumps`; `Store.decisions`; `CandidateRow.pack_path` through a new `Store.set_candidate_pack(signal_id, pack_path)`.
- Produces:
```python
# pack.py
PACK_VERSION = "1"
STEPS = (("1", "Freeze the record on Zenodo"), ("2", "Upload to ExoFOP-TESS"), ("3", "Write it up (Research Note)"),
         ("4", "Tell the NASA Exoplanet Archive"), ("5", "Answer follow-up requests"))

@dataclass
class PackReport:
    target_id: str; out_dir: Path; zip_path: Path; files: list[str]; approved: bool
    zenodo: dict | None            # {"deposition_id", "draft_url", "sandbox": bool} when created
    def as_dict(self) -> dict

def build_pack(store, blob, settings, target_id, *, out_dir: Path | None = None, tic: str | None = None,
               observer_tag: str = "skysift", zenodo: ZenodoClient | None = None, log=None) -> PackReport
    # out_dir default <data_dir>/packs/<target_id>/<UTC yyyymmdd>/; writes README.md, note/note.tex + note/figure1.svg,
    # data/lightcurve.csv (time_bkjd, flux_rel_ppm, err_ppm, quarter from the loader's whitened flux), data/ephemeris.csv + .json,
    # data/events.csv, data/periodogram.json, data/rung_metrics.json, figures/{fold,oddeven,periodogram,centroids}.svg (+ .png
    # when matplotlib is importable, else noted in README), dossier.html, provenance/{versions.json, queries.json, hashes.json,
    # calibration.json}, exofop/<TIC>_<yyyymmdd>_<tag>_<content>.<ext> copies + exofop/note.txt; then <out_dir>.zip
def render_note(data: dict, doi_placeholder: str = "10.5281/zenodo.XXXXXXX") -> str     # AASTeX RNAAS, under 1,000 words of body text
def render_readme(data: dict, report_fields: dict) -> str
def exofop_name(tic: str | None, date: str, tag: str, content: str, ext: str) -> str   # "TIC-unknown" when tic is None

# zenodo.py
SANDBOX = "https://sandbox.zenodo.org/api"; LIVE = "https://zenodo.org/api"
class ZenodoError(RuntimeError): ...
class ZenodoClient:
    def __init__(self, token: str, *, sandbox: bool = True, session=None, timeout: float = 120.0)
    def create_deposition(self) -> dict                                   # POST /deposit/depositions with {}; returns the JSON
    def set_metadata(self, deposition: dict, metadata: dict) -> dict      # PUT /deposit/depositions/{id}
    def upload(self, deposition: dict, path: Path) -> dict                # PUT {links.bucket}/{name} streaming the file
    def draft_url(self, deposition: dict) -> str                          # links.html
    # there is no publish method, by design
def metadata_from(data: dict, citation: dict | None) -> dict              # upload_type "dataset", title "SkySift candidate <name>: ...",
                                                                          # creators from CITATION.cff authors, description = summary + caveats,
                                                                          # keywords, access_right "open", license "cc-by-4.0", related_identifiers (the KBonus HLSP DOI 10.17909/7jbr-w430 as "isDerivedFrom")
```
- README content (spec 5.9, in Vikram's plain register, no em-dashes), per step: who needs which account (Zenodo; ExoFOP registered user; AAS journals account and arXiv endorsement; the archive help desk form), what to attach, what to write (the templates), what the engine did or cannot do. Step 2 explains how to find the TIC on ExoFOP when `tic` was not given (search by the Gaia DR3 id or coordinates) and the password-protection choice. Step 4 carries the one-paragraph template. An unapproved candidate gets a first line: "This candidate is not yet approved by a reviewer. Build the record, but do not freeze or upload it before the approval."
- CLI: `skysift pack --target <id> [--out <dir>] [--tic <id>] [--zenodo] [--zenodo-live]`; `--zenodo` reads `SKYSIFT_ZENODO_TOKEN` and exits 2 with "SKYSIFT_ZENODO_TOKEN is not set; the pack was built without a Zenodo draft" when missing; with a token it creates the deposition, sets the metadata, uploads the zip and prints the draft URL; it never calls publish. `Store.set_pack` and `set_candidate_pack` record the path.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_pack.py
def test_build_pack_writes_every_folder_and_the_zip(env_with_dossier):
    rep = build_pack(...); names = {p.relative_to(rep.out_dir).as_posix() for p in rep.out_dir.rglob("*") if p.is_file()}
    assert {"README.md", "note/note.tex", "data/lightcurve.csv", "data/ephemeris.json", "dossier.html", "provenance/queries.json", "exofop/note.txt"} <= names
    assert rep.zip_path.exists() and zipfile.ZipFile(rep.zip_path).testzip() is None

def test_readme_lists_the_five_steps_and_the_approval_warning(env_with_dossier):
    text = (build_pack(...).out_dir / "README.md").read_text(encoding="utf-8")
    for _n, title in STEPS: assert title in text
    assert "not yet approved" in text and "\u2014" not in text and "stakeholder" not in text.lower()

def test_note_is_valid_aastex_under_a_thousand_words(env_with_dossier):
    tex = render_note(data); body = re.sub(r"\\[a-zA-Z]+(\[[^\]]*\])?(\{[^}]*\})?", " ", tex.split(r"\begin{document}")[1])
    assert tex.startswith(r"\documentclass[RNAAS]{aastex") and len(body.split()) < 1000 and "candidate" in tex and "discover" not in tex.lower()

def test_exofop_names_follow_the_convention():
    assert exofop_name("169822650", "20261021", "skysift", "lightcurve", "csv") == "169822650_20261021_skysift_lightcurve.csv"
    assert exofop_name(None, "20261021", "skysift", "dossier", "html").startswith("TIC-unknown_")

def test_pack_without_a_token_does_not_touch_zenodo(cli_env, monkeypatch):
    monkeypatch.delenv("SKYSIFT_ZENODO_TOKEN", raising=False)
    r = runner.invoke(app, ["pack", "--target", STAGE2_TARGET, "--zenodo"])
    assert r.exit_code == 2 and "SKYSIFT_ZENODO_TOKEN is not set" in r.output and cli_env.session.calls == []

def test_pack_with_zenodo_creates_a_draft_and_records_the_path(env_with_dossier, fake_session):
    rep = build_pack(..., zenodo=ZenodoClient("t", session=fake_session))
    assert rep.zenodo["draft_url"].startswith("https://sandbox.zenodo.org/deposit/") and env_with_dossier.store.dossier(STAGE2_TARGET)["pack_path"] == str(rep.out_dir)

# tests/unit/test_zenodo.py (fake_session records (method, url, json/data) and answers canned JSON)
def test_zenodo_client_never_publishes(fake_session):
    c = ZenodoClient("tok", session=fake_session); d = c.create_deposition(); c.set_metadata(d, {"title": "x"}); c.upload(d, tmp_file)
    assert not hasattr(c, "publish") and all("publish" not in url for _m, url, _b in fake_session.calls)
    assert fake_session.calls[0][1] == "https://sandbox.zenodo.org/api/deposit/depositions"
    assert fake_session.headers["Authorization"] == "Bearer tok"

def test_zenodo_live_flag_switches_the_host(fake_session): ...
def test_zenodo_errors_are_plain(fake_session_500): with pytest.raises(ZenodoError, match="Zenodo answered 500"): ...
def test_metadata_from_citation(): m = metadata_from(data, {"authors": [{"family-names": "Pandya", "given-names": "Vikram"}]}); assert m["creators"] == [{"name": "Pandya, Vikram"}] and m["upload_type"] == "dataset"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/unit/test_pack.py tests/unit/test_zenodo.py -q -p no:cacheprovider`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement `pack.py`, `zenodo.py`, the templates and the CLI command**

Figures are drawn as SVG by the same functions the sparkline uses (`ui.sparkline.render_svg` for the fold; small new helpers in `pack.py` for odd/even, the periodogram envelope and the centroid panel from the stored rung 5 metrics); PNG only when `matplotlib` imports, else the README says the SVGs are the figures.

- [ ] **Step 4: Run the tests to verify they pass**

Run: the Step 2 command. Expected: PASS.

- [ ] **Step 5: Ruff, full suite, commit**

```bash
git add src/skysift/pack.py src/skysift/zenodo.py src/skysift/templates src/skysift/data/rnaas src/skysift/cli.py src/skysift/store/db.py tests/unit
git commit -m "feat(pack): submission pack with step-by-step README, RNAAS draft and an unpublished Zenodo draft"
```

---

### Task 7: Real-data run and the numeric comparison with the hand-built dossier

**Files:**
- Create: `tools/dossier_compare.py`, `docs/superpowers/notes/2026-10-21-dossier-comparison.md`
- Modify: `tests/unit/test_dossier.py` (the golden agreement test from the comparison file), `README.md` (the Dossiers page, the two commands, the Zenodo environment variable)
- Test: `tests/unit/test_dossier.py::test_hand_built_values_agree_within_errors`

**Interfaces:**
- Consumes: Tasks 1 to 6; `docs/candidates/kic-006146676/dossier_data.json` (the hand-built values: `ephemeris.period_days`, `depth.event_mean_ppm` and `event_err_ppm`, `fit.t14_h`, `context.summary.planet_baseline` Rp 2.61 (-0.21 +0.19), a 0.1819, Teq 267 (-12 +13), `pixels.summary.seasons_snrbox_centroid_dist_to_candidate_pix`, the two-source depths per season).
- Produces: `tools/dossier_compare.py --data <generated json> --reference docs/candidates/kic-006146676/dossier_data.json` prints and writes a table (quantity, generated, hand-built, difference in units of the quoted error, agree yes/no) and the FPP factor tables of KIC 6146676 and KIC 5689121 for the sign-off of spec 4.2.

- [ ] **Step 1: Run the generator on a copy of the real database**

Copy `.superpowers/sample-run/engine.db` to `.superpowers/dossier-run/engine.db` (the blob directory is shared read-only: `SKYSIFT_BLOB_URL=file://.superpowers/sample-run/blobs`; nothing in this task writes to the blob except the pixel npz if the recheck has not run; if it has not, run `skysift recheck --signal <id>` first on the copy), then from the repo root:

```bash
SKYSIFT_DATA_DIR=.superpowers/dossier-run SKYSIFT_BLOB_URL=file://.superpowers/sample-run/blobs SKYSIFT_DATABASE_URL=sqlite:///.superpowers/dossier-run/engine.db PYTHONUTF8=1 .venv/Scripts/python.exe -m skysift dossier --target kic-006146676 --export .superpowers/dossier-run/kic-006146676.html
SKYSIFT_DATA_DIR=.superpowers/dossier-run SKYSIFT_BLOB_URL=file://.superpowers/sample-run/blobs SKYSIFT_DATABASE_URL=sqlite:///.superpowers/dossier-run/engine.db PYTHONUTF8=1 .venv/Scripts/python.exe -m skysift dossier --target kic-005689121
SKYSIFT_DATA_DIR=.superpowers/dossier-run SKYSIFT_BLOB_URL=file://.superpowers/sample-run/blobs SKYSIFT_DATABASE_URL=sqlite:///.superpowers/dossier-run/engine.db PYTHONUTF8=1 .venv/Scripts/python.exe -m skysift pack --target kic-006146676
```
Expected: three reports, each in one to three minutes; the Gaia queries logged under the target ids; no other network call (check the `queries` table of the copy).

- [ ] **Step 2: Write `tools/dossier_compare.py` and run it**

Expected: period, depth, duration, Rp, a, Teq agree within the quoted errors; the per-season centroid offsets agree within 0.1 pixel; the two-source depths within their errors. Write the table and both factor tables to `docs/superpowers/notes/2026-10-21-dossier-comparison.md` with a sentence per disagreement.

- [ ] **Step 3: Add the golden test**

```python
def test_hand_built_values_agree_within_errors():
    ref = json.loads(Path("docs/candidates/kic-006146676/dossier_data.json").read_text(encoding="utf-8"))
    gen = json.loads(Path("docs/superpowers/notes/kic-006146676-generated.json").read_text(encoding="utf-8"))   # the star and planet block of the real run, committed with the note (under 20 KB)
    pb = ref["context"]["summary"]["planet_baseline"]
    assert abs(gen["planet"]["rp_rearth"]["median"] - pb["Rp_from_k_Rearth"]["median"]) <= pb["Rp_from_k_Rearth"]["plus"]
    assert abs(gen["planet"]["a_au"]["median"] - pb["a_AU"]["median"]) <= pb["a_AU"]["plus"]
    assert abs(gen["planet"]["teq_k"]["median"] - pb["Teq_K_A0.3_full_redistribution"]["median"]) <= pb["Teq_K_A0.3_full_redistribution"]["plus"]
    assert 0.01 <= gen["fpp"]["value"] <= 0.6
```

- [ ] **Step 4: Dashboard check on the real copy**

Start `skysift ui --no-browser --port 8769` on the copy, open `/dossiers` and `/dossiers/kic-006146676` in both modes and themes at 360 and 1440 px; save the screenshots to the plan workspace; stop the server.

- [ ] **Step 5: README, ruff, full suite, commit**

```bash
git add tools/dossier_compare.py docs/superpowers/notes/2026-10-21-dossier-comparison.md docs/superpowers/notes/kic-006146676-generated.json tests/unit/test_dossier.py README.md
git commit -m "docs(dossier): numeric comparison with the hand-built KIC 6146676 page and the FPP factor tables for review"
```

---

## Self-review notes

- Spec coverage: 4.1 → Task 1; 4.2 → Tasks 1, 2 (gate: `SIGNED_OFF`); 4.3 → Task 5 (pills and the Probabilities block); 5.1 to 5.4, 5.6, 5.7, 5.8 → Task 4 (JSON, row, CLI, export, caveats, pixels from the npz); 5.5 → Task 5; 5.9 → Task 6; 7 → Task 5 step 4 and the contrast tool; 8 → the tests of each task plus Task 7; 9 → the open calls are carried as written (final set, add-one rule, templated voice, JSON as source of truth, old folder stays).
- Type consistency: `Neighbour` is defined once in `fpp.py` and imported by `stars.py`; `Value` carries median/minus/plus everywhere; `look` is the dict `look_numbers` returns and `planet_svg` consumes; `DossierRequest.kind` matches the CLI command names.
- Proportion: the plan carries signatures, formulas and test names; no function bodies beyond the formulas the spec leaves open.
