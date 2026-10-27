"""Real compiler/process checks for portable reference ECUs (no CARLA mocks)."""
import copy
import hashlib
import json
import math
from pathlib import Path
import random
import subprocess
import sys

import pytest

from axiom_carla.hil_dut import ReferenceDut
from axiom_carla.hil_policy import PolicyOracle

NATIVE = Path(__file__).resolve().parents[1] / "native"


@pytest.fixture(scope="module")
def builds(tmp_path_factory):
    root = tmp_path_factory.mktemp("native-ecu")
    result = {}
    for policy in ("immediate", "ramp"):
        output = root / policy
        run = subprocess.run([sys.executable, str(NATIVE / "build.py"), "--policy", policy,
                              "--output-dir", str(output)], capture_output=True, text=True, timeout=180)
        assert run.returncode == 0, run.stderr
        manifest = json.loads(run.stdout)
        result[policy] = output / manifest["executable"], manifest
    return result


def frozen_profile(age=250, recovery=3, note="정수 참조 ECU"):
    value = dict(schema_version="hil-dut-profile/1", project_id="default", id="test-profile",
                 revision=1, name="native check", note=note,
                 parameters=dict(max_object_age_ms=age, recovery_fresh_steps=recovery),
                 created_at="2026-10-17T00:00:00Z", author=dict(user_label="검증", role="engineer"))
    value["sha256"] = hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                                separators=(",", ":")).encode()).hexdigest()
    return value


def initialization(curved=False, rear=0., profile=None):
    route = [dict(x=float(i*10), y=2*math.sin(i*.4) if curved else 0., z=0.) for i in range(20)]
    result = dict(op="init", schema_version="hil-dut-init/1", route=route, target_speed_mps=3.,
                  physics=dict(wheelbase_m=2.7, max_steer_angle_rad=.6, rear_axle_offset_m=rear))
    if profile is not None:
        result["dut_profile"] = profile
    return result


def requests(fault="normal", total=360, seed=42, windows=((60, 140),), moving=True):
    rng = random.Random(seed)
    frames = []
    for i in range(total):
        x = i*.12 if moving else 0.
        ego = dict(id="ego", x=x, y=.3*math.sin(i*.08) if moving else 0., z=0.,
                   heading_rad=.04*math.sin(i*.11) if moving else 0.,
                   speed_mps=2.+math.sin(i*.03)*.7 if moving else 0.)
        objects = [dict(id="lead", x=x+10.+3*math.sin(i*.09), y=rng.uniform(-.1, .1),
                        z=0., heading_rad=0., speed_mps=2.)]
        # Equal-gap identity tie and an ignored off-route object exercise route projection.
        if i % 5 == 0:
            objects.append(dict(objects[0], id="aaa"))
        objects.append(dict(objects[0], id="off-route", y=9.))
        frames.append(dict(ego=ego, objects=objects))
    result = []
    previous = -1
    for i, frame in enumerate(frames):
        active = any(start <= i < end for start, end in windows)
        packet_index = max(previous, i-8) if fault == "delay" and active else (
            previous if fault in ("repeat", "dropout") and active else i)
        present = not (fault == "dropout" and active)
        packet = dict(sequence=packet_index, time_s=packet_index*.05, objects=frames[packet_index]["objects"])
        result.append(dict(schema_version="hil-dut-step/1", op="step", sequence=i, time_s=i*.05,
                           ego=frame["ego"], object_packet=packet, object_delivery_present=present))
        previous = packet_index
    return result


def execute(binary, init, messages, close=True):
    lines = [init, *messages] + ([dict(op="close")] if close else [])
    completed = subprocess.run([str(binary)], input="".join(json.dumps(x, ensure_ascii=False) + "\n" for x in lines),
                               capture_output=True, text=True, timeout=30)
    replies = [json.loads(line) for line in completed.stdout.splitlines()]
    return completed, replies


def assert_same(actual, expected, path="reply"):
    if isinstance(expected, dict):
        assert actual.keys() == expected.keys(), path
        for key, value in expected.items():
            assert_same(actual[key], value, path + "." + key)
    elif isinstance(expected, list):
        assert len(actual) == len(expected), path
        for i, (a, b) in enumerate(zip(actual, expected)):
            assert_same(a, b, path + f"[{i}]")
    elif type(expected) in (int, float):
        assert type(actual) in (int, float) and math.isfinite(actual), path
        assert abs(actual-expected) <= 1e-9, (path, actual, expected)
    else:
        assert type(actual) is type(expected) and actual == expected, (path, actual, expected)


