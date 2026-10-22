"""The cross-check (referee pack spec 4, rulings n to v): the inputs fingerprint, the export the TRICERATOPS tool reads,
the import of its result into ``dossiers.cross_check``, the verdict the live gate reads, the class labels and the
pack's comparison sentence; and the tool's pure helpers, which run here under the engine's interpreter (no test calls
TRICERATOPS)."""
import csv
import importlib
import json
import math
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from astropy.io import fits

from skysift import crosscheck as cc
from skysift.dossier import EXCLUSION_RULE, generate
from skysift.lc.tpf import tpf_listing_url
from skysift.stars import mamajek_table
from skysift.store.db import TargetRow
from tests.unit.synthetic_tpf import CCD_ORIGIN, NEIGHBOUR_KIC, listing_html, tpf_fits, tpf_name, tpf_quarters
from tests.unit.test_dossier import RESPONSES, _make_env, fake_fetch
from tests.unit.ui_seed import STAGE2_TARGET

DATA = {"ephemeris": {"period_days": 40.29, "t0_bkjd": 136.3, "depth_ppm": 2180.0, "duration_h": 4.16},
        "target": {"gmag": 16.2},
        "fpp": {"inputs": {"neighbours": [{"source_id": "2", "sep_arcsec": 10.8, "gmag": 12.8, "bp_rp": 1.1},
                                          {"source_id": "1", "sep_arcsec": 7.1, "gmag": 19.7, "bp_rp": 1.8}]}}}
# the Gaia answer to the neighbours' parameter query: the 7.14 arcsec neighbour of the seed's cone with a GSP-Phot Teff
# and radius but no FLAME mass; the 10.8 arcsec one has no row, so its parameters come from its colour
NEIGHBOUR_PARAMS_CSV = (b"source_id,teff_gspphot,bp_rp,radius_gspphot,mass_flame\n"
                        b"2101234567890120001,3500.0,1.814,0.45,\n")
EXPORT_RESPONSES = {**RESPONSES, "n.source_id IN": NEIGHBOUR_PARAMS_CSV}


def export_fetch(tpfs, listing_up: bool = True):
    """The export's requests: the Gaia queries of ``fake_fetch`` and MAST's listing and pixel files of
    ``NEIGHBOUR_KIC`` (``tpfs``), each logged like ``net.fetch``; ``calls`` keeps the MAST URLs asked for. Without
    ``listing_up`` the listing is an outage; any other MAST URL is a 404."""
    from skysift.net import ServiceUnavailable
    gaia, base = fake_fetch(EXPORT_RESPONSES), tpf_listing_url(NEIGHBOUR_KIC)
    files = {base + tpf_name(NEIGHBOUR_KIC, b.quarter): bytes(b) for b in tpfs}

    def fetch(url, *, params=None, service, store=None, target_id=None, **_kw):
        if service == "gaia":
            return gaia(url, params=params, service=service, store=store, target_id=target_id)
        assert service == "mast", service
        fetch.calls.append(url)
        if url == base and listing_up:
            data = listing_html(NEIGHBOUR_KIC, [tpf_name(NEIGHBOUR_KIC, b.quarter) for b in tpfs])
        elif url in files:
            data = files[url]
        else:
            raise ServiceUnavailable(f"mast {url} -> HTTP {503 if url == base else 404}")
        if store:
            store.log_query(service, url, params or {}, 200, len(data), target_id=target_id)
        return data
    fetch.calls = []
    return fetch


def fake_runs(fpp: float = 0.012, nfpp: float = 1e-4, n: int = cc.N_RUNS, p_stp: float = 0.0,
              p_ptp: float = 0.1) -> list[dict]:
    """``n`` identical runs as the tool writes them."""
    return [{"seed": 1 + k, "fpp": fpp, "fpp_no_stp": cc.fpp_without_stp(fpp, p_stp), "nfpp": nfpp, "p_stp": p_stp,
             "p_ptp": p_ptp, "best_scenario": {"id": "169822650", "scenario": "TP", "prob": 1 - fpp},
             "degenerate": False} for k in range(n)]


def fake_result(sha: str, fpp: float = 0.012, nfpp: float = 1e-4, n: int = cc.N_RUNS, p_stp: float = 0.0,
                scenarios: list | None = None) -> dict:
    """A result file as the tool writes it: ``n`` runs whose medians are ``fpp`` and ``nfpp``."""
    runs = fake_runs(fpp, nfpp, n, p_stp)
    return {"tool": cc.TOOL, "tool_version": "1.1.0", "run_utc": "2026-10-22T12:00:00+00:00", "n_runs": n,
            "n_degenerate": 0, **cc.summarise(runs), "runs": runs,
            "scenarios": scenarios or [{"ID": "169822650", "scenario": "TP", "prob": 1 - fpp}],
            "stars": [{"id": "169822650", "tmag": 15.19, "sep_arcsec": 0.0, "flux_ratio": 0.98, "tdepth": 0.0022}],
            "inputs_sha256": sha,
            "caveats": [("The light is shared in the Kepler band with Gaia G as the proxy; the limb darkening comes "
                         "from the package's Kepler table.")],
            "settings": {"N": 1_000_000, "exptime_days": 0.0204,
                         "field_stars": {"file": "r.population.csv", "n_rows": 8883, "sha256": "c" * 64}}}


def _write(path: Path, obj) -> Path:
    path.write_text(json.dumps(obj), encoding="utf-8")
    return path


def store_pixel_files(env) -> None:
    """Make the seed's stage-2 target a star extracted from the pixels of KIC 6146666 (``meta.tpf_org``) and keep
    five synthetic pixel files of that star in the blob as ``kind="tpf"`` artifacts (``env.tpfs``)."""
    with env.store.session() as s:
        t = s.get(TargetRow, STAGE2_TARGET)
        t.meta = {**(t.meta or {}), "tpf_org": NEIGHBOUR_KIC}
    env.tpfs = tpf_quarters()
    for b in env.tpfs:
        name = tpf_name(NEIGHBOUR_KIC, b.quarter)
        sha, loc = env.blob.put(bytes(b), name)
        env.store.add_artifact(sha, len(b), tpf_listing_url(NEIGHBOUR_KIC) + name, loc, target_id=STAGE2_TARGET,
                               kind="tpf")


@pytest.fixture
def env(settings, monkeypatch):
    """The seed's stage-2 dossier with the pixel files of ``store_pixel_files``; ``env.xfetch`` answers the export's
    neighbour-parameter query too."""
    env = _make_env(settings, monkeypatch)
    store_pixel_files(env)
    env.report = generate(env.store, env.blob, env.settings, STAGE2_TARGET, fetch=env.fetch)
    env.xfetch = export_fetch(env.tpfs)
    return env


def _art(env, quarter: int) -> dict:
    return env.store.live_artifact(tpf_listing_url(NEIGHBOUR_KIC) + tpf_name(NEIGHBOUR_KIC, quarter))


def _stored(env) -> dict:
    return json.loads(Path(env.store.dossier(STAGE2_TARGET)["data_path"]).read_text(encoding="utf-8"))


def test_inputs_fingerprint_changes_with_the_period_and_the_neighbours():
    base = cc.inputs_fingerprint(DATA)
    assert len(base) == 64 and base == cc.inputs_fingerprint(json.loads(json.dumps(DATA)))
    reordered = json.loads(json.dumps(DATA))
    reordered["fpp"]["inputs"]["neighbours"].reverse()
    assert cc.inputs_fingerprint(reordered) == base                    # the neighbours are sorted by source id
    for path, value in ((("ephemeris", "period_days"), 40.30), (("ephemeris", "t0_bkjd"), 136.4),
                        (("ephemeris", "depth_ppm"), 2000.0), (("ephemeris", "duration_h"), 4.0),
                        (("target", "gmag"), 16.3)):
        changed = json.loads(json.dumps(DATA))
        changed[path[0]][path[1]] = value
        assert cc.inputs_fingerprint(changed) != base, path
    fewer = json.loads(json.dumps(DATA))
    fewer["fpp"]["inputs"]["neighbours"].pop()
    assert cc.inputs_fingerprint(fewer) != base
    brighter = json.loads(json.dumps(DATA))
    brighter["fpp"]["inputs"]["neighbours"][0]["gmag"] = 12.0
    assert cc.inputs_fingerprint(brighter) != base
    noise = json.loads(json.dumps(DATA))
    noise["ephemeris"]["period_days"] = 40.29 + 1e-12                    # below the dossier's 8 significant figures
    assert cc.inputs_fingerprint(noise) == base


