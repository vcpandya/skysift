# SkySift Two-Candidate Research Note Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A star with two independent stage 2 periods gets one Research Note that reports both candidates, each from its own stored dossier and cross-check, inside the RNAAS word limit, while every single-candidate pack stays byte-identical apart from the commit stamp.

**Architecture:** A `further_dossiers` table holds the second signal's dossier beside the target's own; `skysift dossier --further` writes it and `skysift crosscheck --signal` stores its TRICERATOPS block. `pack.build_pack` collects the ordered candidate list and, when there is more than one, renders a second note template (`note_two.tex.j2`) from per-candidate `numbers()` plus shared sentences in a new module `pack_two.py`, writes the second candidate's files with an `_02` suffix, and extends the README, ExoFOP, Zenodo and plain texts through conditional blocks. Every two-candidate path is reached only through an `others` argument that is empty for a single-candidate pack.

**Tech Stack:** Python 3.12, SQLAlchemy, typer, Jinja2, numpy, matplotlib (optional), pytest. TRICERATOPS 1.1.0 in `.venv-triceratops` for the live run only.

**Spec:** `docs/superpowers/specs/2026-10-27-skysift-two-candidate-note-design.md` (authority), on top of the referee pack spec `docs/superpowers/specs/2026-10-22-skysift-referee-pack-design.md` and the dossiers spec `docs/superpowers/specs/2026-10-20-skysift-dossiers-design.md` (5.4, 5.9). The checklist row is `docs/superpowers/notes/2026-10-22-referee-checklist.md` row 27.

## Global Constraints

- Branch `feat/two-candidate-note` in the worktree `C:\Research\skysift\.worktrees\two-candidate-note` (off main f542d65). Conventional commits ending with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`. Stage files by name. No `git stash`. Never push, never merge, never publish; the Zenodo token is never read.
- Every Python command runs from the worktree as `PYTHONUTF8=1 PYTHONPATH="C:\Research\skysift\.worktrees\two-candidate-note\src;C:\Research\skysift\.worktrees\two-candidate-note" C:\Research\skysift\.venv\Scripts\python.exe -m <module>` (the venv's editable install points at the main checkout's `src`, so the path must name the worktree's). Never a bare `python`, never the `.exe` launchers under `.venv\Scripts` other than `python.exe`. pytest always with `-p no:cacheprovider`. Anything likely to run over two minutes runs in the background.
- `python -m ruff check src tests tools` clean; lines at most 120 columns (checked by hand, E501 is not selected). Tests stay off the network.
- Copy rules: no em-dashes anywhere; British spelling except the -ize family; "candidate", never "discovery" or "confirmed"; every probability keeps its name; candidates are named by period in prose ("the 15.11 d candidate"), by ordinal in file names (`_02`) and ExoFOP targets (`.02`).
- The single-candidate output is sacred: `tests/golden/kic-006146676-generated.json` is not regenerated, no existing pinned sentence in `tests/unit/test_pack.py` changes except the other-signals sentence of Task 5, and every two-candidate branch is behind `others` (a list of the further candidates' JSONs) that is empty or None for a single pack.
- `DOSSIER_VERSION` stays `"6"`; `PACK_VERSION` becomes `"6"` in Task 7.
- The live data dir is `C:\Research\skysift\.superpowers\sample-run` (env `SKYSIFT_DATA_DIR=.superpowers/sample-run`, `SKYSIFT_DATABASE_URL=sqlite:///.superpowers/sample-run/engine.db`, `SKYSIFT_BLOB_URL=file://.superpowers/sample-run/blobs`, `SKYSIFT_RETENTION=keep`, run from the main checkout `C:\Research\skysift` with the worktree's `src` on `PYTHONPATH`); `engine.db` is backed up before any write. Never kill a process other than your own (two dashboards run on ports 8766 and 8768).
- The ledger is `C:\Research\skysift\.superpowers\sdd\2026-10-27-skysift-two-candidate-note\progress.md`; briefs, reports and review diffs sit beside it.

## Review Focus

