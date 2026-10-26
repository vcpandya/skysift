"""tools/extinction.py (spec 12.3 as amended by the controller's addenda of 23 October 2026): the IRSA DUST full-column
E(B-V) parsed from its XML, the stored block with the 3D value "not available", the hand-stored value, and main's
refusals. ``net.fetch`` is mocked: no test reaches the network."""
import json
import math
import subprocess
from pathlib import Path

import pytest

from skysift import stars
from skysift.net import ServiceUnavailable
from tools import bayestar_map as bm
from tools import extinction as ex

# the shape of the service's answer, with the KIC 6146676 values it gave on 22 October 2026 (regSize 2 deg)
IRSA_OK = b"""<?xml version="1.0"?>
<results status="ok">
  <input>
    <objname>      297.868710 41.455570 equ J2000    </objname>
    <regSize>
      5.0 deg
    </regSize>
  </input>
  <result>
    <desc>
      E(B-V) Reddening
    </desc>
    <statistics>
      <refPixelValueSandF>
             0.6741 (mag)
      </refPixelValueSandF>
      <refPixelValueSFD>
             0.7839 (mag)
      </refPixelValueSFD>
      <meanValueSandF>
           0.6518 (mag)
      </meanValueSandF>
      <meanValueSFD>
           0.7579 (mag)
      </meanValueSFD>
      <stdSandF>
           0.0344 (mag)
      </stdSandF>
    </statistics>
  </result>
  <result>
    <desc>
      100 Micron Emission
    </desc>
    <statistics>
      <refPixelValue>
          50.5995 (MJy/sr)
      </refPixelValue>
    </statistics>
  </result>
</results>
"""
IRSA_ERROR = b"""<results status="error">
  <message>
    Coordinate [12] lookup error: Invalid object name.
  </message>
</results>
"""
TARGET = "kic-006146676"


def fake_fetch(answer, calls=None):
    """``net.fetch`` answering every GET with ``answer`` (bytes) or raising it (an exception); logs like the real one."""
    def fetch(url, *, params=None, service, store=None, attempts=5, timeout=60.0, **_kw):
        if calls is not None:
            calls.append({"url": url, "params": params, "service": service, "attempts": attempts})
        if isinstance(answer, Exception):
            if store:
                store.log_query(service, url, params or {}, None, 0, None, error=repr(answer))
            raise answer
        if store:
            store.log_query(service, url, params or {}, 200, len(answer), "0" * 64)
        return answer
    return fetch


def _dossier(data_dir, target=TARGET, route="mann", distance=303.0):
    folder = data_dir / "dossiers" / target
    folder.mkdir(parents=True)
    star = {"route": route, "distance_pc": {"median": distance} if distance is not None else None}
    (folder / "dossier_data.json").write_text(json.dumps({"target": {"ra": 297.86871, "dec": 41.455567},
                                                          "star": star}), encoding="utf-8")


def test_the_column_url_and_the_xml_give_the_pixel_and_mean_e_b_v():
    assert ex.column_url(297.86871, 41.455567) == ("https://irsa.ipac.caltech.edu/cgi-bin/DUST/nph-dust?"
                                                   "locstr=297.86871+41.455567+equ+j2000")
    assert ex.parse_column(IRSA_OK) == {"ebv": 0.6741, "ebv_mean": 0.6518, "ebv_std": 0.0344}
    with pytest.raises(ex.ColumnError, match="IRSA DUST answered status error: Coordinate \\[12\\] lookup error"):
        ex.parse_column(IRSA_ERROR)
    with pytest.raises(ex.ColumnError, match="does not read as XML"):
        ex.parse_column(b"<html>busy")
    with pytest.raises(ex.ColumnError, match="document type declaration; refused"):
        ex.parse_column(b'<!DOCTYPE r [<!ENTITY a "aaaa">]><results status="ok">&a;</results>')
    with pytest.raises(ex.ColumnError, match="no refPixelValueSandF"):
        ex.parse_column(IRSA_OK.replace(b"refPixelValueSandF", b"somethingElse"))


