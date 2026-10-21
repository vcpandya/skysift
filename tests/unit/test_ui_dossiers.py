"""The Dossiers section of the dashboard (dossiers plan Task 5): the tiles with their two separate probabilities, the
dossier page in both modes, the standalone download, the dossier jobs, the pack steps, the planet thumbnail and the
Probabilities block of the verdict popup. TestClient against the dashboard seed with one hand-made dossier."""
import json
import sys
import xml.etree.ElementTree as ET

import pytest

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
    assert STAGE1_TARGET not in html                    # a stage-1 candidate without a dossier is not in the final set
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
    assert "No dossiers yet. A dossier is generated for a stage-2 candidate or an approved one." in main
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
    assert client.get("/partials/dossiers/kic-000000001/status").status_code == 404
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
    assert "data-part=\"hz\"" not in svg and "#fff3d6" in svg
