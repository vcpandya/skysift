"""The submission pack (dossiers plan Task 6): the folder and zip ``build_pack`` writes from a generated dossier, the
README's five steps, the AASTeX v7 Research Note draft and its word count against the RNAAS limits, the ExoFOP files
and upload note, the provenance, the offline rule, and the ``skysift pack`` command with and without a Zenodo token.
The dossier comes from ``dossier.generate`` on the dashboard seed with a synthetic light curve and a fake Gaia fetch;
Zenodo is a fake session, so nothing reaches the network."""
import csv
import hashlib
import json
import re
import shutil
import sys
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest
from typer.testing import CliRunner

from skysift import dossier as dossier_module
from skysift import net, zenodo
from skysift import pack as pack_module
from skysift.cli import app
from skysift.dossier import LightCurveReleased, generate, whitened
from skysift.pack import (
    NOTE_LIMITS,
    PACK_VERSION,
    STEPS,
    NoDossier,
    PackError,
    build_pack,
    exofop_name,
    note_problems,
    note_words,
    render_note,
    render_readme,
)
from skysift.store.db import CandidateRow, QueryRow
from skysift.zenodo import ZenodoClient, ZenodoError
from tests.unit.test_dossier import _make_env, _pixels_npz, _store_pixels
from tests.unit.test_zenodo import DEPOSITION_ID, FakeSession
from tests.unit.ui_seed import STAGE2_TARGET

runner = CliRunner()
EM_DASH = chr(0x2014)
WARNING = ("This candidate is not yet approved by a reviewer. Build the record, but do not freeze or upload it before "
           "the approval.")
ISE = re.compile(r"\b(organis|characteris|summaris|localis|recognis|normalis|minimis|optimis)(e|ed|es|ing|ation)\b",
                 re.IGNORECASE)
SANDBOX_WORDS = "sandbox DOI, replace before submission"


def _today() -> str:
    return datetime.now(UTC).strftime("%Y%m%d")


def _zip_name() -> str:
    return f"{STAGE2_TARGET}_{_today()}_skysift_pack.zip"


@pytest.fixture(autouse=True)
def _svg_only(request, monkeypatch):
    """PNG drawing takes seconds per pack: only the tests that ask for ``with_matplotlib`` draw them."""
    if "with_matplotlib" not in request.fixturenames:
        monkeypatch.setattr(pack_module, "_matplotlib", lambda: None)


@pytest.fixture
def with_matplotlib():
    pytest.importorskip("matplotlib")


@pytest.fixture
def env_with_dossier(settings, monkeypatch):
    env = _make_env(settings, monkeypatch)
    _store_pixels(env, _pixels_npz())
    env.report = generate(env.store, env.blob, env.settings, STAGE2_TARGET, fetch=env.fetch)
    env.data = json.loads(env.report.data_path.read_text(encoding="utf-8"))
    return env


@pytest.fixture
def env_no_pixels(settings, monkeypatch):
    env = _make_env(settings, monkeypatch)
    env.report = generate(env.store, env.blob, env.settings, STAGE2_TARGET, fetch=env.fetch)
    return env


@pytest.fixture
def fake_session():
    return FakeSession()


def _build(env, **kw):
    return build_pack(env.store, env.blob, env.settings, STAGE2_TARGET, **kw)


def _files(rep) -> set[str]:
    """The pack's files, without the zip that sits in the same folder."""
    return {p.relative_to(rep.out_dir).as_posix() for p in rep.out_dir.rglob("*") if p.is_file()} - {
        rep.zip_path.name}


def _approve(env):
    env.store.decide(env.report.signal_id, "approve", "clean in every season")
    generate(env.store, env.blob, env.settings, STAGE2_TARGET, fetch=env.fetch)


# ---------------------------------------------------------------------------------------------------- the folder

def test_build_pack_writes_every_folder_and_the_zip(env_with_dossier):
    rep = _build(env_with_dossier)
    names = _files(rep)
    assert {"README.md", "note/note.tex", "data/lightcurve.csv", "data/ephemeris.json", "dossier.html",
            "provenance/queries.json", "exofop/note.txt"} <= names
    assert {"note/figure1.svg", "data/ephemeris.csv", "data/events.csv", "data/periodogram.json",
            "data/rung_metrics.json", "figures/fold.svg", "figures/oddeven.svg", "figures/periodogram.svg",
            "figures/centroids.svg", "provenance/versions.json", "provenance/hashes.json",
            "provenance/calibration.json", "archive_email.txt", "zenodo_metadata.json"} <= names
    assert rep.zip_path.exists() and zipfile.ZipFile(rep.zip_path).testzip() is None
    assert rep.out_dir == (env_with_dossier.settings.data_dir / "packs" / STAGE2_TARGET / _today()).resolve()
    assert rep.zip_path == rep.out_dir / _zip_name() and rep.zip_path.name not in rep.files
    assert [p.name for p in rep.out_dir.parent.iterdir()] == [rep.out_dir.name]      # nothing beside the folder
    with zipfile.ZipFile(rep.zip_path) as z:
        inside = {n.split("/", 1)[1] for n in z.namelist()}
        assert inside == names and {n.split("/", 1)[0] for n in z.namelist()} == {rep.zip_path.stem}
    assert sorted(rep.files) == sorted(names) and rep.approved is False and rep.zenodo is None
    assert rep.as_dict()["zip_path"] == str(rep.zip_path) and rep.as_dict()["files"] == rep.files
    assert rep.note_words["total"] <= NOTE_LIMITS["total"]
    versions = json.loads((rep.out_dir / "provenance" / "versions.json").read_text(encoding="utf-8"))
    assert versions["zip_name"] == _zip_name()