def test_query_column_stores_the_value_or_the_reason_and_asks_once():
    calls = []
    ok = ex.query_column(297.86871, 41.455567, fetch=fake_fetch(IRSA_OK, calls), now="2026-10-25T10:00:00+00:00")
    assert ok == {"status": "ok", "ebv": 0.6741, "ebv_mean": 0.6518, "ebv_std": 0.0344,
                  "stats_radius_arcmin": ex.COLUMN_STATS_ARCMIN, "source": stars_source(),
                  "queried_utc": "2026-10-25T10:00:00+00:00",
                  "ra": 297.86871, "dec": 41.455567, "url": ex.column_url(297.86871, 41.455567), "used": "pixel"}
    assert calls == [{"url": ex.column_url(297.86871, 41.455567), "params": None, "service": "irsa-dust",
                      "attempts": 1}]
    down = ex.query_column(297.86871, 41.455567, fetch=fake_fetch(ServiceUnavailable("irsa-dust timed out")),
                           now="t")
    assert down["status"] == "not available" and down["reason"] == "irsa-dust timed out" and "ebv" not in down
    error = ex.query_column(297.86871, 41.455567, fetch=fake_fetch(IRSA_ERROR), now="t")
    assert error["status"] == "not available" and error["reason"].startswith("IRSA DUST answered status error")
    for block in (ok, down, error):                    # each is a column the stars module accepts
        assert stars._block_problem({"status": "not available", "reason": "r", "queried_utc": "t",
                                     "column": block}) is None


def stars_source() -> str:
    return "IRSA DUST, Schlafly and Finkbeiner 2011"


def test_main_writes_the_column_and_the_3d_state_once(tmp_path, capsys):
    _dossier(tmp_path / "data")
    args = ["--target", TARGET, "--data-dir", str(tmp_path / "data"), "--root", str(tmp_path / "c")]
    calls = []
    assert ex.main(args, fetch=fake_fetch(IRSA_OK, calls)) == 3                  # written; the 3D value missing
    block = json.loads(ex.store_path(tmp_path / "c", TARGET).read_text(encoding="utf-8"))
    assert block["status"] == "not available"
    assert block["reason"].startswith("Bayestar19 (Green et al. 2019) not available: the Argonaut web query failed")
    assert block["target"] == TARGET and block["distance_pc"] == 303.0 and block["column"]["ebv"] == 0.6741
    assert stars.load_extinction(TARGET, tmp_path / "c") == block
    err = capsys.readouterr().err
    assert "GET https://irsa.ipac.caltech.edu/cgi-bin/DUST/nph-dust" in err and "-> 200" in err
    assert "E(B-V) full column 0.674" in err and "--store --ebv" in err and "--distance-pc 303" in err
    assert ex.main(args, fetch=fake_fetch(AssertionError("no second query"))) == 0
    assert "stored; nothing to do" in capsys.readouterr().err and len(calls) == 2
    assert ex.main([*args, "--force"], fetch=fake_fetch(ServiceUnavailable("irsa-dust down"))) == 3
    again = json.loads(ex.store_path(tmp_path / "c", TARGET).read_text(encoding="utf-8"))
    assert again["column"] == {**again["column"], "status": "not available", "reason": "irsa-dust down"}


def test_a_star_without_a_distance_or_a_dossier(tmp_path, capsys):
    _dossier(tmp_path / "data", route="none", distance=None)
    args = ["--target", TARGET, "--data-dir", str(tmp_path / "data"), "--root", str(tmp_path / "c")]
    assert ex.main(args, fetch=fake_fetch(AssertionError("no query"))) == 0
    assert "the star has no Gaia distance; nothing to store" in capsys.readouterr().err
    assert not ex.store_path(tmp_path / "c", TARGET).exists()
    other = ["--target", "kic-000000001", "--data-dir", str(tmp_path / "data"), "--root", str(tmp_path / "c")]
    assert ex.main(other, fetch=fake_fetch(AssertionError("no query"))) == 2
    assert "dossier_data.json is not there" in capsys.readouterr().err


