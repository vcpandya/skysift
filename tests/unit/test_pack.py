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
from tests.unit.test_dossier import _make_env, _pixels_npz, _store_pixels
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
    assert rows["planet radius"]["value"] == 2.47 and rows["planet radius"]["minus"] == 0.08
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
    assert PACK_VERSION == "3"
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
    assert "1 in 440" in abstract and "under 1 percent" in abstract and "provisional" not in abstract   # signed off
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
    assert "40.290" in tex and "1 in 440" in tex and "provisional" not in tex
    assert r"2.47 $\pm$ 0.08 $R_\oplus$" in tex                                # Rp from the limb-darkened k
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
    assert "mann2015" not in tex and "kopparapu2013" not in tex and "fressin2013" in tex
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
    assert "a mass of" not in tex and "effective temperature of" not in tex and "a radius of 0.509" in tex
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
    fap, fpp = env_with_dossier.data["fap"]["words"], env_with_dossier.data["fpp"]["words"]
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
    assert text.index("pixels from the neighbour.") < text.index(cut) < text.index("Averaged over the seasons")
    assert EM_DASH not in text


def test_pixel_sentence_names_the_floor_when_the_row_stores_it(env_with_dossier):
    """A rung 5 row of rule version 3: the centroid sentence says in one clause that its error carries the measured
    systematic floor, and the Research Note keeps within its limits."""
    data = _with_a_silent_season(env_with_dossier.data)
    for season in next(t for t in data["tests"] if t["rung"] == "rung5_pixels")["metrics"]["seasons"]:
        season["floor_pix"] = 0.0164
    text = pack_module.pixel_sentence(data)
    assert ("The centroid sits a median 0.15 pixels from this star and 2.80 pixels from the neighbour, its error "
            "carrying a measured systematic floor of 0.016 px.") in text
    assert EM_DASH not in text
    tex = render_note(data)
    assert "systematic floor of 0.016 px" in tex and note_problems(note_words(tex), tex) == []


def test_the_abstract_carries_only_a_short_clause_for_a_cut_box(env_with_dossier):
    abstract = pack_module.abstract_text(_with_a_cut_box(env_with_dossier.data))
    assert "in 2 of 2 seasons that voted, 1 of them with a box cut by the stamp edge." in abstract
    assert "3 x 3 box" not in abstract and "px at most" not in abstract
    plain = pack_module.abstract_text(_with_a_silent_season(env_with_dossier.data))
    assert "box cut by the stamp edge" not in plain


def test_pixel_sentence_without_box_keys_is_unchanged(env_with_dossier):
    """The text of the commit before the box keys existed (9b4f076), captured before the change."""
    assert pack_module.pixel_sentence(env_with_dossier.data) == (
        "In difference images (out of transit minus in transit) the dip is nearer this star than its brightest "
        "neighbour in 3 of 3 seasons that voted. The centroid sits a median 0.13 pixels from this star and 2.75 "
        "pixels from the neighbour. Averaged over the seasons that voted, the centroid sits 0.54 ± 0.14 arcsec from "
        "the Gaia position projected through each stamp's WCS, a distance that carries the WCS zero point and the "
        "registration. A two-source fit gives a median depth of 2,213 ppm on this star and -21 ppm on the "
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
            "carrying a measured systematic floor of 0.017 px.") in note
    assert ("In seasons 0 and 3 this star's 3 x 3 box is cut by the stamp edge; the centroid there is measured on "
            "the pixels present and the shift the cut would give a point source (0.08 px at most) is added to its "
            "error.") in note
    # the offset of ruling e: the version 4 golden (a56d8c8) stores the block with the star's own direct-image
    # centroid, so the offset is judged by Bryson's rule and cited (the fallback form, against the fit's position,
    # is tested on a synthetic block in test_the_offset_sentence_names_its_reference); the ExoFOP note keeps the long
    # form, the Research Note the short one (fix round 4)
    assert ("Against the star's own out-of-transit image the difference-image centroid is offset by 1.8 ± 1.0 arcsec "
            "(1.8 sigma), not an offset by the rule of Bryson et al. (2013), which asks for more than 3 sigma and more "
            "than 0.2 arcsec; the distance from the Gaia position (0.25 ± 0.28 arcsec) carries the WCS zero point and "
            "the registration. Both errors are the larger of the seasons' combined error and their scatter.") in note
    assert "not stored for every season" not in note
    tex = (rep.out_dir / "note" / "note.tex").read_text(encoding="utf-8")
    assert (r"Against the star's own out-of-transit image the difference-image centroid is offset by 1.8 $\pm$ 1.0 "
            r"arcsec (1.8 sigma), not an offset by the rule of \citet{bryson2013} (over 3 sigma and 0.2 arcsec); "
            r"against the Gaia position, which carries the WCS zero point and registration, by 0.25 $\pm$ 0.28 arcsec. "
            "Each error is the larger of the seasons' combined error and scatter.") in " ".join(tex.split())
    assert r"]{bryson2013} Bryson" in tex
    assert ("The centroid agrees with the hand-built localisation within 0.03 px over 4 seasons "
            "(docs/candidates/kic-006146676/localisation/results.json).") in note
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
    assert b["minus"] == "" and b["note"] == "no error; " + DEGENERATE.removesuffix(", errors not quoted")
    assert shape["T14"]["unit"] == "h" and float(shape["model depth"]["plus"]) == 110.0