def test_svg_figures_are_well_formed_and_the_centroids_have_a_panel_per_season(env_with_dossier):
    import xml.etree.ElementTree as ET
    rep = _build(env_with_dossier)
    for name in ("fold", "oddeven", "periodogram", "centroids"):
        root = ET.fromstring((rep.out_dir / "figures" / f"{name}.svg").read_text(encoding="utf-8"))
        assert root.tag.endswith("svg") and root.find("{http://www.w3.org/2000/svg}title") is not None
    centroids = (rep.out_dir / "figures" / "centroids.svg").read_text(encoding="utf-8")
    assert centroids.count('class="season-panel"') == len(env_with_dossier.data["pixels"]) == 2
    assert "Season 0" in centroids and "Season 2" in centroids
    assert (rep.out_dir / "note" / "figure1.svg").read_bytes() == (rep.out_dir / "figures" / "fold.svg").read_bytes()


def test_png_figures_when_matplotlib_imports(env_with_dossier, with_matplotlib):
    rep = _build(env_with_dossier)
    names = _files(rep)
    assert {"figures/fold.png", "figures/oddeven.png", "figures/periodogram.png", "figures/centroids.png",
            "note/figure1.pdf"} <= names
    assert (rep.out_dir / "figures" / "fold.png").read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert (rep.out_dir / "note" / "figure1.pdf").read_bytes()[:4] == b"%PDF"
    assert f"exofop/{STAGE2_TARGET}_{_today()}_centroids.png" in names
    assert f"exofop/{STAGE2_TARGET}_{_today()}_centroids.svg" not in names          # one copy per figure
    readme = (rep.out_dir / "README.md").read_text(encoding="utf-8")
    assert "The figure is `note/figure1.pdf`." in readme and "matplotlib could not be imported" not in readme


def test_without_matplotlib_the_svgs_are_the_figures(env_with_dossier, monkeypatch):
    monkeypatch.setattr(pack_module, "_matplotlib", lambda: None)            # as when the import fails
    rep = _build(env_with_dossier)
    assert not any(n.endswith((".png", ".pdf")) for n in _files(rep))
    readme = (rep.out_dir / "README.md").read_text(encoding="utf-8")
    assert "matplotlib could not be imported" in readme and "the SVG files are the figures" in readme


def test_without_pixel_arrays_the_readme_says_the_pixel_step_has_not_run(env_no_pixels):
    rep = _build(env_no_pixels)
    assert "figures/centroids.svg" not in _files(rep)
    readme = (rep.out_dir / "README.md").read_text(encoding="utf-8")
    assert "no centroid figure" in readme and "the pixel step has not run" in readme


def test_lightcurve_csv_is_the_loader_s_whitened_flux(env_with_dossier):
    env = env_with_dossier
    rep = _build(env)
    with (rep.out_dir / "data" / "lightcurve.csv").open(encoding="utf-8", newline="") as fh:
        rows = list(csv.reader(fh))
    assert rows[0] == ["time_bkjd", "flux_rel_ppm", "err_ppm", "quarter"]
    fnorm = whitened(env.lc, env.data["signal"])
    good = np.isfinite(fnorm) & np.isfinite(env.lc.time)
    assert len(rows) - 1 == int(good.sum())
    first = int(np.flatnonzero(good)[0])
    assert float(rows[1][0]) == pytest.approx(env.lc.time[first], abs=1e-6)
    assert float(rows[1][1]) == pytest.approx((fnorm[first] - 1.0) * 1e6, abs=0.06)
    assert float(rows[1][2]) > 0 and int(rows[1][3]) == int(env.lc.quarter[first])


def test_data_files_carry_the_ephemeris_events_and_metrics(env_with_dossier):
    env = env_with_dossier
    rep = _build(env)
    eph = json.loads((rep.out_dir / "data" / "ephemeris.json").read_text(encoding="utf-8"))
    assert eph["ephemeris"]["period_days"] == env.data["ephemeris"]["period_days"]
    rows = {r["parameter"]: r for r in eph["rows"]}
    assert rows["planet radius"]["value"] == 2.61 and rows["planet radius"]["minus"] == 0.08
    assert rows["planet radius"]["unit"] == "Earth radii" and rows["planet radius"]["source"]
    with (rep.out_dir / "data" / "ephemeris.csv").open(encoding="utf-8", newline="") as fh:
        table = list(csv.DictReader(fh))
    assert [r["parameter"] for r in table] == [r["parameter"] for r in eph["rows"]]
    with (rep.out_dir / "data" / "events.csv").open(encoding="utf-8", newline="") as fh:
        events = list(csv.DictReader(fh))
    assert len(events) == len(env.data["plots"]["events"]) and set(events[0]) >= {"epoch", "t_mid_bkjd", "depth_ppm"}
    metrics = json.loads((rep.out_dir / "data" / "rung_metrics.json").read_text(encoding="utf-8"))
    assert [t["rung"] for t in metrics] == [t["rung"] for t in env.data["tests"]]
    pg = json.loads((rep.out_dir / "data" / "periodogram.json").read_text(encoding="utf-8"))
    assert pg["spectrum"] == env.data["plots"]["spectrum"] and pg["sde"] == 50.5


def test_provenance_hashes_every_file_and_names_the_versions(env_with_dossier):
    env = env_with_dossier
    rep = _build(env)
    hashes = json.loads((rep.out_dir / "provenance" / "hashes.json").read_text(encoding="utf-8"))
    assert set(hashes) == _files(rep) - {"provenance/hashes.json"}
    for name, h in hashes.items():
        data = (rep.out_dir / name).read_bytes()
        assert h == {"sha256": hashlib.sha256(data).hexdigest(), "nbytes": len(data)}
    versions = json.loads((rep.out_dir / "provenance" / "versions.json").read_text(encoding="utf-8"))
    assert versions["pack_version"] == PACK_VERSION and versions["search_version"] == env.data["signal"][
        "detector_version"]
    assert versions["ladder_version"] == env.data["provenance"]["ladder_version"] and versions["git_commit"]
    assert versions["creators_source"] == "CITATION.cff in the source checkout"
    queries = json.loads((rep.out_dir / "provenance" / "queries.json").read_text(encoding="utf-8"))
    assert queries == env.data["provenance"]["queries"] and all(q["sha256"] for q in queries)
    calib = json.loads((rep.out_dir / "provenance" / "calibration.json").read_text(encoding="utf-8"))
    assert calib["fap"]["n"] == 440 and calib["rows_by_version"] == {env.data["signal"]["detector_version"]: 220}
    assert calib["calibration_file"] == "calibration/kbonus_bkg.jsonl" and calib["calibration_sha256"]


