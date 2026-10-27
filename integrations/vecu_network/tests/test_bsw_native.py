"""Actual compiler + native-process tests, not a mocked BSW adapter."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import random
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1] / "native" / "bsw"

@pytest.fixture(scope="session")
def native_bsw(tmp_path_factory):
    if not shutil.which("cc") or not shutil.which("c++"):
        pytest.skip("native C/C++ compiler unavailable")
    out = tmp_path_factory.mktemp("bsw-parent") / "build"
    proc = subprocess.run([sys.executable, str(ROOT / "build.py"), "--output-dir", str(out)],
                          capture_output=True, text=True, timeout=180)
    assert proc.returncode == 0, proc.stderr
    manifest = json.loads(proc.stdout)
    assert manifest["binary_sha256"] == hashlib.sha256((out / "axiom-autoas-bsw").read_bytes()).hexdigest()
    return out / "axiom-autoas-bsw"

def execute(binary, requests):
    lines = [r if isinstance(r, str) else json.dumps(r) for r in requests]
    proc = subprocess.run([str(binary)], input="\n".join(lines) + "\n",
                          capture_output=True, text=True, timeout=10)
    assert proc.returncode == 0, proc.stderr
    replies = [json.loads(line) for line in proc.stdout.splitlines()]
    assert len(replies) == len(requests)
    return replies

def path(reply):
    return [(event["module"], event["event"]) for event in reply["trace"]]

@pytest.mark.parametrize("can_id", [0x100, 0x101, 0x200, 0x201])
def test_real_stack_routes_and_confirms_every_registered_id(native_bsw, can_id):
    data = [0, 255, 42, 128, 2, 3, 4, 5]
    init, tx, rx, close = execute(native_bsw, [{"op": "init"},
        {"op": "tx", "can_id": can_id, "data": data},
        {"op": "rx", "can_id": can_id, "data": data}, {"op": "close"}])
    assert init["upstream"]["commit"] == "cc3709675ac30f7539c0710dfec6d579a5235841"
    assert all(r["ok"] for r in [init, tx, rx, close])
    assert tx["frames"] == [{"can_id": can_id, "data": data, "time_ms": 0, "epoch": 0}]
    assert rx["signals"] == [{"can_id": can_id, "data": data, "epoch": 0}]
    assert path(tx) == [("Com", "SendSignal"), ("Com", "TriggerIPDUSend"),
        ("PduR", "Transmit"), ("CanIf", "Transmit"), ("CanDriver", "Write"),
        ("CanIf", "TxConfirmation"), ("PduR", "TxConfirmation"), ("Com", "TxConfirmation")]
    assert path(rx) == [("CanIf", "RxIndication"), ("PduR", "RxIndication"),
                       ("Com", "RxIndication"), ("Com", "ReceiveSignal")]

def test_independent_rx_buffers_no_automatic_loopback_and_reset(native_bsw):
    replies = execute(native_bsw, [{"op": "init"},
        {"op": "tx", "can_id": 256, "data": [7] * 8},
        {"op": "read", "can_id": 256},
        {"op": "rx", "can_id": 257, "data": [9] * 8},
        {"op": "read", "can_id": 256}, {"op": "tick", "elapsed_ms": 15},
        {"op": "reset"}, {"op": "read", "can_id": 257}, {"op": "close"}])
    assert replies[2]["signals"][0]["data"] == [0] * 8
    assert replies[4]["signals"][0]["data"] == [0] * 8
    assert replies[7]["signals"][0] == {"can_id": 257, "data": [0] * 8, "epoch": 1}
    assert replies[7]["time_ms"] == 15

@pytest.mark.parametrize("bad", [
    {"op": "tx", "can_id": 256, "data": [0] * 7},
    {"op": "rx", "can_id": 256, "data": [256] * 8},
    {"op": "rx", "can_id": 256, "data": [-1] * 8},
    {"op": "tx", "can_id": 256, "data": [True] * 8},
    {"op": "tx", "can_id": 256, "data": [1.5] * 8},
    {"op": "rx", "can_id": 0x202, "data": [0] * 8},
    {"op": "rx", "can_id": 0x800, "data": [0] * 8},
    {"op": "read", "can_id": True}, {"op": "tick", "elapsed_ms": 0},
    {"op": "tick", "elapsed_ms": -1}, {"op": "tick", "elapsed_ms": 10001},
    {"op": "tick", "elapsed_ms": 1.5}, {"op": "arbitrary_command"}, "not JSON", "[]",
])
def test_malformed_requests_fail_without_frame_or_state_change(native_bsw, bad):
    _, rejected, read, close = execute(native_bsw, [{"op": "init"}, bad,
        {"op": "read", "can_id": 256}, {"op": "close"}])
    assert rejected["ok"] is False and rejected["frames"] == []
    assert rejected["time_ms"] == 0
    assert read["signals"][0]["data"] == [0] * 8
    assert close["ok"]

def test_init_required_and_cannot_reinitialize_without_reset(native_bsw):
    before, init, second, close = execute(native_bsw, [{"op": "tick"}, {"op": "init"},
                                                    {"op": "init"}, {"op": "close"}])
    assert not before["ok"] and not second["ok"]
    assert init["ok"] and close["ok"]

def test_external_ticks_execute_actual_main_functions_at_10ms(native_bsw):
    replies = execute(native_bsw, [{"op": "init"}, {"op": "tick", "elapsed_ms": 9},
        {"op": "tick"}, {"op": "tick", "elapsed_ms": 25}, {"op": "close"}])
    assert replies[1]["trace"] == []
    assert len(replies[2]["trace"]) == 3
    assert [e["time_ms"] for e in replies[3]["trace"]] == [20] * 3 + [30] * 3
    assert all(not r["frames"] for r in replies)  # cyclic auto-transmit disabled

def test_three_runs_have_identical_logical_traces(native_bsw):
    rng = random.Random(42)
    requests = [{"op": "init"}]
    for i in range(100):
        requests += [{"op": "tick", "elapsed_ms": 1},
            {"op": "rx", "can_id": [256, 257, 512, 513][i % 4],
             "data": [rng.randrange(256) for _ in range(8)]}]
    requests.append({"op": "close"})
    runs = [execute(native_bsw, requests) for _ in range(3)]
    assert runs[0] == runs[1] == runs[2]

def test_upstream_source_lock_is_exact():
    lock = json.loads((ROOT / "vendor/autoas.lock.json").read_text())
    assert lock["patches"] == []
    assert not any("askar" in name.lower() for name in lock["files"])
    for name, expected in lock["files"].items():
        assert hashlib.sha256((ROOT / "vendor/autoas" / name).read_bytes()).hexdigest() == expected

def test_modified_upstream_refused_before_build(tmp_path):
    copied = tmp_path / "source"
    shutil.copytree(ROOT, copied)
    source = copied / "vendor/autoas/infras/communication/Com/Com.c"
    source.write_bytes(source.read_bytes() + b"\n/*changed*/\n")
    proc = subprocess.run([sys.executable, str(copied / "build.py"), "--output-dir", str(tmp_path / "bad")],
                          capture_output=True, text=True, timeout=10)
    assert proc.returncode != 0 and "Pinned autoas file changed" in proc.stderr
    assert not (tmp_path / "bad").exists()
