"""tools/extinction.py (spec 12.3 as amended by the controller's addenda of 23 October 2026): the IRSA DUST full-column
E(B-V) parsed from its XML, the stored block with the 3D value "not available", the hand-stored value, and main's
refusals. ``net.fetch`` is mocked: no test reaches the network."""
import json

import pytest

from skysift import stars
from skysift.net import ServiceUnavailable
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
                  "stats_radius_arcmin": ex.COLUMN_STATS_ARCMIN, "source": stars_source(), "queried_utc": "2026-10-25T10:00:00+00:00",
                  "ra": 297.86871, "dec": 41.455567, "url": ex.column_url(297.86871, 41.455567)}
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
    assert block["status"] == "not available" and block["reason"] == ex.BAYESTAR_NOT_AVAILABLE
    assert block["target"] == TARGET and block["distance_pc"] == 303.0 and block["column"]["ebv"] == 0.6741
    assert stars.load_extinction(TARGET, tmp_path / "c") == block
    err = capsys.readouterr().err
    assert "GET https://irsa.ipac.caltech.edu/cgi-bin/DUST/nph-dust" in err and "-> 200" in err
    assert "E(B-V) full column 0.674" in err and "--store --ebv" in err and "--distance-pc 303" in err
    assert ex.main(args, fetch=fake_fetch(AssertionError("no second query"))) == 0
    assert "stored; nothing to do" in capsys.readouterr().err and len(calls) == 1
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
    assert len(calls) == 1 and ex.store_path(tmp_path / "c", TARGET).is_file()
