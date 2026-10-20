# SkySift Mission Control UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the dashboard its mission-control identity (dark-sky design system, Tonight home with a discovery feed, candidate cards with transit sparklines, flight-deck Live page), simple and expert modes, and a verdict popup that explains every check in plain words with what was missing and what happens next.

**Architecture:** Server-rendered Jinja2 stays the only view layer. A pure module `skysift.ui.explain` turns stored rung rows into sentences, next steps and a verdict object; every template, the popup and the About page read from it. Mode and theme are cookies read by `render()`. One stylesheet of custom properties and components replaces `app.css`; one dependency-free script handles toggles, the dialog, count-ups and keys. Fonts are committed woff2 files served from `static/fonts`. A new `/api/signals/{id}/sparkline.svg` endpoint renders a cached inline-SVG fold for cards.

**Tech Stack:** Python 3.12, FastAPI, Jinja2, HTMX 2.0.3 (vendored), Plotly 2.35.2 (vendored), Space Grotesk + IBM Plex Sans + IBM Plex Mono woff2 (committed, OFL), pytest with `tests/unit/ui_seed.py`. No new Python dependencies.

**Spec:** `docs/superpowers/specs/2026-10-20-skysift-mission-control-design.md` (authority), on top of `docs/superpowers/specs/2026-10-17-skysift-dashboard-polish-design.md` sections 2, 5, 6, 7 and `2026-10-14-skysift-dashboard-design.md` sections 5 to 7 (unchanged behaviour).

## Global Constraints

- Never run a console `.exe` from `.venv/Scripts`; every command is `.venv/Scripts/python.exe -m <module>` with `PYTHONUTF8=1`.
- Branch `feat/mission-control` off `main`. Conventional commits ending with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`. Never push.
- Lint `python -m ruff check src tests` clean (line length 120). `python -m pytest tests/unit -q -p no:cacheprovider` green; the UI files (`test_ui_pages.py`, `test_ui_review.py`, `test_ui_queries.py`, `test_ui_explain.py`, `test_assets.py`, `test_sweeps.py`) are the iteration loop; the full unit suite runs once per task before its commit.
- No runtime network call from any page: fonts, HTMX and Plotly are local; the page degrades to the system font stack when a font file is missing.
- Status is never carried by colour alone; every status colour sits beside a word or an icon. Contrast AA in both themes (4.5:1 text, 3:1 large text and UI), pinned by `tools/contrast_check.py`.
- Every page renders in both modes (`simple`, `expert`) and both themes with the seeded data AND with signals that predate the M3 and ladder-3 metrics (no `stacked`, `period_matched`, `alias_resolved`, `detrend`, `prewhitening`, `suggested_period`, `fit_k`, rung1_events row): templates and `explain` guard every missing key.
- Routes for decisions, the review JSON, the CSRF token, loopback binding, the sweep launcher, `/api/signals/{id}/plots.json` and append-only decisions do not change. Templates may be restructured freely.
- Copy: short declarative sentences, no jargon in simple mode, every unavoidable term glossed, no em-dashes anywhere, no "discovery" wording before a human approval.
- Statuses the UI must know: `candidate_stage1`, `candidate_stage2`, `undetermined`, `rejected`, `superseded`, `pending`; decisions `approve`, `reject`, `needs_data`. `candidate_stage2` and `superseded` exist in the schema only after the recheck plan lands; the UI treats them as valid values now (chips, pills, glossary) and the seed gains one stage-2 signal.
- Each task ends with screenshots at 360, 768, 1024 and 1440 px in dark and light taken through the built-in browser against the dashboard on the seed data (`SKYSIFT_DATA_DIR=.superpowers/ui-seed SKYSIFT_BLOB_URL=file://.superpowers/ui-seed/blobs SKYSIFT_DATABASE_URL=sqlite:///.superpowers/ui-seed/engine.db PYTHONUTF8=1 .venv/Scripts/python.exe -m skysift ui --no-browser --port 8766`; reload static with `?v=`), saved under the plan's SDD workspace and named in the report.

## Review Focus

1. **A signal with no test rows at all** (vetting crashed before the first rung, `final_status` pending): the queue, the card, the popup and the signal page must render a "not vetted yet" verdict instead of raising. Pinned by Task 2 `test_verdict_with_no_tests_is_not_vetted` and Task 3 `test_verdict_popup_for_a_signal_without_tests`.
2. **An unknown reason string** (a future rung, or a free-text exception): the popup must show the raw reason and "needs a person" as the next step, never an empty block. Pinned by Task 2 `test_unknown_reason_falls_back_to_the_raw_text`.
3. **A candidate with a reviewer decision on top** (approved or rejected by a person): the pill shows the person's decision first and the engine's verdict second, and the Tonight feed does not list it as waiting. Pinned by Task 2 `test_verdict_puts_the_reviewer_decision_first` and Task 5 `test_tonight_feed_excludes_decided_candidates`.
4. **A phone at 360 px** must show the Tonight hero, one card per row and the bottom navigation without horizontal scroll, and the dialog must fit the viewport. Pinned by Task 1 `test_stylesheet_declares_the_phone_breakpoints` (structural) and the Task 7 screenshot review.
5. **A sparkline for a signal whose raw light curve is no longer cached** (retention `stream`): the endpoint returns a placeholder SVG with the words "light curve not cached" and status 200, and the card still renders. Pinned by Task 4 `test_sparkline_without_a_cached_curve_returns_a_placeholder`.