@pytest.mark.parametrize("fault", ["normal", "delay", "dropout", "repeat"])
@pytest.mark.parametrize("curved,rear,seed", [(False, 0., 42), (True, 1.1, 21), (False, 1.2, 7)])
def test_cpp_immediate_full_reply_matches_python(builds, fault, curved, rear, seed):
    init = initialization(curved, rear)
    corpus = requests(fault, seed=seed)
    completed, replies = execute(builds["immediate"][0], init, corpus)
    assert completed.returncode == 0, completed.stderr
    assert replies[0]["dut_version"] == "reference-traffic-cpp/1"
    assert replies[-1]["status"] == "closed"
    oracle = ReferenceDut(init)
    for request, actual in zip(corpus, replies[1:-1]):
        assert_same(actual, oracle.step(request))
    assert len(replies) == len(corpus)+2


def test_endpoint_stop_and_zero_length_route_segment_parity(builds):
    init = initialization(rear=1.1)
    init["route"] = [dict(x=x, y=0., z=0.) for x in (0., 10., 10., 20.)]
    corpus = requests(total=120)
    for i, request in enumerate(corpus):
        request["ego"].update(x=i*.2, y=0., heading_rad=0., speed_mps=max(0., 2.-i*.02))
        request["object_packet"]["objects"] = []
    completed, replies = execute(builds["immediate"][0], init, corpus)
    assert completed.returncode == 0, completed.stderr
    oracle = ReferenceDut(init)
    for request, reply in zip(corpus, replies[1:-1]):
        assert_same(reply, oracle.step(request))
    assert replies[101]["plan"]["goal_reached"]
    assert replies[101]["plan"]["state"] == "stopped"
    assert replies[101]["control"]["brake"] == 1.


@pytest.mark.parametrize("age,recovery,note", [(50, 1, "x"), (250, 3, "한글"*40), (1000, 10, "abc"*1000)])
def test_cpp_validates_frozen_utf8_profile_sha256_and_parity(builds, age, recovery, note):
    profile = frozen_profile(age, recovery, note)
    init = initialization(profile=profile)
    corpus = requests("repeat")
    completed, replies = execute(builds["immediate"][0], init, corpus)
    assert completed.returncode == 0, completed.stderr
    assert replies[0]["dut_profile"] == profile
    oracle = ReferenceDut(init)
    for request, actual in zip(corpus, replies[1:-1]):
        assert_same(actual, oracle.step(request))
    init["dut_profile"]["sha256"] = "0"*64
    bad, replies = execute(builds["immediate"][0], init, [])
    assert bad.returncode == 2 and not replies
    assert "hash mismatch" in bad.stderr


@pytest.mark.parametrize("fault", ["normal", "delay", "dropout", "repeat"])
def test_cpp_ramp_matches_independent_policy_oracle(builds, fault):
    init = initialization(curved=True, rear=1.1)
    corpus = requests(fault, windows=((20, 100), (104, 120), (160, 200)))
    completed, replies = execute(builds["ramp"][0], init, corpus)
    assert completed.returncode == 0, completed.stderr
    oracle = PolicyOracle(init, "ramp")
    for request, actual in zip(corpus, replies[1:-1]):
        assert_same(actual, oracle.expected(request))
        assert not (actual["control"]["brake"] > 1e-9 and actual["control"]["throttle"] > 0)


def test_ramp_endpoints_and_refault_begin_from_applied_brake(builds):
    init = initialization()
    corpus = requests("dropout", moving=False, windows=((20, 100),))
    completed, replies = execute(builds["ramp"][0], init, corpus)
    assert completed.returncode == 0, completed.stderr
    by_step = {x["sequence"]: x for x in replies if x["op"] == "step"}
    assert by_step[24]["state"] == "tracking"  # exactly250ms remains fresh
    for offset in range(7):
        row = by_step[25+offset]
        assert row["control"]["throttle"] == 0
        assert row["control"]["brake"] == pytest.approx(offset/6)
    assert [by_step[i]["fresh_recovery_steps"] for i in (100, 101, 102)] == [1, 2, 3]
    for offset in range(10):
        row = by_step[102+offset]
        assert row["state"] == "recovering"
        assert row["control"]["brake"] == pytest.approx(1-offset/10)
        assert row["control"]["throttle"] == 0
    assert by_step[112]["state"] == "tracking"
    assert by_step[112]["control"]["brake"] == 0
    assert by_step[112]["control"]["throttle"] == .6
    assert by_step[113]["phase_elapsed_s"] == 0
    corpus = requests("dropout", moving=False, windows=((20, 100), (104, 120)))
    completed, replies = execute(builds["ramp"][0], init, corpus)
    assert completed.returncode == 0, completed.stderr
    by_step = {x["sequence"]: x for x in replies if x["op"] == "step"}
    assert by_step[109]["policy_phase"] == "braking_ramp"
    assert by_step[109]["phase_elapsed_s"] == 0
    assert by_step[109]["control"]["brake"] == by_step[108]["control"]["brake"]
    start = by_step[108]["control"]["brake"]
    assert by_step[110]["control"]["brake"] == pytest.approx(start+(1-start)/6)