def test_no_local_path_reaches_the_pack(env_with_dossier):
    """A pack is meant for a public record: the data folder's absolute path (here the test's tmp folder, whose name
    is unique) appears in no file, the dossier page included."""
    rep = _build(env_with_dossier)
    marker = env_with_dossier.settings.data_dir.resolve().name
    for name in _files(rep):
        if name.endswith((".png", ".pdf")):
            continue
        assert marker not in (rep.out_dir / name).read_text(encoding="utf-8"), name
    html = (rep.out_dir / "dossier.html").read_text(encoding="utf-8")
    assert "calibration\\/kbonus_bkg.jsonl" in html                     # the relative name, with its slash escaped


def test_dossier_html_is_the_standalone_export(env_with_dossier):
    rep = _build(env_with_dossier)
    html = (rep.out_dir / "dossier.html").read_text(encoding="utf-8")
    assert html.startswith("<!doctype html>") and 'id="dossier-data"' in html and "<script src=" not in html


def test_rebuilding_the_same_day_replaces_the_pack_and_a_foreign_folder_is_refused(env_with_dossier, tmp_path):
    first = _build(env_with_dossier)
    (first.out_dir / "stray.txt").write_text("left over", encoding="utf-8")
    second = _build(env_with_dossier)
    assert second.out_dir == first.out_dir and "stray.txt" not in _files(second)
    foreign = tmp_path / "my documents"
    foreign.mkdir()
    (foreign / "thesis.docx").write_bytes(b"keep me")
    with pytest.raises(PackError, match="not a submission pack"):
        _build(env_with_dossier, out_dir=foreign)
    assert (foreign / "thesis.docx").read_bytes() == b"keep me"
    out = _build(env_with_dossier, out_dir=tmp_path / "custom")
    assert out.out_dir == (tmp_path / "custom").resolve() and (tmp_path / "custom" / _zip_name()).is_file()
    other = _build(env_with_dossier, out_dir=tmp_path / "other")                     # same day, another folder
    assert other.zip_path != out.zip_path and out.zip_path.is_file() and other.zip_path.is_file()


def test_build_pack_without_a_dossier_raises(env_with_dossier):
    with pytest.raises(NoDossier, match="skysift dossier --target kic-000000001"):
        build_pack(env_with_dossier.store, env_with_dossier.blob, env_with_dossier.settings, "kic-000000001")


def test_a_released_light_curve_propagates(env_with_dossier):
    env = env_with_dossier
    env.store.mark_artifact_deleted(env.raw_sha)
    with pytest.raises(LightCurveReleased, match="--retention keep"):
        _build(env)
    assert env.store.dossier(STAGE2_TARGET)["pack_path"] == ""


def test_build_pack_never_fetches_and_never_regenerates(env_with_dossier, monkeypatch):
    """Spec 5.8: the pack reads the stored dossier and the stored light curve; it asks nothing of the network."""
    def boom(*_a, **_kw):
        raise AssertionError("the pack must not call this")
    monkeypatch.setattr(dossier_module, "generate", boom)
    monkeypatch.setattr(net, "fetch", boom)
    with env_with_dossier.store.session() as s:
        before = s.query(QueryRow).count()
    rep = _build(env_with_dossier)
    with env_with_dossier.store.session() as s:
        assert s.query(QueryRow).count() == before
    assert rep.zip_path.is_file()


# ---------------------------------------------------------------------------------------------------- the texts

def test_readme_lists_the_five_steps_and_the_approval_warning(env_with_dossier):
    text = (_build(env_with_dossier).out_dir / "README.md").read_text(encoding="utf-8")
    for _n, title in STEPS:
        assert title in text
    assert text.splitlines()[0] == WARNING
    assert "not yet approved" in text and EM_DASH not in text and "stakeholder" not in text.lower()
    assert "discover" not in text.lower() and not ISE.search(text)
    for account in ("Zenodo account", "ExoFOP", "AAS journals account", "arXiv", "help desk"):
        assert account in text
    assert "Gaia DR3 2101234567890123462" in text and "KIC id is accepted directly" in text
    assert "\n\nData Tag: " in text and f"{_today()}_<username>_skysift-{STAGE2_TARGET}" in text
    assert "password" not in text.lower() and "public at once" in text and "TFOP" in text
    assert "TIC-unknown" not in text and "never publishes" in text and "takes a KIC id directly" in text
    assert "under the star's TIC id" not in text
    assert "https://exoplanetarchive.ipac.caltech.edu/support/helpdesk" in text
    assert "1,500 words including the title, headings, caption and references" in text and "1,000" not in text
    assert "\n2. Replace 10.5281/zenodo.XXXXXXX with the Zenodo DOI from step 1.\n3. Read every" in text


def test_the_readme_prints_both_word_counts(env_with_dossier):
    rep = _build(env_with_dossier)
    text = (rep.out_dir / "README.md").read_text(encoding="utf-8")
    w = rep.note_words
    assert f"{w['total']} words in all ({w['body']} of title, headings, text and caption, {w['abstract']} of " \
           f"abstract, {w['references']} of references" in text
    assert w == note_words((rep.out_dir / "note" / "note.tex").read_text(encoding="utf-8"))
    assert "Over the limit" not in text


def test_an_approved_candidate_has_no_warning_and_its_candidate_row_gets_the_path(env_with_dossier):
    env = env_with_dossier
    _approve(env)
    rep = _build(env)
    text = (rep.out_dir / "README.md").read_text(encoding="utf-8")
    assert rep.approved and "not yet approved" not in text and text.startswith("# Submission pack for KIC 6146676")
    assert env.store.dossier(STAGE2_TARGET)["pack_path"] == str(rep.out_dir)
    assert env.store.dossier(STAGE2_TARGET)["pack_built"] is not None
    with env.store.session() as s:
        row = s.query(CandidateRow).filter(CandidateRow.signal_id == env.report.signal_id).one()
        assert row.pack_path == str(rep.out_dir)