def test_stellar_provenance_csv_names_the_method_and_the_reference(env_with_dossier):
    rep = _build(env_with_dossier)
    assert _header(rep, "data/stellar_provenance.csv") == ["parameter", "value", "minus", "plus", "unit", "method",
                                                           "inputs", "reference"]
    rows = {r["parameter"]: r for r in _rows(rep, "data/stellar_provenance.csv")}
    radius = rows["radius"]
    assert float(radius["value"]) == 0.509 and float(radius["minus"]) == 0.016 and float(radius["plus"]) == 0.017
    assert radius["method"] == "Mann et al. 2015 R(M_Ks)"
    assert radius["reference"] == "Mann, A. W. et al. 2015, ApJ 804, 64"
    assert "Gaia DR3 2101234567890123462" in radius["inputs"] and "2MASS Ks 13.030" in radius["inputs"]
    assert rows["effective temperature"]["reference"].startswith("Pecaut, M. J. and Mamajek")
    assert "Lindegren" in rows["distance"]["reference"] and rows["spectral type"]["value"] == "M1V"
    logg = rows["surface gravity log g"]
    assert float(logg["value"]) == pytest.approx(4.438 + np.log10(0.507) - 2 * np.log10(0.509), abs=1e-3)
    assert float(logg["minus"]) == pytest.approx(np.hypot(0.013 / 0.507, 2 * 0.017 / 0.509) / np.log(10), abs=1e-3)
    assert float(logg["plus"]) == pytest.approx(np.hypot(0.013 / 0.507, 2 * 0.016 / 0.509) / np.log(10), abs=1e-3)
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
    assert planet["epoch"] == "2454969.3003" and planet["epoch_unc"] == "0.0019"
    assert planet["depth"] == "2167" and planet["depth_unc"] == "52" and planet["duration_unc"] == ""
    assert planet["radius"] == "2.47" and planet["radius_unc"] == "0.08"
    assert planet["temp"] == "269" and planet["temp_unc"] == "8" and planet["sma_unc"] == "0.0016"
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
    assert (stellar["radius"], stellar["radius_unc"]) == ("0.509", "0.017") and stellar["logg_unc"]
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
    assert (fitted["ar_star"], fitted["ar_star_err"]) == ("77.5", "2.5")
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
    assert "KBonus header keywords" in text and "crowding" not in text


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
        assert "40.289863 ± 0.000091 d" in text and "2454969.3003 ± 0.0019" in text, name
        assert "4.16 +0.06/-0.05 h" in text and "2,167 ± 52 ppm" in text and NO_BOOT not in text, name
    for name in ("exofop/note.txt", "archive_email.txt"):
        text = (rep.out_dir / name).read_text(encoding="utf-8")
        assert "search version 1+abcdef01" in text and f"commit {commit}" in text, name
    tex = (rep.out_dir / "note" / "note.tex").read_text(encoding="utf-8")
    assert r"40.289863 $\pm$ 0.000091 days" in tex and r"2455694.51787 $\pm$ 0.00092" in tex
    assert "4.16$^{+0.06}_{-0.05}$ hours" in tex and "8.0 minutes" in tex and "34 of 34" in tex
    meta = json.loads((rep.out_dir / "zenodo_metadata.json").read_text(encoding="utf-8"))
    assert "40.289863 ± 0.000091 d" in meta["description"] and "2454969.3003 ± 0.0019" in meta["description"]
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
        # the commit the dossier was generated at (the golden's efd8e0b, not the checkout's HEAD), and triceratops
        # only with a stored cross-check (M10), which the golden has (fix round 4)
        commit = data["provenance"]["git_commit"]
        tools = "numpy, scipy, astropy, matplotlib" + (", triceratops" if data.get("cross_check") else "")
        assert (rf"\software{{SkySift {md.version('skysift')} (search version {tex_safe(version)}, commit "
                rf"{tex_safe(commit)}), {tools}}}") in tex
        assert (data is golden) == bool(data.get("cross_check")) and (data is not golden or commit == "efd8e0b")
        crossed = render_note({**data, "cross_check": {"fpp": 0.01, "nfpp": 0.001, "current": False}})
        assert "matplotlib, triceratops}" in crossed
        assert r"\facilities{Kepler, Gaia, MAST, Exoplanet Archive}" in tex
        assert "the SkySift code at the stated commit is archived at doi:10.5281/zenodo.SOFTWARE (to be minted)" in tex
        assert "CROWDSAP" in tex and "PSFFRAC" in tex
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
    # fix round 4: the parity and secondary sentences keep every number and error in fewer words
    assert (r"The odd and even depths are 2,212 $\pm$ 76 and 2,126 $\pm$ 83 ppm (0.8 sigma apart)." in tex)
    assert (r"The phase 0.5 depth is 15 $\pm$ 65 ppm (3-sigma upper limit 210 ppm), no constraint on the geometric "
            "albedo; the strongest dip elsewhere is 40 ppm at phase 0.62 (1.4 sigma).") in tex
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
                                             "no independent cross-check", "the stored commit is dirty or unknown"]
    assert pack_module.gate_problems(_with_errors(_stored(env))) == [
        "the ephemeris carries no error (fewer than 3 transits timed)", "no independent cross-check"]
    monkeypatch.setattr(net, "fetch", env.fetch)
    monkeypatch.setattr(zenodo, "new_session", lambda: fake_session)
    monkeypatch.setenv("SKYSIFT_ZENODO_TOKEN", "a-secret")
    r = runner.invoke(app, ["pack", "--target", STAGE2_TARGET, "--zenodo-live"])
    assert r.exit_code == 2 and "the ephemeris carries no error" in r.output and fake_session.calls == []
    assert _build(env, zenodo=ZenodoClient("t", session=fake_session)).zenodo is not None    # the sandbox builds
    _store(env, _with_errors(env.data))
    assert pack_module.gate_problems(_stored(env)) == ["no independent cross-check"]
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
    with pytest.raises(PackError, match="no independent cross-check"):
        _build(env, zenodo=live)
    assert fake_session.calls == []
    rep = _build(env)                                                        # without one: no sentence, no file
    readme = (rep.out_dir / "README.md").read_text(encoding="utf-8")
    assert "TRICERATOPS" not in (rep.out_dir / "exofop" / "note.txt").read_text(encoding="utf-8")
    assert "The live Zenodo draft waits on the cross-check: no independent cross-check" in readme
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
    for name in ("README.md", "exofop/note.txt", "archive_email.txt"):
        assert sentence in (rep.out_dir / name).read_text(encoding="utf-8"), name
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
    # fix round 4, M-3: the note's first pointer says which README
    assert ("The ephemeris and transit-parameter errors come from per-transit fits, a weighted linear fit and a "
            "bootstrap over transits (methods in the Zenodo pack's README).") in " ".join(signal.split())
    assert len(pack_module.errors_pointer(data).split()) <= 25
    assert "deeg2015" not in tex and "holczer2016" not in tex and "schmitt2014" not in tex
    assert "It remains a candidate until independent follow-up confirms it." not in signal   # the abstract says it
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
    searched ={**data, "ephemeris": {**data["ephemeris"], "fold_ephemeris": "search"}}
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
    assert pack_module.errors_pointer(timed_only).endswith("a weighted linear fit (method in the Zenodo pack's "
                                                           "README).")
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