def test_export_writes_inputs_lightcurve_and_the_pixel_files(env, tmp_path):
    out = cc.export_inputs(env.store, env.blob, env.settings, STAGE2_TARGET, tmp_path / "cc", fetch=env.xfetch)
    data = _stored(env)
    inputs = json.loads((out / "inputs.json").read_text(encoding="utf-8"))
    assert inputs["fingerprint"] == cc.inputs_fingerprint(data)
    assert inputs["target"]["target_id"] == STAGE2_TARGET and inputs["target"]["ra"] == data["target"]["ra"]
    # the KBonus contamination budget (ruling n) and the KIC Kp check (ruling r; the seed stores no Kp)
    assert inputs["target"]["crowdsap"] == 0.9 and inputs["target"]["psffrac"] == 0.9
    assert inputs["target"]["kepmag"] is None
    assert inputs["ephemeris"] == data["ephemeris"] and "period_err_days" in inputs["ephemeris"]
    assert inputs["star"] == data["star"] and inputs["neighbour_kic"] == NEIGHBOUR_KIC
    stored = data["fpp"]["inputs"]["neighbours"]
    assert [{k: n[k] for k in ("source_id", "sep_arcsec", "gmag", "bp_rp")} for n in inputs["neighbours"]] == [
        {k: n[k] for k in ("source_id", "sep_arcsec", "gmag", "bp_rp")} for n in stored] and stored
    # ruling q: Gaia DR3 GSP-Phot Teff and radius, the mass from the dwarf table's mass-radius relation; the
    # neighbour Gaia has no row for takes all three from its colour
    near, far = inputs["neighbours"]
    assert (near["teff"], near["radius"]) == (3500.0, 0.45) and near["params_source"]["teff"] == "Gaia DR3 GSP-Phot"
    assert near["params_source"]["mass"].startswith("Mamajek dwarf table mass-radius relation")
    tab = mamajek_table()
    assert far["teff"] == pytest.approx(np.interp(1.1, tab[:, 0], tab[:, 1]), abs=1)
    assert far["mass"] == pytest.approx(np.interp(1.1, tab[:, 0], tab[:, 3]), abs=1e-3)
    assert far["params_source"]["radius"].startswith("Mamajek dwarf table at BP-RP 1.100")
    assert inputs["neighbour_params_note"] is None
    assert inputs["leak"] == {"depth_ppm": 14.0, "err_ppm": 31.0, "kic": NEIGHBOUR_KIC}      # a consistency check
    assert inputs["quarters"] == [1, 2, 3, 4, 5]
    assert inputs["tpf_files"] == [f"tpf/q{q:02d}.fits.gz" for q in (1, 2, 3, 4, 5)]
    # every file was kept: the listing is asked (the seed's manifest names other files), nothing is fetched, and the
    # files the cross-check is made from are pinned against budget retention
    assert inputs["tpf_kept"] == [1, 2, 3, 4, 5] and inputs["tpf_fetched"] == [] and inputs["tpf_missing"] == []
    assert env.xfetch.calls == [tpf_listing_url(NEIGHBOUR_KIC)] and all(_art(env, q)["pinned"] for q in range(1, 6))
    for b in env.tpfs:
        assert (out / "tpf" / f"q{b.quarter:02d}.fits.gz").read_bytes() == bytes(b)
    with (out / "lightcurve.csv").open(encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert list(rows[0]) == ["time_bkjd", "flux_rel", "err_rel", "quarter"]
    eph = data["ephemeris"]
    period, t0, dur = eph["period_days"], eph["t0_bkjd"], eph["duration_h"] / 24
    t = np.array([float(r["time_bkjd"]) for r in rows])
    phase = (t - t0 + period / 2) % period - period / 2
    assert np.all(np.abs(phase) <= 3 * dur + 1e-6) and np.abs(phase).min() < dur / 2
    lc = inputs["lightcurve"]
    assert lc["n"] == len(rows) and lc["window_durations"] == 3 and lc["clip_mad"] == 5.0
    assert isinstance(lc["clipped"], int) and lc["clipped"] >= 0 and lc["clip_half_width"] > 0
    assert lc["clipped_in_transit"] + lc["clipped_out_of_transit"] == lc["clipped"] and lc["clipped_in_transit"] >= 0
    from tools import triceratops_check as tc
    assert tc.export_problems(inputs) == []                  # the export carries everything the tool checks for
    flux = np.array([float(r["flux_rel"]) for r in rows])
    assert abs(np.median(flux) - 1.0) < 0.01 and flux.min() < 1 - 0.001          # normalised, the transits inside
    assert {int(r["quarter"]) for r in rows} <= set(range(18)) and all(float(r["err_rel"]) > 0 for r in rows)
    # the parameter query is logged like every other request
    from sqlalchemy import select

    from skysift.store.db import QueryRow
    with env.store.session() as s:
        assert any("n.source_id IN" in (q.params or "") for q in s.scalars(select(QueryRow)))


def test_export_refetches_and_pins_a_pixel_file_retention_dropped(env, tmp_path):
    """A quarter whose artifact row retention marked deleted is fetched again through the engine's pixel-file path,
    stored, logged under the target and pinned; inputs.json names it as fetched fresh."""
    env.store.mark_artifact_deleted(_art(env, 3)["sha256"])
    assert _art(env, 3) is None                   # a resurrected row comes back unpinned, so its pin is the export's
    out = cc.export_inputs(env.store, env.blob, env.settings, STAGE2_TARGET, tmp_path / "cc", fetch=env.xfetch)
    inputs = json.loads((out / "inputs.json").read_text(encoding="utf-8"))
    name3 = tpf_listing_url(NEIGHBOUR_KIC) + tpf_name(NEIGHBOUR_KIC, 3)
    assert inputs["quarters"] == [1, 2, 3, 4, 5] and inputs["tpf_fetched"] == [3]
    assert inputs["tpf_kept"] == [1, 2, 4, 5] and inputs["tpf_missing"] == []
    assert env.xfetch.calls == [tpf_listing_url(NEIGHBOUR_KIC), name3]
    assert all(_art(env, q)["pinned"] for q in range(1, 6))
    assert (out / "tpf" / "q03.fits.gz").read_bytes() == bytes(env.tpfs[2])
    from sqlalchemy import select

    from skysift.store.db import QueryRow
    with env.store.session() as s:
        assert any(q.url == name3 and q.target_id == STAGE2_TARGET for q in s.scalars(select(QueryRow)))


def test_export_refetches_a_kept_pixel_file_whose_file_is_gone(env, tmp_path):
    """A kept row (not deleted) whose file is no longer on disk takes the same path as a deleted one: the state of
    the trial database copy, its rows kept and its files removed."""
    env.blob.delete(_art(env, 4)["local_path"])
    out = cc.export_inputs(env.store, env.blob, env.settings, STAGE2_TARGET, tmp_path / "cc", fetch=env.xfetch)
    inputs = json.loads((out / "inputs.json").read_text(encoding="utf-8"))
    assert inputs["quarters"] == [1, 2, 3, 4, 5] and inputs["tpf_fetched"] == [4] and inputs["tpf_missing"] == []
    assert env.blob.exists(_art(env, 4)["local_path"]) and _art(env, 4)["pinned"]
    assert (out / "tpf" / "q04.fits.gz").read_bytes() == bytes(env.tpfs[3])


def test_a_listing_outage_leaves_the_kept_files_and_names_what_is_missing(env, tmp_path):
    env.blob.delete(_art(env, 4)["local_path"])
    down = export_fetch(env.tpfs, listing_up=False)
    out = cc.export_inputs(env.store, env.blob, env.settings, STAGE2_TARGET, tmp_path / "cc", fetch=down)
    inputs = json.loads((out / "inputs.json").read_text(encoding="utf-8"))
    assert inputs["quarters"] == [1, 2, 3, 5] and inputs["tpf_fetched"] == []
    causes = " ".join(m["cause"] for m in inputs["tpf_missing"])
    assert "FileNotFoundError" in causes and "MAST did not supply the pixel files" in causes and "503" in causes


def test_export_without_gaia_takes_the_neighbours_parameters_from_the_colour(env, tmp_path):
    from skysift.net import ServiceUnavailable

    def down(url, **_kw):
        raise ServiceUnavailable("gaia down")
    out = cc.export_inputs(env.store, env.blob, env.settings, STAGE2_TARGET, tmp_path / "cc", fetch=down)
    inputs = json.loads((out / "inputs.json").read_text(encoding="utf-8"))
    assert "query for the neighbours' parameters failed (ServiceUnavailable: gaia down)" in \
        inputs["neighbour_params_note"]
    assert all(n["params_source"]["teff"].startswith("Mamajek dwarf table at BP-RP") for n in inputs["neighbours"])
    # M8: a query that fails other than as an outage (an HTTP 400 on a schema change) also falls back to the colour

    def refused(url, **_kw):
        raise RuntimeError("HTTP 400 Bad Request")
    out = cc.export_inputs(env.store, env.blob, env.settings, STAGE2_TARGET, tmp_path / "cc400", fetch=refused)
    inputs = json.loads((out / "inputs.json").read_text(encoding="utf-8"))
    assert "failed (RuntimeError: HTTP 400 Bad Request)" in inputs["neighbour_params_note"]
    assert all(n["params_source"]["teff"].startswith("Mamajek dwarf table at BP-RP") for n in inputs["neighbours"])


def test_clip_window_drops_outliers_and_keeps_the_transits():
    rng = np.random.default_rng(4)
    eph = {"period_days": 10.0, "t0_bkjd": 0.5, "duration_h": 4.8, "depth_ppm": 3000.0}
    time = np.linspace(-0.6, 0.6, 2000) + 0.5                      # three durations either side of one transit
    phase = (time - 0.5 + 5.0) % 10.0 - 5.0
    intransit = np.abs(phase) <= 0.1
    flux = 1.0 + rng.normal(0.0, 0.0009, time.size) - np.where(intransit, 0.003, 0.0)
    flux[[100, 900, 1700]] += np.array([0.02, -0.02, 0.015])      # three outliers, one inside the transit
    keep, half = cc.clip_window(time, flux, eph)
    assert not keep[[100, 900, 1700]].any()
    assert keep[intransit].mean() > 0.99                          # the transit itself is not clipped
    assert half == pytest.approx(5.0 * 0.0009 / 1.4826, rel=0.1)  # 5 MAD of the residuals
    no_depth = cc.clip_window(time, flux, {**eph, "depth_ppm": None})
    assert no_depth[0].all() and no_depth[1] is None
    flat = cc.clip_window(time, np.ones(time.size), {**eph, "depth_ppm": 0.0})
    assert flat[0].all() and flat[1] is None                       # no scatter: nothing to clip


def test_neighbour_params_prefers_gaia_and_falls_back_to_the_dwarf_table():
    tab = mamajek_table()
    full = cc.neighbour_params({"teff_gspphot": "5484", "radius_gspphot": "0.68", "mass_flame": "0.9", "bp_rp": "0.92"},
                               0.92)
    assert (full["teff"], full["radius"], full["mass"]) == (5484.0, 0.68, 0.9)
    assert full["source"] == {"teff": "Gaia DR3 GSP-Phot", "radius": "Gaia DR3 GSP-Phot", "mass": "Gaia DR3 FLAME"}
    no_mass = cc.neighbour_params({"teff_gspphot": "5484", "radius_gspphot": "0.68", "mass_flame": ""}, 0.92)
    order = np.argsort(tab[:, 2])
    assert no_mass["mass"] == pytest.approx(np.interp(0.68, tab[order, 2], tab[order, 3]), abs=1e-3)
    colour = cc.neighbour_params(None, 1.1)
    assert colour["teff"] == pytest.approx(np.interp(1.1, tab[:, 0], tab[:, 1]), abs=1)
    assert colour["radius"] == pytest.approx(np.interp(1.1, tab[:, 0], tab[:, 2]), abs=1e-3)
    assert all(s.startswith("Mamajek dwarf table at BP-RP 1.100 (Pecaut") for s in colour["source"].values())
    assert no_mass["mass_radius"] == 0.68 and full["mass_radius"] == 0.68
    # a GSP-Phot radius more than 20 per cent above the dwarf table's at the star's Teff is an evolved star: its mass
    # is left to the package (round 2: against the radius at its Teff, not the table's largest)
    by_teff = np.argsort(tab[:, 1])
    evolved = cc.neighbour_params({"teff_gspphot": "4800", "radius_gspphot": "4.1", "mass_flame": ""}, 1.1)
    assert evolved["radius"] == 4.1 and evolved["teff"] == 4800.0 and evolved["mass"] is None
    dwarf = float(np.interp(4800.0, tab[by_teff, 1], tab[by_teff, 2]))
    assert evolved["source"]["mass"] == (f"Gaia DR3 GSP-Phot radius 4.100 is more than 20% above the dwarf table's "
                                         f"{dwarf:.3f} at Teff 4800 K: evolved; mass left to the package")
    assert evolved["mass_radius"] is None
    subgiant = cc.neighbour_params({"teff_gspphot": "6000", "radius_gspphot": "2.0", "mass_flame": ""}, None)
    assert subgiant["mass"] is None and "evolved" in subgiant["source"]["mass"]        # below 2.49, still evolved
    from tools import triceratops_check as tc
    updates, sources = tc.param_updates({"Teff": 4000.0, "rad": 1.0, "mass": 1.0, "params_estimated": "Teff,rad,mass"},
                                        {**evolved, "params_source": evolved["source"]}, added=False)
    assert updates == [("Teff", 4800.0), ("rad", 4.1)]
    assert sources["mass"].endswith("evolved; mass left to the package: TRICERATOPS estimate from photometry")
    nothing = cc.neighbour_params(None, None)
    assert (nothing["teff"], nothing["radius"], nothing["mass"]) == (None, None, None)


def test_import_validates_and_stores_the_result(env, tmp_path):
    sha = cc.inputs_fingerprint(_stored(env))
    good_runs = fake_result(sha)["runs"]
    bad_run = [dict(good_runs[0], fpp_no_stp=0.5), *good_runs[1:]]
    for bad, why in ((dict(fake_result(sha), fpp=1.5), "fpp"), (dict(fake_result(sha), nfpp=float("nan")), "nfpp"),
                     ({k: v for k, v in fake_result(sha).items() if k != "stars"}, "stars"),
                     (dict(fake_result(sha), tool="vespa"), "tool"),
                     (dict(fake_result(sha), runs=good_runs[:3]), "runs"),
                     (dict(fake_result(sha), fpp=0.2), "non-degenerate runs' value"),
                     (dict(fake_result(sha), fpp_p84=0.3), "fpp_p84"),
                     (dict(fake_result(sha), runs=bad_run), "fpp_no_stp is not"),
                     (dict(fake_result(sha), n_degenerate=2), "n_degenerate"),
                     (fake_result(sha, n=5), "at least 20"),
                     (dict(fake_result(sha), inputs_sha256="abc"), "inputs_sha256"),
                     (dict(fake_result(sha), n_runs=True), "n_runs")):
        with pytest.raises(cc.BadResult, match=why):
            cc.import_result(env.store, STAGE2_TARGET, _write(tmp_path / "bad.json", bad))
    (tmp_path / "broken.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(cc.BadResult, match="not a JSON"):
        cc.import_result(env.store, STAGE2_TARGET, tmp_path / "broken.json")
    with pytest.raises(cc.BadResult, match="cannot be read"):
        cc.import_result(env.store, STAGE2_TARGET, tmp_path / "missing.json")
    assert env.store.dossier(STAGE2_TARGET)["cross_check"] is None               # nothing stored on a bad file
    with pytest.raises(cc.NoDossier):
        cc.import_result(env.store, "kic-000000001", _write(tmp_path / "ok.json", fake_result(sha)))
    good = dict(fake_result(sha), extra="not kept")
    block = cc.import_result(env.store, STAGE2_TARGET, _write(tmp_path / "ok.json", good))
    assert block["current"] is True and set(block) == {*cc.RESULT_KEYS, "current", "class"}
    assert env.store.dossier(STAGE2_TARGET)["cross_check"] == block and block["fpp"] == 0.012
    assert block["class"] == "validated" and cc.verdict(block) == (False, None)


def test_a_hand_made_20_run_file_with_degenerate_runs_is_summarised_over_the_others():
    """Twenty runs, the last two degenerate: the medians and the 16th and 84th percentiles are over the eighteen
    others (FPP 0.01 to 0.18: median 0.095, 16th percentile 0.0372, 84th 0.1528, numpy's linear rule)."""
    runs = []
    for k in range(20):
        fpp, p_stp = (k + 1) / 100, (k + 1) / 200
        runs.append({"seed": 7 + k, "fpp": fpp, "fpp_no_stp": cc.fpp_without_stp(fpp, p_stp), "nfpp": 1e-5 * (k + 1),
                     "p_stp": p_stp, "p_ptp": 0.1, "best_scenario": None, "degenerate": k >= 18})
    runs[18]["fpp"] = runs[19]["fpp"] = 1.0
    runs[18]["fpp_no_stp"] = cc.fpp_without_stp(1.0, runs[18]["p_stp"])
    runs[19]["fpp_no_stp"] = cc.fpp_without_stp(1.0, runs[19]["p_stp"])
    s = cc.summarise(runs)
    assert (s["fpp"], s["fpp_p16"], s["fpp_p84"]) == pytest.approx((0.095, 0.0372, 0.1528))
    assert s["p_stp"] == pytest.approx(0.0475) and s["p_ptp"] == pytest.approx(0.1) == s["p_ptp_p84"]
    assert s["nfpp"] == pytest.approx(9.5e-5)
    assert cc.fpp_without_stp(0.046, 0.0455) == pytest.approx(0.0005 / 0.9545)        # the smoke run: about 0.05 %
    assert cc.fpp_without_stp(1.0, 1.0) is None and cc.fpp_without_stp(0.01, 0.02) == 0.0
    result = {**fake_result("c" * 64), "runs": runs, "n_degenerate": 2, **s}
    cc.validate_result(result)
    with pytest.raises(cc.BadResult, match="every run was degenerate"):
        every = [dict(r, degenerate=True) for r in runs]
        cc.validate_result({**result, "runs": every, "n_degenerate": 20, **cc.summarise(every)})


def test_a_cross_check_against_another_fingerprint_is_not_current(env, tmp_path):
    out = cc.export_inputs(env.store, env.blob, env.settings, STAGE2_TARGET, tmp_path / "cc", fetch=env.xfetch)
    sha = json.loads((out / "inputs.json").read_text(encoding="utf-8"))["fingerprint"]
    data = _stored(env)
    data["ephemeris"]["period_days"] += 0.01                                     # the stored dossier moved on
    Path(env.store.dossier(STAGE2_TARGET)["data_path"]).write_text(json.dumps(data), encoding="utf-8")
    block = cc.import_result(env.store, STAGE2_TARGET, _write(tmp_path / "r.json", fake_result(sha)))
    assert block["current"] is False and env.store.dossier(STAGE2_TARGET)["cross_check"]["current"] is False
    assert cc.verdict(block) == (True, "the cross-check was made for an earlier dossier")
    assert cc.with_current(block, data)["current"] is False
    data["ephemeris"]["period_days"] -= 0.01
    assert cc.with_current(block, data)["current"] is True                        # recomputed, never trusted
    assert cc.with_current(None, data) is None


def test_classes_are_giacalone_labels():
    assert [cc.classify(f, n) for f, n in ((0.01, 1e-4), (0.015, 1e-4), (0.3, 9e-4), (0.3, 1e-3), (0.6, 0.2),
                                           (0.01, 0.11), (0.6, 1e-4), (0.2, 0.05))] == [
        "validated", "likely planet", "likely planet", "candidate", "likely nearby false positive",
        "likely nearby false positive", "candidate", "candidate"]
    assert cc.classify(None, 0.1) is None
    assert not hasattr(cc, "LIKELY_FP_FPP")                       # the "likely false positive" class is withdrawn


def test_the_gate_blocks_only_a_missing_stale_or_old_cross_check():
    sha = "a" * 64
    block = {**fake_result(sha), "current": True}
    assert cc.verdict(None) == (True, "no TRICERATOPS cross-check")
    assert cc.verdict({**block, "current": False}) == (True, "the cross-check was made for an earlier dossier")
    old = {"tool": cc.TOOL, "fpp": 0.01, "nfpp": 1e-4, "fpp_runs": [0.01], "current": True}
    assert cc.verdict(old) == (True, "the cross-check was stored by an earlier version of the tool; run it again")
    assert cc.with_current(old, DATA)["class"] is None and cc.comparison_sentence({"cross_check": old}) is None
    # the numbers never block (ruling t): a median full FPP of 0.6 or an NFPP of 0.2 is a label and a sentence
    for fpp, nfpp in ((0.6, 1e-4), (0.01, 0.2), (0.99, 0.5)):
        assert cc.verdict({**fake_result(sha, fpp=fpp, nfpp=nfpp), "current": True}) == (False, None)


def test_comparison_sentence_prints_both_fpps_with_their_ranges_and_no_class():
    sha = "a" * 64
    runs = fake_runs(fpp=0.046, nfpp=1e-4, p_stp=0.0455)
    runs[0] = dict(runs[0], fpp=0.017, fpp_no_stp=cc.fpp_without_stp(0.017, 0.0455), p_stp=0.0455)
    runs[1] = dict(runs[1], fpp=0.092, fpp_no_stp=cc.fpp_without_stp(0.092, 0.0455), nfpp=2.5e-3)
    block = {**fake_result(sha), "runs": runs, **cc.summarise(runs), "current": True}
    data = {"fpp": {"value": 0.0035}, "cross_check": block}
    text = cc.comparison_sentence(data)
    assert text.startswith("TRICERATOPS 1.1.0 (Giacalone et al. 2021), run on the KBonus light curve with Kepler limb "
                           "darkening and Gaia DR3 field stars, gives a median false-positive probability of 0.046 "
                           "(16th to 84th percentile 0.046 to 0.046)")
    assert "1 in 1,900 (1 in 1,900 to 1 in 1,900) without a planet transiting an unresolved bound companion (STP)" in text
    assert "nearby false-positive probability of 1 in 10,000" in text and "over 20 runs," in text
    # spec 11.2: no class on Giacalone et al.'s scale and no "different questions" tail; SkySift's own estimate is
    # the in-house sentence's, not this one's
    assert text.endswith(f"over 20 runs, {cc.NO_CLASS_CLAUSE}.")
    assert "the medians" not in text and "SkySift's own" not in text and "different questions" not in text
    assert "e-0" not in text and "e-0" not in cc.comparison_sentence(data, tex=True)
    assert chr(0x2014) not in text and "discover" not in text.lower() and "validated" not in text
    assert [cc.probability_text(x) for x in (0.012, 0.0035, 1.2e-4, 1e-4, 0.0)] == [
        "0.012", "0.0035", "1 in 8,300", "1 in 10,000", "0"]
    tex = cc.comparison_sentence(data, tex=True)
    assert r"TRICERATOPS 1.1.0 \citep{giacalone2021}, run on the KBonus light curve with" in tex
    assert "Giacalone et al. 2021" not in tex and "Martinez-Palomera" not in tex
    assert cc.comparison_sentence({"fpp": {"value": 0.0035}, "cross_check": None}) is None
    assert cc.comparison_sentence({"fpp": {"value": 0.0035}, "cross_check": {**block, "current": False}}) is None
    # a degenerate run is left out and counted; medians inside the validation lines are not called validated
    deg = [*runs[:19], dict(runs[19], degenerate=True)]
    v = cc.comparison_sentence({"cross_check": {**block, "runs": deg, "n_degenerate": 1, **cc.summarise(deg)}})
    assert "over 20 runs, 1 degenerate run left out, " in v
    tiny = fake_runs(fpp=0.001, nfpp=1e-6)
    low = cc.comparison_sentence({"cross_check": {**block, "runs": tiny, **cc.summarise(tiny)}})
    assert cc.NO_CLASS_CLAUSE in low and "validation lines" not in low and "validated" not in low


def test_the_headline_leads_with_both_triceratops_numbers():
    sha = "a" * 64
    block = {**fake_result(sha, fpp=0.046, p_stp=0.0455), "current": True}
    data = {"fpp": {"value": 0.0035}, "cross_check": block}
    assert cc.headline(data) == (f"TRICERATOPS 1.1.0 gives a false-positive probability of 0.046 "
                                 f"({cc.probability_text(block['fpp_no_stp'])} without a planet on an unresolved bound "
                                 "companion), in none of its validation classes, which do not apply at 30-minute "
                                 "cadence (Giacalone et al. 2021)")
    assert str(cc.headline(data, tex=True)).endswith(r"at 30-minute cadence \citep{giacalone2021}")
    assert cc.headline({"cross_check": {**block, "current": False}}) is None and cc.headline({}) is None


def test_the_export_writes_the_pixel_exclusion_radius(env, tmp_path):
    out = cc.export_inputs(env.store, env.blob, env.settings, STAGE2_TARGET, tmp_path / "cc", fetch=env.xfetch)
    inputs = json.loads((out / "inputs.json").read_text(encoding="utf-8"))
    co = _stored(env)["centroid_offset"]
    assert inputs["pixel_exclusion"] == {"radius_arcsec": co["exclusion_radius_arcsec"], "rule": EXCLUSION_RULE,
                                         "centre_offset_arcsec": co["catalogue_referenced_arcsec"]}


def test_the_comparison_sentence_names_no_class_and_no_different_questions(env):
    data = {**_stored(env), "cross_check": {**fake_result(cc.inputs_fingerprint(_stored(env))), "current": True}}
    sentence = cc.comparison_sentence(data)
    assert cc.NO_CLASS_CLAUSE in sentence and "answer different questions" not in sentence
    assert "SkySift's own estimate" not in sentence and not hasattr(cc, "DIFFERENT_QUESTIONS")


def test_a_full_fpp_at_or_above_one_half_names_its_dominant_scenario():
    sha = "a" * 64
    scen = [{"ID": "169822650", "scenario": "TP", "prob": 0.3}, {"ID": "169822650", "scenario": "STP", "prob": 0.1},
            {"ID": "120686466", "scenario": "NEB", "prob": 0.55}, {"ID": "169822650", "scenario": "PTP", "prob": 0.05}]
    block = {**fake_result(sha, fpp=0.65, nfpp=0.55, scenarios=scen), "current": True}
    text = cc.comparison_sentence({"fpp": {"value": 0.0016}, "cross_check": block})
    assert ("Its full false-positive probability is at or above 0.5 mainly through an eclipsing binary on a nearby "
            "star (TIC 120686466), the NEB scenario, at a probability of 0.55 in the median run.") in text
    assert cc.NO_CLASS_CLAUSE in text and "likely nearby false positive" not in text
    flag = cc.owner_flag({"cross_check": block})                # M1: never the median alone (ruling s)
    assert flag.startswith("TRICERATOPS puts the median full false-positive probability at 0.65 (16th to 84th "
                           "percentile 0.65 to 0.65).")
    assert "does not block the draft" in flag
    assert cc.owner_flag({"cross_check": {**fake_result(sha), "current": True}}) is None
    assert cc.owner_flag({"cross_check": {**block, "current": False}}) is None
    stp = [{"ID": "1", "scenario": "TP", "prob": 0.4}, {"ID": "1", "scenario": "STP", "prob": 0.6}]
    bound = cc.comparison_sentence({"cross_check": {**fake_result(sha, fpp=0.6, scenarios=stp), "current": True}})
    assert "mainly through a planet transiting an unresolved bound companion, the STP scenario, at a probability " \
           "of 0.60" in bound


# ---------------------------------------------------------------------------------------------------- the tool

def test_tool_helpers_match_neighbours_and_estimate_tmag(tmp_path):
    from tools import triceratops_check as tc
    tic = [{"ID": "1879962333", "sep (arcsec)": 7.2, "GAIAmag": 19.6},
           {"ID": "120686466", "sep (arcsec)": 10.4, "GAIAmag": 12.9}]
    gaia = [{"source_id": "21", "sep_arcsec": 7.14, "gmag": 19.77, "bp_rp": 1.8},
            {"source_id": "22", "sep_arcsec": 15.0, "gmag": 18.0, "bp_rp": None}]
    matched, missing = tc.match_neighbours(tic, gaia)
    assert matched == [{"source_id": "21", "tic_id": "1879962333", "dsep_arcsec": pytest.approx(0.06)}]
    assert missing == [gaia[1]]
    assert tc.match_neighbours(tic, gaia, arcsec=0.01) == ([], gaia)
    assert tc.tmag_from_g(19.77, 1.8) == pytest.approx(19.27) and tc.tmag_from_g(18.0, None) == pytest.approx(17.7)
    assert tc.tmag_from_g(16.0, 1.5) == pytest.approx(15.7)
    # a TIC star without a G ranks last; a dossier neighbour without a separation or G is left out and named
    nan_g = [{"ID": "9", "sep (arcsec)": 7.2, "GAIAmag": math.nan}, *tic]
    assert tc.match_neighbours(nan_g, gaia[:1])[0][0]["tic_id"] == "1879962333"
    broken = [{"source_id": "23", "sep_arcsec": None, "gmag": 17.0, "bp_rp": None},
              {"source_id": "24", "sep_arcsec": 9.0, "gmag": math.nan, "bp_rp": None}]
    assert tc.match_neighbours(tic, [*gaia, *broken]) == (matched, missing)
    assert tc.usable_neighbours([*gaia, *broken]) == (gaia, broken)
    # the quarter images of the synthetic pixel files: padded to 22 x 22, the stars placed through each quarter's WCS
    tpfs = tpf_quarters(n_quarters=2)
    paths = []
    for b in tpfs:
        p = tmp_path / f"q{b.quarter:02d}.fits.gz"
        p.write_bytes(bytes(b))
        paths.append(p)
    images = tc.quarter_images(list(reversed(paths)))
    assert [q["quarter"] for q in images] == [1, 2]
    for q, b in zip(images, tpfs, strict=True):
        assert q["image"].shape == (22, 22) and np.isnan(q["image"][0, 0])
        pc, pr = q["pad"]
        assert (q["col0"], q["row0"]) == (CCD_ORIGIN[0] - pc, CCD_ORIGIN[1] - pr)     # 1CRV4P, 2CRV4P less the pad
        ra, dec = zip(b.target_radec, b.neighbour_radec, strict=True)
        coords = tc.pixel_coords(q, [*ra, math.nan], [*dec, math.nan])
        np.testing.assert_allclose(coords[0], np.add(CCD_ORIGIN, b.pos_target), atol=1e-6)     # 1CRV4P + WCS
        np.testing.assert_allclose(coords[1], np.add(CCD_ORIGIN, b.pos_neighbour), atol=1e-6)
        np.testing.assert_allclose(coords[2], coords[0])              # a star without a position sits on the target
        with fits.open(paths[q["quarter"] - 1]) as h:
            mean = np.asarray(h[1].data["FLUX"], float).mean(axis=0)
        np.testing.assert_allclose(q["image"][pr:pr + 9, pc:pc + 9], mean, rtol=1e-6)    # the stamp, unmoved
        c, r = round(CCD_ORIGIN[0] + b.pos_target[0]), round(CCD_ORIGIN[1] + b.pos_target[1])
        ap = tc.aperture_3x3(coords[0])
        assert ap.shape == (9, 2) and {tuple(x) for x in ap} == {(c + i, r + j) for i in (-1, 0, 1) for j in (-1, 0, 1)}
    b = tpfs[0]          # a file without the FLUX column's physical WCS cannot place the stars on the CCD grid
    bare = tmp_path / "bare.fits"
    from astropy.wcs import WCS
    with fits.open(paths[0]) as h:
        cube, wcs = np.asarray(h[1].data["FLUX"], float), WCS(h[2].header, naxis=2)
        bare.write_bytes(tpf_fits(1, h[1].data["TIME"], h[1].data["QUALITY"], cube, wcs, h[2].data, NEIGHBOUR_KIC,
                                  b.neighbour_radec, gz=False, ccd_origin=None))
    with pytest.raises(ValueError, match="no 1CRV4P or 2CRV4P"):
        tc.quarter_images([bare])


def test_tool_leakage_shares_two_stars_by_hand():
    """KIC 6146676 and KIC 6146666 (ruling n as amended after the Task 9 review): CROWDSAP 0.8565 is the target's
    fraction of the aperture light, so the neighbours' light over the target's is B = 0.1435 / 0.8565 = 0.1675, all
    of it the one neighbour's; the target's flux ratio is CROWDSAP itself, the neighbour's 1 - CROWDSAP, and the depth
    the neighbour would need is the on-target depth over B."""
    from tools import triceratops_check as tc
    stars = [{"ID": "169822650", "Tmag": 15.19, "fluxratio": 0.616},
             {"ID": "169822655", "Tmag": 12.39, "fluxratio": 0.384}]
    out = tc.leakage_shares(stars, 0.8565, [16.24, 12.81], 0.0021653)
    assert out["source"] == "contamination budget" and out["budget"] == pytest.approx(0.167542, abs=1e-6)
    assert out["contaminating_fraction"] == pytest.approx(0.1435) and out["crowdsap"] == 0.8565
    t, n = out["rows"]
    assert t["fluxratio"] == pytest.approx(0.8565) and n["fluxratio"] == pytest.approx(0.1435)
    assert t["fluxratio"] + n["fluxratio"] == pytest.approx(1.0)
    assert n["fluxratio"] / t["fluxratio"] == pytest.approx(out["budget"])        # L F / F_t, the ratio to the target
    assert t["tdepth"] == pytest.approx(0.0021653) and n["tdepth"] == pytest.approx(0.012924, abs=1e-6)
    assert n["share"] == pytest.approx(0.167542, abs=1e-6) and t["share"] is None
    assert n["kepler_flux"] == pytest.approx(10 ** (0.4 * (16.24 - 12.81))) and t["kepler_flux"] == 1.0
    assert n["g_package"] == 0.384 and n["band"] == "Gaia G"
    # the package's box fractions re-weighted into the Kepler band: G - T is 1.05 for the target, 0.42 for the
    # neighbour, so the neighbour's fraction grows by 10^(0.4 x 0.63)
    w = 0.384 * 10 ** (0.4 * 0.63)
    assert n["g"] == pytest.approx(w / (w + 0.616)) and t["g"] == pytest.approx(0.616 / (w + 0.616))
    assert all(r["source"] == "contamination budget" for r in out["rows"])


def test_tool_leakage_shares_split_the_budget_in_the_kepler_band_and_fall_back_to_the_box():
    from tools import triceratops_check as tc
    stars = [{"ID": "t", "Tmag": 15.0, "fluxratio": 0.6}, {"ID": "a", "Tmag": 13.0, "fluxratio": 0.3},
             {"ID": "b", "Tmag": 16.0, "fluxratio": 0.1}, {"ID": "c", "Tmag": 18.0, "fluxratio": 0.0}]
    out = tc.leakage_shares(stars, 0.75, [16.0, 13.5, None, 18.5], 0.002)
    _target, a, b, c = out["rows"]
    # "b" has no G, so it keeps the target's G - T offset; "c" sits outside the box and gets nothing
    assert b["band"] == "Tmag" and b["kepler_flux"] is None and c["share"] == 0.0 and c["tdepth"] == 0.0
    ga, gb = 0.3 * 10 ** (-0.4 * (0.5 - 1.0)), 0.1
    budget = 0.25 / 0.75                                     # (1 - C) / C
    assert out["budget"] == pytest.approx(budget) and out["rows"][0]["fluxratio"] == 0.75
    assert a["share"] == pytest.approx(budget * ga / (ga + gb)) and b["share"] == pytest.approx(budget * gb / (ga + gb))
    assert a["share"] + b["share"] == pytest.approx(budget)
    assert a["fluxratio"] == pytest.approx(0.25 * ga / (ga + gb))
    assert sum(r["fluxratio"] for r in out["rows"]) == pytest.approx(1.0)
    assert a["tdepth"] == pytest.approx(0.002 / a["share"]) and b["tdepth"] == pytest.approx(0.002 / b["share"])
    deep = tc.leakage_shares(stars, 0.9999, [16.0, 13.5, None, 18.5], 0.002)
    assert deep["rows"][2]["tdepth"] == 0.0 and deep["rows"][1]["tdepth"] == 0.0       # above 1: no such eclipse
    for crowdsap in (None, 1.2, 0.0, math.nan):              # no budget: the package's Gaussian box stands
        box = tc.leakage_shares(stars, crowdsap, [16.0, 13.5, None, 18.5], 0.002)
        assert box["source"] == "package Gaussian box" and box["budget"] is None
        assert [r["fluxratio"] for r in box["rows"]] == [0.6, 0.3, 0.1, 0.0]
        assert box["rows"][1]["tdepth"] == pytest.approx(0.002 * 0.6 / 0.3) and box["rows"][3]["tdepth"] == 0.0
    clean = tc.leakage_shares(stars, 1.0, [16.0, 13.5, None, 18.5], 0.002)
    assert clean["budget"] == 0.0 and all(r["tdepth"] == 0.0 for r in clean["rows"][1:])


def test_tool_param_updates_fill_only_what_the_tic_lacks():
    from tools import triceratops_check as tc
    given = {"teff": 5484.0, "radius": 0.677, "mass": 0.8,
             "params_source": {"teff": "Gaia DR3 GSP-Phot", "radius": "Gaia DR3 GSP-Phot", "mass": "table"}}
    updates, sources = tc.param_updates({"Teff": 5400.0, "rad": 0.9, "mass": 1.0, "params_estimated": "rad,mass"},
                                        given, added=False)
    assert updates == [("rad", 0.677), ("mass", 0.8)]
    assert sources == {"Teff": "TIC", "rad": "Gaia DR3 GSP-Phot", "mass": "table"}
    updates, sources = tc.param_updates({"Teff": 3700.0, "rad": 0.5, "mass": 0.5, "params_estimated": ""}, given,
                                        added=False)
    assert updates == [] and set(sources.values()) == {"TIC"}
    updates, sources = tc.param_updates({"Teff": 4000.0, "rad": 0.6, "mass": 0.6, "params_estimated": "Teff"}, None,
                                        added=False)
    assert updates == [] and sources["Teff"] == "TRICERATOPS estimate from photometry" and sources["rad"] == "TIC"
    updates, sources = tc.param_updates({"Tmag": 19.0, "params_estimated": math.nan}, given, added=True)
    assert updates == [("Teff", 5484.0), ("rad", 0.677), ("mass", 0.8)]
    updates, sources = tc.param_updates({"Tmag": 19.0}, None, added=True)
    assert updates == [] and set(sources.values()) == {"none (the package assumes solar values)"}


def test_tool_runs_are_recorded_summarised_and_pass_the_import_checks():
    from tools import triceratops_check as tc
    scen = [{"ID": "169822650", "scenario": "TP", "prob": 0.735}, {"ID": "169822650", "scenario": "PTP", "prob": 0.177},
            {"ID": "169822650", "scenario": "STP", "prob": 0.0455}, {"ID": "169822650", "scenario": "DTP", "prob": 0.042},
            {"ID": "169822655", "scenario": "NEB", "prob": 5e-4}]
    r = tc.run_record(3, 0.046, 1.9e-9, False, scen)
    assert r["p_stp"] == pytest.approx(0.0455) and r["p_ptp"] == pytest.approx(0.177)
    assert r["fpp_no_stp"] == pytest.approx(0.0005 / 0.9545) and r["seed"] == 3
    assert r["best_scenario"] == {"id": "169822650", "scenario": "TP", "prob": 0.735}
    assert tc.run_record(1, -1e-17, 0.0, False, [])["fpp"] == 0.0            # rounding below 0 is clipped
    runs = [tc.run_record(7 + k, f, f / 10, k == 4, scen) for k, f in enumerate(np.linspace(0.01, 0.2, 20))]
    settings = {"N": 1_000_000, "notes": ["a note"], "parallel": True, "allow_old_export": False,
                "field_stars": {"n_rows": 10, "sha256": "c" * 64}}
    result = tc.assemble_result(runs, [{"id": "1", "tmag": 15.2}], settings, {"fingerprint": "b" * 64}, "1.1.0")
    assert tuple(result) == cc.RESULT_KEYS and result["tool"] == cc.TOOL and result["n_degenerate"] == 1
    good = [x for x in runs if not x["degenerate"]]
    assert result["fpp"] == pytest.approx(float(np.median([x["fpp"] for x in good])))
    assert result["fpp_p16"] < result["fpp"] < result["fpp_p84"]
    assert result["settings"]["seeds"] == list(range(7, 27)) and "notes" not in result["settings"]
    assert result["scenarios"] == scen and not runs[[x["seed"] for x in runs].index(
        result["settings"]["median_run_seed"])]["degenerate"]
    assert "a note" in result["caveats"] and "1 of 20 runs had degenerate scenario evidences" in " ".join(
        result["caveats"])
    caveats = " ".join(result["caveats"])
    assert "Kepler band" in caveats and "Gaia G" in caveats and "Kepler table" in caveats and "TESS band" in caveats
    assert "2.2 arcsec" in caveats and "section 4.2" in caveats and "no candidate is called validated" in caveats
    assert "Gaia DR3 field-star population" in caveats and "TESS-band limb darkening" not in caveats
    assert any("separation from the target only" in c for c in tc.CAVEATS)
    cc.validate_result(result)                                         # the tool's file passes the import's checks
    assert tc.summarise(runs) == cc.summarise(runs)                    # the copy in the tool agrees with the engine


def test_tool_parallel_check_and_caveats():
    from tools import triceratops_check as tc
    # the smoke of 22 October 2026: the FPPs equal, the NFPPs 20 percent apart at 20,000 draws, which is noise
    seeds = [2.849e-6, 3.6e-6, 2.2e-6, 3.1e-6]
    rec = tc.parallel_check_record(1, 20_000, {"fpp": 0.11471, "nfpp": 3.421e-6}, {"fpp": 0.11468, "nfpp": 2.849e-6},
                                   seeds)
    assert (rec["fpp_serial"], rec["fpp_parallel"], rec["nfpp_serial"], rec["nfpp_parallel"], rec["N"]) == (
        0.11471, 0.11468, 3.421e-6, 2.849e-6, 20_000)
    assert not any(isinstance(v, bool) for v in rec.values())                 # no pass flag: values and words
    assert rec["nfpp_noise_sd"] == pytest.approx(float(np.std(seeds, ddof=1)))
    assert rec["nfpp_difference_in_sd"] == pytest.approx(0.572e-6 / rec["nfpp_noise_sd"])
    assert "are equal to 3 decimals" in rec["comparison"] and "so the gap is within that noise" in rec["comparison"]
    assert "seed-to-seed scatter of the parallel NFPP over 4 seeds at 20,000 draws" in rec["comparison"]
    far = tc.parallel_check_record(1, 10, {"fpp": 0.05, "nfpp": 1e-3}, {"fpp": 0.07, "nfpp": 1e-4}, [1e-4, 1.1e-4])
    assert "are not equal to 3 decimals" in far["comparison"] and "beyond that noise" in far["comparison"]
    one = tc.parallel_check_record(1, 10, {"fpp": 0.05, "nfpp": 1e-4}, {"fpp": 0.05, "nfpp": 1e-4}, [1e-4])
    assert one["nfpp_noise_sd"] is None and "cannot be set against" in one["comparison"]
    text = tc.leakage_caveat({"source": "contamination budget", "budget": 0.1675}, 0.8565)
    assert "(1 - CROWDSAP) / CROWDSAP = 0.1675" in text and "upper bound" in text and "Gaia G" in text
    assert "the target's fraction of the aperture light" in text and "flux ratio is CROWDSAP" in text
    # M2: the TESS-band dilution has a direction each way
    assert "overstates its light" in tc.CAVEATS[0] and "understates theirs" in tc.CAVEATS[0]
    assert "aperture photometry" in tc.leakage_caveat({"source": "package Gaussian box", "budget": None}, None)
    leak = tc.leak_caveat({"depth_ppm": 14.0, "err_ppm": 31.0, "kic": 6146666})
    assert "14 +- 31 ppm" in leak and "KIC 6146666" in leak and "consistency check" in leak
    assert tc.leak_caveat(None) is None
    assert "3 points" in tc.clip_caveat({"clipped": 3, "clip_mad": 5.0}) and tc.clip_caveat({}) is None
    clip = tc.clip_caveat({"clipped": 3, "clip_mad": 5.0, "clipped_in_transit": 1, "clipped_out_of_transit": 2})
    assert "5 raw median absolute deviations (about 3.4 sigma)" in clip and "(1 in transit, 2 out of transit)" in clip


def test_tool_module_imports_without_triceratops(tmp_path, monkeypatch, capsys):
    tc = importlib.import_module("tools.triceratops_check")
    assert "uv venv .venv-triceratops --python 3.12" in tc.__doc__ and "triceratops==1.1.0" in tc.__doc__
    monkeypatch.setattr(tc, "tr", None)
    assert tc.main(["--inputs", str(tmp_path), "--out", str(tmp_path / "r.json")]) == 2
    assert ".venv-triceratops" in capsys.readouterr().err and not (tmp_path / "r.json").exists()


def test_tool_refuses_a_target_row_without_stellar_parameters():
    from tools import triceratops_check as tc
    full = {"mass": 0.49, "rad": 0.49, "Teff": 3694.0, "plx": 3.33}
    assert tc.missing_stellar(full) == []
    assert tc.missing_stellar({**full, "plx": math.nan, "Teff": None}) == ["Teff", "plx"]
    assert tc.missing_stellar({"mass": 0.5}) == ["rad", "Teff", "plx"]


def test_an_export_from_a_dossier_below_the_current_version_is_refused(env, tmp_path):
    """A version-2 dossier has no ephemeris errors and other P, T0, T14 and depth keys, so a cross-check made from it
    could never be current: the export refuses it before writing anything (I1)."""
    path = Path(env.store.dossier(STAGE2_TARGET)["data_path"])
    path.write_text(json.dumps({**_stored(env), "dossier_version": "2"}), encoding="utf-8")
    from skysift.dossier import DOSSIER_VERSION
    with pytest.raises(cc.OldDossier, match=f"dossier version 2 is below {DOSSIER_VERSION}; regenerate first"):
        cc.export_inputs(env.store, env.blob, env.settings, STAGE2_TARGET, tmp_path / "cc", fetch=env.xfetch)
    assert not (tmp_path / "cc").exists()
    from typer.testing import CliRunner

    from skysift.cli import app
    r = CliRunner().invoke(app, ["crosscheck", "--target", STAGE2_TARGET, "--export", str(tmp_path / "cc")])
    assert r.exit_code == 2 and "regenerate first" in r.output and "Traceback" not in r.output


def test_tool_flux_error_is_the_robust_out_of_transit_scatter():
    """I2: 1.4826 x MAD of the flux more than one duration from mid-transit, every point when fewer than 50 lie
    out of transit; the caveat names the choice and both numbers."""
    from tools import triceratops_check as tc
    rng = np.random.default_rng(1)
    period, t0, dur = 10.0, 0.5, 0.2
    time = np.linspace(0.0, 100.0, 20_000)
    phase = (time - t0 + period / 2) % period - period / 2
    flux = 1.0 + rng.normal(0.0, 0.001, time.size) - np.where(np.abs(phase) < dur / 2, 0.01, 0.0)
    err, which = tc.robust_flux_err(time, flux, period, t0, dur)
    assert err == pytest.approx(0.001, rel=0.05) and which.startswith("out of transit")
    near = np.abs(phase) < 1.2 * dur                         # nearly all in or around transit: every point
    err_all, which_all = tc.robust_flux_err(time[near][:60], flux[near][:60], period, t0, dur)
    assert which_all.startswith("every point") and err_all > 0
    text = tc.flux_err_caveat(0.00092, 0.00072, which)
    assert "1.4826 times the median absolute deviation" in text and "0.000920" in text and "0.000720" in text


def test_tool_reports_an_unreadable_export_without_a_traceback(tmp_path, monkeypatch, capsys):
    from tools import triceratops_check as tc
    monkeypatch.setattr(tc, "tr", object())                 # as if TRICERATOPS were installed
    assert tc.main(["--inputs", str(tmp_path), "--out", str(tmp_path / "r.json")]) == 1      # no inputs.json
    assert "cannot be read" in capsys.readouterr().err
    (tmp_path / "inputs.json").write_text(json.dumps({"lightcurve": {"file": "lc.csv"}, "tpf_files": ["tpf/q01.fits"]}),
                                          encoding="utf-8")
    (tmp_path / "lc.csv").write_text("time_bkjd,flux_rel,err_rel,quarter\n1,1,0.001,1\n", encoding="utf-8")
    hdus = fits.HDUList([fits.PrimaryHDU(header=fits.Header({"QUARTER": 1})),
                         fits.BinTableHDU.from_columns([fits.Column(name="FLUX", format="4E", dim="(2,2)",
                                                                    array=np.ones((3, 2, 2), np.float32))]),
                         fits.ImageHDU(np.ones((2, 2), np.int32))])
    (tmp_path / "tpf").mkdir()
    hdus.writeto(tmp_path / "tpf" / "q01.fits")             # no 1CRV4P: quarter_images raises ValueError
    assert tc.main(["--inputs", str(tmp_path), "--out", str(tmp_path / "r.json")]) == 1
    err = capsys.readouterr().err
    assert "ValueError" in err and "1CRV4P" in err and not (tmp_path / "r.json").exists()


def test_an_export_without_pixel_files_is_refused(settings, monkeypatch, tmp_path):
    """No file kept and the MAST listing down: tpf_missing names the outage, nothing is written but the light curve,
    and the CLI exits 2."""
    env = _make_env(settings, monkeypatch)                  # the seed keeps no pixel file for this star
    store_pixel_files(env)
    for q in range(1, 6):
        env.store.mark_artifact_deleted(_art(env, q)["sha256"])
    generate(env.store, env.blob, env.settings, STAGE2_TARGET, fetch=env.fetch)
    down = export_fetch(tpf_quarters(), listing_up=False)
    with pytest.raises(cc.NoPixelFiles, match="tpf_missing: .*MAST did not supply the pixel files.*HTTP 503"):
        cc.export_inputs(env.store, env.blob, env.settings, STAGE2_TARGET, tmp_path / "cc", fetch=down)
    assert not (tmp_path / "cc" / "inputs.json").exists()
    from typer.testing import CliRunner

    from skysift import net
    from skysift.cli import app
    monkeypatch.setattr(net, "fetch", down)
    r = CliRunner().invoke(app, ["crosscheck", "--target", STAGE2_TARGET, "--export", str(tmp_path / "cli")])
    assert r.exit_code == 2 and "MAST did not supply" in r.output and "Traceback" not in r.output
    gone = export_fetch([])                                  # MAST lists none either
    with pytest.raises(cc.NoPixelFiles, match="none is kept and MAST lists none"):
        cc.export_inputs(env.store, env.blob, env.settings, STAGE2_TARGET, tmp_path / "cc2", fetch=gone)


def test_tool_an_added_star_takes_its_share_at_its_stored_separation():
    """M7: a neighbour the TIC lacks is placed on the target's pixel, so its box fraction is a star's at 0 arcsec;
    weighted by the Gaussian box fraction at its stored separation, one at 7 arcsec takes less of the budget."""
    from tools import triceratops_check as tc
    # d = 7 / 4 pixels, sigma 0.75: [Phi(-0.333) - Phi(-4.333)] / [Phi(2) - Phi(-2)] = 0.36944 / 0.95450
    assert tc.offset_box_factor(7.0) == pytest.approx(0.38705, abs=1e-4)
    assert tc.offset_box_factor(0.0) == pytest.approx(1.0) and tc.offset_box_factor(None) == 1.0
    stars = [{"ID": "t", "Tmag": 15.0, "fluxratio": 0.5}, {"ID": "tic", "Tmag": 14.0, "fluxratio": 0.2},
             {"ID": "added", "Tmag": 17.0, "fluxratio": 0.3}]
    budget = 0.2 / 0.8
    at_zero = tc.leakage_shares(stars, 0.8, [None] * 3, 0.002, box_factors=[None, None, tc.offset_box_factor(0.0)])
    at_seven = tc.leakage_shares(stars, 0.8, [None] * 3, 0.002, box_factors=[None, None, tc.offset_box_factor(7.0)])
    assert at_zero["rows"][2]["share"] == pytest.approx(budget * 0.3 / 0.5)
    f = tc.offset_box_factor(7.0)
    assert at_seven["rows"][2]["share"] == pytest.approx(budget * 0.3 * f / (0.2 + 0.3 * f))
    assert at_seven["rows"][2]["share"] < at_zero["rows"][2]["share"] and at_seven["rows"][2]["box_factor"] == f
    assert at_seven["rows"][1]["share"] + at_seven["rows"][2]["share"] == pytest.approx(budget)


def _old_export(path: Path) -> None:
    """An export as it was before the revision of 22 October 2026: no CROWDSAP, PSFFRAC, parameter sources or clip
    count, with one readable pixel file."""
    (path / "lc.csv").write_text("time_bkjd,flux_rel,err_rel,quarter\n1,1,0.001,1\n", encoding="utf-8")
    table = fits.BinTableHDU.from_columns([fits.Column(name="FLUX", format="4E", dim="(2,2)",
                                                       array=np.ones((3, 2, 2), np.float32))])
    table.header["1CRV4P"], table.header["2CRV4P"] = 100, 200
    (path / "tpf").mkdir()
    fits.HDUList([fits.PrimaryHDU(header=fits.Header({"QUARTER": 1})), table,
                  fits.ImageHDU(np.ones((2, 2), np.int32))]).writeto(path / "tpf" / "q01.fits")
    inputs = {"target": {"ra": 297.9, "dec": 41.5, "gmag": 16.2, "kic": 6146676},
              "ephemeris": {"period_days": 40.29, "t0_bkjd": 136.3, "depth_ppm": 2180.0, "duration_h": 4.16},
              "neighbours": [{"source_id": "1", "sep_arcsec": 7.1, "gmag": 18.0}],
              "lightcurve": {"file": "lc.csv"}, "tpf_files": ["tpf/q01.fits"], "fingerprint": "a" * 64}
    (path / "inputs.json").write_text(json.dumps(inputs), encoding="utf-8")


def test_tool_refuses_an_export_from_before_the_revision(tmp_path, monkeypatch, capsys):
    """I2: an export without the revision's keys is refused with exit 2 and a plain message, unless
    --allow-old-export is given."""
    from tools import triceratops_check as tc
    monkeypatch.chdir(tmp_path)                             # main works in the --out folder
    _old_export(tmp_path)
    monkeypatch.setattr(tc, "tr", object())                 # as if TRICERATOPS were installed
    args = ["--inputs", str(tmp_path), "--out", str(tmp_path / "r.json")]
    assert tc.main(args) == 2
    err = capsys.readouterr().err
    for words in ("no crowdsap key", "no psffrac key", "neighbour 1 carry no parameter sources",
                  "neighbour 1 carry no mass_radius (the radius each mass belongs to)", "no clip count",
                  "re-export it with skysift crosscheck --export", "--allow-old-export"):
        assert words in err
    assert not (tmp_path / "r.json").exists()

    class Reached(Exception):
        pass

    def target(**_kw):
        raise Reached
    monkeypatch.setattr(tc, "tr", SimpleNamespace(target=target))
    with pytest.raises(Reached):                             # past the refusal, at the package's constructor
        tc.main([*args, "--allow-old-export"])
    assert tc.export_problems({"target": {"crowdsap": 1.2, "psffrac": None},
                               "neighbours": [{"params_source": {}, "mass_radius": None}],
                               "lightcurve": {"clipped": 0}}) == ["the target's CROWDSAP 1.2 is not in (0, 1]"]
    assert tc.main([*args, "--draws", "0"]) == 2


def test_tool_checkpoint_resumes_only_the_same_settings(tmp_path):
    """I3: the partial file keeps the runs so far; a resume refuses other settings or a broken seed run."""
    from tools import triceratops_check as tc
    shares = {"source": "contamination budget", "budget": 0.167542, "crowdsap": 0.8565}
    key = tc.checkpoint_key("a" * 64, seed=1, draws=2000, check_draws=2000, parallel_check=True, flux_err=0.00092,
                            shares=shares, quarters=[1, 2], allow_old_export=False, version="1.1.0")
    part = tc.partial_path(tmp_path / "smoke.json")
    assert part.name == "smoke.partial.json"
    state = tc.new_state()
    state["runs"] = [tc.run_record(1 + k, 0.1, 1e-4, False, [{"ID": "1", "scenario": "TP", "prob": 0.9}])
                     for k in range(3)]
    state["check"] = {"comparison": "At 2,000 draws ..."}
    tc.write_partial(part, key, state)
    assert not list(tmp_path.glob("*.tmp"))
    back = tc.resume_state(part, key)
    assert back["runs"] == state["runs"] and back["check"] == state["check"] and back["parallel"] is True
    with pytest.raises(ValueError, match=r"\(N differ\)"):
        tc.resume_state(part, {**key, "N": 1_000_000})
    with pytest.raises(ValueError, match="inputs_sha256"):
        tc.resume_state(part, {**key, "inputs_sha256": "b" * 64})
    tc.write_partial(part, key, {**state, "runs": [dict(state["runs"][0], seed=5)]})
    with pytest.raises(ValueError, match="has seed 5, not 1"):
        tc.resume_state(part, key)
    part.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="not a checkpoint"):
        tc.resume_state(part, key)


def test_tool_leaves_a_mass_that_belongs_to_another_radius_to_the_package():
    """Round 2 minor 6, the smoke's TIC 169822636: the TIC Teff 4950 and radius 2.54 stand, and the mass the TIC
    lacks would come from the dwarf relation at the GSP-Phot radius 0.517, so it is left to the package."""
    from tools import triceratops_check as tc
    given = cc.neighbour_params({"teff_gspphot": "5021.5", "radius_gspphot": "0.517", "mass_flame": ""}, 1.0)
    given = {**given, "params_source": given["source"]}
    row = {"Teff": 4950.0, "rad": 2.54, "mass": 0.9, "params_estimated": "mass"}
    updates, sources = tc.param_updates(row, given, added=False)
    assert updates == [] and sources["Teff"] == sources["rad"] == "TIC"
    assert sources["mass"].startswith("Mamajek dwarf table mass-radius relation at the GSP-Phot radius 0.517")
    assert ("left to the package: that mass belongs to radius 0.517, more than 20% from the radius 2.540 the package "
            "uses; TRICERATOPS estimate from photometry") in sources["mass"]
    updates, _sources = tc.param_updates({**row, "rad": 0.55}, given, added=False)       # within 20 per cent: set
    assert updates == [("mass", given["mass"])]
    updates, _sources = tc.param_updates({"Teff": None, "rad": None, "mass": None}, given, added=True)
    assert [c for c, _v in updates] == ["Teff", "rad", "mass"]                      # an added star: one source


def test_the_import_refuses_a_smoke_result_and_an_old_export_result():
    """Round 2 important 1: fewer than 1e6 draws per run, or a run made with --allow-old-export, is refused."""
    sha = "a" * 64
    good = fake_result(sha)
    cc.validate_result({**good, "settings": {**good["settings"], "N": 1_000_000, "allow_old_export": False}})
    cc.validate_result(good)                                 # a result from before the flag: N 1e6 and no flag
    for n in (2000, 999_999, None, 1e6, True):
        with pytest.raises(cc.BadResult, match=f"settings.N is {n!r} draws per run, below the 1,000,000"):
            cc.validate_result({**good, "settings": {**good["settings"], "N": n}})
    with pytest.raises(cc.BadResult, match="settings.allow_old_export is True: the result was made from an export"):
        cc.validate_result({**good, "settings": {**good["settings"], "allow_old_export": True}})


def test_tool_warns_below_a_million_draws_and_guards_the_checkpoint(tmp_path, monkeypatch, capsys):
    """Round 2: the smoke warning; a checkpoint found without --resume stops the tool, and a resume with fewer runs
    than the checkpoint holds is refused, both before anything is built."""
    from tools import triceratops_check as tc
    monkeypatch.setattr(tc, "tr", None)
    args = ["--inputs", str(tmp_path), "--out", str(tmp_path / "r.json")]
    assert tc.main([*args, "--draws", "2000"]) == 2
    assert "--draws 2,000 is below 1,000,000: smoke only, the import will refuse it" in capsys.readouterr().err
    monkeypatch.setattr(tc, "tr", object())
    key = tc.checkpoint_key("a" * 64, seed=1, draws=2000, check_draws=2000, parallel_check=True, flux_err=0.001,
                            shares={"source": "x"}, quarters=[1], allow_old_export=False, version="1.1.0")
    state = tc.new_state()
    state["runs"] = [tc.run_record(1 + k, 0.1, 1e-4, False, []) for k in range(3)]
    tc.write_partial(tc.partial_path(tmp_path / "r.json"), key, state)
    assert tc.main(args) == 2
    assert "checkpoint exists; pass --resume or remove it" in capsys.readouterr().err
    assert tc.main([*args, "--resume", "--runs", "1"]) == 2
    assert "holds 3 runs, more than --runs 1" in capsys.readouterr().err
    assert tc.partial_path(tmp_path / "r.json").exists()                          # nothing was lost


def test_tool_field_stars_and_stars_are_hashed_for_the_resume_key(tmp_path):
    """Round 2 important 2: no field-star population stops the tool; its hash ignores the row order; the key names
    each rebuilt input that differs."""
    from tools import triceratops_check as tc
    with pytest.raises(ValueError, match="no field-star population"):
        tc.field_star_summary(None)
    with pytest.raises(ValueError, match="cannot be read"):
        tc.field_star_summary(str(tmp_path / "none.csv"))
    (tmp_path / "empty.csv").write_text("Mact,logg\n", encoding="utf-8")
    with pytest.raises(ValueError, match="holds no star"):
        tc.field_star_summary(str(tmp_path / "empty.csv"))
    (tmp_path / "a.csv").write_text("Mact,logg\n0.5,4.6\n0.8,4.5\n", encoding="utf-8")
    (tmp_path / "b.csv").write_text("Mact,logg\n0.8,4.5\n0.5,4.6\n", encoding="utf-8")
    a, b = tc.field_star_summary(str(tmp_path / "a.csv")), tc.field_star_summary(str(tmp_path / "b.csv"))
    assert a["n_rows"] == 2 and a["sha256"] == b["sha256"] and a["file"] == "a.csv"
    stars = [{"ID": 1, "Tmag": 15.2, "ra": 1.0, "dec": 2.0, "Teff": 3600.0, "rad": 0.5, "mass": 0.5, "plx": 3.3,
              "fluxratio": 0.85, "tdepth": 0.002}]
    assert tc.stars_digest(stars) == tc.stars_digest([dict(stars[0])])
    assert tc.stars_digest(stars) != tc.stars_digest([{**stars[0], "mass": 0.6}])
    common = {"seed": 1, "draws": 2000, "check_draws": 2000, "parallel_check": True, "flux_err": 0.001,
              "shares": {"source": "x"}, "quarters": [1], "allow_old_export": False, "version": "1.1.0"}
    key = tc.checkpoint_key("a" * 64, **common, field_stars=a, stars_sha256=tc.stars_digest(stars),
                            lightcurve_sha256="1" * 64, inputs_json_sha256="2" * 64)
    part = tmp_path / "r.partial.json"
    tc.write_partial(part, key, tc.new_state())                    # a hand-made checkpoint
    assert tc.resume_state(part, key)["runs"] == []
    for field, other in (("field_stars_sha256", "f" * 64), ("field_stars_n", 3), ("stars_sha256", "e" * 64),
                         ("lightcurve_sha256", "3" * 64), ("inputs_json_sha256", "4" * 64)):
        with pytest.raises(ValueError, match=rf"\({field} differ\)"):
            tc.resume_state(part, {**key, field: other})


def test_tool_checkpoint_survives_a_file_held_open(tmp_path, monkeypatch, capsys):
    from tools import triceratops_check as tc
    real, calls = tc.os.replace, []

    def flaky(src, dst):
        calls.append(1)
        if len(calls) < 3:
            raise PermissionError("in use")
        real(src, dst)
    monkeypatch.setattr(tc.os, "replace", flaky)
    part = tmp_path / "r.partial.json"
    assert tc.write_partial(part, {"k": 1}, tc.new_state(), pause=0) is True and len(calls) == 3 and part.exists()

    def held(src, dst):
        raise PermissionError("in use")
    monkeypatch.setattr(tc.os, "replace", held)
    assert tc.write_partial(part, {"k": 2}, tc.new_state(), pause=0) is False
    assert "held open by another process" in capsys.readouterr().err
    assert json.loads(part.read_text(encoding="utf-8"))["key"] == {"k": 1}        # the last whole checkpoint stays


def test_tool_check_record_is_clamped_and_star_rows_carry_the_box_factor():
    from tools import triceratops_check as tc
    rec = tc.parallel_check_record(1, 2000, {"fpp": -2.709e-14, "nfpp": 1.12e-24}, {"fpp": -2.665e-14, "nfpp": 0.5},
                                   [0.5, 0.4])
    assert rec["fpp_serial"] == rec["fpp_parallel"] == 0.0 and rec["nfpp_parallel"] == 0.5
    assert "the FPPs (0 serial, 0 parallel)" in rec["comparison"] and "the NFPPs (0 serial, 0.5 parallel)" in \
        rec["comparison"]
    stars = [{"ID": "t", "Tmag": 15.0, "fluxratio": 0.6}, {"ID": "tic", "Tmag": 14.0, "fluxratio": 0.2},
             {"ID": "added", "Tmag": 17.0, "fluxratio": 0.2}]
    f = tc.offset_box_factor(7.0)
    shares = tc.leakage_shares(stars, 0.8, [None] * 3, 0.002, box_factors=[None, None, f])
    rows = tc.star_rows(stars, [], {"added": 17.0}, shares, [None] * 3, {})
    assert [r["box_factor"] for r in rows] == [1.0, 1.0, f]


def test_the_import_requires_a_field_star_population():
    """Round 3: a result that does not show the package's field-star population was used is refused."""
    good = fake_result("a" * 64)
    for stars in (None, {}, {"n_rows": 0}, {"n_rows": True}, {"n_rows": "8883"}, []):
        settings = {k: v for k, v in good["settings"].items() if k != "field_stars"}
        if stars is not None:
            settings["field_stars"] = stars
        with pytest.raises(cc.BadResult, match="settings.field_stars records"):
            cc.validate_result({**good, "settings": settings})
    cc.validate_result(good)


def test_neighbour_params_at_the_edges_of_the_dwarf_table():
    """Round 3 minor 5: without a Teff the radius-only rule leaves an evolved star's mass to the package, and a value
    read outside the table says it is held at the table's edge."""
    tab = mamajek_table()
    big = cc.neighbour_params({"radius_gspphot": "5.0"}, None)
    assert big["teff"] is None and big["mass"] is None
    assert big["source"]["mass"] == ("Gaia DR3 GSP-Phot radius 5.000 above the dwarf table's largest 2.49, with no "
                                     "Teff to compare it at: evolved; mass left to the package")
    by_teff = np.argsort(tab[:, 1])
    hot = float(tab[by_teff, 1][-1]) - 100.0
    assert 2.55 <= 1.2 * float(np.interp(hot, tab[by_teff, 1], tab[by_teff, 2]))    # a dwarf by the Teff rule
    edge = cc.neighbour_params({"teff_gspphot": str(hot), "radius_gspphot": "2.55"}, None)
    assert edge["mass"] is not None and edge["source"]["mass"].endswith(", held at the table's edge")
    cool = cc.neighbour_params({"teff_gspphot": "2000", "radius_gspphot": "1.0"}, None)
    assert cool["mass"] is None and "K (Teff held at the table's edge): evolved" in cool["source"]["mass"]
    red = cc.neighbour_params(None, 5.5)
    assert all(s.endswith(", held at the table's edge") for s in red["source"].values())
    assert not cc.neighbour_params(None, 1.1)["source"]["teff"].endswith("edge")


def test_tool_writes_a_sorted_population_copy_per_result(tmp_path):
    """Round 3 minor 1: the result's own copy, rows sorted, header first, its hash the source's."""
    from tools import triceratops_check as tc
    src = tmp_path / "6146676_gaia_background.csv"
    src.write_text("Mact,logg\n0.8,4.5\n0.5,4.6\n0.6,4.55\n", encoding="utf-8")
    dest = tc.population_path(tmp_path / "smoke.json")
    assert dest.name == "smoke.population.csv"
    out = tc.sorted_population(str(src), dest)
    assert dest.read_text(encoding="utf-8") == "Mact,logg\n0.5,4.6\n0.6,4.55\n0.8,4.5\n"
    assert out["file"] == str(dest.resolve()) and out["source_file"] == src.name and out["n_rows"] == 3
    assert out["sha256"] == tc.field_star_summary(str(src))["sha256"] and not list(tmp_path.glob("*.tmp"))
    with pytest.raises(ValueError, match="no field-star population"):
        tc.sorted_population(None, dest)


def test_tool_leaves_a_finished_result_alone(tmp_path, monkeypatch, capsys):
    """Round 3 minor 4: an existing result stops the tool with exit 0 before anything is read or written."""
    from tools import triceratops_check as tc
    monkeypatch.setattr(tc, "tr", None)
    out = tmp_path / "r.json"
    out.write_text("{}", encoding="utf-8")
    assert tc.main(["--inputs", str(tmp_path / "missing"), "--out", str(out), "--resume"]) == 0
    assert "result exists; remove it to rerun" in capsys.readouterr().err
    assert out.read_text(encoding="utf-8") == "{}" and sorted(p.name for p in tmp_path.iterdir()) == ["r.json"]
