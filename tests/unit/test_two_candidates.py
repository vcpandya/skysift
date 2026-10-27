"""The two-candidate Research Note (spec 2026-10-27): the candidate list a pack reports, the per-candidate stale and
gate checks, the period-ratio and coincidence sentences. The fixtures ``env_two`` (a pack environment whose target has
a second, independent stage 2 signal with its own further dossier) and ``two_live`` (the live-shape golden of KIC
6146676 twice, the second at 3.156 times the period) are shared by the note, pack and CLI tests of the same work."""
import json
from pathlib import Path

import pytest

from skysift import crosscheck as cross_check
from skysift import pack, pack_two
from skysift.dossier import generate
from skysift.models import TestResult, TestStatus
from skysift.zenodo import ZenodoClient
from tests.unit.test_crosscheck import fake_result
from tests.unit.test_dossier import _second_signal
from tests.unit.test_pack import (  # noqa: F401  (fixtures used by name)
    _approve,
    _files,
    _live_shape,
    _svg_only,
    env_with_dossier,
    fake_session,
)
from tests.unit.ui_seed import STAGE2_TARGET

RATIO = 3.156


@pytest.fixture
def env_two(env_with_dossier):  # noqa: F811  (the imported fixture)
    """``env_with_dossier`` plus a second stage 2 signal at 3.156 times the first period, approved, with its further
    dossier written; its id is ``env.second``."""
    env = env_with_dossier
    env.second = _second_signal(env, period_factor=RATIO)
    env.store.decide(env.second, "approve", "clean in every season")
    generate(env.store, env.blob, env.settings, STAGE2_TARGET, signal_id=env.second, further=True, fetch=env.fetch)
    return env


def _a_test_result(rung: str) -> TestResult:
    return TestResult(rung=rung, status=TestStatus.PASS)


def two_live(prior: bool) -> tuple[dict, dict]:
    """The golden-based pair of the spec's section 5.3: the live shape of KIC 6146676 with the golden's 40.29 d signal
    first, and the same JSON as a 127.2 d second signal (signal 3480) with its own numbers."""
    a = _live_shape("dominant", prior, "adopted")
    b = json.loads(json.dumps(a))
    eph = b["ephemeris"]
    eph["period_days"] *= RATIO
    eph["t0_bkjd"] += 1.1
    eph["t0_bjd"] += 1.1
    eph.update({"depth_ppm": 920.0, "depth_ppm_err": 87.0, "duration_h": 5.47, "n_events": 28, "sde": 33.2,
                "snr": 19.2, "epoch_ref": 16})
    b["signal"] = {**b["signal"], "id": 3480, "period": a["signal"]["period"] * RATIO, "t0": a["signal"]["t0"] + 1.1,
                   "depth_ppm": 920.0, "sde": 33.2, "snr": 19.2, "n_events": 28}
    b["timing"] = {**b["timing"], "period": eph["period_days"], "t0": eph["t0_bkjd"], "t0_bjd": eph["t0_bjd"],
                   "n_used": 19, "n_measured": 20, "rms_min": 24.2, "epoch_ref": 16}
    b["fap"] = {**b["fap"], "k": 16, "one_in": 120, "words": "1 in 120"}
    b["fpp"] = {**b["fpp"], "value": 0.108}
    b["planet"] = {**b["planet"], "rp_rearth": {**b["planet"]["rp_rearth"], "median": 2.27},
                   "teq_k": {**b["planet"]["teq_k"], "median": 366.0}}
    block = fake_result(cross_check.inputs_fingerprint(b), fpp=0.037, nfpp=0.012)
    b["cross_check"] = cross_check.with_current({**block, "current": True}, b)
    return a, b


def test_candidates_list_the_targets_own_first_then_further_by_period(env_two):
    cands = pack.candidates(env_two.store, STAGE2_TARGET)
    assert [c["ordinal"] for c in cands] == [1, 2] and cands[0]["further"] is False and cands[1]["further"] is True
    assert cands[0]["signal_id"] == env_two.ids["stage2"] and cands[1]["signal_id"] == env_two.second
    assert cands[1]["period_days"] == pytest.approx(3.156 * cands[0]["period_days"], rel=1e-6)
    assert pack.left_out(env_two.store, STAGE2_TARGET) == []


def test_a_third_further_dossier_is_left_out_named_and_blocks_a_live_draft(
        env_two, fake_session, monkeypatch):  # noqa: F811
    from skysift import fpp
    monkeypatch.setattr(fpp, "SIGNED_OFF", True)
    _approve(env_two)                      # the live gate looks at the approval and the sign-off first
    third = _second_signal(env_two, period_factor=7.3)                  # independent of both
    generate(env_two.store, env_two.blob, env_two.settings, STAGE2_TARGET, signal_id=third, further=True,
             fetch=env_two.fetch)
    assert [c["signal_id"] for c in pack.candidates(env_two.store, STAGE2_TARGET)] == [env_two.ids["stage2"],
                                                                                        env_two.second]
    out = pack.left_out(env_two.store, STAGE2_TARGET)
    assert out == [{"text": f"signal {third}'s further dossier is left out: the note shape holds two candidates, and "
                            "a third needs its own shape (spec section 2)", "blocks_live": True}]
    rep = pack.build_pack(env_two.store, env_two.blob, env_two.settings, STAGE2_TARGET)
    assert "holds two candidates" in (rep.out_dir / "README.md").read_text(encoding="utf-8")
    with pytest.raises(pack.PackError, match="holds two candidates"):
        pack.build_pack(env_two.store, env_two.blob, env_two.settings, STAGE2_TARGET,
                        zenodo=ZenodoClient("t", sandbox=False, session=fake_session))