def test_store_by_hand_writes_the_block_and_keeps_the_column(tmp_path, capsys):
    args = ["--target", TARGET, "--data-dir", str(tmp_path), "--root", str(tmp_path / "c")]
    hand = ["--store", "--ebv", "0.05", "--ebv-p16", "0.03", "--ebv-p84", "0.07", "--distance-pc", "303",
            "--source", "read off the Bayestar19 web page"]
    assert ex.main([*args, *hand], fetch=fake_fetch(AssertionError("no query"))) == 0
    block = json.loads(ex.store_path(tmp_path / "c", TARGET).read_text(encoding="utf-8"))
    assert (block["ebv"], block["map"], block["source"], block["status"]) == (
        0.05, "by hand", "read off the Bayestar19 web page", "manual")
    assert stars.load_extinction(TARGET, tmp_path / "c") == block and "column" not in block
    assert ex.main(args, fetch=fake_fetch(AssertionError("no query"))) == 0
    assert "stored; nothing to do" in capsys.readouterr().err
    # over a stored column, with --force: the 3D value is replaced and the column kept
    _dossier(tmp_path / "data")
    queried = ["--target", TARGET, "--data-dir", str(tmp_path / "data"), "--root", str(tmp_path / "q")]
    assert ex.main(queried, fetch=fake_fetch(IRSA_OK)) == 3
    assert ex.main([*queried, *hand, "--force"], fetch=fake_fetch(AssertionError("no query"))) == 0
    both = json.loads(ex.store_path(tmp_path / "q", TARGET).read_text(encoding="utf-8"))
    assert both["status"] == "manual" and both["column"]["ebv"] == 0.6741 and "reason" not in both
    assert ex.main([*args, "--store", "--ebv", "0.05", "--force"], fetch=fake_fetch(AssertionError("no query"))) == 2
    assert "--store needs --ebv, --ebv-p16, --ebv-p84, --distance-pc and --source" in capsys.readouterr().err
    assert ex.main([*args, *hand[:-2], "--source", "x", "--ebv-p16", "0.06", "--force"],
                   fetch=fake_fetch(AssertionError("no query"))) == 2
    assert "not 0 <= ebv_p16 <= ebv <= ebv_p84" in capsys.readouterr().err


def test_all_runs_each_live_target_and_returns_the_largest_exit_code(tmp_path, monkeypatch):
    assert ex.LIVE_TARGETS == ("kic-005689121", "kic-006146676", "kic-006699887", "kic-009243078", "kic-010352243")
    monkeypatch.setattr(ex, "LIVE_TARGETS", (TARGET, "kic-005689121"))
    _dossier(tmp_path / "data")
    calls = []
    assert ex.main(["--all", "--data-dir", str(tmp_path / "data"), "--root", str(tmp_path / "c")],
                   fetch=fake_fetch(IRSA_OK, calls)) == 3                   # kic-005689121 has no dossier: 2 < 3
    assert len(calls) == 2 and ex.store_path(tmp_path / "c", TARGET).is_file()


# ---------------------------------------------------------------------------------------------------- fix round 1

ARGONAUT_OK = json.dumps([0.05, 0.06, 0.07]).encode()       # Bayestar19's own units at the 16th, 50th, 84th percentile
NEUTRAL_WORDS = ("owner", "approval", "download")


def routed_fetch(argonaut, irsa, calls=None):
    """``net.fetch`` answering the Argonaut query with ``argonaut`` and IRSA with ``irsa`` (bytes, or raised)."""
    def fetch(url, *, params=None, service, store=None, attempts=5, timeout=60.0, **_kw):
        if calls is not None:
            calls.append({"service": service, "url": url, "params": params, "attempts": attempts, "timeout": timeout})
        answer = argonaut if service == "argonaut" else irsa
        if isinstance(answer, Exception):
            raise answer
        return answer
    return fetch


def _run(tmp_path, argonaut, irsa, calls=None, extra=()):
    if not (tmp_path / "data" / "dossiers" / TARGET).is_dir():
        _dossier(tmp_path / "data")
    args = ["--target", TARGET, "--data-dir", str(tmp_path / "data"), "--root", str(tmp_path / "c"), *extra]
    code = ex.main(args, fetch=routed_fetch(argonaut, irsa, calls))
    path = ex.store_path(tmp_path / "c", TARGET)
    return code, (json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None)