def test_a_stale_dossier_is_named_in_the_readme(env_with_dossier):
    env = env_with_dossier
    env.store.decide(env.report.signal_id, "needs_data", "a second season")
    text = (_build(env).out_dir / "README.md").read_text(encoding="utf-8")
    assert "stale" in text and f"skysift dossier --target {STAGE2_TARGET} --force" in text


def test_steps_titles_are_the_dashboard_s():
    from skysift.ui.routes.dossiers import PACK_STEPS
    assert [(str(n), title) for n, title, _text in PACK_STEPS] == list(STEPS)
    assert [n for n, _t in STEPS] == ["1", "2", "3", "4", "5"]


KNOWN_NOTE = r"""\documentclass{aastex7}
% a comment that mentions \begin{document} and seven more words here
\begin{document}
\title{Two words}
\author[0000-0002-1825-0097]{Some Author}
\affiliation{Somewhere Far}
\begin{abstract}
Three abstract words.
\end{abstract}
\keywords{\uat{Exoplanets}{498}, \uat{Transit photometry}{1709}}
\section{One}
Four body words here, \citep{a} and 50\% more. % a trailing comment
\begin{figure}[ht!]
\includegraphics[width=\linewidth]{figure1}
\caption{Cap two.}
\end{figure}
\begin{thebibliography}{}
\bibitem[{A}(2020)]{a} Ref words five here now.
\end{thebibliography}
\end{document}
"""


def test_note_words_counts_a_known_note():
    """Title 2, heading 1, text 4 + a citation (3: author, et al., year) + "and 50% more" (3), caption 2; abstract 3;
    references 5. The frame comment, the author block and the keywords do not count."""
    assert note_words(KNOWN_NOTE) == {"abstract": 3, "body": 15, "references": 5, "total": 23}
    assert note_problems(note_words(KNOWN_NOTE), KNOWN_NOTE) == []


def test_a_note_over_the_limits_is_reported():
    long_abstract = KNOWN_NOTE.replace("Three abstract words.", "word " * 151)
    padded = long_abstract.replace("Four body words here,", "text " * 1500)
    words = note_words(padded)
    assert words["abstract"] == 151 and words["total"] > NOTE_LIMITS["total"]
    problems = note_problems(words, padded.replace(r"\end{figure}", r"\end{figure}\begin{table}x\end{table}"))
    assert len(problems) == 3 and "1,500" in problems[0] and "150" in problems[1] and "RNAAS takes one" in problems[2]


def test_note_is_aastex7_with_an_abstract_inside_the_rnaas_limits(env_with_dossier):
    data = env_with_dossier.data
    tex = render_note(data)
    words = note_words(tex)
    assert tex.startswith(r"\documentclass{aastex7}") and r"\begin{abstract}" in tex
    assert 0 < words["abstract"] <= NOTE_LIMITS["abstract"] and words["total"] <= NOTE_LIMITS["total"]
    assert note_problems(words, tex) == [] and tex.count(r"\begin{figure}") == 1
    assert "candidate" in tex and "discover" not in tex.lower() and EM_DASH not in tex and "---" not in tex
    assert tex.rstrip().endswith(r"\end{document}") and tex.count("{") == tex.count("}")
    assert r"\uat{Transit photometry}{1709}" in tex
    assert tex.count(r"\begin{document}") == 1                          # the frame never names the begin line
    abstract = tex.split(r"\begin{abstract}")[1].split(r"\end{abstract}")[0]
    assert "1 in 440" in abstract and "under 1 percent" in abstract and "provisional" in abstract
    assert re.search(r"the dip is nearer this star than its brightest neighbour in \d of \d "
                     r"(observing seasons|seasons that voted)\.", abstract)
    assert "lost light" not in tex and "on this star in" not in tex
    assert "10.5281/zenodo.XXXXXXX" in tex and r"\includegraphics" in tex
    cited = set(re.findall(r"\\cite[pt]?\{([^}]+)\}", tex))
    keys = {k.strip() for c in cited for k in c.split(",")}
    items = set(re.findall(r"\\bibitem\[[^\]]*\]\{([^}]+)\}", tex))
    assert keys and keys == items                                              # every citation listed, none unused
    assert {"mann2015", "mann2019", "kopparapu2013", "lindegren2021", "dressing2015", "kbonus", "kbonushlsp"} <= items
    assert "fressin2013" not in items                                         # an M dwarf: the Dressing grid only
    assert "AJ, 166, 265, doi:10.3847/1538-3881/ad0727" in tex and "doi:10.17909/7jbr-w430" in tex
    assert "40.290" in tex and "1 in 440" in tex and "provisional" in tex
    assert re.search(r"2\.61\$\^\{\+0\.09\}_\{-0\.08\}\$", tex)                 # Rp with its asymmetric errors
    assert "3,653$^{+90}_{-73}$ K" in tex                                      # thousands as the dossier prints


def test_note_for_a_star_without_parameters_or_pixels(env_with_dossier):
    data = json.loads(json.dumps(env_with_dossier.data))
    data["star"] = {**data["star"], "route": "none", "teff": None, "radius": None, "mass": None,
                    "distance_pc": None, "spectral_type": None}
    data["planet"] = {k: None for k in data["planet"]}
    data["tests"] = [t for t in data["tests"] if t["rung"] != "rung5_pixels"]
    data["pixels"] = None
    data["fpp"]["scenarios"][0]["factors"]["class"] = "FGK"
    tex = render_note(data, doi_placeholder="10.5281/zenodo.123")
    assert "mann2015" not in tex and "kopparapu2013" not in tex and "fressin2013" in tex
    assert "pixel" in tex and "has not run" in tex and "10.5281/zenodo.123" in tex
    assert tex.count("{") == tex.count("}") and note_problems(note_words(tex), tex) == []


