"""Actual native build/process contract, using a local stand-in for SSH only.

These are not a CARLA execution or Linux-worker readiness check. They exercise
the real remote helper, compiler, executable and both production IPC transports.
"""
import base64
import hashlib
import json
from pathlib import Path
import shutil
import sys
import uuid

import pytest

from ade2e.adapters import AdapterCancelled
from ade2e.service import digest
from axiom_carla import city_control, hil_dut, hil_native, hil_policy, vecu_remote
from axiom_vecu import builds as build_adapter
from axiom_vecu.runtime import Process
from test_cpp_native import initialization, requests


@pytest.fixture(scope="module")
def installed_native(tmp_path_factory):
    root = tmp_path_factory.mktemp("compiled-helper")
    for module in (vecu_remote, hil_policy, hil_dut, city_control):
        shutil.copyfile(module.__file__, root/Path(module.__file__).name)
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(build_adapter, "command", lambda transport, operation: [sys.executable, str(root/"vecu_remote.py"), operation])
    files, bundle = build_adapter.source_bundle()
    runtimes = {}
    try:
        for policy in ("immediate", "ramp"):
            key = uuid.uuid4().hex
            result = build_adapter.call(None, "build", dict(build_key=key, source_id="cpp-"+policy,
                bundle=base64.b64encode(bundle).decode(), bundle_sha256=hashlib.sha256(bundle).hexdigest(), files=files))
            manifest = result["manifest"]
            assert manifest["files"] == files
            assert manifest["source_sha256"] == digest(files)
            assert (root/"vecu-builds"/key/"manifest.json").exists()
            assert not (root/"vecu-builds"/key/"process.json").exists()
            runtime = dict(kind="compiled_reference", build_key=key, source_id="cpp-"+policy,
                policy_id=policy, dut_version=hil_policy.POLICIES[policy], protocol="hil-dut/1",
                binary_sha256=manifest["binary_sha256"], source_sha256=manifest["source_sha256"],
                compiler=manifest["compiler"], target_platform=manifest["target_platform"])
            runtime["runtime_sha256"] = digest(runtime)
            assert build_adapter.call(None, "check", runtime)["available"]
            runtimes[policy] = runtime
        yield root, runtimes
    finally:
        monkeypatch.undo()


@pytest.mark.parametrize("fault", ["normal", "delay", "dropout", "repeat"])
@pytest.mark.parametrize("policy", ["immediate", "ramp"])
def test_actual_compiled_replay_through_remote_launch_preamble(installed_native, tmp_path, policy, fault):
    root, runtimes = installed_native
    runtime = runtimes[policy]
    init = initialization()
    child = Process(runtime, init, tmp_path/"native.log", command=[sys.executable, str(root/"vecu_remote.py"), "launch"],
                    preamble=runtime)
    oracle = hil_policy.PolicyOracle(init, policy)
    try:
        for request in requests(fault, total=160, moving=False, windows=((20, 100),)):
            reply, elapsed = child.exchange(request)
            assert elapsed >= 0
            oracle.check(request, reply)
    finally:
        child.close()
    assert child.process.poll() is not None


@pytest.mark.parametrize("policy", ["immediate", "ramp"])
def test_native_carla_transport_uses_selected_binary_and_rechecks_hash(installed_native, tmp_path, monkeypatch, policy):
    root, runtimes = installed_native
    runtime = runtimes[policy]
    monkeypatch.setattr(hil_native, "__file__", str(root/"hil_native.py"))
    init = initialization()
    child = hil_native.DutProcess(init, tmp_path/"hil.log", runtime=runtime)
    assert child.initial["dut_version"] == runtime["dut_version"]
    assert child.initial["pid"] == child.process.pid
    oracle = hil_policy.PolicyOracle(init, policy)
    try:
        for request in requests("repeat", total=160, windows=((20, 100),)):
            reply, _ = child.exchange(request)
            oracle.check(request, reply)
    finally:
        child.close()
    assert child.process.poll() is not None


