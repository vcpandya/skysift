"""The Dossiers section of the dashboard (dossiers plan Task 5): the tiles with their two separate probabilities, the
dossier page in both modes, the standalone download, the dossier jobs, the pack steps, the planet thumbnail and the
Probabilities block of the verdict popup. TestClient against the dashboard seed with one hand-made dossier."""
import html as html_module
import json
import re
import sys
import xml.etree.ElementTree as ET
from types import SimpleNamespace

import pytest

from skysift import dossier as ds
from skysift.ui.jobs import DossierJobs, DossierRequest
from skysift.ui.planet_svg import look_sentence, planet_svg
from tests.unit.test_ui_pages import make_seeded_app
from tests.unit.ui_seed import APPROVED_TARGET, STAGE1_TARGET, STAGE2_TARGET, seed_dossier

SECTION_IDS = ("signal", "repeat", "checks", "pixels", "star", "look", "caveats", "reproduce")
LONG_JOB = [sys.executable, "-c", "import time; print('dossier job', flush=True); time.sleep(30)"]


@pytest.fixture
def seeded(settings, tmp_path):
    client, app, ids, _launcher = make_seeded_app(settings, tmp_path)
    return client, app, ids


@pytest.fixture
def seeded_dossier(seeded, settings):
    client, app, ids = seeded
    data = seed_dossier(app.state.store, settings.data_dir, target_id=STAGE2_TARGET)
    return client, app, ids, data


@pytest.fixture
def fake_jobs(seeded_dossier, settings):
    _client, app, _ids, _data = seeded_dossier
    jobs = DossierJobs(settings.data_dir, command=LONG_JOB)
    app.state.dossier_jobs = jobs
    yield jobs
    jobs.stop()


def token(app) -> str:
    return app.state.token


# ---- the Dossiers page

def test_dossiers_page_lists_the_final_set_in_order(seeded_dossier):
    client, _app, _ids, _data = seeded_dossier
    r = client.get("/dossiers")
    html = r.text
    assert r.status_code == 200
    assert html.index(APPROVED_TARGET) < html.index(STAGE2_TARGET)              # approved first
    assert "False alarm" in html and "1 in 1,700" in html and "about 3 percent" in html and "provisional" in html
    assert 'role="img"' in html and "times the size of Earth" in html
    assert STAGE1_TARGET in html                        # a stage-1 candidate is in the final set even without a dossier (21 October 2026)
    assert "False positive" in html and "1 in 1,700 about 3 percent" not in html     # two pills, never one number


def test_dossiers_page_shows_a_stage_one_dossier_and_every_version(seeded_dossier, settings):
    client, app, _ids, _data = seeded_dossier
    seed_dossier(app.state.store, settings.data_dir, target_id=STAGE1_TARGET)
    html = client.get("/dossiers").text
    assert html.index(STAGE2_TARGET) < html.index(STAGE1_TARGET)                # stage 2 before stage 1
    assert client.get("/dossiers?versions=all").status_code == 200
    assert client.get("/dossiers?versions=bogus").status_code == 400


def test_dossiers_page_empty_state(seeded):
    client, _app, _ids = seeded
    html = client.get("/dossiers").text
    assert "No dossiers yet" in html and 'href="/candidates"' in html


def test_dossiers_page_empty_state_without_candidates(settings):
    from fastapi.testclient import TestClient

    from skysift.store.blob import blob_from_url
    from skysift.store.db import Store
    from skysift.ui.app import create_app
    store = Store(settings.database_url); store.init_schema()
    client = TestClient(create_app(settings, store=store, blob=blob_from_url(settings.blob_url)),
                        base_url="http://127.0.0.1")
    main = client.get("/dossiers").text.split('id="main"')[1]
    assert "No dossiers yet. A dossier is generated for a candidate of stage 1 or 2, an approved one, or one the pixel stage could not decide." in main
    assert "Run a sweep, then a recheck." in main and 'href="/sources"' in main and 'href="/candidates"' in main


def test_nav_has_dossiers_between_candidates_and_live(seeded):
    client, _app, _ids = seeded
    nav = client.get("/dossiers").text.split('class="topnav"')[1].split("</nav>")[0]
    assert nav.index('href="/candidates"') < nav.index('href="/dossiers"') < nav.index('href="/live"')
    assert 'href="/dossiers" aria-current="page"' in nav


def test_about_glossary_explains_the_two_probabilities(seeded):
    client, _app, _ids = seeded
    html = client.get("/about").text
    assert "<dt>False alarm</dt>" in html and "<dt>False positive</dt>" in html


def test_tile_without_a_dossier_offers_generate_and_marks_stale_ones(seeded_dossier):
    client, app, ids, _data = seeded_dossier
    html = client.get("/dossiers").text
    approved = html.split(f'id="tile-{APPROVED_TARGET}"')[1].split("</article>")[0]
    assert f'hx-post="/dossiers/{APPROVED_TARGET}/generate"' in approved and "Generate" in approved
    assert "no star parameters" in approved
    current = html.split(f'id="tile-{STAGE2_TARGET}"')[1].split("</article>")[0]
    assert "stale" not in current and "Generated" in current and f'href="/dossiers/{STAGE2_TARGET}"' in current
    app.state.store.decide(ids["stage2"], "needs_data", "want a second season")
    current = client.get("/dossiers").text.split(f'id="tile-{STAGE2_TARGET}"')[1].split("</article>")[0]
    assert "stale" in current and f'hx-post="/dossiers/{STAGE2_TARGET}/generate"' in current


def test_tile_and_dossier_rank_a_target_s_signals_the_same_way(seeded_dossier, settings):
    from skysift import dossier as ds
    from skysift.ui import queries
    from tests.unit.ui_seed import add_older_version
    client, app, ids, _data = seeded_dossier
    store = app.state.store
    other = add_older_version(store, STAGE2_TARGET)             # stage 1 of an older search, SDE 30, then approved
    store.decide(other, "approve", "clean")
    best = ds.pick_signal(store, STAGE2_TARGET)["id"]
    assert best == ids["stage2"]                                # stage 2 ranks above an approved stage-1 signal
    seed_dossier(store, settings.data_dir, target_id=STAGE2_TARGET)          # regenerated
    for version in (None, queries.current_search_version(store)):
        row = next(r for r in queries.final_set(store, version) if r["target_id"] == STAGE2_TARGET)
        assert row["signal_id"] == best and row["stale"] is False and row["stale_reason"] is None
    tile = client.get(f"/partials/dossiers/{STAGE2_TARGET}/tile").text
    assert f'data-dialog-src="/partials/signals/{best}/verdict"' in tile and "stale" not in tile
    assert "stale" not in client.get(f"/dossiers/{STAGE2_TARGET}").text.split("dossier-sections")[0]


def test_tile_pills_open_the_verdict_at_the_probabilities(seeded_dossier):
    client, _app, ids, _data = seeded_dossier
    tile = client.get(f"/partials/dossiers/{STAGE2_TARGET}/tile").text
    assert tile.count(f'data-dialog-src="/partials/signals/{ids["stage2"]}/verdict"') >= 2
    assert 'data-dialog-at="probabilities-popup"' in tile and "<html" not in tile
    assert client.get("/partials/dossiers/kic-000000001/tile").status_code == 404


# ---- the dossier page

def test_dossier_page_renders_the_eight_sections_in_both_modes(seeded_dossier):
    client, _app, ids, _data = seeded_dossier
    for mode in ("simple", "expert"):
        client.cookies.set("skysift_mode", mode)
        html = client.get(f"/dossiers/{STAGE2_TARGET}").text
        assert f'data-mode="{mode}"' in html
        for sid in SECTION_IDS:
            assert f'id="{sid}"' in html
        assert "This is about noise." in html and "not a vespa" in html
        assert '<nav class="dossier-sections" aria-label="Sections">' in html
        assert 'id="dossier-data"' in html and "/static/dossier.js" in html and "/static/dossier.css" in html
        assert f'href="/signals/{ids["stage2"]}"' in html and "Open in the candidate queue" in html
        assert f'href="/dossiers/{STAGE2_TARGET}/export.html"' in html and "Download standalone page" in html
        assert f'hx-post="/signals/{ids["stage2"]}/decide"' in html                    # the decision bar
        assert "provisional" in html and "—" not in html
    assert "stale" not in html
    expert = client.get(f"/dossiers/{STAGE2_TARGET}?mode=expert").text
    assert "0 of 1,699 null searches reached SDE 50.5" in expert and "BEB" in expert         # counts, factor table


def test_dossier_page_reads_the_pixel_seasons(seeded_dossier, settings):
    client, app, ids, _data = seeded_dossier
    html = client.get(f"/dossiers/{STAGE2_TARGET}").text
    pixels = html.split('id="pixels"')[1].split("</section>")[0]
    assert "<table" in pixels and "season" in pixels.lower()                       # the screen-reader table
    assert "200 Monte Carlo draws" not in pixels                                   # every season far from 3 sigma
    _set_rung5_seasons(app, ids["stage2"], [
        {"season": 0, "voting": True, "non_voting_reason": None, "separation_sigma": 3.2,
         "centroid_sigma_axis_pix": 0.04, "nearer": "target", "dist_target_pix": 0.2, "dist_neighbour_pix": 2.9},
        {"season": 1, "voting": False, "non_voting_reason": "box incomplete: neighbour", "separation_sigma": None,
         "centroid_sigma_axis_pix": None, "nearer": None, "dist_target_pix": None, "dist_neighbour_pix": None}])
    seed_dossier(app.state.store, settings.data_dir, target_id=STAGE2_TARGET)
    pixels = client.get(f"/dossiers/{STAGE2_TARGET}").text.split('id="pixels"')[1].split("</section>")[0]
    assert ("The centroid error comes from 200 Monte Carlo draws, so a season this close to the 3 sigma threshold can "
            "move by about 5 percent with the seed.") in pixels
    assert "box incomplete: neighbour" in pixels and "not voting" in pixels


