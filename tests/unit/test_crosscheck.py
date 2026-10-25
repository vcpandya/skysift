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
from skysift import dossier as ds
from skysift.dossier import EXCLUSION_RULE, generate
from skysift.lc.tpf import parse_tpf, tpf_listing_url
from skysift.stars import mamajek_table
from skysift.store.db import TargetRow
from tests.unit.synthetic_tpf import (
    CCD_ORIGIN,
    NEIGHBOUR_KIC,
    TARGET_RADEC,
    listing_html,
    tpf_fits,
    tpf_name,
    tpf_quarters,
)
from tests.unit.test_dossier import RESPONSES, _make_env, fake_fetch
from tests.unit.test_pixels import _psf
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


REMOVED = [{"tic_id": "169822655", "source_id": "2073896190925687296", "sep_arcsec": 11.452381,
            "min_margin_sigma": 12.657602}]


def fake_variant(fpp: float = 0.004, nfpp: float = 0.0, n: int = cc.N_RUNS, p_stp: float = 0.003,
                 removed: list | None = None, scenarios: list | None = None) -> dict:
    """The pixel-excluded run set as the tool writes it: ``n`` identical runs, seeds 1 to n."""
    runs = fake_runs(fpp, nfpp, n, p_stp)
    return {"n_runs": n, "n_degenerate": 0, **cc.summarise(runs), "runs": runs,
            "scenarios": scenarios or [{"ID": "169822650", "scenario": "TP", "prob": 1 - fpp},
                                       {"ID": "169822650", "scenario": "STP", "prob": p_stp}],
            "removed": REMOVED if removed is None else removed, "same_as_all": False,
            "seeds": [r["seed"] for r in runs], "median_run_seed": 1}


def fake_result(sha: str, fpp: float = 0.012, nfpp: float = 1e-4, n: int = cc.N_RUNS, p_stp: float = 0.0,
                scenarios: list | None = None, variant: dict | None = None) -> dict:
    """A result file as the tool writes it: ``n`` runs whose medians are ``fpp`` and ``nfpp``, and the pixel-excluded
    run set ``variant`` (``fake_variant(n=n)`` by default)."""
    runs = fake_runs(fpp, nfpp, n, p_stp)
    return {"schema": 3, "tool": cc.TOOL, "tool_version": "1.1.0", "run_utc": "2026-10-22T12:00:00+00:00", "n_runs": n,
            "n_degenerate": 0, **cc.summarise(runs), "runs": runs,
            "scenarios": scenarios or [{"ID": "169822650", "scenario": "TP", "prob": 1 - fpp}],
            "stars": [{"id": "169822650", "tmag": 15.19, "sep_arcsec": 0.0, "flux_ratio": 0.98, "tdepth": 0.0022}],
            "inputs_sha256": sha,
            "caveats": [("The light is shared in the Kepler band with Gaia G as the proxy; the limb darkening comes "
                         "from the package's Kepler table.")],
            "settings": {"N": 1_000_000, "exptime_days": 0.0204,
                         "field_stars": {"file": "r.population.csv", "n_rows": 8883, "sha256": "c" * 64}},
            "variant_excluded": variant if variant is not None else fake_variant(n=n)}


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
    # round 3 review M3: the separations, CROWDSAP and rung 5's PSF width the shares use
    farther = json.loads(json.dumps(DATA))
    farther["fpp"]["inputs"]["neighbours"][0]["sep_arcsec"] = 11.8
    assert cc.inputs_fingerprint(farther) != base
    crowded = {**DATA, "target": {**DATA["target"], "meta": {"crowdsap": 0.8565}}}
    assert cc.inputs_fingerprint(crowded) != base
    assert cc.inputs_fingerprint({**crowded, "target": {**DATA["target"], "meta": {"crowdsap": 0.86}}}) != \
        cc.inputs_fingerprint(crowded)
    wide = {**crowded, **_live()}
    narrow = {**wide, **_live([{**s, "psf_sigma_pix": 0.5} for s in LIVE_SEASONS])}
    assert cc.inputs_fingerprint(wide) != cc.inputs_fingerprint(crowded)
    assert cc.inputs_fingerprint(narrow) != cc.inputs_fingerprint(wide)


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
    assert block["current"] is True and set(block) == {*cc.RESULT_KEYS, "current", "class", "class_excluded"}
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
    same = {**fake_variant(removed=[]), "runs": runs, "n_degenerate": 2, **s, "seeds": [r["seed"] for r in runs],
            "same_as_all": True}
    result = {**fake_result("c" * 64), "runs": runs, "n_degenerate": 2, **s, "variant_excluded": same}
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
    # spec 12.2: the variant with its ranges
    assert (f"over 20 runs, {cc.NO_CLASS_CLAUSE}. With the one neighbour the difference images exclude removed "
            "(Gaia DR3 2073896190925687296 at 11.45 arcsec, smallest margin 12.7 sigma), 20 seeded runs give 0.0040 "
            "(16th to 84th percentile 0.0040 to 0.0040)") in text
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
                                 "companion), and 0.0040 (0.0010 without it) with the one neighbour the difference "
                                 "images exclude removed, in none of its validation classes, which do not apply at "
                                 "30-minute cadence (Giacalone et al. 2021)")
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


SIGMA_5 = 0.5695905975              # KIC 6146676: rung 5's psf_sigma_pix, mean over its four voting seasons
R2_STARS = [                        # results-r2/kic-006146676.json: the target and the three nearest TIC stars
    {"ID": "169822650", "Tmag": 15.1926, "fluxratio": 0.6157398795699183, "sep (arcsec)": 0.0},
    {"ID": "1879962333", "Tmag": 19.0349, "fluxratio": 0.007575984235464079, "sep (arcsec)": 6.938},
    {"ID": "169822636", "Tmag": 17.086, "fluxratio": 0.01056327860128502, "sep (arcsec)": 10.784},
    {"ID": "169822655", "Tmag": 12.3923, "fluxratio": 0.36537585545284657, "sep (arcsec)": 11.69}]
R2_G = [16.236654, 19.771944, 17.895601, 12.806441]
R2_SEPS = [r["sep (arcsec)"] for r in R2_STARS]
CROWDSAP_6146676, DEPTH_6146676 = 0.85650748, 0.0022139651


def test_tool_leakage_shares_two_stars_by_hand():
    """KIC 6146676 and KIC 6146666 (ruling n as amended after the Task 9 review), the round-2 rule kept as
    ``budget_shares``: CROWDSAP 0.8565 is the target's fraction of the aperture light, so the neighbours' light over
    the target's is B = 0.1435 / 0.8565 = 0.1675, all of it the one neighbour's. Under spec 12.1 a neighbour stored
    without a separation has overlap 1, so its weight (its Kepler-band flux, 23.6) is capped at B and the round-2
    numbers stand: the target's flux ratio is CROWDSAP itself, the neighbour's 1 - CROWDSAP, and the depth the
    neighbour would need is the on-target depth over B."""
    from tools import triceratops_check as tc
    stars = [{"ID": "169822650", "Tmag": 15.19, "fluxratio": 0.616},
             {"ID": "169822655", "Tmag": 12.39, "fluxratio": 0.384}]
    g, before = tc.budget_shares(stars, 0.8565, [16.24, 12.81])
    assert before[0] is None and before[1] == pytest.approx(0.167542, abs=1e-6)
    # the package's box fractions re-weighted into the Kepler band: G - T is 1.05 for the target, 0.42 for the
    # neighbour, so the neighbour's fraction grows by 10^(0.4 x 0.63)
    w = 0.384 * 10 ** (0.4 * 0.63)
    assert g[1] == pytest.approx(w / (w + 0.616)) and g[0] == pytest.approx(0.616 / (w + 0.616))
    out = tc.leakage_shares(stars, 0.8565, [16.24, 12.81], 0.0021653, [0.0, None], SIGMA_5)
    assert out["source"] == tc.OVERLAP and out["budget"] == pytest.approx(0.167542, abs=1e-6)
    assert out["contaminating_fraction"] == pytest.approx(0.1435) and out["crowdsap"] == 0.8565
    assert out["capped"] is True and out["weight_sum"] == pytest.approx(10 ** (0.4 * (16.24 - 12.81)))
    t, n = out["rows"]
    assert n["overlap"] == 1.0 and t["overlap"] is None and t["weight"] is None
    assert t["fluxratio"] == pytest.approx(0.8565) and n["fluxratio"] == pytest.approx(0.1435)
    assert t["fluxratio"] + n["fluxratio"] == pytest.approx(1.0)
    assert n["fluxratio"] / t["fluxratio"] == pytest.approx(out["budget"])        # L F / F_t, the ratio to the target
    assert t["tdepth"] == pytest.approx(0.0021653) and n["tdepth"] == pytest.approx(0.012924, abs=1e-6)
    assert n["share"] == pytest.approx(0.167542, abs=1e-6) and t["share"] is None
    assert n["share_before"] == before[1] and n["g"] == g[1] and t["share_before"] is None
    assert n["kepler_flux"] == pytest.approx(10 ** (0.4 * (16.24 - 12.81))) and t["kepler_flux"] == 1.0
    assert n["g_package"] == 0.384 and n["band"] == "Gaia G"
    assert all(r["source"] == tc.OVERLAP for r in out["rows"])