def test_launch_and_readback_reject_changed_binary(installed_native, tmp_path):
    root, runtimes = installed_native
    runtime = runtimes["immediate"]
    target = hil_policy.executable(runtime, root)
    original = target.read_bytes()
    replacement = target.with_suffix(".changed")
    replacement.write_bytes(original+b"changed")
    replacement.chmod(0o755)
    replacement.replace(target)
    try:
        with pytest.raises(ValueError, match="binary missing or changed"):
            build_adapter.call(None, "check", runtime)
        with pytest.raises(RuntimeError, match="exited"):
            Process(runtime, initialization(), tmp_path/"changed.log",
                    command=[sys.executable, str(root/"vecu_remote.py"), "launch"], preamble=runtime)
    finally:
        replacement.write_bytes(original)
        replacement.chmod(0o755)
        replacement.replace(target)
    assert build_adapter.call(None, "check", runtime)["available"]


def test_cancel_compiled_process_reaps_child(installed_native, tmp_path):
    root, runtimes = installed_native
    runtime = runtimes["ramp"]
    child = Process(runtime, initialization(), tmp_path/"cancel.log",
                    command=[sys.executable, str(root/"vecu_remote.py"), "launch"], preamble=runtime)
    def cancel():
        raise AdapterCancelled("test cancellation")
    child.check = cancel
    try:
        with pytest.raises(AdapterCancelled, match="test cancellation"):
            child.exchange(requests(total=1)[0])
    finally:
        child.close(False)
    assert child.process.poll() is not None


def test_helper_rejects_source_archive_hash_and_incomplete_files(installed_native):
    root, _ = installed_native
    files, bundle = build_adapter.source_bundle()
    request = dict(build_key=uuid.uuid4().hex, source_id="cpp-immediate", bundle=base64.b64encode(bundle).decode(),
                   bundle_sha256="0"*64, files=files)
    with pytest.raises(ValueError, match="archive changed"):
        build_adapter.call(None, "build", request)
    request.update(build_key=uuid.uuid4().hex, bundle_sha256=hashlib.sha256(bundle).hexdigest(),
                   files={**files, "missing.cpp": "0"*64})
    with pytest.raises(ValueError, match="Incomplete source archive"):
        build_adapter.call(None, "build", request)


def bad_close_child(tmp_path, behavior):
    """Actual child deliberately violates only the final protocol handshake."""
    source = tmp_path/"bad_close.py"
    source.write_text('''import json, os, sys
behavior = '''+repr(behavior)+'''
json.loads(sys.stdin.readline())
print(json.dumps(dict(schema_version='hil-dut-reply/1', op='init', status='ready',
                     pid=os.getpid(), dut_version='close-fixture/1')), flush=True)
if behavior == 'exit_before_close':
    sys.exit(0)
message = json.loads(sys.stdin.readline())
assert message['op'] == 'close'
if behavior == 'exit_on_close':
    sys.exit(0)
print(json.dumps(dict(schema_version='hil-dut-reply/1', op='close', status='wrong')), flush=True)
''')
    return Process(dict(command=[sys.executable, str(source)], dut_version="close-fixture/1"),
                   dict(op="init"), tmp_path/"bad_close.log")


@pytest.mark.parametrize("behavior,error,match", [
    ("bad_ack", ValueError, "close acknowledgment missing"),
    ("exit_on_close", RuntimeError, "exited without a complete reply"),
    ("exit_before_close", RuntimeError, "exited before close acknowledgment"),
])
def test_graceful_close_rejects_bad_ack_or_exit_and_reaps_child(tmp_path, behavior, error, match):
    child = bad_close_child(tmp_path, behavior)
    if behavior == "exit_before_close":
        child.process.wait(timeout=2)
    with pytest.raises(error, match=match):
        child.close()
    assert child.process.poll() is not None
    assert all(stream.closed for stream in (child.process.stdin, child.process.stdout, child.log))


def test_invalid_close_does_not_replace_active_execution_failure(tmp_path):
    child = bad_close_child(tmp_path, "bad_ack")
    with pytest.raises(ValueError, match="original execution failure"):
        try:
            raise ValueError("original execution failure")
        finally:
            child.close()
    assert child.process.poll() is not None
    assert all(stream.closed for stream in (child.process.stdin, child.process.stdout, child.log))