def test_pixel_seasons_list_voting_first_and_a_missing_vote_as_not_recorded(seeded_dossier, settings):
    client, app, ids, _data = seeded_dossier
    far = {"separation_sigma": 4.2, "centroid_sigma_axis_pix": 0.04, "nearer": "target", "dist_target_pix": 0.2,
           "dist_neighbour_pix": 2.9}
    _set_rung5_seasons(app, ids["stage2"], [
        {"season": 0, **far},                                                    # stored before seasons voted
        {"season": 1, "voting": False, "non_voting_reason": "too few transits", **far},
        {"season": 3, "voting": True, **far}, {"season": 2, "voting": True, **far}])
    data = seed_dossier(app.state.store, settings.data_dir, target_id=STAGE2_TARGET)
    assert not [c for c in data["caveats"] if c.get("key") == "close_call"]      # no season both voting and near
    pixels = client.get(f"/dossiers/{STAGE2_TARGET}").text.split('id="pixels"')[1].split("</section>")[0]
    rows = pixels.split("<tbody>")[1].split("</tbody>")[0].split("<tr")[1:]
    assert [r.split('<th scope="row">')[1].split("<")[0] for r in rows] == ["2", "3", "1", "0"]     # voting first
    assert "vote not recorded" in rows[3] and 'class="is-muted"' in rows[3] and "voting</td>" not in rows[3]
    assert "not voting: too few transits" in rows[2] and 'class="is-muted"' in rows[2]
    assert 'class="is-muted"' not in rows[0] and "voting</td>" in rows[0]
    assert "200 Monte Carlo draws" not in pixels


def test_the_monte_carlo_sentence_comes_from_the_dossier_caveats(seeded_dossier, settings):
    from pathlib import Path
    client, app, _ids, data = seeded_dossier
    path = Path(app.state.store.dossier(STAGE2_TARGET)["data_path"])
    sentence = "The centroid error comes from 200 Monte Carlo draws"
    caveat = {"key": "close_call", "title": "A season sits close to the 3 sigma line", "text": f"{sentence}, so."}
    path.write_text(json.dumps({**data, "caveats": [caveat]}), encoding="utf-8")
    html = client.get(f"/dossiers/{STAGE2_TARGET}").text
    assert sentence in html.split('id="pixels"')[1].split("</section>")[0]
    assert sentence in html.split('id="caveats"')[1].split("</section>")[0]


def _set_rung5_seasons(app, signal_id, seasons):
    from skysift.store.db import TestRow
    with app.state.store.session() as s:
        row = (s.query(TestRow).filter(TestRow.signal_id == signal_id, TestRow.rung == "rung5_pixels")
               .order_by(TestRow.id.desc()).first())
        row.metrics = {**row.metrics, "seasons": seasons}


def test_dossier_page_shows_section_errors_as_notices(seeded_dossier, settings):
    client, app, _ids, data = seeded_dossier
    row = app.state.store.dossier(STAGE2_TARGET)
    from pathlib import Path
    broken = {**data, "pixels": None, "section_errors": {"pixels": {"error": "bad zip file"}}}
    Path(row["data_path"]).write_text(json.dumps(broken), encoding="utf-8")
    html = client.get(f"/dossiers/{STAGE2_TARGET}").text
    assert "could not be built" in html and "bad zip file" in html


def test_dossier_page_404_and_stale_banner(seeded_dossier):
    client, app, ids, _data = seeded_dossier
    r = client.get("/dossiers/kic-000000001")
    assert r.status_code == 404 and "No dossier" in r.text
    r = client.get(f"/dossiers/{APPROVED_TARGET}")
    assert r.status_code == 404 and f'hx-post="/dossiers/{APPROVED_TARGET}/generate"' in r.text
    app.state.store.decide(ids["stage2"], "approve", "clean")
    html = client.get(f"/dossiers/{STAGE2_TARGET}").text
    assert 'class="dossier-stale' in html and "stale" in html and "Regenerate" in html


def test_export_download_is_one_file(seeded_dossier):
    client, app, _ids, _data = seeded_dossier
    r = client.get(f"/dossiers/{STAGE2_TARGET}/export.html")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    assert r.headers["content-disposition"].endswith('_dossier.html"') and "<script src=" not in r.text
    assert f'filename="{STAGE2_TARGET}_dossier.html"' in r.headers["content-disposition"]
    path = app.state.store.dossier(STAGE2_TARGET)["export_path"]
    assert path and path.endswith(f"{STAGE2_TARGET}_dossier.html")
    assert client.get(f"/dossiers/{STAGE2_TARGET}/export.html").text == r.text                # served from the cache
    assert client.get("/dossiers/kic-000000001/export.html").status_code == 404
    link = client.get(f"/dossiers/{STAGE2_TARGET}").text.split(f'href="/dossiers/{STAGE2_TARGET}/export.html"')[1]
    assert not link.split(">")[0].strip()                       # no download attribute: the route names the file
    assert r.headers["content-disposition"].startswith("attachment;")
    assert "No data for this figure." in r.text and "could not be read" in r.text            # dossier.js guards
    assert "The star beside the Sun, to scale." in r.text and "vote not recorded" in r.text   # its own captions


# ---- jobs

def test_dossier_request_argv_and_validation():
    req = DossierRequest("dossier", STAGE2_TARGET)
    req.validate()
    assert req.argv("py") == ["py", "-m", "skysift", "dossier", "--target", STAGE2_TARGET] and req.env() == {}
    assert DossierRequest("dossier", STAGE2_TARGET, force=True).argv("py")[-1] == "--force"
    assert DossierRequest("pack", STAGE2_TARGET).argv("py") == ["py", "-m", "skysift", "pack", "--target",
                                                                STAGE2_TARGET]
    for bad in (DossierRequest("run", STAGE2_TARGET), DossierRequest("dossier", "../etc"),
                DossierRequest("dossier", ""), DossierRequest("dossier", "kic 1; rm")):
        with pytest.raises(ValueError):
            bad.validate()


def test_dossier_jobs_live_apart_from_the_sweeps(settings):
    jobs = DossierJobs(settings.data_dir, command=[sys.executable, "-c", "print('done')"])
    assert jobs.dir == settings.data_dir / "ui" / "dossier_jobs"
    assert jobs.stop_file.parent == jobs.dir                           # never the sweep's stop request


def test_dossier_jobs_run_below_normal_priority(settings, monkeypatch):
    import skysift.ui.sweeps as sw
    seen = {}

    def fake_popen(argv, **kwargs):
        seen.update(argv=argv, **kwargs)
        raise OSError("not started in this test")
    monkeypatch.setattr(sw.subprocess, "Popen", fake_popen)
    jobs = DossierJobs(settings.data_dir)
    assert jobs.below_normal is True
    with pytest.raises(OSError):
        jobs.start(DossierRequest("dossier", STAGE2_TARGET, force=True))
    assert seen["argv"][-5:] == ["skysift", "dossier", "--target", STAGE2_TARGET, "--force"]
    assert {k: seen[k] for k in sw.spawn_options(True)} == sw.spawn_options(True)
    assert jobs.active() is None


def test_generate_button_starts_one_job_and_refuses_a_second(seeded_dossier, fake_jobs):
    client, app, _ids, _data = seeded_dossier
    assert client.post(f"/dossiers/{STAGE2_TARGET}/generate").status_code == 403         # no token
    r = client.post(f"/dossiers/{APPROVED_TARGET}/generate", data={"_token": token(app)})
    assert r.status_code == 200 and "generating" in r.text.lower()
    assert f'hx-get="/partials/dossiers/{APPROVED_TARGET}/status"' in r.text and 'hx-trigger="every 5s"' in r.text
    active = fake_jobs.active()
    assert active["request"]["kind"] == "dossier" and active["request"]["target_id"] == APPROVED_TARGET
    r = client.post(f"/dossiers/{STAGE2_TARGET}/generate", data={"_token": token(app)})
    assert r.status_code == 409 and "another dossier job is running" in r.headers["HX-Trigger"]
    tile = client.get(f"/partials/dossiers/{APPROVED_TARGET}/tile").text
    assert "every 5s" in tile                                                   # still polling while it runs
    assert "every 5s" not in client.get(f"/partials/dossiers/{STAGE2_TARGET}/tile").text
    fake_jobs.stop()
    assert "every 5s" not in client.get(f"/partials/dossiers/{APPROVED_TARGET}/tile").text
    r = client.post("/dossiers/kic-000000001/generate", data={"_token": token(app)})
    assert r.status_code == 404


def test_only_the_tile_s_foot_polls_and_the_forms_swap_their_own_tile(seeded_dossier, fake_jobs):
    client, app, _ids, _data = seeded_dossier
    html = client.get("/dossiers").text
    tile = html.split(f'id="tile-{STAGE2_TARGET}"')[1].split("</article>")[0]
    assert "every 5s" not in tile
    assert 'hx-target="#tile-' not in html and 'hx-target="closest article"' in html
    r = client.post(f"/dossiers/{APPROVED_TARGET}/generate", data={"_token": token(app)})
    article = r.text.split("<article")[1].split(">")[0]
    assert "hx-get" not in article and "hx-trigger" not in article                 # the article itself never polls
    foot = r.text.split(f'id="tile-foot-{APPROVED_TARGET}"')[1].split(">")[0]
    assert 'aria-live="polite"' in foot and 'hx-trigger="every 5s"' in foot and 'hx-swap="innerHTML"' in foot
    assert f'hx-get="/partials/dossiers/{APPROVED_TARGET}/status"' in foot
    status = client.get(f"/partials/dossiers/{APPROVED_TARGET}/status")
    assert status.status_code == 204 and status.text == ""                       # running: nothing to swap
    fake_jobs.stop()
    status = client.get(f"/partials/dossiers/{APPROVED_TARGET}/status")
    assert status.status_code == 286 and "<article" not in status.text            # ended: swap and stop polling
    assert f'id="tile-main-{APPROVED_TARGET}" hx-swap-oob="true"' in status.text and "Generate" in status.text
    gone = client.get("/partials/dossiers/kic-000000001/status")
    assert gone.status_code == 286 and "no longer available" in gone.text      # stops the poll
    assert client.get("/partials/dossiers/bad%20id;x/status").status_code == 400