def test_tool_leakage_shares_split_the_budget_in_the_kepler_band_and_fall_back_to_the_box():
    from tools import triceratops_check as tc
    stars = [{"ID": "t", "Tmag": 15.0, "fluxratio": 0.6}, {"ID": "a", "Tmag": 13.0, "fluxratio": 0.3},
             {"ID": "b", "Tmag": 16.0, "fluxratio": 0.1}, {"ID": "c", "Tmag": 18.0, "fluxratio": 0.0}]
    gmags = [16.0, 13.5, None, 18.5]
    budget = 0.25 / 0.75                                     # (1 - C) / C
    # the round-2 rule (share_before): "b" has no G, so it keeps the target's G - T offset; "c" sits outside the
    # package's box and gets nothing
    g, before = tc.budget_shares(stars, 0.75, gmags)
    ga, gb = 0.3 * 10 ** (-0.4 * (0.5 - 1.0)), 0.1
    assert before[1] == pytest.approx(budget * ga / (ga + gb)) and before[2] == pytest.approx(budget * gb / (ga + gb))
    assert before[3] == 0.0 and before[1] + before[2] == pytest.approx(budget) and before[0] is None
    assert g[1] / g[2] == pytest.approx(ga / gb)
    # spec 12.1 at 0, 1, 2 and 10 Kepler pixels: "b" is weighted by its Tmag, "c" is far outside the box
    seps = [0.0, tc.KEPLER_PIX_ARCSEC, 2 * tc.KEPLER_PIX_ARCSEC, 10 * tc.KEPLER_PIX_ARCSEC]
    out = tc.leakage_shares(stars, 0.75, gmags, 0.002, seps, SIGMA_5)
    _target, a, b, c = out["rows"]
    assert [r["share_before"] for r in (a, b, c)] == before[1:]
    assert b["band"] == "Tmag" and b["kepler_flux"] is None and c["share"] == 0.0 and c["tdepth"] == 0.0
    wa = tc.overlap(seps[1], SIGMA_5) * 10 ** (-0.4 * (13.5 - 16.0))
    wb = tc.overlap(seps[2], SIGMA_5) * 10 ** (-0.4 * (16.0 - 15.0))
    assert a["weight"] == pytest.approx(wa) and b["weight"] == pytest.approx(wb) and out["capped"] is True
    assert out["budget"] == pytest.approx(budget) and out["scale"] == pytest.approx(budget / (wa + wb))
    assert a["share"] == pytest.approx(budget * wa / (wa + wb)) and b["share"] == pytest.approx(budget * wb / (wa + wb))
    assert a["share"] + b["share"] == pytest.approx(budget)
    assert out["rows"][0]["fluxratio"] == pytest.approx(0.75) and a["fluxratio"] == pytest.approx(a["share"] * 0.75)
    assert sum(r["fluxratio"] for r in out["rows"]) == pytest.approx(1.0)
    assert a["tdepth"] == pytest.approx(0.002 / a["share"]) and b["tdepth"] == pytest.approx(0.002 / b["share"])
    deep = tc.leakage_shares(stars, 0.9999, gmags, 0.002, seps, SIGMA_5)
    assert deep["rows"][2]["tdepth"] == 0.0 and deep["rows"][1]["tdepth"] == 0.0       # above 1: no such eclipse
    for crowdsap in (None, 1.2, 0.0, math.nan):              # no budget: the package's Gaussian box stands
        box = tc.leakage_shares(stars, crowdsap, gmags, 0.002, seps, SIGMA_5)
        assert box["source"] == "package Gaussian box" and box["budget"] is None
        assert [r["fluxratio"] for r in box["rows"]] == [0.6, 0.3, 0.1, 0.0]
        assert box["rows"][1]["tdepth"] == pytest.approx(0.002 * 0.6 / 0.3) and box["rows"][3]["tdepth"] == 0.0
    clean = tc.leakage_shares(stars, 1.0, gmags, 0.002, seps, SIGMA_5)
    assert clean["budget"] == 0.0 and all(r["tdepth"] == 0.0 for r in clean["rows"][1:])
    bare = tc.leakage_shares([*stars, {"ID": "d", "fluxratio": 0.0}], 0.75, [*gmags, None], 0.002, [*seps, 4.0],
                             SIGMA_5)                       # a star with neither magnitude takes nothing
    assert bare["rows"][4]["band"] is None and bare["rows"][4]["weight"] == 0.0 and bare["rows"][4]["share"] == 0.0


def test_tool_overlap_is_the_box_fraction_at_rung_5s_width_in_kepler_pixels():
    from skysift.lc.pixels import PIX_ARCSEC
    from tools import triceratops_check as tc
    assert tc.KEPLER_PIX_ARCSEC == PIX_ARCSEC == 3.98
    assert tc.overlap(11.69, SIGMA_5) == pytest.approx(0.005864323, rel=1e-5)        # 2.94 Kepler pixels
    assert tc.overlap(6.938, SIGMA_5) == pytest.approx(0.3375419, rel=1e-5)
    assert tc.overlap(10.784, SIGMA_5) == pytest.approx(0.01699803, rel=1e-5)
    assert tc.overlap(0.0, SIGMA_5) == pytest.approx(1.0) and tc.overlap(None, SIGMA_5) == 1.0
    assert tc.offset_box_factor(7.0) == pytest.approx(0.38705, abs=1e-4)             # the round-2 rule, unchanged
    for bad in (None, 0.0, -0.5, math.nan):
        with pytest.raises(ValueError, match="overlap needs rung 5's PSF width"):
            tc.overlap(11.69, bad)


def test_tool_shares_are_overlap_times_kepler_flux_under_the_budget_cap():
    """Spec 12.1 on KIC 6146676's three nearest TIC stars: w = overlap x 10^(-0.4 (G - G_t)). Their sum 0.15483 is
    below the budget 0.16753, so the shares stand: the G 12.8 star at 11.69 arcsec keeps 0.1381 of the 0.1618 the
    round-2 rule gives it on these four rows (0.1615 on the full list)."""
    from tools import triceratops_check as tc
    out = tc.leakage_shares(R2_STARS, CROWDSAP_6146676, R2_G, DEPTH_6146676, R2_SEPS, SIGMA_5)
    assert out["source"] == tc.OVERLAP and out["sigma_pix"] == SIGMA_5
    assert out["budget"] == pytest.approx(0.1675321271, rel=1e-9) and out["capped"] is False and out["scale"] == 1.0
    assert out["weight_sum"] == pytest.approx(0.1548311, rel=1e-5)
    t, a, b, c = out["rows"]
    assert [r["share"] for r in (a, b, c)] == pytest.approx([0.01300803, 0.003688244, 0.1381348], rel=1e-5)
    assert [r["share_before"] for r in (a, b, c)] == pytest.approx([0.002491678, 0.003249592, 0.1617909], rel=1e-5)
    assert c["overlap"] == pytest.approx(0.005864323, rel=1e-5) and c["kepler_flux"] == pytest.approx(23.5551,
                                                                                                      rel=1e-5)
    assert t["share"] is None and t["share_before"] is None and t["overlap"] is None
    assert t["fluxratio"] == pytest.approx(0.8659275, rel=1e-6) and c["fluxratio"] == pytest.approx(0.1196147, rel=1e-5)
    assert c["tdepth"] == pytest.approx(0.01602757, rel=1e-5) and t["tdepth"] == DEPTH_6146676
    assert sum(r["fluxratio"] for r in out["rows"]) == pytest.approx(1.0)
    assert all(r["source"] == tc.OVERLAP for r in out["rows"]) and c["band"] == "Gaia G"


def test_tool_shares_above_the_budget_are_scaled_down_to_it():
    from tools import triceratops_check as tc
    out = tc.leakage_shares(R2_STARS, 0.95, R2_G, DEPTH_6146676, R2_SEPS, SIGMA_5)
    assert out["capped"] is True and out["scale"] == pytest.approx(0.3399291, rel=1e-6)
    t, a, b, c = out["rows"]
    assert a["share"] + b["share"] + c["share"] == pytest.approx(0.05 / 0.95)
    assert c["share"] == pytest.approx(0.04695603, rel=1e-5) and b["tdepth"] == 0.0      # 1.77 needed: no eclipse
    assert t["fluxratio"] == pytest.approx(0.95)


def test_tool_without_rung_5s_width_or_a_budget_the_package_box_stands():
    from tools import triceratops_check as tc
    for crowdsap, sigma in ((CROWDSAP_6146676, None), (None, SIGMA_5), (CROWDSAP_6146676, math.nan)):
        box = tc.leakage_shares(R2_STARS, crowdsap, R2_G, DEPTH_6146676, R2_SEPS, sigma)
        assert box["source"] == tc.BOX and box["weight_sum"] is None and box["capped"] is None
        assert [r["fluxratio"] for r in box["rows"]] == [r["fluxratio"] for r in R2_STARS]


