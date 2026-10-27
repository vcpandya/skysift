# SkySift two-candidate Research Note: design

Date: 27 October 2026. Written on main at f542d65, after the referee pack rounds 1 to 4 (spec `2026-10-22-skysift-referee-pack-design.md`). It closes row 27 of the referee checklist (`docs/superpowers/notes/2026-10-22-referee-checklist.md`): a star with two independent candidate periods is reported as two candidates in one note. The live case is KIC 6699887, whose stage 2 signals at 15.11 d (signal 3479, approved; 5590 is its recheck row at the same period) and 47.69 d (signal 3480, approved) have a period ratio of 3.155 and are not aliases of each other. Today the dossier and the pack are one per target, the pack writes the note for 15.11 d only, and its README says the other period "needs its own dossier and pack".

The precedent is the Kepler catalogue itself: a multi-planet KOI host carries one row per candidate (Thompson et al. 2018, Table 2), each with its own ephemeris, depth, duration, odd-even depths, secondary limit and centroid result, and the stellar parameters once. Bienias and Szabo 2025 report their KBonus background candidates per signal too. A Research Note of the AAS has room for one figure and 1,500 words with the title, headings, caption and references, so a two-candidate note is a word-budget problem as much as a data problem, and this spec settles both.

## 1. Intent

One note per star. Each candidate is reported with its own ephemeris and errors, depth, duration, limb-darkened shape fit, odd and even depths, secondary limit, pixel-centroid result, TESS statement and false-positive numbers (the in-house estimate and the stored TRICERATOPS cross-check, per signal). The star is described once (Gaia parameters, Bayestar19 reddening). The false-alarm paragraph is shared. The period ratio is stated with the alias test. The single figure shows one panel per candidate, each folded on its own ephemeris. Every single-candidate note, README, ExoFOP file and Zenodo record stays as it is, byte for byte apart from the commit stamp.

## 2. Goals, non-goals, anti-goals

Goals.
- A stored dossier for the second signal of a target, beside the target's own, with the same stale checks (tests, status, reviewer decision, null calibration, extinction) and its own TRICERATOPS cross-check.
- A pack that reads both, renders one two-candidate note inside the 1,500-word limit with the same 1,480-word test margin that `tests/unit/test_pack.py::test_the_live_shape_stays_inside_the_word_limit_with_margin` holds, and names both candidates in the README, the ExoFOP files (one planet-parameter file per candidate), `zenodo_metadata.json` and the plain texts.
- The single-candidate shape unchanged: the golden `tests/golden/kic-006146676-generated.json` stays as it is, and the live packs of KIC 6146676 and KIC 5689121 render the same note text apart from the commit stamp.
- Checklist row 27 marked covered, with the precedent; the README template no longer says the second period needs its own pack when both are in the note.

Non-goals.
- The dashboard. The Dossiers tile, the dossier page and the pack button keep showing the target's own dossier; the second signal's dossier is reached through the CLI and the pack. A dashboard view of it is a later change.
- A joint fit of the two candidates (mutual inclinations, transit-timing variations between them, a dynamical stability check). The note says the candidates were vetted singly.
- Three or more candidates on one star. The data side holds any number; the note shape is designed and tested for two, and a third is a gate problem (section 5.6) until a three-candidate shape is designed.
- A per-signal pack (two packs for one star). Row 27's alternative; the one-note shape is the precedent and the pack stays one per target.

Anti-goals (what would make this a failure even if it ran).
- A second candidate's number printed from the first candidate's dossier, or a shared sentence that silently quotes one candidate's value for both. Every per-candidate sentence is built from that candidate's own JSON, and the tests pin two different values in every such sentence.
- A single-candidate pack whose text changes. The two-candidate code path is reached only when a second current dossier exists; a pack built for a target without one goes through exactly today's functions and templates.
- A note over 1,500 words, or a two-candidate note that drops a number the single note states (every candidate keeps its ephemeris with errors, depth, duration, shape fit, odd-even, secondary limit, centroid result, TESS statement, FAP, TRICERATOPS and in-house FPP). The cuts of section 5.4 shorten wording and share method clauses; they drop no quantity.
- A publish, a push, a merge or a change to the Zenodo token.

## 3. The data side: a dossier for the second signal

### 3.1 Why not one dossier row per signal