def test_the_star_sentence_leaves_out_a_missing_mass_or_temperature(env_with_dossier):
    data = json.loads(json.dumps(env_with_dossier.data))
    data["star"] = {**data["star"], "teff": None, "mass": None, "spectral_type": None}
    tex = render_note(data)
    assert "a mass of" not in tex and "effective temperature of" not in tex and "a radius of 0.509" in tex
    items = set(re.findall(r"\\bibitem\[[^\]]*\]\{([^}]+)\}", tex))
    keys = {k.strip() for c in re.findall(r"\\cite[pt]?\{([^}]+)\}", tex) for k in c.split(",")}
    assert keys == items and "mann2019" not in items and "pecaut2013" not in items


def test_note_escapes_latex_characters(env_with_dossier):
    data = json.loads(json.dumps(env_with_dossier.data))
    data["target"]["name"] = "KIC 6146676 & 50% of_it"
    tex = render_note(data)
    assert r"KIC 6146676 \& 50\% of\_it" in tex


def test_every_pack_text_gives_both_probabilities_and_says_provisional(env_with_dossier):
    rep = _build(env_with_dossier)
    fap, fpp = env_with_dossier.data["fap"]["words"], env_with_dossier.data["fpp"]["words"]
    for name in ("README.md", "note/note.tex", "exofop/note.txt", "archive_email.txt"):
        text = (rep.out_dir / name).read_text(encoding="utf-8")
        assert fap in text and fpp in text and "provisional" in text, name
        assert EM_DASH not in text and "discover" not in text.lower() and not ISE.search(text), name


def test_the_exofop_note_keeps_its_blank_lines(env_with_dossier):
    text = (_build(env_with_dossier).out_dir / "exofop" / "note.txt").read_text(encoding="utf-8")
    assert "= Gaia DR3 2101234567890123462\n\nFound in the KBonus" in text
    assert text.startswith("NOT YET APPROVED BY A REVIEWER: do not upload this before the approval.\n\nSkySift")


def test_render_readme_takes_the_report_fields(env_with_dossier):
    text = render_readme(env_with_dossier.data, {"approved": True, "tic": "169822650", "date": "20261021",
                                                 "tag": "skysift", "files": ["README.md"], "png": True,
                                                 "zenodo": None, "stale_reason": None, "pixels_figure": True})
    assert "not yet approved" not in text and "TIC 169822650" in text
    assert f"{STAGE2_TARGET}_20261021_lightcurve.csv" in text and f"{STAGE2_TARGET}_20261021_skysift_pack.zip" in text


def test_exofop_names_follow_the_pack_s_convention():
    name = exofop_name(STAGE2_TARGET, "20261021", "lightcurve", "csv")
    assert name == "kic-006146676_20261021_lightcurve.csv"
    assert exofop_name("gaia:dr3-1", "20261021", "fold", "svg") == "gaia-dr3-1_20261021_fold.svg"
    for bad in (("../x", "20261021", "fold", "svg"), (STAGE2_TARGET, "2026-10-21", "fold", "svg"),
                (STAGE2_TARGET, "20261021", "Fold!", "svg")):
        with pytest.raises(PackError):
            exofop_name(*bad)


def test_a_tic_id_goes_into_the_upload_note_and_a_bad_one_is_refused(env_with_dossier):
    rep = _build(env_with_dossier, tic="TIC 169822650", observer_tag="vpandya")
    exo = {n for n in _files(rep) if n.startswith("exofop/")}
    date = _today()
    assert {f"exofop/{STAGE2_TARGET}_{date}_lightcurve.csv", f"exofop/{STAGE2_TARGET}_{date}_ephemeris.csv",
            f"exofop/{STAGE2_TARGET}_{date}_dossier.html", f"exofop/{STAGE2_TARGET}_{date}_fold.svg",
            "exofop/note.txt"} <= exo
    assert "TIC 169822650" in (rep.out_dir / "exofop" / "note.txt").read_text(encoding="utf-8")
    assert f"{date}_<username>_vpandya-{STAGE2_TARGET}" in (rep.out_dir / "README.md").read_text(encoding="utf-8")
    for bad in ({"tic": "1698;rm"}, {"observer_tag": "my tag"}):
        with pytest.raises(PackError):
            _build(env_with_dossier, **bad)


def test_creators_fall_back_to_the_package_metadata(monkeypatch, tmp_path):
    from importlib import metadata as md

    class Meta:
        def get_all(self, key):
            return {"Author": ["Vikram Pandya"], "Author-email": ["Vikram Pandya <v@example.org>"]}.get(key)
    monkeypatch.setattr(md, "metadata", lambda _name: Meta())
    citation, source = pack_module.load_citation(tmp_path / "missing.cff")
    assert citation == {"authors": [{"name": "Vikram Pandya"}]} and "package's author metadata" in source

    class Empty:
        def get_all(self, key):
            return None
    monkeypatch.setattr(md, "metadata", lambda _name: Empty())
    citation, source = pack_module.load_citation(tmp_path / "missing.cff")
    assert citation is None and "placeholders" in source


def _with_a_silent_season(data: dict) -> dict:
    """The fixture's dossier with three pixel seasons: two that voted for this star and one that did not, as the
    real KIC 6146676 run has season 3: its two-source depths are null and its ``nearer`` is unknown."""
    data = json.loads(json.dumps(data))
    row = next(t for t in data["tests"] if t["rung"] == "rung5_pixels")

    def season(n, depth, voting=True, why=None):
        return {"season": n, "n_events": 9, "dist_target_pix": 0.15, "dist_neighbour_pix": 2.8,
                "centroid_sigma_pix": 0.06, "nearer": "target" if voting else "unknown",
                "depth_target_ppm": [depth, 600.0] if voting else [None, None],
                "depth_neighbour_ppm": [-20.0, 50.0] if voting else [None, None], "two_source": voting,
                "voting": voting, "non_voting_reason": why}
    row["metrics"]["seasons"] = [season(0, 2800.0), season(1, 2300.0),
                                 season(3, None, voting=False, why="box incomplete: target")]
    return data