LIVE_SEASONS = [                    # KIC 6146676's rung 5 seasons in the live dossier, the fields spec 12 reads
    {"season": 0, "voting": True, "psf_sigma_pix": 0.58222301, "nearer": "target", "separation_sigma": 27.845078,
     "dist_target_pix": 0.19970259, "centroid_sigma_axis_pix": 0.057549507,
     "centroid_sigma_pix": 0.063594178,
     "pos_target": [0.4881452, 4.1079262], "pos_neighbour": [3.2587867, 3.2589792]},
    {"season": 1, "voting": True, "psf_sigma_pix": 0.55311152, "nearer": "target", "separation_sigma": 19.814692,
     "dist_target_pix": 0.18730524, "centroid_sigma_axis_pix": 0.068707453,
     "centroid_sigma_pix": 0.072737804,
     "pos_target": [0.92328348, 3.0889016], "pos_neighbour": [3.6952899, 2.2423046]},
    {"season": 2, "voting": True, "psf_sigma_pix": 0.54572735, "nearer": "target", "separation_sigma": 17.424733,
     "dist_target_pix": 0.11745102, "centroid_sigma_axis_pix": 0.078553466,
     "centroid_sigma_pix": 0.076771451,
     "pos_target": [0.90604171, 2.9713739], "pos_neighbour": [3.6771262, 2.1225931]},
    {"season": 3, "voting": True, "psf_sigma_pix": 0.59730051, "nearer": "target", "separation_sigma": 12.657602,
     "dist_target_pix": 0.18658094, "centroid_sigma_axis_pix": 0.10056612,
     "centroid_sigma_pix": 0.10787247,
     "pos_target": [0.73929463, 3.0401927], "pos_neighbour": [3.5106545, 2.1932004]}]
LIVE_NEIGHBOURS = [                 # its fpp.inputs.neighbours (Gaia DR3 id, separation, G)
    {"source_id": "2073896294004902144", "sep_arcsec": 7.1422442, "gmag": 19.771944},
    {"source_id": "2073896289692192128", "sep_arcsec": 10.941229, "gmag": 17.895601},
    {"source_id": "2073896190925687296", "sep_arcsec": 11.452381, "gmag": 12.806441},
    {"source_id": "2073896190926955520", "sep_arcsec": 12.965638, "gmag": 19.735788},
    {"source_id": "2073896190921609600", "sep_arcsec": 13.513456, "gmag": 19.379517},
    {"source_id": "2073896289692177024", "sep_arcsec": 18.624956, "gmag": 18.883438}]


def _live(seasons=LIVE_SEASONS, neighbours=LIVE_NEIGHBOURS) -> dict:
    return {"tests": [{"rung": "rung5_pixels", "status": "pass", "metrics": {"seasons": seasons}}],
            "fpp": {"inputs": {"neighbours": neighbours}}}


LIVE_PSF = cc.rung5_psf_sigma(_live()) if hasattr(cc, "rung5_psf_sigma") else None


def test_rung5_psf_sigma_is_the_mean_over_voting_seasons():
    out = cc.rung5_psf_sigma(_live())
    assert out["sigma_pix"] == pytest.approx(SIGMA_5) and out["seasons"] == [0, 1, 2, 3] and out["reason"] is None
    assert out["per_season"]["3"] == 0.59730051 and out["source"] == cc.PSF_SIGMA_SOURCE
    some = [{**LIVE_SEASONS[0], "voting": False}, *LIVE_SEASONS[1:3], {**LIVE_SEASONS[3], "psf_sigma_pix": None}]
    assert cc.rung5_psf_sigma(_live(some))["sigma_pix"] == pytest.approx((0.55311152 + 0.54572735) / 2)
    assert cc.rung5_psf_sigma(_live(some))["seasons"] == [1, 2]
    none = cc.rung5_psf_sigma(_live([{**s, "voting": False} for s in LIVE_SEASONS]))
    assert none["sigma_pix"] is None and none["reason"] == "rung 5 has no voting season with a fitted PSF width"
    assert cc.rung5_psf_sigma({"tests": []})["reason"] == "rung 5 has not run"


def test_the_export_carries_schema_3_and_rung_5s_psf_width(env, tmp_path):
    cc.export_inputs(env.store, env.blob, env.settings, STAGE2_TARGET, tmp_path / "cc", fetch=env.xfetch)
    inputs = json.loads((tmp_path / "cc" / "inputs.json").read_text(encoding="utf-8"))
    assert inputs["schema"] == cc.EXPORT_SCHEMA == 3
    assert inputs["rung5_psf_sigma"] == cc.rung5_psf_sigma(_stored(env))


def _current_export(path: Path, **inputs_changes) -> None:
    """``_old_export`` with everything an export of round 3 carries; ``inputs_changes`` replace keys of inputs.json."""
    _old_export(path)
    inputs = json.loads((path / "inputs.json").read_text(encoding="utf-8"))
    inputs.update({"schema": 3, "rung5_psf_sigma": LIVE_PSF, "pixel_excluded": cc.pixel_excluded(_live()),
                   "target": {**inputs["target"], "crowdsap": 0.8565, "psffrac": 0.8},
                   "neighbours": [{**n, "params_source": {}, "mass_radius": None} for n in inputs["neighbours"]],
                   "lightcurve": {**inputs["lightcurve"], "clipped": 0}, **inputs_changes})
    (path / "inputs.json").write_text(json.dumps(inputs), encoding="utf-8")


def test_tool_refuses_an_export_without_rung_5s_psf_width(tmp_path, monkeypatch, capsys):
    from tools import triceratops_check as tc
    monkeypatch.chdir(tmp_path)
    none = cc.rung5_psf_sigma(_live([{**s, "voting": False} for s in LIVE_SEASONS]))
    _current_export(tmp_path, rung5_psf_sigma=none)
    monkeypatch.setattr(tc, "tr", object())                 # as if TRICERATOPS were installed
    args = ["--inputs", str(tmp_path), "--out", str(tmp_path / "r.json")]
    assert tc.main(args) == 2 and tc.main([*args, "--allow-old-export"]) == 2
    err = capsys.readouterr().err
    assert ("rung 5 has no fitted PSF width for this star (rung 5 has no voting season with a fitted PSF width), so "
            "the Kepler-pixel overlap cannot be computed; no run is started") in err
    assert not (tmp_path / "r.json").exists()


def test_the_import_refuses_a_result_before_schema_3():
    good = fake_result("a" * 64)
    assert good["schema"] == cc.RESULT_SCHEMA == 3 and cc.RESULT_KEYS[0] == "schema"
    cc.validate_result(good)
    with pytest.raises(cc.BadResult, match=r"the result is schema 2 \(no schema key\), not 3: it was made before "
                                           r"round 3"):
        cc.validate_result({k: v for k, v in good.items() if k != "schema"})
    with pytest.raises(cc.BadResult, match="the result is schema 2, not 3"):
        cc.validate_result({**good, "schema": 2})
    assert not cc.complete({k: v for k, v in good.items() if k != "schema"})       # a stored round-2 block


