"""The submission pack (dossiers plan Task 6): the folder and zip ``build_pack`` writes from a generated dossier, the
README's five steps, the AASTeX v7 Research Note draft and its word count against the RNAAS limits, the ExoFOP files
and upload note, the provenance, the offline rule, and the ``skysift pack`` command with and without a Zenodo token.
The dossier comes from ``dossier.generate`` on the dashboard seed with a synthetic light curve and a fake Gaia fetch;
Zenodo is a fake session, so nothing reaches the network."""
import csv
import hashlib
import json
import math
import re
import shutil
import sys
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest
from typer.testing import CliRunner

from skysift import crosscheck as cross_check
from skysift import dossier as dossier_module
from skysift import ephemeris, exofop, net, pack_rows, pack_text, zenodo
from skysift import pack as pack_module
from skysift.cli import app
from skysift.dossier import LightCurveReleased, generate, whitened
from skysift.lc.transit_model import UNCONSTRAINED
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
from tests.unit.test_dossier import (
    DEVELOPMENT_NOTE,
    DEVELOPMENT_NOTE_SHORT,
    PRIOR_CLAIM,
    _make_env,
    _pixels_npz,
    _store_pixels,
)
from tests.unit.test_zenodo import DEPOSITION_ID, FakeSession
from tests.unit.ui_seed import STAGE2_TARGET

runner = CliRunner()
EM_DASH = chr(0x2014)
WARNING = ("This candidate is not yet approved by a reviewer. Build the record, but do not freeze or upload it before "
           "the approval.")