def test_duplicate_is_not_new_recovery_packet_and_counters_distinguish_missing(builds):
    corpus = requests("repeat", total=100, moving=False, windows=((20, 60),))
    for i in (61, 62):
        corpus[i]["object_packet"] = copy.deepcopy(corpus[60]["object_packet"])
    for policy in ("immediate", "ramp"):
        completed, replies = execute(builds[policy][0], initialization(), corpus)
        assert completed.returncode == 0, completed.stderr
        steps = replies[1:-1]
        assert steps[60]["fresh_recovery_steps"] == 1
        assert steps[61]["fresh_recovery_steps"] == steps[62]["fresh_recovery_steps"] == 0
        assert steps[64]["state"] == "recovering"
        assert steps[-1]["duplicate_packet_count"] == 42
        assert steps[-1]["missing_packet_count"] == 0
        dropout = requests("dropout", total=100, moving=False, windows=((20, 60),))
        completed, replies = execute(builds[policy][0], initialization(), dropout)
        assert completed.returncode == 0, completed.stderr
        assert replies[-2]["missing_packet_count"] == 40
        assert replies[-2]["duplicate_packet_count"] == 0


@pytest.mark.parametrize("mutate", [
    lambda m: m.update(sequence=4),
    lambda m: m.update(time_s=999.),
    lambda m: m.update(object_delivery_present=False),
    lambda m: m["ego"].update(speed_mps=101),
    lambda m: m["object_packet"]["objects"][0].update(id="ego"),
])
def test_invalid_contract_rejected_in_actual_process(builds, mutate):
    corpus = requests(total=1)
    mutate(corpus[0])
    completed, replies = execute(builds["immediate"][0], initialization(), corpus)
    assert completed.returncode == 2 and len(replies) == 1
    assert "native ECU error:" in completed.stderr


def test_repeated_sequence_with_changed_payload_and_unterminated_line_fail(builds):
    corpus = requests("repeat", total=22, windows=((20, 22),))
    corpus[20]["object_packet"] = copy.deepcopy(corpus[20]["object_packet"])
    corpus[20]["object_packet"]["objects"][0]["x"] += 1
    completed, _ = execute(builds["immediate"][0], initialization(), corpus)
    assert completed.returncode == 2 and "changed packet contents" in completed.stderr
    for content in ('{"op":"init"}', 'x'*(256*1024)+'\n'):
        completed = subprocess.run([str(builds["immediate"][0])], input=content, text=True, capture_output=True, timeout=5)
        assert completed.returncode == 2 and "message exceeds bound" in completed.stderr


def test_build_manifest_hashes_native_binary_and_cannot_overwrite(builds):
    for policy, (binary, manifest) in builds.items():
        assert manifest["source_id"] == "cpp-"+policy
        assert hashlib.sha256(binary.read_bytes()).hexdigest() == manifest["binary_sha256"]
        for name, expected in manifest["files"].items():
            assert hashlib.sha256((NATIVE/name).read_bytes()).hexdigest() == expected
        source = hashlib.sha256(json.dumps(manifest["files"], sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        assert manifest["source_sha256"] == source
        assert not manifest["production_ecu_software"]
        assert manifest["dependencies"][0]["license"] == "MIT"
        again = subprocess.run([sys.executable, str(NATIVE/"build.py"), "--policy", policy,
                                "--output-dir", str(binary.parent)], capture_output=True, text=True)
        assert again.returncode == 2 and "immutable" in again.stderr


def test_fixed_replay_logical_response_repeatability(builds):
    corpus = requests("repeat", total=160)
    for policy in ("immediate", "ramp"):
        hashes = []
        for _ in range(3):
            completed, replies = execute(builds[policy][0], initialization(), corpus)
            assert completed.returncode == 0, completed.stderr
            hashes.append(hashlib.sha256(json.dumps(replies[1:], sort_keys=True, separators=(",", ":")).encode()).hexdigest())
        assert len(set(hashes)) == 1