def test_tool_result_and_checkpoint_carry_the_schema_and_the_width():
    from tools import triceratops_check as tc
    shares = tc.leakage_shares(R2_STARS, CROWDSAP_6146676, R2_G, DEPTH_6146676, R2_SEPS, SIGMA_5)
    key = tc.checkpoint_key("a" * 64, seed=1, draws=2000, check_draws=2000, parallel_check=True, flux_err=0.001,
                            shares=shares, quarters=[1], allow_old_export=False, version="1.1.0")
    assert key["flux_shares"] == {"source": tc.OVERLAP, "budget": shares["budget"], "crowdsap": CROWDSAP_6146676,
                                  "sigma_pix": SIGMA_5}
    rows = tc.star_rows(R2_STARS, [], {}, shares, R2_G, {})
    assert rows[3]["overlap"] == pytest.approx(0.005864323, rel=1e-5) and rows[3]["share_before"] == pytest.approx(
        0.1617909, rel=1e-5) and rows[3]["share"] == pytest.approx(0.1381348, rel=1e-5)
    one = [tc.run_record(1, 0.1, 0.01, False, [])]
    assert tc.assemble_result(one, rows, {}, {"fingerprint": "a" * 64}, "1.1.0",
                              variant=tc.variant_block(one, [], True))["schema"] == tc.RESULT_SCHEMA == 3


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
    result = tc.assemble_result(runs, [{"id": "1", "tmag": 15.2}], settings, {"fingerprint": "b" * 64}, "1.1.0",
                                variant=tc.variant_block(runs, [], True))
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
    shares = tc.leakage_shares(R2_STARS, CROWDSAP_6146676, R2_G, DEPTH_6146676, R2_SEPS, SIGMA_5)
    text = tc.leakage_caveat(shares, CROWDSAP_6146676)
    assert text.startswith("The budget (1 - C) / C = 0.1675 comes from the KBonus crowding metric for this "
                           "extraction (the CROWDSAP of the target's KBonus file, C = 0.8565: the catalogue's own "
                           "estimate that 14 percent of the fitted flux is from other sources, PRF wings included); "
                           "the split among the neighbours follows the PRF overlap rule; the cap did not bind. ")
    assert "rung 5's fitted width (0.570 Kepler pixels, the mean over its voting seasons)" in text and "Gaia G" in text
    # round 3 review M4: the sum is of the overlap-weighted fluxes, which are the shares only under the budget
    assert "the overlap-weighted fluxes sum to 0.1548, within the budget, so they stand." in text
    named = tc.leakage_caveat(shares, CROWDSAP_6146676, {"crowdsap_column": "crowdsap", "crowdsap_catalogue":
                                                         "KBonus-bkg v1.0 source catalogue (Martinez-Palomera et "
                                                         "al. 2023)"})
    assert named.startswith("The budget (1 - C) / C = 0.1675 comes from the KBonus crowding metric for this extraction "
                            "(the crowdsap column of the KBonus-bkg v1.0 source catalogue (Martinez-Palomera et al. "
                            "2023), C = 0.8565: ")
    assert "upper bound for the KBonus PSF photometry" in text and "—" not in text
    # the controller's ruling after Task 14 (spec 12.1 amended): a floor on the box light, which bounds the PSF leak
    assert text.endswith("Each share is a floor on the light that neighbour puts in the target's 3 x 3 box, and that "
                         "light, with the nearby false-positive probability it drives, is an upper bound for the "
                         "KBonus PSF photometry, since the PRF fit removes a resolved neighbour's light to first "
                         "order.")
    capped = tc.leakage_shares(R2_STARS, 0.95, R2_G, DEPTH_6146676, R2_SEPS, SIGMA_5)
    assert "above the budget, so they are scaled down to it" in tc.leakage_caveat(capped, 0.95)
    assert "the cap did bind. " in tc.leakage_caveat(capped, 0.95)
    # round 3 review M2: the PRF caveat stands beside the overlap shares only
    assert tc.PRF_CAVEAT not in tc.CAVEATS
    assert tc.share_caveats(shares, CROWDSAP_6146676) == [text, tc.PRF_CAVEAT]
    box = tc.leakage_shares(R2_STARS, None, R2_G, DEPTH_6146676, R2_SEPS, SIGMA_5)
    assert tc.share_caveats(box, None) == [tc.leakage_caveat(box, None)]
    assert "so each is a floor on the light that neighbour puts in that box. That box light is in turn an upper " \
           "bound on what leaks into the KBonus PSF light curve, since the PRF fit removes" in tc.PRF_CAVEAT
    assert "leaks into the KBonus PSF light curve (Bryson" not in tc.PRF_CAVEAT
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
    weighted by the Gaussian box fraction at its stored separation, one at 7 arcsec takes less of the budget under the
    round-2 rule (share_before). Spec 12.1 weighs it by its overlap at that stored separation, as every star."""
    from tools import triceratops_check as tc
    # d = 7 / 4 pixels, sigma 0.75: [Phi(-0.333) - Phi(-4.333)] / [Phi(2) - Phi(-2)] = 0.36944 / 0.95450
    assert tc.offset_box_factor(7.0) == pytest.approx(0.38705, abs=1e-4)
    assert tc.offset_box_factor(0.0) == pytest.approx(1.0) and tc.offset_box_factor(None) == 1.0
    stars = [{"ID": "t", "Tmag": 15.0, "fluxratio": 0.5}, {"ID": "tic", "Tmag": 14.0, "fluxratio": 0.2},
             {"ID": "added", "Tmag": 17.0, "fluxratio": 0.3}]
    budget = 0.2 / 0.8
    at_zero = tc.leakage_shares(stars, 0.8, [None] * 3, 0.002, [0.0, 4.0, 0.0], SIGMA_5,
                                box_factors=[None, None, tc.offset_box_factor(0.0)])
    at_seven = tc.leakage_shares(stars, 0.8, [None] * 3, 0.002, [0.0, 4.0, 7.0], SIGMA_5,
                                 box_factors=[None, None, tc.offset_box_factor(7.0)])
    assert at_zero["rows"][2]["share_before"] == pytest.approx(budget * 0.3 / 0.5)
    f = tc.offset_box_factor(7.0)
    assert at_seven["rows"][2]["share_before"] == pytest.approx(budget * 0.3 * f / (0.2 + 0.3 * f))
    assert at_seven["rows"][2]["share_before"] < at_zero["rows"][2]["share_before"]
    assert at_seven["rows"][2]["box_factor"] == f
    assert at_seven["rows"][1]["share_before"] + at_seven["rows"][2]["share_before"] == pytest.approx(budget)
    assert at_seven["rows"][2]["overlap"] == tc.overlap(7.0, SIGMA_5) and at_zero["rows"][2]["overlap"] == 1.0
    assert at_seven["rows"][2]["weight"] == pytest.approx(tc.overlap(7.0, SIGMA_5) * 10 ** (-0.4 * 2.0))


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
                  "the export is schema None, not 3", "the export has no rung5_psf_sigma key",
                  "the export has no pixel_excluded key",
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
    assert tc.export_problems({"schema": 3, "rung5_psf_sigma": LIVE_PSF, "pixel_excluded": None,
                               "target": {"crowdsap": 1.2, "psffrac": None},
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
    shares = tc.leakage_shares(stars, 0.8, [None] * 3, 0.002, [0.0, 4.0, 7.0], SIGMA_5, box_factors=[None, None, f])
    rows = tc.star_rows(stars, [], {"added": 17.0}, shares, [None] * 3, {})
    assert [r["box_factor"] for r in rows] == [1.0, 1.0, f]
    assert [r["overlap"] for r in rows] == [None, tc.overlap(4.0, SIGMA_5), tc.overlap(7.0, SIGMA_5)]
    assert [r["weight"] for r in rows] == [r["weight"] for r in shares["rows"]] and rows[0]["weight"] is None
    assert [r["share_before"] for r in rows] == [r["share_before"] for r in shares["rows"]]
    assert rows[0]["share_bound"] is None and "upper bound for the KBonus PSF photometry" in rows[1]["share_bound"]
    # round 3 review M1: an added star's row has no package separation, so the one its overlap used is stored
    assert [r["overlap_sep_arcsec"] for r in rows] == [None, 4.0, 7.0]


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


def test_the_predicted_leak_is_the_targets_share_of_the_neighbours_aperture_times_the_depth():
    tpfs = [parse_tpf(bytes(b), tpf_name(NEIGHBOUR_KIC, b.quarter)) for b in tpf_quarters()]
    # the scene's PSF is a pixel-integrated Gaussian of 0.9 px; rung 5's model samples one at the pixel centres, and
    # its fit to this scene takes the width the pixel adds in quadrature, sqrt(0.9^2 + 1/12) px
    width = math.hypot(0.9, 1.0 / math.sqrt(12.0))
    rung5 = {"leak_depth_ppm": [14.0, 31.0], "neighbour_kic": NEIGHBOUR_KIC,
             "seasons": [{"season": s, "psf_sigma_pix": width, "shift_pix": [0.0, 0.0]} for s in range(4)]}
    target = {"ra": TARGET_RADEC[0], "dec": TARGET_RADEC[1], "meta": {}}
    block = ds.predicted_leak(tpfs, target, rung5, 2200.0)
    expected = []
    for b, t in zip(tpf_quarters(), tpfs, strict=True):
        mask = (t.aperture & 2) != 0
        pt, pn = _psf(b.pos_target, t.cube.shape[1:])[mask].sum(), _psf(b.pos_neighbour, t.cube.shape[1:])[mask].sum()
        expected.append(pt / (pt + 5.0 * pn))                   # the scene's neighbour is five times the target
    assert block["share"] == pytest.approx(np.mean(expected), rel=0.05)
    assert block["predicted_ppm"] == pytest.approx(2200.0 * block["share"])
    assert (block["measured_ppm"], block["measured_err_ppm"], block["kic"]) == (14.0, 31.0, NEIGHBOUR_KIC)
    assert [q["quarter"] for q in block["quarters"]] == [1, 2, 3, 4, 5] and block["reason"] is None
    assert ds.predicted_leak([], target, rung5, 2200.0)["reason"] == f"no kept pixel file of KIC {NEIGHBOUR_KIC}"
    assert ds.predicted_leak(tpfs, target, {**rung5, "leak_depth_ppm": None}, 2200.0) is None


def test_the_predicted_leak_uses_the_usable_files_and_says_why_it_has_no_prediction(monkeypatch):
    """Task 12 review M-3, M-4 and M-7: only the quarters rung 5's seasons used, a missing depth named, and a fitted
    flux of this star that is not positive giving a share of 0 with its reason."""
    tpfs = [parse_tpf(bytes(b), tpf_name(NEIGHBOUR_KIC, b.quarter)) for b in tpf_quarters()]
    width = math.hypot(0.9, 1.0 / math.sqrt(12.0))
    seasons = [{"season": s, "psf_sigma_pix": width, "shift_pix": [0.0, 0.0], "quarters": q}
               for s, q in ((0, [2]), (1, [3]), (2, [4]), (3, []))]            # quarters 1 and 5 unusable
    rung5 = {"leak_depth_ppm": [14.0, 31.0], "neighbour_kic": NEIGHBOUR_KIC, "seasons": seasons}
    target = {"ra": TARGET_RADEC[0], "dec": TARGET_RADEC[1], "meta": {}}
    block = ds.predicted_leak(tpfs, target, rung5, 2200.0)
    assert [q["quarter"] for q in block["quarters"]] == [2, 3, 4] and block["reason"] is None
    no_depth = ds.predicted_leak(tpfs, target, rung5, None)
    assert no_depth["predicted_ppm"] is None and no_depth["reason"] == "no depth on the fold"
    none_used = ds.predicted_leak(tpfs, target, {**rung5, "seasons": [{**s, "quarters": [9]} for s in seasons]},
                                  2200.0)
    assert none_used["predicted_ppm"] is None and none_used["reason"] == (
        f"no pixel file of KIC {NEIGHBOUR_KIC} that the pixel check used")
    monkeypatch.setattr(ds, "psf_fluxes", lambda *a, **k: (-3.0e3, 5.0e6))
    negative = ds.predicted_leak(tpfs, target, rung5, 2200.0)
    assert negative["share"] == 0.0 and negative["predicted_ppm"] == 0.0
    assert {q["reason"] for q in negative["quarters"]} == {"the fitted flux of this star is not positive"}


R2_ROWS = [                         # results-r2/kic-006146676.json "stars": id, sep, G, Kepler flux, share, Gaia id
    ("169822650", 0.0, 16.236654, 1.0, None, None),
    ("1879962333", 6.938, 19.771944, 0.03853754101954881, 0.0024876585183042496, "2073896294004902144"),
    ("169822636", 10.784, 17.895601, 0.21698074699419992, 0.003244349526576897, "2073896289692192128"),
    ("169822655", 11.69, 12.806441, 23.555113433751, 0.16152983418298314, "2073896190925687296"),
    ("508690015", 13.151, 19.735788, 0.0398424833663588, 0.00013322001395335415, "2073896190926955520"),
    ("169822646", 13.584, 19.379517, 0.055316513563358254, 0.0001354611838130699, "2073896190921609600"),
    ("169822657", 18.369, 18.883438, 0.08735472482743722, 1.5945761920931935e-06, "2073896289692177024"),
    ("1879962327", 20.886, 20.5462, 0.018887809767256545, 5.1585379086076936e-09, None),
    ("1879962326", 22.446, 20.3416, 0.02280455489544754, 2.579127821808112e-09, None)]
R2_RESULT = {"stars": [{"id": i, "sep_arcsec": s, "gmag": g, "kepler_flux": f, "share": sh, "gaia_source_id": gid}
                       for i, s, g, f, sh, gid in R2_ROWS],
             "settings": {"flux_shares": {"source": "contamination budget", "budget": 0.1675321270982946,
                                          "crowdsap": 0.85650748}},
             "nfpp": 0.02232841831933376, "nfpp_p16": 0.016386351289008505, "nfpp_p84": 0.03243009921894156,
             "n_runs": 20}


def test_the_leakage_note_lists_each_neighbour_before_and_after(tmp_path):
    from tools import leakage_note as ln
    from tools import triceratops_check as tc
    rows = ln.note_rows(R2_RESULT, SIGMA_5)
    assert [r["sep_arcsec"] for r in rows] == [6.938, 10.784, 11.69, 13.151, 13.584, 18.369, 20.886]
    assert [r["share_before"] for r in rows] == [r[4] for r in R2_ROWS[1:8]]
    assert [r["share"] for r in rows] == pytest.approx([0.01300803, 0.003688244, 0.1381348, 3.087380e-05,
                                                        2.184640e-05, 1.98896e-09, 4.48961e-13], rel=1e-4)
    assert rows[2]["sep_pix"] == pytest.approx(2.937186, rel=1e-6) and rows[2]["gaia_source_id"] == (
        "2073896190925687296")
    sigma = cc.rung5_psf_sigma(_live())
    text = ln.render(R2_RESULT, sigma, rows)
    assert "| 169822655 | 2073896190925687296 | 11.69 | 2.94 | 12.81 | 23.56 | 0.005864 | 0.1615 | 0.1381 |" in text
    assert "NFPP before: 0.022 (16th to 84th percentile 0.016 to 0.032) over 20 runs" in text
    assert "NFPP after: not yet measured (the round-3 run)" in text
    assert ("The largest share after the rule is 0.138 (TIC 169822655, 11.69 arcsec, 2.94 Kepler pixels): its "
            "overlap 0.0059 times its Kepler-band flux of 23.6 times the target's.") in text
    after = {**R2_RESULT, "nfpp": 0.0191, "nfpp_p16": 0.014, "nfpp_p84": 0.027}
    assert "NFPP after: 0.019 (16th to 84th percentile 0.014 to 0.027) over 20 runs" in ln.render(
        R2_RESULT, sigma, rows, after)
    assert ("The shares after the rule are a floor on the light each neighbour puts in the target's 3 x 3 box, and "
            "that box light, with the NFPP it drives, is an upper bound for the KBonus PSF photometry") in text
    assert text.rstrip().endswith(ln.tc.PRF_CAVEAT) and "Both columns" not in text
    assert "\u2014" not in text
    # Task 15 addendum 2: the budget's basis in three parts, in order, with the cap's state
    # Task 15 fix round 2, M6: in the engine's one wording (crosscheck.budget_basis), then where the code reads C
    assert ("The leakage budget behind the nearby false-positive probability has three parts. First, it comes from "
            "the catalogue, not from SkySift: (1 - C) / C = 0.1675, with C = 0.8565 the KBonus crowding metric") in text
    assert "14 percent of the fitted flux belongs to other sources, PRF wings included. Second, " in text
    assert ("Third, the overlap-weighted fluxes sum to 0.1549, below the budget of 0.1675, so the cap did not bind. "
            "`src/skysift/sources/kbonus_bkg.py` reads C into the target's") in text
    # round 3 review M5: the "keeps most of its round-2 share" clause only beyond two pixels and at half or more
    keeps = "keeps most of its round-2 share"
    assert keeps in text
    risen = [{**r, "share_before": r["share"] / 5} if r["id"] == "169822655" else r for r in rows]
    assert keeps in ln.render(R2_RESULT, sigma, risen)                    # a share that rose still kept most
    fell = [{**r, "share_before": r["share"] * 3} if r["id"] == "169822655" else r for r in rows]
    assert keeps not in ln.render(R2_RESULT, sigma, fell)
    near = [{**r, "sep_pix": 1.5} if r["id"] == "169822655" else r for r in rows]
    neutral = ln.render(R2_RESULT, sigma, near)
    assert keeps not in neutral and "The largest share after the rule is 0.138 (TIC 169822655" in neutral
    # round 3 review M1: an added star (no sep_arcsec) is listed and weighted at the separation its overlap used
    added = {"id": "2073896294000000001", "gaia_source_id": "2073896294000000001", "sep_arcsec": None,
             "overlap_sep_arcsec": 9.0, "gmag": 18.0, "kepler_flux": 0.2, "share": 1e-4}
    more = {**R2_RESULT, "stars": [*R2_RESULT["stars"], added]}
    listed = {r["id"]: r for r in ln.note_rows(more, SIGMA_5)}
    assert listed["2073896294000000001"]["sep_arcsec"] == 9.0
    assert listed["2073896294000000001"]["overlap"] == pytest.approx(tc.overlap(9.0, SIGMA_5))


# ---------------------------------------------------------------------------------------------------- Task 15

def test_pixel_excluded_takes_rung_5s_vote_for_its_neighbour_and_the_bound_for_the_others():
    out = cc.pixel_excluded(_live())
    assert out["seasons"] == [0, 1, 2, 3] and out["reason"] is None and out["sigma"] == 3.0
    assert out["stamp_source_id"] == "2073896190925687296"          # 11.45 arcsec against the stamp's 11.53
    rows = {n["source_id"]: n for n in out["neighbours"]}
    stamp = rows["2073896190925687296"]
    assert [m["basis"] for m in stamp["margins"]] == ["rung 5 vote"] * 4
    assert [m["margin_sigma"] for m in stamp["margins"]] == pytest.approx([27.845078, 19.814692, 17.424733,
                                                                           12.657602])
    near = rows["2073896294004902144"]                                # 7.14 arcsec: D / 2 = 0.8973 px
    assert [m["basis"] for m in near["margins"]] == ["geometric bound"] * 4
    # Task 15 fix round 2, M1: over the larger axis error (across the stamp axis in seasons 0, 1 and 3)
    assert [m["margin_sigma"] for m in near["margins"]] == pytest.approx([10.09, 9.27, 9.93, 6.20], abs=0.01)
    assert near["min_margin_sigma"] == pytest.approx(6.20, abs=0.01) and near["reason"] is None
    # 10.94 arcsec lies within 1 arcsec of the stamp separation too, but 11.45 is nearer: the bound for it
    other = rows["2073896289692192128"]["margins"][0]
    assert other["basis"] == "geometric bound" and other["margin_sigma"] == pytest.approx(17.00, abs=0.01)
    assert out["excluded_ids"] == [n["source_id"] for n in LIVE_NEIGHBOURS]
    assert all(n["excluded"] for n in rows.values()) and out["rule"] == cc.EXCLUDE_RULE


def test_a_season_without_a_vote_keeps_every_neighbour_and_no_voting_season_excludes_nothing():
    holed = [*LIVE_SEASONS[:3], {**LIVE_SEASONS[3], "dist_target_pix": None, "separation_sigma": None}]
    out = cc.pixel_excluded(_live(holed))
    assert out["excluded_ids"] == []
    rows = {n["source_id"]: n for n in out["neighbours"]}
    assert rows["2073896294004902144"]["reason"] == "season 3 has no centroid distance or error"
    assert rows["2073896190925687296"]["reason"] == "season 3 has no rung 5 vote against this neighbour"
    assert rows["2073896294004902144"]["excluded"] is False
    low = [*LIVE_SEASONS[:3], {**LIVE_SEASONS[3], "centroid_sigma_axis_pix": 0.5}]  # (0.8973 - 0.1866) / 0.5 = 1.42
    out = cc.pixel_excluded(_live(low))
    assert "2073896294004902144" not in out["excluded_ids"] and "2073896190925687296" in out["excluded_ids"]
    assert next(n for n in out["neighbours"] if n["source_id"] == "2073896294004902144")["reason"] == (
        "season 3 margin 1.42 sigma, not above 3")
    none = cc.pixel_excluded(_live([{**s, "voting": False} for s in LIVE_SEASONS]))
    assert none["excluded_ids"] == [] and none["reason"] == "rung 5 has no voting season"
    assert cc.pixel_excluded({"tests": [], "fpp": {"inputs": {"neighbours": LIVE_NEIGHBOURS}}})["reason"] == (
        "rung 5 has not run")


def test_the_export_lists_the_pixel_excluded_neighbours(env, tmp_path):
    cc.export_inputs(env.store, env.blob, env.settings, STAGE2_TARGET, tmp_path / "cc", fetch=env.xfetch)
    inputs = json.loads((tmp_path / "cc" / "inputs.json").read_text(encoding="utf-8"))
    assert inputs["pixel_excluded"] == cc.pixel_excluded(_stored(env))
    # Task 15 addendum 2: the tool prints the budget's catalogue column from the export
    assert inputs["target"]["crowdsap_column"] == "crowdsap"
    assert inputs["target"]["crowdsap_catalogue"] == "KBonus-bkg v1.0 source catalogue (Martinez-Palomera et al. 2023)"


def test_tool_maps_the_excluded_neighbours_to_the_stars_it_removes():
    from tools import triceratops_check as tc
    px = {"excluded_ids": ["g1", "g2", "g3"],
          "neighbours": [{"source_id": "g1", "sep_arcsec": 7.14, "min_margin_sigma": 7.07},
                         {"source_id": "g2", "sep_arcsec": 11.45, "min_margin_sigma": 12.66},
                         {"source_id": "g3", "sep_arcsec": 18.6, "min_margin_sigma": 21.4}]}
    removed, notes = tc.excluded_stars(px, [{"source_id": "g2", "tic_id": "169822655", "dsep_arcsec": 0.24}],
                                       {"g1": 19.3})
    assert removed == [{"tic_id": "g1", "source_id": "g1", "sep_arcsec": 7.14, "min_margin_sigma": 7.07},
                       {"tic_id": "169822655", "source_id": "g2", "sep_arcsec": 11.45, "min_margin_sigma": 12.66}]
    assert notes == [("Gaia DR3 g3 is excluded by the difference images but is not in the run's star list, so "
                      "there is nothing to remove.")]
    assert tc.excluded_stars(None, [], {}) == ([], []) and tc.excluded_stars({"excluded_ids": []}, [], {}) == ([], [])
    assert tc.VARIANT_KEYS == cc.VARIANT_KEYS


def test_tool_result_carries_the_pixel_excluded_variant_and_passes_the_import():
    from tools import triceratops_check as tc
    table = [{"ID": "t", "scenario": "TP", "prob": 0.92}, {"ID": "t", "scenario": "STP", "prob": 0.05}]
    runs = [tc.run_record(1 + k, 0.08, 0.02, False, table) for k in range(20)]
    ex = [tc.run_record(1 + k, 0.055, 0.0, False, [{"ID": "t", "scenario": "TP", "prob": 0.945},
                                                    {"ID": "t", "scenario": "STP", "prob": 0.05}]) for k in range(20)]
    removed = [{"tic_id": "169822655", "source_id": "g2", "sep_arcsec": 11.45, "min_margin_sigma": 12.66}]
    settings = {"N": 1_000_000, "field_stars": {"file": "p.csv", "n_rows": 10, "sha256": "c" * 64}}
    result = tc.assemble_result(runs, [], settings, {"fingerprint": "a" * 64}, "1.1.0",
                                variant=tc.variant_block(ex, removed, False))
    assert result["schema"] == 3 and tuple(result) == cc.RESULT_KEYS
    v = result["variant_excluded"]
    assert tuple(v) == (*cc.VARIANT_KEYS, "not_in_star_list") and v["not_in_star_list"] == []
    assert v["removed"] == removed and v["seeds"] == list(range(1, 21))
    assert v["fpp"] == pytest.approx(0.055) and v["nfpp"] == 0.0 and v["same_as_all"] is False
    cc.validate_result(result)
    same = tc.assemble_result(runs, [], settings, {"fingerprint": "a" * 64}, "1.1.0",
                              variant=tc.variant_block(runs, [], True))
    assert same["variant_excluded"]["fpp"] == same["fpp"] and same["variant_excluded"]["removed"] == []
    cc.validate_result(same)


def test_tool_checkpoint_resumes_the_second_set(tmp_path):
    from tools import triceratops_check as tc
    key = tc.checkpoint_key("a" * 64, seed=1, draws=2000, check_draws=2000, parallel_check=True, flux_err=0.001,
                            shares={"source": tc.OVERLAP}, quarters=[1], allow_old_export=False, version="1.1.0",
                            excluded_tic_ids=["1879962333", "169822655"])
    assert key["excluded_tic_ids"] == ["169822655", "1879962333"]                         # sorted
    state = tc.new_state()
    assert state["runs_excluded"] == []
    state["runs"] = [tc.run_record(1 + k, 0.1, 1e-4, False, []) for k in range(3)]
    state["runs_excluded"] = [tc.run_record(1 + k, 0.05, 0.0, False, []) for k in range(2)]
    part = tmp_path / "r.partial.json"
    tc.write_partial(part, key, state)
    back = tc.resume_state(part, key)
    assert back["runs_excluded"] == state["runs_excluded"] and tc.checkpoint_runs(part) == 3
    with pytest.raises(ValueError, match=r"\(excluded_tic_ids differ\)"):
        tc.resume_state(part, {**key, "excluded_tic_ids": []})
    tc.write_partial(part, key, {**state, "runs": state["runs"][:1]})
    with pytest.raises(ValueError, match="the checkpoint's second set runs ahead of its first"):
        tc.resume_state(part, key)


def test_the_import_requires_the_pixel_excluded_variant():
    good = fake_result("a" * 64)
    cc.validate_result(good)
    with pytest.raises(cc.BadResult, match="the result file lacks variant_excluded"):
        cc.validate_result({k: v for k, v in good.items() if k != "variant_excluded"})
    v = good["variant_excluded"]
    with pytest.raises(cc.BadResult, match="variant_excluded's seeds are not the first set's"):
        cc.validate_result({**good, "variant_excluded": {**v, "seeds": list(range(2, 22))}})
    with pytest.raises(cc.BadResult, match="variant_excluded says same_as_all but its runs differ from the first "
                                           "set's"):
        cc.validate_result({**good, "variant_excluded": {**v, "same_as_all": True}})
    with pytest.raises(cc.BadResult, match=r"variant_excluded\.fpp 0\.5 is not the non-degenerate runs' value"):
        cc.validate_result({**good, "variant_excluded": {**v, "fpp": 0.5}})
    with pytest.raises(cc.BadResult, match="variant_excluded.removed must list"):
        cc.validate_result({**good, "variant_excluded": {**v, "removed": [{"tic_id": "1"}]}})
    stored = cc.with_current({**good, "current": True}, DATA)
    assert stored["class_excluded"] == cc.classify(v["fpp"], v["nfpp"])


def _four(fpp_ex: float = 0.055, nfpp_ex: float = 0.0, removed=None, same: bool = False) -> dict:
    sha = cc.inputs_fingerprint(DATA)
    variant = fake_variant(fpp=fpp_ex, nfpp=nfpp_ex, p_stp=0.05, removed=removed)
    if same:
        variant = {**fake_variant(fpp=0.08, nfpp=0.02, p_stp=0.05, removed=[]), "same_as_all": True}
    block = {**fake_result(sha, fpp=0.08, nfpp=0.02, p_stp=0.05, variant=variant), "current": True}
    return {**DATA, "fpp": {"value": 0.0035}, "cross_check": block,
            "centroid_offset": {"exclusion_radius_arcsec": 0.83480394}}


def test_every_text_carries_the_four_numbers():
    data = _four()
    assert cc.headline(data) == (
        "TRICERATOPS 1.1.0 gives a false-positive probability of 0.080 (0.032 without a planet on an unresolved bound "
        "companion), and 0.055 (0.0053 without it) with the one neighbour the difference images exclude removed, in "
        "none of its validation classes, which do not apply at 30-minute cadence (Giacalone et al. 2021)")
    # Task 15 fix round 1, cut A1: the abstract carries the two FPPs; the no-STP values are the body's
    assert cc.headline(data, short=True) == (
        "TRICERATOPS 1.1.0 gives a false-positive probability of 0.080, or 0.055 with the one neighbour the "
        "difference images exclude removed (no validation class applies at 30-minute cadence; Giacalone et al. 2021)")
    assert cc.headline(_four(same=True), short=True) == (
        "TRICERATOPS 1.1.0 gives a false-positive probability of 0.080, the same with no neighbour removed, since the "
        "difference images exclude none (no validation class applies at 30-minute cadence; Giacalone et al. 2021)")
    assert "without" not in cc.headline(data, short=True)
    after = cc.comparison_sentence(data, after_headline=True)
    assert ("With the one neighbour the difference images exclude removed (Gaia DR3 2073896190925687296 at 11.45 "
            "arcsec, smallest margin 12.7 sigma), 20 seeded runs give 0.055 (16th to 84th percentile 0.055 to "
            "0.055), 0.0053 (0.0053 to 0.0053) without STP and a nearby false-positive probability of 0 (0 to "
            "0).") in after
    assert after.endswith(
        "With those neighbours removed the false-positive weight sits mainly on STP, a planet transiting an "
        "unresolved bound companion inside the 0.83 arcsec exclusion radius, which no high-resolution imaging "
        "constrains, so the signal stays a candidate.")
    # Task 16's cut 2 and Task 15 fix round 1, cut B8: the note's variant sits in the no-STP parenthesis
    assert ("gives a median false-positive probability of 0.080 (0.032 without a planet transiting an unresolved "
            "bound companion, STP, absent from SkySift's model; 0.055, and 0.0053 without STP, with the one neighbour "
            "the difference images exclude removed) and a nearby false-positive probability of 0.020 over 20 runs "
            "(no validation class applies at 30-minute cadence).") in cc.comparison_sentence(data, short=True)
    assert ("absent from SkySift's model; the same with no neighbour removed, since the difference images exclude "
            "none) and a nearby") in cc.comparison_sentence(_four(same=True), short=True)
    assert cc.dominant_excluded_sentence(data, short=True) == (
        "The remaining weight is mainly STP, a planet on an unresolved bound companion inside the 0.83 arcsec "
        "exclusion radius, which no imaging constrains, so the signal stays a candidate.")
    six = _four(removed=REMOVED * 6)
    assert "with the 6 neighbours the difference images exclude removed" in cc.headline(six)
    assert "and the same with no neighbour removed, since the difference images exclude none" in cc.headline(
        _four(same=True))
    assert cc.dominant_excluded_sentence({**data, "cross_check": {**data["cross_check"], "current": False}}) is None
    no_radius = {**data, "centroid_offset": None}
    assert "within 2.2 arcsec, which no high-resolution imaging" in cc.dominant_excluded_sentence(no_radius)
    assert "—" not in after



def test_the_texts_say_which_fpp_rests_on_our_own_evidence_and_trace_the_budget():
    """Task 15 addendum 2: the own-evidence sentence in the note's and the after-headline forms, and the leakage
    budget's three-part basis from the result's flux shares, with the catalogue column and the cap's state."""
    data = _four()
    own = ("The FPP with the pixel-excluded neighbours removed (0.055) is the number that rests on our own evidence "
           "(the difference-image centroids); the all-neighbours FPP (0.080) rests on the catalogue's crowding metric "
           "split by the PRF overlap rule, a bound rather than a measurement.")
    assert cc.own_evidence_sentence(data) == own
    assert own in cc.comparison_sentence(data, after_headline=True)
    assert own not in cc.comparison_sentence(data, short=True) and own not in cc.comparison_sentence(data)
    # Task 15 fix round 1, cut B2: the note's form
    short = ("The pixel-excluded value (0.055, mainly STP) rests on our own evidence, the difference-image "
             "centroids; the all-neighbours value (0.080) rests on the catalogue's crowding metric split by the PRF "
             "overlap rule, a bound rather than a measurement.")
    assert cc.own_evidence_sentence(data, short=True) == short
    assert cc.comparison_sentence(data, short=True).count(short) == 1
    assert cc.own_evidence_sentence(_four(same=True)) is None
    assert cc.own_evidence_sentence(_four(same=True), short=True) is None
    assert cc.leakage_basis(data) is None                               # the fake result stores no flux shares
    shares = {"source": "Kepler-pixel overlap, capped by the contamination budget", "budget": 0.16753213,
              "crowdsap": 0.85650748, "sigma_pix": 0.5695906, "weight_sum": 0.1548311, "capped": False}
    block = {**data["cross_check"], "settings": {**data["cross_check"]["settings"], "flux_shares": shares}}
    basis = cc.leakage_basis({**data, "cross_check": block})
    assert basis.startswith("The leakage budget behind the nearby false-positive probability has three parts. First, "
                            "it comes from the catalogue, not from SkySift: (1 - C) / C = 0.1675, with C = 0.8565 the "
                            "KBonus crowding metric for this extraction (the crowdsap column of the KBonus-bkg v1.0 "
                            "source catalogue (Martinez-Palomera et al. 2023)), the catalogue's own estimate that 14 "
                            "percent of the fitted flux belongs to other sources, PRF wings included. Second, its "
                            "split among the neighbours follows the PRF overlap rule")
    assert "(0.570 Kepler pixels)" in basis
    assert basis.endswith("Third, the overlap-weighted fluxes sum to 0.1548, below the budget of 0.1675, so the cap "
                          "did not bind.")
    bound = cc.leakage_basis({**data, "cross_check": {**block, "settings": {**block["settings"], "flux_shares": {
        **shares, "crowdsap": 0.95, "budget": 0.05263158, "capped": True}}}})
    assert bound.endswith("Third, the overlap-weighted fluxes sum to 0.1548, above the budget, so the cap bound and "
                          "they were scaled down to it.")
    for text in (own, basis):
        assert "\u2014" not in text and "discover" not in text.lower() and "validated" not in text


