import re
import sys

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from skysift.store.blob import blob_from_url
from skysift.store.db import Store
from skysift.ui.app import create_app
from skysift.ui.sweeps import SweepLauncher
from tests.unit.ui_seed import seed

BASE = "http://127.0.0.1"   # the trusted-host check refuses TestClient's default host, "testserver"


def make_client(settings):
    store = Store(settings.database_url); store.init_schema()
    app = create_app(settings, store=store, blob=blob_from_url(settings.blob_url))
    return TestClient(app, base_url=BASE), app


def test_healthz_and_token(settings):
    client, app = make_client(settings)
    r = client.get("/healthz")
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert len(app.state.token) >= 32


def test_static_urls_carry_a_cache_busting_version(settings):
    client, app = make_client(settings)
    html = client.get("/").text
    assert "/static/app.css?v=" in html and "/static/app.js?v=" in html and "/static/htmx.min.js?v=" in html
    assert f"?v={app.state.git_commit}" in html


def test_error_page_is_plain_html_without_traceback(settings):
    _client, app = make_client(settings)

    @app.get("/boom")
    def boom():
        raise RuntimeError("secret internals")
    r = TestClient(app, base_url=BASE, raise_server_exceptions=False).get("/boom")
    assert r.status_code == 500 and "RuntimeError" in r.text and "Traceback" not in r.text


def test_post_without_token_is_refused(settings):
    client, app = make_client(settings)

    from fastapi import Depends

    from skysift.ui.app import require_token

    @app.post("/guarded", dependencies=[Depends(require_token)])
    def guarded():
        return {"ok": True}
    assert client.post("/guarded").status_code == 403
    assert client.post("/guarded", headers={"X-SkySift-Token": app.state.token}).status_code == 200
    assert client.post("/guarded", data={"_token": app.state.token}).status_code == 200


def test_foreign_host_header_is_refused(settings):
    client, _app = make_client(settings)
    assert client.get("/healthz", headers={"Host": "evil.example"}).status_code == 400
    assert client.get("/healthz", headers={"Host": "evil.example:8765"}).status_code == 400
    for host in ("[::1]:8765", "localhost:8765", "127.0.0.1:8765"):
        assert client.get("/healthz", headers={"Host": host}).status_code == 200


def test_openapi_schema_is_not_served(settings):
    client, _app = make_client(settings)
    assert client.get("/openapi.json").status_code == 404


def test_rendered_page_masks_the_database_password(settings, tmp_path, monkeypatch):
    from skysift.config import load_settings
    monkeypatch.setenv("SKYSIFT_DATABASE_URL", "postgresql://u:secret@h/db")
    pg_settings = load_settings()
    store = Store(settings.database_url); store.init_schema()      # a real sqlite store: nothing connects to h
    app = create_app(pg_settings, store=store, blob=blob_from_url(settings.blob_url))
    from skysift.ui.app import render

    @app.get("/page")
    def page(request: Request):
        return render(request, "error.html", error="x", log_hint="y")
    r = TestClient(app, base_url=BASE).get("/page")
    assert r.status_code == 200
    assert "secret" not in r.text and "postgresql://u:***@h/db" in r.text


SLEEPER = [sys.executable, "-c", "import time; print('started', flush=True); time.sleep(20)"]


def make_seeded_app(settings, tmp_path, command=SLEEPER):
    store = Store(settings.database_url); store.init_schema()
    blob = blob_from_url(settings.blob_url)
    ids = seed(store, blob)
    launcher = SweepLauncher(tmp_path, command=command)
    app = create_app(settings, store=store, blob=blob, launcher=launcher)
    return TestClient(app, base_url=BASE), app, ids, launcher


def make_seeded_client(settings):
    """The seeded dashboard as (client, ids), its sweep launcher in the settings' data directory."""
    client, _app, ids, _launcher = make_seeded_app(settings, settings.data_dir)
    return client, ids


@pytest.fixture
def seeded_settings(settings):
    return settings


def test_tonight_is_the_landing_page_with_hero_and_feed(seeded_settings):
    client, _ids = make_seeded_client(seeded_settings)
    html = client.get("/").text
    assert "Tonight" in html and 'data-count=' in html and 'class="feed' in html and "kic-005689121" in html
    assert "candidates waiting" in html.lower() and "being rechecked" in html.lower()


def test_tonight_feed_excludes_decided_candidates(seeded_settings):
    client, ids = make_seeded_client(seeded_settings)
    feed = client.get("/").text.split('class="feed')[1]
    assert ids["approved_target"] not in feed


def test_tonight_empty_state_points_at_sources(settings):
    client, _app = make_client(settings)
    html = client.get("/").text
    assert "No sweep yet" in html and 'href="/sources"' in html


def test_sources_moved_and_nav_order(seeded_settings):
    client, _ids = make_seeded_client(seeded_settings)
    assert client.get("/sources").status_code == 200
    html = client.get("/").text
    assert html.index('href="/"') < html.index('href="/candidates"') < html.index('href="/live"') < html.index('href="/sources"')