`dossiers` is keyed by `target_id` (store spec 5.4). `store.dossier(target_id)` is read in the pack, the cross-check import and export, the dashboard's queries, the dossier routes and the published page. Re-keying the table by signal would touch all of them and the migration would have to rewrite every row. The two-candidate case is rare (two of the five live targets carry a second stage 2 signal, and one of those is an alias), so the second signal's dossier goes into a table of its own and the target's dossier row stays what it is.

### 3.2 The `further_dossiers` table (migration `005_further_dossiers`)

A second (or later) signal's dossier is a "further dossier". `FurtherDossierRow`, table `further_dossiers`:

| column | as in `dossiers` | note |
|---|---|---|
| `signal_id` | primary key, FK `signals.id` | one row per signal |
| `target_id` | indexed | the star |
| `generated`, `data_path`, `search_version`, `ladder_version`, `dossier_version`, `fap`, `fpp`, `fap_detail`, `fpp_detail`, `tests_fingerprint`, `status_at_generation` | same types and meaning | |
| `cross_check` | JSON, nullable | the TRICERATOPS block for this signal, stored only by `skysift crosscheck --import --signal` |

No pack columns: the pack is the target's (`dossiers.pack_path`, `candidates.pack_path` is set for every candidate's signal as today's `set_candidate_pack`). Store methods: `upsert_further_dossier(**fields)`, `further_dossier(signal_id)`, `further_dossiers(target_id)` (ordered by period through a join on `signals`), `set_further_cross_check(signal_id, block)`. `create_all` makes the table on a fresh schema; the migration step creates it with `checkfirst` on an old one, as `003_dossiers` does.

### 3.3 Generating it: `skysift dossier --target <id> --signal <sid> --further`

`dossier.generate(..., signal_id, further=True)` runs exactly the generation of today (the same `_assemble`, the same blocks, the same `DOSSIER_VERSION`) for the named signal and writes `<data_dir>/dossiers/<target_id>/s<signal_id>/dossier_data.json` and a `further_dossiers` row instead of the target's row and file. The artifact record is `skysift://dossier/<target_id>/s<signal_id>`. The no-op rule is the same (fingerprint, signal id, dossier version and the file present). The cross-check block is carried over from the further row with `current` recomputed, as the target's is.

Refused before any work, with exit 2 and the reason:
- `--further` without `--signal` ("a further dossier is for one named signal").
- a signal that is the target's best signal (`pick_signal(store, target_id)["id"]`): "signal N is the target's best signal; its dossier is the target's own (`skysift dossier --target <id>`)".
- a signal whose period is not independent of the target's dossier signal's period by `pack.independent_periods` (within 2 percent of 1, 2, 3, 1/2 or 1/3 times it): "signal N at P d is within 2 percent of R times the target's dossier period P0 d, an alias, not a second candidate".
- a target without a dossier of its own: "generate the target's dossier first".

`--signal` without `--further` keeps today's meaning: the target's own dossier is built from that signal (tests use it).

### 3.4 The cross-check per signal: `skysift crosscheck --target <id> --signal <sid> --export | --import`

With `--signal`, `export_inputs` reads the further dossier's JSON and `import_result` stores the block on the further row with `current` against that JSON's `inputs_fingerprint`. Without `--signal` both work on the target's dossier as today. A `--signal` that has no further dossier exits 1 naming the `--further` command.

### 3.5 Staleness and approval, per candidate

`pack._approval` today returns (approved, stale reason) for the target's signal. It becomes a per-candidate check: for each candidate signal (the target's and every further one the pack takes, section 4.1), the fingerprint of its latest tests, status, decision, the null calibration, the sweep size and the stored extinction is compared with its stored row's. The pack's `approved` is true only when every candidate's latest reviewer decision approves it (or its candidate row says approved or published); the README's "not yet approved" banner names the signals that are not. The stale reason names the signal: "the 47.690 d candidate's dossier (signal 3480) is stale: ...", with today's reasons after the colon, and "another signal is now the target's best" stays for the target's own. A stale further dossier is a stale pack: the README banner, the live-draft refusal and the dashboard's stale flag (which reads the target's row only, non-goal) behave as today, and the remedy line names both commands.

## 4. Which signals are the note's candidates

### 4.1 The candidate list

`pack.candidates(store, target_id)` returns the ordered list the pack reports:
1. the target's dossier signal, always first ("candidate 01");
2. then every further dossier of the target whose signal is still `candidate_stage2` or approved and not rejected by a reviewer, whose period is independent of the first's and of every candidate already listed (`independent_periods`, 2 percent), in increasing period.

