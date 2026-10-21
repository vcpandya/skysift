"""The submission pack (dossiers plan Task 6): the folder and zip ``build_pack`` writes from a generated dossier, the
README's five steps, the AASTeX Research Note draft, the ExoFOP names, the provenance, and the ``skysift pack``
command with and without a Zenodo token. The dossier comes from ``dossier.generate`` on the dashboard seed with a
synthetic light curve and a fake Gaia fetch; Zenodo is a fake session, so nothing reaches the network."""
import csv
import hashlib
import json
import re
import zipfile
from datetime import UTC, datetime

import numpy as np
import pytest
from typer.testing import CliRunner

from skysift import net, zenodo
from skysift import pack as pack_module
from skysift.cli import app
from skysift.dossier import LightCurveReleased, generate, whitened
from skysift.pack import (
    PACK_VERSION,
    STEPS,
    NoDossier,
    build_pack,
    exofop_name,
    render_note,
    render_readme,
)
from skysift.zenodo import ZenodoClient
from tests.unit.test_dossier import _make_env, _pixels_npz, _store_pixels
from tests.unit.test_zenodo import DEPOSITION_ID, FakeSession
from tests.unit.ui_seed import STAGE2_TARGET

runner = CliRunner()
EM_DASH = chr(0x2014)
WARNING = ("This candidate is not yet approved by a reviewer. Build the record, but do not freeze or upload it before "
           "the approval.")
ISE = re.compile(r"\b(organis|characteris|summaris|localis|recognis|normalis|minimis|optimis)(e|ed|es|ing|ation)\b",
                 re.IGNORECASE)


def _today() -> str:
    return datetime.now(UTC).strftime("%Y%m%d")


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
    return {p.relative_to(rep.out_dir).as_posix() for p in rep.out_dir.rglob("*") if p.is_file()}


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
    assert rep.out_dir == env_with_dossier.settings.data_dir / "packs" / STAGE2_TARGET / _today()
    assert rep.zip_path == rep.out_dir.with_name(rep.out_dir.name + ".zip")
    with zipfile.ZipFile(rep.zip_path) as z:
        inside = {n.split("/", 1)[1] for n in z.namelist()}
        assert inside == names and len({n.split("/", 1)[0] for n in z.namelist()}) == 1   # one top folder
    assert sorted(rep.files) == sorted(names) and rep.approved is False and rep.zenodo is None
    assert rep.as_dict()["zip_path"] == str(rep.zip_path) and rep.as_dict()["files"] == rep.files


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
    assert f"exofop/TIC-unknown_{_today()}_skysift_centroids.png" in names
    assert f"exofop/TIC-unknown_{_today()}_skysift_centroids.svg" not in names          # one copy per figure
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
    queries = json.loads((rep.out_dir / "provenance" / "queries.json").read_text(encoding="utf-8"))
    assert queries == env.data["provenance"]["queries"] and all(q["sha256"] for q in queries)
    calib = json.loads((rep.out_dir / "provenance" / "calibration.json").read_text(encoding="utf-8"))
    assert calib["fap"]["n"] == 440 and calib["rows_by_version"] == {env.data["signal"]["detector_version"]: 220}


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
    with pytest.raises(FileExistsError, match="not a submission pack"):
        _build(env_with_dossier, out_dir=foreign)
    assert (foreign / "thesis.docx").read_bytes() == b"keep me"
    out = _build(env_with_dossier, out_dir=tmp_path / "custom")
    assert out.out_dir == tmp_path / "custom" and (tmp_path / "custom.zip").is_file()


def test_build_pack_without_a_dossier_raises(env_with_dossier):
    with pytest.raises(NoDossier, match="skysift dossier --target kic-000000001"):
        build_pack(env_with_dossier.store, env_with_dossier.blob, env_with_dossier.settings, "kic-000000001")


