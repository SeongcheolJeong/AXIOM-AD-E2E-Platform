#!/usr/bin/env python3
"""Build a registered native ECU; no downloads or dependencies are installed.

python3 integrations/vecu/native/build.py --policy immediate --output-dir /tmp/ecu-a
Stdout is one machine-readable build manifest. Compiler logs go to stderr and
build.log. Only the operator chooses compiler/output paths, never public API input.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
POLICIES = {
    "immediate": ("reference-traffic-cpp", "reference-traffic-cpp/1", "0"),
    "ramp": ("ramp-traffic-cpp", "ramp-traffic-cpp/1", "1"),
}
SOURCES = (
    "build.py", "traffic_dut.cpp", "sha256.hpp", "README.md",
    "vendor/nlohmann/json.hpp", "vendor/nlohmann/LICENSE.MIT", "vendor/dependencies.json",
)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_identity() -> tuple[dict[str, str], str]:
    files = {name: digest(ROOT / name) for name in SOURCES}
    return files, hashlib.sha256(json.dumps(files, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def build(policy: str, output_dir: Path, compiler: str = "c++") -> dict:
    executable, version, flag = POLICIES[policy]
    resolved = shutil.which(compiler)
    if not resolved:
        raise ValueError("C++17 compiler is unavailable; no reference result will be substituted")
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / executable
    if target.exists() or (output_dir / "manifest.json").exists():
        raise ValueError("Native build output is immutable; choose a new output directory")
    files, source_hash = source_identity()
    dependencies = json.loads((ROOT / "vendor/dependencies.json").read_text())
    for dependency in dependencies["dependencies"]:
        for relative, expected in dependency["files"].items():
            if digest(ROOT / relative) != expected:
                raise ValueError("Pinned native dependency has changed: " + relative)
    compiler_version = subprocess.run([resolved, "--version"], capture_output=True, text=True, check=True, timeout=30).stdout.strip()
    flags = ["-std=c++17", "-O2", "-Wall", "-Wextra", "-pedantic", "-DAXIOM_RAMP_POLICY=" + flag]
    command = [resolved, *flags, str(ROOT / "traffic_dut.cpp"), "-o", str(target)]
    completed = subprocess.run(command, capture_output=True, text=True, timeout=180)
    log = completed.stdout + completed.stderr
    (output_dir / "build.log").write_text(log)
    if log:
        print(log, file=sys.stderr, end="" if log.endswith("\n") else "\n")
    if completed.returncode:
        raise RuntimeError("Native compiler failed (exit %s); inspect build.log" % completed.returncode)
    after_files, after_hash = source_identity()
    if after_files != files or after_hash != source_hash:
        raise ValueError("Native source changed during compilation; build cannot be registered")
    target.chmod(0o755)
    system, machine = platform.system().lower(), platform.machine().lower()
    manifest = {
        "schema_version": "vecu-native-build/1",
        "source_id": "cpp-" + policy,
        "policy_id": policy,
        "dut_version": version,
        "protocol": "hil-dut/1",
        "binary_sha256": digest(target),
        "source_sha256": source_hash,
        "compiler": {"version": compiler_version, "command": resolved, "flags": flags},
        "target_platform": {"os": system, "arch": machine},
        "executable": executable,
        "files": files,
        "dependencies": dependencies["dependencies"],
        "build_log_sha256": digest(output_dir / "build.log"),
        "fidelity": "host-native-reference-application",
        "production_ecu_software": False,
    }
    (output_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", choices=tuple(POLICIES), required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--compiler", default=os.environ.get("CXX", "c++"))
    args = parser.parse_args()
    try:
        manifest = build(args.policy, args.output_dir, args.compiler)
    except (ValueError, RuntimeError, OSError, subprocess.SubprocessError) as error:
        print("Native build failed: " + str(error), file=sys.stderr)
        raise SystemExit(2)
    print(json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()