# ------------------------------------------------------------------------------- Task 15 fix round 2 (review minors)

def test_the_geometric_bound_divides_by_the_larger_axis_error():
    """M1: the bound over the larger of the two axis errors (along the stamp axis, and across it from the per-axis
    mean), so a neighbour the smaller axis would exclude stays in the list when the larger does not."""
    from skysift.priors import PIXEL_ARCSEC
    season = {"season": 0, "voting": True, "dist_target_pix": 0.1, "centroid_sigma_axis_pix": 0.1,
              "centroid_sigma_pix": 0.15}
    across = math.sqrt(2 * 0.15 ** 2 - 0.1 ** 2)
    assert cc.larger_axis_sigma(season) == pytest.approx(across)
    assert cc.larger_axis_sigma({**season, "centroid_sigma_pix": 0.1}) == pytest.approx(0.1)   # isotropic
    assert cc.larger_axis_sigma({**season, "centroid_sigma_pix": None}) is None
    sep = 0.9 * PIXEL_ARCSEC                    # D / 2 = 0.45 px: (0.45 - 0.1) / 0.1 = 3.5 along the stamp axis
    margin, basis, why = cc.season_margin(season, sep, stamp=False)
    assert basis == "geometric bound" and why is None and margin == pytest.approx(0.35 / across)
    assert margin < cc.EXCLUDE_SIGMA < 0.35 / 0.1
    data = {"tests": [{"rung": "rung5_pixels", "status": "pass", "metrics": {"seasons": [season]}}],
            "fpp": {"inputs": {"neighbours": [{"source_id": "9", "sep_arcsec": sep, "gmag": 18.0}]}}}
    assert cc.pixel_excluded(data)["excluded_ids"] == []
    assert "the larger axis error" in cc.EXCLUDE_RULE and "whatever its direction" in cc.EXCLUDE_RULE