def test_the_pack_is_offered_and_started_only_for_an_approved_candidate(seeded_dossier, fake_jobs, settings):
    client, app, ids, _data = seeded_dossier
    tile = client.get(f"/partials/dossiers/{STAGE2_TARGET}/tile").text
    assert "Build submission pack" not in tile and "/pack" not in tile
    r = client.post(f"/dossiers/{STAGE2_TARGET}/pack", data={"_token": token(app)})
    assert r.status_code == 409 and "only for an approved candidate" in r.headers["HX-Trigger"]
    assert fake_jobs.active() is None
    r = client.post(f"/dossiers/{APPROVED_TARGET}/pack", data={"_token": token(app)})
    assert r.status_code == 400 and "Generate the dossier first" in r.headers["HX-Trigger"]
    app.state.store.decide(ids["stage2"], "approve", "clean")
    seed_dossier(app.state.store, settings.data_dir, target_id=STAGE2_TARGET)
    assert "Build submission pack" in client.get(f"/partials/dossiers/{STAGE2_TARGET}/tile").text
    r = client.post(f"/dossiers/{STAGE2_TARGET}/pack", data={"_token": token(app)})
    assert r.status_code == 200 and fake_jobs.active()["request"]["kind"] == "pack"


def test_pack_steps_toggle_with_the_token(seeded_dossier):
    client, app, _ids, _data = seeded_dossier
    url = f"/dossiers/{STAGE2_TARGET}/steps"
    assert client.post(url, data={"step": "2", "done": "1"}).status_code == 403
    r = client.post(url, data={"step": "2", "done": "1", "_token": token(app)})
    assert r.status_code == 200 and "<html" not in r.text and 'name="step" value="2"' in r.text
    assert app.state.store.dossier(STAGE2_TARGET)["pack_steps"] == {"2": True}
    client.post(url, data={"step": "4", "done": "1", "_token": token(app)})
    client.post(url, data={"step": "2", "done": "0", "_token": token(app)})
    assert app.state.store.dossier(STAGE2_TARGET)["pack_steps"] == {"2": False, "4": True}
    for bad in ({"step": "9", "done": "1"}, {"step": "x", "done": "1"}, {"step": "1", "done": "2"}):
        assert client.post(url, data={**bad, "_token": token(app)}).status_code == 400
    assert client.post("/dossiers/kic-000000001/steps",
                       data={"step": "1", "done": "1", "_token": token(app)}).status_code == 404


def test_approved_page_links_the_dossier(seeded_dossier, settings):
    client, app, _ids, _data = seeded_dossier
    assert f'href="/dossiers/{APPROVED_TARGET}"' not in client.get("/approved").text
    seed_dossier(app.state.store, settings.data_dir, target_id=APPROVED_TARGET)
    for mode in ("simple", "expert"):
        html = client.get(f"/approved?mode={mode}").text
        assert f'href="/dossiers/{APPROVED_TARGET}"' in html and ">Dossier<" in html


def test_verdict_popup_has_the_probabilities_block_only_with_a_dossier(seeded_dossier):
    client, _app, ids, _data = seeded_dossier
    html = client.get(f"/partials/signals/{ids['stage2']}/verdict").text
    assert 'id="probabilities"' not in html                       # the dossier page's own block keeps that id
    block = html.split('<section id="probabilities-popup"')[1]
    assert 'aria-labelledby="probabilities-popup-title"' in block and 'id="probabilities-popup-title"' in block
    assert "False alarm" in block and "1 in 1,700" in block and "about 3 percent" in block
    assert "This is about noise." in block and "not a vespa" in block and "provisional" in block
    assert f'href="/dossiers/{STAGE2_TARGET}#checks"' in block
    html = client.get(f"/partials/signals/{ids['candidate']}/verdict").text
    assert "probabilities" not in html and "False alarm" not in html


def test_the_dossier_page_and_its_popup_never_share_an_id(seeded_dossier):
    client, _app, _ids, _data = seeded_dossier
    page = client.get(f"/dossiers/{STAGE2_TARGET}").text
    assert page.count('id="probabilities"') == 1 and 'id="probabilities-page"' in page
    assert 'id="probabilities-popup"' not in page and 'id="probabilities-popup-title"' not in page


def test_the_popup_of_another_signal_of_the_star_names_the_dossier_s_signal(seeded_dossier):
    from tests.unit.ui_seed import add_older_version
    client, app, _ids, _data = seeded_dossier
    other = add_older_version(app.state.store, STAGE2_TARGET)
    html = client.get(f"/partials/signals/{other}/verdict").text
    assert "The dossier was built from another signal of this star." in html
    assert f'href="/dossiers/{STAGE2_TARGET}"' in html and 'id="probabilities-popup"' in html
    assert "False alarm" not in html and "about 3 percent" not in html


def _set_probabilities(app, target_id, fap=None, fpp=None, value=...):
    row = app.state.store.dossier(target_id)
    fields = {"fap_detail": {**row["fap_detail"], **(fap or {})}, "fpp_detail": {**row["fpp_detail"], **(fpp or {})}}
    if value is not ...:
        fields["fpp"] = value
    app.state.store.upsert_dossier(target_id=target_id, **fields)


@pytest.mark.parametrize(("fap", "words"), [
    ({"calibrated": False, "words": "not yet calibrated", "fap": None, "one_in": None}, "not yet calibrated"),
    ({"calibrated": False, "words": None, "fap": None, "one_in": None}, "not yet calibrated"),
    ({"version_matches": False, "words": "calibration is for an older search", "versions": ["0+0ld0ld00"]},
     "calibration is for an older search"),
])
def test_the_false_alarm_wording_without_a_current_calibration(seeded_dossier, fap, words):
    client, app, ids, _data = seeded_dossier
    _set_probabilities(app, STAGE2_TARGET, fap=fap)
    tile = client.get(f"/partials/dossiers/{STAGE2_TARGET}/tile").text
    assert f'<span class="prob-name">False alarm</span> <span class="prob-num">{words}</span>' in tile
    assert "1 in 1,700" not in tile
    popup = client.get(f"/partials/signals/{ids['stage2']}/verdict").text
    assert f'<p class="prob-value">{words}</p>' in popup


def test_the_dossier_page_explains_a_missing_or_older_calibration(seeded_dossier):
    from pathlib import Path
    client, app, _ids, data = seeded_dossier
    path = Path(app.state.store.dossier(STAGE2_TARGET)["data_path"])
    path.write_text(json.dumps({**data, "fap": {**data["fap"], "calibrated": False, "words": None}}), encoding="utf-8")
    html = client.get(f"/dossiers/{STAGE2_TARGET}?mode=expert").text
    assert "not yet calibrated" in html and "Not yet calibrated: at least 200 null searches" in html
    older = {**data["fap"], "version_matches": False, "words": "calibration is for an older search",
             "versions": ["0+0ld0ld00"]}
    path.write_text(json.dumps({**data, "fap": older}), encoding="utf-8")
    html = client.get(f"/dossiers/{STAGE2_TARGET}?mode=expert").text
    assert "calibration is for an older search" in html
    assert "The calibration is for an older search (0+0ld0ld00)" in html


def test_tiles_sort_by_fpp_within_a_group_with_none_last(seeded_dossier, settings):
    client, app, ids, _data = seeded_dossier
    store = app.state.store
    store.decide(ids["stage2"], "approve", "clean")
    store.decide(ids["candidate"], "approve", "clean")
    for tid in (APPROVED_TARGET, STAGE2_TARGET, STAGE1_TARGET):
        seed_dossier(store, settings.data_dir, target_id=tid)
    _set_probabilities(app, APPROVED_TARGET, value=None)
    _set_probabilities(app, STAGE2_TARGET, value=0.2)
    _set_probabilities(app, STAGE1_TARGET, value=0.01)
    html = client.get("/dossiers?versions=all").text
    assert html.index(f'id="tile-{STAGE1_TARGET}"') < html.index(f'id="tile-{STAGE2_TARGET}"') \
        < html.index(f'id="tile-{APPROVED_TARGET}"')
    assert html.count("group-approved") == 3


def test_a_signed_off_fpp_is_not_provisional(seeded_dossier):
    from pathlib import Path
    client, app, ids, data = seeded_dossier
    _set_probabilities(app, STAGE2_TARGET, fpp={"signed_off": True})
    path = Path(app.state.store.dossier(STAGE2_TARGET)["data_path"])
    path.write_text(json.dumps({**data, "fpp": {**data["fpp"], "signed_off": True}}), encoding="utf-8")
    assert "provisional" not in client.get(f"/partials/dossiers/{STAGE2_TARGET}/tile").text
    assert "provisional" not in client.get(f"/partials/signals/{ids['stage2']}/verdict").text
    page = client.get(f"/dossiers/{STAGE2_TARGET}").text
    assert "provisional" not in page.split('id="dossier-data"')[0]
    _set_probabilities(app, STAGE2_TARGET, fpp={"signed_off": False})
    assert "provisional" in client.get(f"/partials/dossiers/{STAGE2_TARGET}/tile").text