def test_a_rejected_further_signal_is_left_out_and_named(env_two):
    env_two.store.decide(env_two.second, "reject", "a neighbour")
    assert len(pack.candidates(env_two.store, STAGE2_TARGET)) == 1
    assert pack.left_out(env_two.store, STAGE2_TARGET) == [
        {"text": f"signal {env_two.second}'s further dossier is left out: its status is now rejected",
         "blocks_live": False}]
    rep = pack.build_pack(env_two.store, env_two.blob, env_two.settings, STAGE2_TARGET)
    readme = (rep.out_dir / "README.md").read_text(encoding="utf-8")
    assert "further dossier is left out" in readme.split("## Step 2.")[1].split("## Step 3.")[0]
    assert "_02" not in "\n".join(_files(rep))


def test_a_further_row_without_its_file_is_left_out(env_two):
    Path(env_two.store.further_dossier(env_two.second)["data_path"]).unlink()
    assert len(pack.candidates(env_two.store, STAGE2_TARGET)) == 1
    assert "is missing; run `skysift dossier --target" in pack.left_out(env_two.store, STAGE2_TARGET)[0]["text"]


def test_a_further_signal_within_the_alias_tolerance_of_a_listed_one_is_left_out_with_its_ratio(env_two):
    near = _second_signal(env_two, period_factor=RATIO * 1.01)          # 1 percent from the second candidate's period
    generate(env_two.store, env_two.blob, env_two.settings, STAGE2_TARGET, signal_id=near, further=True,
             fetch=env_two.fetch)
    cands = pack.candidates(env_two.store, STAGE2_TARGET)
    assert [c["signal_id"] for c in cands] == [env_two.ids["stage2"], env_two.second]
    out = pack.left_out(env_two.store, STAGE2_TARGET)
    assert out == [{"text": f"signal {near}'s further dossier is left out: its period is within 2 percent of 1 times "
                            f"the {cands[1]['period_days']:.3f} d candidate's", "blocks_live": False}]


def test_a_stale_further_dossier_names_its_signal_and_remedy(env_two, fake_session, monkeypatch):  # noqa: F811
    from skysift import fpp
    monkeypatch.setattr(fpp, "SIGNED_OFF", True)
    _approve(env_two)
    env_two.store.add_test(env_two.second, _a_test_result("rung4_novelty"), 0.1)
    cands = pack.candidates(env_two.store, STAGE2_TARGET)
    approved, reason = pack.candidate_approval(env_two.store, STAGE2_TARGET, cands[1], env_two.settings.data_dir)
    assert approved and reason.startswith(
        f"the {cands[1]['period_days']:.3f} d candidate's dossier (signal {env_two.second}) is stale")
    rep = pack.build_pack(env_two.store, env_two.blob, env_two.settings, STAGE2_TARGET)
    assert rep.stale_reason == reason and "--further" in (rep.out_dir / "README.md").read_text(encoding="utf-8")
    with pytest.raises(pack.PackError, match="is stale"):
        pack.build_pack(env_two.store, env_two.blob, env_two.settings, STAGE2_TARGET,
                        zenodo=ZenodoClient("t", sandbox=False, session=fake_session))


def test_coincident_transits_counts_the_longer_periods_overlaps():
    short = {"period_days": 10.0, "t0_bkjd": 100.0, "duration_h": 4.0}
    long = {"period_days": 31.0, "t0_bkjd": 100.0, "duration_h": 4.0}       # epochs 0 and 10 coincide exactly
    assert pack.coincident_transits(short, long, 90.0, 420.0) == (2, 11)
    assert pack.coincident_transits(short, {**long, "t0_bkjd": 100.5}, 90.0, 420.0) == (0, 11)


def test_ratio_and_coincidence_sentences():
    assert pack_two.ratio_sentence(15.113696, 47.690453) == (
        "The period ratio is 3.155, 5 percent from 3, outside the 2 percent alias tolerance of the search (ratios of "
        "2, 3, 1/2 and 1/3), so neither is an alias of the other.")
    assert pack_two.coincidence_sentence(1, 31, "15.11", "47.69", note=True) == (
        "One of the 31 predicted 47.69 d transits falls within a duration of a 15.11 d transit.")
    assert pack_two.coincidence_sentence(0, 31, "15.11", "47.69", note=True) is None
    assert pack_two.coincidence_sentence(0, 31, "15.11", "47.69", note=False) == (
        "0 of 31 predicted 47.69 d transits coincide with a 15.11 d transit within a duration.")


def test_gate_problems_name_the_candidate():
    a, b = two_live(prior=False)
    b["timing"] = {**b["timing"], "ok": False, "reason": "fewer than 3 transits timed"}
    problems = pack.gate_problems(a, others=[b])
    assert any(p.startswith(f"the {b['ephemeris']['period_days']:.3f} d candidate: ") and "carries no error" in p
               for p in problems)
    assert not any(p.startswith(f"the {a['ephemeris']['period_days']:.3f} d candidate: ") and "ephemeris" in p
                   for p in problems)