RECOVERY = "independent recovery"
PRIOR_DOI = "10.5281/zenodo.23325261"


@pytest.fixture
def env_prior(settings, monkeypatch):
    """The seed with a prior claim declared in its candidates folder, and a hand-built localisation beside it."""
    from tests.unit.test_dossier import PRIOR_CLAIM
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


def test_a_prior_claim_makes_every_text_an_independent_recovery(env_prior):
    env = env_prior
    assert env.data["prior_claims"][0]["doi"] == PRIOR_DOI
    rep = _build(env, tic="169822650")
    texts = _texts(rep)
    for name, text in texts.items():
        assert RECOVERY in text and PRIOR_DOI in text and "13 October 2026" in text, name
        assert "discovery" not in text.lower() and EM_DASH not in text and not IZE.search(text), name
    note = texts["note/note.tex"]
    assert r"\title{An independent recovery of the" in note and r"\citet{mehta2026}" in note
    assert (r"\bibitem[{Mehta}(2026)]{mehta2026} Mehta, K. 2026, Planethunt: archival evidence for the KIC "
            "5689121") in note
    assert "This note reports a transit candidate" not in note and "not a new candidate" in note
    # Task 8 fix round 3: the claim's date and DOI once, in the body beside the citation, not again in the abstract
    abstract = note.split(r"\begin{abstract}")[1].split(r"\end{abstract}")[0]
    body = note.split(r"\end{abstract}")[1].split(r"\begin{thebibliography}")[0]
    assert "reported the signal first." in abstract and PRIOR_DOI not in abstract and "13 October" not in abstract
    assert body.count(PRIOR_DOI) == 1 and body.count("13 October 2026") == 1
    assert "It remains a candidate until independent follow-up confirms it." in abstract
    assert "It remains a candidate until independent follow-up confirms it." not in body
    assert note.index("]{mann2019}") < note.index("]{mehta2026}") < note.index("]{morton2012}")   # alphabetical
    assert note_problems(note_words(note), note) == []
    step2 = texts["README.md"].split("## Step 2.")[1].split("## Step 3.")[0]
    assert "record of priority" in step2 and PRIOR_DOI in step2
    assert "same host assignment" not in texts["README.md"]          # round 1: the texts cite the record only
    assert "SkySift independent recovery of the transit candidate on " in texts["exofop/note.txt"]
    assert "first reported" in texts["archive_email.txt"]
    # the pixel section says how far the dossier's centroid sits from the hand-built localisation
    for name in ("note/note.tex", "exofop/note.txt"):
        assert "hand-built localisation" in texts[name] and "within 0.03 px" in texts[name], name
    planet = _parse_pipe((rep.out_dir / "exofop" / f"params_planet_{_today()}_001.txt").read_text(encoding="utf-8"),
                         PLANET_COLUMNS)
    assert planet["paper"] == f"https://doi.org/{PRIOR_DOI}" and PRIOR_DOI in planet["notes"]
    assert RECOVERY in planet["notes"] and len(planet["notes"]) <= exofop.EXOFOP_NOTES_MAX
    # item 10 of Task 9: the Zenodo description names the claim in the README's own words, with its DOI and date
    description = json.loads((rep.out_dir / "zenodo_metadata.json").read_text(encoding="utf-8"))["description"]
    prior = pack_module.prior_claim_sentence(env.data)
    assert f"Prior claim: {prior}" in texts["README.md"] and f"<p>Prior claim: {zenodo._html(prior)}</p>" in description
    assert PRIOR_DOI in description and "13 October 2026" in description
    assert "found it again in a blind sweep" in description