def test_dossier_routes_reject_a_bad_target_id(seeded_dossier):
    client, app, _ids, _data = seeded_dossier
    assert client.get("/dossiers/../etc").status_code in (400, 404)
    assert client.get("/dossiers/bad%20id;x").status_code == 400
    assert client.get("/dossiers/%2E%2E").status_code in (400, 404)             # "..", not normalized away
    assert client.post("/dossiers/bad%20id;x/generate", data={"_token": token(app)}).status_code == 400
    assert client.get("/dossiers/bad%20id;x/export.html").status_code == 400


# ---- the planet thumbnail

@pytest.mark.parametrize("star,planet,words", [
    ({"teff_k": 3650}, {"rp_rearth": 2.61, "in_habitable_zone": True, "in_optimistic_habitable_zone": True},
     "A cool red star with a planet about 2.6 times the size of Earth, in the habitable zone"),
    ({"teff": {"median": 5800}}, {"rp_rearth": {"median": 1.0}, "a_au": 0.05, "hz_inner_au": 0.95,
                                  "hz_outer_au": 1.67, "in_habitable_zone": False,
                                  "in_optimistic_habitable_zone": False},
     "A yellow star with a planet about the size of Earth"),
    ({"teff_k": 3653}, {"rp_rearth": 2.61, "in_habitable_zone": False, "in_optimistic_habitable_zone": True},
     "A cool red star with a planet about 2.6 times the size of Earth, in the optimistic habitable zone"),
    ({"teff_k": 4800}, {"rp_rearth": 0.74}, "An orange star with a planet about 0.7 times the size of Earth"),
    ({"teff_k": 6400}, {"rp_rearth": 11.2}, "A white star with a planet about 11.2 times the size of Earth"),
])
def test_look_sentence(star, planet, words):
    assert look_sentence(star, planet) == words


LOOK = {"teff_k": 3653.0, "radius_rsun": 0.509, "rp_rearth": 2.61, "teq_k": 269.0, "a_au": 0.1834,
        "hz_inner_au": 0.2177, "hz_outer_au": 0.413, "hz_optimistic_inner_au": 0.1648, "hz_optimistic_outer_au": 0.4295,
        "in_habitable_zone": False, "in_optimistic_habitable_zone": True}


def test_planet_svg_is_valid_and_labelled():
    svg = planet_svg(LOOK)
    root = ET.fromstring(svg)
    assert root.get("role") == "img" and root.get("aria-label") == look_sentence(LOOK, LOOK)
    assert 'aria-label=' in svg and "#ff9d5c" in svg and "#7fb7a3" in svg      # a cool red star, a temperate planet
    assert root.get("width") == "96"


@pytest.mark.parametrize("rp,teq,colour", [(1.0, 150.0, "#cfe6ff"), (4.0, 500.0, "#c9a96e"), (12.0, 900.0, "#e0784a")])
def test_planet_svg_scales_the_planet_and_colours_it_by_teq(rp, teq, colour):
    svg = planet_svg({**LOOK, "rp_rearth": rp, "teq_k": teq}, size=120)
    root = ET.fromstring(svg)
    planet = root.find(".//{http://www.w3.org/2000/svg}circle[@data-part='planet']")
    assert planet.get("fill") == colour and root.get("width") == "120"
    sizes = [float(ET.fromstring(planet_svg({**LOOK, "rp_rearth": x})).find(
        ".//{http://www.w3.org/2000/svg}circle[@data-part='planet']").get("r")) for x in (0.5, 1, 3, 12, 30)]
    assert sizes[0] == sizes[1] < sizes[2] < sizes[3] == sizes[4]               # log scale held between 1 and 12


def test_planet_svg_without_a_habitable_zone_has_no_bar():
    svg = planet_svg({"teff_k": 5800, "rp_rearth": 1.0})
    ET.fromstring(svg)
    assert "data-part=\"hz\"" not in svg and "#ffd9a0" in svg          # a Sun-like star reads yellow


def test_one_temperature_scale_for_the_star_colour_its_word_and_the_dossier():
    from skysift.ui.planet_svg import STAR_SCALE, star_colour, star_word
    for teff, word in ((3650, "red"), (3950, "orange"), (5772, "yellow"), (6400, "white")):
        colour = next(c for _edge, w, c in STAR_SCALE if w == word)
        assert star_word(teff) == word and star_colour(teff) == colour
        assert look_sentence({"teff_k": teff}, {}).split(" star")[0].endswith(word)
        star = SimpleNamespace(route="mann", teff=SimpleNamespace(median=teff), radius=SimpleNamespace(median=0.5),
                               mass=None, spectral_type=None)
        planet = SimpleNamespace(a_au=None, rp_rearth=None, teq_k=None, insolation_earth=None, hz_inner_au=None,
                                 hz_outer_au=None, hz_optimistic_inner_au=None, hz_optimistic_outer_au=None,
                                 in_habitable_zone=None, in_optimistic_habitable_zone=None)
        assert ds.look_numbers(star, planet)["star_colour"] == word


# ---- the submission pack (Task 6)

def test_a_tile_whose_target_is_gone_stops_polling(seeded_dossier, fake_jobs):
    client, _app, _ids, _data = seeded_dossier
    r = client.get("/partials/dossiers/kic-000000001/status")
    assert r.status_code == 286 and "no longer available" in r.text and "every 5s" not in r.text
    assert "<html" not in r.text and "<article" not in r.text


def test_the_dashboard_steps_come_from_the_pack_module():
    from skysift.pack import STEP_TEXTS, STEPS
    from skysift.ui.routes.dossiers import PACK_STEPS
    assert [(str(n), title, text) for n, title, text in PACK_STEPS] == [(n, t, STEP_TEXTS[n]) for n, t in STEPS]


def _fake_pack(app, settings, target_id=STAGE2_TARGET):
    from datetime import datetime
    out = settings.data_dir / "packs" / target_id / "20261021"
    out.mkdir(parents=True)
    (out / "README.md").write_text("# pack", encoding="utf-8")
    (out / "provenance").mkdir()
    zip_name = f"{target_id}_20261021_skysift_pack.zip"
    (out / "provenance" / "versions.json").write_text(json.dumps({"zip_name": zip_name}), encoding="utf-8")
    zip_path = out / zip_name
    zip_path.write_bytes(b"PK fake zip")
    app.state.store.set_pack(target_id, str(out), datetime(2026, 10, 21, 10, 0))
    return out, zip_path


def test_the_pack_zip_is_linked_and_served_once_built(seeded_dossier, settings):
    client, app, _ids, _data = seeded_dossier
    assert client.get(f"/dossiers/{STAGE2_TARGET}/pack.zip").status_code == 404           # not built yet
    assert f'href="/dossiers/{STAGE2_TARGET}/pack.zip"' not in client.get(f"/dossiers/{STAGE2_TARGET}").text
    _out, zip_path = _fake_pack(app, settings)
    r = client.get(f"/dossiers/{STAGE2_TARGET}/pack.zip")
    assert r.status_code == 200 and r.content == zip_path.read_bytes()
    assert r.headers["content-type"] == "application/zip"
    assert r.headers["content-disposition"] == f'attachment; filename="{zip_path.name}"'
    assert zip_path.name == f"{STAGE2_TARGET}_20261021_skysift_pack.zip"
    page = client.get(f"/dossiers/{STAGE2_TARGET}").text
    assert f'href="/dossiers/{STAGE2_TARGET}/pack.zip"' in page and "Download the submission pack" in page
    tile = client.get(f"/partials/dossiers/{STAGE2_TARGET}/tile").text
    assert f'href="/dossiers/{STAGE2_TARGET}/pack.zip"' in tile
    versions = _out / "provenance" / "versions.json"
    versions.write_text(json.dumps({"zip_name": "../../secret.zip"}), encoding="utf-8")
    assert client.get(f"/dossiers/{STAGE2_TARGET}/pack.zip").status_code == 404           # a name, never a path
    versions.write_text(json.dumps({"zip_name": zip_path.name}), encoding="utf-8")
    zip_path.unlink()
    assert client.get(f"/dossiers/{STAGE2_TARGET}/pack.zip").status_code == 404           # a zip moved away
    assert client.get("/dossiers/bad%20id;x/pack.zip").status_code == 400
    assert client.get("/dossiers/kic-000000001/pack.zip").status_code == 404


# ---- the final review (21 October 2026)

def test_the_fap_split_by_null_kind_sits_under_the_pooled_number(seeded_dossier):
    client, _app, ids, _data = seeded_dossier
    page = client.get(f"/dossiers/{STAGE2_TARGET}").text
    assert '<p class="prob-value">1 in 1,700</p>' in page                     # the pooled number stays the headline
    assert '<p class="prob-split">inverted nulls 0 of 850, scrambled 0 of 849</p>' in page
    assert "inverted nulls 0 of 850" in client.get(f"/partials/signals/{ids['stage2']}/verdict").text
    export = client.get(f"/dossiers/{STAGE2_TARGET}/export.html").text
    assert '"k_by_kind":{"inverted":0,"scrambled":0}' in export and '" nulls"' in export     # dossier.js draws it


def _no_neighbours(app, data_path, data):
    from pathlib import Path

    from skysift.fpp import FppInputs, compute
    inputs = {**data["fpp"]["inputs"], "neighbours": [], "neighbours_known": False}
    fpp = {**compute(FppInputs(**inputs)).as_dict(), "inputs": inputs,
           "sentence_not_covered": data["fpp"]["sentence_not_covered"]}
    Path(data_path).write_text(json.dumps({**data, "fpp": fpp}), encoding="utf-8")
    app.state.store.upsert_dossier(target_id=STAGE2_TARGET, fpp=None, fpp_detail=fpp)
    return fpp


