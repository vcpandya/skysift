# SkySift mission control: visual identity, verdict explanations and stage-2 rechecks

Date: 20 October 2026. Supersedes the visual sections (3, 4) of `2026-10-17-skysift-dashboard-polish-design.md` and keeps its modes, behaviour, accessibility and testing sections. Builds on `2026-10-14-skysift-dashboard-design.md` (M2) whose routes, decisions, CSRF token and loopback rule do not change. Vikram asked for three things on 20 October: the difference between a candidate and an undetermined signal explained clearly, with the exact reason a verdict could not be reached one click away; a fresh, polished, modern look with a planet-hunting feel that makes people want to use it; and a second, more scrutinising recheck for undetermined signals instead of leaving them for a person. He asked for the design to be made from best practice and persona profiling without further questions; the choices below are the designer's calls and he reviews the result.

## 1. Who it is for

Two personas, both served by the same pages.

**Nova, 22, the explorer.** Follows space accounts, has read about Planet Hunters, opens things on a phone first. Wants to know within ten seconds whether anything was found, how sure we are, and what the thing would be like. Loses interest at a table of column headers; stays for a picture of a dip, a number that means something, and a sense of progress. Shares what excites her. Will not forgive hype that turns out to be noise, so excitement has to be earned by evidence.

**The vetting astronomer (Vikram).** Wants the triage loop fast and every number the rungs measured one click away. Keyboard first on a wide monitor. Needs the status model to be honest: a signal nobody has looked at is not a discovery.

Simple mode is Nova's view and the default; expert mode is the astronomer's. Same URLs, a toggle in the top bar, remembered in the `skysift_mode` cookie (polish spec section 2).

## 2. Principles

1. **Earned excitement.** The interface celebrates what the evidence supports and says plainly what it does not. A stage-1 candidate is "cleared every light-curve check"; only a pixel-vetted candidate is "the dip is on this star". No "discovery" language anywhere before a human approves.
2. **The dip is the hero.** Every candidate is shown as a picture of its transit before any number. A dip people can see is the thing that makes a transit search exciting.
3. **One click to the why.** Every verdict word (candidate, undetermined, rejected) opens a popup that lists each check in plain words, the numbers that decided it, and for an undetermined check, exactly what was missing and what happens next.
4. **Progress you can feel.** Stars scanned, signals found, candidates waiting, time left, all live, all animated once on load and then quietly.
5. **Dark sky, light sky.** Dark is the designed-first theme (a night-sky product), light is designed with equal care, both pass AA, the choice follows the system and can be overridden.
6. **No build step, no framework, no runtime network call.** Jinja2 templates, HTMX 2 (vendored), Plotly (vendored), one stylesheet, one small script. Fonts vendored and SHA-256 pinned like the Plotly bundle.

## 3. Visual identity

**Name on screen.** "SkySift" with the tagline "mission control" in the top bar, small caps. The brand mark is the existing ring-and-planet glyph (a circle with a small filled disc on its rim), drawn inline as SVG.

**Type.** Space Grotesk (SIL OFL, vendored woff2, 500 and 700) for the brand, page titles and hero numbers; IBM Plex Sans (400, 500, 600) for body and UI; IBM Plex Mono (400, 500) for identifiers, coordinates and tabular numbers. Fallback stacks declared; the page is readable with every font missing. Scale on a 4 px rhythm: 12 (captions), 13 (tables), 14 (body), 16, 20, 28, 40 (hero numbers), 48 (page hero). Tabular numerals wherever a number sits in a column or counts up.

**Colour.** Dark theme surfaces: page `#070b14` (deep sky), surface `#0e1422`, raised surface `#151d2e`, hairline `rgba(255,255,255,.08)`, text `#e8ecf4`, secondary text `#a7b0c3`, muted `#6b7489`. Light theme: page `#f5f7fb`, surface `#ffffff`, raised `#eef2f8`, hairline `rgba(10,16,32,.10)`, text `#0b1020`, secondary `#4a5368`, muted `#7b8499`. Two accents: **ion** cyan `#4fd1e8` dark / `#0e8ca8` light for interactive elements and progress; **solar** amber `#ffb454` dark / `#b86a00` light for the transit model line and the "new" markers. Status colours stay Okabe-Ito and never carry meaning alone: pass `#009E73`, fail `#D55E00`, undetermined `#E69F00`, running `#0072B2`; on the dark surface these are lightened one step (`#2fc495`, `#ff8a4a`, `#ffc04d`, `#4ea1ff`) and the tool `tools/contrast_check.py` pins AA for every text and UI pair in both themes.