def test_a_released_light_curve_propagates(env_with_dossier):
    env = env_with_dossier
    env.store.mark_artifact_deleted(env.raw_sha)
    with pytest.raises(LightCurveReleased, match="--retention keep"):
        _build(env)
    assert env.store.dossier(STAGE2_TARGET)["pack_path"] == ""


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
    assert "Gaia DR3 2101234567890123462" in text and "TIC-unknown" in text           # how to find the TIC
    assert "password" in text.lower() and "never publishes" in text


def test_an_approved_candidate_has_no_warning_and_its_candidate_row_gets_the_path(env_with_dossier):
    env = env_with_dossier
    _approve(env)
    rep = _build(env)
    text = (rep.out_dir / "README.md").read_text(encoding="utf-8")
    assert rep.approved and "not yet approved" not in text and text.startswith("# Submission pack for KIC 6146676")
    assert env.store.dossier(STAGE2_TARGET)["pack_path"] == str(rep.out_dir)
    assert env.store.dossier(STAGE2_TARGET)["pack_built"] is not None
    from skysift.store.db import CandidateRow
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


def _body(tex: str) -> str:
    return re.sub(r"\\[a-zA-Z]+(\[[^\]]*\])?(\{[^}]*\})?", " ", tex.split(r"\begin{document}")[1])


def test_note_is_valid_aastex_under_a_thousand_words(env_with_dossier):
    data = env_with_dossier.data
    tex = render_note(data)
    body = _body(tex)
    assert tex.startswith(r"\documentclass[RNAAS]{aastex") and len(body.split()) < 1000
    assert "candidate" in tex and "discover" not in tex.lower() and EM_DASH not in tex and "---" not in tex
    assert tex.rstrip().endswith(r"\end{document}") and tex.count("{") == tex.count("}")
    assert "10.5281/zenodo.XXXXXXX" in tex and r"\includegraphics" in tex and tex.count(r"\begin{figure}") == 1
    cited = set(re.findall(r"\\cite[pt]?\{([^}]+)\}", tex))
    keys = {k.strip() for c in cited for k in c.split(",")}
    items = set(re.findall(r"\\bibitem\[[^\]]*\]\{([^}]+)\}", tex))
    assert keys and keys == items                                              # every citation listed, none unused
    assert {"mann2015", "mann2019", "kopparapu2013", "lindegren2021", "dressing2015"} <= items
    assert "fressin2013" not in items                                         # an M dwarf: the Dressing grid only
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
    assert tex.count("{") == tex.count("}") and len(_body(tex).split()) < 1000


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


def test_render_readme_takes_the_report_fields(env_with_dossier):
    text = render_readme(env_with_dossier.data, {"approved": True, "tic": "169822650", "date": "20261021",
                                                 "tag": "skysift", "files": ["README.md"], "png": True,
                                                 "zenodo": None, "stale_reason": None,
                                                 "zip_name": "x.zip", "pixels_figure": True})
    assert "not yet approved" not in text and "TIC 169822650" in text and "169822650_20261021_skysift_" in text


def test_exofop_names_follow_the_convention():
    name = exofop_name("169822650", "20261021", "skysift", "lightcurve", "csv")
    assert name == "169822650_20261021_skysift_lightcurve.csv"
    assert exofop_name(None, "20261021", "skysift", "dossier", "html").startswith("TIC-unknown_")
    assert exofop_name("TIC 169822650", "20261021", "skysift", "fold", "svg") == "169822650_20261021_skysift_fold.svg"
    with pytest.raises(ValueError):
        exofop_name("1698;rm", "20261021", "skysift", "fold", "svg")


def test_exofop_files_use_the_tic_when_given(env_with_dossier):
    rep = _build(env_with_dossier, tic="169822650", observer_tag="vpandya")
    exo = {n for n in _files(rep) if n.startswith("exofop/")}
    date = _today()
    assert {f"exofop/169822650_{date}_vpandya_lightcurve.csv", f"exofop/169822650_{date}_vpandya_ephemeris.csv",
            f"exofop/169822650_{date}_vpandya_dossier.html", f"exofop/169822650_{date}_vpandya_fold.svg",
            "exofop/note.txt"} <= exo
    assert "TIC 169822650" in (rep.out_dir / "exofop" / "note.txt").read_text(encoding="utf-8")
    assert not any("TIC-unknown" in n for n in exo)