def test_without_neighbour_data_the_tile_and_page_give_no_fpp_number(seeded_dossier):
    client, app, _ids, data = seeded_dossier
    fpp = _no_neighbours(app, app.state.store.dossier(STAGE2_TARGET)["data_path"], data)
    assert fpp["value"] is None and fpp["words"] == "not computed (no neighbour data)"
    tile = client.get(f"/partials/dossiers/{STAGE2_TARGET}/tile").text
    probs = tile.split('class="tile-probs"')[1].split("</div>")[0]
    assert '<span class="prob-num">not computed (no neighbour data)</span>' in probs and "percent" not in probs
    page = client.get(f"/dossiers/{STAGE2_TARGET}?mode=expert").text.split('id="dossier-data"')[0]
    assert "not computed (no neighbour data)" in page and "Not computed (no neighbour data): the Gaia" in page
    assert "under 1 percent" not in page


def test_the_export_is_refused_while_its_dossier_is_generated(seeded_dossier, fake_jobs):
    client, app, _ids, _data = seeded_dossier
    r = client.post(f"/dossiers/{STAGE2_TARGET}/generate", data={"_token": token(app), "force": "1"})
    assert r.status_code == 200 and fake_jobs.active()["request"]["target_id"] == STAGE2_TARGET
    r = client.get(f"/dossiers/{STAGE2_TARGET}/export.html")
    assert r.status_code == 409 and "being generated" in r.headers["HX-Trigger"]
    assert app.state.store.dossier(STAGE2_TARGET)["export_path"] == ""                       # nothing written
    fake_jobs.stop()
    assert client.get(f"/dossiers/{STAGE2_TARGET}/export.html").status_code == 200


def test_the_export_names_no_local_folder(seeded_dossier, settings):
    from pathlib import Path
    client, app, _ids, data = seeded_dossier
    root = Path(settings.data_dir).resolve()
    where = root / "blobs" / "7e" / "pixels.npz"
    data = {**data, "section_errors": {"pixels": {"error": f"[Errno 2] No such file or directory: '{where}'"}},
            "provenance": {**data["provenance"], "calibration_file": str(root / "calibration" / "kbonus_bkg.jsonl")}}
    Path(app.state.store.dossier(STAGE2_TARGET)["data_path"]).write_text(json.dumps(data), encoding="utf-8")
    text = client.get(f"/dossiers/{STAGE2_TARGET}/export.html").text
    payload = json.loads(text.split('type="application/json">')[1].split("</script>")[0])
    assert payload["provenance"]["calibration_file"] == "calibration/kbonus_bkg.jsonl"
    assert payload["section_errors"]["pixels"]["error"] == "[Errno 2] No such file or directory: 'blobs/7e/pixels.npz'"
    assert root.parent.name not in text


def test_a_pack_from_an_earlier_dossier_is_not_offered(seeded_dossier, settings):
    from datetime import datetime
    client, app, _ids, _data = seeded_dossier
    _fake_pack(app, settings)
    store = app.state.store
    store.set_pack(STAGE2_TARGET, store.dossier(STAGE2_TARGET)["pack_path"], datetime(2026, 10, 20, 8, 0))
    store.upsert_dossier(target_id=STAGE2_TARGET, generated=datetime(2026, 10, 21, 9, 0))
    tile = client.get(f"/partials/dossiers/{STAGE2_TARGET}/tile").text
    assert "the pack was built from an earlier dossier" in tile and "pack built (zip)" not in tile
    assert "The pack was built from an earlier dossier" in client.get(f"/dossiers/{STAGE2_TARGET}").text


@pytest.mark.parametrize("mode", ["simple", "expert"])
def test_the_dossier_page_renders_in_the_dark_theme(seeded_dossier, mode):
    client, _app, _ids, _data = seeded_dossier
    r = client.get(f"/dossiers/{STAGE2_TARGET}?mode={mode}&theme=dark")
    assert r.status_code == 200 and 'data-theme="dark"' in r.text and f'data-mode="{mode}"' in r.text
    assert 'id="checks"' in r.text and 'id="look"' in r.text and "—" not in r.text
    assert "dossier.js" in r.text and 'id="dossier-data"' in r.text


def test_pixel_undecided_signals_join_the_page_but_other_undetermined_ones_do_not(seeded_dossier):
    # 21 October 2026: the page shows stage 1 and the signals only the pixel stage could not decide; a signal
    # undetermined at rung 4 (the seed's outage target) stays off it
    from skysift.models import TestResult, TestStatus
    from skysift.ui import queries
    from tests.unit.ui_seed import VERSION, _signal, _target, _tests, make_job_key
    client, app, _ids, _data = seeded_dossier
    store = app.state.store
    tid = "kic-009243078"
    store.upsert_targets([_target(tid, kic=9243078, gaia="2101234567890123470")])
    rid = store.start_run(make_job_key(tid, "box_dip", VERSION, "7"), tid, "box_dip", VERSION, "7")
    tests = _tests("pass") + [TestResult(rung="rung5_pixels", status=TestStatus.UNDETERMINED,
                                         reason="pixels: season 2 ambiguous (1.0 sigma)", metrics={"seasons": []})]
    item = {"signal": _signal(tid, 6.113, 20.5).model_dump(), "outcome": "undetermined",
            "tests": [r.model_dump() for r in tests], "timings": [0.01] * len(tests)}
    store.record_results(rid, tid, [item], [], pin_evidence=False)
    html = client.get("/dossiers").text
    assert tid in html and "group-pixels" in html
    assert "gaia-dr3-2101234567890123457" not in html                 # undetermined at rung 4: not on the page
    rows = queries.final_set(store, None)
    groups = [r["group"] for r in rows]
    assert groups == sorted(groups, key=queries.FINAL_GROUPS.index) and groups[-1] == "pixels"
    assert STAGE1_TARGET in [r["target_id"] for r in rows if r["group"] == "stage1"]
    # a rejected sibling at a higher SDE does not take the tile or the dossier from the undetermined signal
    rid2 = store.start_run(make_job_key(tid, "box_dip", VERSION, "8"), tid, "box_dip", VERSION, "8")
    failed = {"signal": _signal(tid, 3.0565, 50.0).model_dump(), "outcome": "fail",
              "tests": [r.model_dump() for r in _tests("fail")], "timings": [0.01, 0.01]}
    store.record_results(rid2, tid, [failed], [], pin_evidence=False)
    tile = next(r for r in queries.final_set(store, None) if r["target_id"] == tid)
    assert tile["group"] == "pixels" and tile["signal"]["sde"] == 20.5
    assert ds.pick_signal(store, tid)["id"] == tile["signal_id"]
    assert queries.pixels_undecided([{"rung": "rung2_shape", "status": "undetermined", "reason": "equal secondary"},
                                     {"rung": "rung5_pixels", "status": "undetermined", "reason": "pixels: x"}]) is False
    assert queries.pixels_undecided([{"rung": "rung5_pixels", "status": "undetermined", "reason": "outage:mast: 1"}]) is False


# ---- the errors (dossier version 3, 22 October 2026)

def test_dossier_page_prints_the_ephemeris_errors_in_expert(seeded_dossier):
    client, _app, _ids, data = seeded_dossier
    client.cookies.set("skysift_mode", "expert")
    html = client.get(f"/dossiers/{STAGE2_TARGET}").text
    hero = html.split('class="tiles dossier-tiles"')[1].split("</header>")[0]
    assert '40.289904<span class="only-expert pm"> ± 0.000091</span>' in hero          # value at the error's decimals
    assert '<span class="only-expert pm"> +0.07 −0.06</span>' in hero                   # the duration, asymmetric
    signal = html.split('id="signal"')[1].split('id="repeat"')[0]
    assert "T_ref (BJD_TDB)" in signal and "T_ref (BKJD)" in signal and "± 0.00041" in signal
    assert "linear fit of 32 transit times" in signal and "32 of 33 timed transits used" in signal
    assert "rms 7.9 min" in signal and data["timing"]["method"] in signal and "fitted T14" in signal
    repeat = html.split('id="repeat"')[1].split('id="checks"')[0]
    assert "Odd and even" in repeat and "2,196 ± 70 ppm" in repeat and "Secondary eclipse" in repeat
    assert "3-sigma limit" in repeat and "210 ppm" in repeat and "phase 0.62" in repeat
    assert "the vetting number is the stacked fold's" not in repeat.lower()          # 0.4 and 0.3 sigma agree
    checks = html.split('id="checks"')[1].split('id="pixels"')[0]
    assert "Shape with errors" in checks and "20 of 20 resamples converged" in checks
    assert "ingress shorter than the cadence" in checks and "ingress, minutes" in checks
    assert "—" not in html


def test_the_page_prints_both_parity_numbers_when_they_differ(seeded_dossier, settings):
    from pathlib import Path
    client, app, _ids, data = seeded_dossier
    path = Path(app.state.store.dossier(STAGE2_TARGET)["data_path"])
    path.write_text(json.dumps({**data, "parity": {**data["parity"], "difference_sigma": 1.4}}), encoding="utf-8")
    repeat = client.get(f"/dossiers/{STAGE2_TARGET}").text.split('id="repeat"')[1].split('id="checks"')[0]
    assert "1.4 sigma" in repeat and "0.3 sigma" in repeat