def test_a_season_that_did_not_vote_is_named_and_its_null_depths_are_skipped(env_with_dossier):
    data = _with_a_silent_season(env_with_dossier.data)
    text = pack_module.pixel_sentence(data)
    assert "the dip is nearer this star than its brightest neighbour in 2 of 2 seasons that voted" in text
    assert "Season 3 did not vote (box incomplete: target)." in text and "nan" not in text
    assert "A two-source fit gives a median depth of 2,550 ppm on this star" in text  # the voting seasons only
    abstract = pack_module.abstract_text(data)
    assert "nearer this star than its brightest neighbour in 2 of 2 seasons that voted" in abstract
    tex = render_note(data)
    assert "Season 3 did not vote" in tex and note_problems(note_words(tex), tex) == []
    for season in next(t for t in data["tests"] if t["rung"] == "rung5_pixels")["metrics"]["seasons"]:
        season["voting"], season["non_voting_reason"] = False, "box incomplete: target"
    assert "no season voted" in pack_module.pixel_sentence(data)


def test_a_crashed_build_leaves_no_folder_that_blocks_the_retry(env_with_dossier, monkeypatch):
    env = env_with_dossier
    first = _build(env)
    before = (first.out_dir / "README.md").read_bytes()
    real = pack_module._write_texts

    def crash(*_a, **_kw):
        raise RuntimeError("a bug half way through")
    monkeypatch.setattr(pack_module, "_write_texts", crash)
    with pytest.raises(RuntimeError, match="half way"):
        _build(env)
    assert (first.out_dir / "README.md").read_bytes() == before                    # the earlier pack is intact
    assert [p.name for p in first.out_dir.parent.iterdir()] == [first.out_dir.name]  # no partial folder left
    fresh = env.settings.data_dir / "fresh"
    with pytest.raises(RuntimeError):
        _build(env, out_dir=fresh)
    assert not fresh.exists() and not any(env.settings.data_dir.glob(".*building*"))
    monkeypatch.setattr(pack_module, "_write_texts", real)
    assert _build(env, out_dir=fresh).zip_path.is_file()                             # the retry goes through


def _aside(folder: Path, kind: str) -> Path:
    return folder.with_name(f".{folder.name}.{kind}")


def test_a_failed_swap_keeps_the_earlier_pack_and_the_new_build(env_with_dossier, monkeypatch):
    """Step 2 of the swap fails (the new folder will not rename into place): the earlier pack is renamed back, the
    new build stays in its building folder, the dossiers row still points at the earlier pack."""
    env = env_with_dossier
    first = _build(env)
    before = (first.out_dir / "README.md").read_bytes()
    building = _aside(first.out_dir, "building")
    real_rename = Path.rename

    def rename(self, target):
        if self == building:
            raise PermissionError(13, "Access is denied")
        return real_rename(self, target)
    monkeypatch.setattr(Path, "rename", rename)
    with pytest.raises(PackError, match="the earlier pack is back in place") as e:
        _build(env)
    assert str(building) in str(e.value)
    assert (first.out_dir / "README.md").read_bytes() == before and first.zip_path.is_file()
    assert (building / "README.md").is_file() and not _aside(first.out_dir, "old").exists()
    assert env.store.dossier(STAGE2_TARGET)["pack_path"] == str(first.out_dir)
    monkeypatch.setattr(Path, "rename", real_rename)
    again = _build(env)                                                             # the retry goes through
    assert again.zip_path.is_file() and [p.name for p in again.out_dir.parent.iterdir()] == [again.out_dir.name]


@pytest.mark.skipif(sys.platform != "win32", reason="only Windows refuses to rename a folder with a file held open")
def test_an_open_file_in_the_earlier_pack_stops_the_swap_before_anything_moves(env_with_dossier):
    """The dashboard serving the zip holds it open: Windows then refuses to rename the earlier pack aside (step 1),
    so nothing moves, the error names the finished build, and the next run goes through."""
    env = env_with_dossier
    first = _build(env)
    before = first.zip_path.read_bytes()
    with first.zip_path.open("rb"):
        with pytest.raises(PackError, match="could not move the earlier pack") as e:
            _build(env)
        assert str(_aside(first.out_dir, "building")) in str(e.value)
    assert first.zip_path.read_bytes() == before and (first.out_dir / "README.md").is_file()
    assert (_aside(first.out_dir, "building") / "README.md").is_file()
    assert _build(env).zip_path.is_file()


def test_an_earlier_pack_that_cannot_be_deleted_is_left_behind_with_a_warning(env_with_dossier, monkeypatch):
    """Step 3 fails (a file of the earlier pack, set aside, is still held): the new pack is in place and recorded,
    the old folder stays as ``.<name>.old``, the log says so, and nothing is raised. The next run clears it."""
    env = env_with_dossier
    first = _build(env)
    old = _aside(first.out_dir, "old")
    real_rmtree = shutil.rmtree

    def rmtree(path, *a, **kw):
        if Path(path) == old:
            raise PermissionError(32, "The process cannot access the file because it is being used by another process")
        return real_rmtree(path, *a, **kw)
    monkeypatch.setattr(shutil, "rmtree", rmtree)
    lines = []
    rep = _build(env, log=lines.append)
    assert (rep.out_dir / "README.md").is_file() and rep.zip_path.is_file() and old.is_dir()
    assert env.store.dossier(STAGE2_TARGET)["pack_path"] == str(rep.out_dir)
    assert any(line.startswith("warning:") and str(old) in line for line in lines)
    monkeypatch.setattr(shutil, "rmtree", real_rmtree)
    assert _build(env).zip_path.is_file() and not old.exists()


