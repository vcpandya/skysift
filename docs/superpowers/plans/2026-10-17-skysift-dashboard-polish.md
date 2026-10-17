# SkySift Dashboard Polish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the M2 dashboard into a product a non-expert can read and an expert can triage fast: simple and expert modes on the same URLs, a vendored design system (IBM Plex, Okabe-Ito status colours, a responsive grid), plain-language verdicts with every number one click away, and an About page, with no build step, no framework and no runtime network call.

**Architecture:** Server-rendered Jinja2 templates stay the only view layer; a small pure module `skysift.ui.explain` turns stored test results into sentences for both the queue and the detail page; the mode and theme are cookies read by the render helper so every template sees `mode` and `theme`; HTMX 2 does the detail-pane swap in the expert split view and the existing polling; one stylesheet of custom properties and components replaces `app.css`; fonts are vendored through the same SHA-256 pinning the Plotly bundle uses.

**Tech Stack:** Python 3.12, FastAPI, Jinja2, HTMX 2.0.3 (vendored), Plotly 2.35.2 (vendored), IBM Plex Sans and Mono (vendored woff2), pytest with the existing `ui_seed.py` fixtures. No new Python dependencies.

**Spec:** `docs/superpowers/specs/2026-10-17-skysift-dashboard-polish-design.md` (this plan's authority) on top of `docs/superpowers/specs/2026-10-14-skysift-dashboard-design.md` (M2, unchanged behaviour in its section 5 to 7).

## Global Constraints

- **Never run a console `.exe` from `.venv/Scripts`** (Norton CyberCapture froze this machine). Every command is `.venv/Scripts/python.exe -m <module>`.
- Branch `feat/dashboard-polish` off `main` after the M3 accuracy branch is merged (or off `feat/m3-accuracy` if Vikram keeps it unmerged; the controller says which). Conventional commits ending with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`. Never push.
- Lint: `python -m ruff check src tests` clean (line length 120). Tests: `python -m pytest tests/unit -q -p no:cacheprovider` green and warning-free (`-W error::DeprecationWarning`); the UI test files (`test_ui_pages.py`, `test_ui_review.py`, `test_ui_queries.py`, `test_assets.py`, `test_sweeps.py`) run in seconds and are the iteration loop; the full suite (about 18 minutes) runs once per task before its commit.
- No runtime network call from any page: fonts, HTMX and Plotly are vendored and SHA-256 pinned; the dashboard works offline once the assets are present, and degrades to the system font stack when a font file is missing.
- Status is never carried by colour alone (a word or icon accompanies every status colour). Contrast AA in both themes (4.5:1 text, 3:1 large text and UI), checked by the script Task 1 adds.
- Every page renders in both modes with the seeded data of `tests/unit/ui_seed.py` AND with signals that predate the M3 metrics (no `stacked`, `period_matched`, `alias_resolved`, `detrend` or `prewhitening` keys): the templates and `explain` guard every missing key.
- Routes, query functions, decision semantics, the CSRF token, loopback binding and the review JSON do not change (M2 spec sections 5 to 7). Templates may be restructured freely.
- Prose on pages: short declarative sentences, no jargon in simple mode, every unavoidable term glossed; no em-dashes anywhere.
- Each task ends with screenshots the implementer takes through the dashboard started on the seed data (`SKYSIFT_DATA_DIR=.superpowers/ui-seed ... python.exe -m skysift ui --no-browser --port 8766`, see the M2 ledger) at 360, 768, 1024 and 1440 px in light and dark, saved under the plan's SDD workspace, named in the report. The controller judges them against the spec.

## Review Focus

1. **A signal stored before M3** (no stacked, alias, detrend or pre-whitening keys, rung 3 metrics without `season_errs`) must render on every page in both modes without a template error or a blank verdict. Pinned by Task 2's `test_explain_handles_legacy_metrics` and Task 3's `test_signal_page_renders_a_legacy_signal_in_both_modes`.
2. **A viewer on a 360 px phone** must read the verdict, the three simple plots and the decision buttons without horizontal scroll, and the tables must stack into cards. Pinned by Task 1's `test_stylesheet_has_the_narrow_breakpoints` (structural) and the Task 7 screenshot review.
3. **Keyboard-only triage** in expert split view: `j`/`k` move, `a`/`r`/`n` decide, the focus ring is visible and the detail pane announces its change. Pinned by Task 4's `test_split_view_swaps_the_detail_pane` and the Task 7 accessibility checklist.
4. **A reject without a note** must be refused with a visible message in both modes (M2 rule). Pinned by Task 3's `test_reject_requires_a_note_in_simple_mode`.
5. **The mode cookie must not leak into the engine or the API**: `/api/...` responses are identical in both modes and the sweep form posts the same fields. Pinned by Task 1's `test_mode_cookie_changes_only_the_rendered_view`.

---

## File structure

| Path | Responsibility |
|---|---|
| `src/skysift/ui/static/app.css` | Rewritten: custom properties (neutral scale, Okabe-Ito status, spacing, type), components, grid, breakpoints, both themes, reduced motion. |
| `src/skysift/ui/static/app.js` | Mode and theme toggles (set cookie, reload), keyboard map, split-view navigation, toast; no dependency. |
| `src/skysift/ui/static/fonts/` + the asset pinning module | IBM Plex Sans (400, 600) and Plex Mono (400) woff2, pinned like Plotly; `@font-face` with `font-display: swap`. |
| `src/skysift/ui/app.py` | `render()` adds `mode`, `theme`, `glossary` to the context; a `/prefs` POST sets the cookies; `/about` route. |
| `src/skysift/ui/explain.py` | Pure functions: `verdict(signal, tests, decision) -> Verdict(headline, confidence, tone)`, `why(tests) -> list[Why(rung, sentence, status, metrics)]`, `provenance(signal) -> list[str]`, `reason_in_words(reason) -> str`, `GLOSSARY: dict[str, str]`. |
| `src/skysift/ui/templates/base.html` | Top bar with mode and theme toggles, collapsible navigation, footer with About; `partials/stat.html`, `partials/pill.html`, `partials/empty.html`. |
| `src/skysift/ui/templates/start.html` (was `sources.html`) | Three-step panel, source cards, drawer form, expert comparison table. |
| `src/skysift/ui/templates/live.html` + partials | Progress bar with ETA, state in words, mode-dependent cards, rung funnel, log disclosure. |
| `src/skysift/ui/templates/candidates.html` + `partials/queue_row.html`, `partials/signal_detail.html` | Chips with counts, verdict pills, simple reasons, expert split view. |
| `src/skysift/ui/templates/signal.html` | Banner, why list, provenance, mode-dependent plots with captions, decision bar with consequences, history. |
| `src/skysift/ui/templates/approved.html`, `about.html` | Cards or table; method, glossary, version, citation. |
| `src/skysift/ui/static/plots.js` | Reads `data-mode` to draw three or six plots; captions; unchanged data endpoint. |
| `tools/contrast_check.py` | Reads the CSS custom properties and asserts AA pairs; run by a unit test. |
| `tests/unit/test_ui_explain.py`, `test_ui_pages.py`, `test_ui_review.py`, `test_assets.py` | Tests named in the tasks. |

---

### Task 1: Design system foundation, modes, themes, About

**Files:** `static/app.css` (rewrite), `static/app.js`, `static/fonts/*`, the asset pinning module and `test_assets.py`, `ui/app.py` (`render`, `/prefs`, `/about`), `templates/base.html`, `templates/about.html`, `templates/partials/{stat,pill,empty}.html`, `tools/contrast_check.py`, `tests/unit/test_ui_pages.py`.

**Interfaces:**
- Produces: template context keys `mode` ("simple" | "expert"), `theme` ("auto" | "light" | "dark"), `glossary` (from `explain.GLOSSARY`, Task 2 supplies the content; Task 1 ships an empty dict and the hover component); cookie names `skysift_mode`, `skysift_theme`; `POST /prefs` with form fields `mode`, `theme`, `next` (same-origin path only) setting the cookies and redirecting; `?mode=` query overriding the cookie for one render; CSS classes documented at the top of `app.css` (`.stat`, `.pill.pass|fail|undetermined|running|approved|rejected`, `.card`, `.grid`, `.drawer`, `.chips`, `.progress`, `.toast`, `.empty`, `.kbd`, `.gloss`).
- Fonts: `IBMPlexSans-Regular.woff2`, `IBMPlexSans-SemiBold.woff2`, `IBMPlexMono-Regular.woff2` from the IBM Plex release on GitHub (OFL licence file vendored alongside), pinned by SHA-256 the way the Plotly bundle is, served under `/static/fonts/`, with `font-display: swap` and the system stack as fallback.

- [ ] **Step 1: Failing tests** in `tests/unit/test_ui_pages.py`: `test_mode_cookie_changes_only_the_rendered_view` (GET `/candidates` with `skysift_mode=expert` contains `data-mode="expert"`, without it `data-mode="simple"`; `/api/live.json` (or the existing JSON endpoint) byte-identical under both cookies); `test_prefs_sets_cookies_and_redirects_same_origin_only` (POST `/prefs` with `next=/live` sets both cookies and 303s to `/live`; `next=https://evil` falls back to `/`); `test_about_page_lists_version_and_citation`; `test_stylesheet_has_the_narrow_breakpoints` (the CSS contains `@media (max-width: 720px)` and `@media (max-width: 900px)` and `prefers-reduced-motion`); in `test_assets.py`: the three font files are pinned and served with `font/woff2`.
- [ ] **Step 2: Implement.** `render()` reads the cookies and the query; `base.html` gets the top bar (brand, mode segmented control, theme button, data path in a muted chip), the navigation that collapses under 900 px (a `<details>` disclosure, no JS needed), the footer with version, commit and About. `app.css`: tokens first, then components, then page layouts, then breakpoints; both themes as `[data-theme=dark]` and the `prefers-color-scheme` default. `tools/contrast_check.py` parses the `:root` blocks and asserts the pairs (fg on bg, muted on bg, each status on bg and on panel) meet AA; `test_contrast_pairs_meet_aa` runs it.
- [ ] **Step 3: Screenshots** at the four widths in both themes of `/candidates` and `/about`; UI tests, full suite, ruff; commit `feat(ui): design system, simple and expert modes, themes and About page`.

---

### Task 2: Plain-language explanations (`skysift.ui.explain`)

**Files:** `src/skysift/ui/explain.py`, `tests/unit/test_ui_explain.py`.

**Interfaces:**
- `Verdict(headline: str, confidence: str, tone: str)`; `verdict(signal: dict, tests: list[dict], decision: dict | None) -> Verdict`. Rules: approved decision -> "Approved as a candidate" (tone pass); rejected -> "Rejected: <reason in words>" (fail); needs data -> "Waiting for more data" (undetermined); else by final status: `candidate_stage1` -> "Looks like a transit candidate" with confidence "high" when all four rungs passed, "medium" when one is undetermined; `undetermined` -> "Not decided: <what is missing>"; `rejected` -> "Rejected: <reason in words>". `tone` is one of pass, fail, undetermined, running.
- `reason_in_words(reason: str) -> str`: the engine's reason strings to sentences: `sinusoidal` -> "the dip is a smooth wave, probably a pulsating or spotted star"; `odd_even` -> "odd and even dips differ, as in an eclipsing binary"; `secondary_at_0.5` -> "there is a second dip half way round, as in an eclipsing binary"; `eccentric_secondary` -> "a second dip at another phase, as in an eccentric binary"; `season-dependent depth` -> "the depth changes with the telescope's season, a sign of a contaminating neighbour"; `known:<table>:<id>` -> "already known: <catalogue name> <id>"; `too few odd/even events` and `too few events` and `too few seasons` -> "the data cannot answer this check yet"; `outage:...` -> "a catalogue service was down"; `below threshold` style rung 1 reasons -> "the signal is too weak"; unknown reasons pass through unchanged.
- `why(tests) -> list[Why(rung, sentence, status, metrics)]`: one sentence per rung from its metrics (rung 1: "Signal strength SDE 12.3, above the threshold of 11; 46 events" ; rung 2: parity, secondary, shape; rung 3: seasons; rung 4: catalogues searched and radius), using `stacked`, `alias_resolved` and `period_matched` when present. `provenance(signal) -> list[str]`: sentences for pre-whitening ("3 periodic waves were removed before the search"), the doubled period, the second pass, and the detrend record.
- `GLOSSARY`: SDE, SNR, odd/even, secondary eclipse, phase, season, pre-whitening, detrend, alias, BLS, KOI, TCE, ppm, duration ratio; one sentence each.

- [ ] **Step 1: Failing tests:** `test_verdict_for_each_status_and_decision`, `test_reason_in_words_covers_every_engine_reason` (parametrised over the reason strings found by grepping `src/skysift/vetting` and `pipeline.py` for `reason=`; the test fails if a new reason string appears without a mapping, by asserting the mapping covers the set the test extracts from the source files), `test_why_sentences_use_stacked_and_alias_metrics`, `test_explain_handles_legacy_metrics` (metrics dicts without the M3 keys and with None values produce sentences, never raise).
- [ ] **Step 2: Implement; tests; ruff; commit** `feat(ui): plain-language verdicts, reasons and glossary`.

---

### Task 3: Signal page

**Files:** `templates/signal.html`, `partials/decision_bar.html`, `static/plots.js`, `ui/routes/review.py` (context: `verdict`, `why`, `provenance`), `tests/unit/test_ui_review.py`.

- [ ] **Step 1: Failing tests:** `test_signal_page_shows_the_verdict_banner_and_why_list_in_simple_mode`, `test_signal_page_shows_the_ladder_and_raw_in_expert_mode`, `test_signal_page_renders_a_legacy_signal_in_both_modes`, `test_reject_requires_a_note_in_simple_mode` (the HTMX response carries the message and no decision is stored), `test_plots_container_carries_the_mode` (`data-mode` on `#plots`).
- [ ] **Step 2: Implement** per spec 4.4: banner (`.stat` row), why list with expandable metrics (expert shows the ladder table and raw JSON instead), provenance sentences, plots by mode with captions (`plots.js` reads `data-mode` and draws 3 or 6, captions in the DOM, not in Plotly), decision bar with the consequence under each button, toast after a decision (`aria-live="polite"`), keyboard hints in expert mode.
- [ ] **Step 3: Screenshots** (4 widths, 2 themes, both modes); tests; full suite; ruff; commit `feat(ui): signal page with verdict banner, why list and mode-dependent plots`.

---

### Task 4: Candidates triage and expert split view

**Files:** `templates/candidates.html`, `partials/queue_row.html`, `partials/signal_detail.html`, `ui/routes/review.py` (counts per status; a `GET /signals/{id}/pane` returning the detail partial), `static/app.js`, `tests/unit/test_ui_review.py`.

- [ ] **Step 1: Failing tests:** `test_filter_chips_carry_counts`, `test_queue_rows_show_verdict_pills_and_simple_reasons`, `test_split_view_swaps_the_detail_pane` (expert: `GET /signals/{id}/pane` returns the partial with `hx-` attributes; the candidates page in expert mode contains the split container and the pane target; simple mode does not), `test_keyboard_map_is_declared` (the page lists `j`, `k`, `a`, `r`, `n` in the `.kbd` footer in expert mode only).
- [ ] **Step 2: Implement** per spec 4.3: chips, pills, reasons in words, the split view at 1280 px and above in expert mode (HTMX swap of the pane, `history.replaceState`, the selected row highlighted and scrolled into view, `aria-live` on the pane), rows open the full page otherwise.
- [ ] **Step 3: Screenshots; tests; full suite; ruff; commit** `feat(ui): candidates triage with chips, pills and an expert split view`.

---

### Task 5: Start page and Approved

**Files:** `templates/start.html` (rename from `sources.html`, route unchanged at `/`), `partials/sweep_form.html` (drawer, one-line explanations), `templates/approved.html`, `ui/routes/sources.py`, `ui/routes/published.py`, `tests/unit/test_ui_pages.py`, `tests/unit/test_sweeps.py`.

- [ ] **Step 1: Failing tests:** `test_start_page_shows_three_steps_with_state`, `test_source_cards_replace_the_wide_table_in_simple_mode`, `test_expert_mode_keeps_the_comparison_table`, `test_sweep_form_opens_in_a_drawer_with_explanations` (every field has a `.gloss`), `test_approved_cards_and_share_link`.
- [ ] **Step 2: Implement** per spec 4.1 and 4.5. The sweep form posts the same fields as before (`test_sweeps.py` must stay green unchanged).
- [ ] **Step 3: Screenshots; tests; full suite; ruff; commit** `feat(ui): start page with source cards and a guided sweep form; approved cards`.

---

### Task 6: Live flight deck

**Files:** `templates/live.html`, `partials/live_cards.html`, `partials/live_sweep.html`, `ui/routes/live.py` (state in words, ETA, rung funnel data), `tests/unit/test_ui_pages.py`.

- [ ] **Step 1: Failing tests:** `test_live_progress_bar_and_eta` (processed over limit, ETA from mean seconds; "idle" when no sweep), `test_live_state_in_words`, `test_live_expert_cards_and_funnel`, `test_live_simple_mode_hides_the_log_behind_a_disclosure`.
- [ ] **Step 2: Implement** per spec 4.2; polling unchanged (5 s, stops when idle); `aria-live` on the counters.
- [ ] **Step 3: Screenshots; tests; full suite; ruff; commit** `feat(ui): live flight deck with progress, ETA and rung funnel`.

---

### Task 7: Accessibility and responsive pass

**Files:** every template and `app.css`; `docs/superpowers/notes/2026-10-17-dashboard-a11y-checklist.md`.

- [ ] **Step 1:** Walk every page in both modes with the keyboard only; record the checklist (focus order, focus ring, labels, `aria-live`, scope headers, contrast script result, reduced motion) with pass/fail per item and fix every fail. Screenshots at 360, 768, 1024 and 1440 px in both themes of every page.
- [ ] **Step 2:** README: a "Dashboard" section with two screenshots (simple and expert) and the mode explanation; commit `fix(ui): accessibility and responsive pass`.

---

## Final whole-branch review

Dispatch the final reviewer on the most capable model with the whole branch diff and the screenshot set; ONE fix wave; then `superpowers:finishing-a-development-branch` (merge decision stays with Vikram).