**Sky.** A fixed, non-interactive starfield behind the page: a single CSS layer of three `radial-gradient` star sheets at different scales and opacities, drifting by 40 px over 120 s, no JavaScript, disabled under `prefers-reduced-motion`, 4 percent opacity in light theme so the page stays paper-like. It must never reduce text contrast: content sits on surfaces, not on the sky.

**Marks.** The folded light curve uses secondary-text points, ion binned points and a solar model line; the same three colours appear in every sparkline, card and plot so a reader learns them once. Plots are Plotly with a shared layout template (transparent paper, hairline grid, the type above); each carries a one-line caption of what to look for.

**Motion.** 160 ms ease on hover and panel changes; hero numbers count up over 600 ms on first paint; the progress ring eases to its value; a new candidate card slides in from 8 px below with a 240 ms fade. All motion off under `prefers-reduced-motion`. No confetti, no sound, no looping animation other than the 120 s sky drift.

**Voice.** Short declarative sentences, warm, specific, no jargon in simple mode, every unavoidable term glossed on hover and on the About page. No em-dashes. Examples of the register:
- "A dip repeats every 40.3 days on a red dwarf Kepler never targeted."
- "Cleared every light-curve check. Pixels not yet examined."
- "No verdict: the odd and even transits could not be compared, because each parity has only two events. The recheck will measure them on the stacked fold."
- Empty state: "Nothing waiting. The sweep is 43 percent through tonight's list."

## 4. The status model, explained and extended

### 4.1 What the words mean (shown on the About page and in every popup's footer)

| Word | Meaning | Who decides |
|---|---|---|
| **Candidate, stage 1** | Every light-curve check reached a verdict and all of them passed. | the engine |
| **Candidate, stage 2** | Stage 1, and the pixel checks (section 4.3) show the dip on this star and not on a neighbour. | the engine |
| **Undetermined** | No check failed, but at least one could not reach a verdict. The popup names it and says what happens next. | the engine, then the recheck |
| **Rejected** | A check failed. The popup names the check and the number that failed it. | the engine |
| **Approved / Rejected by reviewer / Needs data** | A person's decision, recorded with a note, on top of the engine's status. | a person |

### 4.2 Why a check gives no verdict, and what happens next

Every undetermined reason the rungs produce maps to a plain sentence and a next step. The mapping lives in `skysift.ui.explain` and is the single source for popups, cards and the About page:

| Stored reason (prefix) | In words | Next step |
|---|---|---|
| `too few odd/even events`, `too few events`, `too few seasons` | "Each transit has too few points for this test" / "fewer than three seasons have data" | re-vet under the current ladder (stacked-fold tests) |
| `equal secondary at phase 0.5: true period may be P / 2` | "The dip at phase 0.5 matches the main one, so the real period is probably half of this" | re-vet at the suggested period |
| `season-dependent depth`, `aperture-dependent depth` | "The depth changes with the detector or the aperture, which a real transit does not do" | pixel stage |
| `systematic_alias` | "The period sits on a known spacecraft rhythm" | pixel stage |
| `outage:` | "A catalogue did not answer when we asked" | retry the catalogue |
| `period matches ... but epoch differs` | "A catalogue has this period on a neighbour, but its transits fall at a different time" | pixel stage |
| `exception:` | "The check crashed" | re-vet under the current ladder; stays flagged for a person if it crashes again |

### 4.3 Stage 2: the recheck

A new engine step, `skysift recheck`, run automatically at the end of every `skysift run` and available on its own. It takes every signal whose final status is `undetermined` or `candidate_stage1` under the current search version and applies, in this order:

1. **Re-vet under the current ladder** when the signal's stored ladder version is older than the current one (the stacked-fold tests resolve the "too few events" class).
2. **Re-vet at the suggested period** when rung 2 recorded `suggested_period`: the engine re-measures the signal at P/2 (new `Signal` row with `branch = "recheck:P/2"` and `parent_signal_id`), vets it, and marks the original `superseded` when the new one is not undetermined for the same reason. A P/2 signal that shows equal odd and even depths and no secondary is a real period-doubling fix; one that shows unequal depths is an eclipsing binary and fails rung 2 on its own merits.
3. **Retry the catalogue** when rung 4 recorded an outage: rung 4 alone is run again.
4. **Pixel stage** for every remaining `candidate_stage1` and for every undetermined signal whose reason maps to "pixel stage": a new rung `rung5_pixels` (section 4.4). A pass promotes to `candidate_stage2`; a fail rejects with the reason "the dip is on a neighbouring star"; no data (no target pixel files reachable, fewer than three usable transits) leaves it undetermined with that reason.