def test_an_argonaut_answer_is_the_adopted_3d_value_with_the_column_beside_it(tmp_path):
    calls = []
    code, block = _run(tmp_path, ARGONAUT_OK, IRSA_OK, calls)
    assert code == 0 and block["status"] == "adopted"
    assert (block["ebv"], block["ebv_p16"], block["ebv_p84"]) == (round(0.884 * 0.06, 4), round(0.884 * 0.05, 4),
                                                                  round(0.884 * 0.07, 4))
    assert block["source"] == "Bayestar19 (Green et al. 2019) via Argonaut" and block["distance_pc"] == 303.0
    assert block["column"]["ebv"] == 0.6741 and block["column"]["used"] == "pixel"
    assert [c["service"] for c in calls] == ["argonaut", "irsa-dust"]
    assert calls[0]["url"] == ex.ARGONAUT_URL and calls[0]["attempts"] == 1 and calls[0]["timeout"] == 20.0
    assert stars.load_extinction(TARGET, tmp_path / "c") == block and "reason" not in block


@pytest.mark.parametrize(("failure", "words"), [
    (ServiceUnavailable("argonaut http://argonaut.skymaps.info/api/v2/bayestar2019/query -> HTTP 404"), "HTTP 404"),
    (ServiceUnavailable("argonaut http://x failed after 1 attempts: HTTP 503"), "HTTP 503"),
    (ServiceUnavailable("argonaut http://x failed after 1 attempts: ConnectTimeout(MaxRetryError('x'))"),
     "ConnectTimeout"),
    (b"<html>not json</html>", "unexpected answer"),
], ids=["404", "503", "timeout", "not-json"])
def test_an_argonaut_failure_falls_through_to_irsa_with_a_neutral_reason(tmp_path, failure, words):
    calls = []
    code, block = _run(tmp_path, failure, IRSA_OK, calls)
    assert code == 3 and block["status"] == "not available" and block["column"]["used"] == "pixel"
    assert block["reason"] == (f"Bayestar19 (Green et al. 2019) not available: the Argonaut web query failed "
                               f"({words}); no local map file")
    assert [c["service"] for c in calls] == ["argonaut", "irsa-dust"]
    text = json.dumps(block).lower()
    assert not [w for w in NEUTRAL_WORDS if w in text]


def test_store_wants_one_target_and_a_stored_file_is_validated(tmp_path, capsys):
    hand = ["--store", "--ebv", "0.05", "--ebv-p16", "0.03", "--ebv-p84", "0.07", "--distance-pc", "303",
            "--source", "read by hand"]
    assert ex.main(["--all", "--data-dir", str(tmp_path), "--root", str(tmp_path / "c"), *hand],
                   fetch=routed_fetch(AssertionError(), AssertionError())) == 2
    assert "store one target at a time" in capsys.readouterr().err
    path = ex.store_path(tmp_path / "c", TARGET)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"status": "not available"}), encoding="utf-8")
    args = ["--target", TARGET, "--data-dir", str(tmp_path / "data"), "--root", str(tmp_path / "c")]
    assert ex.main(args, fetch=routed_fetch(AssertionError(), AssertionError())) == 2
    err = capsys.readouterr().err
    assert "reason is empty" in err and str(path) in err and "nothing to do" not in err
    code, block = _run(tmp_path, ARGONAUT_OK, IRSA_OK, extra=["--force"])    # --force writes it again
    assert code == 0 and block["status"] == "adopted"
    assert ex.main([*args, *hand, "--force"], fetch=routed_fetch(AssertionError(), AssertionError())) == 0
    assert "kept the stored column" in capsys.readouterr().err
    assert json.loads(path.read_text(encoding="utf-8"))["column"]["ebv"] == 0.6741