IZE = re.compile(r"\b(organiz|characteriz|summariz|localiz|recogniz|normaliz|minimiz|optimiz)(e|ed|es|ing|ation)\b",
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
    assert rows["planet radius"]["value"] == 2.45 and rows["planet radius"]["minus"] == 0.08
    assert rows["planet radius"]["unit"] == "Earth radii" and rows["planet radius"]["source"]
    with (rep.out_dir / "data" / "ephemeris.csv").open(encoding="utf-8", newline="") as fh:
        table = list(csv.DictReader(fh))
    assert [r["parameter"] for r in table] == [r["parameter"] for r in eph["rows"]]
    with (rep.out_dir / "data" / "events.csv").open(encoding="utf-8", newline="") as fh:
        events = list(csv.DictReader(fh))
    # one row per epoch of the timing block, as transit_times.csv (spec 11.3)
    assert len(events) == len(env.data["timing"]["times"]) and set(events[0]) >= {"epoch", "t_mid_bkjd", "depth_ppm"}
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
    assert PACK_VERSION == "5"
    assert versions["pack_version"] == PACK_VERSION and versions["search_version"] == env.data["signal"][
        "detector_version"]
    assert versions["ladder_version"] == env.data["provenance"]["ladder_version"] and versions["git_commit"]
    # review M-5: the source checkout's docs/author.json gives the author, CITATION.cff what it leaves empty
    assert versions["creators_source"] == "docs/author.json (with CITATION.cff in the source checkout)"
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
    same = tmp_path / "same-day"                    # a fixed folder: the default one is named by the UTC date
    first = _build(env_with_dossier, out_dir=same)
    (first.out_dir / "stray.txt").write_text("left over", encoding="utf-8")
    second = _build(env_with_dossier, out_dir=same)
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
    assert "discover" not in text.lower() and not IZE.search(text)
    for account in ("Zenodo account", "ExoFOP", "AAS journals account", "arXiv", "help desk"):
        assert account in text
    assert "Gaia DR3 2101234567890123462" in text and "KIC id is accepted directly" in text
    assert "\n\nData Tag: " in text and f"{_today()}_<username>_skysift-{STAGE2_TARGET}_00001" in text
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
    in_house = cross_check.probability_text(data["fpp"]["value"])
    assert "1 in 440" in abstract and f"gives {in_house}, a lower bound" in abstract and "provisional" not in abstract
    assert re.search(r"the dip is nearer this star than its brightest neighbour in \d of \d "
                     r"(observing seasons|seasons that voted)\.", abstract)
    assert "lost light" not in tex and "on this star in" not in tex
    assert "10.5281/zenodo.XXXXXXX" in tex and r"\includegraphics" in tex
    cited = set(re.findall(r"\\cite[pt]?\{([^}]+)\}", tex))
    keys = {k.strip() for c in cited for k in c.split(",")}
    items = set(re.findall(r"\\bibitem\[[^\]]*\]\{([^}]+)\}", tex))
    assert keys and keys == items                                              # every citation listed, none unused
    assert {"mann2015", "mann2019", "gaia2016", "gaia2023", "borucki2010", "kbonus", "kbonushlsp"} <= items
    # Task 13: the in-house method, the colour table, the parallax zero point and the habitable zone are the README's
    assert not {"morton2012", "dressing2015", "fressin2013", "pecaut2013", "lindegren2021", "kopparapu2013"} & items
    assert "AJ, 166, 265, doi:10.3847/1538-3881/ad0727" in tex and "doi:10.17909/7jbr-w430" in tex
    assert "40.290" in tex and "1 in 440" in tex and "provisional" not in tex
    assert r"2.45 $\pm$ 0.08 $R_\oplus$" in tex                                # Rp from the limb-darkened k
    assert "3,653$^{+90}_{-73}$ K" in tex                                      # thousands as the dossier prints


def test_the_note_names_the_radius_ratio_the_planet_radius_came_from(env_with_dossier):
    """Task 10 and its fix round 1 (I-3, M-9): one radius ratio per text. The planet radius is k R* with the quoted
    centre (here, without resamples, the quoted fit's full-data k), the note names that k and not rung 2's
    uniform-disc one, and T14 comes from the same fit, so the ephemeris duration and the shape row agree."""
    data = env_with_dossier.data
    fit = data["shape"]["fits"][data["shape"]["comparison"]["quoted"]]
    k = fit["k"]
    assert dossier_module.quoted_radius_ratio(data["shape"]) == pytest.approx(k)
    assert pack_module.fit_radius_ratio(data) == pytest.approx(k)
    assert f"k {k:.4f} from the transit fit" in data["planet"]["rp_rearth"]["source"]
    t14_h = fit["t14_days"] * 24.0
    assert data["ephemeris"]["duration_h"] == pytest.approx(t14_h)
    rows = {r["key"]: r for r in pack_rows.shape_rows(data)}
    assert rows["t14_h"]["value"] == pytest.approx(t14_h) and rows["k"]["value"] == pytest.approx(k)
    flat = " ".join(render_note(data).split())
    rp = pack_text.value_text(data["planet"]["rp_rearth"], True)
    assert f"With the fitted radius ratio of {k:.4f} the planet radius is {rp} $R_\\oplus$" in flat
    legacy = json.loads(json.dumps(data))
    del legacy["shape"]["fits"], legacy["shape"]["comparison"]
    rung2 = next(t["metrics"] for t in legacy["tests"] if t["rung"] == "rung2_shape")
    assert f"With the fitted radius ratio of {rung2['fit_k']:.4f} the" in " ".join(render_note(legacy).split())


def test_note_for_a_star_without_parameters_or_pixels(env_with_dossier):
    data = json.loads(json.dumps(env_with_dossier.data))
    data["star"] = {**data["star"], "route": "none", "teff": None, "radius": None, "mass": None,
                    "distance_pc": None, "spectral_type": None}
    data["planet"] = {k: None for k in data["planet"]}
    data["tests"] = [t for t in data["tests"] if t["rung"] != "rung5_pixels"]
    data["pixels"] = None
    data["fpp"]["scenarios"][0]["factors"]["class"] = "FGK"
    tex = render_note(data, doi_placeholder="10.5281/zenodo.123")
    assert "mann2015" not in tex and "kopparapu2013" not in tex and "fressin2013" not in tex    # Task 13: README
    assert "fressin2013" in pack_module.readme_works(data)
    assert "pixel" in tex and "has not run" in tex and "10.5281/zenodo.123" in tex
    assert tex.count("{") == tex.count("}") and note_problems(note_words(tex), tex) == []
    assert "No Kepler stellar catalogue" not in tex                     # no star parameters, no provenance sentence
    # a failed fit: no rung 2 geometry, no timing fit, no bootstrap; every number is printed alone with its reason
    next(t for t in data["tests"] if t["rung"] == "rung2_shape")["metrics"]["fit_ok"] = False
    data["timing"] = {**data["timing"], "ok": False, "reason": "fewer than 3 transits timed"}
    data["shape"] = {**data["shape"], "ok": False, "reason": "the bootstrap failed", "k": None, "b": None,
                     "a_over_r": None, "t14_h": None, "model_depth_ppm": None}
    data["ephemeris"].update(period_err_days=None, t0_bkjd_err=None, t0_bjd_err=None, t_ref_bkjd=None,
                             t_ref_bjd=None, t_ref_err_days=None, epoch_ref=None, duration_h_err=None,
                             duration_source="search box", period_source="search")
    tex = render_note(data)
    assert "(no error; fewer than 3 transits timed)" in tex and "(no error; the bootstrap failed)" in tex
    assert "uniform-disc" not in tex and "radius ratio" not in tex and "rms of" not in tex
    assert tex.count("{") == tex.count("}") and note_problems(note_words(tex), tex) == []


def test_the_star_sentence_leaves_out_a_missing_mass_or_temperature(env_with_dossier):
    data = json.loads(json.dumps(env_with_dossier.data))
    data["star"] = {**data["star"], "teff": None, "mass": None, "spectral_type": None}
    tex = render_note(data)
    assert "a mass of" not in tex and "effective temperature of" not in tex and "a radius of 0.503" in tex
    items = set(re.findall(r"\\bibitem\[[^\]]*\]\{([^}]+)\}", tex))
    keys = {k.strip() for c in re.findall(r"\\cite[pt]?\{([^}]+)\}", tex) for k in c.split(",")}
    assert keys == items and "mann2019" not in items and "pecaut2013" not in items


def test_note_escapes_latex_characters(env_with_dossier):
    data = json.loads(json.dumps(env_with_dossier.data))
    data["target"]["name"] = "KIC 6146676 & 50% of_it"
    tex = render_note(data)
    assert r"KIC 6146676 \& 50\% of\_it" in tex


def test_every_pack_text_gives_both_probabilities_without_the_provisional_mark(env_with_dossier):
    rep = _build(env_with_dossier)
    fap = env_with_dossier.data["fap"]["words"]
    fpp = f"gives {cross_check.probability_text(env_with_dossier.data['fpp']['value'])}, a lower bound"
    for name in ("README.md", "note/note.tex", "exofop/note.txt", "archive_email.txt"):
        text = (rep.out_dir / name).read_text(encoding="utf-8")
        assert fap in text and fpp in text and "provisional" not in text, name       # signed off 21 October 2026
        assert EM_DASH not in text and "discover" not in text.lower() and not IZE.search(text), name


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


def _with_a_cut_box(data: dict) -> dict:
    """The silent-season fixture with season 0's target box cut by the stamp edge (6 of 9 pixels)."""
    data = _with_a_silent_season(data)
    row = next(t for t in data["tests"] if t["rung"] == "rung5_pixels")
    row["metrics"]["seasons"][0].update({"box_partial": True, "box_pixels_target": 6, "box_pixels_neighbour": 9,
                                         "edge_bias_pix": 0.1})
    return data


def test_pixel_sentence_names_a_box_cut_by_the_stamp_edge(env_with_dossier):
    data = _with_a_cut_box(env_with_dossier.data)
    cut = dossier_module.partial_box_sentence(
        next(t for t in data["tests"] if t["rung"] == "rung5_pixels")["metrics"]["seasons"])
    text = pack_module.pixel_sentence(data)
    assert "In season 0 this star's 3 x 3 box is cut by the stamp edge" in text and "(0.10 px at most)" in text
    assert text.index("pixels from the neighbour.") < text.index(cut) < text.index("Against the Gaia position")
    assert EM_DASH not in text


def test_pixel_sentence_names_the_floor_when_the_row_stores_it(env_with_dossier):
    """A rung 5 row of rule version 3: the centroid sentence says in one clause that its error carries the measured
    pointing-jitter floor, and the Research Note keeps within its limits."""
    data = _with_a_silent_season(env_with_dossier.data)
    for season in next(t for t in data["tests"] if t["rung"] == "rung5_pixels")["metrics"]["seasons"]:
        season["floor_pix"] = 0.0164
    text = pack_module.pixel_sentence(data)
    assert ("The centroid sits a median 0.15 pixels from this star and 2.80 pixels from the neighbour, its error "
            "carrying a pointing-jitter floor of 0.016 px.") in text
    assert EM_DASH not in text
    tex = render_note(data)
    assert "pointing-jitter floor of 0.016 px" in tex and note_problems(note_words(tex), tex) == []


def test_the_abstract_carries_only_a_short_clause_for_a_cut_box(env_with_dossier):
    abstract = pack_module.abstract_text(_with_a_cut_box(env_with_dossier.data))
    assert "in 2 of 2 seasons that voted, 1 of them with a box cut by the stamp edge." in abstract
    assert "3 x 3 box" not in abstract and "px at most" not in abstract
    plain = pack_module.abstract_text(_with_a_silent_season(env_with_dossier.data))
    assert "box cut by the stamp edge" not in plain


def test_pixel_sentence_without_box_keys_is_unchanged(env_with_dossier):
    """The text of the commit before the box keys existed (9b4f076), captured before the change; since spec 11.5 the
    Gaia-referenced offset carries the exclusion radius and the nearest Gaia source beyond it."""
    assert pack_module.pixel_sentence(env_with_dossier.data) == (
        "In difference images (out of transit minus in transit) the dip is nearer this star than its brightest "
        "neighbour in 3 of 3 seasons that voted. The centroid sits a median 0.13 pixels from this star and 2.75 "
        "pixels from the neighbour. Against the Gaia position projected through each stamp's WCS, a reference that "
        "carries the WCS zero point and the registration, the difference-image centroid sits 0.54 ± 0.14 arcsec "
        "away, with an exclusion radius of 0.50 arcsec (max(3 sigma, 0.5 arcsec)) within which a background source "
        "is not excluded; the nearest Gaia source beyond it is Gaia DR3 2101234567890120001 at 6.6 arcsec from the "
        "centroid (G = 19.8). A two-source fit gives a median depth of 2,213 ppm on this star and -21 ppm on the "
        "neighbour. Season 3 did not vote (box incomplete: target).")


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
    out = env.settings.data_dir / "packs" / "swap"      # a fixed folder: the default one is named by the UTC date
    first = _build(env, out_dir=out)
    before = (first.out_dir / "README.md").read_bytes()
    building = _aside(first.out_dir, "building")
    real_rename = Path.rename

    def rename(self, target):
        if self == building:
            raise PermissionError(13, "Access is denied")
        return real_rename(self, target)
    monkeypatch.setattr(Path, "rename", rename)
    with pytest.raises(PackError, match="the earlier pack is back in place") as e:
        _build(env, out_dir=out)
    assert str(building) in str(e.value)
    assert (first.out_dir / "README.md").read_bytes() == before and first.zip_path.is_file()
    assert (building / "README.md").is_file() and not _aside(first.out_dir, "old").exists()
    assert env.store.dossier(STAGE2_TARGET)["pack_path"] == str(first.out_dir)
    monkeypatch.setattr(Path, "rename", real_rename)
    again = _build(env, out_dir=out)                                                # the retry goes through
    assert again.zip_path.is_file() and [p.name for p in again.out_dir.parent.iterdir()] == [again.out_dir.name]


@pytest.mark.skipif(sys.platform != "win32", reason="only Windows refuses to rename a folder with a file held open")
def test_an_open_file_in_the_earlier_pack_stops_the_swap_before_anything_moves(env_with_dossier):
    """The dashboard serving the zip holds it open: Windows then refuses to rename the earlier pack aside (step 1),
    so nothing moves, the error names the finished build, and the next run goes through."""
    env = env_with_dossier
    out = env.settings.data_dir / "packs" / "held"        # a fixed folder, not the UTC-date one
    first = _build(env, out_dir=out)
    before = first.zip_path.read_bytes()
    with first.zip_path.open("rb"):
        with pytest.raises(PackError, match="could not move the earlier pack") as e:
            _build(env, out_dir=out)
        assert str(_aside(first.out_dir, "building")) in str(e.value)
    assert first.zip_path.read_bytes() == before and (first.out_dir / "README.md").is_file()
    assert (_aside(first.out_dir, "building") / "README.md").is_file()
    assert _build(env, out_dir=out).zip_path.is_file()


def test_an_earlier_pack_that_cannot_be_deleted_is_left_behind_with_a_warning(env_with_dossier, monkeypatch):
    """Step 3 fails (a file of the earlier pack, set aside, is still held): the new pack is in place and recorded,
    the old folder stays as ``.<name>.old``, the log says so, and nothing is raised. The next run clears it."""
    env = env_with_dossier
    out = env.settings.data_dir / "packs" / "left"        # a fixed folder, not the UTC-date one
    first = _build(env, out_dir=out)
    old = _aside(first.out_dir, "old")
    real_rmtree = shutil.rmtree

    def rmtree(path, *a, **kw):
        if Path(path) == old:
            raise PermissionError(32, "The process cannot access the file because it is being used by another process")
        return real_rmtree(path, *a, **kw)
    monkeypatch.setattr(shutil, "rmtree", rmtree)
    lines = []
    rep = _build(env, out_dir=out, log=lines.append)
    assert (rep.out_dir / "README.md").is_file() and rep.zip_path.is_file() and old.is_dir()
    assert env.store.dossier(STAGE2_TARGET)["pack_path"] == str(rep.out_dir)
    assert any(line.startswith("warning:") and str(old) in line for line in lines)
    monkeypatch.setattr(shutil, "rmtree", real_rmtree)
    assert _build(env, out_dir=out).zip_path.is_file() and not old.exists()


def test_build_pack_on_the_real_kic_6146676_dossier(env_with_dossier):
    """The dossier the live run regenerated at dossier version 3 (tests/golden): all four seasons vote, seasons 0 and
    3 have a 3 x 3 box cut by the stamp edge, and the centroid error carries the measured floor. The light curve is
    the fixture's; every number in the texts comes from the real dossier."""
    golden = json.loads((Path(__file__).parents[1] / "golden" / "kic-006146676-generated.json").read_text(
        encoding="utf-8"))
    env = env_with_dossier
    Path(env.store.dossier(STAGE2_TARGET)["data_path"]).write_text(json.dumps(golden), encoding="utf-8")
    rep = _build(env)
    note = (rep.out_dir / "exofop" / "note.txt").read_text(encoding="utf-8")
    assert "the dip is nearer this star than its brightest neighbour in 4 of 4 observing seasons." in note
    assert "seasons that voted" not in note and "did not vote" not in note        # season 3 votes now
    assert ("The centroid sits a median 0.19 pixels from this star and 2.82 pixels from the neighbour, its error "
            "carrying a pointing-jitter floor of 0.017 px.") in note
    assert ("In seasons 0 and 3 this star's 3 x 3 box is cut by the stamp edge; the centroid there is measured on "
            "the pixels present and the shift the cut would give a point source (0.08 px at most) is added to its "
            "error.") in note
    # the offset of ruling e: the version 4 golden (a56d8c8) stores the block with the star's own direct-image
    # centroid, so the offset is judged by Bryson's rule and cited (the fallback form, against the fit's position,
    # is tested on a synthetic block in test_the_offset_sentence_names_its_reference); the ExoFOP note keeps the long
    # form, the Research Note the short one (fix round 4)
    # spec 11.5: the Gaia-referenced offset, the exclusion radius and the nearest source beyond it lead; the direct
    # centroid sits 1.30 px from the Gaia position, so the self-referenced test says its reference is pulled
    assert ("Against the Gaia position projected through each stamp's WCS, a reference that carries the WCS zero "
            "point and the registration, the difference-image centroid sits 0.25 ± 0.28 arcsec away, with an "
            "exclusion radius of 0.83 arcsec (max(3 sigma, 0.5 arcsec)) within which a background source is not "
            "excluded; the nearest Gaia source beyond it is Gaia DR3 2073896294004902144 at 6.9 arcsec from the "
            "centroid (G = 19.8). Against the star's own out-of-transit image the difference-image centroid is offset "
            "by 1.8 ± 1.0 arcsec (1.8 sigma), not an offset by the rule of Bryson et al. (2013), which asks for more "
            "than 3 sigma and more than 0.2 arcsec; its reference is pulled by the neighbour, up to 1.30 px from the "
            "Gaia position. Both errors are the larger of the seasons' combined error and their scatter.") in note
    assert "not stored for every season" not in note
    tex = (rep.out_dir / "note" / "note.tex").read_text(encoding="utf-8")
    assert (r"Against the Gaia position the difference-image centroid sits 0.25 $\pm$ 0.28 arcsec away, with an "
            r"exclusion radius of 0.83 arcsec (max(3 sigma, 0.5 arcsec)) within which a background source is not "
            r"excluded; the nearest Gaia source beyond it lies at 6.9 arcsec (G = 19.8); against the star's own "
            r"out-of-transit image the offset is 1.8 $\pm$ 1.0 arcsec (1.8 sigma), not an offset by the rule of "
            r"\citet{bryson2013} (over 3 sigma and 0.2 arcsec), its reference pulled by the neighbour up to 1.30 "
            r"px.") in (
        " ".join(tex.split()))
    assert r"]{bryson2013} Bryson" in tex
    assert "hand-built" not in note                     # spec 11.5: the README keeps it, with the file
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
    monkeypatch.setattr(fpp, "SIGNED_OFF", False)                # the gate before the sign-off
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
    _store_errors(env)                                           # the live gate needs the shape errors
    _store_cross_check(env)                                      # and a current cross-check
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
    from skysift import fpp
    monkeypatch.setattr(fpp, "SIGNED_OFF", False)
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
    _store_errors(cli_env)
    _store_cross_check(cli_env)
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


def test_a_live_draft_refuses_a_stale_dossier(env_with_dossier, fake_session, monkeypatch):
    # a dossier written under an older false-positive model is stale through the fingerprint; the live gate refuses
    env = env_with_dossier
    _approve(env)
    monkeypatch.setattr(dossier_module, "FPP_MODEL_VERSION", "9")
    live = ZenodoClient("t", sandbox=False, session=fake_session)
    with pytest.raises(PackError, match="is stale"):
        _build(env, zenodo=live)
    assert fake_session.calls == [] and env.store.dossier(STAGE2_TARGET)["pack_path"] == ""
    rep = _build(env, zenodo=ZenodoClient("t", session=fake_session))         # the sandbox still builds, and says so
    assert rep.zenodo is not None and "stale" in (rep.out_dir / "README.md").read_text(encoding="utf-8").lower()


def test_a_live_draft_refuses_a_dossier_stored_before_the_sign_off(env_with_dossier, fake_session):
    env = env_with_dossier
    _approve(env)
    path = Path(env.store.dossier(STAGE2_TARGET)["data_path"])
    data = json.loads(path.read_text(encoding="utf-8"))
    data["fpp"]["signed_off"] = False
    path.write_text(json.dumps(data), encoding="utf-8")
    live = ZenodoClient("t", sandbox=False, session=fake_session)
    with pytest.raises(PackError, match="written before the false-positive sign-off"):
        _build(env, zenodo=live)
    assert fake_session.calls == []


# ---------------------------------------------------------------------------------------------------- the referee pack

BKJD = 2454833.0
PLANET_COLUMNS = ["target", "flag", "disp", "discovery", "candname", "period", "period_unc", "epoch", "epoch_unc",
                  "depth", "depth_unc", "duration", "duration_unc", "inc", "inc_unc", "imp", "imp_unc", "r_planet",
                  "r_planet_err", "ar_star", "ar_star_err", "radius", "radius_unc", "mass", "mass_unc", "temp",
                  "temp_unc", "insol", "insol_unc", "dens", "dens_unc", "sma", "sma_unc", "ecc", "ecc_unc",
                  "arg_peri", "arg_peri_err", "time_peri", "time_peri_unc", "vsa", "vsa_unc", "tag", "group",
                  "prop_period", "paper", "notes"]
STELLAR_COLUMNS = ["target", "tel", "telsize", "inst", "sp_type", "teff", "teff_unc", "logg", "logg_unc", "radius",
                   "radius_unc", "logrhk", "logrhk_unc", "sindex", "sindex_unc", "halpha", "halpha_unc", "vsini",
                   "vsini_unc", "rotper", "rotper_unc", "met", "met_unc", "mass", "mass_unc", "dens", "dens_unc",
                   "lum", "lum_unc", "obs_time", "rv", "rv_unc", "dist", "dist_unc", "age", "age_unc", "snr", "fit",
                   "tag", "group", "prop_period", "notes"]
DEGENERATE = "ingress shorter than the cadence: b and a/R* are degenerate with k, errors not quoted"
def _shape_fit(variant: str, k: float, b: float, a: float, chi2: float, prior: float) -> dict:
    """A stored ``ShapeFit.as_dict()`` of the fixture's geometry (40.29 d, 4.16 h)."""
    return {"ok": True, "reason": None, "variant": variant, "k": k, "b": b, "a_over_r": a, "t14_days": 4.16 / 24,
            "depth_ppm": 2660.0, "chi2_data": chi2, "chi2_prior": prior, "chi2_total": chi2 + prior,
            "n_points": 1650, "u1": 0.4415, "u2": 0.2740, "exposure_min": 29.4,
            "density_gcc": 3 * np.pi * a ** 3 / (6.674e-8 * (40.2899 * 86400) ** 2),
            "prior_gcc": None if variant == "free" else [7.29, 0.62]}


SHAPE_OK = {"ok": True, "reason": None,
            "method": ("bootstrap over transits of the quadratic limb-darkened, exposure-integrated fit (Schmitt et "
                       "al. 2014)"), "n_boot": 200,
            "n_converged": 196, "seed": 20261022,
            "k": {"median": 0.047, "minus": 0.0011, "plus": 0.0012, "point": 0.0469},
            "b": {"median": 0.31, "minus": None, "plus": None, "point": 0.33},
            "a_over_r": {"median": 74.0, "minus": None, "plus": None, "point": 73.0},
            "t14_h": {"median": 4.16, "minus": 0.05, "plus": 0.06, "point": 4.15},
            "model_depth_ppm": {"median": 2209.0, "minus": 100.0, "plus": 110.0, "point": 2200.0},
            "box_depth_ppm": {"median": 2166.4, "minus": 52.4, "plus": 52.4}, "box_duration_h": None,
            "ingress_min": 12.3, "degenerate": True, "degeneracy_reason": DEGENERATE,
            "fits": {"free": _shape_fit("free", 0.0468, 0.42, 68.0, 1640.2, 0.0),
                     "prior": _shape_fit("prior", 0.0469, 0.33, 73.0, 1640.4, 0.3)},
            "comparison": {"constrained": False, "quoted": "prior", "delta_chi2": 0.5, "density_tension_sigma": None,
                           "a_over_r_label": "from the stellar parameters", "reason": UNCONSTRAINED},
            "limb_darkening": {"law": "quadratic", "u1": 0.4415, "u2": 0.2740, "teff": 3855.0, "logg": 4.5,
                               "source": "Claret and Bloemen 2011, Kepler band, quadratic law"},
            "exposure_min": 29.4}
NO_BOOT = "0 of 0 resamples converged (fewer than 20)"
GOLDEN = Path(__file__).parents[1] / "golden" / "kic-006146676-generated.json"


def _with_errors(data: dict) -> dict:
    """The fixture's dossier (whose bootstrap has no resamples) with a shape block that has errors, as a dossier
    generated with 200 resamples has, and the fitted T14 with its error in the ephemeris."""
    data = json.loads(json.dumps(data))
    data["shape"] = json.loads(json.dumps(SHAPE_OK))
    data["ephemeris"].update(duration_h=4.16, duration_h_err={"minus": 0.05, "plus": 0.06})
    data["caveats"] = [c for c in data["caveats"] if c.get("key") != "no_shape_error"]
    return data


def _store(env, data: dict) -> None:
    Path(env.store.dossier(STAGE2_TARGET)["data_path"]).write_text(json.dumps(data), encoding="utf-8")


def _stored(env) -> dict:
    return json.loads(Path(env.store.dossier(STAGE2_TARGET)["data_path"]).read_text(encoding="utf-8"))


def _store_errors(env) -> None:
    _store(env, _with_errors(_stored(env)))


def _store_cross_check(env, **changes) -> dict:
    """A cross-check made for the stored dossier, as ``skysift crosscheck --import`` stores it."""
    from tests.unit.test_crosscheck import fake_result
    block = {**fake_result(cross_check.inputs_fingerprint(_stored(env))), "current": True, **changes}
    env.store.set_cross_check(STAGE2_TARGET, block)
    return block


def _stored_with_cross_check(env) -> dict:
    """The stored dossier with the stored cross-check merged in, as ``build_pack`` reads it."""
    data = _stored(env)
    return {**data, "cross_check": cross_check.with_current(env.store.dossier(STAGE2_TARGET)["cross_check"], data)}


def _unquoted(readme: str) -> str:
    """The README without its quoted archive email, a text of its own that names a missing error itself."""
    return "\n".join(line for line in readme.splitlines() if not line.startswith("> "))


def _rows(rep, name: str) -> list[dict]:
    with (rep.out_dir / name).open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def _header(rep, name: str) -> list[str]:
    with (rep.out_dir / name).open(encoding="utf-8", newline="") as fh:
        return next(csv.reader(fh))


def test_ephemeris_csv_carries_the_errors_and_the_dilution_rows(env_with_dossier):
    env = env_with_dossier
    rep = _build(env)
    eph = env.data["ephemeris"]
    assert _header(rep, "data/ephemeris.csv") == ["parameter", "value", "minus", "plus", "unit", "source"]
    by = {(r["parameter"], r["unit"]): r for r in _rows(rep, "data/ephemeris.csv")}
    period = by[("period", "d")]
    assert float(period["value"]) == eph["period_days"]
    assert float(period["minus"]) == float(period["plus"]) == eph["period_err_days"]
    assert "linear fit of 34 transit times" in period["source"] and "Deeg 2015" in period["source"]
    t0 = by[("mid-transit time", "BJD_TDB")]
    assert float(t0["value"]) == eph["t0_bjd"]               # the stored BJD at six decimals, not BKJD at 8 figures
    assert float(t0["minus"]) == eph["t0_bjd_err"]
    ref = by[("reference mid-transit time", "BJD_TDB")]
    assert float(ref["value"]) == eph["t_ref_bjd"]
    assert float(ref["plus"]) == eph["t_ref_err_days"] and "epoch 18" in ref["source"]
    depth = by[("depth", "ppm")]
    assert float(depth["value"]) == 2166.5579 and float(depth["minus"]) == 52.345237
    assert depth["source"] == "per-event weighted mean of the box depth"
    duration = by[("duration", "h")]
    assert duration["minus"] == duration["plus"] == "" and "fitted T14" in duration["source"]
    crowdsap, psffrac = by[("target light fraction (CROWDSAP)", "")], by[("PSF flux fraction (PSFFRAC)", "")]
    assert float(crowdsap["value"]) == 0.9 and float(psffrac["value"]) == 0.9
    assert crowdsap["source"] == f"KBonus header keyword CROWDSAP: {pack_rows.CROWDSAP_DEFINITION}"
    assert "1 means no contamination" in crowdsap["source"] and "inside the KBonus aperture" in psffrac["source"]
    leak = by[("dip in the neighbour's aperture (leak test)", "ppm")]
    assert float(leak["value"]) == 14.0 and float(leak["minus"]) == float(leak["plus"]) == 31.0
    assert "rung 5" in leak["source"]
    rows = json.loads((rep.out_dir / "data" / "ephemeris.json").read_text(encoding="utf-8"))["rows"]
    assert [r["parameter"] for r in rows] == [r["parameter"] for r in _rows(rep, "data/ephemeris.csv")]
    # a dossier stored before the BJD decimals were kept: the pack's JSON copy takes BJD from BKJD
    old = _stored(env)
    old["ephemeris"].update(t0_bjd=2454969.3, t_ref_bjd=2455694.5)
    _store(env, old)
    copy = json.loads((_build(env).out_dir / "data" / "ephemeris.json").read_text(encoding="utf-8"))["ephemeris"]
    assert abs(copy["t0_bjd"] - (eph["t0_bkjd"] + BKJD)) < 1e-5 and copy["t0_bjd_err"] == eph["t0_bjd_err"]
    assert abs(copy["t_ref_bjd"] - (eph["t_ref_bkjd"] + BKJD)) < 1e-5


def test_transit_times_and_shape_csvs(env_with_dossier):
    env = env_with_dossier
    rep = _build(env)
    assert _header(rep, "data/transit_times.csv") == ["epoch", "t_bkjd", "t_bjd", "err_days", "chi2_red", "used",
                                                      "reason"]
    times, stored = _rows(rep, "data/transit_times.csv"), env.data["timing"]["times"]
    assert len(times) == len(stored) and int(times[0]["epoch"]) == stored[0]["epoch"]
    assert float(times[0]["t_bjd"]) == pytest.approx(stored[0]["t_bkjd"] + BKJD, abs=1e-6)
    assert float(times[0]["err_days"]) == stored[0]["err_days"] and times[0]["used"] == "True"
    assert _header(rep, "data/shape.csv") == ["parameter", "value", "minus", "plus", "unit", "point", "note"]
    # no resample converged: the quoted fit's full-data values (all five from it since fix round 1, M-9)
    shape = {r["parameter"]: r for r in _rows(rep, "data/shape.csv")}
    k = env.data["shape"]["fits"][env.data["shape"]["comparison"]["quoted"]]["k"]
    assert float(shape["radius ratio k"]["value"]) == pytest.approx(k) and shape["radius ratio k"]["minus"] == ""
    assert float(shape["radius ratio k"]["point"]) == pytest.approx(k)
    assert shape["radius ratio k"]["note"] == f"no error; {NO_BOOT}"
    assert float(shape["box depth"]["minus"]) == pytest.approx(52.345237)
    _store_errors(env)
    shape = {r["parameter"]: r for r in _rows(_build(env), "data/shape.csv")}
    # fix round 1, I-1: the a/R* row names the fit it comes from (SHAPE_OK quotes the density-prior fit)
    assert list(shape) == ["radius ratio k", "impact parameter b", "a/R* (density-prior fit)", "T14", "model depth",
                           "box depth"]
    assert float(shape["radius ratio k"]["minus"]) == 0.0011 and float(shape["radius ratio k"]["plus"]) == 0.0012
    assert float(shape["radius ratio k"]["point"]) == 0.0469 and shape["radius ratio k"]["note"] == ""
    b = shape["impact parameter b"]
    # polish round 2: the note says what sets b and a/R* (the density prior here), after the missing-error reason
    assert b["minus"] == "" and b["note"] == ("no error; " + DEGENERATE.removesuffix(", errors not quoted")
                                              + "; with the density prior; unconstrained by the data at this cadence")
    assert shape["a/R* (density-prior fit)"]["note"].endswith(
        "with the density prior; unconstrained by the data at this cadence")
    assert shape["T14"]["unit"] == "h" and float(shape["model depth"]["plus"]) == 110.0 and shape["T14"]["note"] == ""


def test_shape_csv_notes_say_what_sets_b_and_a_over_r():
    """Polish round 2: b and a/R* carry "with the density prior; unconstrained by the data at this cadence" when the
    prior sets them, "free fit; the data reject the host's density at X sigma" when the data constrain them, and
    nothing else in the row changes."""
    golden = _golden()
    rows = {r["key"]: r for r in pack_rows.shape_csv_rows(golden)}
    note = "with the density prior; unconstrained by the data at this cadence"
    assert rows["b"]["note"].endswith(note) and rows["a_over_r"]["note"].endswith(note)
    assert rows["k"]["note"] == "" and rows["t14_h"]["note"] == ""
    free = json.loads(json.dumps(golden))
    free["shape"]["comparison"] = {**free["shape"]["comparison"], "quoted": "free", "constrained": True,
                                   "density_tension_sigma": -3.46, "a_over_r_label": "fitted", "reason": None}
    rows = {r["key"]: r for r in pack_rows.shape_csv_rows(free)}
    assert rows["b"]["note"].endswith("free fit; the data reject the host's density at 3.5 sigma")
    assert rows["a_over_r"]["note"].endswith("free fit; the data reject the host's density at 3.5 sigma")
    assert rows["k"]["note"] == ""
    assert "density prior" not in rows["b"]["note"]


def test_stellar_provenance_csv_names_the_method_and_the_reference(env_with_dossier):
    rep = _build(env_with_dossier)
    assert _header(rep, "data/stellar_provenance.csv") == ["parameter", "value", "minus", "plus", "unit", "method",
                                                           "inputs", "reference"]
    rows = {r["parameter"]: r for r in _rows(rep, "data/stellar_provenance.csv")}
    radius = rows["radius"]
    # the round 3 final review, C1(ii): A_Ks 0 at the unreddened end moved the seeded star (0.509 and 0.507 before)
    assert float(radius["value"]) == 0.503 and float(radius["minus"]) == 0.016 and float(radius["plus"]) == 0.016
    assert radius["method"] == "Mann et al. 2015 R(M_Ks)"
    assert radius["reference"] == "Mann, A. W. et al. 2015, ApJ 804, 64"
    assert "Gaia DR3 2101234567890123462" in radius["inputs"] and "2MASS Ks 13.030" in radius["inputs"]
    assert rows["effective temperature"]["reference"].startswith("Pecaut, M. J. and Mamajek")
    assert "Lindegren" in rows["distance"]["reference"] and rows["spectral type"]["value"] == "M1V"
    logg = rows["surface gravity log g"]
    assert float(logg["value"]) == pytest.approx(4.438 + np.log10(0.501) - 2 * np.log10(0.503), abs=1e-3)
    assert float(logg["minus"]) == pytest.approx(np.hypot(0.012 / 0.501, 2 * 0.016 / 0.503) / np.log(10), abs=1e-3)
    assert float(logg["plus"]) == pytest.approx(np.hypot(0.013 / 0.501, 2 * 0.016 / 0.503) / np.log(10), abs=1e-3)
    assert "mass and radius" in logg["method"] and logg["unit"] == "cgs"


def _parse_pipe(text: str, columns: list[str]) -> dict:
    """An ExoFOP bulk file: comment lines starting with a backslash, then the bare header naming exactly the columns,
    one row of as many fields, and no uncertainty without its value."""
    comments = [line for line in text.splitlines() if line.startswith("\\")]
    lines = [line for line in text.splitlines() if not line.startswith("\\")]
    assert comments and all(c.startswith("\\ ") for c in comments) and not any(x.startswith("#") for x in lines)
    assert text.splitlines()[:len(comments)] == comments                       # the comments come first
    assert len(lines) == 2 and lines[0].split("|") == columns
    values = lines[1].split("|")
    assert len(values) == len(columns)
    row = dict(zip(columns, values, strict=True))
    for name, cell in row.items():
        if cell and name.endswith(("_unc", "_err")):
            value = name.rsplit("_", 1)[0]
            assert row[value], f"{name} has no {value}"
    return row


def test_exofop_parameter_files_follow_the_templates(env_with_dossier):
    env = env_with_dossier
    rep = _build(env)
    date = _today()
    planet = _parse_pipe((rep.out_dir / "exofop" / f"params_planet_{date}_001.txt").read_text(encoding="utf-8"),
                         PLANET_COLUMNS)
    assert planet["target"] == "TIC<TIC-TO-FILL>.01" and planet["flag"] == "newctoi" and planet["disp"] == "PC"
    assert planet["discovery"] == "SkySift (Kepler)" and planet["candname"] == f"SkySift-{STAGE2_TARGET}-s5"
    eph = env.data["ephemeris"]
    assert float(planet["period"]) == pytest.approx(eph["period_days"], abs=1e-5)
    assert float(planet["period_unc"]) == pytest.approx(eph["period_err_days"], abs=1e-5)
    # Task 13 (spec 11.6): the central epoch, with its own error
    assert float(planet["epoch"]) == pytest.approx(eph["t_ref_bjd"], abs=5e-5)
    assert float(planet["epoch_unc"]) == pytest.approx(eph["t_ref_err_days"], abs=5e-5)
    assert planet["depth"] == "2167" and planet["depth_unc"] == "52" and planet["duration_unc"] == ""
    assert planet["radius"] == "2.45" and planet["radius_unc"] == "0.08"
    assert planet["temp"] == "267" and planet["temp_unc"] == "8" and planet["sma_unc"] == "0.0015"
    assert planet["r_planet"] == planet["imp"] == planet["ar_star"] == ""        # no bootstrap error yet
    assert planet["mass"] == planet["ecc"] == planet["inc"] == planet["group"] == "" and planet["prop_period"] == "0"
    assert planet["tag"] == f"{date}_<username>_skysift-{STAGE2_TARGET}_00001"
    assert planet["paper"] == "<PAPER-URL-TO-FILL>" and planet["notes"] == "<NOTES-TO-FILL>"
    stellar = _parse_pipe((rep.out_dir / "exofop" / f"params_stellar_{date}_001.txt").read_text(encoding="utf-8"),
                          STELLAR_COLUMNS)
    assert stellar["target"] == "KIC6146676" and stellar["sp_type"] == "M1V" and stellar["prop_period"] == "0"
    head = (rep.out_dir / "exofop" / f"params_stellar_{date}_001.txt").read_text(encoding="utf-8").splitlines()[:3]
    assert head == ["\\ SkySift stellar parameters for ExoFOP's bulk upload",
                    f"\\ Data Tag: {date}_<username>_skysift-{STAGE2_TARGET}_00001", f"\\ pack date: {date}"]
    assert (stellar["teff"], stellar["teff_unc"]) == ("3653", "90") and stellar["group"] == stellar["age"] == ""
    assert (stellar["radius"], stellar["radius_unc"]) == ("0.503", "0.016") and stellar["logg_unc"]
    assert (stellar["dist"], stellar["dist_unc"]) == ("303.0", "3.6") and stellar["mass_unc"] == "0.013"
    assert stellar["notes"] == ("Teff: Mamajek dwarf table; R: Mann et al. 2015 R(M_Ks); M: Mann et al. 2019 M(M_Ks); "
                                "dist: 1000 / Gaia DR3 parallax")
    assert len(stellar["notes"]) <= 120
    with_tic = _parse_pipe(exofop.render_exofop_planet(env.data, "20261022", "123", "tag"), PLANET_COLUMNS)
    assert with_tic["target"] == "TIC123.01" and with_tic["tag"] == "tag"
    no_kic = {**env.data, "target": {**env.data["target"], "kic": None}}
    assert _parse_pipe(exofop.render_exofop_stellar(no_kic, "20261022", "123", "t"),
                       STELLAR_COLUMNS)["target"] == "TIC123"
    fitted = _parse_pipe(exofop.render_exofop_planet(_with_errors(env.data), "20261022", None, "t"), PLANET_COLUMNS)
    assert fitted["imp"] == fitted["imp_unc"] == ""                            # degenerate: b without errors
    # the quoted prior fit: ar_star is the stellar a/R* with its errors (spec 11.3)
    assert (fitted["ar_star"], fitted["ar_star_err"]) == ("78.1", "2.5")
    assert (fitted["r_planet"], fitted["r_planet_err"]) == ("0.0470", "0.0012")
    assert (fitted["duration"], fitted["duration_unc"]) == ("4.16", "0.06")
    readme = (rep.out_dir / "README.md").read_text(encoding="utf-8")
    step2 = readme.split("## Step 2.")[1].split("## Step 3.")[0]
    attach = step2.split("Attach these:")[1].split("What ExoFOP takes now")[0]
    assert f"exofop/params_stellar_{date}_001.txt" in attach and "params_planet" not in attach
    assert f"`exofop/params_planet_{date}_001.txt`" in step2 and "do not upload it now" in step2


def test_the_stellar_file_names_the_route_it_came_from(env_with_dossier):
    """The mamajek route (no 2MASS Ks): radius and mass come from the colour table, and the note says so."""
    data = json.loads(json.dumps(env_with_dossier.data))
    table = "Mamajek dwarf table at BP-RP 2.097 +- 0.20 (Pecaut, M. J. and Mamajek, E. E. 2013, ApJS 208, 9)"
    data["star"].update(route="mamajek", radius={**data["star"]["radius"], "source": table},
                        mass={**data["star"]["mass"], "source": table})
    stellar = _parse_pipe(exofop.render_exofop_stellar(data, "20261022", None, "t"), STELLAR_COLUMNS)
    assert stellar["notes"] == ("Teff, R, M: Mamajek dwarf table; dist: 1000 / Gaia DR3 parallax; density: M* / "
                                "(4/3 pi R*^3) from the same draws")
    assert "Mann" not in stellar["notes"] and "2MASS" not in stellar["notes"] and len(stellar["notes"]) <= 120
    rows = {r["parameter"]: r for r in pack_rows.provenance_rows(data)}
    assert rows["radius"]["method"].startswith("Mamajek dwarf table") and "Pecaut" in rows["radius"]["reference"]


def test_a_star_without_parameters_gets_no_stellar_file(env_with_dossier):
    env = env_with_dossier
    data = _stored(env)
    data["star"] = {**data["star"], "route": "none", "teff": None, "radius": None, "mass": None, "density_gcc": None,
                    "distance_pc": None, "spectral_type": None}
    _store(env, data)
    rep = _build(env)
    date = _today()
    assert exofop.render_exofop_stellar(data, date, None, "t") is None
    assert not (rep.out_dir / "exofop" / f"params_stellar_{date}_001.txt").exists()
    assert (rep.out_dir / "exofop" / f"params_planet_{date}_001.txt").exists()
    step2 = (rep.out_dir / "README.md").read_text(encoding="utf-8").split("## Step 2.")[1].split("## Step 3.")[0]
    assert "params_stellar" not in step2 and "There is no stellar parameter file" in step2
    assert "upload now" not in step2
    note = (rep.out_dir / "exofop" / "note.txt").read_text(encoding="utf-8")
    assert "stellar parameters in ExoFOP's template" not in note and "no stellar parameter file" in note
    assert "stellar parameters in ExoFOP's template" in pack_module._render_plain("exofop_note.txt.j2",
                                                                                  env.data, {})
    assert "when the star is characterised" in pack_module.STEP_TEXTS["2"]


def test_readme_defines_crowdsap_and_psffrac(env_with_dossier):
    text = render_readme(env_with_dossier.data, {})
    assert "| target light fraction (CROWDSAP) | 0.90 |" in text and "| PSF flux fraction (PSFFRAC) | 0.90 |" in text
    assert text.count("CROWDSAP is the fraction of the light in the aperture that belongs to the target") == 1
    assert "PSFFRAC is the fraction of the target's PSF flux inside the KBonus aperture" in text
    # round-2 final review, I-2: "crowding" only in the PSF-photometry statement the note prints
    # Task 15 addendum 2: and the "crowding metric" the leakage budget is traced to
    assert "KBonus header keywords" in text and text.count("crowding") == text.count("no crowding correction") + \
        text.count("crowding metric")


def test_readme_without_a_parity_difference_or_a_timing_fit(env_with_dossier):
    data = _with_errors(env_with_dossier.data)
    data["parity"]["difference_sigma"] = None
    data["timing"] = {**data["timing"], "ok": False, "reason": "fewer than 3 transits timed"}
    data["ephemeris"].update(period_err_days=None, t0_bkjd_err=None, t0_bjd_err=None, t_ref_bkjd=None,
                             t_ref_bjd=None, t_ref_err_days=None, epoch_ref=None)
    text = render_readme(data, {})
    assert "| odd and even depths | 2,212 ± 76 ppm and 2,126 ± 83 ppm |" in text and "( sigma" not in text
    step5 = text.split("## Step 5.")[1].split("## What is not yet known")[0]
    assert "the transit lasts 4.16 +0.06/-0.05 h" in step5 and "no formal error" in step5


def test_readme_step_five_lists_the_next_transits_with_windows(env_with_dossier):
    from skysift.ephemeris import predict_from
    data, after = env_with_dossier.data, 2461335.5                       # 22 October 2026, 0 h UTC
    rows = pack_rows.next_transits(data, after)
    assert len(rows) == 3 and [r["epoch"] for r in rows] == [rows[0]["epoch"] + i for i in range(3)]
    t, err = predict_from(data["timing"], rows[0]["epoch"])
    assert rows[0]["t_bjd"] == pytest.approx(t + BKJD) and rows[0]["window_min"] == pytest.approx(err * 1440)
    assert rows[0]["t_bjd"] > after >= predict_from(data["timing"], rows[0]["epoch"] - 1)[0] + BKJD
    text = render_readme(data, {"date": "20261022", "after_bjd": after})
    step5 = text.split("## Step 5.")[1].split("## What is not yet known")[0]
    for r in rows:
        assert f"- epoch {r['epoch']}: BJD_TDB {r['t_bjd']:.4f} ± {r['window_min']:.0f} min" in step5
    assert "22 October 2026" in step5 and "no formal error" not in step5
    assert pack_rows.next_transits({**data, "timing": None}, after) == []
    assert pack_rows.next_transits({**data, "timing": {**data["timing"], "ok": False}}, after) == []


def test_every_text_quotes_the_period_and_epoch_with_errors(env_with_dossier):
    env = env_with_dossier
    _store_errors(env)
    rep = _build(env)
    commit = env.data["provenance"]["git_commit"]
    for name in ("README.md", "exofop/note.txt", "archive_email.txt"):
        text = (rep.out_dir / name).read_text(encoding="utf-8")
        # Task 13 (spec 11.6): T0 is the central epoch with its own error
        assert "40.289863 ± 0.000091 d" in text and "2455694.51787 ± 0.00092" in text, name
        assert "4.16 +0.06/-0.05 h" in text and "2,167 ± 52 ppm" in text and NO_BOOT not in text, name
    for name in ("exofop/note.txt", "archive_email.txt"):
        text = (rep.out_dir / name).read_text(encoding="utf-8")
        assert "search version 1+abcdef01" in text and f"commit {commit}" in text, name
    tex = (rep.out_dir / "note" / "note.tex").read_text(encoding="utf-8")
    assert r"40.289863 $\pm$ 0.000091 days" in tex and r"2455694.51787 $\pm$ 0.00092" in tex
    assert "4.16$^{+0.06}_{-0.05}$ hours" in tex and "8.0 minutes" in tex and "34 of 34" in tex
    meta = json.loads((rep.out_dir / "zenodo_metadata.json").read_text(encoding="utf-8"))
    assert "40.289863 ± 0.000091 d" in meta["description"] and "2455694.51787 ± 0.00092" in meta["description"]
    _store(env, env.data)                                                 # the fixture's own: no shape error
    text = _unquoted((_build(env).out_dir / "README.md").read_text(encoding="utf-8"))
    assert text.count(f"(no error; {NO_BOOT})") == 1 and "4.41 h (no error;" in text


def test_a_missing_ephemeris_error_is_named_once_and_never_paired_with_a_search_value(env_with_dossier):
    data = json.loads(json.dumps(env_with_dossier.data))
    data["timing"] = {**data["timing"], "ok": False, "reason": "fewer than 3 transits timed"}
    data["ephemeris"].update(period_days=40.2899, t0_bkjd=136.3, t0_bjd=136.3 + BKJD, period_err_days=None,
                             t0_bkjd_err=None,
                             t0_bjd_err=None, t_ref_bkjd=None, t_ref_bjd=None, t_ref_err_days=None, epoch_ref=None,
                             period_source="search")
    for text in (_unquoted(render_readme(data, {})), pack_module._render_plain("exofop_note.txt.j2", data, {}),
                 pack_module.archive_paragraph(data, "10.5281/zenodo.1", None)):
        assert text.count("(no error; fewer than 3 transits timed)") == 1
        assert "40.28990 d (no error; fewer than 3 transits timed)" in text and "2454969.3000 ±" not in text
    tex = render_note(data)
    assert tex.count("no error; fewer than 3 transits timed") == 1 and "2454969.3000" in tex


def tex_safe(text: str) -> str:
    return pack_text.tex_escape(text)


def test_note_has_software_facilities_and_the_model_clause_within_the_limits(env_with_dossier):
    from importlib import metadata as md
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    for data in (env_with_dossier.data, golden):
        tex = render_note(data)
        version = data["signal"]["detector_version"]
        # the commit the dossier was generated at (the golden's 09ee6e3, not the checkout's HEAD), and triceratops
        # only with a stored cross-check (M10), which the golden has (fix round 4)
        commit = data["provenance"]["git_commit"]
        # Task 13 (spec 11.6): each package with its installed version
        tools = ", ".join(f"{p} {md.version(p)}" for p in ("numpy", "scipy", "astropy", "matplotlib")) + (
            f", triceratops {data['cross_check']['tool_version']}" if data.get("cross_check") else "")
        assert (rf"\software{{SkySift {md.version('skysift')} (search version {tex_safe(version)}, commit "
                rf"{tex_safe(commit)}), {tools}}}") in tex
        assert (data is golden) == bool(data.get("cross_check")) and (data is not golden or commit == "09ee6e3")
        crossed = render_note({**data, "cross_check": {"fpp": 0.01, "nfpp": 0.001, "current": False}})
        assert f"matplotlib {md.version('matplotlib')}, triceratops}}" in crossed
        assert r"\facilities{Kepler, Gaia, MAST, Exoplanet Archive}" in tex
        # fix round 1 cut 14: the data sentence keeps the commit and both DOIs without its connectives
        assert (f"code: SkySift at commit {tex_safe(commit)}, doi:10.5281/zenodo.SOFTWARE (to be minted)."
                in " ".join(tex.split()))
        # Task 15 fix round 1, cut B7: the note glosses PSFFRAC without the keyword; the README defines it
        assert "CROWDSAP" in tex and "PSFFRAC" not in tex and "a PSF flux fraction inside the aperture of" in tex
        assert "No Kepler stellar catalogue covers a background star" in tex
        words = note_words(tex)
        assert note_problems(words, tex) == [] and words["total"] <= NOTE_LIMITS["total"]
    tex = render_note(_with_errors(env_with_dossier.data))
    assert (r"a quadratic limb-darkened model \citep{claret2011} on a circular orbit, integrated over the 29.4-minute "
            "exposure") in tex
    # the quoted prior fit: b and the stellar a/R* with the words of spec 11.3, the degeneracy not repeated
    assert "0.0470$^{+0.0012}_{-0.0011}$" in tex and DEGENERATE not in tex
    prior_flat = " ".join(tex.split())
    assert ("b and a/R* are unconstrained at this cadence (the quoted values follow the host's density): b is 0.31 "
            "and a/R* ") in prior_flat
    assert "sigma from the host's density" not in prior_flat          # fix round 1, M-1: no tension when unconstrained
    # fix round 1, M-2: a prior fit quoted because the free fit failed is not described as a comparison
    failed = _with_errors(env_with_dossier.data)
    failed["shape"]["comparison"] = {**failed["shape"]["comparison"], "constrained": None, "delta_chi2": None,
                                     "reason": "no start converged"}
    failed_flat = " ".join(render_note(failed).split())
    assert "b and a/R* are from the density-prior fit (the free fit did not converge): b is 0.31" in failed_flat
    assert "unconstrained at this cadence" not in failed_flat
    # fix round 1, M-10: the clause prints the measured exposure
    short = _with_errors(env_with_dossier.data)
    short["shape"]["exposure_min"] = 1.966
    assert "integrated over the 2.0-minute exposure" in " ".join(render_note(short).split())
    # a quoted free fit: the degenerate b says "no error" once, also for a reason stored before it stopped saying
    # "errors not quoted"
    free = _with_errors(env_with_dossier.data)
    free["shape"]["comparison"] = {**free["shape"]["comparison"], "quoted": "free", "constrained": True,
                                   "density_tension_sigma": -1.3, "a_over_r_label": "fitted", "reason": None}
    flat = " ".join(render_note(free).split())
    assert ("b is 0.31 and a/R* 74.0 fitted, 1.3 sigma from the host's density (no error; ingress shorter than the "
            "cadence: b and a/R* are degenerate with k)") in flat
    # fix round 4: the parity and secondary sentences keep every number and error in fewer words; Task 13: the
    # stacked folds and an albedo without a limit are the README's
    assert (r"The odd and even depths are 2,212 $\pm$ 76 and 2,126 $\pm$ 83 ppm per event (0.8 sigma apart)." in
            " ".join(tex.split()))
    assert "stacked folds" not in tex and "geometric albedo" not in tex
    assert (r"The phase 0.5 depth is 15 $\pm$ 65 ppm (3-sigma limit 210 ppm), the strongest dip elsewhere 40 ppm "
            "at phase 0.62 (1.4 sigma).") in tex                       # fix round 1 cut 12
    assert "14 $\\pm$ 31 ppm" in tex
    assert note_problems(note_words(tex), tex) == []


def test_readme_says_the_archive_needs_a_refereed_paper(env_with_dossier):
    rep = _build(env_with_dossier)
    text = (rep.out_dir / "README.md").read_text(encoding="utf-8")
    step2 = text.split("## Step 2.")[1].split("## Step 3.")[0]
    assert "community TOI" in step2 and "peer-reviewed" in step2 and "now" in step2
    step3 = text.split("## Step 3.")[1].split("## Step 4.")[0]
    assert "not refereed" in step3
    step4 = text.split("## Step 4.")[1].split("## Step 5.")[0]
    assert "refereed paper" in step4 and "not from the Research Note" in step4
    assert "since the archive adds objects from the literature" not in text
    email = (rep.out_dir / "archive_email.txt").read_text(encoding="utf-8")
    first = email.split("\n\n", 1)[1].split(". ")[0]
    assert "refereed paper" in first and "Research Note" in first
    assert "once it is published" not in pack_module.STEP_TEXTS["4"] and "refereed" in pack_module.STEP_TEXTS["4"]
    provenance = text.split("## Provenance")[1]
    assert "doi:10.5281/zenodo.SOFTWARE (to be minted)" in provenance and "triceratops" not in provenance


def test_independent_periods():
    others = [40.5, 80.4, 20.1, 13.4, 120.0, 27.0, 55.0, 10.0, 27.0]
    assert pack_module.independent_periods(40.0, others) == [10.0, 27.0, 55.0]
    assert pack_module.independent_periods(40.0, []) == []
    assert pack_module.independent_periods(40.0, [41.0], tolerance=0.03) == []


def test_the_readme_names_another_independent_signal(env_with_dossier):
    from skysift.models import Signal
    env = env_with_dossier
    assert "covers one candidate only" not in (_build(env).out_dir / "README.md").read_text(encoding="utf-8")
    run = env.store.start_run("second", STAGE2_TARGET, "box_dip", "1+abcdef01", "1")
    for period in (27.3, 80.58):                                        # one independent, one at twice the period
        sid = env.store.add_signal(Signal(target_id=STAGE2_TARGET, detector="box_dip", detector_version="1+abcdef01",
                                          period=period, t0=140.0, duration=0.1, depth_ppm=500.0, snr=9.0, sde=10.0,
                                          n_events=40), run)
        env.store.set_signal_status(sid, "candidate_stage2")
    text = (_build(env).out_dir / "README.md").read_text(encoding="utf-8")
    step2 = text.split("## Step 2.")[1].split("## Step 3.")[0]
    assert "27.300 days" in step2 and "80.580" not in step2 and "covers one candidate only" in step2
    assert pack_module.other_signal_periods(None, STAGE2_TARGET, 1) == []


def test_a_live_draft_refuses_a_dossier_without_ephemeris_errors(env_with_dossier, fake_session, monkeypatch):
    from skysift import fpp
    monkeypatch.setattr(fpp, "SIGNED_OFF", True)
    env = env_with_dossier
    _approve(env)
    data = _stored(env)
    data["timing"] = {**data["timing"], "ok": False, "reason": "fewer than 3 transits timed"}
    _store(env, data)
    live = ZenodoClient("t", sandbox=False, session=fake_session)
    with pytest.raises(PackError) as e:
        _build(env, zenodo=live)
    assert "the ephemeris carries no error (fewer than 3 transits timed)" in str(e.value)
    assert f"the transit shape carries no error ({NO_BOOT})" in str(e.value)
    assert fake_session.calls == [] and env.store.dossier(STAGE2_TARGET)["pack_path"] == ""
    assert pack_module.gate_problems({}) == ["the ephemeris carries no error (no timing block)",
                                             "the transit shape carries no error (no shape block)",
                                             "no TRICERATOPS cross-check", "the stored commit is dirty or unknown"]
    assert pack_module.gate_problems(_with_errors(_stored(env))) == [
        "the ephemeris carries no error (fewer than 3 transits timed)", "no TRICERATOPS cross-check"]
    monkeypatch.setattr(net, "fetch", env.fetch)
    monkeypatch.setattr(zenodo, "new_session", lambda: fake_session)
    monkeypatch.setenv("SKYSIFT_ZENODO_TOKEN", "a-secret")
    r = runner.invoke(app, ["pack", "--target", STAGE2_TARGET, "--zenodo-live"])
    assert r.exit_code == 2 and "the ephemeris carries no error" in r.output and fake_session.calls == []
    assert _build(env, zenodo=ZenodoClient("t", session=fake_session)).zenodo is not None    # the sandbox builds
    _store(env, _with_errors(env.data))
    assert pack_module.gate_problems(_stored(env)) == ["no TRICERATOPS cross-check"]
    _store_cross_check(env)
    assert pack_module.gate_problems(_stored_with_cross_check(env)) == []


def test_figure_data_csv_matches_the_fold_bins(env_with_dossier):
    data = env_with_dossier.data
    rep = _build(env_with_dossier)
    assert _header(rep, "note/figure1_data.csv") == ["phase_h", "flux_ppm", "err_ppm", "n", "model_ppm"]
    rows, bins = _rows(rep, "note/figure1_data.csv"), data["plots"]["fold_bins"]
    model = np.array(data["plots"]["model"], float)
    assert len(rows) == len(bins) > 0
    for row, b in zip(rows, bins, strict=True):
        assert [float(row["phase_h"]), float(row["flux_ppm"]), float(row["err_ppm"]), int(row["n"])] == b
        assert float(row["model_ppm"]) == pytest.approx(np.interp(b[0], model[:, 0], model[:, 1]), abs=0.05)
    assert pack_rows.figure_data_rows(json.loads(GOLDEN.read_text(encoding="utf-8"))) == []
    readme = (rep.out_dir / "README.md").read_text(encoding="utf-8")
    for name in ("note/figure1_data.csv", "data/transit_times.csv", "data/shape.csv", "data/stellar_provenance.csv"):
        assert f"- `{name}`: " in readme and pack_module.file_notes(data)[name] in readme
    assert all(not k.startswith("exofop/") or k == "exofop/note.txt" or "params_stellar" in k
               for k in pack_module.FILE_NOTES)                              # no entry the README never shows


def test_pack_prints_the_cross_check_and_the_gate_refuses_without_one(env_with_dossier, fake_session, monkeypatch):
    from skysift import fpp
    monkeypatch.setattr(fpp, "SIGNED_OFF", True)
    env = env_with_dossier
    _approve(env)
    _store_errors(env)
    live = ZenodoClient("t", sandbox=False, session=fake_session)
    with pytest.raises(PackError, match="no TRICERATOPS cross-check"):
        _build(env, zenodo=live)
    assert fake_session.calls == []
    rep = _build(env)                                                        # without one: no sentence, no file
    readme = (rep.out_dir / "README.md").read_text(encoding="utf-8")
    assert "TRICERATOPS" not in (rep.out_dir / "exofop" / "note.txt").read_text(encoding="utf-8")
    assert "The live Zenodo draft waits on the cross-check: no TRICERATOPS cross-check" in readme
    assert "data/cross_check.json" not in rep.files
    # ruling t: the numbers never block; a median full FPP at or above 0.5 is explained and flagged for the owner
    from tests.unit.test_crosscheck import fake_result
    scen = [{"ID": "169822650", "scenario": "TP", "prob": 0.4}, {"ID": "169822650", "scenario": "STP", "prob": 0.6}]
    high = {**fake_result(cross_check.inputs_fingerprint(_stored(env)), fpp=0.6, scenarios=scen), "current": True}
    env.store.set_cross_check(STAGE2_TARGET, high)
    assert pack_module.gate_problems(_stored_with_cross_check(env)) == []
    rep = _build(env)
    readme = (rep.out_dir / "README.md").read_text(encoding="utf-8")
    assert ("For the owner: TRICERATOPS puts the median full false-positive probability at 0.60 (16th to 84th "
            "percentile ") in readme
    assert "mainly through a planet transiting an unresolved bound companion, the STP scenario" in readme
    old = {"tool": cross_check.TOOL, "fpp": 0.01, "nfpp": 1e-4, "fpp_runs": [0.01] * 10,
           "inputs_sha256": cross_check.inputs_fingerprint(_stored(env))}
    env.store.set_cross_check(STAGE2_TARGET, old)                           # the format before 22 October 2026
    assert pack_module.gate_problems(_stored_with_cross_check(env)) == [
        "the cross-check was stored by an earlier version of the tool; run it again"]
    block = _store_cross_check(env)                                          # 0.012 against SkySift's FPP
    rep = _build(env, zenodo=live)
    assert rep.zenodo is not None and not rep.zenodo["sandbox"]
    sentence = cross_check.comparison_sentence({**_stored(env), "cross_check": block})
    assert sentence.startswith("TRICERATOPS 1.1.0 (Giacalone et al. 2021), run on the KBonus light curve with Kepler "
                               "limb darkening and Gaia DR3 field stars")
    assert "16th to 84th percentile" in sentence and "without a planet transiting an unresolved bound" in sentence
    # M5: the plain texts print the median once, in the headline, and the cross-check sentence refers back to it
    after = cross_check.comparison_sentence({**_stored(env), "cross_check": block}, after_headline=True)
    assert after.startswith("The TRICERATOPS values are medians over ")
    for name in ("README.md", "exofop/note.txt", "archive_email.txt"):
        text = (rep.out_dir / name).read_text(encoding="utf-8")
        assert after in text and sentence not in text, name
        own = "\n".join(line for line in text.splitlines() if not line.startswith("> "))   # the README quotes the email
        assert own.count("gives a false-positive probability of 0.012") + own.count("gives 0.012") == 1, name
    readme = (rep.out_dir / "README.md").read_text(encoding="utf-8")
    assert "waits on the cross-check" not in readme and "For the owner" not in readme
    tex = (rep.out_dir / "note" / "note.tex").read_text(encoding="utf-8")
    fp_section = tex.split(r"\section{How likely is a false positive}")[1].split(r"\section")[0]
    assert r"TRICERATOPS 1.1.0 \citep{giacalone2021}, run on the KBonus light curve with" in fp_section
    assert "{Giacalone} et~al.(2021)" in tex.split(r"\begin{thebibliography}")[1]
    assert note_problems(note_words(tex), tex) == []
    stored = json.loads((rep.out_dir / "data" / "cross_check.json").read_text(encoding="utf-8"))
    assert stored == cross_check.with_current(block, _stored(env)) and stored["class"] == "validated"
    assert "data/cross_check.json" in rep.files and "data/cross_check.json" in readme


# ---------------------------------------------------------------------------------------------------- final fix wave

def test_the_note_names_its_methods_and_cites_them(env_with_dossier):
    """I3: one method sentence (Deeg 2015, Holczer et al. 2016, Schmitt et al. 2014) with its three bibliography
    entries, and the full-data value beside the resample median when the shape_offset caveat fires; M4 and spec 11.3:
    the fold and the bootstrap use the ephemeris the dossier folded on."""
    _store_errors(env_with_dossier)
    data = _stored(env_with_dossier)
    data["caveats"] = [*data["caveats"], {"key": "shape_offset", "title": "The shape fit sits off its bootstrap",
                                          "text": "quote the full-data value beside it."}]
    tex = render_note(data)
    signal = tex.split(r"\section{The signal}")[1].split(r"\section{")[0]
    # Task 8 fix round 3: the note keeps a pointer without citations, the sentence and its works are in the README;
    # fix round 4, M-3: the note's first pointer says which README; round-2 word budget: a clause closing the
    # ephemeris sentence; Task 15 fix round 1, cut B6: the clause goes, the Data availability line names the methods
    assert "on the linear-fit ephemeris." in " ".join(signal.split()) and "(methods in" not in signal
    assert "Pack (data and methods): " in " ".join(tex.split())
    assert pack_module.errors_pointer(data) == "methods in the Zenodo pack's README"
    assert "deeg2015" not in tex and "holczer2016" not in tex and "schmitt2014" not in tex
    assert "It remains a candidate pending follow-up." not in signal   # the abstract says it
    readme = render_readme(data, {})
    errors = readme.split("Ephemeris and transit-parameter errors (the note's signal section points here): ")[1]
    errors = errors.split("\n")[0]
    assert "weighted linear fit of the per-transit mid-times" in errors and "(Deeg 2015)" in errors
    assert "(Holczer et al. 2016)" in errors and "bootstrap over transits (Schmitt et al. 2014)" in errors
    assert "16th to 84th percentile spread of the resamples about their median" in errors
    assert "use the linear-fit period and epoch." in errors
    # fix round 1, M-7: the SES cut is named once, with its threshold and citation
    assert ("transits with a single-event S/N below 2.5 are not timed (Holczer et al. 2016)" in errors
            and errors.count("2.5") == 1)
    # fix round 1, I-1: a bootstrapped density-prior fit says how its prior entered the resamples
    assert "drawn from its Gaussian" not in errors
    drawn = {**data, "shape": {**data["shape"], "method": ephemeris.SHAPE_PRIOR_METHOD}}
    assert ("bootstrap over transits (Schmitt et al. 2014), with the density prior's centre drawn from its Gaussian "
            "on each resample") in pack_module.errors_methods_text(drawn)
    searched = {**data, "ephemeris": {**data["ephemeris"], "fold_ephemeris": "search"}}
    assert "use the search period and epoch; the quoted ephemeris is the linear fit." in \
        pack_module.errors_methods_text(searched)
    for item in ("Deeg, H. J. 2015, arXiv:1503.05368", "Holczer, T., et al. 2016, ApJS, 225, 9",
                 "Schmitt, J. R., et al. 2014, AJ, 148, 28"):
        assert item in errors
    assert readme.index("| TESS | ") < readme.index("Ephemeris and transit-parameter errors (the note")
    assert "0.0470$^{+0.0012}_{-0.0011}$ (full-data fit 0.0469)" in tex
    items = set(re.findall(r"\\bibitem\[[^\]]*\]\{([^}]+)\}", tex))
    keys = {k.strip() for c in re.findall(r"\\cite[pt]?\{([^}]+)\}", tex) for k in c.split(",")}
    assert keys == items and note_problems(note_words(tex), tex) == []
    # no full-data value in the note without the caveat; no method sentence without either block
    assert "full-data fit" not in render_note(_with_errors(_stored(env_with_dossier)))
    bare = {**data, "timing": {"ok": False, "reason": "x"}, "shape": {"ok": False, "reason": "y"}}
    assert pack_module.methods_sentence(bare) is None and pack_module.errors_pointer(bare) is None
    assert "(methods in the README" not in render_note(bare) and "transit-parameter errors (the" not in \
        render_readme(bare, {})
    timed_only = {**data, "shape": {"ok": False, "reason": "y"}}
    assert pack_module.errors_pointer(timed_only) == "methods in the Zenodo pack's README"
    assert "Schmitt" not in pack_module.errors_methods_text(timed_only)


def test_the_caption_quotes_the_stored_fold_bin(env_with_dossier):
    """I4: the bin of the fold (an eighth of the search duration), not one worked out from the fitted T14."""
    data = _with_errors(env_with_dossier.data)
    dur = data["signal"]["duration"]
    assert data["plots"]["bin_minutes"] == pytest.approx(dur * 1440 / 8, abs=0.01)
    data["ephemeris"]["duration_h"] = 4.16                    # the fitted T14 would give 31 minutes
    tex = render_note({**data, "plots": {**data["plots"], "bin_minutes": 22.5}})
    assert "bins of 22.5 minutes" in tex
    old = {**data, "plots": {k: v for k, v in data["plots"].items() if k != "bin_minutes"}}
    assert f"bins of {dur * 1440 / 8:.1f}".removesuffix(".0") in render_note(old)


def test_the_caption_quotes_the_fold_window_with_or_without_the_plots(env_with_dossier):
    """The golden block keeps no plots, so the caption of a dossier without them works the window out from the signal
    (the smaller of four search durations and half the period), never "within  hours"."""
    data = _with_errors(env_with_dossier.data)
    assert f"within {data['plots']['window_h']:.1f} hours of mid-transit" in render_note(data)
    bare = {k: v for k, v in data.items() if k != "plots"}
    sig = data["signal"]
    window = min(4 * sig["duration"], 0.5 * sig["period"]) * 24
    assert f"within {window:.1f} hours of mid-transit" in render_note(bare)
    golden = json.loads((Path(__file__).parents[1] / "golden" / "kic-006146676-generated.json").read_text(
        encoding="utf-8"))
    assert "plots" not in golden and pack_module.window_hours(golden) == "12.0"
    assert re.search(r"within \d+\.\d hours", render_note(_live_shape("validated")))


def test_the_live_gate_refuses_a_dirty_or_unknown_commit(env_with_dossier):
    """I6 (ruling g): the software statement must name archived code."""
    ok = {**_stored_with_cross_check(env_with_dossier), "provenance": {"git_commit": "8f9b249"}}
    dirty = "the stored commit is dirty or unknown"
    assert dirty not in pack_module.gate_problems(ok)
    for commit in ("8f9b249-dirty", "unknown", None):
        assert dirty in pack_module.gate_problems({**ok, "provenance": {"git_commit": commit}}), commit


def test_the_data_tag_carries_its_sequence(env_with_dossier):
    """M7: YYYYMMDD_username_description_nnnnn, 00001 by default, --tag-seq on the command line."""
    assert exofop.data_tag("20261022", "skysift", "kic-006146676") == "20261022_<username>_skysift-kic-006146676_00001"
    rep = _build(env_with_dossier, tag_seq=12)
    note = (rep.out_dir / "README.md").read_text(encoding="utf-8")
    assert f"_<username>_skysift-{STAGE2_TARGET}_00012" in note
    with pytest.raises(PackError, match="sequence"):
        _build(env_with_dossier, tag_seq=0)


def test_a_dossier_without_timing_prints_no_empty_timing_numbers(env_with_dossier):
    """G2: a version-2-shaped JSON (the golden's keys, no timing block) never prints "of  timed transits"."""
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    v2 = {k: v for k, v in golden.items() if k not in ("timing", "shape", "parity", "secondary")}
    v2["ephemeris"] = {**golden["ephemeris"]}
    for text in (pack_module._render_plain("exofop_note.txt.j2", v2, {}), _unquoted(render_readme(v2, {})),
                 render_note(v2)):
        assert "timed transits" not in text and "rms of  " not in text and " of  " not in text
        assert "Timed one by one" not in text
    assert "Allow at least one duration either side of a predicted time." in pack_module._render_plain(
        "exofop_note.txt.j2", v2, {})
    assert note_problems(note_words(render_note(v2)), render_note(v2)) == []


def test_one_precision_rule_for_the_pack_and_the_page():
    """M2 (ruling j): the error to two significant figures, the value to its last decimal, never more decimals than
    the stored error carries; the page's helper is the pack's."""
    from skysift.ui.routes import dossiers as routes
    assert pack_text.pm_text(40.2898631, 9.1263e-05, 9.1263e-05, nd=5) == "40.289863 ± 0.000091"
    assert pack_text.pm_text(4.1612, 0.0512, 0.0634) == "4.161 +0.063/-0.051"
    assert pack_text.pm_text(2.61, 0.08, 0.09) == "2.61 +0.09/-0.08"
    assert [routes.err_digits(x) for x in (9.1263e-05, 0.016, 73.0, None, "x")] == [
        pack_text.err_digits(x) for x in (9.1263e-05, 0.016, 73.0, None, None)] == [6, 3, 0, 2, 2]
    js = (Path(pack_module.__file__).parent / "ui" / "static" / "dossier.js").read_text(encoding="utf-8")
    assert "skysift.pack_text.pm_digits" in js and "Math.max(0, 1 - Math.floor(Math.log10(err)))" in js


def test_the_readme_says_where_to_find_the_tic(env_with_dossier):
    """M14: without --tic, step 2 names the ExoFOP target page and the MAST TIC search by the KIC coordinates."""
    step2 = render_readme(env_with_dossier.data, {}).split("## Step 2.")[1].split("## Step 3.")[0]
    assert "TIC id is on its ExoFOP target page" in step2 and "MAST TIC search by the KIC coordinates" in step2
    named = render_readme(env_with_dossier.data, {"tic": "169822650"}).split("## Step 2.")[1].split("## Step 3.")[0]
    assert "MAST TIC search" not in named and "TIC 169822650" in named


# ---------------------------------------------------------------------------------------------------- prior claims (P1)

PRIOR_DOI = "10.5281/zenodo.23325261"


@pytest.fixture
def env_prior(settings, monkeypatch):
    """The seed with a prior claim declared in its candidates folder, and a hand-built localisation beside it."""
    env = _make_env(settings, monkeypatch)
    folder = dossier_module.CANDIDATES_DIR / STAGE2_TARGET
    (folder / "localisation").mkdir(parents=True)
    (folder / "prior_claims.json").write_text(json.dumps([PRIOR_CLAIM]), encoding="utf-8")
    _store_pixels(env, _pixels_npz())
    env.report = generate(env.store, env.blob, env.settings, STAGE2_TARGET, fetch=env.fetch)
    env.data = json.loads(env.report.data_path.read_text(encoding="utf-8"))
    seasons = [s for s in next(t for t in env.data["tests"] if t["rung"] == "rung5_pixels")["metrics"]["seasons"]
               if s.get("voting")]
    hand = {str(s["season"]): s["dist_target_pix"] + 0.03 for s in seasons}
    (folder / "localisation" / "results.json").write_text(json.dumps({"summary": {
        "seasons_snrbox_centroid_dist_to_candidate_pix": hand}}), encoding="utf-8")
    env.report = generate(env.store, env.blob, env.settings, STAGE2_TARGET, fetch=env.fetch, force=True)
    env.data = json.loads(env.report.data_path.read_text(encoding="utf-8"))
    return env


def _texts(rep) -> dict[str, str]:
    return {name: (rep.out_dir / name).read_text(encoding="utf-8")
            for name in ("README.md", "note/note.tex", "exofop/note.txt", "archive_email.txt")}


def test_a_prior_claim_makes_every_text_a_vetting_and_never_a_blind_recovery(env_prior):
    env = env_prior
    assert env.data["prior_claims"][0]["doi"] == PRIOR_DOI
    rep = _build(env, tic="169822650")
    texts = _texts(rep)
    note = " ".join(texts["note/note.tex"].split())
    assert r"\title{SkySift vetting of the 40.290 day transit candidate on KIC 6146676}" in note
    for name, text in texts.items():
        assert not re.search(r"\bblind\b", text, re.IGNORECASE) and "independent recovery" not in text.lower(), name
        assert "independent" not in text.lower().replace("independent in method", ""), name
        assert PRIOR_DOI in text and "13 October 2026" in text, name
        assert "discovery" not in text.lower() and EM_DASH not in text and not IZE.search(text), name
    for name in ("README.md", "note/note.tex"):
        assert "a re-detection" in texts[name] and "Against that claim the period differs by" in texts[name], name
    # Task 13 fix round 1 (I-3): spec 11.1's development note in full in every public text, the short clause in the
    # note alone
    for name in ("README.md", "exofop/note.txt", "archive_email.txt"):
        assert DEVELOPMENT_NOTE in " ".join(texts[name].split()), name
    assert f"SkySift's result is a re-detection: {DEVELOPMENT_NOTE_SHORT}." in note and DEVELOPMENT_NOTE not in note
    assert "the sweep's target list" in pack_module.prior_claim_sentence(env.data)
    # I-2: the prior-claim paragraph keeps its blank line before the next one; M1: its comparison reaches every text
    comparison = pack_module.comparison_text(env.data)
    assert comparison and comparison.startswith("Against that claim the period differs by")
    assert re.search(r"^Prior claim: .+" + re.escape(comparison) + r"\n\nThis pack was built", texts["README.md"],
                     re.MULTILINE)
    assert re.search(r"^Prior claim: .+" + re.escape(comparison) + r"\n\nEphemeris:", texts["exofop/note.txt"],
                     re.MULTILINE)
    assert comparison in texts["archive_email.txt"]
    # the citation, the bibliography and where the claim's date and DOI are printed stay as Task 8 set them
    note = texts["note/note.tex"]
    assert r"\citet{mehta2026}" in note
    assert (r"\bibitem[{Mehta}(2026)]{mehta2026} Mehta, K. 2026, Planethunt: archival evidence for the KIC "
            "5689121") in note
    assert "This note reports a transit candidate" not in note
    abstract = note.split(r"\begin{abstract}")[1].split(r"\end{abstract}")[0]
    body = note.split(r"\end{abstract}")[1].split(r"\begin{thebibliography}")[0]
    assert "This note vets the transit candidate Kuber Mehta reported first on KIC 6146676" in abstract
    assert PRIOR_DOI not in abstract and "13 October" not in abstract
    assert body.count(PRIOR_DOI) == 1 and body.count("13 October 2026") == 1
    assert "It remains a candidate pending follow-up." in abstract
    assert "It remains a candidate pending follow-up." not in body
    assert note.index("]{gaia2023}") < note.index("]{mann2019}") < note.index("]{mehta2026}")      # alphabetical
    assert note_problems(note_words(note), note) == []
    step2 = texts["README.md"].split("## Step 2.")[1].split("## Step 3.")[0]
    assert "record of priority" in step2 and PRIOR_DOI in step2 and "(a re-detection)" in step2
    assert "same host assignment" not in texts["README.md"]          # round 1: the texts cite the record only
    assert "SkySift vetting of the transit candidate on " in texts["exofop/note.txt"]
    assert "first reported" in texts["archive_email.txt"]
    assert "re-detected it in its sweep of the KBonus Kepler Background light curves" in texts["archive_email.txt"]
    # the pixel section's comparison with the hand-built localisation is the README's alone (spec 11.5)
    for name in ("note/note.tex", "exofop/note.txt"):
        assert "hand-built localisation" not in texts[name], name
    assert "hand-built localisation" in texts["README.md"] and "within 0.03 px" in texts["README.md"]
    planet = _parse_pipe((rep.out_dir / "exofop" / f"params_planet_{_today()}_001.txt").read_text(encoding="utf-8"),
                         PLANET_COLUMNS)
    assert planet["paper"] == f"https://doi.org/{PRIOR_DOI}" and PRIOR_DOI in planet["notes"]
    assert planet["notes"].startswith("SkySift vetting of the signal first reported in doi:")
    assert "a re-detection" in planet["notes"] and len(planet["notes"]) <= exofop.EXOFOP_NOTES_MAX
    # item 10 of Task 9: the Zenodo description names the claim in the README's own words, with its DOI and date
    description = json.loads((rep.out_dir / "zenodo_metadata.json").read_text(encoding="utf-8"))["description"]
    prior = pack_module.prior_claim_sentence(env.data)
    assert f"Prior claim: {prior}" in texts["README.md"]
    assert f"<p>Prior claim: {zenodo._html(prior)} {zenodo._html(comparison)}</p>" in description
    assert PRIOR_DOI in description and "13 October 2026" in description
    assert "a re-detection" in description and not re.search(r"\bblind\b", description, re.IGNORECASE)


def test_without_a_prior_claim_nothing_speaks_of_a_vetting_of_a_claim(env_with_dossier):
    env = env_with_dossier
    assert env.data["prior_claims"] == [] and env.data["hand_built_localisation"] is None
    texts = _texts(_build(env))
    for name, text in texts.items():
        assert "re-detection" not in text and "SkySift vetting of" not in text and "Against that claim" not in text
        assert "record of priority" not in text and PRIOR_DOI not in text, name
        assert "hand-built localisation" not in text, name
    assert r"\title{A " in texts["note/note.tex"]
    assert note_problems(note_words(texts["note/note.tex"]), texts["note/note.tex"]) == []


def test_a_version_3_dossier_without_the_key_reads_as_no_claim(env_with_dossier):
    data = {k: v for k, v in env_with_dossier.data.items() if k not in ("prior_claims", "hand_built_localisation")}
    assert pack_module.prior_claim_sentence(data) is None and "SkySift vetting of" not in render_note(data)
    assert pack_module.prior_comparison(data) is None and pack_module.comparison_text(data) is None
    assert "record of priority" not in render_readme(data, {})


LIVE_5689121 = {"ephemeris": {"period_days": 13.769414, "period_err_days": 4.7713013e-05, "depth_ppm": 2031.0,
                              "depth_ppm_err": 113.0},
                "timing": {"ok": True, "t0": 136.314294, "t_ref": 797.246185, "t_ref_err": 0.0014377853,
                           "epoch_ref": 48, "period": 13.769414, "period_err": 4.7713013e-05},
                "fpp": {"value": 0.0015958986, "signed_off": True},
                "prior_claims": [{k: PRIOR_CLAIM[k] for k in dossier_module.PRIOR_CLAIM_PUBLIC}]}


def test_prior_comparison_gives_each_difference_in_sigma():
    cmp = pack_module.prior_comparison(LIVE_5689121)
    assert cmp["period"]["sigma"] == pytest.approx(3.4e-5 / 4.7713013e-05, rel=1e-3)        # 0.71
    assert cmp["epoch"]["epoch"] == 48
    assert cmp["epoch"]["sigma"] == pytest.approx(0.000755 / 0.0014377853, rel=1e-3)          # 0.53
    assert cmp["depth"]["sigma"] == pytest.approx(148 / math.hypot(113, 106), rel=1e-3)       # 0.96
    assert cmp["one_sided"] == ["period", "epoch"] and cmp["fpp"]["theirs"] == 0.07
    assert pack_module.comparison_text(LIVE_5689121) == (
        "Against that claim the period differs by 0.7 sigma, the mid-transit time at epoch 48 by 0.5 sigma and the "
        "depth by 1.0 sigma (1,883 ± 106 ppm there; the claim gives no error on the period or the epoch, so those "
        "use ours alone); its false-positive probability of 0.07 (TRICERATOPS) sits beside our 0.0016 (SkySift's "
        "four-scenario estimate).")


def test_a_quantity_the_claim_does_not_give_is_left_out():
    claim = {**LIVE_5689121["prior_claims"][0], "t0_bjd": None, "depth_ppm": None, "fpp": None,
             "period_err_days": 2e-5}
    data = {**LIVE_5689121, "prior_claims": [claim]}
    cmp = pack_module.prior_comparison(data)
    assert set(cmp) == {"period", "one_sided"} and cmp["one_sided"] == []
    assert cmp["period"]["sigma"] == pytest.approx(3.4e-5 / math.hypot(4.7713013e-05, 2e-5), rel=1e-3)
    assert pack_module.comparison_text(data) == "Against that claim the period differs by 0.7 sigma."


def test_the_live_gate_refuses_a_claim_without_a_relationship_and_the_forbidden_words(env_prior):
    data = _stored(env_prior)
    claim = data["prior_claims"][0]
    no_rel = {**data, "prior_claims": [{**claim, "relationship": None}]}
    assert ("prior claim doi:10.5281/zenodo.23325261 has no relationship (re-detection, blind-recovery or "
            "vetting-only)") in pack_module.gate_problems(no_rel)
    blind = {**data, "prior_claims": [{**claim, "development_note": "a blind search found it",
                                       "development_note_short": "a blind search found it"}]}
    assert "the Research Note says 'blind', which spec 11.1 rules out" in pack_module.gate_problems(blind)
    assert not [p for p in pack_module.gate_problems(data) if "relationship" in p or "spec 11.1" in p]


def test_the_fpp_headline_leads_with_triceratops(env_with_dossier):
    env = env_with_dossier
    _store_errors(env)
    _store_cross_check(env)
    data = _stored_with_cross_check(env)
    abstract = pack_module.abstract_text(data)
    assert abstract.index("TRICERATOPS") < abstract.index("a four-scenario estimate without a bound-companion")
    # the abstract's short class clause (Task 13, addendum cut 8); Task 15 fix round 1, cut A1: the abstract
    # carries the FPPs alone, the no-STP values are the body's, and the gate keys on the headline's opening
    assert "(no validation class applies at 30-minute cadence; Giacalone et al. 2021)" in abstract
    assert "without a planet on an unresolved bound companion" not in abstract
    assert not [p for p in pack_module.gate_problems(data) if "in-house false-positive probability without" in p]
    assert f"gives {cross_check.probability_text(data['fpp']['value'])}, a lower bound" in abstract
    texts = _texts(_build(env))
    assert all("answer different questions" not in t for t in texts.values())
    # spec 12.2: the note gives the four numbers and what carries the weight once the excluded neighbours are removed
    note = " ".join(texts["note/note.tex"].split())
    assert str(cross_check.comparison_sentence(data, tex=True, short=True)) in note
    # addendum 2: the own-evidence sentence takes the dominant-excluded sentence's place in the note
    assert str(cross_check.dominant_excluded_sentence(data, tex=True, short=True)) not in note
    assert cross_check.own_evidence_sentence(data, short=True) in note          # fix round 1, B2
    assert "a run without them is a follow-up" not in note
    assert cross_check.comparison_sentence(data, after_headline=True) in texts["README.md"]
    for name in ("exofop/note.txt", "archive_email.txt"):
        assert "with the one neighbour the difference images exclude removed" in texts[name], name


def test_the_gate_refuses_an_abstract_with_the_in_house_fpp_alone(env_with_dossier, monkeypatch):
    env = env_with_dossier
    _store_errors(env)
    _store_cross_check(env)
    monkeypatch.setattr(cross_check, "headline", lambda data, tex=False, named=True, short=False: None)
    assert ("the abstract carries the in-house false-positive probability without the TRICERATOPS one"
            in pack_module.gate_problems(_stored_with_cross_check(env)))


def test_the_fpp_order_gate_keys_on_our_own_headline_not_on_a_prior_claims_naming_the_tool(env_with_dossier,
                                                                                         monkeypatch):
    """Polish round 2: only the prior-claim sentence names TRICERATOPS (its false-positive probability of 0.07
    (TRICERATOPS)), and our own "TRICERATOPS 1.1.0 gives" sentence is gone: the gate refuses the Zenodo description
    (it passed before, on the first word TRICERATOPS anywhere)."""
    claim = "its false-positive probability of 0.07 (TRICERATOPS) sits beside our 0.0016 (SkySift's four-scenario"
    assert not pack_module._triceratops_first(f"Prior claim: {claim} estimate). The tool gives 0.012.", "1.1.0")
    assert not pack_module._triceratops_first(f"Prior claim: {claim} estimate).", "1.1.0")
    own = "False-positive probability: TRICERATOPS 1.1.0 gives a false-positive probability of 0.012"
    assert pack_module._triceratops_first(f"Prior claim: {claim} estimate). {own}; the four-scenario estimate", "1.1.0")
    assert pack_module._triceratops_first(f"Prior claim: its probability (TRICERATOPS). {own}.", "1.1.0")
    assert not pack_module._triceratops_first(f"A four-scenario estimate gives 0.1. {own}.", "1.1.0")    # in-house first
    assert not pack_module._triceratops_first(own, "1.0.0")           # another version's sentence is not ours
    env = env_with_dossier
    _store_errors(env)
    _store_cross_check(env)
    data = {**_stored_with_cross_check(env),
            "prior_claims": [{k: PRIOR_CLAIM[k] for k in dossier_module.PRIOR_CLAIM_PUBLIC}]}
    assert "TRICERATOPS" in pack_module.zenodo_prior(data)
    refusal = "carries the in-house false-positive probability without the TRICERATOPS one"
    assert not [p for p in pack_module.gate_problems(data) if refusal in p]
    monkeypatch.setattr(cross_check, "headline", lambda data, tex=False, named=True, short=False: None)
    problems = pack_module.gate_problems(data)
    assert "the Zenodo description " + refusal in problems and "the abstract " + refusal in problems


def test_the_gate_refuses_a_151_word_abstract_under_the_total(env_with_dossier, monkeypatch):
    """M8: the gate refuses on every RNAAS limit note_problems names, the abstract's 150 words included, not on the
    total alone."""
    env = env_with_dossier
    _store_errors(env)
    _store_cross_check(env)
    data = _stored_with_cross_check(env)
    real = pack_module.render_note
    words = note_words(real(data))
    assert words["abstract"] <= NOTE_LIMITS["abstract"]
    assert words["total"] + 151 - words["abstract"] <= NOTE_LIMITS["total"]        # the total stays inside
    pad = " ".join(["more"] * (151 - words["abstract"]))
    monkeypatch.setattr(pack_module, "render_note", lambda raw: real(raw).replace(r"\begin{abstract}",
                                                                                  r"\begin{abstract}" + pad + " ", 1))
    assert note_words(pack_module.render_note(data))["abstract"] == 151
    assert ("the Research Note breaks an RNAAS limit: the abstract has 151 words, over the limit of 150"
            in pack_module.gate_problems(data))
    monkeypatch.setattr(pack_module, "render_note", real)
    assert not [p for p in pack_module.gate_problems(data) if "RNAAS" in p]


# ---------------------------------------------------------------------------------------------------- fix round 1

def test_no_text_states_the_status_of_the_prior_claim(env_prior):
    """Round 1, item 4: the public texts cite the claim's author, title, DOI and date only, never the status of the
    claimant's submission; the dossier JSON, which travels with the pack, does not carry it either."""
    env = env_prior
    assert all("status" not in c for c in env.data["prior_claims"])
    rep = _build(env, tic="169822650")
    texts = {**_texts(rep), "zenodo_metadata.json": (rep.out_dir / "zenodo_metadata.json").read_text(encoding="utf-8")}
    planet = next(rep.out_dir.glob("exofop/params_planet_*.txt"))
    texts["planet"] = planet.read_text(encoding="utf-8")
    for name, text in texts.items():
        assert "reported not accepted" not in text and "Research Note submitted" not in text, name
        assert "submitted" not in text, name
    assert PRIOR_DOI in texts["zenodo_metadata.json"]
    # a dossier stored before the status was dropped prints none of it either
    old = {**env.data, "prior_claims": [{**env.data["prior_claims"][0], "status": "submitted, not accepted"}]}
    for text in (render_note(old), render_readme(old, {}), pack_module.prior_claim_sentence(old),
                 pack_module.archive_paragraph(old, "10.5281/zenodo.1", None)):
        assert "submitted, not accepted" not in text


def test_the_hand_built_sentence_says_differs_beyond_the_combined_error():
    """Round 1, item 8."""
    block = {"max_diff_pix": 0.12, "n_seasons": 4, "agrees": False, "combined_err_pix": 0.05, "source": "x.json"}
    words = pack_module.hand_built_sentence({"hand_built_localisation": block})
    # fix round 4: one clause, every number and the source kept
    assert words == ("The centroid differs from the hand-built localisation by up to 0.12 px over 4 seasons, more "
                     "than the seasons' combined error (x.json).")
    assert "agrees" not in words
    words = pack_module.hand_built_sentence({"hand_built_localisation": {**block, "max_diff_pix": 0.03,
                                                                          "agrees": True}})
    assert words == "The centroid agrees with the hand-built localisation within 0.03 px over 4 seasons (x.json)."


OFFSET_FIT = {"self_referenced_arcsec": 0.39, "self_referenced_err_arcsec": 0.15, "sigma": 2.6, "counts": None,
              "reference": "the catalogue position plus the two-source fit's common shift",
              "catalogue_referenced_arcsec": 0.25, "catalogue_referenced_err_arcsec": 0.28, "level_arcsec": 0.2,
              "sigma_level": 3.0, "rule": "Bryson et al. 2013", "n_seasons": 4}


def test_the_offset_sentence_names_its_reference():
    """Round 1, item 3b: only a direct-image reference is described as the star's own image and judged by Bryson's
    rule (and cited); the fit's fallback is named and not assessed. Both say the errors share one rule."""
    fit = pack_module.offset_sentence({"centroid_offset": OFFSET_FIT})
    assert fit.startswith("Against the Gaia position") and (". Against the catalogue position plus the two-source "
                                                             "fit's common shift (the star's direct-image centroid is "
                                                             "not stored for every season)") in fit
    assert "not assessed from the star's own image" in fit and "Bryson" not in fit
    assert fit.endswith("Both errors are the larger of the seasons' combined error and their scatter.")
    assert pack_module.references({"centroid_offset": OFFSET_FIT})["bryson2013"] is False
    own = {**OFFSET_FIT, "reference": "the star's own direct-image centroid", "counts": False}
    direct = pack_module.offset_sentence({"centroid_offset": own})
    assert ("Against the star's own out-of-transit image the difference-image centroid is offset by 0.39 ± 0.15 "
            "arcsec (2.6 sigma), not an offset by the rule of Bryson et al. (2013)") in direct
    assert pack_module.references({"centroid_offset": own})["bryson2013"] is True
    assert r"\citet{bryson2013}" in str(pack_module.offset_sentence({"centroid_offset": own}, tex=True))


def test_one_precision_rule_on_the_page_and_in_the_pack():
    """Round 1, item 6: a stored error of 0.1 prints as 0.1 and one of 5.0 as 5, in the pack and on the page."""
    from skysift.ui.routes.dossiers import err_digits as page_digits
    assert pack_text.pm_digits(0.1) == page_digits(0.1) == 1 and pack_text.pm_digits(5.0) == page_digits(5.0) == 0
    assert pack_text.pm_text(2.3, 0.1, 0.1) == "2.3 ± 0.1" and pack_text.pm_text(73.0, 5.0, 5.0) == "73 ± 5"
    assert page_digits(0.016) == pack_text.pm_digits(0.016) == 3 and page_digits(None) == 2
    js = (Path(pack_module.__file__).parent / "ui" / "static" / "dossier.js").read_text(encoding="utf-8")
    assert "Math.min(Math.max(0, 1 - Math.floor(Math.log10(err))), places(err))" in js


# ---------------------------------------------------------------------------------------------------- Task 8: TESS

def _with_tess(data: dict) -> dict:
    from skysift import tess
    from tests.unit.ui_seed import SEED_TESS
    return {**data, "tess": tess.statement(SEED_TESS, data["ephemeris"])}


def _cites(tex: str) -> tuple[set, set]:
    cited = {k.strip() for c in re.findall(r"\\cite[pt]?(?:\[[^\]]*\]){0,2}\{([^}]+)\}", tex) for k in c.split(",")}
    return cited, set(re.findall(r"\\bibitem\[[^\]]*\]\{([^}]+)\}", tex))


def test_without_tess_inputs_the_texts_say_it_was_not_assessed_and_the_gate_does_not_block(env_with_dossier):
    env, data = env_with_dossier, env_with_dossier.data
    assert data["tess"] == {"available": False, "caveats": ["TESS inputs not fetched"]}
    tex = render_note(data)
    section = tex.split(r"\section{How likely is a false positive}")[1].split(r"\section{")[0]
    assert "TESS coverage was not assessed." in section
    cited, items = _cites(tex)
    assert cited == items and not {"christ2019", "stassun2018", "battley2021"} & items
    assert "| TESS | not assessed (TESS inputs not fetched) |" in render_readme(data, {})
    assert "TESS:" not in pack_module._render_plain("exofop_note.txt.j2", data, {})
    assert not [p for p in pack_module.gate_problems(_stored_with_cross_check(env)) if "TESS" in p]


def test_the_texts_carry_the_tess_statement(env_with_dossier):
    from skysift import tess
    data = _with_tess(env_with_dossier.data)
    block = data["tess"]
    assert block["verdict"] in tess.VERDICTS
    tex = render_note(data)
    section = tex.split(r"\section{How likely is a false positive}")[1].split(r"\section{")[0]
    assert str(pack_module.tess_text(data, True)) in section.replace("\n", " ")
    # round 2, N1: the note keeps the one sentence, citing Christ et al. 2019 alone; the methods go to the README;
    # Task 13 fix round 1 (cut 11): its short form
    # Task 15 fix round 1, cut B3: "of S to S sigma means <verdict>", citing without a pointer
    assert r"; the predicted folded significance of " in section and r"\citep{christ2019}." in section
    assert "noise model and thresholds" not in section
    assert "The method follows" not in tex and "no TESS light curve was examined" not in tex
    cited, items = _cites(tex)
    assert cited == items                                                     # every citation listed, none unused
    assert "christ2019" in items and not {key for key, _w in tess.METHOD_CITES} - {"christ2019"} & items
    assert note_words(tex)["references"] < note_words(render_note(env_with_dossier.data))["references"] + 20
    words = note_words(tex)
    assert words["total"] <= NOTE_LIMITS["total"] and note_problems(words, tex) == []
    readme = render_readme(data, {})
    row = next(line for line in readme.splitlines() if line.startswith("| TESS | "))
    assert row == f"| TESS | {tess.readme_row(block)} |" and block["verdict"] in row
    section = readme.split("TESS prediction (the note's TESS sentence points here): ")[1].split("\n")[0]
    assert section.startswith(tess.methods_text(block)) and "7.1 or more" in section
    assert section.count("Christ et al. 2019") == 1 and section.count("Christ, C. N.") == 1   # round 3: named once
    assert ("References: Christ, C. N., Montet, B. T., & Fabrycky, D. C. 2019, AJ, 157, 235; Stassun, K. G.") in section
    assert "Twicken, J. D., Jenkins, J. M., Caldwell, D. A., et al. 2025, RNAAS, 9, 132" in section
    assert "~" not in section and "\\" not in section
    exofop_note = pack_module._render_plain("exofop_note.txt.j2", data, {})
    assert tess.exofop_line(block) in exofop_note.splitlines()
    archive = pack_module.archive_paragraph(data, "10.5281/zenodo.1", None)
    assert "TESS observed" not in archive and "TESS coverage" not in archive   # the archive email does not change
    for text in (tex, readme, exofop_note):
        assert EM_DASH not in text and not IZE.search(text) and "discover" not in text.lower()


def tess_text_plain(block: dict) -> str:
    from skysift import tess
    return str(tess.note_sentence(block, tex=True))


def test_the_golden_note_with_a_tess_statement_stays_inside_the_word_limit():
    """The live KIC 6146676 dossier (the version 4 golden) with the statement its own inputs give: the note with the
    sentence and its one reference stays inside the RNAAS 1,500 words and costs it under 100 (round 2, N1)."""
    golden = json.loads((Path(__file__).resolve().parents[1] / "golden" / "kic-006146676-generated.json")
                        .read_text(encoding="utf-8"))
    before = note_words(render_note(golden))["total"]
    words = note_words(render_note(_with_tess(golden)))
    assert words["total"] <= NOTE_LIMITS["total"] and words["total"] - before < 100


# the live shape's margin under the RNAAS 1,500 (fix round 2, N1): room for a caveat title, and for the 6 to 8 words
# a word processor counts over ``_count`` on the citations (fix round 3 review, M-5)
LIVE_SHAPE_LIMIT = 1480
LIVE_ABSTRACT_LIMIT = 148          # round 3 polish: two under the RNAAS 150; the ruled cut saves two counted words


def _live_shape(cross: str, prior: bool = True) -> dict:
    """The version 4 golden as a live draft has it (fix round 4, I-1): its own ``timing``, ``shape``, ``parity``,
    ``secondary``, ``centroid_offset`` (the star's own direct-image centroid, so the Bryson sentence and bibitem) and
    ``hand_built_localisation`` blocks; a prior claim unless ``prior`` is False; the worst TESS block, a skipped
    sector and an uncertain count (a period error that blurs the TESS epochs) with transits still in the windows, so
    the sentence carries its uncertain clause, the skipped clause and its citation; and a current cross-check in one
    of the two longest forms: ``cross="validated"`` (the validation-lines phrase, 23 words over the candidate phrase)
    or ``cross="dominant"`` (a full FPP at 0.6, the dominant-scenario sentence and two degenerate runs left out),
    each with the factor sentence."""
    from skysift import tess
    from tests.unit.test_crosscheck import REMOVED, fake_result, fake_variant
    from tests.unit.ui_seed import SEED_TESS
    data = json.loads(GOLDEN.read_text(encoding="utf-8"))
    # spec 12.2: the golden's own exclusion radius, so both long forms of the dominant-excluded sentence count
    assert data["centroid_offset"]["exclusion_radius_arcsec"] > 0
    assert data["dossier_version"] == dossier_module.DOSSIER_VERSION
    assert data["timing"]["ok"] is True and data["shape"]["ok"] is True
    assert pack_module.self_referenced(data["centroid_offset"]) and data["hand_built_localisation"]["agrees"]
    if prior:
        data["prior_claims"] = [{k: PRIOR_CLAIM[k] for k in dossier_module.PRIOR_CLAIM_PUBLIC}]
    meta = {**SEED_TESS, "sectors": [*SEED_TESS["sectors"], 109]}
    data["tess"] = tess.statement(meta, {**data["ephemeris"], "period_err_days": 0.002})
    if cross == "validated":
        block = fake_result(cross_check.inputs_fingerprint(data), fpp=0.012, nfpp=1e-4,
                            variant=fake_variant(fpp=0.004))
    else:
        block = fake_result(cross_check.inputs_fingerprint(data), fpp=0.6, nfpp=0.002,
                            scenarios=[{"ID": "169822655", "scenario": "NEB", "prob": 0.55},
                                       {"ID": "169822650", "scenario": "TP", "prob": 0.4}],
                            variant=fake_variant(fpp=0.31, nfpp=0.0, p_stp=0.3, removed=REMOVED * 6))
        for run in block["runs"][:2]:                     # identical runs, so the medians do not move
            run["degenerate"] = True
        block["n_degenerate"] = 2
    data["cross_check"] = cross_check.with_current({**block, "current": True}, data)
    # Task 13 fix round 1 (I-2): what the golden leaves out and a live note prints: rung 1's row (the 24-word
    # single-event sentence, KIC 5689121's numbers), the caveats ``dossier.caveats`` stores with the error caveat
    # KIC 5689121 really has, and panel (d) of the figure
    data["tests"] = [*data["tests"], {"rung": "rung1_events", "status": "pass", "reason": "",
                                      "metrics": {"n_covered": 63, "n_events_with_data": 79,
                                                  "ses_max_over_mes": 0.33}}]
    data["caveats"] = [*dossier_module.caveats(data), SHAPE_OFFSET_CAVEAT]
    data["pixels"] = [{"season": s} for s in range(4)]
    return data


SHAPE_OFFSET_CAVEAT = {"key": "shape_offset", "title": "The shape fit sits off its bootstrap",
                       "text": "The full-curve radius ratio differs from the bootstrap median, the quoted value, by "
                               "more than its 1-sigma spread; quote the full-data value beside it."}


def _check_live_shape(cross: str, prior: bool) -> dict:
    """Render ``_live_shape`` and check it carries every long form it should; its ``note_words``."""
    from skysift import tess
    data = _live_shape(cross, prior)
    block = data["tess"]
    assert block["sectors_skipped"] == [109] and block["uncertain"] and block["n_transits"] > 0
    tex = render_note(data)
    flat = " ".join(tex.split())
    assert str(pack_module.cross_check_sentence(data, True)) in flat
    assert ("The full false-positive probability comes mainly from" in flat) == (cross == "dominant")
    assert ("2 degenerate runs left out" in flat) == (cross == "dominant")
    assert cross_check.NO_CLASS_SHORT in flat and "validation lines" not in flat
    # spec 12.2: the pixel-excluded set's two medians and the scenario that carries its weight
    v = data["cross_check"]["variant_excluded"]
    # Task 15 fix round 1, cuts B8 and B2
    assert f"; {cross_check.probability_text(v['fpp'])}, and {cross_check.probability_text(v['fpp_no_stp'])} " \
           "without STP, with the " in flat
    assert cross_check.own_evidence_sentence(data, short=True) in flat
    assert (str(pack_module.comparison_text(data, True)) in flat) == prior
    # Task 13 fix round 1 (cut 11): the note's short TESS sentence; the README's row keeps the skipped sector and
    # the uncertain count
    sentence = str(pack_module.tess_text(data, True))
    assert sentence in flat and sentence.endswith(r"\citep{christ2019}.")          # fix round 1, B3
    row = tess.readme_row(block)
    assert "Sector 109, not assessed" in row and "expected)" in row and "diluted depth" in row
    # M-3 said it once; Task 15 fix round 1 (B3, B6) leaves the note no README pointer: the Data availability
    # line names the pack's methods
    assert flat.count("the Zenodo pack's README") == 0 and "Pack (data and methods)" in flat
    # the version 4 sentences the round 3 test did not see (I-1)
    assert r"by the rule of \citet{bryson2013}" in flat and r"]{bryson2013} Bryson" in flat
    assert "hand-built" not in flat                                               # spec 11.5: the README's
    # the digits move with each regeneration of the golden, so the sentences are built from it (Task 10)
    assert str(pack_module.parity_sentence(data, True, stacked=False)) in flat     # the note: per event (Task 13)
    assert str(pack_module.secondary_sentence(data, True, short=True)) in flat
    assert (PRIOR_DOI in flat) == prior
    assert "largest single-event statistic is 0.33" in flat and "(d) per-season difference images" in flat
    assert "The shape fit sits off its bootstrap" in flat and "No spectrum" in flat
    words = note_words(tex)
    assert note_problems(words, tex) == [] or words["total"] > NOTE_LIMITS["total"]
    return words


@pytest.mark.parametrize("prior", [False, True], ids=["no-prior-claim", "prior-claim"])
@pytest.mark.parametrize("cross", ["validated", "dominant"])
def test_the_live_shape_stays_inside_the_word_limit_with_margin(cross, prior):
    """Fix rounds 2 to 4, N1 and I-1: the note as a live draft of the golden renders it (its own timing,
    shape, parity, secondary, pixel-offset and hand-built blocks, the longest cross-check forms, a TESS sentence with
    a skipped sector and an uncertain count, with and without a prior claim) stays at or under 1,480 words, 20 under
    the RNAAS limit, and the gate does not refuse it for its length. Measured in round 4: 1,376 and 1,384 without a
    prior claim, 1,439 and 1,447 with one (validated, dominant with two degenerate runs); with the version 5 golden
    (Task 10 fix round 1): 1,413 and 1,421 without, 1,476 and 1,484 with one. Round 2 added words (up to 1,787 with
    Task 13's additions) and Task 13 made the cuts, so the margin is a hard assertion again: 1,330 and 1,358
    without a prior claim, 1,426 and 1,454 with one, before the golden carries the star's Gaia flags. Task 13 fix
    round 1 (I-2) gives the shape the rung 1 row, the stored caveats with an error caveat and panel (d), which a
    live note carries and the golden leaves out, and its cuts 10 to 14 hold the 1,480 margin on that shape: 1,348
    and 1,376 without a prior claim, 1,447 and 1,475 with one (validated, dominant). The round-2 final review's
    additions (I-1, minors 4, 5 and 9, the skipped sector) and its pointer cut (the errors pointer as a clause, the
    Pack line without its gloss): 1,347 and 1,375 without, 1,451 and 1,479 with one. I-1: the abstract carries the
    sweep-wide expectation and stays at or under 150 words on every shape (144 without a prior claim, 148 with).
    Task 15 (the pixel-excluded variant and addendum 2) took the shapes to 1,390 and 1,418 without a prior claim and
    1,494 and 1,522 with one, abstracts 156 and 160; its fix round 1 cuts (A1, B1 to B8): 1,344 and 1,372 without,
    1,448 and 1,476 with one; abstracts 146 without a prior claim, 150 with. Task 16 (the planet-radius bracket up to
    the full-column E(B-V), the caveat title) took the longest to 1,508; its cut 1 (no GSP-Phot sentence in the note):
    1,335 and 1,363 without, 1,439 and 1,467 with one; abstracts unchanged. Task 15 fix round 2, M2 (the excluded
    set's dominant scenario in the own-evidence sentence, two words): 1,337 and 1,365 without, 1,441 and 1,469 with
    one; abstracts 146 and 150. The round 3 final review (the bracket with the Mann flag, I2's own-evidence sentence,
    no second unit and no second "E(B-V) of" in the bracket), on the golden of 09ee6e3: 1,347 and 1,375 without, 1,451
    and 1,479 with one; abstracts 146 and 150.
    Round 3 polish: the closing sentence "pending follow-up" (the live KIC 5689121 abstract was 151): 144 without a
    prior claim, 148 with one, held at or under LIVE_ABSTRACT_LIMIT."""
    words = _check_live_shape(cross, prior)
    assert words["total"] <= LIVE_SHAPE_LIMIT, words
    assert words["abstract"] <= LIVE_ABSTRACT_LIMIT <= NOTE_LIMITS["abstract"] - 2, words
    abstract = " ".join(render_note(_live_shape(cross, prior)).split(r"\begin{abstract}")[1].split())
    assert ("Null searches give a false-alarm rate of 1 in 170 per star, about 9 such peaks expected across the "
            "1,579-star sweep;") in abstract
    assert not [p for p in pack_module.gate_problems(_live_shape(cross, prior)) if "words" in p]


@pytest.mark.parametrize("cross", ["validated", "dominant"])
def test_the_abstract_keeps_inside_150_words_with_the_kic_5689121_clauses(cross):
    """Round 3 polish (P1): the live pack of KIC 5689121 rendered a 151-word abstract, which the four synthetic live
    shapes (at most 150 before the closing sentence lost two counted words) did not catch. Its longer clauses, put on the
    longest shape: three seasons that voted beside one that did not, a period of 13.769449 days over 65 transits,
    and a false-alarm rate of 1 in 51 per star with about 31 such peaks across the sweep; two of the voting seasons
    also carry the box-cut clause, so the abstract is the longest the clauses allow."""
    data = _live_shape(cross, True)
    data["ephemeris"].update({"period_days": 13.769449, "n_events": 65})
    row = next(t for t in data["tests"] if t["rung"] == "rung5_pixels")
    row["metrics"]["seasons"] = [
        {"season": n, "n_events": 9, "dist_target_pix": 0.15, "dist_neighbour_pix": 2.8, "centroid_sigma_pix": 0.06,
         "nearer": "target", "depth_target_ppm": [2800.0, 600.0], "depth_neighbour_ppm": [-20.0, 50.0],
         "two_source": True, "voting": True, "non_voting_reason": None} for n in (0, 1, 2)
    ] + [{"season": 3, "n_events": 9, "nearer": "unknown", "depth_target_ppm": [None, None],
          "depth_neighbour_ppm": [None, None], "two_source": False, "voting": False,
          "non_voting_reason": "box incomplete: target"}]
    for s in row["metrics"]["seasons"][:2]:                 # the longest abstract clause, as the shapes carry it
        s.update({"box_partial": True, "box_pixels_target": 6, "box_pixels_neighbour": 9, "edge_bias_pix": 0.1})
    data["fap"].update({"words": "1 in 51", "fap": 1 / 51})
    data["calibration"]["sweep_size"] = 1579
    tex = render_note(data)
    abstract = " ".join(tex.split(r"\begin{abstract}")[1].split(r"\end{abstract}")[0].split())
    assert "3 of 3 seasons that voted" in abstract
    assert "13.76945 days over 65 transits" in abstract            # 13.769449 at the abstract's five decimals
    assert "1 in 51 per star, about 31 such peaks" in abstract
    assert abstract.endswith("It remains a candidate pending follow-up.")
    words = note_words(tex)
    assert words["abstract"] <= NOTE_LIMITS["abstract"], (cross, words)
    print(f"KIC 5689121 clauses, {cross}: abstract {words['abstract']} words")


def test_the_body_does_not_repeat_the_abstract_and_keeps_every_number():
    """Fix round 4, I-1: the signal section opens on the light curve, the star's Gaia DR3 id and G magnitude and the
    dip, not on the abstract's sentence again; nothing the old opening carried is lost."""
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    tex = render_note(golden)
    signal = " ".join(tex.split(r"\section{The signal}")[1].split(r"\section{")[0].split())
    assert signal.startswith(r"In the KBonus Kepler Background light curve \citep{kbonus} of KIC 6146676 (Gaia DR3 "
                             f"{golden['target']['gaia_id']}, $G = 16.2$), recorded by Kepler "
                             r"\citep{borucki2010} only in other targets' pixels, a dip of ")
    assert "This note reports" not in signal and "recorded only in the background" not in signal
    abstract = tex.split(r"\begin{abstract}")[1].split(r"\end{abstract}")[0]
    assert abstract.startswith("\nThis note reports a transit candidate on KIC 6146676, a star Kepler recorded only "
                               "in other targets' pixels.")
    q = pack_text.quantities(golden, tex=True)
    for value in (q["depth_pm"], q["duration_pm"], q["period_pm"], q["t_ref_bjd_pm"]):
        assert str(value) in signal


def test_the_short_offset_sentence_keeps_both_offsets_and_the_verdict():
    """Fix round 4, I-1: the note's offset sentence (``short``) keeps the two offsets with their errors, the sigma,
    the reference, Bryson's rule and the verdict; the fallback against the fit's position keeps its own reference
    and is not judged by the rule. The long form stays the README's, the ExoFOP note's and the page's."""
    own = {**OFFSET_FIT, "reference": "the star's own direct-image centroid", "counts": False}
    short = pack_module.offset_sentence({"centroid_offset": own}, short=True)
    # spec 11.5: the Gaia offset and the exclusion radius (max(3 x 0.28, 0.5) arcsec) lead
    # Task 13: one clause on the star's own image, the error rule and the reference's carriage the README's
    gaia = ("Against the Gaia position the difference-image centroid sits 0.25 ± 0.28 arcsec away, with an exclusion "
            "radius of 0.84 arcsec (max(3 sigma, 0.5 arcsec)) within which a background source is not excluded")
    assert short == (gaia + "; against the star's own out-of-transit image the offset is 0.39 ± 0.15 arcsec (2.6 "
                     "sigma), not an offset by the rule of Bryson et al. (2013) (over 3 sigma and 0.2 arcsec).")
    counted = pack_module.offset_sentence({"centroid_offset": {**own, "counts": True}}, tex=True, short=True)
    assert r"(2.6 sigma), an offset by the rule of \citet{bryson2013} (over 3 sigma and 0.2 arcsec)." in str(
        counted)
    fit = pack_module.offset_sentence({"centroid_offset": OFFSET_FIT}, short=True)
    assert fit == (gaia + ". Against the catalogue position plus the two-source fit's common shift (the star's "
                   "direct-image centroid is not stored for every season) the difference-image centroid is offset by "
                   "0.39 ± 0.15 arcsec (2.6 sigma), not assessed from the star's own image.")
    assert len(short.split()) < len(pack_module.offset_sentence({"centroid_offset": own}).split()) - 5
    assert pack_module.offset_sentence({"centroid_offset": own}).endswith(pack_module.SAME_ERROR_RULE)


def test_the_note_says_once_which_readme_it_points_to(env_with_dossier):
    """Fix round 4, M-3: the first pointer of the note names the Zenodo pack's README, the errors pointer when
    the note has one, else the TESS sentence's; a later pointer says "the README". Task 15 fix round 1 (cuts B3 and
    B6) drops both pointers: the note names the README nowhere, and its Data availability line reads "Pack (data and
    methods)", with or without the errors blocks."""
    from skysift import tess
    data = _with_tess(_with_errors(env_with_dossier.data))
    assert pack_module.errors_pointer(data) and tess.note_cites(data["tess"])
    bare = {**data, "timing": {"ok": False, "reason": "x"}, "shape": {"ok": False, "reason": "y"}}
    for shape in (data, bare):
        flat = " ".join(render_note(shape).split())
        assert "README" not in flat and "Pack (data and methods): " in flat
        assert r"\citep{christ2019}" in flat


def test_the_readme_labels_the_tess_paragraph_by_what_the_note_says(env_with_dossier):
    """Fix round 4, M-2: "TESS prediction (the note's TESS sentence points here)" only when the note's sentence
    carries the pointer; with counted sectors but no transit in a window the paragraph says how the windows were
    counted, and the note points nowhere."""
    from skysift import tess
    data = env_with_dossier.data
    predicted = _with_tess(data)
    assert tess.note_cites(predicted["tess"])
    readme = render_readme(predicted, {})
    assert "TESS prediction (the note's TESS sentence points here): " in readme
    assert "TESS coverage (how the windows were counted)" not in readme
    eph = {**data["ephemeris"], "period_days": 400.0, "t0_bjd": 2457000.0 + 1650.0}
    from tests.unit.ui_seed import SEED_TESS
    zero = {**data, "tess": tess.statement({**SEED_TESS, "sectors": [14, 15]}, eph)}
    assert zero["tess"]["n_transits"] == 0 and not tess.note_cites(zero["tess"]) and zero["tess"]["methods"]
    readme = render_readme(zero, {})
    assert "TESS prediction" not in readme and "the note's TESS sentence points here" not in readme
    assert f"TESS coverage (how the windows were counted): {tess.methods_text(zero['tess'])}" in readme
    assert "README" not in str(tess.note_sentence(zero["tess"], tex=True))


def test_the_live_gate_refuses_a_note_over_the_word_limit(env_with_dossier):
    """Task 8 fix round 1, I3(a): a rendered note over 1,500 words blocks the live draft, and the sentence and the
    README both name the count."""
    ok = _stored_with_cross_check(env_with_dossier)
    assert not [p for p in pack_module.gate_problems(ok) if "words" in p]
    long = {**ok, "caveats": [{"title": "A caveat of many words " + "word " * 1500, "text": "Its detail."}]}
    n = note_words(render_note(long))["total"]
    assert n > NOTE_LIMITS["total"]
    assert (f"the Research Note has {n:,} words, over the RNAAS limit of 1,500; shorten it first"
            in pack_module.gate_problems(long))
    readme = render_readme(long, {})
    assert f"`note/figure1`: {n} words in all" in readme and "**Over the limit:**" in readme



# ---------------------------------------------------------------------------------------------------- fix round 2

def test_the_hand_built_sentence_without_an_error_gives_no_verdict():
    """Round 2, M2: a block stored before ``agrees`` existed, or one without a combined error, says only how far."""
    old = {"max_diff_pix": 0.03, "n_seasons": 4, "source": "x.json"}
    for block in (old, {**old, "agrees": None, "combined_err_pix": None}, {**old, "agrees": False,
                                                                           "combined_err_pix": 0.0}):
        words = pack_module.hand_built_sentence({"hand_built_localisation": block})
        assert words.endswith("by up to 0.03 px over 4 seasons (the error is not available; x.json).")
        assert "agrees" not in words and "combined error" not in words


def test_the_pack_embeds_only_the_public_fields_of_a_prior_claim(env_prior):
    """Round 2, M1: a stored dossier that still carries the claim's status and note (code before round 2) is cut at
    pack time: the dossier.html in the pack and its ExoFOP copy embed neither."""
    env = env_prior
    path = Path(env.store.dossier(STAGE2_TARGET)["data_path"])
    path.write_text(json.dumps({**env.data, "prior_claims": [PRIOR_CLAIM]}), encoding="utf-8")
    rep = _build(env)
    for page in [rep.out_dir / "dossier.html", *rep.out_dir.glob("exofop/*_dossier.html")]:
        text = page.read_text(encoding="utf-8").replace(chr(92) + "/", "/")     # the JSON escapes every slash
        assert PRIOR_DOI in text and "reported not accepted" not in text and "sweep a week later" not in text, page


# ---------------------------------------------------------------------------------------------------- dossier version 5


def test_events_and_transit_times_list_the_same_epochs_and_the_readme_says_how_many(env_with_dossier):
    rep = _build(env_with_dossier)
    events, times = _rows(rep, "data/events.csv"), _rows(rep, "data/transit_times.csv")
    assert [r["epoch"] for r in events] == [r["epoch"] for r in times]
    assert _header(rep, "data/events.csv") == ["epoch", "t_mid_bkjd", "quarter", "depth_ppm", "err_ppm",
                                               "coverage", "ses", "timed", "timing_reason"]
    # fix round 1, M-4: the ses column is the statistic the timing cut read, so it never contradicts the reason
    stored = {t["epoch"]: t for t in env_with_dossier.data["timing"]["times"]}
    for r in events:
        ses = stored[int(r["epoch"])]["ses"]
        assert (r["ses"] == "") if ses is None else (float(r["ses"]) == pytest.approx(ses, rel=1e-6)), r
        if r["ses"] != "":
            assert (r["timing_reason"] == ephemeris.LOW_SES_REASON) == (float(r["ses"]) < ephemeris.SES_MIN), r
    readme = (rep.out_dir / "README.md").read_text(encoding="utf-8")
    used = sum(r["used"] == "True" for r in times)
    assert f"`data/events.csv`: the {len(events)} transits with data" in readme
    assert f"the same {len(times)} epochs as `data/events.csv`, {used} of them used" in readme


def test_one_a_over_r_in_the_texts_and_both_in_the_ephemeris_csv(env_with_dossier):
    rep = _build(env_with_dossier)
    rows = {r["parameter"] for r in _rows(rep, "data/ephemeris.csv")}
    assert {"a/R* from the stellar parameters (Kepler's third law)", "a/R* fitted (free fit)"} <= rows
    copy = next(p for p in (rep.out_dir / "exofop").iterdir() if p.name.endswith("ephemeris.csv"))
    assert copy.read_bytes() == (rep.out_dir / "data" / "ephemeris.csv").read_bytes()
    # fix round 1, M-3: every a/R* with a value after it (the note writes the value between "a/R*" and its label), in
    # a dossier whose bootstrap gives the shape sentence its geometry
    _store_errors(env_with_dossier)
    texts = _texts(_build(env_with_dossier))
    valued = re.compile(r"a/R\*[^\n.;,]{0,60}?\d")
    # round-2 final review, I-4: the README's shape-fit sentence gives the free fit's a/R* beside the quoted one as
    # the evidence for the shape call; every other text keeps one
    shape_fit = pack_module.shape_fit_text(_stored(env_with_dossier))
    assert shape_fit is None or shape_fit in texts["README.md"]
    texts = {**texts, "README.md": texts["README.md"].replace(shape_fit or "", "")}
    counts = {name: valued.findall(texts[name])
              for name in ("README.md", "note/note.tex", "exofop/note.txt", "archive_email.txt")}
    assert len(counts["README.md"]) == len(counts["note/note.tex"]) == 1, counts
    assert max(len(found) for found in counts.values()) == 1, counts


def test_the_note_names_the_limb_darkening_and_the_exposure_integration(env_with_dossier):
    env = env_with_dossier
    _store_errors(env)
    flat = " ".join((_build(env).out_dir / "note" / "note.tex").read_text(encoding="utf-8").split())
    assert "quadratic limb-darkened model" in flat and "integrated over the 29.4-minute exposure" in flat
    assert r"\citep{claret2011}" in flat and r"Claret, A., \& Bloemen, S. 2011, A\&A, 529, A75" in flat
    assert "uniform-disc" not in flat and "without limb darkening" not in flat
    assert "on the linear-fit ephemeris" in flat                # the texts say which ephemeris folded
    readme = (_build(env).out_dir / "README.md").read_text(encoding="utf-8")
    k = _stored(env)["shape"]["k"]
    assert f"(full-data fit {k['point']:.4f})" in readme       # the point value beside the median


def test_the_centroid_paragraph_leads_with_the_gaia_offset_and_the_exclusion_radius():
    # the golden: the seed's rung 5 rows predate the direct-image centroid, so only it has both offsets and the pull
    data = json.loads(GOLDEN.read_text(encoding="utf-8"))
    co = data["centroid_offset"]
    text = pack_module.offset_sentence(data)
    gaia = text.index("Against the Gaia position")
    assert gaia == 0 and text.index("Against the star's own out-of-transit image") > gaia
    assert f"an exclusion radius of {co['exclusion_radius_arcsec']:.2f} arcsec (max(3 sigma, 0.5 arcsec))" in text
    assert "within which a background source is not excluded" in text
    if co["nearest_beyond"]:
        assert f"Gaia DR3 {co['nearest_beyond']['source_id']}" in text
    assert ("its reference is pulled by the neighbour" in text) is co["pulled"]
    assert "pointing-jitter floor" in pack_module.pixel_sentence(data) and "systematic floor" not in (
        pack_module.pixel_sentence(data))


def test_the_red_noise_parity_and_trend_sentences():
    data = {"tests": [{"rung": "rung2_shape", "status": "pass",
                       "metrics": {"sigma_pos": 7.98, "sigma_ter": 3.10, "fit_ok": True}}],
            "secondary": {"depth_ppm": 95.0, "err_ppm": 128.0, "limit_3sigma_ppm": 480.0, "strongest_ppm": 573.0,
                          "strongest_phase": 0.726, "strongest_sigma": 3.98},
            "parity": {"odd": {"depth_ppm": 2031.0, "err_ppm": 178.0, "n": 28},
                       "even": {"depth_ppm": 2031.0, "err_ppm": 138.0, "n": 27}, "difference_sigma": 0.002,
                       "stacked": {"odd": {"depth_ppm": 2050.0, "err_ppm": 120.0},
                                   "even": {"depth_ppm": 2010.0, "err_ppm": 115.0}, "difference_sigma": 0.24}},
            "depth_trend": {"slope_ppm_per_year": -61.0, "err_ppm_per_year": 25.0, "sigma": 2.44, "printed": True}}
    assert pack_module.secondary_sentence(data).endswith(
        "the strongest dip elsewhere is 573 ppm at phase 0.73 (4.0 sigma), against model-shift margins of 8.0 sigma "
        "for the largest positive excursion and 3.1 sigma for the tertiary dip.")
    # pm_text keeps every digit of an error of three digits (178, not 180); the brief's form rounded it
    assert pack_module.parity_sentence(data) == (
        "The odd and even depths are 2,031 ± 178 and 2,031 ± 138 ppm per event (0.0 sigma apart) and 2,050 ± 120 and "
        "2,010 ± 115 ppm on the stacked folds (0.2 sigma apart).")
    assert pack_module.trend_sentence(data) == (                 # Task 12 review M-2: the sign is in the verb
        "The per-event depth shallows by 61 ± 25 ppm per year (2.4 sigma, errors scaled by the scatter).")
    deeper = {**data, "depth_trend": {**data["depth_trend"], "slope_ppm_per_year": 228.0}}
    assert pack_module.trend_sentence(deeper).startswith("The per-event depth deepens by 228 ± 25 ppm per year")
    assert pack_module.trend_sentence({**data, "depth_trend": {**data["depth_trend"], "printed": False}}) is None


def test_the_hand_built_comparison_leaves_the_note_for_the_readme_and_its_file(env_prior):
    rep = _build(env_prior)
    texts = _texts(rep)
    assert "hand-built localisation" not in texts["note/note.tex"]
    assert ("The centroid agrees with the hand-built localisation within 0.03 px over 3 seasons "
            "(data/hand_built_localisation.json)") in texts["README.md"]            # the seed's three voting seasons
    assert pack_module.BEB_RADIUS in texts["README.md"]                  # the FPP's confusion radius, spec 11.5
    copied = rep.out_dir / "data" / "hand_built_localisation.json"
    source = dossier_module.CANDIDATES_DIR / STAGE2_TARGET / "localisation" / "results.json"
    assert copied.read_bytes() == source.read_bytes()


def test_the_note_names_the_photometry_the_aperture_ratio_and_the_predicted_leak(env_with_dossier):
    data = {**_stored(env_with_dossier),
            "aperture": {"ratio": 0.63466, "ratio_err": 0.064244, "crowdsap": 0.74724716, "shortfall_sigma": -1.7525},
            "predicted_leak": {"predicted_ppm": 61.2, "share": 0.0301, "measured_ppm": 130.4,
                               "measured_err_ppm": 41.8, "kic": 5689116, "reason": None}}
    flat = " ".join(render_note(data).split())
    assert "KBonus PSF photometry, decontaminated to first order, with no crowding correction applied" in flat
    assert pack_module.aperture_sentence(data) == (
        "The SAP depth is 0.635 ± 0.064 of the PSF depth against a CROWDSAP (this star's light fraction in the "
        "aperture) of 0.75, 1.8 sigma short of it.")
    assert flat.count("CROWDSAP") == 1                       # Task 12 review M-5: defined once, in the ratio sentence
    assert "a PSF flux fraction inside the aperture of" in flat and "PSFFRAC" not in flat    # fix round 1, B7
    assert pack_module.leak_sentence(data) == (       # the share and the method are the README's (addendum cut 5)
        "The target's light predicts 61 ppm in KIC 5689116's optimal aperture against 130 ± 42 ppm measured.")


# ---------------------------------------------------------------------------------------------------- Task 11 fix round 1

def test_the_prior_claim_words_have_one_source():
    """M2: dossier.py takes the prior-claim names from pack_text, and the standalone page prints the pack's sentences
    (dossier.page_texts) instead of a relationship map of its own."""
    assert dossier_module.RELATIONSHIPS is pack_text.RELATIONSHIPS
    assert pack_text.RELATIONSHIPS == tuple(pack_text.RELATIONSHIP_WORDS) == tuple(pack_text.RELATIONSHIP_VERBS)
    assert dossier_module.PRIOR_CLAIM_PUBLIC is pack_text.PRIOR_CLAIM_PUBLIC
    assert dossier_module.PRIOR_CLAIM_NUMBERS is pack_text.PRIOR_CLAIM_NUMBERS
    js = (dossier_module.STATIC_DIR / "dossier.js").read_text(encoding="utf-8")
    assert "D.page_texts" in js
    for key, words in pack_text.RELATIONSHIP_WORDS.items():
        assert words not in js and f'"{key}"' not in js, key


def test_the_sweep_size_joins_the_dossier_fingerprint():
    """M3: a dossier whose false-alarm sentence quotes a sweep size goes stale when the sweep grows."""
    tests = [{"id": 1}, {"id": 2}]
    base = dossier_module.fingerprint(tests, "candidate", None, 850, 1579)
    assert base == dossier_module.fingerprint(tests, "candidate", None, 850, 1579)
    assert base != dossier_module.fingerprint(tests, "candidate", None, 850, 1600)
    assert base != dossier_module.fingerprint(tests, "candidate", None, 850)
    assert dossier_module.fingerprint(tests, "candidate", None, 850) == dossier_module.fingerprint(
        tests, "candidate", None, 850, 0)


@pytest.mark.parametrize(("x", "words"), [(0.49, "fewer than one such peak is"), (0.5, "about one such peak is"),
                                          (1.49, "about one such peak is"), (2.5, "about 3 such peaks are"),
                                          (30.5, "about 31 such peaks are")])
def test_the_expected_peak_count_rounds_half_up(x, words):
    """M4: half up, never Python's banker's rounding (round(2.5) is 2)."""
    assert zenodo._expected(x) == words


def test_the_page_texts_lead_with_triceratops_and_carry_the_prior_claim(env_prior):
    """I-1 and M1: the page's sentences are the pack's: the TRICERATOPS headline with its no-STP value and the
    no-class clause, the in-house estimate as a lower bound naming the scenarios it lacks, the class name as a label
    only, and the prior claim with its comparison."""
    data = {**env_prior.data, "cross_check": _validated_block(env_prior.data)}
    texts = dossier_module.page_texts(data)
    assert texts["fpp_lead"] == f"{cross_check.headline(data)}."
    assert texts["fpp_lead"].startswith("TRICERATOPS 1.1.0 gives a false-positive probability of 0.012 (")
    assert cross_check.NO_CLASS_CLAUSE in texts["fpp_lead"] and "(Giacalone et al. 2021)" in texts["fpp_lead"]
    assert texts["fpp_value"] == "0.012"
    assert texts["in_house"].startswith("A four-scenario estimate without a bound-companion or diluted-planet "
                                        "scenario gives ") and "a lower bound" in texts["in_house"]
    # round-2 final review, minor 7: the page's class text is the clause alone
    assert texts["class_text"] == cross_check.CLASS_LABEL
    assert texts["class_text"] == "No validation class applies at 30-minute cadence (Giacalone et al. 2021)."
    for text in texts.values():
        assert "read as" not in (text or "") and "validation lines" not in (text or "")
    assert texts["prior"] == pack_module.prior_claim_sentence(data)
    assert texts["comparison"] == pack_module.comparison_text(data) and texts["comparison"]
    stale = dossier_module.page_texts({**data, "cross_check": {**data["cross_check"], "current": False}})
    assert stale["fpp_lead"] is None and stale["fpp_value"] is None and stale["in_house"] == texts["in_house"]
    bare = dossier_module.page_texts({k: v for k, v in data.items() if k not in ("cross_check", "prior_claims")})
    assert bare["fpp_lead"] is None and bare["class_text"] is None and bare["prior"] is None
    assert bare["comparison"] is None


def _validated_block(data: dict) -> dict:
    from tests.unit.test_crosscheck import fake_result
    block = fake_result(cross_check.inputs_fingerprint(data), fpp=0.012, nfpp=1e-4)
    return cross_check.with_current({**block, "current": True}, data)


# ---------------------------------------------------------------------------------------------------- Task 12 fix round

def test_generate_stores_the_rung3_line_with_the_notes_aperture_ratio(env_with_dossier):
    """I-1: the stored ``why`` row of rung 3 (the dossier page prints it) carries the note's aperture clause."""
    data = _stored(env_with_dossier)
    assert data["aperture"] is not None
    line = next(w for w in data["why"] if w["rung"] == "rung3_stability")
    note = pack_module.aperture_sentence(data)
    assert note[0].lower() + note[1:-1] in line["sentence"]


def test_the_readme_gives_the_leak_method_as_a_floor():
    """M-1: the README's method for the predicted leak says the Gaussian has no PRF wings, so it is a floor."""
    data = {"predicted_leak": {"predicted_ppm": 4.0, "share": 0.0017, "measured_ppm": -9.0, "measured_err_ppm": 12.0,
                               "kic": 6146666, "reason": None, "model": dossier_module.LEAK_MODEL}}
    text = pack_module.leak_method_text(data)
    assert text.startswith("Predicted leak: this star's light is 0.2 percent of the light in KIC 6146666's optimal "
                           "aperture, from " + dossier_module.LEAK_MODEL)
    assert text.endswith("the Gaussian has no PRF wings, so the prediction is a floor.")
    assert pack_module.leak_method_text({"predicted_leak": {**data["predicted_leak"], "predicted_ppm": None}}) is None


def test_the_readme_names_the_hand_built_file_only_when_the_pack_holds_it(env_prior):
    """M-6: without the study at pack time the README points at the study's own path, not a file the pack lacks."""
    (dossier_module.CANDIDATES_DIR / STAGE2_TARGET / "localisation" / "results.json").unlink()
    rep = _build(env_prior)
    readme = _texts(rep)["README.md"]
    assert not (rep.out_dir / pack_module.HAND_BUILT_FILE).exists()
    assert pack_module.HAND_BUILT_FILE not in readme
    assert f"(docs/candidates/{STAGE2_TARGET}/localisation/results.json)" in readme


# ---------------------------------------------------------------------------------------------------- Task 13 (spec 11.6)

def _star_extras(data, **star):
    return {**data, "star": {**data["star"], "ruwe": 0.9449, "non_single_star": 0, "ebpminrp_gspphot": None, **star}}


def test_the_star_sentence_and_the_provenance_carry_ruwe_nss_and_the_reddening(env_with_dossier):
    # a star block before version 6 (no extinction): the title rule of spec 11.6; Task 16 tests the states after it
    data = _star_extras(_stored(env_with_dossier), extinction=None)
    flat = " ".join(render_note(data).split())
    assert "a RUWE of 0.94 and no non-single-star solution" in flat
    assert "the reddening is not measured" in flat                     # the "not yet known" titles name it
    rows = {r["parameter"]: r for r in pack_rows.provenance_rows(data)}
    assert rows["RUWE"]["value"] == 0.9449 and rows["Gaia non-single-star flag"]["value"] == 0
    red = _star_extras(data, ebpminrp_gspphot=0.12)
    assert "GSP-Phot gives E(BP-RP) 0.12" in " ".join(render_note(red).split())
    assert "the reddening is not measured" not in " ".join(render_note(red).split())


def test_the_readme_gives_the_flame_radius_as_a_systematic_and_keeps_the_habitable_zone(env_with_dossier):
    rep = _build(env_with_dossier)
    readme, note = _texts(rep)["README.md"], _texts(rep)["note/note.tex"]
    flame = _stored(env_with_dossier)["star"]["gaia_cross_check"]["radius_flame"]
    assert f"The Gaia FLAME radius of {flame:.3f} Rsun" in readme and "a systematic on the stellar radius" in readme
    assert "kopparapu2013" not in note and "habitable" not in note.lower()
    assert "sub-Neptune" in readme and "Rogers 2015" in readme and "Fulton et al. 2017" in readme


def test_the_note_cites_gaia_dr3_and_kepler_and_its_software_line_is_not_counted(env_with_dossier):
    tex = render_note(_stored(env_with_dossier))
    flat = " ".join(tex.split())
    for key in ("gaia2016", "gaia2023", "borucki2010"):
        assert f"]{{{key}}}" in flat, key
    assert r"\citep{gaia2016,gaia2023}" in flat and r"\citep{borucki2010}" in flat
    versions = pack_module.software_versions()
    assert f"numpy {versions['numpy']}" in flat and f"scipy {versions['scipy']}" in flat
    software = re.search(r"\\software\{[^}]*\}", tex).group(0)
    assert note_words(tex) == note_words(tex.replace(software, r"\software{}"))
    readme = _texts(_build(env_with_dossier))["README.md"]
    for ref in ("Skrutskie", "Thompson", "Kirk", "Stassun"):            # 2MASS, the KOI and EB catalogues, the TIC
        assert ref in readme, ref


def test_the_author_block_comes_from_docs_author_json(env_with_dossier, tmp_path, monkeypatch):
    path = tmp_path / "author.json"
    path.write_text(json.dumps({"name": "A. Person", "affiliation": "Somewhere", "email": "a@example.org",
                                "orcid": "0000-0002-1825-0097"}), encoding="utf-8")
    monkeypatch.setattr(pack_module, "AUTHOR_FILE", path)
    tex = render_note(_stored(env_with_dossier), citation=None)
    assert r"\author[0000-0002-1825-0097]{A. Person}" in tex and r"\affiliation{Somewhere}" in tex
    assert r"\email{a@example.org}" in tex and "TO FILL IN" not in tex
    assert pack_module.author_problems(pack_module.load_author(path)) == []
    path.write_text(json.dumps({"name": "A. Person", "affiliation": "", "email": "", "orcid": "12"}),
                    encoding="utf-8")
    assert pack_module.author_problems(pack_module.load_author(path)) == [
        "docs/author.json has no affiliation", "docs/author.json has no email",
        "docs/author.json has the ORCID '12', not 0000-0000-0000-000X"]
    assert pack_module.author_problems(None) == ["no docs/author.json"]


def test_the_repository_author_file_names_the_author_and_leaves_the_rest_to_fill():
    """docs/author.json carries the name of CITATION.cff; the other three fields are Vikram's to fill in."""
    assert pack_module.load_author() == {"name": "Vikram Pandya", "affiliation": "", "email": "", "orcid": ""}


def test_final_refuses_without_the_live_draft_and_with_any_placeholder(env_with_dossier, fake_session, tmp_path,
                                                                     monkeypatch):
    from skysift import fpp
    monkeypatch.setattr(fpp, "SIGNED_OFF", True)
    env = env_with_dossier
    _approve(env)
    _store_errors(env)
    _store_cross_check(env)
    with pytest.raises(PackError, match="--final needs --zenodo-live"):
        _build(env, final=True)
    path = tmp_path / "author.json"
    path.write_text(json.dumps({"name": "A. Person", "affiliation": "", "email": "a@example.org",
                                "orcid": "0000-0002-1825-0097"}), encoding="utf-8")
    monkeypatch.setattr(pack_module, "AUTHOR_FILE", path)
    live = ZenodoClient("t", sandbox=False, session=fake_session)
    with pytest.raises(PackError, match="docs/author.json has no affiliation"):
        _build(env, zenodo=live, final=True)
    assert fake_session.calls == []                                  # refused before anything is written
    path.write_text(json.dumps({"name": "A. Person", "affiliation": "Somewhere", "email": "a@example.org",
                                "orcid": "0000-0002-1825-0097"}), encoding="utf-8")
    with pytest.raises(ZenodoError, match=r"still says '10\.5281/zenodo\.SOFTWARE'.*unfinished"):
        _build(env, zenodo=live, final=True)                         # CITATION.cff carries no software DOI yet
    assert [c[0] for c in fake_session.calls] == ["POST"]            # reserved, never filled or uploaded
    monkeypatch.setattr(pack_module, "load_citation",
                        lambda path=None: ({"authors": [{"name": "A. Person"}], "doi": "10.5281/zenodo.777"}, "test"))
    fake_session.calls.clear()
    rep = _build(env, zenodo=live, final=True)
    assert pack_module.final_problems(rep.out_dir) == []
    tex = (rep.out_dir / "note" / "note.tex").read_text(encoding="utf-8")
    assert f"10.5281/zenodo.{DEPOSITION_ID}" in tex and "doi:10.5281/zenodo.777" in tex
    assert [c[0] for c in fake_session.calls] == ["POST", "PUT", "PUT"]
    versions = json.loads((rep.out_dir / "provenance" / "versions.json").read_text(encoding="utf-8"))
    assert versions["software"] == pack_module.software_versions() and versions["pack_version"] == PACK_VERSION


def test_imp_unc_carries_the_larger_error():
    data = {"ephemeris": {}, "planet": {}, "shape": {"ok": True, "b": {"median": 0.5, "minus": 0.063, "plus": 0.030,
                                                                      "point": 0.5}}}
    row = _parse_pipe(exofop.render_exofop_planet(data, "20261022", None, "tag"), PLANET_COLUMNS)
    assert row["imp"] == "0.500" and row["imp_unc"] == "0.063"


def test_exofop_and_the_archive_quote_the_central_epoch(env_with_dossier):
    data = _with_errors(_stored(env_with_dossier))
    t = data["timing"]
    row = _parse_pipe(exofop.render_exofop_planet(data, "20261022", None, "tag"), PLANET_COLUMNS)
    # the bulk file prints the value at its error's digits (pm_digits), so compare the number, not four decimals
    assert float(row["epoch"]) == pytest.approx(t["t_ref"] + BKJD, abs=5e-5)
    assert float(row["epoch_unc"]) == pytest.approx(t["t_ref_err"], rel=0.05)
    assert f"(epoch {t['epoch_ref']}, the centre of the series)" in pack_text.ephemeris_sentence(data)


def test_the_single_event_sentence_compares_the_statistics():
    data = {"tests": [{"rung": "rung1_events", "status": "pass",
                       "metrics": {"n_covered": 31, "n_events_with_data": 33, "ses_max_over_mes": 0.28}}]}
    assert pack_module._vetting_sentences(data)[0] == (
        "Of the 33 predicted transits with data, 31 are well covered, and the largest single-event statistic is 0.28 "
        "of the multiple-event statistic.")


def test_the_abstract_quotes_the_period_with_the_texts_digits(env_with_dossier):
    data = _with_errors(_stored(env_with_dossier))
    assert f"dip every {pack_text.quantities(data)['period']} days" in pack_module.abstract_text(data)


def test_season_means_the_roll_season_and_the_depth_test_speaks_of_quarter_groups():
    data = {"stability": {"metrics": {"season_depths": {"0": 1, "1": 2, "2": 3, "3": 4}, "chi2": 3.5, "dof": 3,
                                      "p_value": 0.32}}, "tests": []}
    text = " ".join(pack_module._vetting_sentences(data))
    assert "The depth agrees across 4 quarter groups (quarters taken modulo 4;" in text and "seasons" not in text


def test_the_note_figure_carries_the_fold_the_parity_and_the_season_difference_images(env_with_dossier,
                                                                                     with_matplotlib):
    rep = _build(env_with_dossier)
    assert (rep.out_dir / "note" / "figure1.png").is_file()
    flat = " ".join(_texts(rep)["note/note.tex"].split())
    assert "(a) the light curve" in flat and "(b) odd and (c) even transits" in flat
    assert "(d) per-season difference images" in flat and flat.count(r"\begin{figure}") == 1
    assert (rep.out_dir / "note" / "figure1.pdf").is_file() and (rep.out_dir / "figures" / "centroids.png").is_file()
    plain = " ".join(render_note(_stored(env_with_dossier), panels=False).split())     # the SVG: the fold alone
    assert "(a)" not in plain and "(b) odd" not in plain and "The light curve folded on" in plain


def test_one_clause_gives_the_follow_up_window_and_the_photometry(env_with_dossier):
    data = _with_errors(_stored(env_with_dossier))
    after = 2461335.5                                         # 22 October 2026
    nxt = pack_rows.next_transits(data, after, n=1)[0]
    text = pack_module.followup_text(data, after)
    depth = float(f"{data['ephemeris']['depth_ppm']:.2g}")
    assert text == (f"A transit in {nxt['date'][-4:]} is predicted to within {nxt['window_min']:.0f} minutes "
                    f"(1 sigma): a {depth:,.0f} ppm transit on a G {data['target']['gmag']:.1f} star, within reach "
                    "of a 1 to 2 m telescope at a few hundred ppm per 10 minutes.")
    assert pack_module.followup_text({**data, "timing": None}, after) is None


# ---------------------------------------------------------------------------------------------------- Task 13 fix round 1

def test_the_gsp_phot_reddening_prints_with_its_cool_dwarf_caveat(env_with_dossier):
    """I-1 (spec 11.6, the controller's ruling): GSP-Phot's E(BP-RP) carries the cool-dwarf bias (Andrae et al.
    2023) and what the parameters assume, in the note and in the README's Star paragraph; without it neither."""
    data = _star_extras(_stored(env_with_dossier), ebpminrp_gspphot=0.27, extinction=None)   # before version 6
    flat = " ".join(render_note(data).split())
    assert (r"GSP-Phot gives E(BP-RP) 0.27, a value biased for cool dwarfs \citep{andrae2023}; the temperature "
            r"assumes no reddening (E(B-V) = 0), and the $K_s$ radius barely depends on it.") in flat
    assert r"]{andrae2023} Andrae, R., Fouesneau, M., Sordo, R., et~al. 2023, A\&A, 674, A27" in flat
    assert pack_module.references(data)["andrae2023"]
    table = {**data, "star": {**data["star"], "route": "mamajek"}}
    radius, alt = data["star"]["radius"]["median"], data["star"]["alt_radius_e02"]["median"]
    verb = "raise" if alt > radius else "lower"
    assert pack_module.reddening_text(table) == (f"GSP-Phot gives E(BP-RP) 0.27, a value biased for cool dwarfs "
                                                 f"(Andrae et al. 2023); the radius assumes no reddening (E(B-V) = 0), "
                                                 f"and an E(BP-RP) of 0.2 would {verb} it to {alt:.3f} Rsun")
    readme = pack_module.render_readme(data, {})
    star = next(p for p in readme.split("\n\n") if p.startswith("Star:"))
    assert "GSP-Phot gives E(BP-RP) 0.27, a value biased for cool dwarfs (Andrae et al. 2023)" in star
    # spec 12.3 as amended, with Task 16's cut 1: with the stored block (the bracket) the note drops the GSP-Phot
    # sentence and its citation, and the README prints GSP-Phot as not adopted in the reddening paragraph
    stored = _star_extras(_stored(env_with_dossier), ebpminrp_gspphot=0.27)
    assert pack_module.reddening_text(stored, True) is None and not pack_module.references(stored)["andrae2023"]
    assert "GSP-Phot" not in render_note(stored)
    assert any(r.startswith("Andrae, R.") for r in pack_module.readme_references(stored))
    star = next(p for p in pack_module.render_readme(stored, {}).split("\n\n") if p.startswith("Star:"))
    assert "GSP-Phot E(BP-RP) 0.27, biased for cool dwarfs (Andrae et al. 2023), is not adopted." in star
    assert any(r.startswith("Andrae, R.") for r in pack_module.readme_references(data))
    bare = _star_extras(_stored(env_with_dossier))
    assert pack_module.reddening_text(bare) is None and "andrae2023" not in render_note(bare)
    assert not pack_module.references(bare)["andrae2023"]


def test_the_readme_paragraphs_stay_apart_and_its_sentences_are_current(env_with_dossier):
    """M-3: the Star and the Pixel offset paragraphs end before the next one in Markdown. M-4: the word-count
    sentence names the software line, step 3 names docs/author.json, the references run by author. M-6: the
    follow-up clause once (step 5). M-1: the README says where the note's wording is shorter."""
    env = env_with_dossier
    _store_errors(env)
    readme = _texts(_build(env))["README.md"]
    paragraphs = readme.split("\n\n")
    star = next(p for p in paragraphs if p.startswith("Star:"))
    assert "\n" not in star.strip()
    offset = next(p for p in paragraphs if p.startswith("Pixel offset:"))
    assert "Predicted leak" not in offset
    assert "the author block, the keywords and the software line are not counted" in readme
    assert "1. The author block comes from `docs/author.json`" in readme
    refs = readme.split("## References")[1].strip().splitlines()
    assert refs == sorted(refs, key=pack_module._by_author)
    data = _with_errors(_stored(env))
    followup = pack_module.followup_text(data, 2461335.5)[:40]
    assert readme.count(followup) <= 1
    assert "Note wording: The Research Note words these more briefly than this README" in readme


def test_the_zenodo_creators_and_the_note_authors_come_from_one_author_dict(tmp_path, monkeypatch):
    """M-5: docs/author.json gives the note's author and the Zenodo creator, a field it leaves empty taken from the
    citation's same author."""
    from skysift import zenodo
    path = tmp_path / "author.json"
    path.write_text(json.dumps({"name": "Vikram Pandya", "affiliation": "SP Jain", "email": "", "orcid": ""}),
                    encoding="utf-8")
    monkeypatch.setattr(pack_module, "AUTHOR_FILE", path)
    citation = {"authors": [{"given-names": "Vikram", "family-names": "Pandya", "orcid": "0000-0002-1825-0097"}]}
    joined = pack_module.with_author(citation)
    m = zenodo.metadata_from({"target": {"name": "KIC 1"}}, joined)
    assert m["creators"] == [{"name": "Pandya, Vikram", "orcid": "0000-0002-1825-0097", "affiliation": "SP Jain"}]
    assert pack_module._authors(citation) == [{"name": "Vikram Pandya", "orcid": "0000-0002-1825-0097",
                                               "affiliation": "SP Jain", "email": ""}]
    assert pack_module.with_author(None)["authors"] == [{"name": "Vikram Pandya", "affiliation": "SP Jain"}]


def test_the_exofop_note_gives_the_central_epoch_once(env_with_dossier):
    """M-6: the ExoFOP note's ephemeris sentence carries the central epoch; the timing clause does not repeat it."""
    env = env_with_dossier
    _store_errors(env)
    note = _texts(_build(env))["exofop/note.txt"]
    assert note.count("2455694.51787") == 1 and "Reference mid-transit" not in note


def test_psffrac_prints_for_a_target_that_is_not_kbonus(env_with_dossier):
    """M-8: PSFFRAC leaves the named list only where the KBonus sentence prints it."""
    data = _stored(env_with_dossier)
    if not any(r["key"] == "psffrac" for r in pack_rows.dilution_rows(data)) or not data.get("aperture"):
        pytest.skip("the fixture carries no PSFFRAC or aperture block")
    other = {**data, "target": {**data["target"], "source": "spoc"}}
    assert "PSFFRAC" in str(pack_module.dilution_sentence(other, tex=True))


def test_the_software_line_names_the_triceratops_version():
    """M-9: triceratops carries the stored tool version in the software line."""
    line = pack_module.software_line({"cross_check": {"tool_version": "1.1.0"}}, versions={})
    assert line.endswith("matplotlib, triceratops 1.1.0")
    assert pack_module.software_line({"cross_check": {}}, versions={}).endswith("matplotlib, triceratops")


def test_the_figure_file_note_names_panel_d_only_with_pixel_data():
    """M-10: the PDF figure's file note names the difference images only when the pixel check stored them."""
    assert "(d)" not in pack_module.file_notes({})["note/figure1.pdf"]
    assert "(d) the per-season difference images" in pack_module.file_notes({"pixels": [{}]})["note/figure1.pdf"]


def test_the_note_keeps_the_nulls_named_and_bryson_rule_as_a_rule():
    """M-2: the note's FAP sentence keeps "of other KBonus stars". M-1: Bryson's rule reads "(over 3 sigma and 0.2
    arcsec)"."""
    data = json.loads(GOLDEN.read_text(encoding="utf-8"))
    flat = " ".join(render_note(data).split())
    assert "null searches of other KBonus stars reached the signal's SDE" in flat
    assert r"\citet{bryson2013} (over 3 sigma and 0.2 arcsec)" in flat


def test_the_note_gives_the_short_tess_sentence_and_leaves_the_follow_up_to_the_readme():
    """Fix round 1 cuts 10 and 11: the follow-up clause is the README's step 5; the note's TESS sentence is the
    sector count, the folded significance under both noise models and the verdict, citing Christ et al. 2019."""
    data = _live_shape("dominant", True)
    flat = " ".join(render_note(data).split())
    sig = data["tess"]["significance"]
    # round-2 final review: the skipped sector again (Task 13 parked minor) and "no TESS data were examined" (minor 9);
    # Task 15 fix round 1, cut B3
    assert (f"TESS observed the star in {len(data['tess']['sectors'])} sectors (Sector 109 not assessed); the "
            f"predicted folded significance of {sig['folded_conservative']:.2f} to {sig['folded_nominal']:.2f} sigma "
            "means no detection is expected at the nominal noise; no TESS data were examined "
            r"\citep{christ2019}.") in flat
    assert "is predicted to within" not in flat


# ------------------------------------------------------------------------------------------ round-2 final review

def _golden() -> dict:
    return json.loads(GOLDEN.read_text(encoding="utf-8"))


def test_the_gate_refuses_a_prior_claim_far_from_the_signal():
    """Minor 10: a prior claim whose period, epoch or depth sits more than 5 sigma from ours is refused with one line
    (KIC 5689121's claim on KIC 6146676's golden); the claim on its own star is not."""
    golden = _golden()
    golden["prior_claims"] = [{k: PRIOR_CLAIM[k] for k in dossier_module.PRIOR_CLAIM_PUBLIC}]
    assert pack_module.gate_problems(golden).count(pack_module.PRIOR_MISMATCH) == 1
    assert pack_module.PRIOR_MISMATCH == ("the prior claim does not match this signal; check the claim's star and "
                                          "numbers")
    assert pack_module.PRIOR_MISMATCH not in pack_module.gate_problems(LIVE_5689121)
    assert pack_module.PRIOR_MISMATCH not in pack_module.gate_problems(_golden())


def test_the_mismatch_gate_also_fires_for_a_quantity_with_no_error_on_either_side():
    """Polish round 2: with no error on either side there is no sigma, so a period more than 1 percent from the
    claim's, or a mid-transit time more than 0.1 d from it, is a mismatch too; the message states the rule."""
    assert "more than 1 percent" in pack_module.PRIOR_MISMATCH_NO_ERROR and "0.1 d" in pack_module.PRIOR_MISMATCH_NO_ERROR
    assert pack_module.PRIOR_MISMATCH_NO_ERROR.startswith(pack_module.PRIOR_MISMATCH)
    claim = {k: PRIOR_CLAIM[k] for k in dossier_module.PRIOR_CLAIM_PUBLIC}
    bare = {"ephemeris": {"period_days": 13.769414}, "timing": {"ok": False, "t0": 136.314294, "period": 13.769414},
            "prior_claims": [claim]}
    cmp = pack_module.prior_comparison(bare)
    assert cmp["period"]["sigma"] is None and cmp["epoch"]["sigma"] is None
    assert not pack_module._unerred_mismatch("period", cmp["period"])
    assert not pack_module._unerred_mismatch("epoch", cmp["epoch"])
    # the epoch: 0.05 d off passes, 0.3 d off does not
    for shift, fires in ((0.05, False), (0.3, True)):
        moved = {**bare, "timing": {**bare["timing"], "t0": 136.314294 + shift}}
        row = pack_module.prior_comparison(moved)["epoch"]
        assert row["sigma"] is None and pack_module._unerred_mismatch("epoch", row) is fires
    # the period: 0.95 percent off passes, 1.65 percent off does not
    for period, fires in ((13.9, False), (14.0, True)):
        row = pack_module.prior_comparison({**bare, "ephemeris": {"period_days": period}})["period"]
        assert pack_module._unerred_mismatch("period", row) is fires
    # a quantity with an error on either side keeps the sigma rule alone
    assert not pack_module._unerred_mismatch("period", {"ours": 14.0, "theirs": 13.0, "sigma": 2.0})
    assert not pack_module._unerred_mismatch("depth", {"ours": 1000.0, "theirs": 3000.0, "sigma": None})
    assert not pack_module._unerred_mismatch("period", None)
    # through the gate: the golden with no period error and a claim 3 percent off
    golden = _golden()
    period = golden["ephemeris"]["period_days"]
    golden["ephemeris"] = {**golden["ephemeris"], "period_err_days": None}
    off = {**claim, "period_days": period * 1.03, "period_err_days": None, "t0_bjd": None, "depth_ppm": None,
           "fpp": None}
    golden["prior_claims"] = [off]
    assert pack_module.PRIOR_MISMATCH_NO_ERROR in pack_module.gate_problems(golden)
    assert pack_module.PRIOR_MISMATCH not in pack_module.gate_problems(golden)
    golden["prior_claims"] = [{**off, "period_days": period * 1.005}]
    assert pack_module.PRIOR_MISMATCH_NO_ERROR not in pack_module.gate_problems(golden)


def test_a_stale_cross_check_is_refused_once():
    """Minor 1: a stale cross-check is refused by its own reason, not again for the FPP order of the abstract and
    the Zenodo description."""
    golden = _golden()
    assert golden["cross_check"]["current"] is False
    problems = pack_module.gate_problems(golden)
    assert not [p for p in problems if "in-house false-positive probability without" in p]
    assert cross_check.verdict(golden["cross_check"])[1] in problems


def test_with_a_prior_claim_the_zenodo_record_is_titled_as_the_note_and_the_gate_reads_it(monkeypatch):
    """I-3: with a prior claim the Zenodo title is the note's, the description says vetted and carries the
    comparison sentence, and the gate scans the title and the description for the forbidden words."""
    data = {**_golden(), "prior_claims": LIVE_5689121["prior_claims"]}
    meta = zenodo.metadata_from(data, None, prior=pack_module.zenodo_prior(data))
    assert meta["title"] == "SkySift vetting of the 40.291 day transit candidate on KIC 6146676"
    assert "candidate vetted by the SkySift engine" in meta["description"]
    assert str(pack_module.comparison_text(data)) in meta["description"]
    assert zenodo.metadata_from(_golden(), None)["title"].startswith("SkySift candidate KIC 6146676: ")
    monkeypatch.setattr(pack_module, "FORBIDDEN_WORDS", (r"\bvetting of\b",))
    problems = pack_module.gate_problems(data)
    assert "the Zenodo title says 'vetting of', which spec 11.1 rules out" in problems


def test_the_follow_up_both_tools_and_radius_sentences_read_as_the_review_asks():
    """Minors 2, 3 and 5: the follow-up clause gives the transit's depth and the telescope class, BOTH_TOOLS says
    where the nearby weight sits, and the background-binary radius says what model 2 used."""
    golden = _golden()
    text = pack_module.followup_text(golden, 2461000.0)
    assert text.endswith(": a 2,200 ppm transit on a G 16.2 star, within reach of a 1 to 2 m telescope at a few "
                         "hundred ppm per 10 minutes.")
    faint = {**golden, "target": {**golden["target"], "gmag": 18.0}}
    assert pack_module.followup_text(faint, 2461000.0).endswith("ppm transit on a G 18.0 star.")
    assert not hasattr(pack_module, "BOTH_TOOLS")      # spec 12.2: the run without them is made, and named
    assert pack_module.BEB_RADIUS.endswith("as its confusion radius; it was 2.0 arcsec in model 2.")
    assert "spec 11.5" not in pack_module.BEB_RADIUS


def test_the_readme_gives_the_shape_call_with_its_evidence_and_the_note_does_not_cite_it():
    """I-4: the README's b row says "with the density prior"; a sentence gives the free fit, the chi-square
    difference against the rule of 2 and the reading, citing Seager and Mallén-Ornelas (2003) at the density and
    Kipping (2010) at the exposure integration, which the README names, both in the README's list; the note keeps "unconstrained at this cadence" without them."""
    golden = _golden()
    assert pack_module.b_set_by_prior(golden)
    readme = render_readme(golden, {})
    assert "| impact parameter (with the density prior) | 0.455 +0.054/-0.061" in readme
    text = pack_module.shape_fit_text(golden)
    assert text.startswith("Shape fit: with the density prior b is 0.455 +0.054/-0.061 and a/R* 78.5")
    assert ("with a chi-square lower by only 0.26, within the rule of 2: the data do not constrain them; the quoted "
            "values follow the host's density (Seager and Mallén-Ornelas 2003). The model is integrated over the "
            "29.4-minute exposure, which matters at this cadence (Kipping 2010).") in text
    assert text.count("Kipping") == 1 and text.index("Seager") < text.index("integrated") < text.index("Kipping")
    stated = json.loads(json.dumps(golden))
    stated["shape"]["exposure_min"] = 29.43
    assert "integrated over the 29.4-minute exposure" in pack_module.shape_fit_text(stated)
    stated["shape"]["exposure_min"] = 1.966
    assert "integrated over the 2.0-minute exposure, which matters at this cadence (Kipping 2010)" \
        in pack_module.shape_fit_text(stated)
    assert text in readme
    assert "- Seager, S., & Mallén-Ornelas, G. 2003, ApJ, 585, 1038" in readme
    assert "- Kipping, D. M. 2010, MNRAS, 408, 1758" in readme
    tex = render_note(golden)
    assert UNCONSTRAINED in " ".join(tex.split())
    assert "seager2003" not in tex and "kipping2010" not in tex
    free = {**golden, "shape": {**golden["shape"], "comparison": {**golden["shape"]["comparison"],
                                                                     "constrained": True}}}
    assert pack_module.shape_fit_text(free) is None
    assert "(with the density prior)" not in render_readme(free, {})


def test_the_readme_says_where_the_null_stars_come_from_and_what_sets_the_tail():
    """Minor 6: the README's false-alarm paragraph cites Thompson et al. (2018) and Coughlin (2017), says the null
    stars are the calibration pilot's with 10 of them in the sweep, and that every exceedance is an inverted null."""
    golden = _golden()
    golden["calibration"] = {**golden["calibration"], "null_stars_in_sweep": 10}
    text = pack_module.fap_method_text(golden)
    assert text.startswith("The null searches are the inverted and scrambled light curves of Thompson et al. (2018) "
                           "and Coughlin (2017); the 999 null stars come from the calibration pilot, a selection "
                           "apart from the sweep (10 of them are in it); ")
    assert text.endswith("all 11 null searches that reached the signal's SDE are inverted light curves, so "
                         "systematics, not white noise, set the tail.")
    readme = render_readme(golden, {})
    assert text in readme and "- Coughlin, J. L. 2017, Planet Detection Metrics" in readme
    mixed = {**golden, "fap": {**golden["fap"], "k_by_kind": {"inverted": 8, "scrambled": 3}}}
    assert "of the 11 that reached the signal's SDE, 8 are inverted and 3 are scrambled." in \
        pack_module.fap_method_text(mixed)
    assert pack_module.fap_method_text({**golden, "fap": {**golden["fap"], "calibrated": False}}) is None


def test_the_readme_references_sort_by_first_author_then_year():
    """Task 13 parked minor: the README's list sorts by the first author's family name, accents folded, then the
    year."""
    refs = pack_module.readme_references(_golden())
    keys = [pack_module._by_author(r)[:2] for r in refs]
    assert keys == sorted(keys)
    mann = [r for r in refs if r.startswith("Mann, A. W.")]
    assert len(mann) == 2 and "2015" in mann[0] and "2019" in mann[1]
    assert pack_module._by_author("Martínez-Palomera, J., Hedges, C. 2023a, AJ")[0] == "martinez-palomera"


def test_the_silent_seasons_and_the_secondary_read_without_nested_brackets():
    """Minor 10 and the Task 13 parked minor: a non-voting season's reason is one clause; the note's short secondary
    keeps "in the same fold"."""
    assert pack_module._silent_sentence([{"season": 3, "non_voting_reason": "box cut: ambiguous (1.8 sigma)"}]) == \
        "Season 3 did not vote (box cut: ambiguous at 1.8 sigma)."
    assert pack_module._silent_sentence([{"season": 0, "non_voting_reason": "box cut"},
                                         {"season": 3, "non_voting_reason": None}]) == \
        "Seasons 0 and 3 did not vote (season 0 box cut; season 3 no reason stored)."
    assert pack_text.reason_words("too few points (0.5 sigma)") == "too few points at 0.5 sigma"
    assert pack_text.reason_words("") == "no reason stored"
    assert pack_text.reason_words("box incomplete: target") == "box incomplete: target"      # one reason, not two
    short = str(pack_module.secondary_sentence(_golden(), True, short=True))
    assert ", and positive excursions in the same fold reach 5.2 sigma" in short


def test_the_exofop_planet_file_credits_the_first_finder():
    """Minor 8: the planet file's discovery names the prior claim's author with a prior claim, SkySift otherwise."""
    assert exofop.discovery(LIVE_5689121) == "Mehta (2026), Zenodo"
    assert exofop.discovery(_golden()) == exofop.EXOFOP_DISCOVERY == "SkySift (Kepler)"


def test_the_note_readme_upload_note_and_page_carry_the_same_round_2_numbers():
    """I-2: the leak, the depth trend and the sweep expectation are printed with the same numbers in the Research
    Note, the README, the ExoFOP upload note and the dossier page's texts."""
    golden = _golden()
    page = " ".join(str(v) for v in dossier_module.page_texts(golden).values() if v)
    texts = {"note": " ".join(pack_module._plain_tex(render_note(golden)).split()),
             "readme": render_readme(golden, {}), "exofop": pack_module._render_plain("exofop_note.txt.j2", golden, {}), "page": page}
    for number in ("4 ppm", "-9 ± 12 ppm", "228 ± 90 ppm per year", "1,579-star sweep", "about 9 such peaks"):
        for name, text in texts.items():
            flat = " ".join(text.replace("$\\pm$", "±").split())
            assert number in flat, (number, name)



def test_the_readme_traces_the_leakage_budget_to_the_catalogue(env_with_dossier):
    """Task 15 addendum 2: the stellar provenance row and the README's star line name CROWDSAP's catalogue column;
    the README gives the three-part basis and every text with the four numbers names the number that rests on our
    own evidence; the forbidden-words gate stays clean."""
    env = env_with_dossier
    _store_errors(env)
    stored = _stored(env)
    crowd = {"value": 0.85650748, "column": "crowdsap",
             "catalogue": "KBonus-bkg v1.0 source catalogue (Martinez-Palomera et al. 2023)",
             "per_quarter": {"2": 0.8401, "3": 0.8702}, "per_quarter_min": 0.8401, "per_quarter_max": 0.8702}
    _store(env, {**stored, "star": {**stored["star"], "crowdsap": crowd}})
    shares = {"source": "Kepler-pixel overlap, capped by the contamination budget", "budget": 0.16753213,
              "crowdsap": 0.85650748, "sigma_pix": 0.5695906, "weight_sum": 0.1548311, "capped": False}
    block = _store_cross_check(env)
    env.store.set_cross_check(STAGE2_TARGET, {**block, "settings": {**block["settings"], "flux_shares": shares}})
    data = _stored_with_cross_check(env)
    row = {r["parameter"]: r for r in pack_rows.provenance_rows(data)}["KBonus crowding metric (CROWDSAP)"]
    assert row["value"] == 0.85650748 and row["method"] == "the crowdsap column of the KBonus-bkg v1.0 source catalogue"
    assert row["reference"] == "Martinez-Palomera et al. 2023" and "0.8401 to 0.8702 over its 2 usable quarters" in \
        row["inputs"]
    rep = _build(env)
    texts = _texts(rep)
    readme = texts["README.md"]
    assert pack_rows.crowdsap_text(data) in readme and "(the crowdsap column of the KBonus-bkg v1.0 source " \
        "catalogue, Martinez-Palomera et al. 2023;" in readme
    assert "most crowded quarter" not in pack_rows.crowdsap_text(data)          # C is not the lowest quarter here
    assert cross_check.leakage_basis(data) in readme and "so the cap did not bind." in readme
    own = cross_check.own_evidence_sentence(data)
    # the round 3 final review, I2: one form in every text
    assert own.startswith("The pixel-excluded value (0.0040") and "rests on neighbour shares set by the PRF overlap " \
        "rule within the catalogue's crowding budget, a bound rather than a measurement." in own
    for name in ("README.md", "exofop/note.txt", "archive_email.txt"):
        assert own in texts[name], name
    # Task 15 fix round 1, cut B2: the note carries the short form, once
    assert str(pack_text.tex_escape(cross_check.own_evidence_sentence(data, short=True))) in " ".join(
        texts["note/note.tex"].split())
    assert " ".join(texts["note/note.tex"].split()).count("rests on our own evidence") == 1
    meta = json.loads((rep.out_dir / "zenodo_metadata.json").read_text(encoding="utf-8"))
    assert own in meta["description"]
    assert "KBonus crowding metric (CROWDSAP)" in (rep.out_dir / "data" / "stellar_provenance.csv").read_text(
        encoding="utf-8")
    assert not [p for p in pack_module.gate_problems(data) if "spec 11.1" in p]


def test_the_round_3_cuts_leave_the_note_and_keep_every_number_in_the_readme():
    """Task 15 fix round 1 (the controller's rulings of 24 October 2026): cuts A1 and B1 to B8 on the longest live
    shape (dominant, with a prior claim). What leaves the note stays in the README."""
    data = _live_shape("dominant", True)
    tex = " ".join(render_note(data).split())
    abstract = tex.split(r"\begin{abstract}")[1].split(r"\end{abstract}")[0]
    body = tex.split(r"\end{abstract}")[1]
    # A1: the abstract carries the two FPPs; the no-STP values are in the body
    assert ("TRICERATOPS 1.1.0 gives a false-positive probability of 0.60, or 0.31 with the 6 neighbours the "
            "difference images exclude removed (no validation class applies at 30-minute cadence; Giacalone et al. "
            "2021)") in abstract
    assert "without" not in abstract.split("TRICERATOPS")[1].split("Giacalone")[0]
    # B1: the pixel section opens on one sentence
    pixels = body.split(r"\section{Which star is dimming}")[1].lstrip()
    assert pixels.startswith("In difference images the centroid sits a median 0.19 pixels from this star and 2.82 "
                             "pixels from its brightest neighbour, nearer this star in 4 of 4 observing seasons, its "
                             "error carrying a pointing-jitter floor of 0.017 px. In seasons 0 and 3")
    # B2 and B8: the own-evidence sentence and the cross-check clause
    # B2, and fix round 2's M2: the excluded set's dominant scenario in two words
    assert ("The pixel-excluded value (0.31, mainly STP) rests on our own evidence, the difference-image centroids; "
            "the all-neighbours value (0.60) rests on neighbour shares set by the PRF overlap rule within the "
            "catalogue's crowding budget, a bound rather than a measurement.") in body
    assert ("gives a median false-positive probability of 0.60 (0.60 without a planet transiting an unresolved bound "
            "companion, STP, absent from SkySift's model; 0.31, and 0.014 without STP, with the 6 neighbours the "
            "difference images exclude removed) and a nearby false-positive probability of 0.0020 over 20 runs, 2 "
            "degenerate runs left out (no validation class applies at 30-minute cadence).") in body
    # B3: the TESS sentence, no README pointer
    assert ("TESS observed the star in 9 sectors (Sector 109 not assessed); the predicted folded significance of "
            r"0.56 to 0.96 sigma means no detection is expected at the nominal noise; no TESS data were examined "
            r"\citep{christ2019}.") in body
    # B4, B5, B6 and B7
    assert "The SkySift engine found the signal with a box search" in body and "ladder version" not in body
    assert "False-alarm probability:" not in body
    assert ("11 of 1,998 null searches of other KBonus stars reached the signal's SDE (1 in 170); across the "
            "1,579-star sweep about 9 such peaks are expected by chance") in body
    assert "README" not in tex.split(r"\begin{document}")[1].split(r"\software")[0]
    assert r"Pack (data and methods): \dataset[" in body
    assert "a PSF flux fraction inside the aperture of 0.80" in body and "(PSFFRAC)" not in body
    assert "search version 5+6cfe4cf4" in tex.split(r"\software")[1]
    # what left the note stays in the README
    readme = render_readme(data, {})
    for kept in ("ladder version", "PSFFRAC", "False-alarm probability: 1 in 170", "0.014", "Christ"):
        assert kept in readme, kept


def test_the_crowding_metric_says_when_it_is_the_most_crowded_quarters():
    """Task 15 fix round 1, C1: the catalogue value stays the budget; when it equals the lowest per-quarter CROWDSAP
    the README's and the page's text says so beside the range, and not otherwise."""
    crowd = {"value": 0.85650748, "column": "crowdsap",
             "catalogue": "KBonus-bkg v1.0 source catalogue (Martinez-Palomera et al. 2023)",
             "per_quarter": {"12": 0.85693011, "16": 1.0, "17": 0.85650748}, "per_quarter_min": 0.85650748,
             "per_quarter_max": 1.0}
    text = pack_rows.crowdsap_text({"star": {"crowdsap": crowd}})
    assert text == ("The KBonus crowding metric is C = 0.8565 (the crowdsap column of the KBonus-bkg v1.0 source "
                    "catalogue, Martinez-Palomera et al. 2023; the light-curve file's CROWDSAP keyword runs 0.8565 to "
                    "1.0000 over its 3 usable quarters, and the catalogue value equals the most crowded quarter's).")
    lower = {**crowd, "value": 0.84}
    assert "most crowded" not in pack_rows.crowdsap_text({"star": {"crowdsap": lower}})
    golden = _golden()
    assert "and the catalogue value equals the most crowded quarter's" in pack_rows.crowdsap_text(golden)
    assert "most crowded" not in render_note(golden)                      # the note does not change (C1)


# ---------------------------------------------------------------------------------------------------- Task 16 (spec 12.3)

def _with_adopted_extinction(env) -> dict:
    """The fixture dossier again with a 3D value stored by hand (adopted, the full column kept as a check)."""
    from tests.unit.test_dossier import EXTINCTION_ADOPTED, write_extinction
    write_extinction(STAGE2_TARGET, EXTINCTION_ADOPTED)
    generate(env.store, env.blob, env.settings, STAGE2_TARGET, fetch=env.fetch)
    return _stored(env)


def _readme_table(readme: str) -> list[list[str]]:
    lines = readme.split("| Estimate | E(B-V) |")[1].split("\n\n")[0].splitlines()
    return [[c.strip() for c in line.strip("|").split("|")] for line in lines[2:]]


def test_without_a_3d_value_every_text_brackets_the_planet_and_adopts_the_unreddened_star(env_with_dossier):
    """The controller's addendum 2: the unreddened star stays adopted; the note prints the planet radius as a bracket
    up to the full-column value in one clause, the README a row per estimate with the reasons, and the plain texts
    the whole paragraph."""
    data = _stored(env_with_dossier)
    star, planet = data["star"], data["planet"]
    assert star["extinction"]["state"] == "not available" and star["extinction"]["column"]["ebv"] == 0.10
    rep = _build(env_with_dossier)
    texts = _texts(rep)
    flat = " ".join(texts["note/note.tex"].split())
    clause = str(pack_module.rp_bracket_text(data, True))
    lo, hi = planet["rp_rearth"]["median"], star["extinction"]["column"]["rp_rearth"]["median"]
    d = pack_text.pm_digits(planet["rp_rearth"]["minus"], planet["rp_rearth"]["plus"])
    assert float(f"{lo:.{d}f}") < float(f"{hi:.{d}f}")
    assert clause == (rf", {lo:.{d}f} to {hi:.{d}f} from unreddened (E(B-V) = 0), adopted, to the "
                      r"full-column 0.100 \citep{schlafly2011}")
    assert rf"$R_\oplus${clause}, on an orbit of" in flat
    assert r"]{schlafly2011} Schlafly, E.~F., \& Finkbeiner, D.~P. 2011, ApJ, 737, 103" in flat
    cited, items = _cites(texts["note/note.tex"])
    assert cited == items and "green2019" not in items
    assert "only bracketed" in flat and "Bayestar19" not in flat                 # the caveat title, no 3D clause
    plain = pack_module.reddening_plain(data)
    assert plain.startswith("Reddening: not measured at the star's distance (Bayestar19 not available at build "
                            "time), so the parameters are unreddened (E(B-V) = 0), adopted (")
    assert "full-column E(B-V) of 0.100 ± 0.010 (Schlafly and Finkbeiner 2011) is an upper bound" in plain
    column_rp = pack_module._at_adopted(star["extinction"]["column"]["rp_rearth"], planet["rp_rearth"])
    assert f"planet radius {column_rp} Earth radii" in plain
    for name in ("exofop/note.txt", "archive_email.txt"):
        assert plain in " ".join(texts[name].split()), name
    readme = texts["README.md"]
    assert ("| Estimate | E(B-V) | E(BP-RP) | A_G | A_Ks | (BP-RP)_0 | Teff (K) | Radius (Rsun) | Mass (Msun) | "
            "Insolation (Earth) | Planet radius (Earth radii) | Status |") in readme
    rows = {r[0]: r for r in _readme_table(readme)}
    assert set(rows) == {"Unreddened", "Full column (Schlafly and Finkbeiner 2011)", "GSP-Phot (Gaia DR3)"}
    assert rows["Unreddened"][-1] == "adopted" and rows["Unreddened"][1:5] == ["0", "0", "0", "0"]
    assert rows["Unreddened"][6] == pack_text.value_text(star["unreddened"]["teff"])
    assert rows["Full column (Schlafly and Finkbeiner 2011)"][1] == "0.100 ± 0.010"
    assert rows["Full column (Schlafly and Finkbeiner 2011)"][-1] == "upper bound"
    assert rows["Full column (Schlafly and Finkbeiner 2011)"][10] == column_rp
    assert rows["GSP-Phot (Gaia DR3)"][-1] == "not adopted" and rows["GSP-Phot (Gaia DR3)"][6] == "3,855"
    assert pack_module.reddening_key(data) in readme and "biased for cool dwarfs (Andrae et al. 2023)" in readme
    for ref in ("Cardelli, J. A., Clayton, G. C., & Mathis, J. S. 1989, ApJ, 345, 245",
                "Fitzpatrick, E. L., Massa, D., Gordon, K. D., Bohlin, R., & Clayton, G. C. 2019, ApJ, 886, 108",
                "Riello, M., De Angeli, F., Evans, D. W., et al. 2021, A&A, 649, A3",
                "Schlafly, E. F., & Finkbeiner, D. P. 2011, ApJ, 737, 103"):
        assert ref in readme, ref
    prov = {r["parameter"]: r for r in _rows(rep, "data/stellar_provenance.csv")}
    assert float(prov["E(B-V) full column (Schlafly and Finkbeiner 2011), an upper bound"]["value"]) == 0.10
    assert {name for name, _key in pack_rows.COLUMN_ROWS} <= set(prov)
    assert not {name for name, _key in pack_rows.EXTINCTION_ROWS} & set(prov)
    assert pack_module.star_texts(data).startswith(plain)
    for text in texts.values():
        assert EM_DASH not in text and not IZE.search(text) and "discover" not in text.lower()


def _bracketed(data: dict, adopted: float, upper: float, flagged: bool = True) -> dict:
    planet, col = data["planet"], data["star"]["extinction"]["column"]
    star = {**data["star"], "extinction": {**data["star"]["extinction"], "column": {
        **col, "outside_mann_range": flagged, "rp_rearth": {**col["rp_rearth"], "median": upper, "minus": 0.14,
                                                            "plus": 0.19}}}}
    return {**data, "star": star,
            "planet": {**planet, "rp_rearth": {**planet["rp_rearth"], "median": adopted, "minus": 0.1, "plus": 0.1}}}


def test_the_bracket_flags_the_mann_range_and_prints_the_upper_end_at_the_adopted_decimals(env_with_dossier):
    """The round 3 final review: C1 (the Mann-range clause in every text; "a to b" with a <= b), M3 (the upper end at
    the adopted value's decimals, 2.7 beside 2.5, not 2.68) and M2 (the README key states the unreddened assumption
    once, as C1(ii) words it)."""
    data = _bracketed(_stored(env_with_dossier), 2.48, 2.68)
    flag = "outside the Mann relations' range, so indicative"
    tail = (" from unreddened (E(B-V) = 0), adopted, to the full-column 0.100 (Schlafly and Finkbeiner "
            f"2011), {flag}")
    assert pack_module.rp_bracket_text(data) == ", 2.5 to 2.7" + tail
    assert str(pack_module.rp_bracket_text(data, True)) in " ".join(render_note(data).split())
    plain = pack_module.reddening_plain(data)
    assert f"planet radius 2.7 +0.2/-0.1 Earth radii, {flag}; the star sits about" in plain
    rows = {r[0]: r for r in _readme_table(render_readme(data, {}))}
    full = rows["Full column (Schlafly and Finkbeiner 2011)"]
    assert full[10] == "2.7 +0.2/-0.1" and full[-1] == f"upper bound, {flag}"
    key = pack_module.reddening_key(data)
    assert key.count("E(B-V) = 0") == 1 and "Unreddened (E(B-V) = 0), adopted while no 3D value" in key
    assert flag in key and "round 2" not in key and "0.1 or less" not in key and "at or below" not in key
    for text in (pack_module.star_texts(data), render_readme(data, {})):
        assert flag in text and "0 (assumed)" not in text
    # the two ends print alike, or arrive the other way round: no "2.5 to 2.5", and the smaller first
    assert pack_module.rp_bracket_text(_bracketed(data, 2.48, 2.52)) == ", unchanged at the printed precision" + tail
    assert pack_module.rp_bracket_text(_bracketed(data, 2.48, 2.31)).startswith(", 2.3 to 2.5 from ")
    inside = _bracketed(data, 2.48, 2.68, flagged=False)
    assert flag not in pack_module.rp_bracket_text(inside) and flag not in pack_module.reddening_plain(inside)
    assert flag not in pack_module.reddening_key(inside)


def test_a_3d_value_is_adopted_with_the_unreddened_star_and_gsp_phot_beside_it(env_with_dossier):
    data = _with_adopted_extinction(env_with_dossier)
    star = data["star"]
    assert star["extinction"]["state"] == "manual"
    tex = render_note(data)
    flat = " ".join(tex.split())
    sentence = str(pack_module.reddening_text(data, True))
    assert sentence in flat and r"\citep{green2019}" in sentence and "E(B-V) = 0.050 (0.030 to 0.070)" in sentence
    assert pack_text.value_text(star["unreddened"]["teff"], True) not in flat     # the note's one clause only
    assert "from unreddened" not in flat and "schlafly2011" not in flat
    cited, items = _cites(tex)
    assert cited == items and "green2019" in items
    assert r"Green, G.~M., Schlafly, E., Zucker, C., Speagle, J.~S., \& Finkbeiner, D. 2019, ApJ, 887, 93" in flat
    plain = pack_module.reddening_plain(data)
    assert plain.startswith("Reddening: E(B-V) 0.050 (16th to 84th percentile 0.030 to 0.070) from Bayestar19 "
                            "(Green et al. 2019), stored by hand (test fixture), at 303 pc, a sightline average")
    assert "unreddened (E(B-V) = 0), Teff" in plain and pack_text.value_text(star["teff"]) in plain
    assert "earlier assumption" not in plain
    readme = render_readme(data, {})
    rows = {r[0]: r for r in _readme_table(readme)}
    assert rows["Bayestar19 (Green et al. 2019)"][-1] == "adopted"
    assert rows["Bayestar19 (Green et al. 2019)"][1] == "0.050 (0.030 to 0.070)"
    assert rows["Unreddened"][-1] == "not adopted"
    assert rows["Full column (Schlafly and Finkbeiner 2011)"][-1] == "upper bound"
    prov = {r["parameter"]: r for r in pack_rows.provenance_rows(data)}
    assert {name for name, _key in pack_rows.EXTINCTION_ROWS} <= set(prov)
    ebv = prov["E(B-V) (Bayestar19)"]
    assert (ebv["value"], ebv["minus"], ebv["plus"]) == (0.05, 0.02, 0.02)
    assert ebv["reference"] == "Green et al. 2019; Gaia (E)DR3 extinction law; Cardelli et al. 1989"
    assert dossier_module.no_spectrum_title(star) == dossier_module.NO_SPECTRUM


def test_with_neither_value_the_texts_say_not_available(env_with_dossier):
    data = _stored(env_with_dossier)
    column = {"status": "not available", "reason": "irsa-dust timed out", "queried_utc": "2026-10-23T20:00:00+00:00"}
    data["star"]["extinction"] = {**data["star"]["extinction"], "column": column}
    assert pack_module.rp_bracket_text(data, True) is None
    flat = " ".join(render_note(data).split())
    assert "(Bayestar19 not available at build time)" in flat and "from unreddened" not in flat
    assert pack_module.reddening_plain(data) == (
        "Reddening: not measured (Bayestar19 not available at build time, and the IRSA full-column value could not "
        "be fetched), so the parameters are unreddened (E(B-V) = 0), adopted.")
    rows = {r[0]: r for r in _readme_table(render_readme(data, {}))}
    assert set(rows) == {"Unreddened", "GSP-Phot (Gaia DR3)"}
    assert pack_module.references(data)["schlafly2011"] is False


def test_an_uncharacterised_host_keeps_its_crowding_metric_in_the_readme():
    """Task 15 fix round 2, M5: the README's Star line prints the crowding metric for a star block with no
    parameters (no method, reddening, FLAME or habitable-zone text)."""
    golden = _golden()
    bare = {**golden, "star": {"route": "none", "crowdsap": golden["star"]["crowdsap"]}}
    text = pack_rows.crowdsap_text(bare)
    assert text and text.startswith("The KBonus crowding metric is C = 0.8565")
    readme = render_readme(bare, {})
    assert f"Star: {text}" in readme


def test_the_plain_texts_carry_the_height_clause_and_the_note_does_not(env_with_dossier):
    """Task 16 fix round 1, ruling (c) and minors 7 and 8."""
    data = _stored(env_with_dossier)
    e = data["star"]["extinction"]
    assert e["height_clause"].startswith(f"the star sits about {e['height_pc']} pc above the plane")
    plain = pack_module.reddening_plain(data)
    assert f"; {e['height_clause']}." in plain and e["height_clause"] not in render_note(data)
    assert "full-column E(B-V) of 0.100 ± 0.010" in plain
    rows = {r[0]: r for r in _readme_table(render_readme(data, {}))}
    assert rows["Full column (Schlafly and Finkbeiner 2011)"][1] == "0.100 ± 0.010"
    # minor 7: no GSP-Phot row, no GSP-Phot sentence in the key and no Andrae et al. 2023 for it
    bare = {**data, "star": {**data["star"], "ebpminrp_gspphot": None,
                             "gaia_cross_check": {**data["star"]["gaia_cross_check"], "teff_gspphot": None}}}
    assert "GSP-Phot (Gaia DR3)" not in {r["estimate"] for r in pack_module.reddening_rows(bare)}
    assert "GSP-Phot" not in pack_module.reddening_key(bare) and "andrae2023" not in pack_module.readme_works(bare)
    assert "GSP-Phot" in pack_module.reddening_key(data)