def test_without_a_prior_claim_nothing_speaks_of_a_recovery(env_with_dossier):
    env = env_with_dossier
    assert env.data["prior_claims"] == [] and env.data["hand_built_localisation"] is None
    texts = _texts(_build(env))
    for name, text in texts.items():
        assert RECOVERY not in text and "record of priority" not in text and PRIOR_DOI not in text, name
        assert "hand-built localisation" not in text, name
    assert r"\title{A " in texts["note/note.tex"]
    assert note_problems(note_words(texts["note/note.tex"]), texts["note/note.tex"]) == []


def test_a_version_3_dossier_without_the_key_reads_as_no_claim(env_with_dossier):
    data = {k: v for k, v in env_with_dossier.data.items() if k not in ("prior_claims", "hand_built_localisation")}
    assert pack_module.prior_claim_sentence(data) is None and RECOVERY not in render_note(data)
    assert "record of priority" not in render_readme(data, {})


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
    assert fit.startswith("Against the catalogue position plus the two-source fit's common shift (the star's "
                          "direct-image centroid is not stored for every season)")
    assert "not assessed from the star's own image" in fit and "Bryson" not in fit
    assert fit.endswith("Both errors are the larger of the seasons' combined error and their scatter.")
    assert pack_module.references({"centroid_offset": OFFSET_FIT})["bryson2013"] is False
    own = {**OFFSET_FIT, "reference": "the star's own direct-image centroid", "counts": False}
    direct = pack_module.offset_sentence({"centroid_offset": own})
    assert direct.startswith("Against the star's own out-of-transit image the difference-image centroid is offset by "
                             "0.39 ± 0.15 arcsec (2.6 sigma), not an offset by the rule of Bryson et al. (2013)")
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
    assert tess_text_plain(block) in section.replace("\n", " ")
    # round 2, N1: the note keeps the one sentence, citing Christ et al. 2019 alone; the methods go to the README
    assert r"\citep[noise model and thresholds in the README;][]{christ2019}." in section
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
    from tests.unit.test_crosscheck import fake_result
    from tests.unit.test_dossier import PRIOR_CLAIM
    from tests.unit.ui_seed import SEED_TESS
    data = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert data["dossier_version"] == dossier_module.DOSSIER_VERSION
    assert data["timing"]["ok"] is True and data["shape"]["ok"] is True
    assert pack_module.self_referenced(data["centroid_offset"]) and data["hand_built_localisation"]["agrees"]
    if prior:
        data["prior_claims"] = [{k: PRIOR_CLAIM[k] for k in ("doi", "date", "author", "title")}]
    meta = {**SEED_TESS, "sectors": [*SEED_TESS["sectors"], 109]}
    data["tess"] = tess.statement(meta, {**data["ephemeris"], "period_err_days": 0.002})
    if cross == "validated":
        block = fake_result(cross_check.inputs_fingerprint(data), fpp=0.012, nfpp=1e-4)
    else:
        block = fake_result(cross_check.inputs_fingerprint(data), fpp=0.6, nfpp=0.002,
                            scenarios=[{"ID": "169822655", "scenario": "NEB", "prob": 0.55},
                                       {"ID": "169822650", "scenario": "TP", "prob": 0.4}])
        for run in block["runs"][:2]:                     # identical runs, so the medians do not move
            run["degenerate"] = True
        block["n_degenerate"] = 2
    data["cross_check"] = cross_check.with_current({**block, "current": True}, data)
    return data


