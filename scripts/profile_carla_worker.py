#!/usr/bin/env python3
"""Acceptance-only, runner-scoped timing over the installed CARLA worker app.

AXIOM_CARLA_PROFILE_DIR=/absolute/new-or-existing/dir \
AXIOM_CARLA_PROFILE_PORT=8101 python carla-profile-worker.py

Root supplies the ordinary worker token/engine/store environment and dispatches
new execution IDs directly through RemoteCarlaAdapter. This file neither claims
local API jobs nor changes the normal worker on 8100. The installed worker's
existing store/native ownership locks remain authoritative.

Profiling adds clocks, Python wrappers, one read-only meta call at entry and a
separate JSON write after runner exit. It changes neither checkpoint frequency,
commit durability, inputs, control nor artifact construction. Timings include
instrumentation overhead and are diagnostic, not a throughput/realtime claim.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
from functools import wraps
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import threading
import time
from uuid import uuid4

_local = threading.local()
_patched = []
_output_dir = None
CLOCK_NAMES = ("wall", "process_cpu", "thread_cpu")


def clocks():
    return (time.perf_counter_ns(), time.process_time_ns(), time.thread_time_ns())


class Profile:
    def __init__(self, identity, method, identity_read_ns):
        self.identity, self.method = identity, method
        self.identity_read_ns = identity_read_ns
        self.stack, self.stats = [], {}

    @contextmanager
    def span(self, name):
        frame = {"start": clocks(), "children": [0, 0, 0]}
        self.stack.append(frame)
        failed = False
        try:
            yield
        except BaseException:
            failed = True
            raise
        finally:
            end = clocks()
            self.stack.pop()
            inclusive = [max(0, end[i] - frame["start"][i]) for i in range(3)]
            raw_exclusive = [inclusive[i] - frame["children"][i] for i in range(3)]
            stat = self.stats.setdefault(name, {"calls": 0, "raised": 0, "inclusive_ns": [0, 0, 0],
                                               "exclusive_ns": [0, 0, 0], "rounding_clamps": 0})
            stat["calls"] += 1
            stat["raised"] += int(failed)
            stat["rounding_clamps"] += sum(value < 0 for value in raw_exclusive)
            for i in range(3):
                stat["inclusive_ns"][i] += inclusive[i]
                stat["exclusive_ns"][i] += max(0, raw_exclusive[i])
                if self.stack:
                    self.stack[-1]["children"][i] += inclusive[i]

    def report(self, returned):
        functions = {}
        for name, stat in sorted(self.stats.items()):
            functions[name] = {"calls": stat["calls"], "raised": stat["raised"], "rounding_clamps": stat["rounding_clamps"],
                "inclusive_seconds": dict(zip(CLOCK_NAMES, [value / 1e9 for value in stat["inclusive_ns"]])),
                "exclusive_seconds": dict(zip(CLOCK_NAMES, [value / 1e9 for value in stat["exclusive_ns"]]))}
        return {"schema_version": "carla-runner-profile/1", **self.identity,
            "observed_at": datetime.now(timezone.utc).isoformat(), "runner_method": self.method,
            "runner_returned": returned, "pid": os.getpid(), "thread_id": threading.get_ident(),
            "functions": functions,
            "entry_identity_read_seconds": dict(zip(CLOCK_NAMES, [value / 1e9 for value in self.identity_read_ns])),
            "scope": "Only synchronous code executed in this runner invocation's thread; no local core API job claims",
            "timing_contract": {
                "inclusive": "From entry to exit of each measured call, including nested measured calls",
                "exclusive": "Inclusive minus directly nested measured spans; includes uninstrumented work and wrapper overhead",
                "Attempt.db": "Actual context manager entry, SQL work during yield, commit/rollback and connection close; not generator construction",
                "process_cpu": "Process-wide CPU counter; other concurrent threads may contribute and this is not CPU ownership attribution",
                "thread_cpu": "CPU counter for the runner thread only; callback/other-request work is not attributed",
                "wall": "perf_counter monotonic elapsed time, includes waits and instrumentation overhead",
            },
            "limitations": [
                "Instrumentation adds overhead; no subtraction-based claim of uninstrumented performance",
                "Identity metadata read and profile JSON write occur outside the runner span",
                "Remote final seal/response encoding and HTTP transfer occur outside CarlaRunner and are excluded",
                "Sensors/callbacks on other threads are excluded from function statistics",
                "Runner return is not proof of sealed archive, acknowledged cleanup or driving verdict",
                "Process-kill before runner exit can leave no profile JSON; this is not a durable evidence journal",
                "Inclusive columns overlap across parent/child functions and must not be summed",
            ]}


def measured(original, name, *, context_manager=False):
    if context_manager:
        @wraps(original)
        @contextmanager
        def wrapper(*args, **kwargs):
            profile = getattr(_local, "profile", None)
            if profile is None:
                with original(*args, **kwargs) as value:
                    yield value
            else:
                with profile.span(name):
                    with original(*args, **kwargs) as value:
                        yield value
    else:
        @wraps(original)
        def wrapper(*args, **kwargs):
            profile = getattr(_local, "profile", None)
            if profile is None:
                return original(*args, **kwargs)
            with profile.span(name):
                return original(*args, **kwargs)
    return wrapper


def write_profile(profile, returned):
    # Never convert an otherwise successful engine execution into a failure due
    # to diagnostic output. Production artifacts/checkpoints remain untouched.
    try:
        identifier = profile.identity["execution_id"]
        safe = identifier[:100] if re.fullmatch(r"[A-Za-z0-9_.-]+", identifier) else hashlib.sha256(identifier.encode()).hexdigest()
        name = f"{safe}.{profile.method.rsplit('.', 1)[-1]}.{uuid4().hex}.json"
        content = json.dumps(profile.report(returned), sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        fd = os.open(_output_dir / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as stream:
            if stream.write(content) != len(content):
                raise OSError("Short profile write")
        print(f"CARLA runner profile saved: {name}", flush=True)
    except Exception as error:
        print(f"CARLA profiling output unavailable ({type(error).__name__}); engine result unchanged", file=sys.stderr, flush=True)


def outer(original, name):
    @wraps(original)
    def wrapper(*args, **kwargs):
        from axiom_carla import durability
        # Nesting remains part of one invocation. Other request/worker threads
        # have no TLS session and never enter this profile's counters.
        if getattr(_local, "profile", None) is not None:
            with _local.profile.span(name):
                return original(*args, **kwargs)
        sink = durability._active.get()
        if sink is None:
            return original(*args, **kwargs)
        started = clocks()
        try:
            meta = sink.meta()  # Exactly one profiling-owned metadata read.
            keys = ("execution_id", "store_id", "request_hash", "input_hash", "context_hash", "worker_adapter_version")
            identity = {key: meta[key] for key in keys}
            if not all(isinstance(identity[key], str) and identity[key] for key in keys if key != "context_hash"):
                raise ValueError("Incomplete profile execution identity")
        except Exception as error:
            print(f"CARLA profile identity unavailable ({type(error).__name__}); runner remains unchanged", file=sys.stderr, flush=True)
            return original(*args, **kwargs)
        elapsed = [end - start for start, end in zip(started, clocks())]
        profile = Profile(identity, name, elapsed)
        _local.profile = profile
        returned = False
        try:
            with profile.span(name):
                result = original(*args, **kwargs)
            returned = True
            return result
        finally:
            _local.profile = None
            write_profile(profile, returned)
    return wrapper


def install(output_dir):
    global _output_dir
    if _patched:
        raise RuntimeError("Profiling is already installed")
    _output_dir = Path(output_dir).resolve()
    _output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    from axiom_carla import native_journal, runner, spool, stack_bridge, vehicle_response
    targets = [
        (stack_bridge.LiveEvidence, name, f"LiveEvidence.{name}", False)
        for name in ("artifacts", "checkpoint", "reserve_before_dispatch", "retain")]
    targets += [(vehicle_response.VehicleResponseEvidence, "artifact", "VehicleResponseEvidence.artifact", False),
                (stack_bridge, "live_stack_recording", "stack_bridge.live_stack_recording", False),
                (stack_bridge, "_json", "stack_bridge._json", False),
                (native_journal.LegacyEvidence, "artifacts", "LegacyEvidence.artifacts", False),
                (native_journal.LegacyEvidence, "checkpoint", "LegacyEvidence.checkpoint", False),
                (spool, "canonical", "spool.canonical", False)]
    targets += [(spool.Attempt, name, f"Attempt.{name}", name == "db")
                for name in ("event", "checkpoint", "meta", "db")]
    for owner, attribute, name, cm in targets:
        original = getattr(owner, attribute)
        setattr(owner, attribute, measured(original, name, context_manager=cm))
        _patched.append((owner, attribute, original))
    for attribute in ("execute", "execute_with_context"):
        original = getattr(runner.CarlaRunner, attribute)
        setattr(runner.CarlaRunner, attribute, outer(original, f"CarlaRunner.{attribute}"))
        _patched.append((runner.CarlaRunner, attribute, original))


def uninstall():
    while _patched:
        owner, attribute, original = _patched.pop()
        setattr(owner, attribute, original)


def self_test():
    """Local authored fake worlds + real temporary SQLite store, never CARLA."""
    import inspect
    import math
    import tempfile
    import pytest
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root / "integrations/carla/tests"))
    from ade2e.execution_attempt import ExecutionAttemptRequest
    from ade2e.schemas import Scenario
    from axiom_carla import durability, spool, startup
    from axiom_carla.runner import CarlaRunner
    from axiom_carla.version import ADAPTER_VERSION
    from test_live_stack import install_live_world, live_request
    from test_native_durability import metadata
    from test_runner_evidence import fake_world_runner

    scenario = Scenario(id="profile-fixture", name="Authored profiling invariance fixture", category="lane_follow",
        map_name="Town01", duration_s=4, seed=17, revision=1, created_at="2026-09-12T00:00:00Z", updated_at="2026-09-12T00:00:00Z")
    originals = {name: inspect.signature(getattr(CarlaRunner, name)) for name in ("execute", "execute_with_context")}
    from ade2e.simulation_context import simulation_input_hash
    signatures = {}
    with tempfile.TemporaryDirectory(prefix="axiom-profile-selftest-") as temporary:
        directory = Path(temporary)
        for enabled in (False, True):
            if enabled:
                install(directory / "profiles")
                for name, signature in originals.items():
                    if inspect.signature(getattr(CarlaRunner, name)) != signature:
                        raise AssertionError("Wrapper changed the public signature")
            try:
                for mode in ("live", "legacy"):
                    with pytest.MonkeyPatch.context() as patch:
                        # Only the proof freezes existing diagnostic elapsed
                        # clocks. Instrumentation uses independent perf/CPU clocks.
                        # Otherwise kernel elapsed_ms/startup wall time naturally
                        # differ between two executions even without profiling.
                        patch.setattr(time, "monotonic", lambda: 0.0)
                        patch.setattr(startup.wait_for_stability, "__kwdefaults__", {
                            **startup.wait_for_stability.__kwdefaults__, "clock": lambda: 0.0})
                        if mode == "live":
                            world = metadata(install_live_world(patch), patch)
                            request = live_request(scenario)
                            attempt_request = ExecutionAttemptRequest(execution_id=request.execution_id,
                                input_hash=request.input_hash, adapter_id="carla", adapter_version=ADAPTER_VERSION,
                                scenario=request.scenario, context=request.context)
                        else:
                            world = metadata(fake_world_runner.__wrapped__(patch)(), patch)
                            attempt_request = ExecutionAttemptRequest(execution_id="profile-legacy", adapter_id="carla",
                                adapter_version=ADAPTER_VERSION, scenario=scenario,
                                input_hash=simulation_input_hash(scenario, "carla", ADAPTER_VERSION, None))
                        store = spool.Store(directory / f"store-{enabled}-{mode}")
                        with store.begin(attempt_request) as attempt, durability.recording(attempt):
                            result = CarlaRunner().execute_with_context(request) if mode == "live" else CarlaRunner().execute(scenario)
                            journal = attempt.journal(include_blobs=True)
                        signature = {"result": result.model_dump(mode="json", exclude={"artifact_payloads"}),
                            "artifacts": [(p.name, p.kind, p.media_type, p.content) for p in result.artifact_payloads],
                            "journal_events": [(entry["sequence"], entry["kind"], entry["data"],
                                                entry["blob_sha256"], entry["blob_size_bytes"])
                                               for entry in journal["entries"]], "world_frame": world.frame}
                        if not enabled:
                            signatures[mode] = signature
                        elif signature != signatures[mode]:
                            raise AssertionError(f"Profiling changed {mode} result/artifact bytes or event/frame counts")
                if enabled:
                    reports = [json.loads(path.read_bytes()) for path in (directory / "profiles").glob("*.json")]
                    if len(reports) != 2:
                        raise AssertionError("Expected separate live and legacy execution profiles")
                    for report in reports:
                        if not report["runner_returned"] or report["functions"]["Attempt.db"]["calls"] < 10:
                            raise AssertionError("Actual SQLite context-manager lifetimes were not profiled")
                        for stat in report["functions"].values():
                            if stat["calls"] < 1:
                                raise AssertionError("Invalid call count")
                            for clock in CLOCK_NAMES:
                                inc, exc = stat["inclusive_seconds"][clock], stat["exclusive_seconds"][clock]
                                if not math.isfinite(inc) or not math.isfinite(exc) or not 0 <= exc <= inc:
                                    raise AssertionError("Invalid timing accounting")
                    # Prove TLS exclusion and original context-manager rollback
                    # semantics without touching any actual simulator/store.
                    prof = Profile({}, "test", [0, 0, 0])
                    _local.profile = prof
                    try:
                        thread = threading.Thread(target=lambda: spool.canonical({"other_request": True}))
                        thread.start(); thread.join()
                        if prof.stats:
                            raise AssertionError("Other thread polluted runner profile")
                        with pytest.raises(RuntimeError, match="rollback-proof"):
                            with attempt.db() as connection:
                                connection.execute("UPDATE meta SET value='uncommitted' WHERE id=1")
                                raise RuntimeError("rollback-proof")
                        if attempt.meta()["execution_id"] != attempt_request.execution_id:
                            raise AssertionError("Instrumented context manager failed to roll back")
                    finally:
                        _local.profile = None
            finally:
                uninstall()
    print("Profiling self-test passed: live/legacy result + ALL artifact bytes identical with existing elapsed clocks fixed; real SQLite DB spans/rollback; finite nested timings; signatures; TLS exclusion. Authored fake worlds only.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true", help="Local fake-world + temporary SQLite proof only; never start HTTP or connect CARLA")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    directory = os.environ.get("AXIOM_CARLA_PROFILE_DIR")
    if not directory or not Path(directory).is_absolute():
        parser.error("AXIOM_CARLA_PROFILE_DIR must be an explicit absolute directory")
    try:
        port = int(os.environ.get("AXIOM_CARLA_PROFILE_PORT", "8101"))
        if not 1 <= port <= 65535:
            raise ValueError
    except ValueError:
        parser.error("AXIOM_CARLA_PROFILE_PORT must be 1..65535")
    install(directory)
    from axiom_carla.worker import app
    import uvicorn
    try:
        uvicorn.run(app, host="127.0.0.1", port=port, workers=1)
    finally:
        uninstall()


if __name__ == "__main__":
    main()