def test_live_flight_deck_has_ring_state_and_ticker(seeded_settings):
    client, _ids = make_seeded_client(seeded_settings)
    html = client.get("/live?mode=simple").text
    assert 'class="ring' in html and 'id="state-line"' in html and 'class="ticker' in html
    assert "rung funnel" not in html.lower()
    assert "funnel" in client.get("/live?mode=expert").text.lower()


def test_tonight_hero_numbers_tiles_and_recheck_strip(seeded_settings):
    client, _ids = make_seeded_client(seeded_settings)
    html = client.get("/").text
    # 7 stars scanned and 3 candidates waiting (stage 1, legacy, stage 2); the approved one has had its look
    assert 'scanned <span data-count="7">7</span> stars' in html
    assert '<span data-count="3">3</span> candidates are waiting for a look.</h1>' in html     # the backlog, apart
    assert "hx-trigger" not in html.split('class="feed')[0].split("<main")[1]
    # only a hidden status speaks, with the final words; the visible hero, whose numbers count up, is no live region
    before_hero = html.split('<section id="hero"')[0].split("<main")[1]
    assert before_hero.count("aria-live") == 1 and '<p id="tonight-status" class="sr-only" aria-live="polite">' in before_hero
    assert " SkySift scanned 7 stars. 3 candidates are waiting for a look.</p>" in before_hero
    assert 'data-live-for="tonight-status"' in html and "signals (dips worth checking)" in html
    assert "rejected so far" in html
    assert '<a class="chip" href="/candidates?status=undetermined">retry the catalogue' in html
    feed = html.split('class="feed')[1]
    assert feed.index("kic-006146676") < feed.index("gaia-dr3-2101234567890123459") < feed.index("kic-005689121")
    assert "Waiting for a look" in html and "discover" not in html.lower() and "—" not in html
    assert '<a href="/candidates">See all</a>' in html


def test_tonight_hero_polls_only_while_a_sweep_runs(settings, tmp_path):
    client, app, _ids, launcher = make_seeded_app(settings, tmp_path)
    idle = client.get("/partials/tonight/hero")
    assert idle.status_code == 200 and "<html" not in idle.text and "hx-trigger" not in idle.text
    try:
        client.post("/sweeps", data={"source": "kbonus_bkg", "limit": "20", "_token": app.state.token},
                    follow_redirects=False)
        hero = client.get("/partials/tonight/hero").text
        assert 'hx-trigger="every 10s"' in hero and "Sweep running: 0 of 20" in hero and 'class="ring' in hero
        assert 'hx-trigger="every 10s"' in client.get("/").text
    finally:
        launcher.stop()
    assert "hx-trigger" not in client.get("/partials/tonight/hero").text


def test_tonight_and_live_render_in_both_modes_and_themes(seeded_settings):
    client, _ids = make_seeded_client(seeded_settings)
    for path in ("/", "/live"):
        for mode in ("simple", "expert"):
            for theme in ("dark", "light"):
                r = client.get(f"{path}?mode={mode}&theme={theme}")
                assert r.status_code == 200, (path, mode, theme)
                assert "—" not in r.text and "discover" not in r.text.lower(), (path, mode, theme)
    expert = client.get("/?mode=expert").text
    assert "Rung funnel" in expert and "stars an hour" in expert
    assert "Rung funnel" not in client.get("/?mode=simple").text


def test_live_state_line_and_ticker_in_words(settings, tmp_path):
    client, app, _ids, launcher = make_seeded_app(settings, tmp_path)
    html = client.get("/live").text
    line = html.split('id="state-line"')[1].split("</p>")[0]
    assert "Idle. The last sweep finished" in line and "found 4 candidates" in line
    ticker = html.split('<ol class="ticker"')[1].split("</ol>")[0]
    assert ticker.count("<li") == 7 and "kic-005689121" in ticker
    for word in ("candidate", "undetermined", "rejected"):
        assert f">{word}</span>" in ticker, word
    assert 'title="no checks ran yet">not vetted</span>' in ticker and ticker.count('Z">') == 7     # naive UTC, marked
    try:
        client.post("/sweeps", data={"source": "kbonus_bkg", "limit": "20", "_token": app.state.token},
                    follow_redirects=False)
        sweep = client.get("/partials/live/sweep").text
        assert "Scanning star 1 of 20" in sweep and 'data-live-for="state-line"' in sweep
        assert "finishes the stars in flight" in sweep and "drops the stars in flight. They are redone next time" in sweep
        assert 'class="ring' in sweep
    finally:
        launcher.stop()


