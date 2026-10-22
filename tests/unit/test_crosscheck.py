"""The cross-check (referee pack spec 4): the inputs fingerprint, the export the TRICERATOPS tool reads, the import of
its result into ``dossiers.cross_check``, the verdict the live gate reads and the pack's comparison sentence; and the
tool's pure helpers, which run here under the engine's interpreter (no test calls TRICERATOPS)."""
import csv
import importlib
import json
import math
from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits

from skysift import crosscheck as cc
from skysift.dossier import generate
from skysift.lc.tpf import tpf_listing_url
from skysift.store.db import TargetRow
from tests.unit.synthetic_tpf import CCD_ORIGIN, NEIGHBOUR_KIC, tpf_fits, tpf_name, tpf_quarters
from tests.unit.test_dossier import _make_env
from tests.unit.ui_seed import STAGE2_TARGET

DATA = {"ephemeris": {"period_days": 40.29, "t0_bkjd": 136.3, "depth_ppm": 2180.0, "duration_h": 4.16},
        "target": {"gmag": 16.2},
        "fpp": {"inputs": {"neighbours": [{"source_id": "2", "sep_arcsec": 10.8, "gmag": 12.8, "bp_rp": 1.1},
                                          {"source_id": "1", "sep_arcsec": 7.1, "gmag": 19.7, "bp_rp": 1.8}]}}}


def fake_result(sha: str, fpp: float = 0.012, nfpp: float = 1e-4, n: int = cc.N_RUNS) -> dict:
    """A result file as the tool writes it: ``n`` runs whose median is ``fpp`` and ``nfpp``."""
    return {"tool": cc.TOOL, "tool_version": "1.1.0", "run_utc": "2026-10-22T12:00:00+00:00", "n_runs": n,
            "fpp": fpp, "nfpp": nfpp, "fpp_runs": [fpp] * n, "nfpp_runs": [nfpp] * n,
            "scenarios": [{"ID": "169822650", "scenario": "TP", "prob": 1 - fpp}],
            "stars": [{"id": "169822650", "tmag": 15.19, "sep_arcsec": 0.0, "flux_ratio": 0.98, "tdepth": 0.0022}],
            "inputs_sha256": sha,
            "caveats": [("TRICERATOPS shares the light in the aperture by TIC TESS magnitudes (Tmag), not Kepler "
                         "magnitudes; its limb darkening comes from its Kepler table.")],
            "settings": {"N": 1_000_000, "exptime_days": 0.0204}}


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
    """The seed's stage-2 dossier with the pixel files of ``store_pixel_files``."""
    env = _make_env(settings, monkeypatch)
    store_pixel_files(env)
    env.report = generate(env.store, env.blob, env.settings, STAGE2_TARGET, fetch=env.fetch)
    return env


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
    out = cc.export_inputs(env.store, env.blob, env.settings, STAGE2_TARGET, tmp_path / "cc")
    data = _stored(env)
    inputs = json.loads((out / "inputs.json").read_text(encoding="utf-8"))
    assert inputs["fingerprint"] == cc.inputs_fingerprint(data)
    assert inputs["target"]["target_id"] == STAGE2_TARGET and inputs["target"]["ra"] == data["target"]["ra"]
    assert inputs["ephemeris"] == data["ephemeris"] and "period_err_days" in inputs["ephemeris"]
    assert inputs["star"] == data["star"] and inputs["neighbour_kic"] == NEIGHBOUR_KIC
    assert inputs["neighbours"] == [{k: n[k] for k in ("source_id", "sep_arcsec", "gmag", "bp_rp")}
                                    for n in data["fpp"]["inputs"]["neighbours"]] and inputs["neighbours"]
    assert inputs["quarters"] == [1, 2, 3, 4, 5]
    assert inputs["tpf_files"] == [f"tpf/q{q:02d}.fits.gz" for q in (1, 2, 3, 4, 5)]
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
    assert inputs["lightcurve"]["n"] == len(rows) and inputs["lightcurve"]["window_durations"] == 3
    flux = np.array([float(r["flux_rel"]) for r in rows])
    assert abs(np.median(flux) - 1.0) < 0.01 and flux.min() < 1 - 0.001          # normalised, the transits inside
    assert {int(r["quarter"]) for r in rows} <= set(range(18)) and all(float(r["err_rel"]) > 0 for r in rows)