# ---------------------------------------------------------------------------------------------------- Zenodo

def test_pack_with_zenodo_creates_a_draft_and_records_the_path(env_with_dossier, fake_session):
    env = env_with_dossier
    rep = _build(env, zenodo=ZenodoClient("t", session=fake_session))
    assert rep.zenodo["draft_url"].startswith("https://sandbox.zenodo.org/deposit/")
    assert env.store.dossier(STAGE2_TARGET)["pack_path"] == str(rep.out_dir)
    assert rep.zenodo == {"deposition_id": DEPOSITION_ID, "draft_url": f"https://sandbox.zenodo.org/deposit/{DEPOSITION_ID}",
                          "sandbox": True, "doi": f"10.5072/zenodo.{DEPOSITION_ID}"}
    methods = [(m, url.rsplit("/", 1)[-1]) for m, url, _b in fake_session.calls]
    assert methods == [("POST", "depositions"), ("PUT", str(DEPOSITION_ID)), ("PUT", rep.zip_path.name)]
    assert fake_session.calls[2][2] == rep.zip_path.read_bytes()
    assert fake_session.calls[1][2]["metadata"]["upload_type"] == "dataset"
    assert all("publish" not in url for _m, url, _b in fake_session.calls)
    tex = (rep.out_dir / "note" / "note.tex").read_text(encoding="utf-8")
    assert f"10.5072/zenodo.{DEPOSITION_ID}" in tex and "XXXXXXX" not in tex      # the reserved DOI is used
    assert "sandbox" in (rep.out_dir / "README.md").read_text(encoding="utf-8")


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
    assert report["out_dir"] == str(out) and report["zenodo"] is None and cli_env.session.calls == []
    assert cli_env.store.dossier(STAGE2_TARGET)["pack_path"] == str(out) and "a-secret" not in r.output


def test_cli_pack_with_a_token_makes_a_draft_and_prints_its_url(cli_env, monkeypatch):
    monkeypatch.setenv("SKYSIFT_ZENODO_TOKEN", "a-secret")
    r = runner.invoke(app, ["pack", "--target", STAGE2_TARGET, "--zenodo"])
    assert r.exit_code == 0, r.output
    assert f"https://sandbox.zenodo.org/deposit/{DEPOSITION_ID}" in r.stderr and "not published" in r.stderr
    assert cli_env.session.headers["Authorization"] == "Bearer a-secret" and "a-secret" not in r.output
    assert [m for m, _u, _b in cli_env.session.calls] == ["POST", "PUT", "PUT"]
    live = runner.invoke(app, ["pack", "--target", STAGE2_TARGET, "--zenodo-live"])
    assert live.exit_code == 0, live.output
    assert cli_env.session.calls[3][1] == "https://zenodo.org/api/deposit/depositions"
    assert json.loads(live.stdout)["zenodo"]["sandbox"] is False


def test_cli_pack_reports_a_zenodo_error_plainly(cli_env, monkeypatch):
    monkeypatch.setenv("SKYSIFT_ZENODO_TOKEN", "a-secret")
    cli_env.session.status = 500
    r = runner.invoke(app, ["pack", "--target", STAGE2_TARGET, "--zenodo"])
    assert r.exit_code == 1 and "Zenodo answered 500" in r.output and "Traceback" not in r.output
    assert "a-secret" not in r.output


def test_cli_pack_exits_2_when_the_light_curve_was_released(cli_env):
    cli_env.store.mark_artifact_deleted(cli_env.raw_sha)
    r = runner.invoke(app, ["pack", "--target", STAGE2_TARGET])
    assert r.exit_code == 2 and "--retention keep" in r.output and "Traceback" not in r.output


def test_cli_pack_without_a_dossier_exits_1(cli_env):
    r = runner.invoke(app, ["pack", "--target", "kic-000000001"])
    assert r.exit_code == 1 and "skysift dossier --target kic-000000001" in r.output and "Traceback" not in r.output
