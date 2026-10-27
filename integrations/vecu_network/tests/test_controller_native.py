"""Compile/run the network C++ controller; Python is only a post-run oracle."""
from __future__ import annotations
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest
from axiom_carla.hil_dut import ReferenceDut

ROOT = Path(__file__).resolve().parents[1] / "native/controller"

@pytest.fixture(scope="module")
def controller(tmp_path_factory):
    output = tmp_path_factory.mktemp("network-controller") / "build"
    completed = subprocess.run([sys.executable, str(ROOT / "build.py"), "--output-dir", str(output)],
                               capture_output=True, text=True, timeout=180)
    assert completed.returncode == 0, completed.stderr
    manifest = json.loads(completed.stdout)
    assert manifest["generated_source_sha256"] == manifest["origin"]["patched_traffic_dut_sha256"]
    return output / manifest["executable"]

def initialization(profile=False):
    value = {"schema_version": "hil-dut-init/1", "op": "init",
             "route": [{"x": float(x), "y": 0., "z": 0.} for x in (0,10,20,40,80)],
             "target_speed_mps": 3.,
             "physics": {"wheelbase_m": 2.7, "max_steer_angle_rad": .6, "rear_axle_offset_m": 1.1}}
    if profile:
        frozen = {"schema_version": "hil-dut-profile/1", "project_id": "default", "id": "network-check",
                  "revision": 1, "name": "network", "note": "네트워크 검사",
                  "parameters": {"max_object_age_ms": 250, "recovery_fresh_steps": 3},
                  "created_at": "2026-10-17T00:00:00Z", "author": {"user_label": "check", "role": "engineer"}}
        frozen["sha256"] = hashlib.sha256(json.dumps(frozen, sort_keys=True, ensure_ascii=False,
                                                     separators=(",", ":")).encode()).hexdigest()
        value["dut_profile"] = frozen
    return value

def step(seq, object_seq=None, present=True):
    obj = seq if object_seq is None else object_seq
    return {"schema_version": "hil-dut-step/1", "op": "step", "sequence": seq, "time_s": seq*.05,
            "ego": {"id": "ego", "x": seq*.04, "y": .1, "z": 0., "heading_rad": .01, "speed_mps": 1.5},
            "object_packet": {"sequence": obj, "time_s": obj*.05, "objects": []},
            "object_delivery_present": present}

def execute(controller, init, requests):
    completed = subprocess.run([str(controller)],
        input="\n".join(json.dumps(m, ensure_ascii=False) for m in [init,*requests,{"op":"close"}])+"\n",
        capture_output=True, text=True, timeout=15)
    return completed, [json.loads(line) for line in completed.stdout.splitlines()]

def assert_reply(actual, expected):
    if isinstance(expected, dict):
        assert actual.keys() == expected.keys()
        for key in expected: assert_reply(actual[key], expected[key])
    elif isinstance(expected, list):
        assert len(actual) == len(expected)
        for a,e in zip(actual,expected): assert_reply(a,e)
    elif type(expected) in (float,int): assert actual == pytest.approx(expected, abs=1e-9)
    else: assert actual == expected

@pytest.mark.parametrize("start", [0, 80, 140])
def test_start_at_current_global_tick_and_full_reference_parity(controller, start):
    init = initialization(profile=True)
    requests = [step(i) for i in range(start,start+20)]
    completed,replies = execute(controller,init,requests)
    assert completed.returncode == 0,completed.stderr
    assert replies[0]["dut_version"] == "network-traffic-cpp/1"
    assert replies[0]["dut_profile"] == init["dut_profile"]
    oracle = ReferenceDut(init)
    for request,reply in zip(requests,replies[1:-1]):
        oracle.sequence = request["sequence"]-1  # post-run only; never supplies live controls
        assert_reply(reply,oracle.step(request))

def test_missing_os_ticks_do_not_synthesize_controller_steps(controller):
    init = initialization()
    requests = [step(0),step(1),step(2),step(82,2,False),step(83),step(84),step(85)]
    completed,replies = execute(controller,init,requests)
    assert completed.returncode == 0,completed.stderr
    assert [r["sequence"] for r in replies[1:-1]] == [0,1,2,82,83,84,85]
    assert replies[4]["state"] == "degraded" and replies[4]["control"]["brake"] == 1.
    assert replies[4]["missing_packet_count"] == 1  # one invocation, no invented missed steps
    assert replies[7]["state"] == "tracking"
    oracle = ReferenceDut(init)
    for request,reply in zip(requests,replies[1:-1]):
        oracle.sequence = request["sequence"]-1
        assert_reply(reply,oracle.step(request))

@pytest.mark.parametrize("requests", [[step(80),step(80)], [step(80),step(79)], [step(-1)],
    [dict(step(80),time_s=0)], [dict(step(80),sequence=True)]])
def test_duplicate_decreasing_or_wrong_clock_control_requests_fail(controller, requests):
    completed,replies = execute(controller,initialization(),requests)
    assert completed.returncode != 0
    assert "sequence or 20 Hz" in completed.stderr
    assert not any(r.get("status")=="closed" for r in replies)

def test_object_identity_checks_remain_active_after_skips(controller):
    same = step(82,80)
    same["object_packet"]["objects"] = [{"id":"lead","x":20.,"y":0.,"z":0.,"heading_rad":0.,"speed_mps":0.}]
    completed,_ = execute(controller,initialization(),[step(80),same])
    assert completed.returncode != 0 and "repeated sequence changed" in completed.stderr
    completed,_ = execute(controller,initialization(),[step(80),step(82,79)])
    assert completed.returncode != 0 and "object packet identity" in completed.stderr

def test_fresh_process_reset_starts_at_global_tick_with_cleared_state(controller):
    init=initialization()
    completed,old=execute(controller,init,[step(0),step(80,0)])
    assert completed.returncode==0 and old[-2]["state"]=="degraded"
    completed,new=execute(controller,init,[step(81)])
    assert completed.returncode==0 and new[1]["sequence"]==81
    assert new[1]["state"]=="tracking" and new[1]["duplicate_packet_count"]==0

def test_frozen_profile_digest_is_still_enforced(controller):
    init=initialization(profile=True)
    init["dut_profile"]["parameters"]["max_object_age_ms"]=500
    completed,_=execute(controller,init,[step(80)])
    assert completed.returncode!=0 and "profile" in completed.stderr.lower()

def test_patch_and_upstream_pin_match_current_reference_source():
    lock=json.loads((ROOT/"source.lock.json").read_text())
    for name,digest in lock["files"].items():
        assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==digest
    original=ROOT.parents[2]/"vecu/native/traffic_dut.cpp"
    assert original.read_bytes()==(ROOT/"upstream/traffic_dut.cpp").read_bytes()