def test_dossier_export_carries_the_same_errors(seeded_dossier):
    """The standalone page draws from the JSON it embeds, so the test reads that JSON (the inlined script holds the
    words whether or not it draws them): the error blocks are the stored dossier's own."""
    import re
    client, _app, _ids, data = seeded_dossier
    export = client.get(f"/dossiers/{STAGE2_TARGET}/export.html").text
    blob = re.search(r'<script id="dossier-data" type="application/json">(.*?)</script>', export, re.DOTALL).group(1)
    embedded = json.loads(blob)
    for key in ("timing", "shape", "parity", "secondary", "centroid_offset"):
        assert embedded.get(key) == data.get(key), key
    assert embedded["ephemeris"]["period_err_days"] == data["ephemeris"]["period_err_days"] > 0
    assert embedded["timing"]["ok"] is True and embedded["shape"]["k"]["minus"] > 0
    assert embedded["parity"]["odd"]["err_ppm"] > 0 and embedded["secondary"]["limit_3sigma_ppm"] > 0


def test_dossier_page_shows_the_cross_check_block(seeded_dossier):
    from skysift import crosscheck as cc
    from tests.unit.test_crosscheck import fake_result
    client, app, _ids, data = seeded_dossier
    html = client.get(f"/dossiers/{STAGE2_TARGET}").text
    checks = html.split('id="checks"')[1].split('id="pixels"')[0]
    assert "Cross-check" in checks and "No TRICERATOPS cross-check is stored yet" in checks
    result = fake_result(cc.inputs_fingerprint(data))
    # twenty runs with FPP 0.010 to 0.029: median 0.0195, 16th to 84th percentile 0.013 to 0.026 (ruling s)
    runs = [dict(r, fpp=0.01 + 0.001 * k, fpp_no_stp=cc.fpp_without_stp(0.01 + 0.001 * k, 0.005), p_stp=0.005)
            for k, r in enumerate(result["runs"])]
    result = {**result, "runs": runs, **cc.summarise(runs)}
    app.state.store.set_cross_check(STAGE2_TARGET, {**result, "current": False, "class": "validated"})   # recomputed
    for mode in ("simple", "expert"):
        client.cookies.set("skysift_mode", mode)
        checks = client.get(f"/dossiers/{STAGE2_TARGET}").text.split('id="checks"')[1].split('id="pixels"')[0]
        assert "Cross-check" in checks and "TRICERATOPS 1.1.0" in checks and "over 20 runs:" in checks
        assert "median false-positive probability" in checks and "0.0195" in checks
        assert "16th to 84th percentile" in checks and "0.013 to 0.026" in checks
        assert "without a planet transiting an unresolved bound companion (STP)" in checks and "0.0146" in checks
        assert "nearby false-positive probability" in checks and "0.0001 to 0.0001" in checks
        # I-1 (spec 11.2): the class name is a label of where the medians fall, never a reading of the signal
        plain = html_module.unescape(checks)
        assert "read as" not in plain and cc.CLASS_LABEL in plain         # final review minor 7
        assert "never blocks the live draft" in plain
        # the false-positive items lead with the TRICERATOPS headline; the in-house lower bound follows
        lead = f"{cc.headline({**data, 'cross_check': cc.with_current(result, data)})}."
        assert lead.startswith("TRICERATOPS 1.1.0 gives a false-positive probability of ") and lead in plain
        assert plain.index(lead) < plain.index(", in-house") < plain.index("a lower bound")
        assert "without a bound-companion or diluted-planet scenario" in plain
        assert "made for this dossier" in checks and "earlier dossier" not in checks
        assert "Kepler table" in checks and chr(0x2014) not in checks
        # spec 12.2: the pixel-excluded set, its spread in the page's g3 form and the neighbour it removes
        bare = re.sub(r"<[^>]+>", "", plain)
        assert "with the one neighbour the difference images exclude removed" in bare
        assert ("With the one neighbour the difference images exclude removed, over 20 runs: median false-positive "
                "probability 0.004 (16th to 84th percentile 0.004 to 0.004)") in bare
        assert "Gaia DR3 2073896190925687296 at 11.45 arcsec, smallest margin 12.7 sigma" in bare
        assert cc.dominant_excluded_sentence({**data, "cross_check": cc.with_current(result, data)}) in bare
        # Task 15 addendum 2: which number rests on our own evidence, beside the variant line
        assert cc.own_evidence_sentence({**data, "cross_check": cc.with_current(result, data)}) in bare
    app.state.store.set_cross_check(STAGE2_TARGET, {**result, "inputs_sha256": "0" * 64, "current": True})
    checks = client.get(f"/dossiers/{STAGE2_TARGET}").text.split('id="checks"')[1].split('id="pixels"')[0]
    assert "made for an earlier dossier" in checks
    old = {"tool": cc.TOOL, "tool_version": "1.1.0", "fpp": 0.01, "nfpp": 1e-4, "fpp_runs": [0.01] * 10,
           "inputs_sha256": cc.inputs_fingerprint(data)}
    app.state.store.set_cross_check(STAGE2_TARGET, old)                # the format before 22 October 2026
    checks = client.get(f"/dossiers/{STAGE2_TARGET}").text.split('id="checks"')[1].split('id="pixels"')[0]
    assert "stored by an earlier version of the tool" in checks and "median false-positive" not in checks


def test_the_standalone_page_carries_a_cross_check_imported_after_its_export(seeded_dossier, tmp_path):
    from skysift import crosscheck as cc
    from tests.unit.test_crosscheck import fake_result
    client, app, _ids, data = seeded_dossier
    before = client.get(f"/dossiers/{STAGE2_TARGET}/export.html").text
    assert '"cross_check":null' in before.replace(" ", "")
    assert "Cross-check" in before and "No TRICERATOPS cross-check is stored yet." in before     # dossier.js draws it
    result = tmp_path / "result.json"
    result.write_text(json.dumps(fake_result(cc.inputs_fingerprint(data), fpp=0.0234, nfpp=0.0012)), encoding="utf-8")
    cc.import_result(app.state.store, STAGE2_TARGET, result)
    assert app.state.store.dossier(STAGE2_TARGET)["export_path"] == ""          # the kept page is drawn again
    after = client.get(f"/dossiers/{STAGE2_TARGET}/export.html").text
    blob = after.split('<script id="dossier-data" type="application/json">')[1].split("</script>")[0]
    block = json.loads(blob)["cross_check"]
    assert block["fpp"] == 0.0234 and block["nfpp"] == 0.0012 and block["current"] is True
    assert "over ${CC.n_runs} runs" in after and "16th to 84th percentile" in after and "PT.class_text" in after
    texts = json.loads(blob)["page_texts"]          # the headline follows the imported block (spec 11.2)
    assert texts["fpp_lead"].startswith("TRICERATOPS 1.1.0 gives a false-positive probability of 0.023 (")
    # spec 12.2: the standalone page carries the variant (dossier.js draws its spread from the block)
    assert "with the one neighbour the difference images exclude removed" in texts["fpp_lead"]
    assert block["variant_excluded"]["fpp"] == 0.004 and texts["excluded"].startswith("With those neighbours")
    assert "removed, over ${VX.n_runs} runs: median false-positive probability ${vspread(\"fpp\")}" in after
    assert texts["own_evidence"].startswith("The FPP with the pixel-excluded neighbours removed (0.0040)")
    assert "PT.own_evidence" in after and "PT.leakage_basis" in after and "PT.crowdsap" in after


def test_the_pixel_section_quotes_the_offset_against_the_rule(seeded_dossier):
    from pathlib import Path
    client, app, _ids, data = seeded_dossier
    block = {"self_referenced_arcsec": 0.39, "self_referenced_err_arcsec": 0.15, "sigma": 2.6, "counts": False,
             "reference": "the catalogue position plus the two-source fit's common shift",
             "catalogue_referenced_arcsec": 0.25, "catalogue_referenced_err_arcsec": 0.28, "level_arcsec": 0.2,
             "sigma_level": 3.0, "rule": "Bryson et al. 2013", "n_seasons": 4}
    path = Path(app.state.store.dossier(STAGE2_TARGET)["data_path"])
    path.write_text(json.dumps({**data, "centroid_offset": block}), encoding="utf-8")
    import html
    pixels = html.unescape(client.get(f"/dossiers/{STAGE2_TARGET}").text.split('id="pixels"')[1].split('id="star"')[0])
    # the fit's fallback reference is named and not judged by Bryson's rule (round 1, item 3)
    assert ("Against the catalogue position plus the two-source fit's common shift (the star's direct-image "
            "centroid is not stored for every season)") in pixels
    assert "offset by 0.39 ± 0.15 arcsec (2.6 sigma), not assessed from the star's own image" in pixels
    # spec 11.5: the Gaia offset leads, with the exclusion radius (max(3 sigma, 0.5 arcsec), 0.84 arcsec here)
    assert pixels.index("Against the Gaia position projected through each stamp's WCS, a reference that carries the "
                        "WCS zero point and the registration, the difference-image centroid sits 0.25 ± 0.28 arcsec "
                        "away, with an exclusion radius of 0.84 arcsec") < pixels.index("Against the catalogue position")
    own = {**block, "reference": "the star's own direct-image centroid"}
    path.write_text(json.dumps({**data, "centroid_offset": own}), encoding="utf-8")
    pixels = client.get(f"/dossiers/{STAGE2_TARGET}").text.split('id="pixels"')[1].split('id="star"')[0]
    assert ("offset by 0.39 ± 0.15 arcsec (2.6 sigma), not an offset by the rule of Bryson et al. (2013), which asks "
            "for more than 3 sigma and more than 0.2 arcsec") in pixels
    export = client.get(f"/dossiers/{STAGE2_TARGET}/export.html").text
    assert '"centroid_offset":{' in export.replace(" ", "") and "Against the star's own out-of-transit" in export
    assert "not assessed from the star's own image" in export          # the script's twin of the fallback