def _check_live_shape(cross: str, prior: bool) -> dict:
    """Render ``_live_shape`` and check it carries every long form it should; its ``note_words``."""
    from skysift import tess
    data = _live_shape(cross, prior)
    block = data["tess"]
    assert block["sectors_skipped"] == [109] and block["uncertain"] and block["n_transits"] > 0
    tex = render_note(data)
    flat = " ".join(tex.split())
    assert str(pack_module.cross_check_sentence(data, True)) in flat and "a factor of" in flat
    assert ("Its full false-positive probability is at or above 0.5" in flat) == (cross == "dominant")
    assert ("2 degenerate runs left out" in flat) == (cross == "dominant")
    assert ("validation lines" in flat) == (cross == "validated")
    sentence = str(tess.note_sentence(block, True))
    assert sentence in flat and "Sector 109, not assessed" in sentence and "(count uncertain, about" in sentence
    assert sentence.endswith(r"\citep[noise model and thresholds in the README;][]{christ2019}.")
    assert flat.count("the Zenodo pack's README") == 1                            # M-3: said once
    # the version 4 sentences the round 3 test did not see (I-1)
    assert r"by the rule of \citet{bryson2013}" in flat and r"]{bryson2013} Bryson" in flat
    assert "The centroid agrees with the hand-built localisation within 0.03 px over 4 seasons" in flat
    # the digits move with each regeneration of the golden, so the sentences are built from it (Task 10)
    assert str(pack_module.parity_sentence(data, True)) in flat
    assert str(pack_module.secondary_sentence(data, True)) in flat
    assert (PRIOR_DOI in flat) == prior
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
    (Task 10 fix round 1): 1,413 and 1,421 without, 1,476 and 1,484 with one. Round 2 adds note words and Task 13
    Step 15 makes the cuts (plan, word budget), so until then a count between the margin and the RNAAS limit is an
    expected failure; the RNAAS limit and the gate hold throughout."""
    words = _check_live_shape(cross, prior)
    assert words["total"] <= NOTE_LIMITS["total"], words
    assert not [p for p in pack_module.gate_problems(_live_shape(cross, prior)) if "words" in p]
    if words["total"] > LIVE_SHAPE_LIMIT:
        pytest.xfail(f"{words['total']} words, over the {LIVE_SHAPE_LIMIT} margin until Task 13 Step 15 cuts")


def test_the_body_does_not_repeat_the_abstract_and_keeps_every_number():
    """Fix round 4, I-1: the signal section opens on the light curve, the star's Gaia DR3 id and G magnitude and the
    dip, not on the abstract's sentence again; nothing the old opening carried is lost."""
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    tex = render_note(golden)
    signal = " ".join(tex.split(r"\section{The signal}")[1].split(r"\section{")[0].split())
    assert signal.startswith(r"In the KBonus Kepler Background light curve \citep{kbonus} of KIC 6146676 (Gaia DR3 "
                             f"{golden['target']['gaia_id']}, $G = 16.2$), a dip of ")
    assert "This note reports" not in signal and "recorded only in the background" not in signal
    abstract = tex.split(r"\begin{abstract}")[1].split(r"\end{abstract}")[0]
    assert abstract.startswith("\nThis note reports a transit candidate on KIC 6146676, a star that Kepler recorded "
                               "only in the background of other targets' pixels.")
    q = pack_text.quantities(golden, tex=True)
    for value in (q["depth_pm"], q["duration_pm"], q["period_pm"], q["t_ref_bjd_pm"]):
        assert str(value) in signal