def _absent(fpp: float = 0.08) -> dict:
    """``_four`` with a pixel-excluded set that removed nothing because the star list lacks every excluded
    neighbour."""
    data = _four(same=True)
    block = data["cross_check"]
    variant = {**block["variant_excluded"], "not_in_star_list": ["2073896190925687296"]}
    if fpp != 0.08:
        sha = cc.inputs_fingerprint(DATA)
        variant = {**fake_variant(fpp=fpp, nfpp=0.02, p_stp=0.05, removed=[]), "same_as_all": True,
                   "not_in_star_list": ["2073896190925687296"]}
        table = [{"ID": "169822650", "scenario": "TP", "prob": 1 - fpp}, {"ID": "169822650", "scenario": "STP",
                                                                         "prob": fpp}]
        block = {**fake_result(sha, fpp=fpp, nfpp=0.02, p_stp=0.05, scenarios=table, variant=variant),
                 "current": True}
    return {**data, "cross_check": {**block, "variant_excluded": variant}}


def test_an_excluded_set_the_star_list_lacks_is_not_called_empty():
    """M3: when every neighbour the difference images exclude is absent from TRICERATOPS's star list, the texts say
    so and never "exclude none"; the result's optional ``not_in_star_list`` is validated."""
    data = _absent()
    texts = [cc.headline(data), cc.headline(data, short=True), cc.comparison_sentence(data, after_headline=True),
             cc.comparison_sentence(data, short=True), cc.comparison_sentence(data)]
    for text in texts:
        assert cc.NOT_IN_STAR_LIST in text or cc.NOT_IN_STAR_LIST[:1].upper() + cc.NOT_IN_STAR_LIST[1:] in text
        assert "exclude none" not in text and "exclude no neighbour" not in text
    assert cc.headline(data, short=True) == (
        "TRICERATOPS 1.1.0 gives a false-positive probability of 0.080, and none of the neighbours the difference "
        "images exclude is in TRICERATOPS's star list, so the pixel-excluded run equals the first (no validation "
        "class applies at 30-minute cadence; Giacalone et al. 2021)")
    cc.validate_result({k: v for k, v in data["cross_check"].items() if k != "current"})
    bad = {**data["cross_check"], "variant_excluded": {**data["cross_check"]["variant_excluded"],
                                                       "not_in_star_list": [7]}}
    with pytest.raises(cc.BadResult, match="not_in_star_list must list Gaia DR3 source ids"):
        cc.validate_result({k: v for k, v in bad.items() if k != "current"})
    from tools import triceratops_check as tc
    runs = [tc.run_record(1 + k, 0.1, 1e-4, False, []) for k in range(3)]
    assert tc.variant_block(runs, [], True, ["5"])["not_in_star_list"] == ["5"]