A further dossier whose signal has since been rejected, superseded or merged into another is left out, and the README says so in one sentence under step 2 ("signal 3480's further dossier is left out: its status is now rejected"); so is one whose JSON file is gone from disk (the sentence names the file and the `--further` command). The list holds two candidates at most: a third further dossier, by period order, is left out with the sentence "signal N's further dossier is left out: the note shape holds two candidates, and a third needs its own shape (spec section 2)", and that sentence is also a live-gate problem, so a plain pack is still built as a correct two-candidate pack and no live draft is made (builder check, question 3). A stage 2 independent signal with no further dossier is named as today, with the remedy changed (section 6.3). The ordinal (01, 02) names the candidate in file names and ExoFOP targets; the prose names candidates by period ("the 15.11 d candidate", "the 47.69 d candidate"), never b and c, since "candidate" is the project's word.

### 4.2 The alias statement

The note states the period ratio and the test once, in the signals section: "The period ratio is 3.155, 5 percent from 3, outside the 2 percent alias tolerance of the search (ratios of 2, 3, 1/2 and 1/3), so neither is an alias of the other." The ratio is the longer period over the shorter at three decimals; the distance is from the nearest of the ratio set, in percent of that ratio, at the precision the distance needs (one significant figure when under 10 percent).

Beside it, `pack.coincident_transits(eph_a, eph_b, span)` counts the predicted transits of the longer-period candidate whose mid-time falls within half the sum of the two durations of a predicted transit of the shorter-period one, over the light curve's span. The README states the count always. The note states it only when it is not zero, since a coincidence can bias a depth and a referee asks; at zero the ratio sentence stands alone. For the live star the count is 1 of 31 ("one of the 31 predicted 47.69 d transits falls within a duration of a 15.11 d transit"), measured in the spike of 27 October on the two live JSONs, so the live note carries the clause. My call.

### 4.3 Prior claims and the hand-built study for a further dossier

`docs/candidates/<target>/prior_claims.json` is one file per star, and each claim carries `period_days` (or none). A claim belongs to the candidate whose period it is within 2 percent of (`alias_of` at ratio 1); a claim without a period belongs to the target's own dossier. `dossier.generate` stores on the target's own dossier the claims that match its period or have none, and on a further dossier only the claims that match its period, so a claim on the 15.11 d period never lands in the 47.69 d candidate's JSON, its comparison, its ExoFOP file or the note's title (builder check, question 2). The live stars' claims (KIC 5689121's, at its own period) are unchanged by this rule. The hand-built localisation study (`localisation/results.json`) is the target's own signal's: a further dossier stores `hand_built_localisation` as None and the README's hand-built sentence prints for the first candidate only.

## 5. The note

### 5.1 Shape