def test_section_01_presents_a_prior_claim_with_its_relationship(seeded_dossier):
    """P1 and spec 11.1: with a prior claim stored, the page's section 01 cites it (author, DOI, date) and states the
    relationship and the development note, never "blind" or "independent recovery"; the standalone page carries the
    claim in its JSON and the same words in its script."""
    import re
    from pathlib import Path

    from tests.unit.test_dossier import PRIOR_CLAIM
    client, app, _ids, data = seeded_dossier
    signal = client.get(f"/dossiers/{STAGE2_TARGET}").text.split('id="signal"')[1].split('id="repeat"')[0]
    assert "re-detection" not in signal and PRIOR_CLAIM["doi"] not in signal
    path = Path(app.state.store.dossier(STAGE2_TARGET)["data_path"])
    path.write_text(json.dumps({**data, "prior_claims": [PRIOR_CLAIM]}), encoding="utf-8")
    signal = client.get(f"/dossiers/{STAGE2_TARGET}").text.split('id="signal"')[1].split('id="repeat"')[0]
    assert "Kuber Mehta reported this signal first" in signal and "doi:10.5281/zenodo.23325261" in signal
    assert "13 October 2026" in signal
    assert "result is a re-detection: the sweep" in signal and "the search itself used no prior ephemeris" in signal
    # M1: the comparison with the claim's numbers follows the claim on the page, as in the README
    from skysift import pack
    claimed = {**data, "prior_claims": [PRIOR_CLAIM]}
    comparison = pack.comparison_text(claimed)
    assert comparison and html_module.unescape(signal).count(f"{pack.prior_claim_sentence(claimed)} {comparison}") == 1
    export = client.get(f"/dossiers/{STAGE2_TARGET}/export.html").text
    assert '"prior_claims":[{' in export.replace(" ", "")
    # M2: the standalone script prints the pack's sentences from the JSON (dossier.page_texts), with no word map
    blob = re.search(r'<script id="dossier-data" type="application/json">(.*?)</script>', export, re.DOTALL).group(1)
    texts = json.loads(blob)["page_texts"]
    assert texts["prior"] == pack.prior_claim_sentence(claimed) and texts["comparison"] == comparison
    assert "a recovery made before SkySift knew of" not in export
    for page in (signal, export):
        # the relationship key "blind-recovery" is data the script maps to words, never prose
        assert not re.search(r"\bblind\b(?!-recovery)", page, re.IGNORECASE) and "independent recovery" not in page


def test_the_validated_class_is_not_named_and_withdrawn_in_one_breath(seeded_dossier):
    """Addendum to Task 8, and Task 11 fix round 1 (I-1, spec 11.2): medians inside Giacalone et al.'s validation
    lines are in none of its validation classes, the class name a label of where they fall; the page and the
    standalone page lead their false-positive items with the TRICERATOPS headline, the in-house estimate after it
    as a lower bound, the hero's TRICERATOPS pill before the in-house one."""
    import re

    from skysift import crosscheck as cc
    from tests.unit.test_crosscheck import fake_result
    client, app, _ids, data = seeded_dossier
    app.state.store.set_cross_check(STAGE2_TARGET, fake_result(cc.inputs_fingerprint(data), fpp=0.012, nfpp=1e-4))
    page = html_module.unescape(client.get(f"/dossiers/{STAGE2_TARGET}").text)
    checks = page.split('id="checks"')[1].split('id="pixels"')[0]
    label = cc.CLASS_LABEL          # round-2 final review, minor 7: only that no class applies
    assert label in checks
    lead = ("TRICERATOPS 1.1.0 gives a false-positive probability of 0.012 (0.012 without a planet on an unresolved "
            "bound companion), and 0.0040 (0.0010 without it) with the one neighbour the difference images exclude "
            f"removed, {cc.NO_CLASS_CLAUSE} (Giacalone et al. 2021).")
    assert lead in checks and checks.index(lead) < checks.index(", in-house") < checks.index("a lower bound")
    for bad in ("validation lines", "read as", "its section 4.2", 'class="crosscheck-class"', "likely planet"):
        assert bad not in checks, bad
    hero = page.split('class="hero-verdict"')[1].split("</div>")[0]
    assert hero.index("False positive, TRICERATOPS</span> 0.012") < hero.index(", in-house</span>")
    # the standalone page: the script draws the same sentences from the JSON's page_texts, and words none itself
    export = client.get(f"/dossiers/{STAGE2_TARGET}/export.html").text
    blob = re.search(r'<script id="dossier-data" type="application/json">(.*?)</script>', export, re.DOTALL).group(1)
    texts = json.loads(blob)["page_texts"]
    assert texts["fpp_lead"] == lead and texts["class_text"] == label and "a lower bound" in texts["in_house"]
    script = export.split('</script>\n<script>')[1]
    assert '["False-positive probability:", PT.fpp_lead]' in script and "PT.class_text" in script
    assert script.index("PT.fpp_lead") < script.index("PT.fpp_lead ? \", in-house\" : \"\"")   # only beside a lead
    for bad in ("validation lines", "read as", "likely planet", "${CC.class}"):
        assert bad not in script, bad


def test_section_03_carries_the_tess_statement(seeded_dossier):
    """Task 8: the TESS block under the cross-check, its sentence in both modes, the per-sector table and the
    dilution numbers for the expert, the block's own caveats; a dossier without the inputs says it was not assessed,
    and the standalone script draws the same block from its JSON."""
    import html
    from pathlib import Path
    client, app, _ids, data = seeded_dossier
    block = data["tess"]
    assert block["available"] is True
    for mode in ("simple", "expert"):
        client.cookies.set("skysift_mode", mode)
        checks = html.unescape(client.get(f"/dossiers/{STAGE2_TARGET}").text.split('id="checks"')[1]
                               .split('id="pixels"')[0])
        assert "<h3>TESS</h3>" in checks and block["sentence"] in checks
        assert block["methods"] and block["methods"] in checks             # round 2, N1: the method beside it
        assert "README" not in block["sentence"]
        assert "TESS sectors on the Kepler ephemeris" in checks
        assert "as the two-pixel-radius sum, for comparison" in checks
        assert "no TESS light curve was examined" in checks and chr(0x2014) not in checks
        assert checks.index("Cross-check") < checks.index("<h3>TESS</h3>")
    path = Path(app.state.store.dossier(STAGE2_TARGET)["data_path"])
    path.write_text(json.dumps({**data, "tess": {"available": False, "caveats": ["TESS inputs not fetched"]}}),
                    encoding="utf-8")
    checks = client.get(f"/dossiers/{STAGE2_TARGET}").text.split('id="checks"')[1].split('id="pixels"')[0]
    assert "TESS coverage was not assessed (TESS inputs not fetched)." in checks
    assert f"tools/tess_inputs.py --target {STAGE2_TARGET}" in checks
    assert f"then run <span class=\"mono\">skysift dossier --target {STAGE2_TARGET} --force</span>" in checks
    old = {k: v for k, v in data.items() if k != "tess"}                      # a version 3 dossier has no block
    path.write_text(json.dumps(old), encoding="utf-8")
    checks = client.get(f"/dossiers/{STAGE2_TARGET}").text.split('id="checks"')[1].split('id="pixels"')[0]
    assert "TESS coverage was not assessed." in checks
    path.write_text(json.dumps(data), encoding="utf-8")
    export = client.get(f"/dossiers/{STAGE2_TARGET}/export.html").text
    blob = export.split('<script id="dossier-data" type="application/json">')[1].split("</script>")[0]
    assert json.loads(blob)["tess"] == block
    assert 'if (TS.methods) para(2, TS.methods, "dossier-small");' in export
    assert 'h("h3", null, "TESS")' in export and "TESS coverage was not assessed${why}." in export


def test_a_star_not_assessed_shows_no_prediction_caveat_and_no_significance(seeded_dossier):
    """Task 8 fix round 4, M-4: a star TESS saw only in sectors outside the table has no counted window, so the page
    shows the sentence and the skipped-sector caveat, but neither the prediction caveat nor the sector table and the
    S/N line; the standalone script skips the same two."""
    import html
    from pathlib import Path

    from skysift import tess
    from tests.unit.ui_seed import SEED_TESS
    client, app, _ids, data = seeded_dossier
    only = tess.statement({**SEED_TESS, "sectors": [109]}, data["ephemeris"])
    assert only["verdict"] == "not assessed"
    path = Path(app.state.store.dossier(STAGE2_TARGET)["data_path"])
    path.write_text(json.dumps({**data, "tess": only}), encoding="utf-8")
    client.cookies.set("skysift_mode", "expert")
    checks = html.unescape(client.get(f"/dossiers/{STAGE2_TARGET}").text.split('id="checks"')[1]
                           .split('id="pixels"')[0])
    assert only["sentence"] in checks and "TESScut lists Sector 109" in checks
    assert "This is a prediction of detectability" not in checks and "per-transit S/N" not in checks
    assert "TESS sectors on the Kepler ephemeris" not in checks
    export = client.get(f"/dossiers/{STAGE2_TARGET}/export.html").text
    assert 'const assessed = TS.verdict !== "not assessed";' in export
    assert "if (assessed) list(2," in export and "if (assessed) para(2, `TIC ${TS.tic}" in export