def test_build_pack_on_the_real_kic_6146676_dossier(env_with_dossier):
    """The dossier the real-data run generated (tests/golden, Task 7): season 3 did not vote and its depths are
    null. The light curve is the fixture's; every number in the texts comes from the real dossier."""
    golden = json.loads((Path(__file__).parents[1] / "golden" / "kic-006146676-generated.json").read_text(
        encoding="utf-8"))
    env = env_with_dossier
    Path(env.store.dossier(STAGE2_TARGET)["data_path"]).write_text(json.dumps(golden), encoding="utf-8")
    rep = _build(env)
    note = (rep.out_dir / "exofop" / "note.txt").read_text(encoding="utf-8")
    assert "nearer this star than its brightest neighbour in 3 of 3 seasons that voted" in note
    assert "Season 3 did not vote (box incomplete: target)." in note
    tex = (rep.out_dir / "note" / "note.tex").read_text(encoding="utf-8")
    assert note_problems(rep.note_words, tex) == [] and rep.zip_path.is_file()
    import xml.etree.ElementTree as ET
    ET.fromstring((rep.out_dir / "figures" / "periodogram.svg").read_text(encoding="utf-8"))  # no plots block


# ---------------------------------------------------------------------------------------------------- Zenodo

def test_pack_with_zenodo_creates_a_draft_after_the_files_and_records_the_path(env_with_dossier, fake_session):
    env = env_with_dossier
    rep = _build(env, zenodo=ZenodoClient("t", session=fake_session))
    assert rep.zenodo["draft_url"].startswith("https://sandbox.zenodo.org/deposit/")
    assert env.store.dossier(STAGE2_TARGET)["pack_path"] == str(rep.out_dir)
    assert rep.zenodo == {"deposition_id": DEPOSITION_ID, "draft_url": f"https://sandbox.zenodo.org/deposit/{DEPOSITION_ID}",
                          "sandbox": True, "doi": f"10.5072/zenodo.{DEPOSITION_ID}"}
    methods = [(m, url.rsplit("/", 1)[-1]) for m, url, _b in fake_session.calls]
    assert methods == [("POST", "depositions"), ("PUT", str(DEPOSITION_ID)), ("PUT", _zip_name())]
    assert fake_session.calls[2][2] == rep.zip_path.read_bytes()
    assert fake_session.calls[1][2]["metadata"]["upload_type"] == "dataset"
    assert all("publish" not in url for _m, url, _b in fake_session.calls)
    tex = (rep.out_dir / "note" / "note.tex").read_text(encoding="utf-8")
    assert f"10.5072/zenodo.{DEPOSITION_ID}}}, a {SANDBOX_WORDS}" in tex          # a sandbox DOI never stands alone
    for name in ("exofop/note.txt", "archive_email.txt"):
        assert f"10.5072/zenodo.{DEPOSITION_ID}, a {SANDBOX_WORDS}" in (rep.out_dir / name).read_text(encoding="utf-8")
    readme = (rep.out_dir / "README.md").read_text(encoding="utf-8")
    assert "sandbox" in readme and str(DEPOSITION_ID) in readme
    assert f"2. Replace 10.5072/zenodo.{DEPOSITION_ID} (a sandbox DOI) before submission" in readme
    assert f"in place of 10.5072/zenodo.{DEPOSITION_ID} (a sandbox DOI)" in readme
    assert f"{SANDBOX_WORDS} with" not in readme                                     # no run-on sentence
    hashes = json.loads((rep.out_dir / "provenance" / "hashes.json").read_text(encoding="utf-8"))
    assert hashes["note/note.tex"]["sha256"] == hashlib.sha256(tex.encode("utf-8")).hexdigest()   # rehashed


def test_a_live_draft_waits_for_the_fpp_sign_off(env_with_dossier, fake_session, monkeypatch):
    from skysift import fpp
    env = env_with_dossier
    _approve(env)
    assert fpp.SIGNED_OFF is False
    live = ZenodoClient("t", sandbox=False, session=fake_session)
    with pytest.raises(PackError, match="provisional until spec 4.2's factor tables are signed off"):
        _build(env, zenodo=live)
    assert fake_session.calls == [] and env.store.dossier(STAGE2_TARGET)["pack_path"] == ""
    rep = _build(env, zenodo=ZenodoClient("t", session=fake_session))         # the sandbox stays allowed
    assert rep.zenodo is not None and fake_session.calls


def test_a_live_draft_needs_an_approved_candidate_and_writes_its_doi(env_with_dossier, fake_session, monkeypatch):
    from skysift import fpp
    monkeypatch.setattr(fpp, "SIGNED_OFF", True)                 # the gate after the sign-off
    env = env_with_dossier
    live = ZenodoClient("t", sandbox=False, session=fake_session)
    with pytest.raises(PackError, match="not yet approved"):
        _build(env, zenodo=live)
    assert fake_session.calls == [] and env.store.dossier(STAGE2_TARGET)["pack_path"] == ""
    _approve(env)
    rep = _build(env, zenodo=live)
    tex = (rep.out_dir / "note" / "note.tex").read_text(encoding="utf-8")
    assert f"10.5281/zenodo.{DEPOSITION_ID}" in tex and SANDBOX_WORDS not in tex and "XXXXXXX" not in tex


def test_a_refused_folder_leaves_no_draft(env_with_dossier, fake_session, tmp_path):
    foreign = tmp_path / "busy"
    foreign.mkdir()
    (foreign / "notes.txt").write_text("mine", encoding="utf-8")
    with pytest.raises(PackError):
        _build(env_with_dossier, out_dir=foreign, zenodo=ZenodoClient("t", session=fake_session))
    assert fake_session.calls == []


def test_a_failure_after_the_draft_names_the_draft(env_with_dossier):
    session = FakeSession(status=500, fail_on="/files/")              # the upload fails, the rest answers
    with pytest.raises(ZenodoError) as e:
        _build(env_with_dossier, zenodo=ZenodoClient("t", session=session))
    text = str(e.value)
    assert "Zenodo answered 500" in text and f"draft {DEPOSITION_ID}" in text and "discard it on Zenodo" in text
    assert env_with_dossier.store.dossier(STAGE2_TARGET)["pack_path"] in text


# ---------------------------------------------------------------------------------------------------- the command