def test_with_nothing_removed_the_note_does_not_speak_of_remaining_weight():
    """M4: the note's short sentence with nothing removed says the two values coincide, and says nothing when the
    first set's dominant sentence (an FPP at or above 0.5) has named the same scenario."""
    for data in (_four(same=True), _absent()):
        short = cc.dominant_excluded_sentence(data, short=True)
        assert short.startswith("With no neighbour removed the two values coincide; the weight is mainly STP, ")
        assert "remaining" not in short
    assert cc.dominant_excluded_sentence(_absent(fpp=0.6), short=True) is None
    assert cc.dominant_excluded_sentence(_absent(fpp=0.6)).startswith("The false-positive weight sits mainly on")


def test_the_note_names_the_excluded_sets_dominant_scenario_in_the_own_evidence_sentence():
    """M2: two words inside the note's own-evidence sentence, the scenario's short name from the result."""
    data = _four()
    assert cc.own_evidence_sentence(data, short=True).startswith("The pixel-excluded value (0.055, mainly STP) rests")
    neb = {**data, "cross_check": {**data["cross_check"], "variant_excluded": {
        **data["cross_check"]["variant_excluded"],
        "scenarios": [{"ID": "1", "scenario": "TP", "prob": 0.5}, {"ID": "2", "scenario": "NEB", "prob": 0.4}]}}}
    assert "(0.055, mainly NEB)" in cc.own_evidence_sentence(neb, short=True)
    assert "mainly" not in cc.own_evidence_sentence(data)                     # the long form is unchanged