def test_the_writer_keys_match_the_five_stored_records(tmp_path):
    """Minor 10: the keys the tool writes (with the Argonaut query failing or answering, whichever the live run met)
    are the keys of the five stored records, so a rename cannot drift from the records again. Task 17: a record the
    local map wrote (it carries ``map_version``) is held against the map writer's keys, adopted or not available."""
    by_state = {}
    for name, argonaut in (("failed", ServiceUnavailable("argonaut x failed after 1 attempts: ConnectTimeout()")),
                           ("answered", ARGONAUT_OK)):
        _code, block = _run(tmp_path / name, argonaut, IRSA_OK)
        by_state[(block["status"], False)] = block
    for name, flags in (("map-adopted", {}), ("map-unconverged", {"converged": False})):
        args, path = _map_setup(tmp_path / name)
        ex.main([*args, "--map", str(path)],
                fetch=routed_fetch(AssertionError(), IRSA_OK), map_reader=fake_reader(**flags))
        block = json.loads(ex.store_path(tmp_path / name / "c", TARGET).read_text(encoding="utf-8"))
        by_state[(block["status"], True)] = block
    root = Path(__file__).resolve().parents[2] / "docs" / "candidates"
    for target in ex.LIVE_TARGETS:
        stored = json.loads(ex.store_path(root, target).read_text(encoding="utf-8"))
        written = by_state[(stored["status"], "map_version" in stored)]
        assert list(stored) == list(written), target
        assert list(stored["column"]) == list(written["column"]), target
        assert stars.load_extinction(target, root) == stored
        assert not [w for w in NEUTRAL_WORDS if w in json.dumps(stored).lower()], target


# ------------------------------------------------------------------------------- Task 17 (spec 12.5): the local map

MAP_UNITS = {303.0: (0.15, 0.082, 0.218), 300.0: (0.14, 0.07, 0.2), 306.1: (0.16, 0.09, 0.23)}


def map_query(distance, median, p16, p84, *, in_footprint=True, converged=True, reliable=True, finite=True,
              dm_range=(5.0, 12.0)):
    """One query as ``tools.bayestar_map.query`` returns it, at KIC 6146676's sightline (nside 1024); ``dm_range``
    the pixel's reliable range in distance modulus (None for an end that is not finite)."""
    units = {"median": median, "p16": p16, "p84": p84} if in_footprint and finite else None
    return {"l_deg": 76.0444, "b_deg": 7.4198, "distance_pc": distance, "dm": 5 * math.log10(distance) - 5,
            "in_footprint": in_footprint, "row": 7 if in_footprint else None,
            "nside": 1024 if in_footprint else None, "healpix_index": 5984274 if in_footprint else None,
            "converged": converged if in_footprint else None, "reliable_distance": reliable and in_footprint,
            "dm_reliable_min": dm_range[0] if in_footprint else None,
            "dm_reliable_max": dm_range[1] if in_footprint else None,
            "n_samples": 5 if units else 0, "ebv_map_units": units,
            "ebv": {k: 0.884 * v for k, v in units.items()} if units else None}


def fake_reader(calls=None, **flags):
    """``read_map`` answering with ``MAP_UNITS`` at each distance asked for; ``flags`` go to every query."""
    def read(path, ra, dec, distances):
        if calls is not None:
            calls.append({"path": path, "ra": ra, "dec": dec, "distances": list(distances)})
        return [map_query(d, *MAP_UNITS[round(d, 1)], **flags) for d in distances]
    return read


def _map_setup(tmp_path, md5=bm.MAP_MD5):
    """The dossier with its distance percentiles (303.0, -3.0, +3.1) and a stand-in map file with its sidecar MD5
    (None: no sidecar). The reader is faked, so the file's content is never read."""
    folder = tmp_path / "data" / "dossiers" / TARGET
    folder.mkdir(parents=True)
    star = {"route": "mann", "distance_pc": {"median": 303.0, "minus": 3.0, "plus": 3.1}}
    (folder / "dossier_data.json").write_text(json.dumps({"target": {"ra": 297.86871, "dec": 41.455567},
                                                          "star": star}), encoding="utf-8")
    path = tmp_path / "maps" / "bayestar2019.h5"
    path.parent.mkdir()
    path.write_bytes(b"not read")
    if md5 is not None:
        (tmp_path / "maps" / "bayestar2019.h5.md5").write_text(f"{md5}  bayestar2019.h5\n", encoding="utf-8")
    return ["--target", TARGET, "--data-dir", str(tmp_path / "data"), "--root", str(tmp_path / "c")], path