---

## File structure

| Path | Responsibility |
|---|---|
| `src/skysift/ui/static/app.css` | Rewritten: tokens (both themes), starfield, type scale, layout grid, bottom nav, components (card, tile, pill, chip, ring, dialog, toast, drawer, table, empty state), breakpoints, reduced motion. |
| `src/skysift/ui/static/app.js` | Rewritten: mode and theme toggles (POST `/prefs`), dialog open/close/focus, count-up, keyboard map (`j`/`k`/`a`/`r`/`n`, `?` for hints), toast, split-view swap. No dependency. |
| `src/skysift/ui/static/fonts/` | `SpaceGrotesk-{Medium,Bold}.woff2`, `IBMPlexSans-{Regular,Medium,SemiBold}.woff2`, `IBMPlexMono-{Regular,Medium}.woff2`, `LICENSES.md` (OFL texts and sources). |
| `src/skysift/ui/app.py` | `render()` adds `mode`, `theme`, `explain` helpers; `/prefs` POST; `/about` GET; `NAV` reordered; `/` becomes Tonight and `/sources` the start page. |
| `src/skysift/ui/explain.py` | Pure: `Verdict`, `Why`, `verdict()`, `why()`, `reason_in_words()`, `summary_sentence()`, `copy_summary()`, `RUNG_NAMES`, `STATUS_GLOSSARY`, `GLOSSARY`. |
| `src/skysift/ui/sparkline.py` | `fold_points(lc, signal) -> (hours, ppm, model)` reuse of `plots` helpers; `render_svg(...) -> str`; `placeholder_svg() -> str`; disk cache under `<data_dir>/ui/sparklines/`. |
| `src/skysift/ui/queries.py` | Adds `recent_candidates`, `tonight`, `status_counts`, `signal_card` fields (`stage`, `verdict`, `summary`). |
| `src/skysift/ui/routes/home.py` | `/` Tonight page and its HTMX partial. |
| `src/skysift/ui/routes/review.py` | Popup route, cards/table switch, search, split view, copy summary. |
| `src/skysift/ui/routes/api.py` | `/api/signals/{id}/sparkline.svg`. |
| `src/skysift/ui/templates/base.html`, `partials/{topbar,bottomnav,stat,pill,chip,empty,toast}.html` | Shell and shared components. |
| `src/skysift/ui/templates/{tonight,candidates,signal,live,sources,approved,about}.html` and `partials/{card,verdict,why,queue_row,signal_detail,live_*,sweep_form,decision_bar}.html` | Pages. |
| `tools/contrast_check.py` | Reads `app.css` custom properties and asserts the AA pairs listed in Task 1. |
| `tests/unit/ui_seed.py` | Gains a legacy signal (ladder 1 metrics only), a stage-2 signal, a decided candidate, a signal with no tests. |
| `tests/unit/test_ui_explain.py`, `test_ui_pages.py`, `test_ui_review.py`, `test_ui_queries.py`, `test_assets.py` | Tests named in the tasks. |

---

### Task 1: Design system, shell, modes, themes, fonts, contrast tool

**Files:**
- Create: `src/skysift/ui/static/fonts/*.woff2`, `src/skysift/ui/static/fonts/LICENSES.md`, `tools/contrast_check.py`, `src/skysift/ui/templates/partials/{topbar,bottomnav,stat,pill,chip,empty,toast}.html`
- Modify: `src/skysift/ui/static/app.css` (rewrite), `src/skysift/ui/static/app.js` (rewrite), `src/skysift/ui/app.py`, `src/skysift/ui/templates/base.html`, `src/skysift/ui/templates/error.html`
- Test: `tests/unit/test_ui_pages.py`, `tests/unit/test_assets.py`

