# SkySift dashboard polish: simple and expert modes

Addendum to `2026-10-14-skysift-dashboard-design.md` (M2). Date: 17 October 2026. Status: written for Vikram's review before the implementation plan runs.

## 1. Purpose

The M2 dashboard is correct and dense, written for one operator who knows what an SDE is. Two things change here. First, the same dashboard must make sense to a non-expert (a student, a collaborator, a reader who followed a link to a published candidate): what the engine found, how sure it is, and what to do next, in plain words. Second, the expert view must get better, not thinner: every number the rungs measured stays one click away, and the triage loop (look, decide, next) gets faster. The result has to look like a product someone chose, on a phone as well as a wide monitor, without a build step, a JavaScript framework or any runtime network call.

## 2. Users and modes

Two modes, one dashboard, same URLs:

| | Simple (default on first visit) | Expert |
|---|---|---|
| Who | first-time viewer, student, reviewer of a published candidate | Vikram, a vetting astronomer |
| Verdict | one sentence and a confidence word per signal ("Likely a transiting planet candidate: three of four checks passed, one is undecided because the transit is short") | the ladder table with status, reason, seconds and the key metrics |
| Numbers | period, depth, duration, how many events, with units and a short gloss on hover | everything, plus raw JSON, detrend and pre-whitening records, alias and second-pass provenance |
| Plots | the three plots that tell the story (full curve, phase fold, odd versus even) with annotations in words | all six, with aliases, systematic periods, bands and the threshold line |
| Actions | approve, reject, needs data (same buttons, with the consequence spelled out) | the same, plus keyboard shortcuts and the queue navigator |
| Live | progress bar, time left, "what the engine is doing now" | the same plus rung funnel, throughput, cache, queries, last errors, the log |

The mode is a toggle in the top bar, remembered in a cookie (`skysift_mode`) so the server renders the right view; a `?mode=expert` query sets it for a link. No login, no accounts (section 7 of the M2 spec stands).

## 3. Design system

- **Type.** IBM Plex Sans for text and IBM Plex Mono for identifiers and numbers, vendored under `static/fonts/` with SHA-256 pins the way Plotly and HTMX are, falling back to the system stack. Sizes on a 4 px rhythm: 13 px tables, 14 px body, 16/20/28 px headings. Tabular numerals everywhere a number sits in a column.
- **Colour.** One neutral scale (slate, nine steps) for surfaces and text, light and dark from `prefers-color-scheme` with a manual override in the top bar. Status colours from the Okabe-Ito palette, which stays distinct for the common forms of colour-vision deficiency: pass green `#009E73`, fail vermilion `#D55E00`, undetermined amber `#E69F00`, running blue `#0072B2`, approved `#009E73`, rejected `#D55E00`. Status never relies on colour alone: an icon or word always accompanies it. Contrast meets WCAG AA (4.5:1 text, 3:1 large text and UI) in both themes.
- **Layout.** A 12-column grid with a 16 px gutter; the side navigation collapses to a top bar under 900 px; tables turn into stacked cards under 720 px; the signal page becomes a two-pane triage workbench (queue on the left, detail on the right) at 1280 px and above in expert mode. No horizontal page scroll at any width from 360 px.
- **Motion.** 150 ms ease on hover and panel changes, none under `prefers-reduced-motion`.
- **Components.** Cards, stat tiles, status pills, a verdict banner, a "why" list, a progress bar with ETA, filter chips, a details drawer, a keyboard-hint footer, a toast for saved decisions, empty states with a next step. All CSS, in one `app.css` of about 500 lines with custom properties; HTMX 2 as today; no other JavaScript dependency.
- **Voice.** Short, declarative, no jargon in simple mode. Every technical term that must appear (SDE, odd/even, secondary eclipse, pre-whitening) gets a one-line gloss on hover and a short entry on the About page.

## 4. Pages