def _stored(tmp_path):
    return json.loads(ex.store_path(tmp_path / "c", TARGET).read_text(encoding="utf-8"))


def test_a_local_map_is_read_first_and_stored_as_the_adopted_block(tmp_path, capsys):
    args, path = _map_setup(tmp_path)
    calls, reads = [], []
    assert ex.main([*args, "--map", str(path)], fetch=routed_fetch(AssertionError("no Argonaut"), IRSA_OK, calls),
                   map_reader=fake_reader(reads)) == 0
    assert [c["service"] for c in calls] == ["irsa-dust"]                     # Argonaut only without a map path
    assert reads == [{"path": path, "ra": 297.86871, "dec": 41.455567, "distances": [303.0, 300.0, 306.1]}]
    block = _stored(tmp_path)
    assert block["status"] == "adopted" and block["source"] == "Bayestar19 (Green et al. 2019), local file"
    assert (block["ebv"], block["ebv_p16"], block["ebv_p84"]) == (0.1326, 0.0725, 0.1927)
    assert (block["ebv_at_distance_p16"], block["ebv_at_distance_p84"]) == (0.1238, 0.1414)
    assert block["ebv_map_units"] == {"median": 0.15, "p16": 0.082, "p84": 0.218}
    assert (block["distance_pc"], block["distance_pc_p16"], block["distance_pc_p84"]) == (303.0, 300.0, 306.1)
    assert (block["map_file"], block["map_md5"], block["map_version"]) == ("bayestar2019.h5", bm.MAP_MD5,
                                                                            "bayestar2019")
    assert block["map"] == "bayestar2019 (doi:10.7910/DVN/2EJ9TX), local file"
    assert (block["nside"], block["healpix_index"], block["n_samples"]) == (1024, 5984274, 5)
    assert block["converged"] is True and block["reliable_distance"] is True
    # the reliable range's ends as distances, 10^(DM/5 + 1) to the nearest pc: DM 5 and 12
    assert (block["reliable_distance_min_pc"], block["reliable_distance_max_pc"]) == (100, 2512)
    assert list(block).index("reliable_distance_max_pc") == list(block).index("reliable_distance") + 2
    assert (block["l_deg"], block["b_deg"]) == (76.0444, 7.4198) and block["queried_utc"]
    assert block["column"]["ebv"] == 0.6741 and list(block)[-1] == "column"
    assert stars.load_extinction(TARGET, tmp_path / "c") == block
    err = capsys.readouterr().err
    assert "Bayestar19 E(B-V) 0.133 (0.072 to 0.193) from the local map" in err and "0.124 to 0.141" in err
    assert ex.main([*args, "--map", str(path)], fetch=routed_fetch(AssertionError(), AssertionError()),
                   map_reader=fake_reader()) == 0                            # a stored 3D value: nothing to do
    assert "stored; nothing to do" in capsys.readouterr().err


@pytest.mark.parametrize(("flags", "why", "short"), [
    ({"in_footprint": False}, "the sightline (l 76.04, b 7.42) is outside the map's footprint",
     "outside the map's footprint"),
    ({"converged": False}, "the map's pixel on this sightline did not converge (nside 1024, pixel 5984274)",
     "an unconverged map pixel"),
    ({"finite": False}, "the map gives no finite reddening on this sightline at 303 pc",
     "no finite map value at the star's distance"),
], ids=["footprint", "unconverged", "nan"])
def test_outside_the_footprint_or_in_an_unconverged_pixel_the_3d_value_is_not_available(tmp_path, flags, why, short):
    args, path = _map_setup(tmp_path)
    calls = []
    assert ex.main([*args, "--map", str(path)], fetch=routed_fetch(AssertionError("no Argonaut"), IRSA_OK, calls),
                   map_reader=fake_reader(**flags)) == 3
    block = _stored(tmp_path)
    assert block["status"] == "not available"
    assert block["reason"] == f"Bayestar19 (Green et al. 2019) not available: {why}"
    assert block["map_reason"] == short and block["map_md5"] == bm.MAP_MD5 and "ebv" not in block
    assert [c["service"] for c in calls] == ["irsa-dust"] and block["column"]["ebv"] == 0.6741
    assert stars.load_extinction(TARGET, tmp_path / "c") == block
    assert not [w for w in NEUTRAL_WORDS if w in json.dumps(block).lower()]