Rechecks write ordinary test rows, so the dashboard shows them in the same ladder and popup. A signal keeps its history: the popup lists each recheck with its date and outcome.

### 4.4 The pixel rung

`rung5_pixels` implements, inside the engine, what was done by hand for KIC 6146676 and KIC 5689121 on 20 October (`docs/candidates/*/localisation/diffimage.py`, `context/a2_leak_test.py`):

- Fetch the Kepler long-cadence target pixel files of the star whose pixels hold the target (`Target.meta["tpf_org"]` for KBonus; the target itself when it was a Kepler target) through the existing `net.fetch` and blob cache; one HLSP-style query logged like every other.
- Per quarter: mean in-transit image minus mean out-of-transit image (1 to 3 durations either side, quality flag 0 only), stacked per season with variance weights.
- Localise the lost light with the 3x3 flux-weighted centroid around the highest signal-to-noise difference pixel (the brightest-pixel estimator is not used: it was bimodal on KIC 6146676), with a 200-draw noise Monte Carlo for its error.
- Fit the two-source model (target and the Kepler target at their WCS positions, Gaussian PSF with a free common shift) and compare the implied depth on each star.
- Leak test: the Kepler target's own PDCSAP light curve at the ephemeris, against the leak and on-target expectations.
- Verdict: pass when, in every season with at least two transits, the centroid is nearer the target than the neighbour by more than three times its error and the two-source depth on the neighbour is consistent with zero; fail when the centroid or the two-source fit prefers the neighbour in a majority of seasons; undetermined otherwise, with the reason stating which season disagreed.

Cost: about 50 MB of pixel files and one to two CPU minutes per signal; it runs only on the handful of signals that reach it. Metrics stored: per-season centroid offsets and errors, two-source depths, the leak-test depth, the number of transits used, and the pixel-file manifest.

Rulings on which seasons vote (the history is in the recheck ledger):
- 20 October 2026 (Task 4 review): a season votes only when both stars' 3 x 3 boxes lie complete on valid pixels of the season's union frame; the centroid test is the signed distance from the perpendicular bisector of the two stars over the centroid error along the star axis; a pass needs every voting season above three sigma on the target's side.
- 21 October 2026 (final review): a pass needs at least two voting seasons.
- 22 October 2026 (`docs/superpowers/specs/2026-10-22-skysift-pixel-partial-box-design.md`): a box cut by the stamp edge or by a dead pixel still votes when its centre and at least six of its nine pixels are valid. The centroid is measured on the pixels present, the two-source fit on every valid pixel as before, and the shift the cut would give a point source of the fitted PSF width (the edge allowance) is added in quadrature to the centroid error before the three-sigma test. The metrics record each box's valid-pixel count and missing pixels, the allowance and the raw statistic; the figure, the explain sentences, the pack and the dossier caveats name a season whose box was cut. Complete boxes vote exactly as before. The live recheck of 21 October had silenced three of four seasons for KIC 5689121 and KIC 6699887 under the complete-box rule, because a KBonus background star sits where someone else's stamp caught it.

## 5. Pages

Navigation: **Tonight** (home), **Candidates**, **Live**, **Sources**, **Approved**, and About in the footer. On phones the navigation is a bottom bar with icons and labels.

### 5.1 Tonight (home, replaces Sources as the landing page)

The morning-after screen. Top: a hero line in the brand face ("Tonight SkySift scanned 692 stars and found 2 candidates") that counts up on load, with the sweep progress ring (percent of the current list, time left) when a sweep is running, or the last sweep's summary when idle. Below: three stat tiles (candidates waiting for review, undetermined being rechecked, rejected tonight) with one-line glosses. Then the **discovery feed**: the newest candidates as cards (section 5.2), newest first, at most six, with "See all". Then a quiet strip of the queue of rechecks in progress ("3 signals being rechecked at half their period"). Expert mode adds the rung funnel and the throughput line from Live. Empty state for a fresh install: "No sweep yet. Pick a source and start one" with the Start button.

### 5.2 Candidate cards and the Candidates page

A card is the unit of excitement. It holds: the target name and a human line ("red dwarf, 303 pc" when known from the catalogue, else the magnitude), a **sparkline of the folded transit** (an inline SVG from `/api/signals/{id}/sparkline.svg`, 240 x 72, binned fold with the model line, cached per signal), three numbers with glosses (period, depth as a percentage and in ppm, how many transits were seen), a **verdict pill** that opens the popup (section 5.4), the stage ("stage 1", "stage 2 pixel-vetted"), and in expert mode the SDE and SNR. The card is a link to the signal page; the pill and the "why" link are separate controls (keyboard reachable).