**Interfaces:**
- Produces: `render(request, name, status_code=200, **ctx)` context gains `mode: str` ("simple" default, "expert"), `theme: str` ("system" default, "dark", "light"), `page_title: str`. Cookies `skysift_mode`, `skysift_theme` (path `/`, SameSite Lax, 1 year); `?mode=` and `?theme=` query values win for that request. `POST /prefs` (token required) with form fields `mode`, `theme`, `next` (a same-origin path, else `/`) sets the cookies and answers 303 to `next`. `<html data-mode="..." data-theme="...">`; CSS tokens `--bg --surface --raised --line --fg --fg-2 --muted --ion --solar --pass --fail --undet --running` with the hex values of spec section 3 (dark on `[data-theme=dark]` and under `prefers-color-scheme: dark` when `data-theme=system`); `.only-expert` and `.only-simple` utility classes; component classes `.card .tile .pill .chip .ring .dialog .toast .drawer .empty .bottomnav .topbar`; breakpoints 720 px (cards stack), 900 px (bottom nav), 1280 px (split view).
- `tools/contrast_check.py`: `python -m tools.contrast_check` exits 0 when every pair in `PAIRS` (fg on bg, fg on surface, fg-2 on surface, muted on surface at 3:1, ion on surface, solar on surface, each status colour on surface and on bg, white on ion, white on fail) meets its ratio in both themes; prints the failing pair otherwise.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_ui_pages.py
def test_mode_and_theme_cookies_drive_the_html_attributes(settings):
    client, app = make_client(settings)
    assert 'data-mode="simple"' in client.get("/").text and 'data-theme="system"' in client.get("/").text
    r = client.post("/prefs", data={"mode": "expert", "theme": "dark", "next": "/candidates", "_token": app.state.token},
                    follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/candidates"
    assert "skysift_mode=expert" in r.headers.get("set-cookie", "") or r.cookies.get("skysift_mode") == "expert"
    html = client.get("/").text
    assert 'data-mode="expert"' in html and 'data-theme="dark"' in html
    assert 'data-mode="simple"' in client.get("/?mode=simple").text          # the query wins for one request
    r = client.post("/prefs", data={"mode": "expert", "theme": "dark", "next": "https://evil.example/",
                                    "_token": app.state.token}, follow_redirects=False)
    assert r.headers["location"] == "/"                                       # only same-origin paths

def test_prefs_requires_the_token(settings):
    client, _app = make_client(settings)
    assert client.post("/prefs", data={"mode": "expert", "theme": "dark"}).status_code == 403

def test_stylesheet_declares_the_phone_breakpoints(settings):
    client, _app = make_client(settings)
    css = client.get("/static/app.css").text
    for token in ("--bg", "--surface", "--ion", "--solar", "--pass", "--fail", "--undet", "--running"):
        assert token + ":" in css
    assert "@media (max-width: 720px)" in css and "@media (max-width: 900px)" in css and "@media (min-width: 1280px)" in css
    assert "prefers-reduced-motion" in css and "[data-theme=\"dark\"]" in css and "prefers-color-scheme: dark" in css

def test_every_page_links_the_local_fonts_and_nothing_remote(settings):
    client, _app = make_client(settings)
    for path in ("/", "/candidates", "/live", "/sources", "/approved", "/about"):
        html = client.get(path).text
        assert "https://" not in html.split("<main")[0].replace("https://github.com", "")   # head has no remote asset
    css = client.get("/static/app.css").text
    assert "/static/fonts/SpaceGrotesk-Bold.woff2" in css and "/static/fonts/IBMPlexSans-Regular.woff2" in css
    assert client.get("/static/fonts/IBMPlexMono-Regular.woff2").status_code == 200

def test_contrast_tool_passes_on_the_shipped_stylesheet():
    from tools.contrast_check import check
    assert check() == []
```

Keep `test_static_urls_carry_a_cache_busting_version` green (the `/` page is now Tonight; it still links the three static files).

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/unit/test_ui_pages.py -q -p no:cacheprovider -k "mode_and_theme or prefs or breakpoints or local_fonts or contrast"`
Expected: FAIL (no `/prefs`, no tokens in the old stylesheet, no `tools.contrast_check`).

- [ ] **Step 3: Vendor the fonts**

Download the woff2 files from the Google Fonts GitHub repositories (`google/fonts`, `ofl/spacegrotesk`, `ofl/ibmplexsans`, `ofl/ibmplexmono`; the static instances at the weights listed in the file table) with `.venv/Scripts/python.exe -c "from skysift.net import fetch; ..."` or `curl`, record each source URL and SHA-256 in `LICENSES.md` with the OFL text, and commit the files. Total under 600 KB. Declare `@font-face` with `font-display: swap` and `unicode-range` for Latin.

- [ ] **Step 4: Write `app.css`, `app.js`, `base.html` and the partials**

The stylesheet implements spec section 3 exactly (values in the Interfaces block), the starfield as a fixed `body::before` with three `radial-gradient` sheets drifting 40 px over 120 s (`@keyframes drift`, off under reduced motion, opacity .04 in light), the type scale, the 12-column grid with 16 px gutters, the components, and the breakpoints. `base.html` renders `<html data-mode data-theme>`, the top bar partial (brand mark SVG, "SkySift" + small-caps "mission control", nav links, mode segmented control, theme control, the data/db/retention line moved into a `details` in expert mode), the bottom nav partial under 900 px, `<main>`, the toast region, the footer with About and version. `app.js` posts `/prefs` with the token header, opens and closes `.dialog` elements (focus trap, Escape, click outside, focus return), runs count-ups on `[data-count]`, maps keys, shows toasts from `hx-trigger` headers.

- [ ] **Step 5: `render()`, `/prefs`, `/about` skeleton, `tools/contrast_check.py`**

`render()` reads the cookies and query, validates against `("simple", "expert")` and `("system", "dark", "light")`, falls back to the defaults, and passes `mode`, `theme` and `page_title`. `/prefs` validates `next` with `urllib.parse.urlsplit` (no scheme, no netloc, starts with `/`). `/about` renders a placeholder `about.html` (filled in Task 6) so navigation never 404s. `tools/contrast_check.py` parses `:root` and `[data-theme="dark"]` blocks with a regex, computes WCAG relative luminance, and exposes `check() -> list[str]` plus a `__main__`.

- [ ] **Step 6: Run the UI tests, lint, then the full unit suite**

Run: `PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/unit/test_ui_pages.py tests/unit/test_assets.py -q -p no:cacheprovider` then `python -m ruff check src tests tools` then the full suite.
Expected: PASS, clean.

- [ ] **Step 7: Screenshots and commit**

Take the eight screenshots of the Candidates page (the only page with content until Task 4) and commit: `git commit -m "feat(ui): mission-control design system, modes, themes, vendored fonts"`.

---

### Task 2: Plain-language explanations (`skysift.ui.explain`)

**Files:**
- Create: `src/skysift/ui/explain.py`, `tests/unit/test_ui_explain.py`
- Modify: `tests/unit/ui_seed.py` (add a legacy signal `gaia-dr3-2101234567890123459` with rung 1 to 3 metrics from before M3 and no rung1_events row; a stage-2 signal `kic-006146676` with the four rungs plus a `rung5_pixels` pass row and `final_status="candidate_stage2"`; a candidate with an `approve` decision; a signal `gaia-dr3-2101234567890123460` with no test rows and `final_status="pending"`).

**Interfaces:**
- Produces:
```python
@dataclass(frozen=True)
class Why:
    rung: str            # stored rung name
    name: str            # RUNG_NAMES[rung] or the rung name
    status: str          # "pass" | "fail" | "undetermined" | "skipped"
    sentence: str        # one plain sentence with the deciding numbers
    numbers: str         # mono string of the deciding metrics, "" when none
    missing: str | None  # undetermined only: what was missing (spec 4.2)
    next_step: str | None  # undetermined only: what happens next (spec 4.2)
    when: str | None     # ISO date of the test row when the row carries one

@dataclass(frozen=True)
class Verdict:
    word: str            # "Candidate" | "Undetermined" | "Rejected" | "Not vetted yet" | "Superseded"
    stage: str | None    # "stage 1" | "stage 2" | None
    tone: str            # "pass" | "undetermined" | "fail" | "muted"
    headline: str        # e.g. "Undetermined: one check could not decide"
    reviewer: str | None # "Approved by reviewer" | "Rejected by reviewer" | "Needs data" | None

RUNG_NAMES: dict[str, str]           # rung -> "Detected above the noise", "Seen in every transit with data", "Transit shape", "Stable across seasons and apertures", "Not already known", "Pixels show the dip on this star"
LADDER_ORDER: list[str]
STATUS_GLOSSARY: list[tuple[str, str]]   # spec 4.1 rows
GLOSSARY: dict[str, str]                 # SDE, SNR, odd/even, secondary eclipse, pre-whitening, season, aperture, BKJD, ppm, impact parameter
def reason_in_words(reason: str) -> tuple[str, str]         # (missing, next_step); unknown -> (reason, "needs a person")
def why(tests: list[dict]) -> list[Why]                     # latest row per rung (highest id or last in list) in ladder order; rungs without a row after a fail are "skipped"
def verdict(signal: dict, tests: list[dict], decision: dict | None) -> Verdict
def summary_sentence(signal: dict, target: dict, tests: list[dict]) -> str   # "A dip of 0.22 percent repeats every 40.3 days on a star of magnitude 16.2."
def copy_summary(signal: dict, target: dict, v: Verdict, url: str) -> str
```
`signal` and `decision` are the dicts `queries.signal_detail` returns (`d["signal"]`, `d["candidate"]`), `tests` is `d["tests"]` (each with `rung`, `status`, `reason`, `metrics`, optional `created`).

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_ui_explain.py
import pytest
from skysift.ui import explain

REASONS = ["too few odd/even events", "too few events", "too few seasons",
           "equal secondary at phase 0.5: true period may be P / 2 (40.2899 d)", "season-dependent depth (p=5.7e-04)",
           "aperture-dependent depth (3.9 sigma)", "systematic_alias", "outage:exoarchive: HTTP 403",
           "period matches cumulative K01234.01 but epoch differs (5.1 h)", "exception: ValueError('x')"]

@pytest.mark.parametrize("reason", REASONS)
def test_every_rung_reason_maps_to_words_and_a_next_step(reason):
    missing, nxt = explain.reason_in_words(reason)
    assert missing and nxt and "undetermined" not in missing.lower() and nxt != "needs a person"

def test_unknown_reason_falls_back_to_the_raw_text():
    assert explain.reason_in_words("weird new reason") == ("weird new reason", "needs a person")

def test_why_lists_rungs_in_ladder_order_with_skipped_after_a_fail(seed_detail):
    rows = explain.why(seed_detail("rejected")["tests"])
    assert [w.rung for w in rows] == explain.LADDER_ORDER
    assert rows[2].status == "fail" and rows[3].status == "skipped" and rows[-1].status == "skipped"
    assert "4.2" in rows[2].sentence and rows[2].numbers

def test_why_for_an_undetermined_signal_carries_missing_and_next(seed_detail):
    rows = explain.why(seed_detail("undetermined")["tests"])
    u = [w for w in rows if w.status == "undetermined"][0]
    assert u.rung == "rung4_novelty" and "catalogue" in u.missing.lower() and "retry" in u.next_step.lower()
    assert all(w.missing is None for w in rows if w.status == "pass")

def test_verdict_words_and_stages(seed_detail):
    assert explain.verdict(**_args(seed_detail("candidate"))).word == "Candidate"
    assert explain.verdict(**_args(seed_detail("candidate"))).stage == "stage 1"
    assert explain.verdict(**_args(seed_detail("stage2"))).stage == "stage 2"
    assert explain.verdict(**_args(seed_detail("undetermined"))).headline == "Undetermined: one check could not decide"
    assert explain.verdict(**_args(seed_detail("rejected"))).tone == "fail"

def test_verdict_with_no_tests_is_not_vetted(seed_detail):
    v = explain.verdict(**_args(seed_detail("pending")))
    assert v.word == "Not vetted yet" and v.tone == "muted"

def test_verdict_puts_the_reviewer_decision_first(seed_detail):
    v = explain.verdict(**_args(seed_detail("approved")))
    assert v.reviewer == "Approved by reviewer" and v.word == "Candidate"

def test_legacy_metrics_render_without_the_new_keys(seed_detail):
    rows = explain.why(seed_detail("legacy")["tests"])
    assert len(rows) == len(explain.LADDER_ORDER) and all(w.sentence for w in rows if w.status != "skipped")

def test_summary_sentence_and_copy_summary(seed_detail):
    d = seed_detail("candidate")
    s = explain.summary_sentence(d["signal"], d["target"], d["tests"])
    assert "13.77" in s or "13.8" in s
    text = explain.copy_summary(d["signal"], d["target"], explain.verdict(**_args(d)), "http://127.0.0.1:8765/signals/1")
    assert "kic-005689121" in text and "http://127.0.0.1:8765/signals/1" in text and "—" not in text
```
`seed_detail` is a fixture in this file that seeds a store with `ui_seed.seed` and returns `queries.signal_detail` for the signal keyed by role (extend `seed()` to return `ids` by role: `candidate`, `undetermined`, `rejected`, `legacy`, `stage2`, `approved`, `pending`); `_args(d)` returns `dict(signal=d["signal"], tests=d["tests"], decision=d["candidate"])`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/unit/test_ui_explain.py -q -p no:cacheprovider`
Expected: FAIL with `ModuleNotFoundError: skysift.ui.explain`.

- [ ] **Step 3: Extend the seed and implement `explain.py`**

`reason_in_words` matches prefixes in the order of the spec 4.2 table (regular expressions for the parameterised ones). Sentences per rung come from the metrics: rung1 "SDE 50.5 against a threshold of 13"; rung1_events "32 of 37 predicted transits have data"; rung2 parity, secondary at 0.5, free-phase secondary, box versus sine, fit k and b; rung3 season depths and χ² p-value, SAP/PSF ratio; rung4 the matched catalogue row from `period_matched` or "no match in N catalogues"; rung5 the per-season centroid offsets. Every metric access goes through `.get` with a fallback phrase. `verdict` prefers the decision, then `final_status`, then derives from tests when `final_status` is pending.

- [ ] **Step 4: Run the tests, lint, full suite**

Run: `PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/unit/test_ui_explain.py tests/unit -q -p no:cacheprovider` and ruff.
Expected: PASS, clean.

- [ ] **Step 5: Commit**

`git commit -m "feat(ui): plain-language verdicts, reasons and next steps (skysift.ui.explain)"`

---

### Task 3: Verdict popup and the signal page

**Files:**
- Create: `src/skysift/ui/templates/partials/verdict.html`, `partials/why.html`
- Modify: `src/skysift/ui/routes/review.py`, `src/skysift/ui/templates/signal.html`, `partials/decision_bar.html`, `src/skysift/ui/static/plots.js`, `src/skysift/ui/plots.py` (layout template: transparent paper, hairline grid, fonts, the three mark colours, captions), `tests/unit/test_ui_review.py`

**Interfaces:**
- Consumes: Task 2's `explain`, Task 1's `.dialog` behaviour (`app.js` opens any `<button data-dialog-src="/partials/...">` by HTMX-loading the partial into `#dialog-slot` and showing it).
- Produces: `GET /partials/signals/{id}/verdict` renders `partials/verdict.html` with `v: Verdict`, `rows: list[Why]`, `signal`, `target`, `recheck_history: list[dict]` (empty until the recheck plan), `mode`; 404 for an unknown id. `GET /signals/{id}` context gains `v`, `rows`, `summary`, `copy_text`, `pixel_figures` (None for now). The signal page sections in order: hero (name, summary sentence, pill, stage, stat row), folded transit, "Why" (inline `partials/why.html`, same rows as the popup, expert adds raw metrics disclosure and seconds), plots with captions (simple: full curve, odd/even, phase 0.5; expert: plus periodogram, seasons), decision bar with consequences under each button, history. A "Copy summary" button with `data-copy` text.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_ui_review.py
def test_verdict_popup_for_an_undetermined_signal_has_the_three_blocks(seeded):
    client, ids = seeded
    html = client.get(f"/partials/signals/{ids['undetermined']}/verdict").text
    assert "Undetermined: one check could not decide" in html
    assert "What was missing" in html and "What happens next" in html and "catalogue" in html.lower()
    assert html.count('class="why-row') == len(explain.LADDER_ORDER)
    assert "Stage 1 means" in html                                   # glossary footer

def test_verdict_popup_for_a_candidate_has_no_missing_block(seeded):
    client, ids = seeded
    html = client.get(f"/partials/signals/{ids['candidate']}/verdict").text
    assert "What was missing" not in html and "Candidate" in html and "stage 1" in html

def test_verdict_popup_for_a_signal_without_tests(seeded):
    client, ids = seeded
    html = client.get(f"/partials/signals/{ids['pending']}/verdict").text
    assert "Not vetted yet" in html

def test_verdict_popup_unknown_signal_is_404(seeded):
    client, _ids = seeded
    assert client.get("/partials/signals/999999/verdict").status_code == 404

def test_signal_page_has_hero_why_plots_and_decision_in_both_modes(seeded):
    client, ids = seeded
    for mode in ("simple", "expert"):
        html = client.get(f"/signals/{ids['candidate']}?mode={mode}").text
        assert "A dip of" in html and 'class="pill' in html and 'id="why"' in html and 'id="plots"' in html
        assert "Copy summary" in html and "Approve" in html
    assert "raw" in client.get(f"/signals/{ids['candidate']}?mode=expert").text
    assert "raw" not in client.get(f"/signals/{ids['candidate']}?mode=simple").text.split('id="why"')[1].split('id="plots"')[0]

def test_signal_page_renders_a_legacy_signal_in_both_modes(seeded):
    client, ids = seeded
    for mode in ("simple", "expert"):
        assert client.get(f"/signals/{ids['legacy']}?mode={mode}").status_code == 200

def test_reject_requires_a_note_in_simple_mode(seeded):
    client, ids = seeded
    r = client.post(f"/signals/{ids['candidate']}/decide", data={"decision": "reject", "note": "", "_token": token(client)})
    assert r.status_code == 400 and "note" in r.text.lower()
```
`seeded` is the existing fixture pattern of this file extended to return the role ids.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/unit/test_ui_review.py -q -p no:cacheprovider`
Expected: FAIL (no popup route, no hero).

- [ ] **Step 3: Implement the route, the two partials and the new `signal.html`**

The popup partial is a `<div class="dialog-body">` with the headline, the `why.html` rows (icon + word, name, sentence, numbers), the highlighted missing/next block per undetermined row, the recheck history list when present, and the footer. `plots.js` reads `data-mode` on `#plots` to draw three or six plots and sets captions from `data-captions` JSON. `plots.py` gains `LAYOUT` (template dict) applied to every figure.

- [ ] **Step 4: Run the tests, lint, full suite; screenshots of the signal page and the popup in both modes and themes; commit**

`git commit -m "feat(ui): verdict popup and the signal page hero, why list and captions"`

---

### Task 4: Candidate cards, sparklines, the Candidates page

**Files:**
- Create: `src/skysift/ui/sparkline.py`, `src/skysift/ui/templates/partials/card.html`, `partials/queue_row.html`, `partials/signal_detail.html`
- Modify: `src/skysift/ui/routes/api.py`, `src/skysift/ui/routes/review.py`, `src/skysift/ui/queries.py`, `src/skysift/ui/templates/candidates.html`, `src/skysift/ui/static/app.js` (split view), `tests/unit/test_ui_queries.py`, `test_ui_review.py`, `test_ui_pages.py`

**Interfaces:**
- Consumes: `plots.whitened_fnorm`, `plots._fold_hours`, `plots._bin` for the fold; `explain.verdict`, `explain.summary_sentence`.
- Produces: `GET /api/signals/{id}/sparkline.svg` → `image/svg+xml`, 240 x 72 viewBox, binned fold points (ion), model line (solar) when `fit_k` exists else the box, `Cache-Control: max-age=86400`; disk cache `<data_dir>/ui/sparklines/<id>.svg`; placeholder SVG with the text "light curve not cached" when the raw file is missing. `queries.review_queue` rows gain `stage`, `verdict_word`, `verdict_tone`, `reviewer`, `summary`. `queries.QUEUE_STATUSES` = `("candidate_stage1", "candidate_stage2", "undetermined", "rejected", "superseded", "approved", "needs_data", "all")` where `approved` and `needs_data` filter by the latest decision. `GET /candidates` gains `?q=<target id substring>`; simple mode renders a `.cards` grid of `partials/card.html`, expert mode the table with `partials/queue_row.html` and, at 1280 px and above, `partials/signal_detail.html` swapped into `#detail` by HTMX (`hx-get="/partials/signals/{id}/detail"`), URL updated with `history.replaceState`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_ui_review.py
def test_sparkline_is_svg_and_cached(seeded, settings):
    client, ids = seeded
    r = client.get(f"/api/signals/{ids['candidate']}/sparkline.svg")
    assert r.status_code == 200 and r.headers["content-type"].startswith("image/svg+xml") and "<svg" in r.text
    assert (settings.data_dir / "ui" / "sparklines" / f"{ids['candidate']}.svg").exists()

def test_sparkline_without_a_cached_curve_returns_a_placeholder(seeded):
    client, ids = seeded
    r = client.get(f"/api/signals/{ids['rejected']}/sparkline.svg")        # the seed stores no raw file for it
    assert r.status_code == 200 and "light curve not cached" in r.text

def test_candidates_simple_mode_renders_cards_with_pills_and_sparklines(seeded):
    client, ids = seeded
    html = client.get("/candidates?mode=simple").text
    assert 'class="card candidate' in html and "sparkline.svg" in html and 'data-dialog-src="/partials/signals/' in html
    assert "stage 1" in html

def test_candidates_expert_mode_renders_the_table_and_the_detail_slot(seeded):
    client, ids = seeded
    html = client.get("/candidates?mode=expert").text
    assert "<table" in html and 'id="detail"' in html and "hx-get=\"/partials/signals/" in html

def test_candidates_chips_carry_counts_and_search_filters(seeded):
    client, ids = seeded
    html = client.get("/candidates").text
    assert "undetermined" in html and "rejected" in html
    assert 'class="chip-count">1<' in html                              # every chip carries its count
    assert "kic-005689121" in client.get("/candidates?q=5689").text
    assert "kic-005689121" not in client.get("/candidates?q=zzz").text

def test_candidates_approved_chip_lists_only_decided_signals(seeded):
    client, ids = seeded
    html = client.get("/candidates?status=approved")
    assert html.status_code == 200 and "Approved by reviewer" in html.text
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/unit/test_ui_review.py -q -p no:cacheprovider -k "sparkline or candidates"`
Expected: FAIL.

- [ ] **Step 3: Implement `sparkline.py`, the queries, the routes and the templates**

`sparkline.render_svg(hours, ppm, model_hours_ppm, depth_ppm) -> str` draws the bins as circles r=2.2 and the model as a 1.5 px path on a transparent background with `currentColor`-free explicit fills (`var(--ion)`, `var(--solar)` through a `<style>` inside the SVG so it follows the page theme when inlined, and fixed hex when served as `<img>`; serve with the hex). The card shows the sparkline through `<img loading="lazy" src=".../sparkline.svg" alt="folded transit of {target}">`.

- [ ] **Step 4: Run the tests, lint, full suite; screenshots of Candidates in both modes and themes at the four widths; commit**

`git commit -m "feat(ui): candidate cards with transit sparklines, chips, search and the expert split view"`

---

### Task 5: Tonight (home) and the Live flight deck

**Files:**
- Create: `src/skysift/ui/routes/home.py`, `src/skysift/ui/templates/tonight.html`, `partials/tonight_hero.html`, `partials/feed.html`, `partials/ring.html`, `partials/ticker.html`
- Modify: `src/skysift/ui/app.py` (`NAV`, router registration; `/` → Tonight, Sources at `/sources`), `src/skysift/ui/routes/sources.py` (path), `src/skysift/ui/routes/live.py`, `src/skysift/ui/queries.py`, `src/skysift/ui/templates/live.html`, `partials/live_cards.html`, `partials/live_sweep.html`, `tests/unit/test_ui_pages.py`, `test_ui_queries.py`

**Interfaces:**
- Consumes: `queries.summary`, `sweeps` history and active state (`live.py` already computes `active`, `elapsed`, `eta_s`, `finishing`), `explain.verdict`, Task 4 cards.
- Produces: `queries.tonight(store, version) -> dict` with `scanned` (targets done since the latest sweep started, or in the latest finished sweep), `signals`, `candidates_waiting` (stage 1 or 2 without a decision), `undetermined` (grouped by `next_step` word), `rejected`, `last_sweep: dict | None`; `queries.recent_candidates(store, version, limit=6) -> list[dict]` (cards, newest first, no decision yet). `GET /` renders `tonight.html`; `GET /partials/tonight/hero` the hero for polling (every 10 s while a sweep is active). `NAV = [("/", "Tonight"), ("/candidates", "Candidates"), ("/live", "Live"), ("/sources", "Sources"), ("/approved", "Approved")]`. Live: `partials/ring.html` (SVG ring, `--value` percent, `data-count` label), the state line in words, `partials/ticker.html` (last ten finished targets with outcome icon from `queries.recent_runs(store, limit=10)`), stop controls with consequences, expert cards and funnel as horizontal bars.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_ui_pages.py
def test_tonight_is_the_landing_page_with_hero_and_feed(seeded_settings):
    client, ids = make_seeded_client(seeded_settings)
    html = client.get("/").text
    assert "Tonight" in html and 'data-count=' in html and 'class="feed' in html and "kic-005689121" in html
    assert "candidates waiting" in html.lower() and "being rechecked" in html.lower()

def test_tonight_feed_excludes_decided_candidates(seeded_settings):
    client, ids = make_seeded_client(seeded_settings)
    feed = client.get("/").text.split('class="feed')[1]
    assert ids["approved_target"] not in feed

def test_tonight_empty_state_points_at_sources(settings):
    client, _app = make_client(settings)
    html = client.get("/").text
    assert "No sweep yet" in html and 'href="/sources"' in html

def test_sources_moved_and_nav_order(seeded_settings):
    client, _ids = make_seeded_client(seeded_settings)
    assert client.get("/sources").status_code == 200
    html = client.get("/").text
    assert html.index('href="/"') < html.index('href="/candidates"') < html.index('href="/live"') < html.index('href="/sources"')

def test_live_flight_deck_has_ring_state_and_ticker(seeded_settings):
    client, _ids = make_seeded_client(seeded_settings)
    html = client.get("/live?mode=simple").text
    assert 'class="ring' in html and 'id="state-line"' in html and 'class="ticker' in html
    assert "rung funnel" not in html.lower()
    assert "funnel" in client.get("/live?mode=expert").text.lower()
```
Update the existing sources tests to `/sources`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/unit/test_ui_pages.py -q -p no:cacheprovider -k "tonight or sources_moved or flight_deck"`
Expected: FAIL.

- [ ] **Step 3: Implement the queries, the home router, the Live templates**

The hero sentence is built server-side: "Tonight SkySift scanned {scanned} stars and found {candidates} candidates" with the numbers wrapped in `<span data-count="N">`; when no sweep has run, the empty state. Rechecks strip: counts of undetermined by `next_step` from `explain.reason_in_words` over the undetermined signals' undetermined test rows.

- [ ] **Step 4: Run the tests, lint, full suite; screenshots of Tonight and Live (idle and, by starting a sweep on the seed with `--limit 1`, running) in both modes and themes; commit**

`git commit -m "feat(ui): Tonight home with discovery feed and the Live flight deck"`

---

### Task 6: Sources (start), Approved, About

**Files:**
- Modify: `src/skysift/ui/templates/sources.html`, `partials/sweep_form.html`, `approved.html`, `about.html`, `src/skysift/ui/routes/published.py`, `src/skysift/ui/app.py` (`/about` context: `explain.STATUS_GLOSSARY`, `explain.GLOSSARY`, search configuration from `Settings`, citation from `CITATION.cff` when present), `tests/unit/test_ui_pages.py`

**Interfaces:**
- Consumes: Task 2 glossaries, Task 1 drawer component.
- Produces: `/sources` with the three-step panel (state of each step from `queries.summary` and the decision counts), source cards with a Start button opening `partials/sweep_form.html` in the drawer (every field with a one-line explanation and its default), expert comparison table; `/approved` cards (simple) or table (expert) each with "Copy summary" and the review JSON link; `/about` with the method (six short paragraphs), the status glossary table (spec 4.1), the recheck steps (spec 4.3) in words, the term glossary, the version, the search configuration, the citation.

- [ ] **Step 1: Write the failing tests**

```python
def test_sources_has_three_steps_and_the_drawer_form(seeded_settings):
    client, _ids = make_seeded_client(seeded_settings)
    html = client.get("/sources").text
    assert "Pick a data source" in html and "Run a sweep" in html and "Review candidates" in html
    form = client.get("/partials/sweep-form?source=kbonus_bkg").text
    assert 'class="drawer' in form and "What it does" in form

def test_approved_renders_cards_and_table(seeded_settings):
    client, _ids = make_seeded_client(seeded_settings)
    assert 'class="card' in client.get("/approved?mode=simple").text
    assert "<table" in client.get("/approved?mode=expert").text

def test_about_lists_statuses_recheck_steps_and_glossary(seeded_settings):
    client, _ids = make_seeded_client(seeded_settings)
    html = client.get("/about").text
    for word in ("Candidate, stage 1", "Candidate, stage 2", "Undetermined", "Rejected", "Re-vet at the suggested period", "SDE"):
        assert word in html
```

- [ ] **Step 2: Run to verify they fail; Step 3: implement; Step 4: tests, lint, full suite, screenshots; Step 5: commit**

`git commit -m "feat(ui): start page, approved cards and the About page"`

---

### Task 7: Accessibility, responsiveness and the screenshot review

**Files:**
- Modify: any template or stylesheet the pass finds wanting; `tests/unit/test_ui_pages.py`

- [ ] **Step 1: Structural tests**

```python
def test_dialog_and_controls_are_labelled(seeded):
    client, ids = seeded
    html = client.get(f"/partials/signals/{ids['undetermined']}/verdict").text
    assert 'role="dialog"' in html and 'aria-labelledby=' in html and 'aria-label="Close"' in html
    home = client.get("/").text
    assert 'aria-live="polite"' in home and 'aria-hidden="true"' in home     # counters; starfield

def test_status_is_never_colour_alone(seeded):
    client, _ids = seeded
    html = client.get("/candidates?mode=expert").text
    import re
    for m in re.finditer(r'class="pill pill-(pass|fail|undetermined|muted)[^>]*>(.*?)</', html):
        assert m.group(2).strip()
```

- [ ] **Step 2: Keyboard pass through the built-in browser**

Tab through Tonight, Candidates (both modes), a signal page and the popup; `j`/`k`/`a`/`r`/`n` in expert split view; Escape closes the dialog and focus returns to the pill. Fix what fails.

- [ ] **Step 3: Screenshot review**

All pages, both modes, both themes, 360/768/1024/1440 px; check no horizontal scroll (`document.documentElement.scrollWidth <= clientWidth`), the starfield never sits behind text, the card grid columns (1/2/3), the bottom nav under 900 px. Save under the SDD workspace, list in the report.

- [ ] **Step 4: Run the full suite, lint, commit**

`git commit -m "chore(ui): accessibility and responsive pass"`