@pytest.mark.parametrize(("dm_range", "ends", "words"), [
    ((8.04, 12.0), (406, 2512), ", nearer than the map's reliable range (from 406 pc)"),     # the live case
    ((5.0, 7.0), (100, 251), ", beyond the map's reliable range (to 251 pc)"),
    ((None, None), (None, None), ", outside the map's reliable range"),
], ids=["nearer", "beyond", "no-range"])
def test_an_unreliable_distance_is_adopted_with_its_flag_and_the_range(tmp_path, capsys, dm_range, ends, words):
    """Fix round 1 (C1, M2): the record stores the reliable range's ends as distances beside the bool, and the
    tool's line says which side of the range the star (303 pc) is on."""
    args, path = _map_setup(tmp_path)
    assert ex.main([*args, "--map", str(path)], fetch=routed_fetch(AssertionError(), IRSA_OK),
                   map_reader=fake_reader(reliable=False, dm_range=dm_range)) == 0
    block = _stored(tmp_path)
    assert block["reliable_distance"] is False and block["status"] == "adopted"
    assert (block["reliable_distance_min_pc"], block["reliable_distance_max_pc"]) == ends
    err = capsys.readouterr().err
    assert f"pc){words}, adopted;" in err and "beyond the pixel's" not in err
    assert stars.load_extinction(TARGET, tmp_path / "c") == block


def test_the_map_md5_comes_from_the_sidecar_and_a_wrong_one_stops_the_tool(tmp_path, capsys):
    args, path = _map_setup(tmp_path, md5=None)
    assert ex.sidecar_md5(path) == "unverified"
    assert ex.main([*args, "--map", str(path)], fetch=routed_fetch(AssertionError(), IRSA_OK),
                   map_reader=fake_reader()) == 0
    assert _stored(tmp_path)["map_md5"] == "unverified" and "no bayestar2019.h5.md5 beside" in capsys.readouterr().err
    sidecar = path.with_name("bayestar2019.h5.md5")
    sidecar.write_text("MD5 hash of bayestar2019.h5:\nab 81 5d 2f d3 06 8d 1b 81 a1 bd 61 fb 18 a7 22\nCertUtil: "
                       "-hashfile command completed successfully.\n", encoding="utf-8")       # older certutil
    assert ex.sidecar_md5(path) == bm.MAP_MD5
    sidecar.write_text("0" * 32 + "\n", encoding="utf-8")
    assert ex.main([*args, "--map", str(path), "--force"], fetch=routed_fetch(AssertionError(), AssertionError()),
                   map_reader=fake_reader()) == 2
    assert f"is not the Dataverse record's {bm.MAP_MD5}" in capsys.readouterr().err
    sidecar.write_text("no digest here\n", encoding="utf-8")
    with pytest.raises(ex.MapError, match="holds no MD5 digest"):
        ex.sidecar_md5(path)


def test_the_environment_names_the_map_and_a_missing_map_stops_the_tool(tmp_path, monkeypatch, capsys):
    args, path = _map_setup(tmp_path)
    monkeypatch.setenv(ex.MAP_ENV, str(path))
    calls = []
    assert ex.main(args, fetch=routed_fetch(AssertionError("no Argonaut"), IRSA_OK, calls),
                   map_reader=fake_reader()) == 0
    assert [c["service"] for c in calls] == ["irsa-dust"] and _stored(tmp_path)["map_file"] == "bayestar2019.h5"
    monkeypatch.setenv(ex.MAP_ENV, str(tmp_path / "maps" / "missing.h5"))
    calls.clear()
    assert ex.main([*args, "--force"], fetch=routed_fetch(AssertionError(), AssertionError(), calls),
                   map_reader=fake_reader()) == 2
    err = capsys.readouterr().err
    assert "missing.h5 is not there" in err and "--store --ebv" in err and calls == []
    assert _stored(tmp_path)["status"] == "adopted"                           # the stored file is untouched