@pytest.fixture
def cli_env(env_with_dossier, monkeypatch):
    monkeypatch.setattr(net, "fetch", env_with_dossier.fetch)
    env_with_dossier.session = FakeSession()
    monkeypatch.setattr(zenodo, "new_session", lambda: env_with_dossier.session)
    return env_with_dossier


def test_pack_without_a_token_does_not_touch_zenodo(cli_env, monkeypatch):
    monkeypatch.delenv("SKYSIFT_ZENODO_TOKEN", raising=False)
    r = runner.invoke(app, ["pack", "--target", STAGE2_TARGET, "--zenodo"])
    assert r.exit_code == 2 and "SKYSIFT_ZENODO_TOKEN is not set" in r.output and cli_env.session.calls == []
    assert "the pack was built without a Zenodo draft" in r.output
    assert cli_env.store.dossier(STAGE2_TARGET)["pack_path"]                         # the pack itself was built


def test_cli_pack_prints_json_and_records_the_path(cli_env, monkeypatch, tmp_path):
    monkeypatch.setenv("SKYSIFT_ZENODO_TOKEN", "a-secret")
    out = tmp_path / "pack"
    r = runner.invoke(app, ["pack", "--target", STAGE2_TARGET, "--out", str(out), "--tic", "169822650"])
    assert r.exit_code == 0, r.output
    report = json.loads(r.stdout)
    assert report["out_dir"] == str(out.resolve()) and report["zenodo"] is None and cli_env.session.calls == []
    assert report["note_words"]["total"] <= NOTE_LIMITS["total"]
    assert cli_env.store.dossier(STAGE2_TARGET)["pack_path"] == str(out.resolve()) and "a-secret" not in r.output


def test_cli_pack_refuses_zenodo_live_before_the_fpp_sign_off(cli_env, monkeypatch):
    monkeypatch.setenv("SKYSIFT_ZENODO_TOKEN", "a-secret")
    _approve(cli_env)
    r = runner.invoke(app, ["pack", "--target", STAGE2_TARGET, "--zenodo-live"])
    assert r.exit_code == 2 and "--zenodo-live is refused while the false-positive estimate is provisional" in r.output
    assert cli_env.session.calls == [] and cli_env.store.dossier(STAGE2_TARGET)["pack_path"] == ""
    monkeypatch.delenv("SKYSIFT_ZENODO_TOKEN")
    assert runner.invoke(app, ["pack", "--target", STAGE2_TARGET, "--zenodo-live"]).exit_code == 2   # token or not


def test_cli_pack_with_a_token_makes_a_draft_and_prints_its_url(cli_env, monkeypatch):
    from skysift import fpp
    monkeypatch.setattr(fpp, "SIGNED_OFF", True)
    monkeypatch.setenv("SKYSIFT_ZENODO_TOKEN", "a-secret")
    r = runner.invoke(app, ["pack", "--target", STAGE2_TARGET, "--zenodo"])
    assert r.exit_code == 0, r.output
    assert f"https://sandbox.zenodo.org/deposit/{DEPOSITION_ID}" in r.stderr and "not published" in r.stderr
    assert cli_env.session.headers["Authorization"] == "Bearer a-secret" and "a-secret" not in r.output
    assert [m for m, _u, _b in cli_env.session.calls] == ["POST", "PUT", "PUT"]
    refused = runner.invoke(app, ["pack", "--target", STAGE2_TARGET, "--zenodo-live"])
    assert refused.exit_code == 2 and "not yet approved" in refused.output and len(cli_env.session.calls) == 3
    _approve(cli_env)
    live = runner.invoke(app, ["pack", "--target", STAGE2_TARGET, "--zenodo-live"])
    assert live.exit_code == 0, live.output
    assert cli_env.session.calls[3][1] == "https://zenodo.org/api/deposit/depositions"
    assert json.loads(live.stdout)["zenodo"]["sandbox"] is False


def test_cli_pack_reports_a_zenodo_error_with_the_right_path(cli_env, monkeypatch, tmp_path):
    monkeypatch.setenv("SKYSIFT_ZENODO_TOKEN", "a-secret")
    runner.invoke(app, ["pack", "--target", STAGE2_TARGET, "--out", str(tmp_path / "earlier")])    # an older pack
    cli_env.session.status = 500
    r = runner.invoke(app, ["pack", "--target", STAGE2_TARGET, "--zenodo"])
    assert r.exit_code == 1 and "Zenodo answered 500" in r.output and "Traceback" not in r.output
    assert "built without a Zenodo draft" in r.output and "a-secret" not in r.output
    row = cli_env.store.dossier(STAGE2_TARGET)
    assert row["pack_path"] in r.output.replace("\n", "") and "earlier" not in r.output


def test_cli_pack_usage_errors_and_other_errors(cli_env):
    r = runner.invoke(app, ["pack", "--target", STAGE2_TARGET, "--tic", "12;x"])
    assert r.exit_code == 2 and "a TIC id is digits" in r.output and "Traceback" not in r.output
    data_path = cli_env.store.dossier(STAGE2_TARGET)["data_path"]
    with open(data_path, "w", encoding="utf-8") as fh:
        fh.write("{not json")
    r = runner.invoke(app, ["pack", "--target", STAGE2_TARGET])
    assert r.exit_code == 1 and isinstance(r.exception, json.JSONDecodeError)    # not dressed up as a usage error
    assert "Invalid value" not in r.output


def test_cli_pack_exits_2_when_the_light_curve_was_released(cli_env):
    cli_env.store.mark_artifact_deleted(cli_env.raw_sha)
    r = runner.invoke(app, ["pack", "--target", STAGE2_TARGET])
    assert r.exit_code == 2 and "--retention keep" in r.output and "Traceback" not in r.output


def test_cli_pack_without_a_dossier_exits_1(cli_env):
    r = runner.invoke(app, ["pack", "--target", "kic-000000001"])
    assert r.exit_code == 1 and "skysift dossier --target kic-000000001" in r.output and "Traceback" not in r.output