The Candidates page: filter chips with counts (candidates, undetermined, rejected, approved, all), a search box for a target id, sort control; simple mode shows cards in a responsive grid (one column at 360 px, two at 720, three at 1080); expert mode shows the dense table with the same pill, and the split view at 1280 px and above (polish spec 4.3, kept).

### 5.3 Signal page

Hero: target name, the one-sentence summary in words, the verdict pill, the stage, and the stat row. Then the folded transit full width (Plotly, model line, caption), then "Why" (the same content as the popup, expanded inline, one line per check with its numbers, the recheck history), then the plots (simple: full curve with transit marks, odd versus even, phase 0.5 window; expert: plus periodogram with threshold and aliases, per-season depths, pixel stage figures when they exist as a season grid of difference images with both stars marked), then the sticky decision bar with consequences spelled out, then history. A "Copy summary" button copies a plain-text paragraph (target, period, depth, verdict, link) for sharing.

### 5.4 The verdict popup

A `<dialog>` loaded by HTMX from `/partials/signals/{id}/verdict`, opened from any verdict pill (queue row, card, signal hero, Tonight feed), closed by Escape, the close button or clicking outside; focus moves into it and returns to the pill. Content:

1. Headline in words with the verdict colour and icon: "Undetermined: one check could not decide".
2. One row per rung, in ladder order: icon and word (passed, failed, no verdict, not run), the check's name in words, the plain sentence, and the deciding numbers in mono ("odd 2,202 ± 105 ppm, even 2,115 ± 170 ppm, 0.3 σ apart").
3. For each undetermined row, a highlighted block: **What was missing** (the sentence from 4.2) and **What happens next** (the next step, with its state: "queued for recheck", "rechecked on 20 Oct: resolved at 40.29 d", "needs pixel files, none reachable").
4. Footer: the status glossary line ("Stage 1 means every light-curve check passed; stage 2 adds the pixel checks") and a link to the full signal page.

Expert mode adds the raw metrics behind a disclosure per row and the rung duration.

### 5.5 Live (flight deck)

The progress ring large, the state in words ("vetting 6 stars, fetching 3, 41 min left"), a ticker of the last ten targets finished with their outcome icon, the Stop controls with their consequences, the counters, and in expert mode the rung funnel, throughput, cache, queries, last errors and the log. Polling as today (5 s, stops when idle). Idle state shows past sweeps.

### 5.6 Sources (start)

The three-step panel from the polish spec (pick, run, review) and the source cards with a Start button opening the form in a drawer where every field has a one-line explanation and a sensible default; expert mode adds the dense table.

### 5.7 Approved, About

Approved: cards in simple mode, table in expert, "Copy summary" per entry. About: the method in six short paragraphs, the status glossary of section 4.1, the recheck steps of 4.3 in words, the term glossary, the version and search configuration, the citation.

## 6. Behaviour that does not change

Routes for decisions, the review JSON, the CSRF token, loopback binding, the sweep launcher, HTMX polling, Plotly data from `/api/signals/{id}/plots.json`, append-only decisions. The engine's search version is untouched by the recheck (the pilot stays valid); the ladder version becomes "4" because `rung5_pixels` and the recheck semantics are new, and signals vetted under ladder 3 are re-vetted by step 1 of the recheck only when they are undetermined or stage-1 candidates.

## 7. Accessibility and responsiveness

Keyboard reachable throughout with visible focus rings; the dialog traps focus and restores it; `aria-live="polite"` on the Tonight counters, the Live state line and the decision toast; status by word or icon plus colour; AA contrast pinned by `tools/contrast_check.py` in both themes; widths 360, 768, 1024 and 1440 px with no horizontal page scroll; reduced motion honoured; the starfield has `aria-hidden` and `pointer-events: none`.

## 8. Testing

Unit tests for `explain` (every reason string the rungs can emit maps to a sentence and a next step; unknown reasons fall back to the raw text); template tests render every page in both modes and both themes with the `ui_seed.py` data, with legacy signals that predate the M3 and ladder-3 metrics, and with a stage-2 signal; the popup route returns the three blocks for an undetermined signal and no "what was missing" block for a candidate; the sparkline endpoint returns valid SVG and caches; engine tests for the recheck (period halving on a synthetic P-doubled signal, outage retry, ladder re-vet) and for `rung5_pixels` on a synthetic pixel cube with the dip injected on the target and on the neighbour; the final review takes screenshots at the four widths in both themes through the built-in browser and judges them against this document.

## 9. Out of scope

Accounts and remote access, a public read-only deployment, push notifications, a native app, charts beyond Plotly and inline SVG, TESS pixel data, PRF-based (rather than Gaussian) pixel fits, automatic approval.