1. **A further dossier whose signal was rejected after it was generated** (a reviewer changes their mind): the candidate list leaves it out, the README names it in one sentence under step 2, and the note is a single-candidate note again, byte-identical to one built without the row. Pinned by Task 5 `test_a_rejected_further_signal_is_left_out_and_named`.
2. **Two candidates of which only one has a current cross-check** (the second's TRICERATOPS run not yet imported, the live star between steps 2 and 3 of the live run): the TRICERATOPS sentence gives the one candidate's numbers and says no cross-check is stored for the other, the abstract keeps TRICERATOPS first for the one it has, and the gate names the candidate. Pinned by Task 6 `test_one_candidate_without_a_cross_check_is_named_not_guessed`.
3. **A further dossier that is stale** (new test rows on the second signal after a recheck): the pack's stale reason names the signal and the `--further` remedy, the README banner prints it, and a live draft is refused. Pinned by Task 5 `test_a_stale_further_dossier_names_its_signal_and_remedy`.
4. **A further row whose JSON file is gone** (a data folder cleaned by hand): the candidate is left out with a README sentence naming the missing file and the command, nothing raises, and the single-candidate pack is built. Pinned by Task 5 `test_a_further_row_without_its_file_is_left_out`.
5. **A second candidate without a timing fit or a shape fit** (a long period with few transits, `timing.ok` and `shape.ok` False): its signal sentence takes the untimed form with the first mid-transit, its vetting sentence drops the fit clause, the README's step 5 prints its untimed paragraph, and the gate names the candidate for the missing errors. Pinned by Task 6 `test_a_second_candidate_without_errors_prints_its_untimed_forms`.

---

## File structure

| Path | Responsibility |
|---|---|
| `src/skysift/store/db.py`, `src/skysift/store/migrations.py` | `FurtherDossierRow`, `further_dossiers`, the four store methods, migration `005_further_dossiers`. |
| `src/skysift/dossier.py` | `alias_of`, `further_path`, `FURTHER_URL`, `FurtherRefused`, `generate(..., further=)`. |
| `src/skysift/crosscheck.py` | `_stored(..., signal_id)`, `export_inputs(..., signal_id)`, `import_result(..., signal_id)`. |
| `src/skysift/cli.py` | `--further` on `dossier`, `--signal` on `crosscheck`. |
| `src/skysift/pack.py` | `candidates`, `load_candidate`, `candidate_approval`, `coincident_transits`, the `others` argument through `render_note`, `abstract_text`, `gate_problems`, `render_readme`, `_render_plain`, `_write_texts`, `_write_pack`, `_png_figures`, `build_pack`; the `_02` file names; `other_signals_sentence`; `PACK_VERSION = "6"`. |
| `src/skysift/pack_two.py` (new) | Every two-candidate sentence: `title`, `ratio_sentence`, `coincidence_sentence`, `signal_sentence`, `vetting_sentence`, `centroid_sentence`, `planet_sentence`, `fap_pair`, `triceratops_pair`, `in_house_pair`, `tess_pair`, `caveat_union`, `abstract`, `caption`, `summary`, `shared(datas, tex)`. |
| `src/skysift/templates/pack/note_two.tex.j2` (new) | The two-candidate note body. |
| `src/skysift/templates/pack/README.md.j2`, `exofop_note.txt.j2`, `archive_email.txt.j2` | Conditional two-candidate blocks on `two` (the second candidate's numbers) and `f.candidates`. |
| `src/skysift/zenodo.py`, `src/skysift/exofop.py` | `metadata_from(..., others)`, `planet_file_name(date, ordinal)`, `render_exofop_planet(..., ordinal)`. |
| `tests/unit/test_store.py`, `test_migrations.py`, `test_dossier.py`, `test_crosscheck.py`, `test_cli.py` | The data side. |
| `tests/unit/test_two_candidates.py` (new) | The candidate list, the texts, the margin, the files, the identity test. |
| `tests/unit/test_pack.py` | The other-signals test updated (Task 5) and nothing else. |
| `docs/superpowers/notes/2026-10-22-referee-checklist.md` | Row 27 covered. |

The fixture shared by the two-candidate tests: `tests/unit/test_two_candidates.py::env_two` builds on `tests.unit.test_pack.env_with_dossier`, adds a second signal to `STAGE2_TARGET` at `period = 3.156 * first period` (t0 shifted by 1.1 d, depth 500 ppm, SDE 20, 28 events) through `env.store.start_run` and `env.store.add_signal`, sets it `candidate_stage2`, copies the first signal's latest test rows onto it (`env.store.add_test` with each row's `TestResult`), approves it (`env.store.decide`), then runs `generate(..., signal_id=second, further=True)`. A helper `two_live(prior: bool) -> tuple[dict, dict]` returns the golden-based pair of the spec's section 5.3: the first `tests.unit.test_pack._live_shape("dominant", prior, "adopted")`, the second the same JSON with `ephemeris.period_days *= 3.156`, `t0_bkjd += 1.1`, `depth_ppm = 920.0` with error 87, `duration_h = 5.47`, `n_events = 28`, `sde 33.2`, `snr 19.2`, the timing block's `n_used 19`, `n_measured 20`, `rms_min 24.2`, `epoch_ref 16`, `fap` `k 16`, `one_in 120`, `words "1 in 120"`, `fpp.value 0.108`, the planet `rp_rearth` median 2.27, `teq_k` 366, the cross-check `fpp 0.037`, `nfpp 0.012`, and `signal.id` 3480.

---

### Task 1: Measure the two-candidate shape on the live JSONs

**Files:**
- Create: `C:\Research\skysift\.superpowers\sdd\2026-10-27-skysift-two-candidate-note\measure-two.py` (a scratch measurement, not committed)
- Read: `C:\Research\skysift\.superpowers\sample-run\dossiers\kic-006699887\dossier_data.json`, `C:\Users\vik17\AppData\Local\Temp\claude\C--Users-vik17-Downloads\d26c1539-5749-45a9-9d4f-4c68f083ee27\scratchpad\spike\store3480\dossiers\kic-006699887\dossier_data.json` (the 47.69 d dossier generated on a read-only copy of the live store on 27 October; regenerate it with `scratchpad/spike/gen3480.py` if it is gone)
- Modify (only if the measurement demands): the spec's section 5.2 and 5.4

**Interfaces:**
- Produces: a ledger entry with the measured word counts of a hand-written two-candidate note body in the exact sentence forms of spec 5.1 for the live star, with and without a synthetic prior claim on the 15.11 d candidate; the cut list the template must carry.

- [ ] **Step 1: Write the measurement**

The script builds `pack.numbers(data, tex=True)` for both JSONs (the second with the first's cross-check block as a stand-in, as the spike did), writes the two-candidate body by hand as a string in the spec's sentence forms (title, headings, the signals section, vetting, pixels, star and planets, false positives, what is not yet known, the caption, data availability) using those numbers, appends the single note's bibliography from `pack.render_note(data)` and counts with `pack.note_words`.

- [ ] **Step 2: Run it and record**

Run: `PYTHONUTF8=1 PYTHONPATH=... python.exe -I <ledger>/measure-two.py`
Expected: four numbers printed (total, body, abstract, references), total at or under 1,480 with the 130-word abstract; a second run with a 25-word prior-claim sentence on the first candidate also at or under 1,480.

- [ ] **Step 3: Amend the spec if the measurement is over**

If either total is over 1,480, add the next cut from spec 5.2 (the catalogue sentence to the README, then the albedo clause), measure again, and record the final cut list in the spec's 5.4 and the ledger. Commit the spec change only.

- [ ] **Step 4: Ledger**

Append the measured numbers and the cut list to `progress.md`.

---

### Task 2: The `further_dossiers` table

**Files:**
- Modify: `src/skysift/store/db.py` (after `DossierRow`, line 288; the `Store` methods after `set_pack_steps`, line 801)
- Modify: `src/skysift/store/migrations.py:35-58`
- Test: `tests/unit/test_store.py`, `tests/unit/test_migrations.py:40`

**Interfaces:**
- Produces: `class FurtherDossierRow(Base)` with `__tablename__ = "further_dossiers"`, columns `signal_id` (Integer, FK `signals.id`, primary key), `target_id` (String(64), index), `generated` (DateTime), `data_path` (Text), `search_version` (String(32)), `ladder_version` (String(16), nullable), `dossier_version` (String(16)), `fap` (Float, nullable), `fpp` (Float, nullable), `fap_detail` (JSON, default dict), `fpp_detail` (JSON, default dict), `tests_fingerprint` (String(64)), `status_at_generation` (String(16)), `cross_check` (JSON, nullable); `FURTHER_FIELDS = tuple(c.name for c in FurtherDossierRow.__table__.columns)`.
- `Store.upsert_further_dossier(**fields) -> None` (as `upsert_dossier`, keyed by `fields["signal_id"]`, TypeError on an unknown column).
- `Store.further_dossier(signal_id: int) -> dict | None`.
- `Store.further_dossiers(target_id: str) -> list[dict]`: every further row of the target with `period`, `final_status` (from `signals`), `decision` (the latest `DecisionRow.decision`, None without one) and `candidate_status` (`CandidateRow.status`, None without a row), ordered by `period`.
- `Store.set_further_cross_check(signal_id: int, block: dict | None) -> None` (KeyError without a row).
- Migration step `("005_further_dossiers", _step_005_further_dossiers)` appended to `STEPS`, creating the table with `checkfirst=True`.

- [ ] **Step 1: Write the failing tests**

In `tests/unit/test_store.py`, after `test_cross_check_is_set_apart_and_kept_by_a_dossier_update`:

```python
def test_further_dossier_rows_round_trip_by_signal(store_with_signals):
    store, ids = store_with_signals
    fields = dict(target_id="kic-4", generated=datetime(2026, 10, 27), data_path="d/s9/dossier_data.json",
                  search_version="1+abc", ladder_version="4", dossier_version="6", fap=0.01, fpp=0.03,
                  tests_fingerprint="f" * 64, status_at_generation="candidate_stage2")
    store.upsert_further_dossier(signal_id=ids["approved"], **fields)
    row = store.further_dossier(ids["approved"])
    assert row["data_path"] == "d/s9/dossier_data.json" and row["cross_check"] is None
    store.upsert_further_dossier(signal_id=ids["approved"], fpp=0.04)        # a partial update keeps the rest
    assert store.further_dossier(ids["approved"])["fap"] == 0.01
    store.set_further_cross_check(ids["approved"], {"fpp": 0.2})
    assert store.further_dossier(ids["approved"])["cross_check"] == {"fpp": 0.2}
    store.upsert_further_dossier(signal_id=ids["approved"], fpp=0.05)        # a regeneration keeps the block
    assert store.further_dossier(ids["approved"])["cross_check"] == {"fpp": 0.2}
    assert store.further_dossier(10**6) is None
    with pytest.raises(KeyError):
        store.set_further_cross_check(10**6, None)
    with pytest.raises(TypeError):
        store.upsert_further_dossier(signal_id=ids["approved"], colour="red")


def test_further_dossiers_of_a_target_join_the_signal_in_period_order(store_with_signals):
    store, ids = store_with_signals
    # the fixture's two signals of kic-4 (see store_with_signals); the longer period first in the inserts
    ...  # insert both as further rows, assert the list is ordered by period and carries final_status, decision,
         # candidate_status for each, and that another target's rows are not listed
```

The implementer completes the second test from the fixture's signals (`store_with_signals` in `tests/unit/test_store.py`), asserting `period` ascending, `decision == "approve"` on the approved one and None on the other, and `candidate_status`.

In `tests/unit/test_migrations.py:40` the expected steps become `[..., "004_cross_check", "005_further_dossiers"]`, and a new test:

```python
def test_migrate_adds_the_further_dossiers_table(tmp_path):
    url = f"sqlite:///{tmp_path.as_posix()}/t.db"
    s = Store(url); Base.metadata.create_all(s.engine)
    with s.engine.begin() as c:
        c.execute(text("DROP TABLE further_dossiers"))
    s.init_schema()
    assert "further_dossiers" in inspect(s.engine).get_table_names()
    assert migrations.migrate(s.engine) == []
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python.exe -m pytest tests/unit/test_store.py -k further tests/unit/test_migrations.py -p no:cacheprovider -q`
Expected: FAIL (no `upsert_further_dossier`, the steps list differs, no table).

- [ ] **Step 3: Implement the row, the methods and the migration**

`FurtherDossierRow` after `DossierRow` with the docstring "A further dossier (two-candidate note spec 3.2): the dossier of a target's second stage 2 signal, beside the target's own; its ``cross_check`` is stored only by ``skysift crosscheck --import --signal``"; the four methods beside the dossier ones; `_step_005_further_dossiers(conn)` as `_step_003_dossiers`.

- [ ] **Step 4: Run the tests**

Run: `python.exe -m pytest tests/unit/test_store.py tests/unit/test_migrations.py -p no:cacheprovider -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/skysift/store/db.py src/skysift/store/migrations.py tests/unit/test_store.py tests/unit/test_migrations.py
git commit -m "feat(store): the further_dossiers table for a target's second candidate"
```

---

### Task 3: `skysift dossier --further`

**Files:**
- Modify: `src/skysift/dossier.py` (`generate`, line 1915; new helpers beside `pick_signal`, line 287; `_stale_reason`, line 1678)
- Modify: `src/skysift/cli.py:272-308`
- Modify: `src/skysift/pack.py:2779` (`independent_periods` uses `ds.alias_of`)
- Test: `tests/unit/test_dossier.py`, `tests/unit/test_cli.py`

**Interfaces:**
- Consumes: Task 2's store methods.
- Produces in `dossier.py`: `ALIAS_RATIOS = (1.0, 2.0, 3.0, 0.5, 1 / 3)`, `ALIAS_TOLERANCE = 0.02`; `alias_of(period: float, other: float, tolerance: float = ALIAS_TOLERANCE) -> float | None` (the ratio in `ALIAS_RATIOS` that `other` is within `tolerance * ratio * period` of `ratio * period`, else None); `further_path(data_dir, target_id: str, signal_id: int) -> Path` = `<data_dir>/dossiers/<target_id>/s<signal_id>/dossier_data.json`; `FURTHER_URL = "skysift://dossier/{target_id}/s{signal_id}"`; `class FurtherRefused(ValueError)`; `generate(..., further: bool = False)`.
- `pack.independent_periods(period, others, tolerance=0.02)` keeps its signature and returns the same values, through `ds.alias_of`.
- `claims_for(claims: list[dict], period: float, own: bool) -> list[dict]` in `dossier.py` (spec 4.3): the claims whose `period_days` is within 2 percent of `period` (`alias_of(period, claim period) == 1.0`), plus, when `own`, the claims without a period. `generate` stores `claims_for(all, P, own=not further)` and, for a further dossier, `hand_built_localisation = None`.
- `skysift dossier --further` (exit 2 with the `FurtherRefused` message).

- [ ] **Step 1: Write the failing tests**

In `tests/unit/test_dossier.py`, a helper `_second_signal(env, period_factor=3.156) -> int` that adds a stage 2 signal to `STAGE2_TARGET` with the first signal's test rows copied and returns its id (as the file-structure note describes; the first signal is `env.ids["stage2"]`), then:

```python
def test_alias_of_names_the_ratio_within_two_percent():
    assert alias_of(15.1137, 45.34) == 3.0 and alias_of(15.1137, 47.69) is None
    assert alias_of(40.0, 20.3) == 0.5 and alias_of(40.0, 13.3) == pytest.approx(1 / 3) and alias_of(40.0, 40.5) == 1.0


def test_a_further_dossier_is_written_beside_the_targets_own(env):
    first = generate(env.store, env.blob, env.settings, STAGE2_TARGET, fetch=env.fetch)
    second = _second_signal(env)
    rep = generate(env.store, env.blob, env.settings, STAGE2_TARGET, signal_id=second, further=True, fetch=env.fetch)
    assert rep.generated and rep.signal_id == second
    assert rep.data_path == further_path(env.settings.data_dir, STAGE2_TARGET, second) and rep.data_path.is_file()
    assert env.store.dossier(STAGE2_TARGET)["signal_id"] == first.signal_id          # the target's row untouched
    row = env.store.further_dossier(second)
    assert row["target_id"] == STAGE2_TARGET and row["dossier_version"] == DOSSIER_VERSION
    assert json.loads(rep.data_path.read_text(encoding="utf-8"))["signal"]["id"] == second
    again = generate(env.store, env.blob, env.settings, STAGE2_TARGET, signal_id=second, further=True, fetch=env.fetch)
    assert again.generated is False                                                   # current: a no-op


def test_claims_follow_the_candidate_whose_period_they_match():
    claims = [{"doi": "a", "period_days": 15.12}, {"doi": "b", "period_days": 47.7}, {"doi": "c", "period_days": None}]
    assert [c["doi"] for c in claims_for(claims, 15.1137, own=True)] == ["a", "c"]
    assert [c["doi"] for c in claims_for(claims, 47.6905, own=False)] == ["b"]
    assert claims_for(claims, 47.6905, own=True) == [claims[1], claims[2]]


def test_a_further_dossier_stores_only_its_claims_and_no_hand_built_study(env):
    ...  # write a prior_claims.json under dossier_module.CANDIDATES_DIR / STAGE2_TARGET with one claim at the first
         # signal's period and one at 3.156 times it; generate both; the target's JSON holds the first claim only,
         # the further JSON the second only and hand_built_localisation None


def test_a_further_dossier_carries_its_own_cross_check_over(env):
    ...  # generate first and further; set_further_cross_check(second, block); regenerate with force=True;
         # the further JSON's cross_check is the block with current recomputed; the target's row has none


def test_further_is_refused_for_the_best_signal_an_alias_and_a_target_without_a_dossier(env):
    second = _second_signal(env)
    with pytest.raises(FurtherRefused, match="generate the target's dossier first"):
        generate(env.store, env.blob, env.settings, STAGE2_TARGET, signal_id=second, further=True, fetch=env.fetch)
    generate(env.store, env.blob, env.settings, STAGE2_TARGET, fetch=env.fetch)
    with pytest.raises(FurtherRefused, match="is the target's best signal"):
        generate(env.store, env.blob, env.settings, STAGE2_TARGET, signal_id=env.ids["stage2"], further=True,
                 fetch=env.fetch)
    alias = _second_signal(env, period_factor=2.01)
    with pytest.raises(FurtherRefused, match="within 2 percent of 2.0 times"):
        generate(env.store, env.blob, env.settings, STAGE2_TARGET, signal_id=alias, further=True, fetch=env.fetch)
    with pytest.raises(FurtherRefused, match="one named signal"):
        generate(env.store, env.blob, env.settings, STAGE2_TARGET, further=True, fetch=env.fetch)
```

In `tests/unit/test_cli.py`, following its existing dossier-command test's pattern: `--further` without `--signal` exits 2 and prints "a further dossier is for one named signal".

- [ ] **Step 2: Run them to verify they fail**

Run: `python.exe -m pytest tests/unit/test_dossier.py -k "alias or further" tests/unit/test_cli.py -k further -p no:cacheprovider -q`
Expected: FAIL (ImportError on `alias_of`, `further_path`, `FurtherRefused`; TypeError on `further=`).

- [ ] **Step 3: Implement**

In `generate`: when `further`, raise `FurtherRefused` for `signal_id is None` ("a further dossier is for one named signal"), for a target without `store.dossier(target_id)` ("generate the target's dossier first: skysift dossier --target <id>"), for `pick_signal(store, target_id)["id"] == signal_id` ("signal N is the target's best signal; its dossier is the target's own (skysift dossier --target <id>)"), and for `alias_of(P0, P)` not None ("signal N at P d is within 2 percent of R times the target's dossier period P0 d, an alias, not a second candidate", R formatted `:g`), where P0 is the target's dossier JSON `ephemeris.period_days` (its signal row's `period` when the JSON lacks it). Then the same generation with `data_path = further_path(...)`, `old = store.further_dossier(sid)`, the artifact URL `FURTHER_URL`, the cross-check carried from `old`, and `store.upsert_further_dossier(signal_id=sid, target_id=..., <the same fields as the target's row minus the pack columns>)`. `_stale_reason` is reused (its "another signal is now the target's best" branch cannot fire for a further row, since `old["signal_id"] == sid`). `independent_periods` in `pack.py` keeps its loop but tests `ds.alias_of(period, p, tolerance) is None`.

- [ ] **Step 4: Run the tests**

Run: `python.exe -m pytest tests/unit/test_dossier.py tests/unit/test_cli.py tests/unit/test_pack.py -k "alias or further or independent" -p no:cacheprovider -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/skysift/dossier.py src/skysift/cli.py src/skysift/pack.py tests/unit/test_dossier.py tests/unit/test_cli.py
git commit -m "feat(dossier): --further writes a second signal's dossier beside the target's own"
```

---

### Task 4: `skysift crosscheck --signal`

**Files:**
- Modify: `src/skysift/crosscheck.py:170-177` (`_stored`), `508` (`export_inputs`), `800-818` (`import_result`)
- Modify: `src/skysift/cli.py:380-416`
- Test: `tests/unit/test_crosscheck.py`

**Interfaces:**
- Consumes: Task 2's `further_dossier`, `set_further_cross_check`; Task 3's `generate(further=True)`.
- Produces: `_stored(store, target_id, signal_id: int | None = None) -> dict` (the further row's JSON with `signal_id`; `NoDossier` naming `skysift dossier --target <id> --signal <sid> --further` when the row or its file is missing); `export_inputs(store, blob, settings, target_id, out_dir, *, fetch=None, signal_id: int | None = None) -> Path`; `import_result(store, target_id, path, signal_id: int | None = None) -> dict` storing through `set_further_cross_check` when `signal_id` is given.
- CLI: `skysift crosscheck --target <id> --signal <sid> --export <dir> | --import <file>`; `--signal` without a further dossier exits 1 with the `NoDossier` message.

- [ ] **Step 1: Write the failing tests**

In `tests/unit/test_crosscheck.py`, beside the existing export and import tests and with their fixtures:

```python
def test_export_and_import_with_a_signal_use_the_further_dossier(env_two_dossiers, tmp_path):
    env, second = env_two_dossiers                      # a fixture: the target's dossier and a further one
    out = cc.export_inputs(env.store, env.blob, env.settings, STAGE2_TARGET, tmp_path / "cc", signal_id=second)
    inputs = json.loads((out / "inputs.json").read_text(encoding="utf-8"))
    further = json.loads(Path(env.store.further_dossier(second)["data_path"]).read_text(encoding="utf-8"))
    assert inputs["fingerprint"] == cc.inputs_fingerprint(further)
    assert inputs["fingerprint"] != cc.inputs_fingerprint(_stored(env))          # not the target's dossier
    block = cc.import_result(env.store, STAGE2_TARGET, _write(tmp_path / "r.json", fake_result(inputs["fingerprint"])),
                             signal_id=second)
    assert block["current"] is True
    assert env.store.further_dossier(second)["cross_check"]["fpp"] == block["fpp"]
    assert env.store.dossier(STAGE2_TARGET)["cross_check"] is None                  # the target's row untouched


def test_a_signal_without_a_further_dossier_is_refused(env_with_dossier_cc, tmp_path):
    with pytest.raises(cc.NoDossier, match="--further"):
        cc.export_inputs(..., signal_id=10**6)
```

and a CLI test in the file's existing style: `--signal` passes through to `export_inputs` and `import_result` (monkeypatched) and the report carries `"signal_id"`.

- [ ] **Step 2: Run them to verify they fail**

Run: `python.exe -m pytest tests/unit/test_crosscheck.py -k "signal" -p no:cacheprovider -q`
Expected: FAIL (TypeError on `signal_id`).

- [ ] **Step 3: Implement**

`_stored` reads `store.further_dossier(signal_id)` when given; `export_inputs` and `import_result` pass it through, and `import_result` calls `store.set_further_cross_check(signal_id, block)` when given. The CLI option `signal: int | None = typer.Option(None, "--signal", help="The signal id of a further dossier (skysift dossier --further); default the target's own dossier.")`, passed through, with `"signal_id": signal` in the report when given.

- [ ] **Step 4: Run the tests**

Run: `python.exe -m pytest tests/unit/test_crosscheck.py tests/unit/test_cli.py -p no:cacheprovider -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/skysift/crosscheck.py src/skysift/cli.py tests/unit/test_crosscheck.py tests/unit/test_cli.py
git commit -m "feat(crosscheck): --signal exports and imports the further dossier's cross-check"
```

---

### Task 5: The candidate list, the per-candidate stale and gate checks

**Files:**
- Modify: `src/skysift/pack.py` (`_approval`, line 3328; `gate_problems`, line 2658; `other_signal_periods` and `other_signals_sentence`, lines 2793-2816; `build_pack`, line 3474)
- Create: `src/skysift/pack_two.py` (the first functions: `ratio_sentence`, `coincidence_sentence`)
- Create: `tests/unit/test_two_candidates.py` (the `env_two` fixture and `two_live` of the file-structure note)
- Modify: `tests/unit/test_pack.py:1471-1485` (`test_the_readme_names_another_independent_signal`)

**Interfaces:**
- Consumes: Tasks 2 to 4.
- Produces in `pack.py`:
  - `candidates(store, target_id: str) -> list[dict]`: `[{"ordinal": 1, "signal_id": int, "period_days": float, "row": <dossier row>, "further": False}, {"ordinal": 2, ..., "further": True}, ...]` by spec 4.1; the first is the target's row (`store.dossier`), the rest `store.further_dossiers(target_id)` filtered (status `candidate_stage2` or `candidate_status in ("approved", "published")` or `decision == "approve"`, not `decision == "reject"` nor `candidate_status == "rejected"`, the file at `row["data_path"]` present, the period independent of every listed one by `independent_periods`) in period order.
  - `candidates` holds two at most (spec 4.1): a third further dossier by period order is left out.
  - `left_out(store, target_id: str) -> list[dict]`: `{"text": str, "blocks_live": bool}` per further row the list leaves out, with the reason ("signal 3480's further dossier is left out: its status is now rejected" / "its data file <path> is missing; run `skysift dossier --target <id> --signal 3480 --further`" / "its period is within 2 percent of 3 times the 15.114 d candidate's", each `blocks_live` False; "signal N's further dossier is left out: the note shape holds two candidates, and a third needs its own shape (spec section 2)" with `blocks_live` True). `build_pack` appends the texts with `blocks_live` to the gate problems.
  - `load_candidate(cand: dict) -> dict`: the JSON at `cand["row"]["data_path"]` with `cross_check = cc.with_current(row["cross_check"], raw)`.
  - `candidate_approval(store, target_id, cand, data_dir) -> tuple[bool, str | None]`: for a further candidate the fingerprint check against the further row, the reason prefixed "the <P> d candidate's dossier (signal N) is stale: "; for the first exactly `_approval` today. `_approval` keeps its signature and body.
  - `coincident_transits(eph_short: dict, eph_long: dict, t_start: float, t_end: float) -> tuple[int, int]`: `(count, n_long)`, the predicted mid-times of the longer-period ephemeris in `[t_start, t_end]` (BKJD, from `t0_bkjd` and `period_days`) within `(duration_h_short + duration_h_long) / 48` days of a predicted mid-time of the shorter.
  - `gate_problems(raw: dict, others: list[dict] | None = None) -> list[str]`: the per-candidate checks on each JSON prefixed "the <P> d candidate: " when `others`; the note-level checks on `render_note(raw, others=others)`, `abstract_text(raw, others=others)` and `metadata_from(raw, None, prior, others=others)` (Tasks 6 and 7 add those arguments; until then this task calls them without `others` and Task 6 switches).
  - `other_signals_sentence(signals: list[dict], target_id: str) -> str`: `signals` are `{"id", "period"}` of the target's stage 2 signals that are independent of the first candidate and not reported; the text of spec 6.3; `other_signal_periods` becomes `other_signals(store, target_id, reported_ids: set[int]) -> list[dict]`.
- Produces in `pack_two.py`: `ratio_sentence(p_short: float, p_long: float) -> str` (spec 4.2: "The period ratio is 3.156, 5 percent from 3, outside the 2 percent alias tolerance of the search (ratios of 2, 3, 1/2 and 1/3), so neither is an alias of the other."; the distance at one significant figure under 10 percent, else an integer); `coincidence_sentence(count: int, n_long: int, p_short: str, p_long: str, note: bool) -> str | None` ("one of the 31 predicted 47.69 d transits falls within a duration of a 15.11 d transit", counts in words up to nine; None in the note when `count == 0`, the README form "0 of 31 predicted 47.69 d transits coincide with a 15.11 d transit within a duration" always).
- `build_pack` collects `cands = candidates(store, target_id)` and `others = [load_candidate(c) for c in cands[1:]]`, combines `candidate_approval` over the list (approved = all; stale = the reasons joined by "; "), passes `others` to `gate_problems` and (from Task 7) to `_write_pack`, and calls `store.set_candidate_pack` for every candidate's signal.

- [ ] **Step 1: Write the failing tests**

`tests/unit/test_two_candidates.py` with `env_two` and `two_live`, then:

```python
def test_candidates_list_the_targets_own_first_then_further_by_period(env_two):
    cands = pack.candidates(env_two.store, STAGE2_TARGET)
    assert [c["ordinal"] for c in cands] == [1, 2] and cands[0]["further"] is False and cands[1]["further"] is True
    assert cands[0]["signal_id"] == env_two.ids["stage2"] and cands[1]["signal_id"] == env_two.second
    assert cands[1]["period_days"] == pytest.approx(3.156 * cands[0]["period_days"], rel=1e-6)
    assert pack.left_out(env_two.store, STAGE2_TARGET) == []


def test_a_third_further_dossier_is_left_out_named_and_blocks_a_live_draft(env_two, fake_session, monkeypatch):
    from skysift import fpp
    monkeypatch.setattr(fpp, "SIGNED_OFF", True)
    third = _second_signal(env_two, period_factor=7.3)                  # independent of both
    generate(env_two.store, env_two.blob, env_two.settings, STAGE2_TARGET, signal_id=third, further=True,
             fetch=env_two.fetch)
    assert [c["signal_id"] for c in pack.candidates(env_two.store, STAGE2_TARGET)] == [env_two.ids["stage2"],
                                                                                        env_two.second]
    out = pack.left_out(env_two.store, STAGE2_TARGET)
    assert out == [{"text": f"signal {third}'s further dossier is left out: the note shape holds two candidates, and "
                            "a third needs its own shape (spec section 2)", "blocks_live": True}]
    rep = pack.build_pack(env_two.store, env_two.blob, env_two.settings, STAGE2_TARGET)
    assert "holds two candidates" in (rep.out_dir / "README.md").read_text(encoding="utf-8")
    with pytest.raises(pack.PackError, match="holds two candidates"):
        pack.build_pack(env_two.store, env_two.blob, env_two.settings, STAGE2_TARGET,
                        zenodo=ZenodoClient("t", sandbox=False, session=fake_session))


def test_a_rejected_further_signal_is_left_out_and_named(env_two):
    env_two.store.decide(env_two.second, "reject", "a neighbour")
    assert len(pack.candidates(env_two.store, STAGE2_TARGET)) == 1
    assert pack.left_out(env_two.store, STAGE2_TARGET) == [
        {"text": f"signal {env_two.second}'s further dossier is left out: its status is now rejected",
         "blocks_live": False}]
    rep = pack.build_pack(env_two.store, env_two.blob, env_two.settings, STAGE2_TARGET)
    readme = (rep.out_dir / "README.md").read_text(encoding="utf-8")
    assert "further dossier is left out" in readme.split("## Step 2.")[1].split("## Step 3.")[0]
    assert "_02" not in "\n".join(_files(rep))


def test_a_further_row_without_its_file_is_left_out(env_two):
    Path(env_two.store.further_dossier(env_two.second)["data_path"]).unlink()
    assert len(pack.candidates(env_two.store, STAGE2_TARGET)) == 1
    assert "is missing; run `skysift dossier --target" in pack.left_out(env_two.store, STAGE2_TARGET)[0]["text"]


def test_a_stale_further_dossier_names_its_signal_and_remedy(env_two, fake_session, monkeypatch):
    from skysift import fpp
    monkeypatch.setattr(fpp, "SIGNED_OFF", True)
    env_two.store.add_test(env_two.second, TestResult(rung="rung4_novelty", status=TestStatus.pass_, reason="",
                                                      metrics={"toi": 0}), 0.1)
    cands = pack.candidates(env_two.store, STAGE2_TARGET)
    approved, reason = pack.candidate_approval(env_two.store, STAGE2_TARGET, cands[1], env_two.settings.data_dir)
    assert reason.startswith(f"the {cands[1]['period_days']:.3f} d candidate's dossier (signal {env_two.second}) is stale")
    rep = pack.build_pack(env_two.store, env_two.blob, env_two.settings, STAGE2_TARGET)
    assert rep.stale_reason == reason and "--further" in (rep.out_dir / "README.md").read_text(encoding="utf-8")
    with pytest.raises(pack.PackError, match="is stale"):
        pack.build_pack(env_two.store, env_two.blob, env_two.settings, STAGE2_TARGET,
                        zenodo=ZenodoClient("t", sandbox=False, session=fake_session))


def test_coincident_transits_counts_the_longer_periods_overlaps():
    short = {"period_days": 10.0, "t0_bkjd": 100.0, "duration_h": 4.0}
    long = {"period_days": 31.0, "t0_bkjd": 100.0, "duration_h": 4.0}       # epochs 0 and 10 coincide exactly
    assert pack.coincident_transits(short, long, 90.0, 420.0) == (2, 11)
    assert pack.coincident_transits(short, {**long, "t0_bkjd": 105.0}, 90.0, 420.0) == (0, 11)


def test_ratio_and_coincidence_sentences():
    assert pack_two.ratio_sentence(15.113696, 47.690453) == (
        "The period ratio is 3.156, 5 percent from 3, outside the 2 percent alias tolerance of the search (ratios of "
        "2, 3, 1/2 and 1/3), so neither is an alias of the other.")
    assert pack_two.coincidence_sentence(1, 31, "15.11", "47.69", note=True) == (
        "One of the 31 predicted 47.69 d transits falls within a duration of a 15.11 d transit.")
    assert pack_two.coincidence_sentence(0, 31, "15.11", "47.69", note=True) is None
    assert pack_two.coincidence_sentence(0, 31, "15.11", "47.69", note=False) == (
        "0 of 31 predicted 47.69 d transits coincide with a 15.11 d transit within a duration.")


def test_gate_problems_name_the_candidate():
    a, b = two_live(prior=False)
    b["timing"] = {**b["timing"], "ok": False, "reason": "fewer than 3 transits timed"}
    problems = pack.gate_problems(a, others=[b])
    assert any(p.startswith("the 47.69") and "carries no error" in p for p in problems)
```

And in `test_pack.py::test_the_readme_names_another_independent_signal` the assertion `"covers one candidate only" in step2` becomes `"--further`, then build the pack again" in step2 and "27.300 days (signal" in step2`.

- [ ] **Step 2: Run them to verify they fail**

Run: `python.exe -m pytest tests/unit/test_two_candidates.py tests/unit/test_pack.py::test_the_readme_names_another_independent_signal -p no:cacheprovider -q`
Expected: FAIL (no `candidates`, `pack_two`).

- [ ] **Step 3: Implement**

The functions of the Interfaces block. `build_pack`: `cands`, `others`, approval over the list, `left_out` passed to the README as `f["left_out"]` (the texts; Task 7's template prints them under step 2, so this task adds the key with `_fields` default `[]` and the one template line `{% for s in f.left_out %} {{ s }}{% endfor %}` after the other-signals sentence), `gate_problems(raw, others)` plus the `blocks_live` texts, `set_candidate_pack` per candidate. The README's remedy line for a stale pack names both commands when the stale candidate is a further one.

- [ ] **Step 4: Run the tests**

Run: `python.exe -m pytest tests/unit/test_two_candidates.py tests/unit/test_pack.py -p no:cacheprovider -q`
Expected: PASS (the single-candidate tests unchanged).

- [ ] **Step 5: Commit**

```bash
git add src/skysift/pack.py src/skysift/pack_two.py src/skysift/templates/pack/README.md.j2 tests/unit/test_two_candidates.py tests/unit/test_pack.py
git commit -m "feat(pack): the candidate list, per-candidate stale and gate checks, the alias and coincidence sentences"
```

---

### Task 6: The two-candidate note

**Files:**
- Create: `src/skysift/templates/pack/note_two.tex.j2`
- Modify: `src/skysift/pack_two.py`
- Modify: `src/skysift/pack.py` (`render_note`, line 2527; `abstract_text`, line 2366; `_png_figures`, line 3151; `fold_svg`, line 3024)
- Test: `tests/unit/test_two_candidates.py`

**Interfaces:**
- Consumes: Task 5's `two_live`, `ratio_sentence`, `coincidence_sentence`, `coincident_transits`.
- Produces in `pack_two.py` (each takes the per-candidate JSONs, builds through `pack.numbers(d, tex)` and the single-note sentence functions, and returns a `Raw` when `tex`):
  - `period_word(data: dict) -> str`: the period at two decimals for prose ("15.11").
  - `title(datas: list[dict]) -> str`: "Two transit candidates on <name>", "SkySift vetting of two transit candidates on <name>" with a prior claim on either.
  - `signal_sentence(data: dict, tex: bool) -> str`: spec 5.1's form with the SDE, S/N and transit count in a bracket after the period and the timing clause, the untimed form ("with the first mid-transit at BJD_TDB ... (BKJD ...)") when `timing.ok` is not True.
  - `vetting_sentence(data: dict, tex: bool) -> str`: "<P> d: <coverage>; <fit clause>; <odd and even>; <phase 0.5 depth with the 3-sigma limit>; the depth agrees across quarter groups (p = 0.22)." with the fit clause from `pack.shape_rows` as "the fit gives a radius ratio of k (+e −e), b 0.80 and a/R* 19.0 (<degeneracy or density clause of pack._geometry_words>)" and no fit clause when `shape.ok` is not True; a failed stability clause in the rung's words.
  - `centroid_sentence(data: dict, tex: bool) -> str`: spec 5.1's per-candidate centroid sentence, from `pack._pixel_votes`, `ds.partial_box_sentence(short=True)`, the self-referenced offset of `data["centroid_offset"]` with its sigma and "an offset by the rule of \citet{bryson2013}" when `pack.offset_block` says so, and the two-source depths; the no-verdict and not-run forms of `pack.pixel_sentence` when they apply.
  - `planet_sentence(data: dict, tex: bool, albedo: bool) -> str | None`: "The <P> d candidate's radius is R (ratio k), on an orbit of a AU at an equilibrium temperature of T K and I times Earth's insolation" with " at a Bond albedo of 0.3" when `albedo`; None when `star_ok` is False (then the single note's "could not be characterised" sentence prints once).
  - `fap_pair(datas, tex) -> str`, `triceratops_pair(datas, tex) -> str`, `in_house_pair(datas) -> str`, `tess_pair(datas, tex) -> str`: spec 5.1's paired sentences; `triceratops_pair` names the candidate without a current cross-check ("no cross-check is stored for the 47.69 d candidate") and prints the single form for the other.
  - `caveat_union(datas) -> str`: the titles once each plus "The two candidates were vetted singly; no joint fit was made."
  - `abstract(datas) -> str` (spec 5.1), `caption(datas, panels: bool) -> str`, `summary(datas) -> str` ("Two dips, of 0.11 and 0.092 percent, repeat every 15.1 and 47.7 days on a star of magnitude 16.2.").
  - `shared(datas: list[dict], tex: bool, span: tuple[float, float]) -> dict` collecting the above for the template: `title`, `ratio`, `coincidence`, `signals`, `vettings`, `centroids`, `planets`, `fap`, `triceratops`, `in_house`, `tess`, `caveats`, `caption`, `abstract`, `ladder` (the ladder-and-model sentence), `catalogue` (the shared catalogue sentence or None), `photometry` (the shared photometry sentence from `pack.dilution_sentence` without the SAP clause), `fold_clause`.
- Produces in `pack.py`: `render_note(data, doi_placeholder, *, citation=None, panels=True, others: list[dict] | None = None, span: tuple[float, float] | None = None) -> str` rendering `note_two.tex.j2` when `others`; `abstract_text(data, others=None)`; `_png_figures(Figure, data, figures, note, others=None)` drawing the note figure as two fold panels (a) and (b) in one row when `others`, sharing the y axis when the depths are within a factor of two; `note/figure1_02.svg` written by Task 7.
- `span`: the light curve's `(min, max)` BKJD, from `lc.time` in `build_pack`; `coincident_transits` runs on it.

- [ ] **Step 1: Write the failing tests**

```python
def test_the_two_candidate_note_prints_each_value_from_its_own_json():
    a, b = two_live(prior=False)
    tex = render_note(a, others=[b], span=(120.0, 1590.0))
    body = tex.split(r"\begin{document}")[1]
    for value in (numbers(a, tex=True)["period"], numbers(b, tex=True)["period"], "1,160", "920", "4.02", "5.47",
                  "2.85", "2.27", "0.028", "0.037", "1 in 170", "1 in 120", "0.0078", "0.11"):
        assert body.count(str(value)) >= 1, value
    assert body.count("TRICERATOPS 1.1.0") == 1 and body.count("Null searches") <= 1
    assert r"\title{Two transit candidates on KIC 6146676}" in body
    assert "so neither is an alias of the other" in body
    assert body.count(r"\section{") == 7 and "quadratic limb-darkened model" in body
    assert body.count("limb-darkened") == 1                      # the model clause once (C2)
    assert "(d) per-season difference images" not in body and "(b) on the" in body       # C10
    assert "The two candidates were vetted singly" in body
    assert EM_DASH not in tex and not IZE.search(tex) and "discover" not in tex.lower()


def test_the_two_candidate_live_shape_stays_inside_the_word_limit_with_margin():
    for prior in (False, True):
        a, b = two_live(prior=prior)
        tex = render_note(a, others=[b], span=(120.0, 1590.0))
        words = note_words(tex)
        assert words["total"] <= 1480 and words["abstract"] <= 130, (prior, words)
        assert not [p for p in pack.gate_problems(a, others=[b]) if "words" in p]


def test_the_two_candidate_abstract_carries_its_pinned_clauses():
    a, b = two_live(prior=False)
    abstract = pack.abstract_text(a, others=[b])
    assert abstract.startswith("This note reports two transit candidates on KIC 6146676, a star Kepler recorded only in "
                               "other targets' pixels: a ")
    assert "Null searches give false-alarm rates of 1 in 170 and 1 in 120 per star;" in abstract        # C20
    assert ("TRICERATOPS 1.1.0 gives false-positive probabilities of 0.012 and 0.037 (no validation class applies at "
            "30-minute cadence; Giacalone et al. 2021)") in abstract                                    # C19
    assert "box cut by the stamp edge" not in abstract                                                   # C21
    assert abstract.endswith("Both remain candidates pending follow-up.") and len(abstract.split()) <= 130


def test_one_candidate_without_a_cross_check_is_named_not_guessed():
    a, b = two_live(prior=False)
    b["cross_check"] = None
    tex = render_note(a, others=[b], span=(120.0, 1590.0))
    assert "no cross-check is stored for the 47.69 d candidate" in tex and "0.037" not in tex
    assert any("47.69 d candidate: no TRICERATOPS cross-check" in p for p in pack.gate_problems(a, others=[b]))
    abstract = pack.abstract_text(a, others=[b])
    assert "TRICERATOPS 1.1.0 gives" in abstract and len(abstract.split()) <= 130


def test_a_second_candidate_without_errors_prints_its_untimed_forms():
    a, b = two_live(prior=False)
    b["timing"] = {**b["timing"], "ok": False, "reason": "fewer than 3 transits timed"}
    b["shape"] = {**b["shape"], "ok": False, "reason": "the bootstrap did not converge"}
    tex = render_note(a, others=[b], span=(120.0, 1590.0))
    assert "with the first mid-transit at" in tex and tex.count("the centre of the series") == 1
    assert tex.count("gives a radius ratio") == 1


def test_candidates_with_different_pixel_verdicts_get_one_abstract_sentence_each():
    a, b = two_live(prior=False)
    b["tests"] = [{**t, "status": "undetermined", "reason": "no voting season"} if t["rung"] == "rung5_pixels" else t
                  for t in b["tests"]]
    abstract = pack.abstract_text(a, others=[b])
    assert "both dips" not in abstract and "gave no verdict" in abstract


def test_the_two_panel_figure_is_drawn_when_matplotlib_is_present(tmp_path):
    Figure = pack._matplotlib()
    if Figure is None:
        pytest.skip("no matplotlib")
    a, b = two_live(prior=False)
    (tmp_path / "f").mkdir(); (tmp_path / "n").mkdir()
    pack._png_figures(Figure, a, tmp_path / "f", tmp_path / "n", others=[b])
    assert (tmp_path / "n" / "figure1.pdf").is_file() and (tmp_path / "n" / "figure1.png").is_file()
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python.exe -m pytest tests/unit/test_two_candidates.py -k "note or shape or cross_check or untimed or verdicts or figure" -p no:cacheprovider -q`
Expected: FAIL (TypeError on `others`).

- [ ] **Step 3: Write `note_two.tex.j2`**

The same frame as `note.tex.j2` (its header comment, `\begin{document}`, the author loop, keywords, data availability, software, facilities, bibliography) with the body of spec 5.1 from `s = shared(...)` and `cands` (the `numbers()` dicts): `\title{\VAR{s.title}}`; the abstract `\VAR{s.abstract}`; `\section{The signals}` with the opening sentence built in the template from `cands[0]` (name, Gaia id, G, the search window) followed by `\VAR{s.ratio}`, `\VAR{s.coincidence}` when set, then `\BLOCK{for x in s.signals}\VAR{x}\BLOCK{endfor}` and the fold clause; `\section{Vetting}` with `\VAR{s.ladder}`, the vetting sentences, `\VAR{s.catalogue}`; `\section{Which star is dimming}` with `\VAR{s.photometry}` then the centroid sentences; `\section{The star and planets}` with the single template's star paragraph verbatim on `n = cands[0]` then the planet sentences; `\section{How likely is a false positive}` with `s.fap`, `s.triceratops`, `s.in_house`, `s.tess`; `\section{What is not yet known}` with `s.caveats`; the figure with `\VAR{s.caption}`; the shared tail.

- [ ] **Step 4: Implement `pack_two.py` and the `others` arguments**

`render_note` chooses the template by `others`; the bibliography is the union of `references(d)` over all JSONs plus `prior_bib` of each. `abstract_text(data, others)` returns `pack_two.abstract([data, *others])` when `others`. `_png_figures` with `others` draws the two fold panels from each JSON's `plots` (the `fold` inner function applied to each axis with its own `plots`), titles "(a)" and "(b)", `sharey` when `max(depth) / min(depth) <= 2`, saves `figure1.png` and `figure1.pdf`, then the per-candidate PNGs as today for the first and with the `_02` names of Task 7 for the second (`figures/fold_02.png`, `oddeven_02.png`, `periodogram_02.png`, `centroids_02.png`).

- [ ] **Step 5: Run the tests**

Run: `python.exe -m pytest tests/unit/test_two_candidates.py tests/unit/test_pack.py -p no:cacheprovider -q`
Expected: PASS. If the margin test fails, apply the next cut from spec 5.2 in `pack_two.py` and amend the spec's 5.4 in the same commit.

- [ ] **Step 6: Commit**

```bash
git add src/skysift/pack.py src/skysift/pack_two.py src/skysift/templates/pack/note_two.tex.j2 tests/unit/test_two_candidates.py
git commit -m "feat(note): the two-candidate Research Note shape inside the 1,480-word margin"
```

---

### Task 7: The pack's files and texts for two candidates

**Files:**
- Modify: `src/skysift/pack.py` (`_write_pack`, line 3561; `_write_texts`, line 3452; `_fields`, line 2822; `render_readme`, line 2878; `_render_plain`; `_exofop_list`, line 2646; `file_notes`, line 2596; `_versions`, line 3658; `PACK_VERSION`, line 124)
- Modify: `src/skysift/exofop.py:11-12, 105`; `src/skysift/zenodo.py:239`
- Modify: `src/skysift/templates/pack/README.md.j2`, `exofop_note.txt.j2`, `archive_email.txt.j2`
- Test: `tests/unit/test_two_candidates.py`, `tests/unit/test_zenodo.py`

**Interfaces:**
- Consumes: Tasks 5 and 6.
- Produces: `suffixed(name: str, ordinal: int) -> str` in `pack.py` (`"data/ephemeris.csv", 2` to `"data/ephemeris_02.csv"`; ordinal 1 returns the name); `exofop.planet_file_name(date: str, ordinal: int = 1) -> str` (`params_planet_<date>_00<ordinal>.txt`); `exofop.render_exofop_planet(data, date, tic, tag, ordinal: int = 1)` with target `TIC<tic>.0<ordinal>`; `zenodo.metadata_from(data, citation, prior=None, others: list[dict] | None = None)` (spec 6.4); `render_readme(data, report_fields, others=None)`, `_render_plain(name, data, f, others=None)`, `_write_texts(..., others)`, `_write_pack(..., others, span)`; template variables `two` (the second candidate's `numbers()` dict or None), `cands` (the list of `numbers()` dicts), `s` (`pack_two.shared` in plain form) and `f.candidates` (`[{ordinal, signal_id, period_days, generated_words}]`, empty for one); `_versions(..., cands)` adding `candidates` only when more than one; `PACK_VERSION = "6"`.

- [ ] **Step 1: Write the failing tests**

```python
def test_a_two_candidate_pack_writes_the_second_candidates_files_with_the_suffix(env_two):
    rep = pack.build_pack(env_two.store, env_two.blob, env_two.settings, STAGE2_TARGET)
    names = _files(rep)
    for name in ("data/ephemeris_02.csv", "data/ephemeris_02.json", "data/transit_times_02.csv", "data/shape_02.csv",
                 "data/events_02.csv", "data/stellar_provenance_02.csv", "data/periodogram_02.json",
                 "data/rung_metrics_02.json", "data/lightcurve_02.csv", "note/figure1_data_02.csv",
                 "figures/fold_02.svg", "figures/oddeven_02.svg", "figures/periodogram_02.svg", "dossier_02.html",
                 "provenance/calibration_02.json", "note/figure1_02.svg"):
        assert name in names, name
    assert {n for n in names if n.startswith("exofop/params_planet")} == {
        f"exofop/params_planet_{rep.date}_001.txt", f"exofop/params_planet_{rep.date}_002.txt"}
    second = (rep.out_dir / "exofop" / f"params_planet_{rep.date}_002.txt").read_text(encoding="utf-8")
    assert "TIC<TIC-TO-FILL>.02" in second and f"-s{env_two.second}" in second
    assert any(n.startswith("exofop/") and "fold-02" in n for n in names)
    versions = json.loads((rep.out_dir / "provenance" / "versions.json").read_text(encoding="utf-8"))
    assert [c["ordinal"] for c in versions["candidates"]] == [1, 2] and versions["pack_version"] == "6"
    hashes = json.loads((rep.out_dir / "provenance" / "hashes.json").read_text(encoding="utf-8"))
    assert "data/ephemeris_02.csv" in hashes
    assert env_two.store.candidate(env_two.second)["pack_path"] == str(rep.out_dir)   # or the store's accessor


def test_the_two_candidate_readme_zenodo_and_plain_texts_name_both(env_two):
    rep = pack.build_pack(env_two.store, env_two.blob, env_two.settings, STAGE2_TARGET)
    readme = (rep.out_dir / "README.md").read_text(encoding="utf-8")
    p1, p2 = (f"{c['period_days']:.3f} d" for c in pack.candidates(env_two.store, STAGE2_TARGET))
    assert f"| quantity | {p1} | {p2} |" in readme and "## The star" in readme
    assert "so neither is an alias of the other" in readme and "within a duration" in readme
    assert "needs its own dossier and pack" not in readme and "covers one candidate only" not in readme
    assert readme.count("Observers who plan a transit ask for the ephemeris first") == 1
    assert readme.split("## Step 5.")[1].count("mid-transit times are T =") == 2
    assert "`data/ephemeris_02.csv`" in readme.split("## What is in the pack")[1]
    meta = json.loads((rep.out_dir / "zenodo_metadata.json").read_text(encoding="utf-8"))
    assert meta["title"].startswith("SkySift candidates KIC") and " and " in meta["title"]
    assert meta["description"].count("Ephemeris, ") == 2
    note = (rep.out_dir / "exofop" / "note.txt").read_text(encoding="utf-8")
    assert note.count("Ephemeris") == 2 and "_002" not in note and "-02" in note     # the files line names the copies
    email = (rep.out_dir / "archive_email.txt").read_text(encoding="utf-8")
    assert email.count(" d,") >= 2 or "two transit candidates" in email
    for text in (readme, note, email, meta["description"]):
        assert EM_DASH not in text and not IZE.search(text) and "discover" not in text.lower()


def test_a_single_candidate_pack_is_what_it_was(env_with_dossier):
    """The guard of spec 5.5: a target without a further dossier builds exactly today's files, and its note, README
    and plain texts carry none of the two-candidate words."""
    rep = _build(env_with_dossier)
    names = _files(rep)
    assert not any("_02" in n or "-02" in n or "_002" in n for n in names)
    versions = json.loads((rep.out_dir / "provenance" / "versions.json").read_text(encoding="utf-8"))
    assert "candidates" not in versions
    for name in ("note/note.tex", "README.md", "exofop/note.txt", "archive_email.txt"):
        text = (rep.out_dir / name).read_text(encoding="utf-8")
        assert "Two transit candidates" not in text and "## The star" not in text and "vetted singly" not in text
```

In `tests/unit/test_zenodo.py`: `metadata_from(a, None, others=[b])` on `two_live` gives the two-candidate title and a description with two "Ephemeris, " paragraphs, both FAP sentences and the caveat union; without `others` the metadata is unchanged (assert equality with today's call).

- [ ] **Step 2: Run them to verify they fail**

Run: `python.exe -m pytest tests/unit/test_two_candidates.py -k "files or texts or what_it_was" tests/unit/test_zenodo.py -p no:cacheprovider -q`
Expected: FAIL.

- [ ] **Step 3: Implement**

`_write_pack` loops over `[(1, data, raw, fnorm), (2, other_public, other_raw, ds.whitened(lc, other_raw["signal"]))...]` writing each per-candidate file through `suffixed(name, ordinal)`; the shared files (README, `note/note.tex`, `note/figure1.*`, `exofop/note.txt`, `archive_email.txt`, `zenodo_metadata.json`, `provenance/versions.json`, `queries.json`, `hashes.json`, the stellar file, the hand-built file) once. `note/figure1_02.svg` is the second candidate's `fold_svg`. `_exofop_list(target_id, date, png, pixels_figure, ordinal=1)` gives the content `fold-02` form. `file_notes` returns notes for suffixed names by stripping the suffix and appending " (the <P> d candidate)", and appends " (the <P> d candidate)" to the first candidate's per-candidate notes only when `f.candidates` has two entries. The README template: the table header and rows take the per-candidate values from `cands`, the per-target rows move to a "The star" table under it inside `{% if two %}`; the methods paragraphs print once when `cands[0][key] == cands[1][key]`, else labelled per candidate; step 2's planet-file sentence, step 5's loop, the caveats and the pack contents as spec 6.2. `exofop_note.txt.j2` and `archive_email.txt.j2` take `{% if two %}` blocks. `metadata_from` with `others` as spec 6.4.

- [ ] **Step 4: Run the tests**

Run: `python.exe -m pytest tests/unit/test_two_candidates.py tests/unit/test_pack.py tests/unit/test_zenodo.py tests/unit/test_ui_dossiers.py -p no:cacheprovider -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/skysift/pack.py src/skysift/exofop.py src/skysift/zenodo.py src/skysift/templates/pack/ tests/unit/test_two_candidates.py tests/unit/test_zenodo.py
git commit -m "feat(pack): the second candidate's files, the README table, ExoFOP .02, Zenodo and plain texts for two candidates"
```

---

### Task 8: The checklist row and the docs

**Files:**
- Modify: `docs/superpowers/notes/2026-10-22-referee-checklist.md:43, 53`
- Modify: `docs/superpowers/specs/2026-10-27-skysift-two-candidate-note-design.md` (section 10, if any call changed during Tasks 1 to 7)

- [ ] **Step 1: Row 27**

Status column: "covered (27 October 2026): one note per star with one row per candidate, as Thompson et al. 2018 Table 2; `skysift dossier --further` stores the second signal's dossier, `skysift crosscheck --signal` its TRICERATOPS block, and the pack renders the two-candidate note (spec `2026-10-27-skysift-two-candidate-note-design.md`); KIC 6699887's 15.11 d and 47.69 d candidates are the live case". Section 3's bullet on row 27 gains "Covered by the two-candidate note plan of 27 October."

- [ ] **Step 2: Commit**

```bash
git add docs/superpowers/notes/2026-10-22-referee-checklist.md docs/superpowers/specs/2026-10-27-skysift-two-candidate-note-design.md
git commit -m "docs(checklist): row 27 covered by the two-candidate note"
```

---

### Task 9: The live run on KIC 6699887

**Files:**
- Read and write: `C:\Research\skysift\.superpowers\sample-run` (the live data dir); scratch folders under the ledger folder.
- Create: `<ledger>/merge_runs.py` (the TRICERATOPS merge, not committed), `<ledger>/live-run.md` (the report).

All commands from `C:\Research\skysift` with the worktree's `src` on `PYTHONPATH` and the live environment of the Global Constraints.

- [ ] **Step 1: Back up**

`copy .superpowers\sample-run\engine.db .superpowers\sample-run\engine.db.bak-20261027-two.db` (and the `-wal` file if present, after a checkpoint through a read-only `PRAGMA wal_checkpoint`).

- [ ] **Step 2: The further dossier**

Run: `python.exe -m skysift dossier --target kic-006699887 --signal 3480 --further`
Expected: exit 0, "wrote .superpowers/sample-run/dossiers/kic-006699887/s3480/dossier_data.json"; the target's own row unchanged (`skysift dossier --target kic-006699887` says current).

- [ ] **Step 3: The TRICERATOPS run for 3480 (background, started as soon as step 2 is done)**

`python.exe -m skysift crosscheck --target kic-006699887 --signal 3480 --export <ledger>/cc-3480`; then five processes, each in its own folder, `..\.venv-triceratops\Scripts\python.exe -I tools\triceratops_check.py --inputs <ledger>/cc-3480 --out <ledger>/cc-3480/run-<seed>/result.json --runs 4 --seed <seed>` for seeds 1, 5, 9, 13 and 17, in the background. `merge_runs.py` then builds one schema-3 result: `runs` concatenated in seed order, `n_runs` 20, `n_degenerate` the sum, the summary keys from `crosscheck.summarise(runs)`, `scenarios` of the run nearest the median FPP, `stars`, `inputs_sha256`, `caveats`, `settings` (with `seeds` concatenated and `median_run_seed`) from the first file, and `variant_excluded` merged the same way over the five files' variants; `crosscheck.validate_result` must accept it. Then `python.exe -m skysift crosscheck --target kic-006699887 --signal 3480 --import <ledger>/cc-3480/result.json` and record the medians (expected near 0.037, 0.015 without STP, 0.024 pixel-excluded, 0.012 NFPP; a difference is reported).

- [ ] **Step 4: The pack**

Run: `python.exe -m skysift pack --target kic-006699887 --out <ledger>/pack-two`
Expected: exit 0; the README's step 3 line gives the word counts; `note/note.tex` is the two-candidate note; the `_02` files present.

- [ ] **Step 5: Compile**

Copy `note/note.tex` and `note/figure1.pdf` to `<ledger>/tex`, replace `\documentclass{aastex7}` with `\documentclass{aastex701}`, run `pdflatex -interaction=nonstopmode note.tex` twice, confirm `note.pdf` exists and the log has no "Undefined control sequence"; record the PDF path and the word counts (total, body, abstract, references).

- [ ] **Step 6: The single-candidate identity check**

`python.exe -m skysift pack --target kic-006146676 --out <ledger>/pack-6146676` and the same for `kic-005689121`; diff each `note/note.tex` against `.superpowers/packs-author/<target>/note/note.tex`: the only differing lines name the commit (`\software{...commit ...}` and the data-availability line). Record the diff output.

- [ ] **Step 7: The ledger and the report**

`live-run.md`: the backup name and time, every command with its time, the TRICERATOPS medians against round 4's, the word counts, the PDF path, the two diffs.

---

### Task 10: The full gate

- [ ] **Step 1: ruff**

Run: `python.exe -m ruff check src tests tools` from the worktree. Expected: clean.

- [ ] **Step 2: The gate (background)**

Run: `python.exe -m pytest tests -m "not slow and not golden" -p no:cacheprovider -q > <ledger>/gate-<tip>.log 2> <ledger>/gate-<tip>.err` in the background (about two hours).
Expected: every test passed; the counts recorded in the ledger with the tip hash.

- [ ] **Step 3: Whole-branch review**

Dispatch the final review of the branch (`git diff f542d65..HEAD`) against the spec; fix findings in a fix wave; rerun the touched test files; record in the ledger.