def test_a_sweep_started_outside_the_dashboard_is_shown_as_running(settings, tmp_path):
    from skysift.store.db import make_job_key
    client, app, _ids, _launcher = make_seeded_app(settings, tmp_path)
    assert "outside the dashboard" not in client.get("/live").text             # the seed's runs are minutes old
    store = app.state.store
    store.start_run(make_job_key("kic-005689121", "box_dip", "9+cli", "1"), "kic-005689121", "box_dip", "9+cli", "1")
    live = client.get("/live").text
    line = live.split('id="state-line"')[1].split("</p>")[0]
    assert "A sweep started outside the dashboard is writing results: 7 stars done in the last hour." in line
    deck = live.split('id="sweep"')[1].split('id="cards"')[0]
    assert 'class="ring' not in deck and 'data-active="1"' in deck and "<button" not in deck
    sweep = client.get("/partials/live/sweep?was=1")
    assert 'hx-trigger="every 5s"' in sweep.text and "HX-Trigger" not in sweep.headers
    hero = client.get("/partials/tonight/hero").text
    assert 'hx-trigger="every 10s"' in hero and "outside the dashboard is writing results" in hero


def test_quiet_live_regions_rewrite_only_changed_words(settings):
    client, _app = make_client(settings)
    js = client.get("/static/app.js").text
    handler = js[js.index('"htmx:afterSettle"'):]
    assert "[data-live-for]" in handler and "!== words" in handler
    assert ".sr-only {" in client.get("/static/app.css").text


def test_sources_page_lists_registry_and_progress(settings, tmp_path):
    client, _app, _ids, _launcher = make_seeded_app(settings, tmp_path)
    r = client.get("/sources")
    assert r.status_code == 200
    assert "kbonus_bkg" in r.text and "CPU h/10k" in r.text and "694" in r.text      # 250 s/star -> 694 CPU h per 10k
    assert "7 done" in r.text and "planned" in r.text and "Start sweep" in r.text