def test_the_shape_table_says_its_value_is_the_resample_median_and_names_the_a_over_r_fit(seeded_dossier):
    """Task 10 fix round 1, I-2 and M-8: since version 5 the value column is the resample median with the full-data
    fit beside it, and the shape block's a/R* is labelled with the fit it comes from, on the page and in the
    standalone export the pack ships."""
    from pathlib import Path
    client, app, _ids, data = seeded_dossier
    client.cookies.set("skysift_mode", "expert")
    path = Path(app.state.store.dossier(STAGE2_TARGET)["data_path"])
    cmp = {"constrained": False, "quoted": "prior", "delta_chi2": 0.2, "density_tension_sigma": None,
           "a_over_r_label": "from the stellar parameters", "reason": "unconstrained"}
    path.write_text(json.dumps({**data, "shape": {**data["shape"], "comparison": cmp}}), encoding="utf-8")
    words = ("the value is the median of the resamples, minus and plus the 16th to 50th and 50th to 84th percentile "
             "spread about it, and the full-data fit stands beside it")
    html = client.get(f"/dossiers/{STAGE2_TARGET}").text
    checks = html.split('id="checks"')[1].split('id="pixels"')[0]
    assert words in checks and "a / R★ (density-prior fit)" in checks
    export = client.get(f"/dossiers/{STAGE2_TARGET}/export.html").text
    assert words in export and "(density-prior fit)" in export
    assert "the fit to the whole light curve" not in html + export


def test_the_checks_section_gives_the_notes_aperture_ratio(seeded_dossier):
    """Task 12 review I-1 (spec 11.5): rung 3's line on the page prints the ratio with its error against CROWDSAP and
    the shortfall in sigma, the numbers of the note's aperture sentence, not rung 3's distance from equal depths."""
    import dataclasses
    from pathlib import Path

    from skysift import pack
    from skysift.ui import explain
    client, app, _ids, data = seeded_dossier
    block = {"psf_depth_ppm": 2213.9651, "psf_err_ppm": 114.37157, "sap_depth_ppm": 1540.8282,
             "sap_err_ppm": 129.2231, "ratio": 0.69595868, "ratio_err": 0.068551662, "crowdsap": 0.85650748,
             "shortfall_sigma": -2.3420118, "ephemeris": "linear fit of 31 transit times"}
    tests = app.state.store.latest_tests(data["signal"]["id"])
    why = [dataclasses.asdict(w) for w in explain.why(tests, aperture=block)]      # as generate stores it
    stored = {**data, "aperture": block, "why": why}
    Path(app.state.store.dossier(STAGE2_TARGET)["data_path"]).write_text(json.dumps(stored), encoding="utf-8")
    checks = html_module.unescape(client.get(f"/dossiers/{STAGE2_TARGET}").text.split('id="checks"')[1]
                                  .split('id="pixels"')[0])
    note = pack.aperture_sentence(stored)
    clause = note[0].lower() + note[1:-1]
    assert clause == ("the SAP depth is 0.696 ± 0.069 of the PSF depth against a CROWDSAP (this star's light fraction "
                      "in the aperture) of 0.86, 2.3 sigma short of it")
    assert clause in checks and "sigma apart" not in checks


def test_the_probabilities_intro_has_a_lead_in_without_a_cross_check(seeded_dossier):
    """Task 13 review M-11: without a TRICERATOPS lead the intro still says what "they" are."""
    client, _app, _ids, _data = seeded_dossier
    page = html_module.unescape(client.get(f"/dossiers/{STAGE2_TARGET}").text)
    intro = page.split('class="prob-intro">')[1].split("</p>")[0]
    assert intro == "Two probabilities, the false alarm and the false positive. They are shown apart and never combined."


_NUMBER = r"\d[\d,]*(?:\.\d+)?"


def _numbers(text: str) -> set[str]:
    """Every number in ``text``, thousands commas kept, a trailing comma or full stop dropped."""
    import re
    return {m.rstrip(",.") for m in re.findall(_NUMBER, text)}


def _note_prose(tex: str) -> str:
    """The Research Note's sentences from the abstract to the figure caption: the citation keys, the LaTeX commands
    and the data-availability, software and bibliography blocks left out, so what is left is what a reader reads."""
    import re
    body = tex.split(r"\begin{abstract}")[1].split(r"\section{Data availability}")[0]
    body = re.sub(r"\\keywords\{.*", " ", body)              # the UAT concept ids are not the note's numbers
    body = re.sub(r"\\cite[pt]?(\[[^\]]*\]){0,2}\{[^}]*\}", " ", body)
    body = re.sub(r"\\includegraphics(\[[^\]]*\])?\{[^}]*\}", " ", body)
    body = body.replace("{,}", ",").replace(r"\pm", " ± ")
    return re.sub(r"\\[a-zA-Z]+\*?", " ", body)


def test_every_number_the_note_prints_is_on_the_dossier_page(seeded_dossier):
    """Round-2 final review, I-2: README step 3 says the note says nothing the dossier page does not. On the golden
    as a live draft has it (a current cross-check, a prior claim), every number in the note's prose is in the served
    page's text."""
    import re
    from pathlib import Path

    from skysift import crosscheck as cc
    from skysift import pack
    from tests.unit.test_crosscheck import fake_result
    from tests.unit.test_dossier import PRIOR_CLAIM
    client, app, _ids, _data = seeded_dossier
    golden = json.loads((Path(__file__).parents[1] / "golden" / "kic-006146676-generated.json").read_text(
        encoding="utf-8"))
    golden["prior_claims"] = [{k: PRIOR_CLAIM[k] for k in ds.PRIOR_CLAIM_PUBLIC}]
    Path(app.state.store.dossier(STAGE2_TARGET)["data_path"]).write_text(json.dumps(golden), encoding="utf-8")
    app.state.store.set_cross_check(STAGE2_TARGET, fake_result(cc.inputs_fingerprint(golden), fpp=0.012, nfpp=1e-4))
    stored = {**golden, "cross_check": cc.with_current(app.state.store.dossier(STAGE2_TARGET)["cross_check"], golden)}
    page = html_module.unescape(client.get(f"/dossiers/{STAGE2_TARGET}").text)
    text = re.sub(r"<[^>]+>", " ", page.split("<body")[1])
    note = _note_prose(pack.render_note(stored))
    missing = sorted(_numbers(note) - _numbers(text))
    assert missing == [], missing


def test_the_star_section_leads_with_the_reddening_paragraph(seeded_dossier):
    """Spec 12.3 as amended: the served and the standalone page print ``pack.star_texts``, which leads with the plain
    reddening paragraph, and the expert block drops the old reddening note beside it."""
    import re
    from pathlib import Path

    from skysift import pack
    from tests.unit.ui_seed import seed_extinction
    client, app, _ids, data = seeded_dossier
    extinction, unreddened = seed_extinction()
    data = {**data, "star": {**data["star"], "extinction": extinction, "unreddened": unreddened}}
    Path(app.state.store.dossier(STAGE2_TARGET)["data_path"]).write_text(json.dumps(data), encoding="utf-8")
    words = pack.star_texts(data)
    assert words.startswith(pack.reddening_plain(data)) and "upper bound" in words
    page = html_module.unescape(client.get(f"/dossiers/{STAGE2_TARGET}").text)
    star = page.split('id="star"')[1].split('id="look"')[0] if 'id="look"' in page else page.split('id="star"')[1]
    assert words in star and "<strong>Reddening.</strong>" not in star
    export = client.get(f"/dossiers/{STAGE2_TARGET}/export.html").text
    blob = re.search(r'<script id="dossier-data" type="application/json">(.*?)</script>', export, re.DOTALL).group(1)
    assert json.loads(blob)["page_texts"]["star"] == words


def test_the_crowding_metric_is_a_row_of_the_star_provenance_list(seeded_dossier):
    """Task 15 fix round 2, M7: the page gives the crowding metric as a row of the star's list (value, column,
    catalogue, per-quarter range and the most-crowded-quarter clause when it holds), not as a sentence, in the served
    page and in the standalone script."""
    import html as html_module
    from pathlib import Path

    from skysift import pack_rows
    client, _app, _ids, data = seeded_dossier
    row = pack_rows.crowdsap_row(data)
    assert row == "0.9000 (the crowdsap column of the KBonus-bkg v1.0 source catalogue, Martinez-Palomera et al. 2023)"
    page = html_module.unescape(client.get(f"/dossiers/{STAGE2_TARGET}").text)
    star = page.split('id="star"')[1].split("</section>")[0]
    assert f"<dt>Crowding metric (CROWDSAP)</dt><dd>{row}</dd>" in star
    assert "The KBonus crowding metric is C =" not in star
    crowd = {"value": 0.85650748, "column": "crowdsap",
             "catalogue": "KBonus-bkg v1.0 source catalogue (Martinez-Palomera et al. 2023)",
             "per_quarter": {"16": 1.0, "17": 0.85650748}, "per_quarter_min": 0.85650748, "per_quarter_max": 1.0}
    assert pack_rows.crowdsap_row({"star": {"crowdsap": crowd}}).endswith(
        "runs 0.8565 to 1.0000 over its 2 usable quarters, and the catalogue value equals the most crowded "
        "quarter's)")
    js = (Path(__file__).resolve().parents[2] / "src" / "skysift" / "ui" / "static" / "dossier.js").read_text(
        encoding="utf-8")
    assert '...(PT.crowdsap ? [["Crowding metric (CROWDSAP)", PT.crowdsap]] : [])' in js
    assert 'para(4, PT.crowdsap' not in js


def test_the_page_says_when_the_star_list_lacks_every_excluded_neighbour():
    """Task 15 fix round 2, M3: the served template and the standalone script carry the sentence for a pixel-excluded
    set that removed nothing because TRICERATOPS's star list lacks every excluded neighbour."""
    from pathlib import Path
    ui = Path(__file__).resolve().parents[2] / "src" / "skysift" / "ui"
    sentence = ("None of the neighbours the difference images exclude is in TRICERATOPS's star list, so the "
                "pixel-excluded run equals the first.")
    assert sentence in (ui / "templates" / "dossier.html").read_text(encoding="utf-8")
    assert sentence in (ui / "static" / "dossier.js").read_text(encoding="utf-8")