### 4.1 Start (replaces the bare Sources table as the landing page)
Simple mode: a three-step panel ("Pick a data source", "Run a sweep", "Review candidates") with the state of each step (what has run, how many candidates wait), then the source cards: name, agency, how many stars, what a sweep costs in hours per 10,000 stars, a progress bar when the source is live, and the Start sweep button that opens the existing form in a drawer with every field explained in one line. Expert mode: the same cards plus the dense comparison table (today's columns), sortable.

### 4.2 Live (flight deck)
A single progress bar with processed over limit, elapsed and time left (from the mean seconds per run), the current state in words ("vetting 6 targets, fetching 3"), a Stop button with a confirm step. Simple mode shows the counters that matter (done, candidates found, errors) and hides the log behind a disclosure. Expert mode shows every M2 card, the rung funnel as horizontal bars (pass, fail, undetermined per rung), throughput, cache against budget, the last errors and the live log. Idle state: the last sweep's summary and a Start link. Polling stays at 5 s and stops when idle.

### 4.3 Candidates (triage)
Filter chips (candidates, undetermined, rejected, approved, all) with counts; sort by column. Each row carries a verdict pill and, in simple mode, a one-line reason in words ("rejected: the dip is sinusoidal, probably a pulsating star"). Expert mode at 1280 px and above: the list stays on the left (320 px) and the detail renders on the right, so `j` and `k` move through the queue without a page load (HTMX swap of the detail pane; the URL updates with `history.replaceState`). Below that width the row opens the detail page as today.

### 4.4 Signal (detail)
Top: a verdict banner in words with the confidence word and the key numbers in a stat row (period, depth, duration, events, SDE in expert mode). Then "Why": one line per rung in words ("Odd and even transits agree (difference 0.1 sigma)", "No eclipse at phase 0.5", "Depth is the same in every season", "Not in any catalogue within 2 arcminutes"), each expandable to the metrics; the stacked-fold, doubled-period, second-pass and pre-whitening provenance appear as plain sentences ("Measured on the stacked fold because each transit holds only two points"). Plots: simple mode shows the full curve with transit marks, the phase fold with the box model, and odd versus even; expert mode adds the periodogram (threshold, aliases, systematic periods, bands), the phase 0.5 window and the per-season depths. Each plot carries a one-line caption of what to look for. Sticky decision bar: Approve, Reject (note required), Needs data, with the consequence under each ("Approve moves it to the Approved list and the review JSON"); keyboard hints in expert mode; previous and next in the current queue. Decision history below.

### 4.5 Approved
Cards in simple mode (target, period, depth, decided when, the note, a "review JSON" button); the table in expert mode. A "Share" control copies a link with `?mode=simple` for a reader.

### 4.6 About
Method in six paragraphs (what the engine searches, the four checks, what the threshold means, what the dashboard does not claim), the glossary, the version and search configuration in use, and the citation from `CITATION.cff`. Linked from the footer.

## 5. Behaviour that does not change
Routes, queries, decisions (append-only), the CSRF token, loopback binding, the review JSON, HTMX polling, Plotly plots served from `/api/signals/{id}/plots.json`, the retention and sweep controls. Nothing here touches the engine.

## 6. Accessibility and responsiveness
Keyboard reachable throughout with visible focus rings; `aria-live` on the Live counters and the decision toast; labels on every control; tables with scope headers; the mode and theme toggles are buttons with state. Widths checked at 360, 768, 1024 and 1440 px in both themes.

## 7. Testing
Template tests (`tests/unit/test_ui_pages.py`, `test_ui_review.py`) assert both modes render every page with seeded data and with signals that predate the new metrics; a contrast check script over the CSS custom properties pins AA; the final review takes screenshots at the four widths in both themes through the built-in browser and judges them against this spec.

## 8. Out of scope
Accounts and sharing beyond loopback, i18n, a public read-only deployment (a later milestone once candidates are published), charts beyond Plotly.