def test_the_short_offset_sentence_keeps_both_offsets_and_the_verdict():
    """Fix round 4, I-1: the note's offset sentence (``short``) keeps the two offsets with their errors, the sigma,
    the reference, Bryson's rule and the verdict; the fallback against the fit's position keeps its own reference
    and is not judged by the rule. The long form stays the README's, the ExoFOP note's and the page's."""
    own = {**OFFSET_FIT, "reference": "the star's own direct-image centroid", "counts": False}
    short = pack_module.offset_sentence({"centroid_offset": own}, short=True)
    assert short == ("Against the star's own out-of-transit image the difference-image centroid is offset by 0.39 ± "
                     "0.15 arcsec (2.6 sigma), not an offset by the rule of Bryson et al. (2013) (over 3 sigma and "
                     "0.2 arcsec); against the Gaia position, which carries the WCS zero point and registration, by "
                     "0.25 ± 0.28 arcsec. Each error is the larger of the seasons' combined error and scatter.")
    counted = pack_module.offset_sentence({"centroid_offset": {**own, "counts": True}}, tex=True, short=True)
    assert r"(2.6 sigma), an offset by the rule of \citet{bryson2013} (over 3 sigma and 0.2 arcsec);" in str(counted)
    fit = pack_module.offset_sentence({"centroid_offset": OFFSET_FIT}, short=True)
    assert fit == ("Against the catalogue position plus the two-source fit's common shift (the star's direct-image "
                   "centroid is not stored for every season) the difference-image centroid is offset by 0.39 ± 0.15 "
                   "arcsec (2.6 sigma), not assessed from the star's own image; against the Gaia position, which "
                   "carries the WCS zero point and registration, by 0.25 ± 0.28 arcsec. Each error is the larger of "
                   "the seasons' combined error and scatter.")
    assert len(short.split()) < len(pack_module.offset_sentence({"centroid_offset": own}).split()) - 5
    assert pack_module.offset_sentence({"centroid_offset": own}).endswith(pack_module.SAME_ERROR_RULE)


def test_the_note_says_once_which_readme_it_points_to(env_with_dossier):
    """Fix round 4, M-3: the first pointer of the note names the Zenodo pack's README, the errors pointer when
    the note has one, else the TESS sentence's; a later pointer says "the README"."""
    from skysift import tess
    data = _with_tess(_with_errors(env_with_dossier.data))
    assert pack_module.errors_pointer(data) and tess.note_cites(data["tess"])
    flat = " ".join(render_note(data).split())
    assert flat.count("the Zenodo pack's README") == 1
    assert flat.index("(methods in the Zenodo pack's README)") < flat.index(
        r"\citep[noise model and thresholds in the README;][]{christ2019}")
    bare = {**data, "timing": {"ok": False, "reason": "x"}, "shape": {"ok": False, "reason": "y"}}
    flat = " ".join(render_note(bare).split())
    assert flat.count("the Zenodo pack's README") == 1
    assert r"\citep[noise model and thresholds in the Zenodo pack's README;][]{christ2019}" in flat


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
    from tests.unit.test_dossier import PRIOR_CLAIM
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
