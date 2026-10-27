#!/usr/bin/env python3
"""Verify pinned SIL Kit SDK/archive, build participant, seal a portable run bundle."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import platform
import shutil
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parent
RUNTIME = ("bin/sil-kit-registry", "bin/sil-kit-system-controller", "lib/libSilKit.so")

def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()

def source_identity():
    files = {str(p.relative_to(ROOT)): digest(p) for p in sorted(ROOT.rglob("*"))
             if p.is_file() and "__pycache__" not in p.parts}
    return files, hashlib.sha256(json.dumps(files, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

def verify_sdk(sdk, archive):
    lock = json.loads((ROOT / "dependencies.lock.json").read_text())["silkit"]
    if digest(archive) != lock["archive_sha256"]:
        raise ValueError("SIL Kit release archive SHA256 does not match pin")
    if sdk.name != "SilKit":
        raise ValueError("sdk-root must identify the release SilKit directory (with include/bin/lib)")
    selected = [*RUNTIME, *[str(p.relative_to(sdk)) for p in sorted((sdk / "include").rglob("*")) if p.is_file()]]
    if len(selected) <= len(RUNTIME):
        raise ValueError("SIL Kit public headers missing")
    hashes = {}
    licenses = {}
    with zipfile.ZipFile(archive) as zipped:
        for relative in selected:
            name = lock["archive_root"] + "/SilKit/" + relative
            expected = hashlib.sha256(zipped.read(name)).hexdigest()
            if digest(sdk / relative) != expected:
                raise ValueError("SIL Kit extracted file differs from pinned archive: " + relative)
            hashes[relative] = expected
        for relative in ("LICENSE", "SilKit-Source/ThirdParty/LICENSES.rst"):
            licenses[relative] = zipped.read(lock["archive_root"] + "/" + relative)
    return lock, hashes, licenses

def build(sdk, archive, output):
    sdk, archive, output = sdk.resolve(), archive.resolve(), output.resolve()
    if output == ROOT or ROOT in output.parents or output == sdk or sdk in output.parents:
        raise ValueError("Output must be separate from participant source and SDK")
    if platform.system() != "Linux" or platform.machine() != "x86_64":
        raise ValueError("Pinned SDK requires Linux/x86_64; no fallback build")
    lock, sdk_hashes, notices = verify_sdk(sdk, archive)
    deps = json.loads((ROOT / "vendor/dependencies.json").read_text())["dependencies"]
    for dependency in deps:
        for relative, expected in dependency["files"].items():
            if digest(ROOT / relative) != expected:
                raise ValueError("Pinned dependency changed: " + relative)
    compiler = shutil.which("c++")
    if not compiler:
        raise ValueError("C++17 compiler required")
    files, source_hash = source_identity()
    output.mkdir(parents=True, exist_ok=False)
    for relative in RUNTIME:
        target = output / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(sdk / relative, target)
        target.chmod(0o755)
    (output / "licenses").mkdir()
    (output / "licenses/SILKit-MIT.txt").write_bytes(notices["LICENSE"])
    (output / "licenses/SILKit-ThirdParty-LICENSES.rst").write_bytes(notices["SilKit-Source/ThirdParty/LICENSES.rst"])
    shutil.copyfile(ROOT / "vendor/nlohmann/LICENSE.MIT", output / "licenses/nlohmann-MIT.txt")
    flags = ["-std=c++17", "-O2", "-Wall", "-Wextra", "-pthread"]
    binary = output / "participant"
    command = [compiler, *flags, "-I", str(sdk / "include"), "-I", str(ROOT / "vendor"),
               str(ROOT / "participant.cpp"), "-L", str(output / "lib"), "-lSilKit",
               "-Wl,-rpath,$ORIGIN/lib", "-o", str(binary)]
    result = subprocess.run(command, capture_output=True, text=True, timeout=180)
    (output / "build.log").write_text(result.stdout + result.stderr)
    if result.returncode:
        raise RuntimeError("SIL Kit participant compile failed: " + result.stderr)
    if source_identity() != (files, source_hash):
        raise ValueError("Participant source changed during build")
    # SDK inputs can be touched by a different process; never seal an unverified bundle.
    for relative, expected in sdk_hashes.items():
        if digest(sdk / relative) != expected:
            raise ValueError("SDK changed during build: " + relative)
    for relative in RUNTIME:
        if digest(output / relative) != sdk_hashes[relative]:
            raise ValueError("Copied SIL Kit runtime mismatch: " + relative)
    runtime = {name: {"path": str(output / relative), "relative_path": relative,
                     "sha256": digest(output / relative)} for name, relative in
               (("participant", "participant"), ("registry", RUNTIME[0]),
                ("system_controller", RUNTIME[1]), ("library", RUNTIME[2]))}
    manifest = {"schema": "axiom-network-native-build/1", "engine": "silkit/5.0.8",
                "silkit": lock, "dependencies": deps, "runtime": runtime,
                "compiler": {"command": compiler, "flags": flags,
                    "version": subprocess.check_output([compiler, "--version"], text=True).strip()},
                "command": command, "source_files": files, "source_sha256": source_hash,
                "sdk_files": sdk_hashes, "build_log_sha256": digest(output / "build.log"),
                "license_files": {str(p.relative_to(output)): digest(p) for p in sorted((output / "licenses").iterdir())},
                "model": "functional CAN/Ethernet frame delivery with coordinated virtual time",
                "physical_can": False, "accurate_arbitration": False, "physical_ethernet": False}
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return manifest

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--sdk-root", type=Path, required=True)
    parser.add_argument("--sdk-archive", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.sdk_root, args.sdk_archive, args.output_dir), sort_keys=True))