def test_import_validates_and_stores_the_result(env, tmp_path):
    sha = cc.inputs_fingerprint(_stored(env))
    for bad, why in ((dict(fake_result(sha), fpp=1.5), "fpp"), (dict(fake_result(sha), nfpp=float("nan")), "nfpp"),
                     ({k: v for k, v in fake_result(sha).items() if k != "stars"}, "stars"),
                     (dict(fake_result(sha), tool="vespa"), "tool"),
                     (dict(fake_result(sha), fpp_runs=[0.012] * 3), "fpp_runs"),
                     (dict(fake_result(sha), fpp=0.2), "median"),
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
    assert block["current"] is True and set(block) == {*cc.RESULT_KEYS, "current"}
    assert env.store.dossier(STAGE2_TARGET)["cross_check"] == block and block["fpp"] == 0.012
    assert cc.verdict(block) == (False, None)


def test_a_cross_check_against_another_fingerprint_is_not_current(env, tmp_path):
    out = cc.export_inputs(env.store, env.blob, env.settings, STAGE2_TARGET, tmp_path / "cc")
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


def test_verdict_blocks_a_likely_false_positive_and_passes_a_small_disagreement():
    sha = "a" * 64
    block = {**fake_result(sha), "current": True}
    assert cc.verdict(None) == (True, "no independent cross-check")
    assert cc.verdict({**block, "current": False}) == (True, "the cross-check was made for an earlier dossier")
    assert cc.verdict({**block, "fpp": 0.5}) == (True, "the cross-check calls it a likely false positive")
    assert cc.verdict({**block, "nfpp": 0.1}) == (True, "the cross-check calls it a likely nearby false positive")
    assert cc.verdict({**block, "fpp": 0.49, "nfpp": 0.099}) == (False, None)
    assert cc.verdict(block) == (False, None)                                    # 0.012 against SkySift's 0.0035
    data = {"fpp": {"value": 0.0035}, "cross_check": block}
    text = cc.comparison_sentence(data)
    assert text.startswith("An independent estimate with TRICERATOPS (Giacalone et al. 2021) gives a false-positive "
                           "probability of 0.012 and a nearby false-positive probability of 1 in 10,000")
    assert "e-0" not in text and "e-0" not in cc.comparison_sentence(data, tex=True)          # never 1.0e-04 (M9)
    # SkySift's estimate is provisional until signed off, as the other sentences say
    assert "SkySift's own estimate (provisional until its factor tables are reviewed) is 0.0035" in text
    assert "a factor of 3.4 apart" in text
    signed = cc.comparison_sentence({"fpp": {"value": 0.0035, "signed_off": True}, "cross_check": block})
    assert "SkySift's own estimate is 0.0035" in signed and "provisional" not in signed
    assert [cc.probability_text(x) for x in (0.012, 0.0035, 1.2e-4, 1e-4, 0.0)] == [
        "0.012", "0.0035", "1 in 8,300", "1 in 10,000", "0"]
    assert "answer different questions" in text and "marginalises over an unresolved companion population" in text
    assert chr(0x2014) not in text and "discover" not in text.lower()
    close = cc.comparison_sentence({"fpp": {"value": 0.01}, "cross_check": block})
    assert "0.010" in close and "a factor of" not in close and "different questions" not in close
    tex = cc.comparison_sentence(data, tex=True)
    assert r"TRICERATOPS \citep{giacalone2021} gives" in tex and "Giacalone et al." not in tex
    assert cc.comparison_sentence({"fpp": {"value": 0.0035}, "cross_check": None}) is None
    assert cc.comparison_sentence({"fpp": {"value": 0.0035}, "cross_check": {**block, "current": False}}) is None
    zeros = cc.comparison_sentence({"fpp": {"value": 0.0}, "cross_check": {**block, "fpp": 0.0, "fpp_runs": [0.0]}})
    assert "estimate (provisional until its factor tables are reviewed) is 0." in zeros and "apart" not in zeros
    alone = cc.comparison_sentence({"fpp": {"value": None}, "cross_check": block})
    assert "SkySift" not in alone and alone.endswith("over 10 runs.")


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
    runs = [{"seed": 7 + k, "fpp": f, "nfpp": f / 10, "degenerate": False,
             "scenarios": [{"ID": "1", "scenario": "TP", "prob": 1 - f}]} for k, f in enumerate((0.03, 0.01, 0.02))]
    result = tc.assemble_result(runs, [{"id": "1", "tmag": 15.2}], {"N": 10}, {"fingerprint": "b" * 64}, "1.1.0")
    assert tuple(result) == cc.RESULT_KEYS and result["tool"] == cc.TOOL
    assert result["fpp"] == pytest.approx(0.02) and result["nfpp"] == pytest.approx(0.002)
    assert result["fpp_runs"] == [0.03, 0.01, 0.02] and result["scenarios"] == runs[2]["scenarios"]
    assert result["settings"]["seeds"] == [7, 8, 9] and result["settings"]["median_run_seed"] == 9
    assert any("Tmag" in c and "Kepler table" in c for c in result["caveats"]) and result["inputs_sha256"] == "b" * 64
    assert not any("TESS-band limb darkening" in c for c in result["caveats"])
    cc.validate_result(result)                                         # the tool's file passes the import's checks


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
    with pytest.raises(cc.OldDossier, match="dossier version 2 is below 3; regenerate first"):
        cc.export_inputs(env.store, env.blob, env.settings, STAGE2_TARGET, tmp_path / "cc")
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
    assert any("separation from the target only" in c for c in tc.CAVEATS)                    # M13


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
    env = _make_env(settings, monkeypatch)                  # the seed keeps no pixel file for this star
    generate(env.store, env.blob, env.settings, STAGE2_TARGET, fetch=env.fetch)
    with pytest.raises(cc.NoPixelFiles, match="tpf_missing: none is kept.*the folder the command runs in"):
        cc.export_inputs(env.store, env.blob, env.settings, STAGE2_TARGET, tmp_path / "cc")
    assert not (tmp_path / "cc" / "inputs.json").exists()