The template `note.tex.j2` gains a second branch, chosen by `len(candidates) > 1`, that renders the two-candidate shape below. The single-candidate branch is today's template text, unchanged. The data for the two-candidate branch is `numbers()` of each candidate's JSON (today's function, unchanged) in a list `cands`, plus a small `shared` dict (the star, the ratio sentence, the shared probability sentences, the caveat union, the caption) built by new functions in `pack.py`, each of which takes the list and never a single dossier.

Title: "Two transit candidates on KIC 6699887" (the periods are the abstract's first numbers; cut C18). With a prior claim on either candidate: "SkySift vetting of two transit candidates on KIC 6699887".

Author block, keywords, software, facilities, bibliography: as today. The bibliography is the union of the works either candidate's sentences cite (`references()` of each, merged), each once.

Abstract (at most 130 words for two candidates, held by the test; cut C15, since the body needs the room): one opening sentence naming the star and both periods ("This note reports two transit candidates on KIC 6699887, a star Kepler recorded only in other targets' pixels: a 4.02 hour, 1,160 ppm dip every 15.113696 days over 81 transits and a 5.47 hour, 993 ppm dip every 47.690566 days over 28."); one pixel sentence covering both when their verdicts agree ("In difference images both dips are nearer this star than its brightest neighbour in 4 of 4 observing seasons") and one each when they differ; "The planets would be about 2.9 and 2.3 Earth radii."; the false-alarm clause with both rates ("Null searches give false-alarm rates of 1 in 290 and 1 in N per star, about 6 and M such peaks expected across the 1,579-star sweep"); the TRICERATOPS headline with the two medians ("TRICERATOPS 1.1.0 gives false-positive probabilities of 0.028 and 0.037 (no validation class applies at 30-minute cadence; Giacalone et al. 2021)"), then the in-house values, in spec 11.2's order; "Both remain candidates pending follow-up." The builder check measured the first form of this abstract at about 161 words on the live numbers, so three clauses the single abstract carries are left to the body here: the pixel-excluded values (cut C19), the sweep-wide expectation after the false-alarm rates (C20) and the partial-box clause of the pixel sentence (C21); the body states all three. With them the estimate is about 127 words.

Section "The signals": the shared opening (the KBonus light curve, Gaia id and G, Kepler's pixels, the box search window: "SkySift's box search over 0.5 to 200 day periods found two independent signals"), the alias sentence (4.2), then one signal sentence per candidate in today's form with the SDE, S/N and transit count folded into it as a bracket, and the timing clause: "A dip of 1,160 ± 50 ppm lasting 4.02 (+0.13, -0.11) hours repeats every 15.113696 ± 0.000054 days (SDE 66.0, S/N 32.9, 81 transits), with the reference mid-transit at BJD_TDB 2455662.9571 ± 0.0015 at epoch 46, the centre of the series; 57 of 60 timed transits fit this linear ephemeris with an rms of 18.0 minutes." The fold clause ("the depth and shape below use the linear-fit ephemeris") is said once when both candidates fold the same way, else per candidate. A prior claim's sentence (the claim, its DOI and date, the relationship) follows the candidate it belongs to; the comparison sentence (the period, epoch and depth differences in sigma) goes to the README for a two-candidate note (cut C16), where it prints today.

Section "Vetting": the ladder sentence once, with the model clause once ("SkySift vets each signal through a fixed ladder of checks; the shape fit is a quadratic limb-darkened model \citep{claret2011} on a circular orbit, integrated over the 29.4-minute exposure."), then one compact vetting sentence per candidate, its clauses joined by semicolons: coverage; the fit's radius ratio with b and a/R* and the degeneracy or density clause; the odd and even depths with their separation; the phase 0.5 depth with the 3-sigma limit; the depth's agreement across quarter groups as "the depth agrees across quarter groups (p = 0.22)" (cut C17; the chi-squared and its degrees of freedom stay in the README). The strongest-dip-elsewhere and positive-excursion clauses of the single note's secondary sentence move to the README (cut C4). The catalogue sentence once: "Neither matches a Kepler KOI, DR25 TCE, TESS TOI or Kepler eclipsing binary entry." (when one does, that candidate's own sentence says so and the shared one is dropped).

Section "Which star is dimming": the per-target sentences once (the jitter-floor clause, the photometry sentence: "The depths are KBonus PSF photometry, decontaminated to first order, with no crowding correction applied, with a CROWDSAP of 0.88 and a PSF flux fraction inside the aperture of 0.73."), then one centroid sentence per candidate: the median distances from this star and the neighbour, the seasons voting with the partial-box clause, the self-referenced offset with its sigma and the Bryson rule when it applies, and the two-source depths. The Gaia-referenced offset, the exclusion radius, the nearest Gaia source beyond it, the pulled-reference clause, the SAP-depth ratio and the predicted leak go per candidate to the README only (cuts C5 and C14), where today's full offset sentence prints them; the note's data-availability line already points at the pack.

Section "The star and planets": the star paragraph exactly as the single note prints it, once; then one planet sentence per candidate: "The 15.11 d candidate's radius is 2.85 (+0.19, -0.15) R⊕ (radius ratio 0.0357), on an orbit of 0.1087 ± 0.0018 AU at an equilibrium temperature of 536 K and 19.7 times Earth's insolation; the 47.69 d candidate's is ..." with "at a Bond albedo of 0.3" once at the end. The radius bracket (the reddening sensitivity) once, on the first candidate, since it is the star's.

Section "How likely is a false positive": the false-alarm sentence with both counts and both rates ("6 of 1,998 null searches of other KBonus stars reached the 15.11 d candidate's SDE and K of N the 47.69 d candidate's (1 in 290 and 1 in M); across the 1,579-star sweep about 6 and J such peaks are expected by chance, and the vetting ladder, not the SDE, separates the candidates from them."); the TRICERATOPS sentence with the method clause once and the numbers paired in a fixed order (median FPP; without STP; pixel-excluded, and without STP; NFPP; runs), each pair "0.028 and 0.037", closing with one clause in place of the single note's own-evidence sentence: "the pixel-excluded values rest on our own difference-image centroids, the all-neighbours values on a crowding bound" (cut C13; the full sentence stays in the README and the Zenodo description); the in-house sentence once with both values; the TESS sentence once with the sector count and both significance ranges, citing Christ et al. 2019 once. When one candidate has a current cross-check and the other has none, the TRICERATOPS sentence names the one and says "no cross-check is stored for the 47.69 d candidate"; the live gate refuses as today (section 5.6).

Section "What is not yet known": the union of both candidates' caveat titles, each once, in the first candidate's order then the second's new ones, under today's rules (the partial-box caveat stated in the body is left out; the candidate caveat is left out), plus one sentence: "The two candidates were vetted singly; no joint fit was made."

Figure: two panels, (a) the first candidate's fold and (b) the second's, each on its own linear-fit (or search) ephemeris, each within its own window and bins, with the same y scale when the depths are within a factor of two (my call; else each its own). Caption: "(a) the light curve folded on the 15.113696 day linear-fit ephemeris and (b) on the 47.690453 day one, within 12.0 and 12.2 hours of mid-transit, in parts per million: bins of 22.5 and 27.0 minutes with standard errors (dots) and the transit fits (lines)." The odd and even panels and the difference-image panel of the single figure are not in the two-candidate figure (cut C10); the pack's `figures/` keeps them per candidate.

Data availability, software, facilities: as today, once.

### 5.2 Word budget

Measured on the parent's single-candidate pack of KIC 6699887 built today at f542d65: 1,291 words (996 of title, headings, text and caption; 144 abstract; 151 references). The references are shared, so the two-candidate body and abstract have 1,480 less 151 = 1,329 words between them, 1,181 for the body at a 148-word abstract.

The single note's sentences were measured on both live JSONs on 27 October (the 47.69 d dossier generated on a read-only copy of the live store; the ledger has the numbers): the signal sentence about 75 words each, the vetting sentences 154 and 148, the pixel text 163 and 164 (the offset sentence about 70 of each) with the photometry sentence 66, the false-alarm sentence 40, the TRICERATOPS sentence 109, the own-evidence sentence 48, the in-house sentence 14, the TESS sentence 31, the star paragraph about 95, the abstract 144, the references 151. Two candidates in those forms with the first twelve cuts alone come to about 1,630 words, so the cuts C13 to C18 were added. The estimate with all of them (the plan's first task measures the real template on the live JSONs before any other work, and the cuts are kept or extended until the measured total is at or under 1,480):

| part | words |
|---|---|
| title, headings | 26 |
| The signals (opening 45, alias and coincidence 40, two signal sentences 2 × 70) | 225 |
| Vetting (ladder and model once 30, two compact sentences 2 × 85, catalogue 14) | 214 |
| Which star is dimming (shared photometry and floor 45, two centroid sentences 2 × 85) | 215 |
| The star and planets (star 95, two planet sentences 70, albedo 8) | 173 |
| False positive (FAP 50, TRICERATOPS with the own-evidence clause 100, in-house 20, TESS 35) | 205 |
| What is not yet known | 35 |
| caption | 38 |
| data availability | 35 |
| body total | 1,166 |
| with abstract 130 and references 151 | 1,447 |

That leaves about 30 words of margin for the live star, which has no prior claim. A prior claim on one candidate adds about 25 words in the compact form of 5.1 (the claim sentence alone, the comparison in the README), so the shape with one claim is estimated near 1,472; the test holds both shapes at or under 1,480, and if the measurement shows the claim shape cannot, the plan lists the next cut (the catalogue sentence to the README, then the albedo clause) and the spec is amended, never the margin.

### 5.3 The margin test

`tests/unit/test_two_candidates.py::test_the_two_candidate_live_shape_stays_inside_the_word_limit_with_margin`: the first candidate is `_live_shape` of the golden (the longest single shapes: the dominant cross-check, the adopted reddening), the second a copy of the golden with its ephemeris, shape, parity, secondary, pixels, timing, TESS and cross-check numbers changed to a different, longer-period candidate (period × 3.156, more digits in the errors, a 2-digit transit count), with and without a prior claim on the first. Assertions: total ≤ 1,480, abstract ≤ 130, no gate problem for the length, and every per-candidate value appears exactly as that candidate's JSON gives it (the anti-goal of section 2: the two periods, depths, durations, radii, FPPs, FAPs and centroid distances each found once in the rendered note).

### 5.4 The cuts (the two-candidate shape against the single shape)

- C1 One opening for the light curve, the star's Gaia id and G, and Kepler's pixels; the search window named there and not in the vetting section.
- C2 The ladder sentence and the limb-darkening model clause once; per candidate the fit's numbers only.
- C3 The catalogue-match sentence once for both.
- C4 The secondary sentence keeps the phase 0.5 depth and the 3-sigma limit; the strongest dip elsewhere and the positive excursions go to the README's "Phase 0.5 and the red noise" paragraph, which prints them already.
- C5 The pixel section's photometry sentence (PSF photometry, CROWDSAP, PSF fraction) once; the SAP-depth ratio and the predicted-leak clause to the README per candidate; the nearest Gaia source beyond the exclusion radius once.
- C6 The star paragraph once; the planet sentence without "With the radius ratio of" (the ratio in brackets); the Bond albedo once.
- C7 The false-alarm sentence with both counts in one sentence; the "ladder, not the SDE" clause once.
- C8 The TRICERATOPS method clause once; the numbers paired; the own-evidence and in-house sentences once with both values.
- C9 The TESS sentence once with the sector count and both significance ranges.
- C10 Two fold panels; no odd-even or difference-image panels in the figure.
- C11 The caveat union, each title once.
- C12 The abstract's pixel sentence shared when the verdicts agree; the planet radii in one sentence.
- C13 The own-evidence sentence becomes a closing clause of the TRICERATOPS sentence; the full sentence stays in the README and the Zenodo description.
- C14 The Gaia-referenced offset, the exclusion radius, the nearest Gaia source beyond it and the pulled-reference clause to the README; the note keeps the self-referenced offset with its sigma and the Bryson rule.
- C15 The two-candidate abstract at or under 130 words (the single note's holds 148).
- C16 With a prior claim, the comparison sentence to the README; the note keeps the claim sentence on its candidate.
- C17 The depth-stability clause without the chi-squared and its degrees of freedom; the p-value stays.
- C18 The title without the periods; the abstract's first sentence carries them.
- C19 The abstract's TRICERATOPS clause carries the two medians only; the pixel-excluded values stay in the body.
- C20 The abstract's false-alarm clause gives the two rates without the sweep-wide expectation; the body keeps it.
- C21 The abstract's pixel sentence without the partial-box clause; the body keeps it.

### 5.5 Single-candidate identity

None of these cuts touches the single-candidate branch. The functions the two-candidate branch adds are new names (`shared_texts`, `candidates`, `coincident_transits`, `ratio_sentence`, `paired_probabilities`, `two_candidate_abstract`, `two_candidate_caption`, `caveat_union`); today's functions are called by both branches with one candidate's JSON and are not edited, apart from `build_pack` and `_write_pack` taking the list, `_approval` taking the list, and `_fields` and the README template reading `f.candidates` (empty for a single pack, so no template line changes its output when empty). The golden tests and the pinned-sentence tests of `test_pack.py` are the guard, and the live check of section 10 is the proof.

### 5.6 The gate

`gate_problems` runs on each candidate's JSON (ephemeris errors, shape errors, the cross-check verdict, the commit, the pixel rule, the pixel disagreement, prior claims) and names the candidate in each problem ("the 47.69 d candidate: no TRICERATOPS cross-check"); the note-level checks (the word count, the RNAAS limits, the forbidden words, the TRICERATOPS-first order in the abstract and the Zenodo description) run once on the two-candidate texts. Two new problems: a third further dossier left out of the list (section 4.1's sentence) and a candidate list whose further dossier is stale (section 3.5). A live Zenodo draft is refused on any of them, as today.

## 6. The pack's files and texts

### 6.1 File names

The first candidate's files keep today's names and content. The second candidate's per-candidate files carry `_02` on the stem: `data/ephemeris_02.csv`, `data/ephemeris_02.json`, `data/transit_times_02.csv`, `data/shape_02.csv`, `data/events_02.csv`, `data/stellar_provenance_02.csv` (the star is the same; the file is per dossier and the planet rows differ), `data/periodogram_02.json`, `data/rung_metrics_02.json`, `data/cross_check_02.json`, `data/lightcurve_02.csv` (the whitened flux is the signal's own: a second-pass signal has the first's transits masked), `note/figure1_data_02.csv`, `figures/fold_02.svg`, `figures/oddeven_02.svg`, `figures/periodogram_02.svg`, `figures/centroids_02.svg`, `dossier_02.html`, `provenance/calibration_02.json`. The ExoFOP copies follow the pack's `<target>_<date>_<content>.<ext>` with the content `fold-02`, `oddeven-02`, `periodogram-02`, `centroids-02`, `lightcurve-02`, `ephemeris-02`, `dossier-02` (hyphens, since `_CONTENT` takes no underscore). `FILE_NOTES` gives the note for a suffixed name by stripping the suffix and adding "(the 47.690 d candidate)"; the first candidate's notes read as today when there is a second, with "(the 15.114 d candidate)" added to the per-candidate ones only when `f.candidates` is not empty.

The ExoFOP planet files: `params_planet_<date>_001.txt` with target `TIC<tic>.01` for the first candidate and `params_planet_<date>_002.txt` with `.02` for the second; the stellar file once. `note/figure1.{svg,pdf,png}` is the two-panel figure; `note/figure1.svg` holds both folds.

`provenance/versions.json` gains `candidates`: `[{ordinal, signal_id, period_days, dossier_generated, dossier_path}]`, only when there is more than one (a single pack's file is unchanged). `provenance/hashes.json` covers every file as today.

### 6.2 The README

- The title and summary name both candidates ("Two dips, of 0.11 and 0.09 percent, repeat every 15.1 and 47.7 days on a star of magnitude 16.2.").
- The build sentence names both dossiers (each signal id, generated date).
- "The candidates in numbers": the table gains one value column per candidate, headed by the period ("15.114 d", "47.690 d"); the per-target rows (TESS sector count, the star's rows, the dilution rows) print once in a short table under it, headed "The star". With one candidate the table is today's.
- The alias sentence and the coincidence count, after the table.
- The methods paragraphs (errors, shape fit, TESS prediction, phase 0.5 and red noise, pixel offset, photometry and dilution, leak, FAP method, in-house method, cross-check) print once when the text is the same for both candidates and once per candidate, labelled, when it differs (the values differ in most; the labels are the periods).
- Step 2: the planet-parameter sentence names both files and both targets `.01` and `.02`; the other-signals sentence (6.3).
- Step 3: the word counts as today (one note).
- Step 5: the ephemeris paragraph and the next three transits for each candidate, labelled.
- "What is not yet known": the caveat union, each with its candidate named when it is one candidate's.
- "What is in the pack": every file, the suffixed ones with their candidate.

### 6.3 The other-signals sentence

`other_signals_sentence` today: "This star also has another stage 2 signal at 47.690 days, at a period unrelated to this one; this pack covers one candidate only, so that signal needs its own dossier and pack." It becomes, for a stage 2 independent signal without a current further dossier: "This star also has another stage 2 signal at 47.690 days (signal 3480), at a period unrelated to this one, which this pack does not report; run `skysift dossier --target kic-006699887 --signal 3480 --further`, then build the pack again, and the note reports both candidates." For a signal the pack reports, the sentence is not printed (the candidate is in every text). The pinned test of today's sentence changes with it.

### 6.4 Zenodo, ExoFOP note, archive email

- `metadata_from` takes the candidate list: title "SkySift candidates KIC 6699887: 15.114 and 47.690 d transit signals in Kepler photometry" (with a prior claim: "SkySift vetting of two transit candidates on ..."); the description's summary, one ephemeris sentence per candidate ("Ephemeris, 15.114 d candidate: ..."), the reddening paragraph once, both probability pairs (each candidate's FAP and FPP sentences, labelled), the record sentence ("two transit candidates") and the caveat union.
- `exofop_note.txt`: the heading names both periods; one ephemeris paragraph per candidate; the planet paragraph per candidate; the pixel paragraph per candidate; both probability pairs; the TESS line per candidate; the files line names the `_02` copies.
- `archive_email.txt`: the paragraph names both candidates with both ephemerides.
- The dashboard's pack button and `set_candidate_pack` record the pack on every candidate's candidate row.

## 7. What does not change

- `DOSSIER_VERSION` stays 6: a further dossier's JSON is the same format. `PACK_VERSION` becomes 6 (the two-candidate texts and the `_02` files).
- Every single-candidate text, file and name; the golden; the dashboard; `skysift dossier` without `--further`; `skysift crosscheck` without `--signal`.
- The engine's rules: no em-dashes, British spelling without the -ize family, "candidate" never "discovery", every probability with its name.

## 8. Testing

- `tests/unit/test_store.py`, `test_migrations.py`: the table, the four store methods, the migration on an old schema and on a fresh one.
- `tests/unit/test_dossier.py`: `--further` writes the further row and file, refuses the best signal, an alias period and a target without its own dossier; the no-op rule; the cross-check carried over.
- `tests/unit/test_crosscheck.py`: export and import with `--signal` read and write the further row; `current` against the further JSON.
- `tests/unit/test_two_candidates.py` (new, beside `test_pack.py`): the candidate list and its order; a rejected further signal left out; the alias and coincidence sentences; the two-candidate note's sections each pinned on a synthetic pair built from the golden (every per-candidate value from its own JSON, the shared sentences once); the margin test of 5.3; the gate problems per candidate and the three-candidate refusal; the README table, the `_02` files, the ExoFOP `.01`/`.02` files, the Zenodo metadata, the plain texts; the per-candidate stale and approval checks; and a test that `build_pack` for a target with no further dossier writes exactly the files it writes today (the file list and the note text pinned against the single-candidate build in the same test).
- `tests/unit/test_pack.py`: the other-signals sentence test updated to 6.3; nothing else changes.
- The golden `tests/golden/kic-006146676-generated.json` is not regenerated.
- The full gate `pytest tests -m "not slow and not golden" -p no:cacheprovider` green on the branch tip, run in the worktree with `PYTHONPATH` on the worktree's `src` (the engine's venv is an editable install of the main checkout).

## 9. The live run (KIC 6699887)

1. Back up `.superpowers/sample-run/engine.db` to `engine.db.bak-20261027-two.db`.
2. `skysift dossier --target kic-006699887 --signal 3480 --further` on the live data dir (the target's own dossier of 3479 is current from this morning's run and is not regenerated unless stale).
3. The TRICERATOPS cross-check for 3480. The round-4 result file for 3480 (median FPP 0.037, 26 October) is not on disk in the repository, the worktrees, the shared notes or any scratch folder, and the dossier row holds the 3479 block; so the run is made again: `skysift crosscheck --target kic-006699887 --signal 3480 --export`, `tools/triceratops_check.py` under `.venv-triceratops` in the round-4 layout (five processes of four seeds, seeds 1, 5, 9, 13 and 17, merged to one schema-3 result), then `--import --signal 3480`. The result is expected near 0.037; a difference is reported, not hidden.
4. `skysift pack --target kic-006699887 --out <scratch>/pack-two` (no `--zenodo`).
5. Compile: copy `note/note.tex` and `figure1.pdf` to a scratch folder, replace `\documentclass{aastex7}` with `\documentclass{aastex701}` (the MiKTeX class name), `pdflatex` twice; report the word counts (total, body, abstract, references) and the PDF path.
6. Build the single-candidate packs of KIC 6146676 and KIC 5689121 into scratch folders and diff their `note/note.tex` against the parent's packs of today at f542d65 (`.superpowers/packs-author/<target>/note/note.tex`): identical apart from the commit stamp.

## 10. Calls made here (for Vikram to check)

1. A separate table for the second signal's dossier rather than re-keying `dossiers` (3.1). Cost if wrong: a later unification touches two tables instead of one.
2. The first candidate is always the target's own dossier signal, whatever its period; further candidates follow in increasing period (4.1). For KIC 6699887 this is also increasing period.
3. The alias statement is the search's 2 percent ratio rule in words, with a transit-coincidence count in the README and in the note only when it is not zero (4.2).
4. The figure drops the odd-even and difference-image panels for two fold panels (5.1, C10); the pack's figures keep them per candidate.
5. The cuts C1 to C18 (5.4), and the rule that a cut shortens wording, shares a method clause or moves a secondary number to the README, and never drops a quantity the note promises in section 1.
6. The `_02` suffix on the second candidate's files and `.02` on its ExoFOP target, with the first candidate's names unchanged (6.1).
7. The TESS sentence, the FAP sentence and the TRICERATOPS sentence pair the numbers in one sentence each rather than repeating the sentence (5.1, C7 to C9).
8. A third candidate is left out of the list by period order, named in the README, and refused by the live gate until a shape is designed (4.1, 5.6); a plain pack is still built.
10. The abstract's three cuts C19 to C21, chosen over dropping the "other targets' pixels" clause, since each moved number is stated in the body and the opening sentence is what a reader of the abstract alone keeps (5.1).
11. A prior claim belongs to the candidate whose period it matches within 2 percent, and a claim without a period to the target's own dossier (4.3).
12. A further dossier stores no hand-built localisation (4.3).
9. The TRICERATOPS run for 3480 is made again in this plan since the round-4 file is lost (9.3), in the background during the implementation tasks.

## 11. Follow-ups, not in this plan

- A dashboard view of further dossiers (the Dossiers tile and the dossier page).
- A three-candidate shape, or a per-signal pack, when a star needs one.
- A joint fit or a stability check for two candidates on one star.