def test_the_basis_is_worded_once_and_the_tool_names_the_same_source():
    """M6: the README's, the page's and the note's basis come from ``crosscheck.budget_basis``; the tool's
    ``OVERLAP`` source starts with the engine's ``OVERLAP_SOURCE``, which ``leakage_basis`` keys on."""
    from skysift.sources.kbonus_bkg import CATALOGUE, CROWDSAP_COLUMN
    from tools import leakage_note
    from tools import triceratops_check as tc
    assert tc.OVERLAP.startswith(cc.OVERLAP_SOURCE)
    budget = (1 - CROWDSAP_6146676) / CROWDSAP_6146676
    basis = cc.budget_basis(CROWDSAP_6146676, budget, 0.1549, False, SIGMA_5, CROWDSAP_COLUMN, CATALOGUE)
    assert leakage_note._basis(CROWDSAP_6146676, budget, 0.1549, SIGMA_5).startswith(basis)
    caveat = tc.leakage_caveat({"source": tc.OVERLAP, "budget": budget, "capped": False, "sigma_pix": SIGMA_5,
                                "weight_sum": 0.1549}, CROWDSAP_6146676,
                               {"crowdsap_column": CROWDSAP_COLUMN, "crowdsap_catalogue": CATALOGUE})
    column = f"the {CROWDSAP_COLUMN} column of the {CATALOGUE}"
    for part in (f"(1 - C) / C = {budget:.4f}", f"C = {CROWDSAP_6146676:.4f}", column, "14 percent of the fitted flux",
                 "the cap did not bind"):
        assert part in basis and part in caveat, part


class _FakeTarget:
    """TRICERATOPS's target as ``run_sets`` uses it: calc_probs through ``probs``, the FPP, NFPP, degenerate flag
    and probability table it leaves, and ``remove_star``."""

    def __init__(self):
        self.calls, self.removed = [], []
        self.FPP, self.NFPP, self.FPP_degenerate = 0.1, 1e-4, False

    class _Table:
        def __getitem__(self, _cols):
            return self

        def to_json(self, orient):
            return json.dumps([{"ID": "1", "scenario": "TP", "prob": 0.9}, {"ID": "1", "scenario": "STP",
                                                                            "prob": 0.1}])

    probs = _Table()

    def remove_star(self, ids):
        self.removed.append(list(ids))


def test_a_resume_with_the_first_set_done_runs_only_the_rest_of_the_second(tmp_path, capsys):
    """M8: a checkpoint with the first set complete and the pixel-excluded set at run 7 of 20 resumes, through the
    run loop both sets share (M6), into exactly 13 more pixel-excluded runs on seeds 8 to 20, with the star removed
    once and none of the first set's runs repeated; the checkpoint then holds both sets whole."""
    from tools import triceratops_check as tc
    key = tc.checkpoint_key("a" * 64, seed=1, draws=2000, check_draws=2000, parallel_check=False, flux_err=0.001,
                            shares={"source": "x"}, quarters=[1], allow_old_export=False, version="1.1.0",
                            excluded_tic_ids=["169822655"])
    state = tc.new_state()
    state["runs"] = [tc.run_record(1 + k, 0.2, 1e-4, False, []) for k in range(20)]
    state["runs_excluded"] = [tc.run_record(1 + k, 0.1, 1e-4, False, []) for k in range(7)]
    partial = tc.partial_path(tmp_path / "r.json")
    tc.write_partial(partial, key, state)
    assert tc.checkpoint_runs(partial) == 20
    resumed = tc.resume_state(partial, key)
    target = _FakeTarget()

    def probs(seed, draws, parallel):
        target.calls.append((seed, draws, parallel))
    removed = [{"tic_id": "169822655", "source_id": "2073896190925687296", "sep_arcsec": 11.45,
                "min_margin_sigma": 12.66}]
    variant = tc.run_sets(target, resumed, probs, removed, seed=1, n_runs=20, draws=2000,
                          write=lambda: tc.write_partial(partial, key, resumed))
    assert [c[0] for c in target.calls] == list(range(8, 21)) and len(target.calls) == 13
    assert target.removed == [["169822655"]]
    assert variant["n_runs"] == 20 and variant["seeds"] == list(range(1, 21)) and variant["same_as_all"] is False
    saved = tc.resume_state(partial, key)
    assert len(saved["runs"]) == 20 and len(saved["runs_excluded"]) == 20
    err = capsys.readouterr().err
    assert "pixel-excluded run 8/20 (seed 8, parallel)" in err and "\nrun " not in f"\n{err}"