def test_a_record_without_a_3d_value_is_read_again_with_the_map_and_keeps_its_column(tmp_path, capsys):
    args, path = _map_setup(tmp_path)
    failed = ServiceUnavailable("argonaut x failed after 1 attempts: ConnectTimeout()")
    assert ex.main(args, fetch=routed_fetch(failed, IRSA_OK)) == 3
    before = _stored(tmp_path)
    assert ex.main([*args, "--map", str(path)], fetch=routed_fetch(AssertionError(), AssertionError()),
                   map_reader=fake_reader()) == 0                             # no --force, no network
    after = _stored(tmp_path)
    assert after["status"] == "adopted" and after["column"] == before["column"]
    assert "kept the stored column" in capsys.readouterr().err
    calls = []
    assert ex.main([*args, "--map", str(path), "--force"], fetch=routed_fetch(AssertionError(), IRSA_OK, calls),
                   map_reader=fake_reader()) == 0                             # --force asks IRSA again
    assert [c["service"] for c in calls] == ["irsa-dust"]


def test_read_map_runs_the_reader_under_the_third_venv_when_h5py_is_missing_here(tmp_path, monkeypatch):
    python = tmp_path / "venv" / "Scripts" / "python.exe"
    python.parent.mkdir(parents=True)
    python.write_bytes(b"")
    monkeypatch.setenv(ex.MAP_PYTHON_ENV, str(python))
    monkeypatch.setattr(ex, "_can_read_here", lambda: False)
    seen = []

    def run(cmd, **kw):
        seen.append((cmd, kw))
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps([map_query(303.0, 0.15, 0.08, 0.22)]),
                                           stderr="")
    out = ex.read_map(tmp_path / "m.h5", 297.86871, 41.455567, [303.0], run=run)
    assert out[0]["ebv_map_units"]["median"] == 0.15
    cmd, kw = seen[0]
    assert cmd == [str(python), "-I", str(Path(bm.__file__).resolve()), "--map", str(tmp_path / "m.h5"), "--ra",
                   "297.86871", "--dec", "41.455567", "--distance", "303.0"]
    assert kw["capture_output"] is True and kw["timeout"] == ex.MAP_TIMEOUT

    def fails(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 2, stdout="", stderr="trace\nm.h5: the map has no /samples\n")
    with pytest.raises(ex.MapError, match="the map has no /samples"):
        ex.read_map(tmp_path / "m.h5", 297.86871, 41.455567, [303.0], run=fails)

    def short(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 0, stdout="[]", stderr="")
    with pytest.raises(ex.MapError, match="not one query per distance"):
        ex.read_map(tmp_path / "m.h5", 297.86871, 41.455567, [303.0], run=short)


def test_the_third_venv_is_found_above_the_tool_or_named_by_the_environment(tmp_path, monkeypatch):
    monkeypatch.delenv(ex.MAP_PYTHON_ENV, raising=False)
    python = tmp_path / "root" / ".venv-dustmaps" / "Scripts" / "python.exe"
    python.parent.mkdir(parents=True)
    python.write_bytes(b"")
    deep = tmp_path / "root" / "worktrees" / "branch" / "tools"
    deep.mkdir(parents=True)
    assert ex.dustmaps_python(start=deep) == python
    alone = tmp_path / "alone" / "tools"
    alone.mkdir(parents=True)
    with pytest.raises(ex.MapError, match="no .venv-dustmaps was found"):
        ex.dustmaps_python(start=alone, stop=tmp_path)
    monkeypatch.setenv(ex.MAP_PYTHON_ENV, str(tmp_path / "nope.exe"))
    with pytest.raises(ex.MapError, match="nope.exe is not there"):
        ex.dustmaps_python()
