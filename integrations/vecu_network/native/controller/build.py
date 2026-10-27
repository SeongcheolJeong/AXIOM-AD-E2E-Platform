#!/usr/bin/env python3
"""Build the copied, pinned reference controller with explicit network patch."""
from __future__ import annotations
import argparse
import difflib
import hashlib
import json
from pathlib import Path
import platform
import shutil
import subprocess

ROOT = Path(__file__).resolve().parent

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def source_identity():
    files = {str(p.relative_to(ROOT)): sha(p) for p in sorted(ROOT.rglob("*"))
             if p.is_file() and "__pycache__" not in p.parts}
    return files, hashlib.sha256(json.dumps(files, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

def patched_source():
    lock = json.loads((ROOT / "source.lock.json").read_text())
    for name, expected in lock["files"].items():
        if sha(ROOT / name) != expected:
            raise ValueError(f"Pinned controller source/patch changed: {name}")
    original = (ROOT / "upstream/traffic_dut.cpp").read_text()
    replacements = {
        'constexpr const char* version = ramp_policy ? "ramp-traffic-cpp/1" : "reference-traffic-cpp/1";':
        'constexpr const char* version = "network-traffic-cpp/1";',
        'if (seq!=sequence+1 || std::abs(now-seq*.05)>1e-6)':
        'if (seq<=sequence || std::abs(now-seq*.05)>1e-6)',
    }
    changed = original
    for old, new in replacements.items():
        if changed.count(old) != 1:
            raise ValueError("Controller patch context is not unique")
        changed = changed.replace(old, new)
    diff = "".join(difflib.unified_diff(original.splitlines(True), changed.splitlines(True),
                                     fromfile="a/traffic_dut.cpp", tofile="b/traffic_dut.cpp"))
    if diff != (ROOT / "network-sequence.patch").read_text():
        raise ValueError("Applied changes differ from preserved controller patch")
    if hashlib.sha256(changed.encode()).hexdigest() != lock["patched_traffic_dut_sha256"]:
        raise ValueError("Patched controller hash mismatch")
    return changed, lock

def build(output):
    output = output.resolve()
    if output == ROOT or ROOT in output.parents:
        raise ValueError("Build output must be outside immutable source tree")
    changed, origin = patched_source()
    dependencies = json.loads((ROOT / "vendor/dependencies.json").read_text())
    for dependency in dependencies["dependencies"]:
        for name, expected in dependency["files"].items():
            if sha(ROOT / name) != expected:
                raise ValueError(f"Pinned dependency changed: {name}")
    compiler = shutil.which("c++")
    if not compiler:
        raise RuntimeError("C++17 compiler required")
    output.mkdir(parents=True, exist_ok=False)
    files, source_hash = source_identity()
    source = output / "traffic_dut.cpp"
    source.write_text(changed)
    binary = output / "reference-traffic-cpp"
    flags = ["-std=c++17", "-O2", "-Wall", "-Wextra", "-pedantic", "-DAXIOM_RAMP_POLICY=0"]
    command = [compiler, *flags, "-I", str(ROOT), "-I", str(ROOT / "upstream"),
               str(source), "-o", str(binary)]
    completed = subprocess.run(command, capture_output=True, text=True, timeout=180)
    (output / "build.log").write_text(completed.stdout + completed.stderr)
    if completed.returncode:
        raise RuntimeError("Controller build failed: " + completed.stderr)
    if source_identity() != (files, source_hash):
        raise ValueError("Controller source changed during compilation")
    manifest = {
        "schema_version": "vecu-network-controller-build/1",
        "source_id": "network-cpp-immediate", "policy_id": "immediate",
        "dut_version": "network-traffic-cpp/1", "protocol": "hil-dut/1",
        "executable": binary.name, "binary_sha256": sha(binary),
        "source_sha256": source_hash, "files": files, "origin": origin,
        "generated_source_sha256": sha(source), "build_log_sha256": sha(output / "build.log"),
        "compiler": {"version": subprocess.check_output([compiler, "--version"], text=True).strip(),
                     "command": compiler, "flags": flags},
        "target_platform": {"os": platform.system().lower(), "arch": platform.machine().lower()},
        "dependencies": dependencies["dependencies"],
        "fidelity": "host-native-reference-application",
        "production_ecu_software": False,
        "sequence_contract": "strictly increasing, time_s=sequence*0.05; first global tick may be nonzero",
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return manifest

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    print(json.dumps(build(parser.parse_args().output_dir), sort_keys=True))