def test_start_sweep_form_launches_child_and_redirects(settings, tmp_path):
    client, app, _ids, launcher = make_seeded_app(settings, tmp_path)
    try:
        form = client.get("/partials/sweep-form?source=kbonus_bkg")
        assert form.status_code == 200 and 'name="store_periodogram"' in form.text and 'value="all" selected' in form.text
        r = client.post("/sweeps", data={"source": "kbonus_bkg", "limit": "25", "min_period": "0.5", "max_period": "200",
                                        "retention_mode": "budget", "budget_gib": "20", "workers_io": "4", "workers_cpu": "2",
                                        "background_only": "on", "gmag_max": "19", "store_periodogram": "all",
                                        "_token": app.state.token}, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/live"
        active = launcher.active()
        assert active["request"]["retention"] == "budget:20" and active["request"]["limit"] == 25
        page = client.get("/sources").text
        assert page.count("Start sweep") == 0 and "is running" in page and "kbonus_bkg" in page
        r2 = client.post("/sweeps", data={"source": "kbonus_bkg", "_token": app.state.token}, follow_redirects=False)
        assert r2.status_code == 409 and "already running" in r2.text
        r = client.post("/sweeps/stop", data={"mode": "now", "_token": app.state.token}, follow_redirects=False)
        assert r.status_code == 303 and launcher.active() is None
    finally:
        launcher.stop()                                   # never leave the SLEEPER child behind if an assertion fails


def test_start_sweep_rejects_bad_window(settings, tmp_path):
    client, app, _ids, launcher = make_seeded_app(settings, tmp_path)
    try:
        r = client.post("/sweeps", data={"source": "kbonus_bkg", "min_period": "40", "max_period": "5",
                                        "_token": app.state.token})
        assert r.status_code == 400 and "period window" in r.text and launcher.active() is None
    finally:
        launcher.stop()


def test_htmx_posts_get_hx_redirect_not_a_303(settings, tmp_path):
    client, app, _ids, launcher = make_seeded_app(settings, tmp_path)
    hx = {"HX-Request": "true", "X-SkySift-Token": app.state.token}
    try:
        r = client.post("/sweeps", data={"source": "kbonus_bkg", "limit": "5"}, headers=hx, follow_redirects=False)
        assert r.status_code == 200 and r.headers["HX-Redirect"] == "/live" and launcher.active() is not None
        r = client.post("/sweeps/stop", data={"mode": "now"}, headers=hx, follow_redirects=False)
        assert r.status_code == 200 and r.headers["HX-Redirect"] == "/live" and launcher.active() is None
    finally:
        launcher.stop()


def test_start_sweep_rejects_malformed_values(settings, tmp_path):
    client, app, _ids, launcher = make_seeded_app(settings, tmp_path)
    try:
        for field, value in (("limit", "abc"), ("limit", "-4"), ("min_period", "x"), ("gmag_max", "nan"),
                             ("workers_io", "0"), ("budget_gib", "1e999"), ("budget_gib", "-3"), ("budget_gib", "zz")):
            data = {"source": "kbonus_bkg", "retention_mode": "budget", "_token": app.state.token, field: value}
            r = client.post("/sweeps", data=data)
            assert r.status_code == 400, (field, value, r.status_code)
            assert field in r.text, (field, value)
            assert launcher.active() is None, (field, value)
    finally:
        launcher.stop()


def test_start_sweep_refuses_sources_that_are_not_live(settings, tmp_path):
    client, app, _ids, launcher = make_seeded_app(settings, tmp_path)
    try:
        for name in ("tess_spoc", "nonexistent"):
            r = client.post("/sweeps", data={"source": name, "_token": app.state.token})
            assert r.status_code == 400 and f"source {name} is not live" in r.text and launcher.active() is None
    finally:
        launcher.stop()


def test_sweep_posts_need_the_token(settings, tmp_path):
    client, _app, _ids, launcher = make_seeded_app(settings, tmp_path)
    try:
        assert client.post("/sweeps", data={"source": "kbonus_bkg"}).status_code == 403
        assert client.post("/sweeps/stop").status_code == 403
        assert launcher.active() is None
    finally:
        launcher.stop()


def test_form_echo_is_html_escaped(settings, tmp_path):
    client, app, _ids, launcher = make_seeded_app(settings, tmp_path)
    try:
        evil = '"><script>alert(1)</script>'
        r = client.post("/sweeps", data={"source": evil, "limit": evil, "_token": app.state.token})
        assert r.status_code == 400 and "<script>alert" not in r.text and "&lt;script&gt;" in r.text
    finally:
        launcher.stop()


def test_live_page_cards_and_idle_state(settings, tmp_path):
    client, _app, _ids, _launcher = make_seeded_app(settings, tmp_path)
    r = client.get("/live?mode=expert")                     # the rung table is expert
    assert r.status_code == 200 and "rung4_novelty" in r.text and "idle" in r.text
    cards = client.get("/partials/live/cards?mode=expert")
    assert cards.status_code == 200 and "candidate_stage1" in cards.text and "<html" not in cards.text
    assert 'hx-trigger="every 5s"' not in client.get("/partials/live/sweep").text      # no polling while idle


def test_live_page_shows_active_sweep_and_tail(settings, tmp_path):
    import time
    client, app, _ids, launcher = make_seeded_app(settings, tmp_path)
    try:
        r = client.post("/sweeps", data={"source": "kbonus_bkg", "_token": app.state.token}, follow_redirects=False)
        assert r.status_code == 303
        for _ in range(50):
            if launcher.tail(): break
            time.sleep(0.1)
        r = client.get("/partials/live/sweep")
        assert "started" in r.text and "Stop" in r.text and 'hx-trigger="every 5s"' in r.text
        assert "stopping" not in r.text
        launcher.stop()
        r = client.get("/partials/live/sweep")
        assert "idle" in r.text and r.headers.get("HX-Trigger") == "sweep-finished"
        assert "HX-Trigger" not in client.get("/partials/live/sweep").headers        # only on the transition
    finally:
        launcher.stop()


def test_live_sweep_shows_stopping_state_without_second_stop_button(settings, tmp_path, monkeypatch):
    client, app, _ids, launcher = make_seeded_app(settings, tmp_path)
    try:
        client.post("/sweeps", data={"source": "kbonus_bkg", "_token": app.state.token}, follow_redirects=False)
        assert launcher.active() is not None
        monkeypatch.setattr(launcher, "stop", lambda: False)                  # the child is slow to exit
        r = client.post("/sweeps/stop", data={"mode": "now", "_token": app.state.token}, follow_redirects=False)
        assert r.status_code == 303 and app.state.stop_requested == (launcher.active()["pid"], launcher.active()["stamp"])
        r = client.get("/partials/live/sweep")
        assert "stopping, waiting for the sweep to exit" in r.text and "<button" not in r.text
        assert 'hx-trigger="every 5s"' in r.text                              # keep polling until it is gone
        monkeypatch.setattr(launcher, "stop", lambda: True)
        client.post("/sweeps/stop", data={"mode": "now", "_token": app.state.token}, follow_redirects=False)
        assert app.state.stop_requested is None
        assert "Stop" in client.get("/partials/live/sweep").text
    finally:
        monkeypatch.undo()
        launcher.stop()


def test_live_idle_shows_past_sweeps_with_engine_summary(settings, tmp_path):
    client, app, _ids, launcher = make_seeded_app(
        settings, tmp_path, command=[sys.executable, "-c", "import json; print(json.dumps({'processed': 7, 'signals': 3}))"])
    client.post("/sweeps", data={"source": "kbonus_bkg", "_token": app.state.token}, follow_redirects=False)
    import time
    for _ in range(100):
        if launcher.active() is None: break
        time.sleep(0.1)
    r = client.get("/partials/live/sweep")
    assert "Past sweeps" in r.text and "processed 7" in r.text and "signals 3" in r.text


def test_live_survives_a_reattached_lock_without_started_or_request(settings, tmp_path):
    import json
    import os

    from skysift.ui.sweeps import _process_created
    client, _app, _ids, launcher = make_seeded_app(settings, tmp_path)
    try:
        launcher.lock.write_text(json.dumps({"pid": os.getpid(), "created": _process_created(os.getpid()),
                                             "log": str(tmp_path / "x.log"), "stamp": "20260101T000000000000Z"}),
                                 encoding="utf-8")                      # a live pid (this test process), nothing else
        for url in ("/live", "/partials/live/sweep"):
            r = client.get(url)
            assert r.status_code == 200 and "running" in r.text, url
        for started in (None, "not a date", 12, "2026-01-01T00:00:00"):     # null, malformed, wrong type, naive
            launcher.lock.write_text(json.dumps({"pid": os.getpid(), "created": _process_created(os.getpid()),
                                                 "log": str(tmp_path / "x.log"), "stamp": "20260101T000000000000Z",
                                                 "started": started, "request": "garbage"}), encoding="utf-8")
            r = client.get("/partials/live/sweep")
            assert r.status_code == 200 and "running" in r.text, started
    finally:
        launcher.lock.unlink(missing_ok=True)


def test_sweep_finished_header_is_sent_to_every_client_that_saw_it_running(settings, tmp_path):
    client, app, _ids, launcher = make_seeded_app(settings, tmp_path)
    try:
        client.post("/sweeps", data={"source": "kbonus_bkg", "_token": app.state.token}, follow_redirects=False)
        assert 'hx-vals=\'{"was": 1}\'' in client.get("/partials/live/sweep").text
        launcher.stop()
        for _ in range(2):                                   # two tabs: neither may lose the signal to the other
            r = client.get("/partials/live/sweep?was=1")
            assert "idle" in r.text and r.headers.get("HX-Trigger") == "sweep-finished"
        assert "HX-Trigger" not in client.get("/partials/live/sweep").headers      # idle -> idle, no was
        assert "HX-Trigger" not in client.get("/partials/live/sweep?was=0").headers
    finally:
        launcher.stop()


def test_provisional_lock_is_not_shown_as_stopping(settings, tmp_path):
    import json
    from datetime import UTC, datetime
    client, app, _ids, launcher = make_seeded_app(settings, tmp_path)
    try:
        launcher.lock.write_text(json.dumps({"pid": None, "provisional": True, "stamp": "20260101T000000000000Z",
                                             "log": str(tmp_path / "x.log"), "started": datetime.now(UTC).isoformat(),
                                             "request": {"source": "kbonus_bkg", "limit": 5}}), encoding="utf-8")
        app.state.stop_requested = (None, "20260101T000000000000Z")
        r = client.get("/partials/live/sweep")
        assert r.status_code == 200 and "stopping" not in r.text and "running" in r.text
    finally:
        launcher.lock.unlink(missing_ok=True)


def test_live_tail_is_entity_escaped(settings, tmp_path):
    import time
    cmd = [sys.executable, "-c", "import time; print('<script>alert(1)</script>', flush=True); time.sleep(20)"]
    client, app, _ids, launcher = make_seeded_app(settings, tmp_path, command=cmd)
    try:
        client.post("/sweeps", data={"source": "kbonus_bkg", "_token": app.state.token}, follow_redirects=False)
        for _ in range(50):
            if launcher.tail(): break
            time.sleep(0.1)
        r = client.get("/partials/live/sweep?mode=expert")       # the log is expert
        assert "&lt;script&gt;alert(1)" in r.text and "<script>alert" not in r.text
    finally:
        launcher.stop()


def test_live_idle_renders_a_corrupt_lock_history_record(settings, tmp_path):
    import json
    client, _app, _ids, launcher = make_seeded_app(settings, tmp_path)
    (launcher.dir / "20260101T000000000000Z.json").write_text(json.dumps(
        {"stamp": "20260101T000000000000Z", "log": "", "request": {}, "reason": "corrupt-lock", "exit_code": None,
         "finished": "2026-01-01T00:00:01+00:00", "summary": None}), encoding="utf-8")
    (launcher.dir / "20260102T000000000000Z.json").write_text(json.dumps(
        {"stamp": "20260102T000000000000Z", "log": "", "reason": "corrupt-lock"}), encoding="utf-8")   # no request at all
    for url in ("/live", "/partials/live/sweep"):
        r = client.get(url)
        assert r.status_code == 200 and "Past sweeps" in r.text, url


def test_plots_endpoint_reports_outage_without_breaking_the_page(settings, tmp_path, monkeypatch):
    from skysift.net import ServiceUnavailable
    from skysift.ui import plots as plots_mod
    client, _app, ids, _launcher = make_seeded_app(settings, tmp_path)
    # the seeded artifacts hold fake bytes, not FITS: make the parser raise an outage-like error to exercise the path
    monkeypatch.setitem(plots_mod.PARSERS, "kbonus_bkg", lambda b: (_ for _ in ()).throw(ServiceUnavailable("mast down")))
    r = client.get(f"/api/signals/{ids['candidate']}/plots.json")
    assert r.status_code == 503 and "mast down" in r.json()["detail"]
    page = client.get(f"/signals/{ids['candidate']}")          # an archive outage must not break the page itself
    assert page.status_code == 200 and 'id="why"' in page.text and "rung1_detection" in page.text
    assert 'id="plots"' in page.text
    assert client.get("/api/signals/999999/plots.json").status_code == 404


def test_plots_json_is_strict(settings, tmp_path, monkeypatch):
    from skysift.ui import plots as plots_mod
    from tests.synthetic import inject_transit, make_lightcurve
    client, _app, ids, _launcher = make_seeded_app(settings, tmp_path)
    lc = inject_transit(make_lightcurve(n_days=400, seed=3, noise_ppm=500), 13.7695, 136.3, 0.096, 1800)
    monkeypatch.setitem(plots_mod.PARSERS, "kbonus_bkg", lambda b: lc)
    r = client.get(f"/api/signals/{ids['candidate']}/plots.json")
    assert r.status_code == 200 and "NaN" not in r.text
    body = r.json()
    assert body["periodogram"] is None and body["periodogram_status"] == "off" and body["seasons"]["season"] == [0, 1, 2, 3] and len(body["fold"]["bin_flux"]) > 10
    assert client.get(f"/api/signals/{ids['candidate']}/plots.json").json() == body     # cached per signal id


@pytest.mark.parametrize("case", ["present", "missing", "deleted", "corrupt"])
def test_plots_periodogram_tolerates_evicted_missing_and_corrupt_summaries(settings, tmp_path, monkeypatch, case):
    import hashlib
    from pathlib import Path

    import numpy as np

    from skysift.detectors.periodogram import PeriodogramSummary
    from skysift.store.db import ArtifactRow, RunRow, utcnow
    from skysift.ui import plots as plots_mod
    from tests.synthetic import inject_transit, make_lightcurve
    lc = inject_transit(make_lightcurve(n_days=400, seed=3, noise_ppm=500), 13.7695, 136.3, 0.096, 1800)
    monkeypatch.setitem(plots_mod.PARSERS, "kbonus_bkg", lambda b: lc)
    client, app, ids, _launcher = make_seeded_app(settings, tmp_path)
    good = PeriodogramSummary.from_spectrum(np.array([13.7695, 6.88]), np.array([21.0, 8.0]), 0.5, 200.0, [0.5, 200.0],
                                            [{"period": 13.7695, "sde": 21.0, "duration": 0.096, "rank": 0}],
                                            n_bins=64).to_npz()
    data = b"not an npz" if case == "corrupt" else good
    sha = hashlib.sha256(data).hexdigest()
    st = app.state
    loc = str(Path(settings.data_dir) / "nowhere.npz") if case == "missing" else st.blob.put(data, "pg.npz")[1]
    st.store.add_artifact(sha, len(data), "skysift://periodogram/kic-005689121", loc, "kic-005689121", kind="summary")
    with st.store.session() as s:
        if case == "deleted": s.get(ArtifactRow, sha).deleted_at = utcnow()
        s.get(RunRow, ids["run_ids"]["candidate"]).periodogram_sha256 = sha
    r = client.get(f"/api/signals/{ids['candidate']}/plots.json")
    assert r.status_code == 200 and "NaN" not in r.text
    body = r.json()
    assert (body["periodogram"] is not None) is (case == "present")
    assert body["periodogram_status"] == {"present": "ok", "missing": "missing", "deleted": "evicted",
                                          "corrupt": "unreadable"}[case]


def test_plots_endpoint_maps_only_integrity_errors_to_409(settings, tmp_path, monkeypatch):
    from skysift.ui import plots as plots_mod
    client, _app, ids, _launcher = make_seeded_app(settings, tmp_path)
    monkeypatch.setitem(plots_mod.PARSERS, "kbonus_bkg",
                        lambda b: (_ for _ in ()).throw(plots_mod.IntegrityError("sha256 changed")))
    r = client.get(f"/api/signals/{ids['candidate']}/plots.json")
    assert r.status_code == 409 and r.json()["error"] == "integrity" and "sha256 changed" in r.json()["detail"]
    # any parse failure of the stored raw file is an integrity failure too (the loader wraps it)
    monkeypatch.setitem(plots_mod.PARSERS, "kbonus_bkg", lambda b: (_ for _ in ()).throw(ValueError("bad header")))
    r = client.get(f"/api/signals/{ids['candidate']}/plots.json")
    assert r.status_code == 409 and "cannot be read" in r.json()["detail"]


def test_pages_render_on_a_migrated_m1_database(settings, tmp_path):
    from sqlalchemy import text

    from skysift.store.db import Base
    store = Store(settings.database_url); Base.metadata.create_all(store.engine)
    with store.engine.begin() as c:                      # strip what milestone 2 added, as test_migrations does
        c.execute(text("ALTER TABLE runs DROP COLUMN periodogram_sha256"))
        c.execute(text("ALTER TABLE candidates DROP COLUMN decided_at"))
        c.execute(text("DROP INDEX ix_queries_target_id")); c.execute(text("ALTER TABLE queries DROP COLUMN target_id"))
        c.execute(text("ALTER TABLE artifacts DROP COLUMN kind")); c.execute(text("DROP TABLE decisions"))
    store = Store(settings.database_url); store.init_schema()
    ids = seed(store, blob_from_url(settings.blob_url))
    app = create_app(settings, store=store, blob=blob_from_url(settings.blob_url),
                     launcher=SweepLauncher(tmp_path, command=SLEEPER))
    client = TestClient(app, base_url=BASE)
    sid = ids["candidate"]
    for path in ("/", "/live", "/candidates", "/candidates?status=all", "/approved", f"/signals/{sid}",
                 f"/signals/{sid}/review.json"):
        assert client.get(path).status_code == 200, path
    r = client.get(f"/api/signals/{sid}/plots.json")           # the seed's raw file is fake bytes, not FITS
    assert r.status_code == 409 and "error" in r.json()
    # the migrated columns (decided_at, periodogram_sha256, queries.target_id) are what these routes read and write
    r = client.post(f"/signals/{sid}/decide", data={"decision": "approve", "note": "ok", "_token": app.state.token})
    assert r.status_code == 200 and "approved" in r.text


def test_stop_defaults_to_graceful_and_live_shows_the_finishing_state(settings, tmp_path):
    client, app, _ids, launcher = make_seeded_app(settings, tmp_path)
    try:
        client.post("/sweeps", data={"source": "kbonus_bkg", "_token": app.state.token}, follow_redirects=False)
        running = client.get("/partials/live/sweep").text
        assert 'value="graceful"' in running and 'value="now"' in running and "finishing" not in running
        r = client.post("/sweeps/stop", data={"_token": app.state.token}, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/live"
        active = launcher.active()
        assert active is not None and active["stop_requested_at"] and launcher.stop_file.exists()
        assert app.state.stop_requested is None                                 # not the hard "stopping" state
        r = client.get("/partials/live/sweep")
        assert "finishing" in r.text and active["stop_requested_at"] in r.text and "in flight" in r.text
        assert 'value="now"' in r.text and 'value="graceful"' not in r.text     # only the hard stop is left to offer
        assert 'hx-trigger="every 5s"' in r.text                                # keep polling until it exits
        r = client.post("/sweeps/stop", data={"mode": "now", "_token": app.state.token}, follow_redirects=False)
        assert r.status_code == 303 and launcher.active() is None and not launcher.stop_file.exists()
        assert launcher.history()[0]["reason"] == "stopped"
        assert "idle" in client.get("/partials/live/sweep").text
    finally:
        launcher.stop()


def test_stop_now_is_the_hard_stop(settings, tmp_path):
    client, app, _ids, launcher = make_seeded_app(settings, tmp_path)
    try:
        client.post("/sweeps", data={"source": "kbonus_bkg", "_token": app.state.token}, follow_redirects=False)
        r = client.post("/sweeps/stop", data={"mode": "now", "_token": app.state.token}, follow_redirects=False)
        assert r.status_code == 303 and launcher.active() is None and launcher.history()[0]["reason"] == "stopped"
        assert client.post("/sweeps/stop", data={"mode": "bogus", "_token": app.state.token},
                           follow_redirects=False).status_code == 400
    finally:
        launcher.stop()


def test_live_history_marks_a_graceful_stop(settings, tmp_path):
    client, app, _ids, launcher = make_seeded_app(
        settings, tmp_path, command=[sys.executable, "-c",
                                     "import json; print(json.dumps({'processed': 4, 'signals': 1, 'stopped': 'graceful'}))"])
    client.post("/sweeps", data={"source": "kbonus_bkg", "_token": app.state.token}, follow_redirects=False)
    import time
    for _ in range(100):
        if launcher.active() is None: break
        time.sleep(0.1)
    r = client.get("/partials/live/sweep")
    assert "processed 4" in r.text and "stopped early" in r.text


def test_mode_and_theme_cookies_drive_the_html_attributes(settings):
    client, app = make_client(settings)
    assert 'data-mode="simple"' in client.get("/").text and 'data-theme="system"' in client.get("/").text
    r = client.post("/prefs", data={"mode": "expert", "theme": "dark", "next": "/candidates", "_token": app.state.token},
                    follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/candidates"
    assert "skysift_mode=expert" in r.headers.get("set-cookie", "") or r.cookies.get("skysift_mode") == "expert"
    html = client.get("/").text
    assert 'data-mode="expert"' in html and 'data-theme="dark"' in html
    assert 'data-mode="simple"' in client.get("/?mode=simple").text          # the query wins for one request
    r = client.post("/prefs", data={"mode": "expert", "theme": "dark", "next": "https://evil.example/",
                                    "_token": app.state.token}, follow_redirects=False)
    assert r.headers["location"] == "/"                                       # only same-origin paths


def test_prefs_requires_the_token(settings):
    client, _app = make_client(settings)
    assert client.post("/prefs", data={"mode": "expert", "theme": "dark"}).status_code == 403


def test_stylesheet_declares_the_phone_breakpoints(settings):
    client, _app = make_client(settings)
    css = client.get("/static/app.css").text
    for token in ("--bg", "--surface", "--ion", "--solar", "--pass", "--fail", "--undet", "--running"):
        assert token + ":" in css
    assert "@media (max-width: 720px)" in css and "@media (max-width: 900px)" in css and "@media (min-width: 1280px)" in css
    assert "prefers-reduced-motion" in css and "[data-theme=\"dark\"]" in css and "prefers-color-scheme: dark" in css


def test_every_page_links_the_local_fonts_and_nothing_remote(settings):
    client, _app = make_client(settings)
    for path in ("/", "/candidates", "/live", "/sources", "/approved", "/about"):
        html = client.get(path).text
        assert "https://" not in html.split("<main")[0].replace("https://github.com", "")   # head has no remote asset
    css = client.get("/static/app.css").text
    assert "/static/fonts/SpaceGrotesk-Bold.woff2" in css and "/static/fonts/IBMPlexSans-Regular.woff2" in css
    assert client.get("/static/fonts/IBMPlexMono-Regular.woff2").status_code == 200


def test_contrast_tool_passes_on_the_shipped_stylesheet():
    from tools.contrast_check import check
    assert check() == []


def test_contrast_tool_reports_a_failing_pair():
    from tools.contrast_check import check
    css = (assets_static() / "app.css").read_text(encoding="utf-8").replace("--muted: #6b7489", "--muted: #2a3040")
    assert any("muted" in failure and "dark" in failure for failure in check(css))


def test_about_placeholder_and_shell_render_in_both_modes_and_themes(settings):
    client, _app = make_client(settings)
    for mode in ("simple", "expert"):
        for theme in ("system", "dark", "light"):
            r = client.get(f"/about?mode={mode}&theme={theme}")
            assert r.status_code == 200 and f'data-mode="{mode}"' in r.text and f'data-theme="{theme}"' in r.text
            assert 'class="topbar' in r.text and 'class="bottomnav' in r.text and 'id="toasts"' in r.text
    assert client.get("/about?mode=bogus&theme=neon").text.count('data-mode="simple" data-theme="system"') == 1


def assets_static():
    from skysift.ui.assets import STATIC
    return STATIC


def test_prefs_refuses_paths_that_leave_the_origin_and_unknown_values(settings):
    client, app = make_client(settings)
    for target in ("//evil.example/", "/\\evil.example", "/\t/evil.example", "javascript:alert(1)", "candidates",
                   "//[bad"):
        r = client.post("/prefs", data={"mode": "expert", "next": target, "_token": app.state.token},
                        follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/", target
    r = client.post("/prefs", data={"next": "/live?status=all", "theme": "light", "_token": app.state.token},
                    follow_redirects=False)
    assert r.headers["location"] == "/live?status=all" and "skysift_mode" not in r.headers.get("set-cookie", "")
    assert client.post("/prefs", data={"mode": "wizard", "_token": app.state.token}).status_code == 400
    html = client.get("/live?mode=expert&status=all").text
    assert 'name="next" value="/live?status=all"' in html                    # the one-request override is not kept


def test_existing_pages_render_on_the_shared_components(settings, tmp_path):
    client, _app, _ids, _launcher = make_seeded_app(settings, tmp_path)
    html = client.get("/candidates").text
    assert 'aria-current="true">stage 1 candidates <span class="chip-count">3</span></a>' in html and 'class="pill pill-' in html
    assert '<a class="chip" href="/approved?status=approved" aria-current="true">approved</a>' in client.get("/approved").text
    live = client.get("/live").text
    assert 'class="tile stat"' in live and "data-count=" in live
    assert 'class="envinfo only-expert"' in live and 'aria-current="page"' in live
    assert "style=" not in html + client.get("/partials/sweep-form?source=kbonus_bkg").text
    # the progress ring carries its percent as a custom property, the one inline style the pages have
    assert re.sub(r'style="--value: \d+"', "", live).count("style=") == 0


def test_the_queue_script_opens_the_split_view_only_on_wide_screens(settings):
    client, _app = make_client(settings)
    js = client.get("/static/app.js").text
    assert "(min-width: 1280px)" in js and "history.replaceState" in js and 'htmx.trigger(' in js
    # the selection and the URL follow only a detail that arrived; a failed load says so and changes nothing
    after = js[js.index('"htmx:afterRequest"'):]
    assert "e.detail.successful" in after and "Could not load the details" in after
    assert after.index("e.detail.successful") < after.index("history.replaceState")
    opener = js[js.index("const openRow"):js.index('"htmx:afterRequest"')]
    assert "replaceState" not in opener and "select(row)" not in opener
    css = client.get("/static/app.css").text
    assert ".card.candidate" in css and ".sparkline" in css and "tr.row-link.is-selected" in css
